from enum import Enum

from pydantic import BaseModel, Field

from app.schemas.starter_idea import StarterIdea


class PickCategory(str, Enum):
    STAY = "stay"
    EAT = "eat"
    DO = "do"
    HIDDEN_GEMS = "hidden_gems"


class PlaceCandidate(BaseModel):
    place_id: str
    name: str
    category: PickCategory
    address: str | None = None
    # Only on places saved from Google before open data; open data has no ratings or prices.
    rating: float | None = None
    rating_count: int | None = None
    price_level: str | None = None
    # Estimated budget|mid|luxury for stays, None when nothing says (no open source has hotel prices).
    tier: str | None = None
    type_label: str | None = None
    summary: str | None = None
    photo_name: str | None = None
    maps_url: str | None = None
    # The guide its description comes from (CC BY-SA: shown with a link).
    source_url: str | None = None
    lat: float | None = None
    lng: float | None = None


class Pick(PlaceCandidate):
    why: str | None = None
    # The trip-personality trait (plain words, e.g. "Food is the point") this pick serves, if any.
    trait: str | None = None


class SwapTarget(BaseModel):
    day_number: int
    index: int


class OptionSet(BaseModel):
    """What the side panel shows: a set of options the user can choose from or react to."""
    id: str
    kind: str  # destinations | stay | eat | do | hidden_gems | swap
    title: str
    items: list[Pick] = Field(default_factory=list)
    ideas: list[StarterIdea] = Field(default_factory=list)
    swap_target: SwapTarget | None = None
