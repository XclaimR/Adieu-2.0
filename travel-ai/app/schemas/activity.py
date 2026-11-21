from pydantic import BaseModel


class Activity(BaseModel):
    name: str
    category: str
    time_of_day: str | None = None
    description: str | None = None
    place_id: str | None = None
    photo_name: str | None = None
    maps_url: str | None = None
