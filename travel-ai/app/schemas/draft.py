from pydantic import BaseModel

from app.schemas.itinerary import Itinerary


class NoPlans(BaseModel):
    """Why the last try made no drafts, in the words the traveler saw."""
    reason: str
    # False when trying again right away can't work (a daily limit).
    retry_now: bool = True


class ScaleReading(BaseModel):
    """One scale as steps 1 (left pole) to 5 (right pole): the plan's, and what the traveler asked (the ghost)."""
    plan: int | None = None
    wish: int | None = None


class PlanScales(BaseModel):
    """A plan on the six scales and the $ to $$$$$ badge (cost), worked out on read (services/plan_scales.py)."""
    scales: dict[str, ScaleReading] = {}
    cost: ScaleReading = ScaleReading()
    why: str = ""  # what the $ level comes from
    summary: str = ""  # "Packed · Icons · $$$"
    closest: bool = False  # nearest what they asked, among the drafts


class Draft(BaseModel):
    """One of the model itineraries offered to the user to choose from."""
    id: str
    flavor: str
    title: str
    summary: str
    photo_name: str | None = None
    # The trip-personality trait this version serves most, if any.
    trait: str | None = None
    # Moments the traveler asked for that this version doesn't fit in yet.
    unplanned: list[str] = []
    # What the review found and couldn't fix, shown on the card.
    warnings: list[str] = []
    itinerary: Itinerary
    # Filled on read, never stored: the plan's scales follow its current stops.
    scales: PlanScales | None = None
