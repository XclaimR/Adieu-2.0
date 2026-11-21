from pydantic import BaseModel

from app.schemas.dayplan import DayPlan
from app.schemas.place import Pick


class Itinerary(BaseModel):
    destination: str
    title: str | None = None
    stay: Pick | None = None
    days: list[DayPlan]
