import uuid

from app.schemas.personality import Personality
from app.schemas.place import OptionSet, Pick, PickCategory, PlaceCandidate, SwapTarget
from app.schemas.taste import Taste
from app.schemas.trip_profile import TripProfile
from app.schemas.turn import OptionsKind
from app.services.places_service import PlacesService, tier_rank
from app.services.ranking_service import RankingService
from app.services.starter_idea_service import StarterIdeaService

_TITLES = {
    PickCategory.STAY: "Places to stay",
    PickCategory.EAT: "Places to eat",
    PickCategory.DO: "Things to do",
    PickCategory.HIDDEN_GEMS: "Hidden gems",
}
_TIER_WORDS = {"budget": "budget", "mid": "mid-range", "luxury": "luxury"}
MIN_AFTER_FILTER = 3


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class OptionsService:
    """Builds the option sets shown in the side panel. Every place comes from Places, inside the destination."""

    def __init__(self, places: PlacesService):
        self.places = places

    def build(
        self,
        kind: OptionsKind,
        query: str,
        profile: TripProfile,
        viewport: dict | None,
        exclude_ids: set[str],
        personality: Personality | None = None,
    ) -> OptionSet:
        if kind == OptionsKind.DESTINATIONS:
            said = [f"{n.kind}: {n.text}" for n in (personality.notes if personality else []) if n.target == "trip"]
            hint = "; ".join(filter(None, [query, *said]))
            ideas = StarterIdeaService(self.places).generate(Taste(), hint=hint or None)
            return OptionSet(id=_new_id(), kind=kind.value, title="Where could you go?", ideas=ideas)

        category = PickCategory(kind.value)
        candidates = self.places.candidates(profile.destination, category, profile, viewport, query or None, personality)
        candidates = [c for c in candidates if c.place_id not in exclude_ids]
        if category == PickCategory.STAY:
            candidates = self._fit_budget(candidates, profile)

        picks = RankingService.rank(category, candidates, profile, focus=query, personality=personality)
        title = query.strip().capitalize() if query else f"{_TITLES[category]} in {profile.destination}"
        return OptionSet(id=_new_id(), kind=category.value, title=title, items=picks)

    @staticmethod
    def _fit_budget(candidates: list[PlaceCandidate], profile: TripProfile) -> list[PlaceCandidate]:
        """Drop stays above the traveler's budget tier, unless that would leave almost nothing."""
        if not profile.budget_level:
            return candidates
        limit = tier_rank(profile.budget_level.value)
        fitting = [c for c in candidates if tier_rank(c.tier) <= limit]
        return fitting if len(fitting) >= MIN_AFTER_FILTER else candidates

    def similar_cheaper(self, pick: Pick, profile: TripProfile, exclude_ids: set[str],
                        personality: Personality | None = None) -> OptionSet:
        """Stays like `pick` (same kind of place, same area) but in a lower price tier."""
        target = profile.budget_level.value if profile.budget_level else "budget"
        if tier_rank(target) >= tier_rank(pick.tier):
            target = {2: "mid", 1: "budget"}.get(tier_rank(pick.tier), "budget")

        kind_of_place = (pick.type_label or "hotel").lower()
        query = f"{_TIER_WORDS[target]} {kind_of_place} near {pick.name}"
        if pick.lat is not None and pick.lng is not None:
            candidates = self.places.nearby(query, PickCategory.STAY, pick.lat, pick.lng)
        else:
            candidates = self.places.candidates(profile.destination, PickCategory.STAY, None, None, query)

        candidates = [
            c for c in candidates
            if c.place_id not in exclude_ids | {pick.place_id} and tier_rank(c.tier) <= tier_rank(target)
        ] or [c for c in candidates if c.place_id != pick.place_id]

        focus = f"places similar to {pick.name} ({pick.type_label or 'stay'}) but at a {target} price"
        picks = RankingService.rank(PickCategory.STAY, candidates, profile, focus=focus, personality=personality)
        return OptionSet(id=_new_id(), kind="stay", title=f"Like {pick.name}, for less", items=picks)

    def swap_options(
        self,
        target: SwapTarget,
        activity_name: str,
        category: str,
        profile: TripProfile,
        viewport: dict | None,
        exclude_ids: set[str],
        personality: Personality | None = None,
        about: list[str] = (),
    ) -> OptionSet:
        """Alternatives for one itinerary activity."""
        cat = PickCategory(category) if category in PickCategory._value2member_map_ else PickCategory.DO
        candidates = [
            c for c in self.places.candidates(profile.destination, cat, profile, viewport, personality=personality)
            if c.place_id not in exclude_ids
        ]
        picks = RankingService.rank(cat, candidates, profile, focus="; ".join([f"an alternative to {activity_name}", *about]),
                                    personality=personality)
        return OptionSet(id=_new_id(), kind="swap", title=f"Instead of {activity_name}", items=picks, swap_target=target)
