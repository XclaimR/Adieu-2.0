import logging

from pydantic import BaseModel, Field

from app.schemas.place import Pick, PickCategory, PlaceCandidate
from app.schemas.taste import Taste
from app.schemas.trip_profile import TripProfile
from app.services.llm_client import LLMClient
from app.services.taste_service import TasteService

logger = logging.getLogger(__name__)

PICKS_PER_CATEGORY = 5


class RankedItem(BaseModel):
    ref: int = Field(description="the [n] number of the candidate")
    why: str = Field(description="why this fits this traveler, max 12 words")


class Ranking(BaseModel):
    # min_length stops small models from answering with an empty list.
    # max_length keeps local models fast: output tokens dominate latency.
    ranked: list[RankedItem] = Field(min_length=3, max_length=PICKS_PER_CATEGORY)


def _describe(index: int, c: PlaceCandidate) -> str:
    bits = [f"[{index}] {c.name}"]
    if c.type_label:
        bits.append(c.type_label)
    if c.rating:
        bits.append(f"{c.rating}★ ({c.rating_count or 0})")
    if c.price_level:
        bits.append(c.price_level)
    elif c.tier:
        bits.append(f"est. {c.tier} price")
    if c.summary:
        bits.append(c.summary)
    return " · ".join(bits)


def _fallback(candidates: list[PlaceCandidate]) -> list[Pick]:
    ordered = sorted(candidates, key=lambda c: (c.rating or 0) * min(c.rating_count or 0, 500), reverse=True)
    return [Pick(**c.model_dump()) for c in ordered[:PICKS_PER_CATEGORY]]


class RankingService:

    @staticmethod
    def rank(
        category: PickCategory,
        candidates: list[PlaceCandidate],
        profile: TripProfile,
        taste: Taste,
        focus: str = "",
    ) -> list[Pick]:
        """Rank real candidates for this traveler. The model only refers to candidates by number, so it can't invent places."""
        if not candidates:
            return []

        listing = "\n".join(_describe(i, c) for i, c in enumerate(candidates))
        prompt = f"""
Pick the {PICKS_PER_CATEGORY} places from the list below that best fit this traveler, best first.
Category: {category.value}
{f"The traveler is looking for: {focus}" if focus else ""}

Trip profile:
{profile.model_dump_json(exclude_none=True)}

What we know about the traveler's taste:
{TasteService.describe(taste)}

Candidates:
{listing}

Rules:
- Only use [n] numbers from the list.
- Price levels: $ cheap, $$ moderate, $$$ expensive, $$$$ very expensive. For a "budget" traveler put cheaper
  places first; for "luxury" the opposite.
- Respect hard preferences (diet, mobility, kids): rank places that clearly fit them first.
- "why" must connect the place to something specific about this traveler, using only the facts listed.
  Never claim a place is cheap, vegetarian, accessible, etc. unless the facts say so. Keep it under 12 words.
- If little is known about the traveler, favour well-rated, distinctive places.
"""
        try:
            ranking = LLMClient.generate_structured(prompt, Ranking)
        except Exception:
            logger.exception("Ranking failed for %s; falling back to rating order", category.value)
            return _fallback(candidates)

        picks: list[Pick] = []
        seen: set[int] = set()
        for item in ranking.ranked:
            if 0 <= item.ref < len(candidates) and item.ref not in seen:
                seen.add(item.ref)
                picks.append(Pick(**candidates[item.ref].model_dump(), why=item.why))
            if len(picks) == PICKS_PER_CATEGORY:
                break

        if not picks:
            logger.warning("Ranking for %s returned no usable picks; falling back to rating order", category.value)
            return _fallback(candidates)
        return picks
