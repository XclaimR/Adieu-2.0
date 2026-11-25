from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.personality import NoteChange
from app.schemas.trip_profile import ProfileUpdates


class TurnAction(str, Enum):
    ASK = "ask"
    SHOW_OPTIONS = "show_options"
    OFFER_DRAFTS = "offer_drafts"
    EDIT_ITINERARY = "edit_itinerary"
    CHAT = "chat"


class OptionsKind(str, Enum):
    DESTINATIONS = "destinations"
    STAY = "stay"
    EAT = "eat"
    DO = "do"
    HIDDEN_GEMS = "hidden_gems"


class OptionsRequest(BaseModel):
    kind: OptionsKind
    query: str = Field(default="", description="what to look for, e.g. 'cafés near Alfama'; empty for general")


class RouteChange(BaseModel):
    """A change to the cities of a multi-city trip, or its length. Code makes it; the model only names it."""
    op: Literal["swap", "add", "remove", "add_day", "swap_day"]
    city: str | None = Field(default=None, description="swap/add: the city coming in; add_day: the city the day is "
                                                       "in (where the place is)")
    leaving: str | None = Field(default=None, description="swap/remove: the route city going away")
    place: str | None = Field(default=None, description="add_day/swap_day: what the day is for, "
                                                                     "e.g. 'Universal Studios Japan'")
    days: int | None = Field(default=None, description="add/add_day: how many days, if they said")
    longer: bool = Field(default=False, description="add: true if the trip gets longer, false if other cities give "
                                                    "up days")


class Conflict(BaseModel):
    """The latest message goes against an earlier wish (Ticket #87). Code asks before changing anything."""
    note: str = Field(description="the id of the earlier note it goes against")
    now: str = Field(description="the new ask, copied from the latest message, e.g. 'put me in Ubud'")
    keep: str = Field(description="the earlier wish as a short answer, e.g. 'the beach town'")
    new: str = Field(description="the new ask as a short answer, e.g. 'Ubud'")


class TurnDecision(BaseModel):
    profile_updates: ProfileUpdates = Field(default_factory=ProfileUpdates)
    note_changes: list[NoteChange] = Field(
        default_factory=list, description="one entry per separate point in the latest message")
    action: TurnAction
    reply: str = Field(description="short, friendly reply to the user's message")
    next_question: str | None = Field(
        default=None,
        description="the single most useful follow-up question, or null if none is needed",
    )
    answer_options: list[str] = Field(
        default_factory=list,
        description="2-5 short tap-to-answer options for next_question",
    )
    user_stuck: bool = Field(default=False, description="true if the user seems unsure, overwhelmed or says 'you decide'")
    options_request: OptionsRequest | None = None
    edit_request: str | None = Field(default=None, description="the change the user wants to their itinerary")
    conflict: Conflict | None = Field(
        default=None, description="only when the latest message goes against an earlier note they did not take back")
    route_changes: list[RouteChange] = Field(
        default_factory=list, description="only on a trip with several cities: cities to swap, add or remove, "
                                          "or days to add")
