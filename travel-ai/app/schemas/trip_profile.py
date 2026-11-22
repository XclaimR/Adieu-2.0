from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Party(str, Enum):
    SOLO = "solo"
    COUPLE = "couple"
    FRIENDS = "friends"
    FAMILY = "family"


class BudgetLevel(str, Enum):
    BUDGET = "budget"
    MID = "mid"
    LUXURY = "luxury"


class Preference(BaseModel):
    """Only on trips from before notes; TripRepository.personality reads these as notes."""
    key: str = Field(description="short snake_case topic, e.g. diet, pace, nightlife, mobility")
    value: str
    source: str = Field(default="stated", description="'stated' if the user said it, 'inferred' otherwise")


class TripProfile(BaseModel):
    # Destination, mood and party must be known before the app shows options (see TurnService.missing_vibe).
    destination: str | None = None
    mood: list[str] = Field(default_factory=list)
    start_date: str | None = None
    duration_days: int | None = None
    party: Party | None = None
    party_size: int | None = None
    kids: bool | None = None
    budget_level: BudgetLevel | None = None
    preferences: list[Preference] = Field(default_factory=list)


class ProfileUpdates(BaseModel):
    """What changed in this turn. Null means unchanged."""
    # Keep these optional for the model: requiring them makes small models fill in guesses.
    model_config = ConfigDict(json_schema_extra={"x-optional-fields": True})
    destination: str | None = None
    start_date: str | None = None
    duration_days: int | None = None
    party: Party | None = None
    party_size: int | None = None
    kids: bool | None = None
    budget_level: BudgetLevel | None = None
