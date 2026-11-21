from enum import Enum

from pydantic import BaseModel, Field

from app.schemas.personality import ScaleSignal
from app.schemas.taste import TasteUpdates
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


class TurnDecision(BaseModel):
    profile_updates: ProfileUpdates = Field(default_factory=ProfileUpdates)
    taste_updates: TasteUpdates = Field(default_factory=TasteUpdates)
    scale_signals: list[ScaleSignal] = Field(
        default_factory=list,
        description="Usually empty. Only when the latest message clearly says how the traveler wants the WHOLE trip: "
                    "pace (slow/packed), money (spend_on_stay/spend_on_experiences), evenings (chill/nightlife), "
                    "crowds (hidden_gems/icons), food (food_as_fuel/food_focus), comfort (backpacker/luxury). "
                    "Read the context: 'a slow week but one big night out' is slow, not nightlife; 'no nightlife' is "
                    "chill. Skip vague words like 'fun' and one-off activities like a spa visit or a single dinner. "
                    "Quote their exact words, including words like 'really', 'a bit' or 'maybe'.",
    )
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
