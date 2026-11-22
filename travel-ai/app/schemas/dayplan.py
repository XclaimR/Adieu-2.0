from pydantic import BaseModel

from app.schemas.activity import Activity


class DayPlan(BaseModel):
    day_number: int
    theme: str | None = None

    activities: list[Activity]

    @property
    def id(self) -> str:
        return f"d{self.day_number}"
