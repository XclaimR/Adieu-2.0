from pydantic import BaseModel, model_validator

from app.schemas.dayplan import DayPlan
from app.schemas.place import Pick


STAY_ID = "s1"  # shortcut: one stay per trip; give stays their own ids with multi-city (#27)


class Itinerary(BaseModel):
    destination: str
    title: str | None = None
    stay: Pick | None = None
    days: list[DayPlan]

    @model_validator(mode="before")
    @classmethod
    def _ids_for_older_plans(cls, data):
        """Plans saved before ids existed get the same ids on every read until they are next saved."""
        if not isinstance(data, dict):
            return data
        days = []
        for day in data.get("days") or []:
            if isinstance(day, dict):
                day = {**day, "activities": [
                    {**a, "id": f"x{day.get('day_number')}_{i}"} if isinstance(a, dict) and "id" not in a else a
                    for i, a in enumerate(day.get("activities") or [])]}
            days.append(day)
        return {**data, "days": days}

    def target_ids(self) -> list[str]:
        """Everything a note or an edit may point at."""
        return [STAY_ID, *(d.id for d in self.days), *(a.id for d in self.days for a in d.activities)]
