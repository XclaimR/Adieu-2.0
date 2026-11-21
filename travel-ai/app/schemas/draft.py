from pydantic import BaseModel

from app.schemas.itinerary import Itinerary


class Draft(BaseModel):
    """One of the model itineraries offered to the user to choose from."""
    id: str
    flavor: str
    title: str
    summary: str
    photo_name: str | None = None
    itinerary: Itinerary
