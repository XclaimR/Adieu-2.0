from typing import Literal

from pydantic import BaseModel, Field

# Each pole of the six scales, as the model names it.
Pole = Literal["slow", "packed", "spend_on_stay", "spend_on_experiences", "chill", "nightlife",
               "hidden_gems", "icons", "food_as_fuel", "food_focus", "backpacker", "luxury"]


class NoteUpdate(BaseModel):
    """One intention from the latest message, in the traveler's own words. Code checks it before keeping it."""
    quote: str = Field(description="the traveler's exact words, a short phrase copied from their latest message")
    topic: str = Field(description="short snake_case topic, e.g. pace, birthday_night, food. Reuse an existing "
                                   "topic when the traveler changes or corrects it")
    scope: Literal["trip", "moment"] = Field(description="'trip' if it describes the whole trip; 'moment' if it is "
                                                         "one thing to fit in, like a dinner, a night out, a day trip")
    toward: Pole | None = Field(default=None, description="for 'trip' notes: the scale pole it points to, or null")


class Note(BaseModel):
    """A kept intention: what the traveler said, whether it shapes the whole trip or is one moment to plan."""
    topic: str
    text: str
    scope: Literal["trip", "moment"]
    toward: Pole | None = None


class Scale(BaseModel):
    """One sliding scale: -1 is the left pole (e.g. slow), +1 the right (e.g. packed)."""
    value: float = 0.0
    confidence: float = 0.0
    # Where it came from: "taste" (lasting vibes), "chat" (budget/kids in the profile), "said" (the traveler's words),
    # later "asked" (#20).
    source: str | None = None


class Personality(BaseModel):
    """A trip's personality: what the traveler told us (notes, the source of truth for suggestions) and six scales
    derived from it (see PersonalityService.SCALES) for the card and for choosing what to ask next."""
    scales: dict[str, Scale] = Field(default_factory=dict)
    notes: list[Note] = Field(default_factory=list)
    # Filled for the API only: known traits in plain words, and labels of the scales still unknown.
    traits: list[str] = Field(default_factory=list)
    unknown: list[str] = Field(default_factory=list)
