from pydantic import BaseModel, Field


class StarterIdea(BaseModel):
    title: str = Field(description="catchy trip name, e.g. 'Slow food week in Portugal'")
    destination: str = Field(description="a country's name alone, e.g. 'Portugal'")
    pitch: str = Field(description="one sentence on why it fits this traveler")
    photo_name: str | None = None


class StarterIdeas(BaseModel):
    ideas: list[StarterIdea]
