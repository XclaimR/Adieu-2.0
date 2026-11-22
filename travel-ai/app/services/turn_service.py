import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import create_model

from app.schemas.itinerary import Itinerary
from app.schemas.personality import NoteChange
from app.schemas.trip_profile import TripProfile
from app.schemas.turn import OptionsKind, OptionsRequest, TurnAction, TurnDecision
from app.services.llm_client import LLMClient

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
- profile_updates: only core facts the user actually said about THIS trip. Null for anything not said.
  Never guess defaults (no "solo", no "mid" budget, no party_size unless stated).
  Core fields: destination (a real place name, e.g. "Japan", never a sentence), start_date, duration_days,
  party (solo|couple|friends|family), party_size, kids, budget_level (budget|mid|luxury).
  A wish about one item or day never changes budget_level.
- note_changes: understand meaning, negation and context. One entry per separate point they made, including every
  request to change the plan. Everything that matters beyond the core facts goes here: mood, wishes, dislikes,
  diet, access, occasions, pace.
  op: add = a wish to remember; remove = they took that note id back or said it is fine now (a changed mind is
  remove + add); do = a one-time instruction with nothing to remember ("change that restaurant").
  Never remove a note they did not take back: changing a place keeps the notes on it.
  target = the narrowest thing they mean: an item, a day, s1 or trip. One whole-trip point is one entry on trip,
  never copied to days. The stay is always s1, whichever night they mention. A move targets the item being moved.
  category: diet is only what they can't or won't eat (vegetarian, allergies); a love of food is eat.
  kind: must (cannot be broken, like diet or access), avoid, want.
  text for avoid names only the thing avoided, without the negation: "no beach" -> text "beach",
  "nothing early" -> text "early starts".
  source: stated, or inferred = it follows from their words but they did not say it; add those as their own
  entries, quoting the words it follows from. Young children, older parents, injuries and occasions always
  imply something (a toddler implies early evenings).
  mood: set it on the point that says what kind of trip this is (an activity trip counts). Never from a place name
  alone or vague praise like "fun" or "nice" (mood null), and never for something they want to avoid.
  toward: null for most points. Only when a whole-trip point itself says that pole: slow/packed is the pace of
  the days, chill/nightlife is the evenings, hidden_gems/icons is crowds, food_focus/food_as_fuel is how much food
  matters, backpacker/luxury is comfort, spend_on_stay/spend_on_experiences is where money goes. An activity
  (hiking, beach), avoiding a place, diet or group size says no pole.
  then: change = the plan has to change at target now, with search = what to look for; none = only remember it.
  A new whole-trip rule on an existing plan is then change at trip, unless nothing in the plan could break it.
  No entries for small talk, questions, or a bare destination. If you cannot tell which item they mean, use no
  entries, action ask, and ask which one.
- action:
  ask            - you still need to understand the trip's vibe, or one more detail would help
  show_options   - the user asked to see options (places to stay/eat/things to do/hidden gems/destinations);
                   fill options_request {kind, query}
  offer_drafts   - you know enough (destination, mood, who's going) or the user wants you to decide
  edit_itinerary - an itinerary exists and the user wants to change it; put the change in edit_request
  chat           - a general question or comment that needs nothing else
- reply: 1-2 short sentences reacting to the user. No questions in the reply. Same language as the user. Don't promise suggestions or plans ("let me suggest…") and never say a change is done — the app makes it afterwards.
- next_question: ONE short question that matters most now, or null. Never ask what's already known.
- answer_options: 2-5 short tap-to-answer choices for next_question (e.g. ["Just me", "My partner", "Friends"]).
- user_stuck: true when the user seems unsure or overwhelmed ("not sure", "idk", "you decide", "anything").

Examples (non-null parts only):
- "I want to go to Japan" -> destination "Japan", action ask, next_question about the trip's mood
- "somewhere relaxing with my wife, 5 days" -> party "couple", party_size 2, duration_days 5,
  note add trip want "relaxing" quote "relaxing" mood "relaxed" toward "slow"
- "a hiking trip in the Dolomites" -> destination "Dolomites",
  note add trip want "hiking trip" quote "a hiking trip" category "do" mood "adventurous" toward null
- "Bali, no beach and no nightlife" -> destination "Bali", two notes: add trip avoid "beach" quote "no beach" toward null,
  add trip avoid "nightlife" quote "no nightlife" toward "chill"
- "a slow food week" -> two notes, as it says two things: add trip want "slow" mood "relaxed" toward "slow",
  add trip want "food" category "eat" mood "foodie" toward "food_focus"
- "a slow week, but one big night out" -> note add trip want "slow" mood "relaxed" toward "slow",
  note add trip want "one big night out" once true
- "my mom uses a wheelchair" -> party "family", note add trip must "wheelchair access" category "access"
- "the day 1 stay is too much, find something similar but cheaper" -> action edit_itinerary,
  note add s1 want "similar but cheaper" category "stay" then change search "mid-range hotel near <current stay>"
- "we have our toddler with us" -> party "family", kids true, two notes quoting "our toddler": add trip must
  "suitable for a toddler", add trip want "early evenings" source "inferred"
- "swap the ramen place for sushi" -> action edit_itinerary, note do <that item's id> "sushi instead of ramen"
  category "eat" then change search "sushi restaurant"
- "move the temple to day 2" -> action edit_itinerary, note do <that item's id> "move to day 2" then change
- "actually the beach is fine" -> action chat, note remove <id of the beach note> then none
- "hmm not sure, you decide" -> user_stuck true
- "show me cafés near Alfama" -> action show_options, options_request {kind "eat", query "cafés near Alfama"}
- "make day 2 more relaxed" -> action edit_itinerary, edit_request "make day 2 more relaxed",
  note add d2 want "more relaxed" category "pace" then change
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


def decision_schema(itinerary: Itinerary | None) -> type[TurnDecision]:
    """TurnDecision where a note can only point at something on this trip."""
    targets = ("trip", *(itinerary.target_ids() if itinerary else ()))
    # `id` stays open: models number new notes, and failing that cost a second call. check_changes checks removes.
    change = create_model("NoteChange", __base__=NoteChange, target=(Literal[targets], "trip"))
    return create_model("TurnDecision", __base__=TurnDecision, note_changes=(list[change], []))


class TurnService:

    @staticmethod
    def decide(message: str, history: list[dict], profile: TripProfile, context: str,
               itinerary: Itinerary | None = None) -> TurnDecision:
        prompt = f"""
Current trip profile:
{profile.model_dump_json(exclude_none=True, exclude={"preferences"})}

{context}

Latest user message:
{message}
"""
        return LLMClient.generate_structured(
            prompt, decision_schema(itinerary), history=history[-HISTORY_LIMIT:], system=SYSTEM_PROMPT
        )

    @staticmethod
    def to_change(decision: TurnDecision) -> list[NoteChange]:
        """The points that change the plan now. Taking a note back ("the beach is fine") only lifts a limit."""
        points = [c for c in decision.note_changes if c.op != "remove"]
        marked = [c for c in points if c.then == "change"]
        # Models sometimes choose the edit action and forget to mark the point.
        return marked or (points if decision.action == TurnAction.EDIT_ITINERARY else [])

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
        to_change = TurnService.to_change(decision)
        changing = bool(to_change)

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
            # An edit with no entries ("add a day") works on the whole trip; only taking a note back edits nothing.
            if changing or (decision.action == TurnAction.EDIT_ITINERARY and decision.edit_request
                            and not decision.note_changes):
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
        if not has_drafts or changing or decision.action in (TurnAction.OFFER_DRAFTS, TurnAction.EDIT_ITINERARY):
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
