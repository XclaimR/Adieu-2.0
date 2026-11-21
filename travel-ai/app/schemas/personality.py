from pydantic import BaseModel, Field


class Scale(BaseModel):
    """One sliding scale: -1 is the left pole (e.g. slow), +1 the right (e.g. packed)."""
    value: float = 0.0
    confidence: float = 0.0
    # Where the value came from: "taste" (lasting taste), "chat" (what they said this trip), later "asked" (#20).
    source: str | None = None


class Personality(BaseModel):
    """A trip's personality: six scales (see PersonalityService.SCALES), plus the card's plain words."""
    scales: dict[str, Scale] = Field(default_factory=dict)
    # Filled for the API only: known traits in plain words, and labels of the scales still unknown.
    traits: list[str] = Field(default_factory=list)
    unknown: list[str] = Field(default_factory=list)
