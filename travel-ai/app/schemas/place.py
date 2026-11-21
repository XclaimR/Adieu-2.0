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
    rating: float | None = None
    rating_count: int | None = None
    price_level: str | None = None
    # Estimated budget|mid|luxury for stays (Places has no hotel prices).
    tier: str | None = None
    type_label: str | None = None
    summary: str | None = None
    photo_name: str | None = None
    maps_url: str | None = None
    lat: float | None = None
    lng: float | None = None


class Pick(PlaceCandidate):
    why: str | None = None


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
