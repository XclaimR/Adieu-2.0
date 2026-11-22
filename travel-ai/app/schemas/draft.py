from pydantic import BaseModel

from app.schemas.itinerary import Itinerary


class NoPlans(BaseModel):
    """Why the last try made no drafts, in the words the traveler saw."""
    reason: str
    # False when trying again right away can't work (a daily limit).
    retry_now: bool = True


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
    itinerary: Itinerary
