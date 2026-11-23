import logging

from pydantic import BaseModel, Field

from app.schemas.personality import Personality
from app.schemas.place import Pick, PickCategory, PlaceCandidate
from app.schemas.trip_profile import TripProfile
from app.services.llm_client import LLMClient
from app.services.personality_service import PersonalityService
from app.services.draft_service import describe
from app.services.places_service import FACTS_RULE, traveler_fit, unclaimed

logger = logging.getLogger(__name__)

PICKS_PER_CATEGORY = 5


class RankedItem(BaseModel):
    ref: int = Field(description="the [n] number of the candidate")
    why: str = Field(description="why this fits this traveler, max 12 words")
    trait: str | None = Field(default=None, description="the listed personality trait this place serves, or null")


class Ranking(BaseModel):
    # min_length stops small models from answering with an empty list.
    # max_length keeps local models fast: output tokens dominate latency.
    ranked: list[RankedItem] = Field(min_length=3, max_length=PICKS_PER_CATEGORY)


def _fallback(candidates: list[PlaceCandidate]) -> list[Pick]:
    """Candidates already come best-fitting first."""
    return [Pick(**c.model_dump()) for c in candidates[:PICKS_PER_CATEGORY]]


class RankingService:

    @staticmethod
    def rank(
        category: PickCategory,
        candidates: list[PlaceCandidate],
        profile: TripProfile,
        focus: str = "",
        personality: Personality | None = None,
    ) -> list[Pick]:
        """Rank real candidates for this traveler. The model only refers to candidates by number, so it can't invent places."""
        if not candidates:
            return []

        listing = "\n".join(describe(f"[{i}]", c) for i, c in enumerate(candidates))
        allowed = PersonalityService.traits(personality)[0]  # only known traits; guesses stay off the cards
        prompt = f"""
Pick the {PICKS_PER_CATEGORY} places from the list below that best fit this traveler, best first.
Category: {category.value}
{f"The traveler is looking for: {focus}" if focus else ""}

Trip profile:
{profile.model_dump_json(exclude_none=True, exclude={"preferences"})}

Candidates:
{listing}

Rules:
- Only use [n] numbers from the list.
- Stays may have an estimated tier (budget, mid, luxury). For a "budget" traveler put budget stays first; for
  "luxury" the opposite. Unknown is unknown, not cheap.
- Respect hard preferences (diet, mobility, kids): rank places that clearly fit them first.
{traveler_fit(profile, personality)}
{f"- trait: copy the one personality trait above that this place serves best (exactly as written), or null. Options: {', '.join(allowed)}." if allowed else "- trait: null."}
- "why" must connect the place to something specific about this traveler, using only the facts listed.
  Never claim a place is cheap, vegetarian, accessible, etc. unless the facts say so. Keep it under 12 words.
{FACTS_RULE}
- If little is known about the traveler, favour described, distinctive places.
"""
        try:
            ranking = LLMClient.generate_structured(prompt, Ranking)
        except Exception:
            logger.exception("Ranking failed for %s; falling back to the given order", category.value)
            return _fallback(candidates)

        picks: list[Pick] = []
        seen: set[int] = set()
        for item in ranking.ranked:
            if 0 <= item.ref < len(candidates) and item.ref not in seen:
                seen.add(item.ref)
                place = candidates[item.ref]
                picks.append(Pick(**place.model_dump(), why=unclaimed(item.why, place.summary),
                                  trait=PersonalityService.match_trait(item.trait, allowed)))
            if len(picks) == PICKS_PER_CATEGORY:
                break

        if not picks:
            logger.warning("Ranking for %s returned no usable picks; falling back to the given order", category.value)
            return _fallback(candidates)
        return picks
