from typing import Literal

from pydantic import BaseModel, Field

# Each pole of the six scales, as the model names it.
Pole = Literal["slow", "packed", "spend_on_stay", "spend_on_experiences", "chill", "nightlife",
               "hidden_gems", "icons", "food_as_fuel", "food_focus", "backpacker", "luxury"]


class ScaleSignal(BaseModel):
    """What the traveler said about the whole trip, read in context by the model. Code checks it and sets the amount."""
    toward: Pole
    quote: str = Field(description="the traveler's exact words from their latest message that say this")


class Scale(BaseModel):
    """One sliding scale: -1 is the left pole (e.g. slow), +1 the right (e.g. packed)."""
    value: float = 0.0
    confidence: float = 0.0
    # Where it came from: "taste" (lasting vibes), "chat" (budget/kids in the profile), "said" (the traveler's words),
    # later "asked" (#20).
    source: str | None = None


class Personality(BaseModel):
    """A trip's personality: six scales (see PersonalityService.SCALES), plus the card's plain words."""
    scales: dict[str, Scale] = Field(default_factory=dict)
    # Filled for the API only: known traits in plain words, and labels of the scales still unknown.
    traits: list[str] = Field(default_factory=list)
    unknown: list[str] = Field(default_factory=list)
