import hashlib
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# Each pole of the six scales, as the model names it.
Pole = Literal["slow", "packed", "hotel_time", "out_exploring", "cheap_experiences", "splurge_experiences", "chill",
               "nightlife",
               "hidden_gems", "icons", "food_as_fuel", "food_focus", "backpacker", "luxury"]
# A closed list, so vague praise ("fun", "nice") has no mood to land on.
Mood = Literal["relaxed", "adventurous", "romantic", "foodie", "culture", "nightlife", "nature", "beach",
               "celebration", "wellness", "shopping", "indulgent"]
_OLD_MONEY_POLES = {"spend_on_stay", "spend_on_experiences"}
_EXPERIENCES = {"cheap_experiences", "splurge_experiences"}
NoteCategory = Literal["stay", "eat", "do", "pace", "budget", "diet", "access", "general"]


class NoteChange(BaseModel):
    """One point from the traveler's latest message. The model understands it; code checks the structure, the
    ids, and that the quote is really in the message. There is no 'replace': it silently overwrote notes."""
    op: Literal["add", "remove", "do"] = Field(
        description="add = a wish to remember; remove = they took note `id` back; "
                    "do = a one-time instruction with nothing to remember")
    id: str | None = Field(default=None, description="for remove: the id of the note taken back")
    target: str = Field(default="trip", description="the narrowest thing meant: trip, a day id, s1 or an item id")
    category: NoteCategory = "general"
    kind: Literal["must", "avoid", "want"] = "want"
    text: str = Field(description="the whole point in a few words, e.g. 'similar but cheaper'")
    quote: str = Field(description="exact words copied from the latest message")
    source: Literal["stated", "inferred"] = "stated"
    once: bool = Field(default=False, description="true for one thing to fit in once, like one big night out")
    mood: Mood | None = Field(
        default=None, description="the kind of trip, only if this point clearly says it; null for vague words")
    toward: Pole | None = Field(default=None, description="the scale pole this whole-trip point clearly means, or null")
    strong: bool = Field(default=False, description="true if they said it emphatically: really, very, only")
    then: Literal["none", "change"] = Field(
        default="none", description="change = the plan must change now at target (swap, move, add, remove, retime)")
    search: str | None = Field(
        default=None, description="for change: what to search Places for, e.g. 'mid-range hotel near Palace Hotel'")


class Note(BaseModel):
    """What the traveler told us, stored once on the trip and pointed at what it is about. Targets are slots
    (trip, d2, s1, an activity id), so a note survives swapping the place in that slot."""
    id: str
    target: str = "trip"
    category: NoteCategory = "general"
    kind: Literal["must", "avoid", "want"] = "want"
    text: str
    source: Literal["stated", "inferred"] = "stated"
    once: bool = False
    mood: str | None = None
    toward: Pole | None = None
    strong: bool = False

    @model_validator(mode="before")
    @classmethod
    def _from_topic_note(cls, data):
        """Notes saved before targets existed had a topic and a trip/moment scope."""
        if isinstance(data, dict) and data.get("toward") in _OLD_MONEY_POLES:
            # Before Ticket #21 money meant stay vs experiences; that says nothing about cheap vs splurge.
            data = {**data, "toward": None}
        if isinstance(data, dict) and "id" not in data and "topic" in data:
            digest = hashlib.sha1(f"{data['topic']}:{data['text']}".encode()).hexdigest()[:6]
            return {"id": f"o{digest}", "text": data["text"], "once": data.get("scope") == "moment",
                    "toward": data.get("toward")}
        return data


class Scale(BaseModel):
    """One sliding scale: -1 is the left pole (e.g. slow), +1 the right (e.g. packed)."""
    value: float = 0.0
    confidence: float = 0.0
    # Where it came from: "note", "chat" (budget/kids in the profile), later "asked" (#20). Older trips have "said".
    source: str | None = None


class Personality(BaseModel):
    """A trip's personality: the notes are the source of truth, the six scales are derived from them for the card."""
    scales: dict[str, Scale] = Field(default_factory=dict)
    notes: list[Note] = Field(default_factory=list)
    # Filled for the API only: known traits in plain words, and labels of the scales still unknown.
    traits: list[str] = Field(default_factory=list)
    unknown: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _drop_old_money_lean(cls, data):
        """Before the two budgets (Ticket #21) a money scale from notes meant stay vs experiences. A trip saved then
        still has those notes' old poles; its money scale is stale, not "cheap/splurge on experiences"."""
        if not isinstance(data, dict):
            return data
        poles = {n.get("toward") for n in data.get("notes") or [] if isinstance(n, dict)}
        money = (data.get("scales") or {}).get("money")
        if poles & _OLD_MONEY_POLES and not poles & _EXPERIENCES and isinstance(money, dict) \
                and money.get("source") == "note":
            data = {**data, "scales": {k: v for k, v in data["scales"].items() if k != "money"}}
        return data
