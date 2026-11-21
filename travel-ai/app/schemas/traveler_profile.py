from pydantic import BaseModel

class TravelerProfile(BaseModel):
    traveler_count: int | None = None
    destination: str | None = None
    budget: int | None = None
    travel_style: str | None = None
    occasion: str | None = None
    relationship_type: str | None = None
    trip_type: str | None = None
    season: str | None = None
    duration: int | None = None