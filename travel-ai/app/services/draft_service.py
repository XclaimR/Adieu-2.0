import logging
import re
import unicodedata
import uuid
from collections.abc import Iterator
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.activity import Activity
from app.schemas.dayplan import DayPlan
from app.schemas.draft import Draft
from app.schemas.itinerary import Itinerary
from app.schemas.place import Pick, PickCategory, PlaceCandidate
from app.schemas.taste import Taste
from app.schemas.trip_profile import TripProfile
from app.services.llm_client import LLMClient
from app.services.places_service import PlacesService, party_fit, tier_rank
from app.services.taste_service import TasteService

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 4
MAX_DAYS = 10

FLAVORS = [
    ("easy", "Slow and easy: a relaxed pace built around the traveler's main mood, long meals, few must-dos"),
    ("classic", "The highlights: the best-known sights and experiences, efficiently grouped"),
    ("local", "Off the beaten path: hidden gems and local favorites, avoiding the tourist crowds"),
]


class PlanStop(BaseModel):
    # Naming the place first keeps the note about that place; the name is checked against ref.
    place: str = Field(description="the exact name of the place, copied from the list")
    ref: int = Field(description="the [n] number of that same place")
    time_of_day: Literal["morning", "afternoon", "evening"]
    note: str = Field(description="what to do there, max 12 words")


class PlanDay(BaseModel):
    theme: str = Field(description="2-4 word theme for the day")
    stops: list[PlanStop] = Field(min_length=2, max_length=4)


class DraftPlan(BaseModel):
    title: str = Field(description="catchy 3-6 word name for this version of the trip")
    summary: str = Field(description="one sentence on what this version feels like")
    stay_ref: int | None = Field(default=None, description="[n] of a place to stay from the list, or null")
    # The cap stops runaway generation (each extra day costs ~10 s on a local model).
    days: list[PlanDay] = Field(min_length=1, max_length=10)


_SLOTS = ["morning", "afternoon", "evening"]


def _norm(name: str) -> str:
    """Lowercase letters and digits only, accents dropped: "Jerónimos Monastery" == "jeronimos monastery"."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", plain.lower())


def _same(a: str, b: str) -> bool:
    # Containment allows "Pantheon" for "National Pantheon"; the length floor stops "Bar" matching everything.
    return bool(a and b) and (a == b or (min(len(a), len(b)) >= 6 and (a in b or b in a)))


def _one_per_slot(activities: list[Activity]) -> list[Activity]:
    """Move a repeated time of day to a free one, then order the day morning → evening."""
    taken: set[str] = set()
    for a in activities:
        if a.time_of_day in taken:
            free = [s for s in _SLOTS if s not in taken]
            if free:  # with 4 stops one slot must repeat
                later = [s for s in free if _SLOTS.index(s) > _SLOTS.index(a.time_of_day)]
                a.time_of_day = (later or free)[0]
        taken.add(a.time_of_day)
    return sorted(activities, key=lambda a: _SLOTS.index(a.time_of_day) if a.time_of_day in _SLOTS else len(_SLOTS))


class PlacePool:
    """Numbered real places the model may use. It can only reference places by number."""

    def __init__(self, places: list[PlaceCandidate]):
        seen: set[str] = set()
        self.places: list[PlaceCandidate] = []
        for p in places:
            if p.place_id not in seen:
                seen.add(p.place_id)
                self.places.append(p)

    def listing(self) -> str:
        lines = []
        for i, p in enumerate(self.places):
            bits = [f"[{i}] {p.name}", p.category.value]
            if p.type_label:
                bits.append(p.type_label)
            if p.rating:
                bits.append(f"{p.rating}★")
            if p.price_level or p.tier:
                bits.append(p.price_level or f"est. {p.tier} price")
            lines.append(" · ".join(bits))
        return "\n".join(lines)

    def get(self, ref: int | None) -> PlaceCandidate | None:
        return self.places[ref] if ref is not None and 0 <= ref < len(self.places) else None

    def resolve(self, stop: PlanStop) -> PlaceCandidate | None:
        """The place the stop's note is about: its ref if the names agree, else the place it names, else None."""
        named = _norm(stop.place)
        place = self.get(stop.ref)
        if place and _same(named, _norm(place.name)):
            return place
        return next((p for p in self.places if _same(named, _norm(p.name))), None)

    def to_itinerary(self, plan: DraftPlan, destination: str) -> Itinerary:
        days = []
        used: set[str] = set()
        for day in plan.days:
            activities = []
            for stop in day.stops:
                place = self.resolve(stop)
                # Unknown refs and stays are dropped; repeats of the same place too.
                if place is None or place.category == PickCategory.STAY or place.place_id in used:
                    continue
                used.add(place.place_id)
                activities.append(Activity(
                    name=place.name,
                    category=place.category.value,
                    time_of_day=stop.time_of_day,
                    description=stop.note,
                    place_id=place.place_id,
                    photo_name=place.photo_name,
                    maps_url=place.maps_url,
                ))
            if activities:
                days.append(DayPlan(day_number=len(days) + 1, theme=day.theme, activities=_one_per_slot(activities)))

        stay = self.get(plan.stay_ref)
        return Itinerary(
            destination=destination,
            title=plan.title,
            stay=Pick(**stay.model_dump()) if stay and stay.category == PickCategory.STAY else None,
            days=days,
        )


class DraftService:

    def __init__(self, places: PlacesService):
        self.places = places

    def pool(self, profile: TripProfile, viewport: dict | None, exclude_ids: set[str], extra: list[PlaceCandidate] = ()) -> PlacePool:
        """Real candidates inside the destination, one search per category (cached)."""
        places: list[PlaceCandidate] = list(extra)
        for category in (PickCategory.DO, PickCategory.EAT, PickCategory.HIDDEN_GEMS, PickCategory.STAY):
            found = self.places.candidates(profile.destination, category, profile, viewport)
            if category == PickCategory.STAY and profile.budget_level:
                limit = tier_rank(profile.budget_level.value)
                found = [c for c in found if tier_rank(c.tier) <= limit] or found
            places += [c for c in found if c.place_id not in exclude_ids]
        return PlacePool(places)

    @staticmethod
    def _prompt(profile: TripProfile, taste: Taste, pool: PlacePool, days: int, brief: str) -> str:
        return f"""
Plan a {days}-day trip to {profile.destination}.
This version's style: {brief}

Trip profile:
{profile.model_dump_json(exclude_none=True)}

Traveler taste:
{TasteService.describe(taste)}

Places you may use (refer to them ONLY by [n]):
{pool.listing()}

Rules:
- Exactly {days} days, 2-3 stops each, mixing things to do and places to eat.
- Each day uses morning, afternoon and evening at most once. Lunch is afternoon, dinner is evening.
- Each stop's place and note must be about the same listed place. Don't plan anything that isn't on the list.
- Group nearby places on the same day. Don't repeat a place.
- Respect diet, mobility, kids and budget preferences.
{party_fit(profile)}
- stay_ref must be a 'stay' place from the list, or null.
"""

    def generate(self, profile: TripProfile, taste: Taste, viewport: dict | None, exclude_ids: set[str]) -> Iterator[Draft]:
        """Yields 3 drafts with different flavors, one at a time (each is a separate model call)."""
        pool = self.pool(profile, viewport, exclude_ids)
        days = min(profile.duration_days or DEFAULT_DAYS, MAX_DAYS)
        for flavor, brief in FLAVORS:
            try:
                plan = LLMClient.generate_structured(self._prompt(profile, taste, pool, days, brief), DraftPlan)
            except Exception:
                logger.exception("Draft %s failed", flavor)
                continue
            itinerary = pool.to_itinerary(plan, profile.destination)
            if not itinerary.days:
                logger.warning("Draft %s referenced no valid places", flavor)
                continue
            first_photo = next((a.photo_name for d in itinerary.days for a in d.activities if a.photo_name), None)
            yield Draft(
                id=uuid.uuid4().hex[:12],
                flavor=flavor,
                title=plan.title,
                summary=plan.summary,
                photo_name=first_photo,
                itinerary=itinerary,
            )

    def edit(self, itinerary: Itinerary, request: str, profile: TripProfile, taste: Taste,
             viewport: dict | None, exclude_ids: set[str]) -> Itinerary:
        """Apply a free-text change ("make day 2 more relaxed") using only real places."""
        current = [
            PlaceCandidate(place_id=a.place_id, name=a.name, category=PickCategory(a.category)
                           if a.category in PickCategory._value2member_map_ else PickCategory.DO,
                           photo_name=a.photo_name, maps_url=a.maps_url)
            for d in itinerary.days for a in d.activities if a.place_id
        ]
        if itinerary.stay:
            current.append(PlaceCandidate(**itinerary.stay.model_dump(exclude={"why"})))
        pool = self.pool(profile, viewport, exclude_ids, extra=current)
        ref = {p.place_id: i for i, p in enumerate(pool.places)}

        outline = "\n".join(
            f"Day {d.day_number} ({d.theme}): " + ", ".join(
                f"[{ref[a.place_id]}] {a.name} ({a.time_of_day})" for a in d.activities if a.place_id in ref)
            for d in itinerary.days
        )
        days = len(itinerary.days)
        if profile.duration_days and profile.duration_days != days:
            days = min(profile.duration_days, MAX_DAYS)

        prompt = self._prompt(profile, taste, pool, days, "keep what works, change only what the traveler asked") + f"""
Current plan:
{outline}
Current stay: {f"[{ref[itinerary.stay.place_id]}]" if itinerary.stay else "none"}

The traveler wants this change: {request}
Return the full updated plan with the same title unless the change calls for a new one.
"""
        plan = LLMClient.generate_structured(prompt, DraftPlan)
        updated = pool.to_itinerary(plan, itinerary.destination)
        if not updated.days:
            return itinerary
        if updated.stay is None:
            updated.stay = itinerary.stay
        return updated

    @staticmethod
    def place_in_itinerary(itinerary: Itinerary, pick: Pick) -> Itinerary:
        """Add a place the user tapped ＋ on: stays become the stay, others go to the lightest day."""
        itinerary = itinerary.model_copy(deep=True)
        if pick.category == PickCategory.STAY:
            itinerary.stay = pick
            return itinerary
        if any(a.place_id == pick.place_id for d in itinerary.days for a in d.activities):
            return itinerary

        activity = Activity(name=pick.name, category=pick.category.value, time_of_day=None,
                            description=pick.why, place_id=pick.place_id, photo_name=pick.photo_name,
                            maps_url=pick.maps_url)
        if not itinerary.days:
            itinerary.days.append(DayPlan(day_number=1, theme="Your picks", activities=[activity]))
        else:
            min(itinerary.days, key=lambda d: len(d.activities)).activities.append(activity)
        return itinerary
