from pydantic import BaseModel, Field


class Taste(BaseModel):
    """What we know about the user across all their trips."""
    vibes: list[str] = Field(default_factory=list)
    likes: list[str] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)


class Vibe(BaseModel):
    id: str
    label: str
    emoji: str
    description: str


class TasteUpdates(BaseModel):
    likes: list[str] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)
