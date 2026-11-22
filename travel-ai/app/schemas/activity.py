import uuid

from pydantic import BaseModel, Field


def new_item_id() -> str:
    return f"i{uuid.uuid4().hex[:6]}"


class Activity(BaseModel):
    # The slot's id: swapping or moving keeps it, so the notes pointing at it stay attached.
    id: str = Field(default_factory=new_item_id)
    name: str
    category: str
    time_of_day: str | None = None
    description: str | None = None
    place_id: str | None = None
    photo_name: str | None = None
    maps_url: str | None = None
    # Where it is, for travel times. None on plans saved before this.
    lat: float | None = None
    lng: float | None = None
    # The traveler's words this stop fulfils, e.g. "one big night out for my birthday".
    for_note: str | None = None
