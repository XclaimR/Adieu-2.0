from pydantic import BaseModel

class ItineraryCritique(BaseModel):
    is_valid: bool
    issues: list[str]
    suggestions: list[str]