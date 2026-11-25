from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.draft import Draft, NoPlans, PlanScales
from app.schemas.itinerary import Itinerary
from app.schemas.personality import Personality
from app.schemas.place import OptionSet
from app.schemas.taste import Taste
from app.schemas.trip_profile import TripProfile


class GoogleLoginRequest(BaseModel):
    id_token: str


class DevLoginRequest(BaseModel):
    email: str
    name: str | None = None


class UserOut(BaseModel):
    id: str
    email: str
    name: str | None = None
    picture: str | None = None
    taste: Taste
    has_taste: bool


class LoginResponse(BaseModel):
    token: str
    user: UserOut


class TasteRequest(BaseModel):
    vibes: list[str]


class CreateTripRequest(BaseModel):
    title: str | None = None


class MessageOut(BaseModel):
    role: str
    content: str
    created_at: datetime


class TripSummary(BaseModel):
    id: str
    title: str
    destination: str | None
    duration_days: int | None = None
    photo_name: str | None = None
    updated_at: datetime


class ConflictOut(BaseModel):
    """A held message that goes against an earlier wish (Ticket #87), in the traveler's words."""
    earlier: str
    now: str


class TripOut(BaseModel):
    id: str
    title: str
    profile: TripProfile
    personality: Personality
    # The chosen plan (or, before one, only what they asked) on the six scales and the $ badge (Ticket #83).
    scales: PlanScales | None = None
    destination_name: str | None
    panel: OptionSet | None
    drafts: list[Draft]
    no_plans: NoPlans | None
    liked_place_ids: list[str]
    skipped_place_ids: list[str]
    next_question: str | None
    answer_options: list[str]
    conflict: ConflictOut | None = None  # while its question is the open one
    itinerary: Itinerary | None
    messages: list[MessageOut]


class SendMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class TurnResponse(BaseModel):
    """Everything a turn produced; the streaming endpoint sends the same pieces as separate events."""
    reply: str
    next_question: str | None
    answer_options: list[str] = Field(default_factory=list)
    options: OptionSet | None = None
    drafts: list[Draft] = Field(default_factory=list)
    trip: TripOut


class PickActionRequest(BaseModel):
    action: str = Field(pattern="^(like|unlike|skip|too_pricey|add)$")


class ActivityActionRequest(BaseModel):
    action: str = Field(pattern="^(remove|swap)$")


class ReplaceActivityRequest(BaseModel):
    place_id: str


class TripUpdateResponse(BaseModel):
    """Result of a card or itinerary action; `options` opens the panel when the action produced new choices."""
    trip: TripOut
    options: OptionSet | None = None
