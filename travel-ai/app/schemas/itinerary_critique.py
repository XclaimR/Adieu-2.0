from pydantic import BaseModel


class Issue(BaseModel):
    problem: str
    suggestion: str | None = None
    # The ids to change: stops, days or the stay. Empty when something is missing from the whole plan.
    where: list[str] = []


class ItineraryCritique(BaseModel):
    issues: list[Issue] = []
    # By place, so a place swapped into a stop doesn't inherit the old one's time.
    visit_minutes: dict[str, int] = {}

    @property
    def is_valid(self) -> bool:
        return not self.issues
