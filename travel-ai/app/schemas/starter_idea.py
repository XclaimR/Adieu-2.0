from pydantic import BaseModel, Field


class StarterIdea(BaseModel):
    title: str = Field(description="catchy trip name, e.g. 'Slow food week in Lisbon'")
    destination: str = Field(description="city or region, e.g. 'Lisbon, Portugal'")
    pitch: str = Field(description="one sentence on why it fits this traveler")
    photo_name: str | None = None


class StarterIdeas(BaseModel):
    ideas: list[StarterIdea]
