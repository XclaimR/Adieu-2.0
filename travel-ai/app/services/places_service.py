import json
import logging
import re
from datetime import datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.models.place_cache import PlaceCacheModel
from app.schemas.place import PickCategory, PlaceCandidate
from app.schemas.trip_profile import BudgetLevel, TripProfile

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
_FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.rating",
    "places.userRatingCount",
    "places.priceLevel",
    "places.primaryTypeDisplayName",
    "places.editorialSummary",
    "places.photos",
    "places.googleMapsUri",
    "places.location",
])
_RESOLVE_FIELD_MASK = "places.id,places.displayName,places.formattedAddress,places.viewport,places.photos,places.types"

# A destination must be a geographic place, never a business. "Korea" once resolved to a restaurant called
# "Streets of Korea" in Hyderabad, because Places ranks nearby businesses highly.
_GEO_TYPES = {
    "country", "administrative_area_level_1", "administrative_area_level_2", "administrative_area_level_3",
    "locality", "sublocality", "postal_town", "colloquial_area", "political", "natural_feature",
    "archipelago", "island", "continent",
}
_GEO_FALLBACK_TYPES = ("country", "locality", "administrative_area_level_1")

CATEGORY_QUERIES: dict[PickCategory, str] = {
    PickCategory.STAY: "best hotels and stays in {destination}",
    PickCategory.EAT: "best restaurants in {destination}",
    PickCategory.DO: "top things to do in {destination}",
    PickCategory.HIDDEN_GEMS: "hidden gems local favorites in {destination}",
}

_PRICE_LEVELS = {
    "PRICE_LEVEL_FREE": "Free",
    "PRICE_LEVEL_INEXPENSIVE": "$",
    "PRICE_LEVEL_MODERATE": "$$",
    "PRICE_LEVEL_EXPENSIVE": "$$$",
    "PRICE_LEVEL_VERY_EXPENSIVE": "$$$$",
}


_BUDGET_WORDS = {BudgetLevel.BUDGET: "affordable", BudgetLevel.LUXURY: "luxury"}
_DIET_KEYS = {"diet", "dietary", "food", "dietary_restrictions"}


def search_hints(category: PickCategory, profile: TripProfile) -> str:
    """Words that steer the Places search toward this traveler, so the right places are in the candidate pool."""
    words: list[str] = []
    if category == PickCategory.EAT:
        words += [p.value for p in profile.preferences if p.key in _DIET_KEYS]
    if category in (PickCategory.EAT, PickCategory.STAY) and profile.budget_level in _BUDGET_WORDS:
        words.append(_BUDGET_WORDS[profile.budget_level])
    if profile.kids and category in (PickCategory.STAY, PickCategory.DO):
        words.append("family-friendly")
    if any(p.key == "mobility" for p in profile.preferences) and category in (PickCategory.STAY, PickCategory.DO):
        words.append("wheelchair accessible")
    return " ".join(dict.fromkeys(w.strip() for w in words if w.strip()))[:80]


_TIER_ORDER = {"budget": 0, "mid": 1, "luxury": 2}
_BUDGET_STAY = re.compile(r"hostel|guest ?house|backpacker|b&b|bed (and|&) breakfast|pension|pensão|motel|capsule|budget|campground|homestay")
_LUXURY_STAY = re.compile(r"palace|resort|luxury|5[- ]star|five[- ]star|grand hotel|ritz|four seasons|mandarin|bulgari|aman|six senses|st\.? regis|waldorf|peninsula")


def tier_rank(tier: str | None) -> int:
    return _TIER_ORDER.get(tier or "mid", 1)


def estimate_stay_tier(name: str, type_label: str | None, summary: str | None, price_level: str | None) -> str:
    """Rough budget|mid|luxury guess for a stay. Places gives hotels no prices, so we read the type and wording."""
    if price_level:
        return "budget" if price_level in ("Free", "$") else "mid" if price_level == "$$" else "luxury"
    text = " ".join(filter(None, [name, type_label, summary])).lower()
    if _BUDGET_STAY.search(text):
        return "budget"
    if _LUXURY_STAY.search(text):
        return "luxury"
    return "mid"


_PHOTO_NAME = re.compile(r"places/[A-Za-z0-9_-]+/photos/[A-Za-z0-9_-]+")


class PlacesUnavailableError(RuntimeError):
    pass


def _to_candidate(raw: dict, category: PickCategory) -> PlaceCandidate:
    photos = raw.get("photos") or []
    location = raw.get("location") or {}
    name = (raw.get("displayName") or {}).get("text", "Unknown")
    type_label = (raw.get("primaryTypeDisplayName") or {}).get("text")
    summary = (raw.get("editorialSummary") or {}).get("text")
    price_level = _PRICE_LEVELS.get(raw.get("priceLevel"))
    return PlaceCandidate(
        place_id=raw["id"],
        name=name,
        category=category,
        address=raw.get("formattedAddress"),
        rating=raw.get("rating"),
        rating_count=raw.get("userRatingCount"),
        price_level=price_level,
        tier=estimate_stay_tier(name, type_label, summary, price_level) if category == PickCategory.STAY else None,
        type_label=type_label,
        summary=summary,
        photo_name=photos[0]["name"] if photos else None,
        maps_url=raw.get("googleMapsUri"),
        lat=location.get("latitude"),
        lng=location.get("longitude"),
    )


class PlacesService:

    def __init__(self, db: Session):
        self.db = db

    def _cached(self, key: str, ttl_hours: int | None = None) -> dict | None:
        ttl = ttl_hours if ttl_hours is not None else settings.PLACES_CACHE_TTL_HOURS
        row = self.db.get(PlaceCacheModel, key)
        if row and row.fetched_at > datetime.utcnow() - timedelta(hours=ttl):
            return row.data
        return None

    def _store(self, key: str, data: dict) -> None:
        row = self.db.get(PlaceCacheModel, key)
        if row:
            row.data = data
            row.fetched_at = datetime.utcnow()
        else:
            self.db.add(PlaceCacheModel(key=key, data=data))
        self.db.commit()

    def _post(self, body: dict, field_mask: str) -> list[dict]:
        if not settings.GOOGLE_PLACES_API_KEY:
            raise PlacesUnavailableError("GOOGLE_PLACES_API_KEY is not set")
        response = httpx.post(
            _SEARCH_URL,
            headers={"X-Goog-Api-Key": settings.GOOGLE_PLACES_API_KEY, "X-Goog-FieldMask": field_mask},
            json=body,
            timeout=15,
        )
        if response.status_code != 200:
            raise PlacesUnavailableError(f"Places search failed ({response.status_code}): {response.text[:200]}")
        return response.json().get("places", [])

    def text_search(
        self,
        query: str,
        max_results: int = 10,
        viewport: dict | None = None,
        near: tuple[float, float] | None = None,
    ) -> list[dict]:
        """Text search. `viewport` restricts results to the destination; `near` biases toward a point."""
        body: dict = {"textQuery": query, "maxResultCount": max_results}
        if viewport:
            body["locationRestriction"] = {"rectangle": {"low": viewport["low"], "high": viewport["high"]}}
        elif near:
            body["locationBias"] = {"circle": {"center": {"latitude": near[0], "longitude": near[1]}, "radius": 2000.0}}

        area = json.dumps(body.get("locationRestriction") or body.get("locationBias") or {}, sort_keys=True)
        key = f"search:{max_results}:{query.strip().lower()}:{area}"
        cached = self._cached(key)
        if cached is not None:
            return cached["places"]

        places = self._post(body, _FIELD_MASK)
        self._store(key, {"places": places})
        return places

    def resolve_destination(self, name: str) -> dict | None:
        """Turn a destination name into a real geographic place with a map area, or None if there isn't one."""
        key = f"resolve-geo:{name.strip().lower()}"
        cached = self._cached(key)
        if cached is not None:
            return cached.get("area")

        raw = self._first_geographic(self._post({"textQuery": name, "maxResultCount": 5}, _RESOLVE_FIELD_MASK))
        for place_type in _GEO_FALLBACK_TYPES:
            if raw:
                break
            raw = self._first_geographic(self._post(
                {"textQuery": name, "maxResultCount": 1, "includedType": place_type, "strictTypeFiltering": True},
                _RESOLVE_FIELD_MASK,
            ))

        area = None
        if raw:
            photos = raw.get("photos") or []
            area = {
                "place_id": raw["id"],
                "name": (raw.get("displayName") or {}).get("text", name),
                "formatted_address": raw.get("formattedAddress"),
                "viewport": raw["viewport"],
                "photo_name": photos[0]["name"] if photos else None,
            }
        self._store(key, {"area": area})
        return area

    @staticmethod
    def _first_geographic(results: list[dict]) -> dict | None:
        return next((r for r in results if r.get("viewport") and _GEO_TYPES & set(r.get("types") or [])), None)

    def candidates(
        self,
        destination: str,
        category: PickCategory,
        profile: TripProfile | None = None,
        viewport: dict | None = None,
        query: str | None = None,
    ) -> list[PlaceCandidate]:
        """Candidates for a category (or a free-text query) inside the destination."""
        text = f"{query} in {destination}" if query else CATEGORY_QUERIES[category].format(destination=destination)
        hints = search_hints(category, profile) if profile else ""
        if hints:
            text = f"{hints} {text}"
        return [_to_candidate(raw, category) for raw in self.text_search(text, viewport=viewport)]

    def nearby(self, query: str, category: PickCategory, lat: float, lng: float) -> list[PlaceCandidate]:
        return [_to_candidate(raw, category) for raw in self.text_search(query, near=(lat, lng))]

    def verify_destination(self, destination: str) -> PlaceCandidate | None:
        """Return the best match for a destination name, or None if Places doesn't know it."""
        area = self.resolve_destination(destination)
        if not area:
            return None
        return PlaceCandidate(place_id=area["place_id"], name=area["name"], category=PickCategory.DO,
                              address=area["formatted_address"], photo_name=area["photo_name"])

    def photo_url(self, photo_name: str, max_width: int = 800) -> str:
        """Resolve a Places photo name to a short-lived public image URL without exposing our API key."""
        # Strict shape: anything looser lets "../" reach other Places endpoints with our key.
        if not _PHOTO_NAME.fullmatch(photo_name):
            raise ValueError("invalid photo name")

        key = f"photo:{max_width}:{photo_name}"
        # Resolved photo URLs are short-lived, so cache them for less time than search results.
        cached = self._cached(key, ttl_hours=12)
        if cached is not None:
            return cached["url"]

        if not settings.GOOGLE_PLACES_API_KEY:
            raise PlacesUnavailableError("GOOGLE_PLACES_API_KEY is not set")

        response = self._photo_media(photo_name, max_width)
        # Photo names stored on old trips expire; the name holds the place id, so fetch that place's current photo.
        if response.status_code in (400, 404) and (fresh := self._current_photo_name(photo_name.split("/")[1])):
            response = self._photo_media(fresh, max_width)
        if response.status_code != 200:
            raise PlacesUnavailableError(f"Photo lookup failed ({response.status_code})")

        url = response.json()["photoUri"]
        self._store(key, {"url": url})
        return url

    @staticmethod
    def _photo_media(photo_name: str, max_width: int) -> httpx.Response:
        return httpx.get(
            f"https://places.googleapis.com/v1/{photo_name}/media",
            params={"maxWidthPx": max_width, "skipHttpRedirect": "true", "key": settings.GOOGLE_PLACES_API_KEY},
            timeout=15,
        )

    @staticmethod
    def _current_photo_name(place_id: str) -> str | None:
        response = httpx.get(
            f"https://places.googleapis.com/v1/places/{place_id}",
            headers={"X-Goog-Api-Key": settings.GOOGLE_PLACES_API_KEY, "X-Goog-FieldMask": "photos"},
            timeout=15,
        )
        photos = response.json().get("photos") if response.status_code == 200 else None
        name = photos[0]["name"] if photos else None
        return name if name and _PHOTO_NAME.fullmatch(name) else None
