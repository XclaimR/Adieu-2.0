import re
from dataclasses import dataclass, field

from app.schemas.taste import Taste
from app.schemas.trip_profile import TripProfile
from app.schemas.turn import OptionsKind, OptionsRequest, TurnAction, TurnDecision
from app.services.llm_client import LLMClient
from app.services.taste_service import TasteService

HISTORY_LIMIT = 12
STUCK_AFTER_QUESTIONS = 4
SHOW_PLANS = "__show_plans__"
SHOW_PLANS_TEXT = "Show me some plans"

_SURPRISE = re.compile(r"\b(surprise me|no idea where|don'?t know where|inspire me|suggest (a|some) (place|destination)s?)\b", re.I)
_EXPLICIT_ASK = re.compile(r"\b(show|find|recommend|suggest|options|any good|where (should|can|to))\b", re.I)
_SENTENCE_BREAK = re.compile(r"(?:(?<=[.!?])|(?<=[.!?]\)))\s+")

# Asked in this order until known. Duration is asked once; if skipped we assume a default.
VIBE_QUESTIONS: dict[str, tuple[str, list[str]]] = {
    "destination": ("Where would you like to go?", ["Surprise me", "Somewhere warm", "A city break", "Mountains"]),
    "mood": ("What kind of trip are you dreaming of?", ["Relaxed", "Adventurous", "Romantic", "Foodie", "Culture"]),
    "party": ("Who's coming along?", ["Just me", "My partner", "Friends", "Family with kids"]),
    "duration": ("How many days are you thinking?", ["A weekend", "4-5 days", "A week", "Not sure yet"]),
}

CHANGE_QUESTION = (
    "Anything you'd like to change?",
    ["More relaxed", "More food spots", "Add a hidden gem", "Looks perfect"],
)

SYSTEM_PROMPT = """
You are Adieu, a warm, concise travel planner. You learn about the trip through conversation and decide what
happens next. The app shows options in a side panel, so never list places in your reply.

Return:
- profile_updates: only what the user actually said about THIS trip. Null / empty for anything not said.
  Never guess defaults (no "solo", no "mid" budget, no party_size unless stated).
  Core fields: destination (a real place name, e.g. "Japan", never a sentence), mood (list of words like
  relaxed, adventurous, romantic, foodie, culture, nightlife, nature, beach), start_date, duration_days,
  party (solo|couple|friends|family), party_size, kids, budget_level (budget|mid|luxury).
  Anything else that matters (diet, pace, mobility, interests, avoid, occasion, stay style) goes in preferences.
- taste_updates: lasting likes/dislikes about the person in general.
- action:
  ask            - you still need to understand the trip's vibe, or one more detail would help
  show_options   - the user asked to see options (places to stay/eat/things to do/hidden gems/destinations);
                   fill options_request {kind, query}
  offer_drafts   - you know enough (destination, mood, who's going) or the user wants you to decide
  edit_itinerary - an itinerary exists and the user wants to change it; put the change in edit_request
  chat           - a general question or comment that needs nothing else
- reply: 1-2 short sentences reacting to the user. No questions in the reply. Same language as the user.
- next_question: ONE short question that matters most now, or null. Never ask what's already known.
- answer_options: 2-5 short tap-to-answer choices for next_question (e.g. ["Just me", "My partner", "Friends"]).
- user_stuck: true when the user seems unsure or overwhelmed ("not sure", "idk", "you decide", "anything").

Examples (non-null parts only):
- "I want to go to Japan" -> destination "Japan", action ask, next_question about the trip's mood
- "somewhere relaxing with my wife, 5 days" -> mood ["relaxed"], party "couple", party_size 2, duration_days 5
- "my mom uses a wheelchair" -> party "family", preferences [{key "mobility", value "wheelchair user"}]
- "hmm not sure, you decide" -> user_stuck true
- "show me cafés near Alfama" -> action show_options, options_request {kind "eat", query "cafés near Alfama"}
- "make day 2 more relaxed" -> action edit_itinerary, edit_request "make day 2 more relaxed"
"""


@dataclass
class TurnPlan:
    """What the workflow should do this turn, after code-level rules are applied to the model's decision."""
    action: TurnAction
    next_question: str | None
    answer_options: list[str] = field(default_factory=list)
    options_request: OptionsRequest | None = None
    asked_field: str | None = None


def missing_vibe(profile: TripProfile, asked: list[str]) -> str | None:
    """The first piece of the minimum vibe still unknown (duration only until it has been asked once)."""
    if not profile.destination:
        return "destination"
    if not profile.mood:
        return "mood"
    if not profile.party:
        return "party"
    if not profile.duration_days and "duration" not in asked:
        return "duration"
    return None


class TurnService:

    @staticmethod
    def decide(message: str, history: list[dict], profile: TripProfile, taste: Taste, context: str) -> TurnDecision:
        prompt = f"""
Current trip profile:
{profile.model_dump_json(exclude_none=True)}

Traveler taste (from past trips and the vibe picker):
{TasteService.describe(taste)}

{context}

Latest user message:
{message}
"""
        return LLMClient.generate_structured(
            prompt, TurnDecision, history=history[-HISTORY_LIMIT:], system=SYSTEM_PROMPT
        )

    @staticmethod
    def wants_surprise(message: str) -> bool:
        return bool(_SURPRISE.search(message))

    @staticmethod
    def explicit_request(message: str) -> bool:
        return bool(_EXPLICIT_ASK.search(message))

    @staticmethod
    def strip_questions(reply: str, next_question: str | None) -> str:
        """The follow-up question is shown separately, so drop questions the model also put in the reply."""
        if not next_question:
            return reply
        kept: list[str] = []
        dropped_last = False
        for sentence in _SENTENCE_BREAK.split(reply.strip()):
            # A "(e.g. ...)" right after a dropped question belongs to that question.
            if sentence.endswith("?") or (dropped_last and sentence.startswith("(")):
                dropped_last = True
                continue
            dropped_last = False
            kept.append(sentence)
        return " ".join(kept) or reply

    @staticmethod
    def plan(
        decision: TurnDecision,
        profile: TripProfile,
        *,
        message: str,
        asked: list[str],
        questions_streak: int,
        has_drafts: bool,
        has_itinerary: bool,
        surprise: bool = False,
    ) -> TurnPlan:
        """Code-level guarantees that don't depend on the model behaving."""
        q, chips = decision.next_question, decision.answer_options[:5]
        request = decision.options_request
        show_plans = message == SHOW_PLANS
        explicit = TurnService.explicit_request(message)

        if surprise or (request and request.kind == OptionsKind.DESTINATIONS and not profile.destination):
            return TurnPlan(TurnAction.SHOW_OPTIONS, q, chips, OptionsRequest(kind=OptionsKind.DESTINATIONS))

        stuck = decision.user_stuck or show_plans or questions_streak >= STUCK_AFTER_QUESTIONS
        missing = missing_vibe(profile, asked)

        if not profile.destination:
            if stuck:
                return TurnPlan(TurnAction.SHOW_OPTIONS, None, [], OptionsRequest(kind=OptionsKind.DESTINATIONS))
            return TurnService._ask(missing, q, chips, decision)

        if has_itinerary:
            if show_plans:
                return TurnPlan(TurnAction.OFFER_DRAFTS, None, [])
            # Refining: every turn ends with one change question.
            q, chips = (q, chips) if q else CHANGE_QUESTION
            if decision.action == TurnAction.EDIT_ITINERARY and decision.edit_request:
                return TurnPlan(TurnAction.EDIT_ITINERARY, q, chips)
            if decision.action == TurnAction.SHOW_OPTIONS and request:
                return TurnPlan(TurnAction.SHOW_OPTIONS, q, chips, request)
            return TurnPlan(TurnAction.CHAT, q, chips)

        # The user explicitly asked to see something: honour it even mid-conversation.
        if decision.action == TurnAction.SHOW_OPTIONS and request and explicit:
            return TurnPlan(TurnAction.SHOW_OPTIONS, q, chips, request)

        if stuck:
            return TurnPlan(TurnAction.OFFER_DRAFTS, None, [])

        if missing:
            return TurnService._ask(missing, q, chips, decision)

        # Vibe is complete: offer drafts once; afterwards (re)draft only when the model decides to.
        if not has_drafts or decision.action in (TurnAction.OFFER_DRAFTS, TurnAction.EDIT_ITINERARY):
            return TurnPlan(TurnAction.OFFER_DRAFTS, None, [])
        return TurnPlan(TurnAction.CHAT, q, chips)

    @staticmethod
    def _ask(missing: str, q: str | None, chips: list[str], decision: TurnDecision) -> TurnPlan:
        """Ask about the missing piece; use the model's wording if it asked about something else, else our own."""
        default_q, default_chips = VIBE_QUESTIONS[missing]
        if q and decision.action == TurnAction.ASK and _mentions(missing, q):
            return TurnPlan(TurnAction.ASK, q, chips or default_chips, asked_field=missing)
        return TurnPlan(TurnAction.ASK, default_q, default_chips, asked_field=missing)


_FIELD_WORDS = {
    "destination": ("where", "destination", "place", "go"),
    "mood": ("kind of trip", "vibe", "mood", "feel", "style", "type of trip"),
    "party": ("who", "travel with", "travelling with", "traveling with", "coming"),
    "duration": ("how long", "how many days", "days", "nights", "length"),
}


def _mentions(missing: str, question: str) -> bool:
    low = question.lower()
    return any(w in low for w in _FIELD_WORDS[missing])
