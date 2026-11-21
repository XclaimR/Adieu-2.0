import re

from app.schemas.personality import Note, NoteUpdate, Personality, Scale
from app.schemas.taste import Taste
from app.schemas.trip_profile import BudgetLevel, Party, TripProfile

# name: (card label, left pole, right pole, middle) in plain words.
SCALES: dict[str, tuple[str, str, str, str]] = {
    "pace": ("Pace", "Slow pace", "Packed days", "Steady pace"),
    "money": ("Money", "Money goes to the stay", "Money goes to experiences", "Money split between stay and experiences"),
    "energy": ("Energy", "Chill evenings", "Nightlife", "Some nights out"),
    "crowds": ("Crowds", "Avoids crowds", "Wants the icons", "Mix of icons and hidden gems"),
    "food": ("Food", "Food is just fuel", "Food is the point", "Good food matters"),
    "comfort": ("Comfort", "Backpacker comfort", "Luxury", "Mid-range comfort"),
}
KNOWN = 0.5  # below this a scale is shown as unknown
LEAN = 0.25  # |value| below this reads as the middle
TASTE_CONFIDENCE = 0.3
CHAT_CONFIDENCE = 0.7
# "taste" and "chat" (the word list over the profile) are recomputed every turn. Others are kept until replaced:
# "said" (the traveler's words, read from one message) and, later, "asked" (answers to questions, #20).
DERIVED = {"taste", "chat"}

# Lasting-taste vibes (TasteService.VIBES) -> leanings.
_VIBES: dict[str, dict[str, float]] = {
    "food": {"food": 0.7},
    "nightlife": {"energy": 0.7},
    "city": {"energy": 0.3},
    "wellness": {"pace": -0.6, "energy": -0.5},
    "beach": {"pace": -0.4},
    "luxury": {"comfort": 0.7, "money": -0.4},
    "budget": {"comfort": -0.7},
    "adventure": {"pace": 0.4, "money": 0.5},
    "photo": {"crowds": 0.4},
    "nature": {"crowds": -0.4},
}

# Words that describe the WHOLE trip and clearly point at one pole of ONE scale. Left out on purpose:
# - vague words ("fun", "nice", "adventurous"): they don't say which way a scale goes;
# - single activities ("a spa", "pub crawl", "fine dining", "a hike", "landmarks"): a wish for one day, not the trip;
# - second effects ("relaxed" is slow pace, not chill evenings).
# Anything not here stays unknown for the questions in #20 to settle.
_WORDS: dict[str, dict[str, float]] = {
    "slow": {"pace": -0.7}, "relax": {"pace": -0.7}, "leisur": {"pace": -0.7},
    "unwind": {"pace": -0.6}, "laid-back": {"pace": -0.6}, "chill": {"pace": -0.6},
    "packed": {"pace": 0.7}, "fast-paced": {"pace": 0.7}, "see everything": {"pace": 0.7}, "active": {"pace": 0.5},
    "nightlife": {"energy": 0.8}, "party": {"energy": 0.8}, "partying": {"energy": 0.8},
    "hidden gem": {"crowds": -0.7}, "off the beaten": {"crowds": -0.7},
    "avoid crowd": {"crowds": -0.7}, "no crowd": {"crowds": -0.7}, "away from the crowd": {"crowds": -0.7},
    "tourist trap": {"crowds": 0.6}, "iconic": {"crowds": 0.7}, "the icons": {"crowds": 0.7}, "must-see": {"crowds": 0.7},
    "food": {"food": 0.8}, "culinar": {"food": 0.8}, "gastro": {"food": 0.8},
    "luxur": {"comfort": 0.8}, "upscale": {"comfort": 0.8}, "five-star": {"comfort": 0.8}, "5-star": {"comfort": 0.8},
    "backpack": {"comfort": -0.8}, "shoestring": {"comfort": -0.8}, "cheap": {"comfort": -0.6},
}
_WHOLE_WORD = {"party"}  # would otherwise match "party of four"
# Only for these does "no X" mean the other pole ("no nightlife" = chill evenings). Elsewhere a negation says
# nothing firm ("not cheap" isn't luxury), so the word is ignored.
_FLIPPABLE = {"nightlife", "party", "partying", "tourist trap"}
_NEGATION = re.compile(r"\b(no|not|avoid\w*|without|skip|less|hate|away from|don't|dont)\b")
# pole -> (scale, direction)
POLES = {
    "slow": ("pace", -1), "packed": ("pace", 1), "spend_on_stay": ("money", -1), "spend_on_experiences": ("money", 1),
    "chill": ("energy", -1), "nightlife": ("energy", 1), "hidden_gems": ("crowds", -1), "icons": ("crowds", 1),
    "food_as_fuel": ("food", -1), "food_focus": ("food", 1), "backpacker": ("comfort", -1), "luxury": ("comfort", 1),
}
# Intensity, read from the traveler's own words around the trait.
_STRONG = re.compile(r"\b(very|really|super|so|extremely|totally|absolutely|all about|only|lots of|tons of)\b")
_MILD = re.compile(r"\b(a bit|a little|slightly|somewhat|kind of|kinda|sort of|fairly|bit of)\b")
_UNSURE = re.compile(r"\b(maybe|perhaps|might|not sure|probably|possibly|i guess|could be)\b")
# A phrase about one moment ("one big night out", "a spa afternoon") is a wish for that day, not the trip's personality.
_ONE_OFF = re.compile(r"\b(one|a single|once)\b(?! (week|month|fortnight|trip|holiday|vacation))"
                      r"|\ban? (\w+ )?(afternoon|evening|morning|night|day|dinner|lunch)\b")
# Words that don't say which way any scale goes: vague praise and generic trip talk. A quote made only of these
# (plus place names) proves nothing; neither does a whole sentence quoted as proof.
_VAGUE = {"fun", "nice", "amazing", "great", "cool", "good", "awesome", "best", "exciting", "interesting", "special",
          "memorable", "unique", "different", "epic", "lovely", "perfect", "trip", "holiday", "vacation", "a", "an",
          "the", "and", "with", "we", "i", "want", "something", "really", "very", "so", "super", "planning", "plan",
          "going", "go", "visit", "visiting", "travel", "traveling", "travelling", "to", "in", "for", "my", "our",
          "friends", "partner", "wife", "husband", "family", "kids", "us", "me", "love", "like", "would", "we'd",
          "i'd", "some", "somewhere", "place", "of", "on", "at", "it", "is", "be"}
_MAX_QUOTE_WORDS = 12  # a note is a phrase; a whole paragraph quoted as proof proves nothing
MODEL_ONLY_CONFIDENCE = 0.4  # a pole only the model read: a weak guess for prompts, "not sure yet" on the card
MAX_NOTES = 30  # shortcut: oldest notes drop off past this; summarise instead if real trips hit it
_BUDGET = {BudgetLevel.BUDGET: -0.8, BudgetLevel.MID: 0.0, BudgetLevel.LUXURY: 0.8}


def _clauses(message: str) -> list[str]:
    """The message in phrases, so "no" and "one" stay with the words they belong to."""
    # "but" opens the next phrase (kept, so quotes that include it still match the traveler's words).
    return [c for c in (_words(c) for c in re.split(r"[,;.!?:]|(?= but )", message.lower())) if c]


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9'-]+", text.lower().replace("’", "'")))


def _stems(clause: str) -> list[tuple[str, int]]:
    """(listed stem, position) found in a clause."""
    found = []
    for stem in _WORDS:
        match = re.search(rf"\b{re.escape(stem)}" + (r"\b" if stem in _WHOLE_WORD else ""), clause)
        if match:
            found.append((stem, match.start()))
    return found


def _size(direction: int, lead: str, clause: str) -> Scale:
    """How strongly and how surely, from the traveler's words just before the trait and the rest of the phrase."""
    amount, confidence = (0.9, 0.85) if _STRONG.search(lead) else (0.4, 0.7) if _MILD.search(lead) else (0.7, 0.75)
    if _UNSURE.search(clause):
        confidence = 0.4  # a guess: kept as a weak lean, shown as unknown
    return Scale(value=direction * amount, confidence=confidence, source="said")


def _lead(clause: str, at: int, words: str) -> str:
    """The trait's words plus the three before them: where "really" or "a bit" would be."""
    return " ".join(clause[:at].split()[-3:] + [words])


def _listed(clause: str) -> dict[str, tuple[int, int]]:
    """Scales the word list reads in one phrase: name -> (direction, position). "no nightlife" flips; other
    negations ("not cheap") say nothing firm, so the word is ignored."""
    out = {}
    for stem, at in _stems(clause):
        negated = _NEGATION.search(clause[:at])
        if negated and stem not in _FLIPPABLE:
            continue
        for name, v in _WORDS[stem].items():
            out[name] = (-1 if (v < 0) != bool(negated) else 1, at)
    return out


def _covering(quote: str, clauses: list[str]) -> list[str]:
    """The phrases of the message a quote spans, or [] if those aren't the traveler's exact words."""
    text, spans, at = " ".join(clauses), [], 0
    for c in clauses:
        spans.append((at, at + len(c), c))
        at += len(c) + 1
    start = f" {text} ".find(f" {quote} ") if quote else -1
    if start < 0:
        return []
    return [c for s, e, c in spans if s < start + len(quote) and e > start]


def _check_note(update: NoteUpdate, clauses: list[str], place: set[str]) -> tuple[Note, str] | None:
    """Keep a model-read note only if it is the traveler's own words and says something. Code, not the model,
    decides it's a one-off ("one big night out" is a moment), and drops a pole the words don't support."""
    quote = _words(update.quote)
    covered = _covering(quote, clauses)  # whole words only; may run across a comma
    if not covered or len(quote.split()) > _MAX_QUOTE_WORDS:
        return None  # not their words, or a whole paragraph
    if not [w for w in quote.split() if w not in _VAGUE | place and w[0].isalpha()]:
        return None  # "fun", "planning a trip to Japan": nothing to keep
    clause = " ".join(covered)
    scope = "moment" if any(_ONE_OFF.search(c) for c in covered) else update.scope
    toward = update.toward if scope == "trip" else None
    if toward:
        name, direction = POLES[toward]
        listed = _listed(quote)
        if listed and (name not in listed or listed[name][0] != direction):
            toward = None  # the words point the other way, or are about another scale
        elif _NEGATION.search(quote) and any(name in _WORDS[st] for st, _ in _stems(quote)):
            toward = None  # "not too packed" isn't packed, and isn't firmly slow either
    topic = "_".join(_words(update.topic).split())[:40] or "note"
    return Note(topic=topic, text=update.quote.strip(), scope=scope, toward=toward), clause


def read_message(message: str, notes: list[NoteUpdate] = (), destination: str | None = None
                 ) -> tuple[dict[str, Scale], list[Note]]:
    """What the latest message says: scales from the traveler's own words (word list), and the checked notes.
    A pole only the model read (no listed word confirms it) is kept as a weak guess."""
    clauses = _clauses(message)
    trip_wide = [c for c in clauses if not _ONE_OFF.search(c)]
    place = set(_words(destination or "").split())  # shortcut: other place names in the message still count as words
    scales: dict[str, Scale] = {}
    for clause in trip_wide:
        for name, (direction, at) in _listed(clause).items():
            scales[name] = _size(direction, _lead(clause, at, clause[at:].split(" ", 1)[0]), clause)
    kept: list[Note] = []
    for update in notes:
        checked = _check_note(update, clauses, place)
        if checked is None:
            continue
        note, clause = checked
        kept.append(note)
        if note.toward and POLES[note.toward][0] not in scales:
            name, direction = POLES[note.toward]
            quote = _words(note.text)
            scale = _size(direction, _lead(clause, f" {clause} ".index(f" {quote} "), quote), clause)
            scales[name] = scale.model_copy(update={"confidence": min(scale.confidence, MODEL_ONLY_CONFIDENCE)})
    return scales, kept


def seed(taste: Taste) -> dict[str, Scale]:
    """Leanings from lasting taste, at low confidence: a starting point, never shown as known."""
    values: dict[str, list[float]] = {}
    for vibe in taste.vibes:
        for name, v in _VIBES.get(vibe, {}).items():
            values.setdefault(name, []).append(v)
    return {n: Scale(value=round(sum(vs) / len(vs), 2), confidence=TASTE_CONFIDENCE, source="taste")
            for n, vs in values.items()}


def from_profile(profile: TripProfile) -> dict[str, Scale]:
    """Checked profile facts: budget and kids. Mood words aren't read here; the model may have written them."""
    scales: dict[str, Scale] = {}
    if profile.budget_level:
        scales["comfort"] = Scale(value=_BUDGET[profile.budget_level], confidence=CHAT_CONFIDENCE, source="chat")
    if profile.party == Party.FAMILY or profile.kids:
        scales["energy"] = Scale(value=-0.6, confidence=CHAT_CONFIDENCE, source="chat")
    return scales


def trait(name: str, scale: Scale | None) -> str | None:
    """The scale in plain words, or None while it's unknown."""
    if scale is None or scale.confidence < KNOWN:
        return None
    _, left, right, middle = SCALES[name]
    if abs(scale.value) < LEAN:
        return middle
    pole = left if scale.value < 0 else right
    return f"{pole} (strongly)" if abs(scale.value) >= 0.85 else f"{pole} (a little)" if abs(scale.value) < 0.5 else pole


def leaning(name: str, scale: Scale | None) -> str | None:
    """A weak guess (from lasting taste) in plain words, or None."""
    if scale is None or scale.confidence >= KNOWN or abs(scale.value) < LEAN:
        return None
    _, left, right, _ = SCALES[name]
    return left if scale.value < 0 else right


class PersonalityService:

    @staticmethod
    def update(stored: Personality | None, profile: TripProfile, taste: Taste, notes: list[NoteUpdate] = (),
               message: str = "", drop_topics: list[str] = ()) -> Personality:
        """Taste and checked profile facts are recomputed; what earlier messages said is kept; what the latest
        message says wins. A note on the same topic replaces the old one ("actually, packed days")."""
        scales = {**seed(taste), **from_profile(profile)}
        if stored:
            scales.update({n: s for n, s in stored.scales.items() if s.source not in DERIVED})
        gone = {"_".join(_words(t).split()) for t in drop_topics}
        for n in (stored.notes if stored else []):
            if n.topic in gone and n.toward and scales.get(POLES[n.toward][0], Scale()).source == "said":
                del scales[POLES[n.toward][0]]  # "forget the slow thing": the card forgets it too
        said, new_notes = read_message(message, notes, profile.destination)
        for name, scale in said.items():
            old = scales.get(name)
            # A weak model-only guess never overwrites something the traveler clearly said earlier.
            if scale.confidence >= KNOWN or not (old and old.source not in DERIVED and old.confidence >= KNOWN):
                scales[name] = scale
        new_notes = list({n.topic: n for n in new_notes}.values())
        dropped = gone | {n.topic for n in new_notes}
        kept = [n for n in (stored.notes if stored else []) if n.topic not in dropped] + new_notes
        return Personality(scales=scales, notes=kept[-MAX_NOTES:])

    @staticmethod
    def card(personality: Personality | None) -> Personality:
        """Adds the plain-word traits and the unknown scales, for the trip screen."""
        p = personality or Personality()
        return p.model_copy(update={
            "traits": PersonalityService.traits(p)[0],
            "unknown": [label for n, (label, *_) in SCALES.items() if not trait(n, p.scales.get(n))],
        })

    @staticmethod
    def traits(personality: Personality | None) -> tuple[list[str], list[str]]:
        """(known traits, weak leanings) in plain words."""
        scales = personality.scales if personality else {}
        known = [t for n in SCALES if (t := trait(n, scales.get(n)))]
        leans = [t for n in SCALES if (t := leaning(n, scales.get(n)))]
        return known, leans

    @staticmethod
    def moments(personality: Personality | None) -> list[str]:
        """The one-off things to fit in, in the order the prompts number them ([m0], [m1], ...)."""
        return [n.text for n in (personality.notes if personality else []) if n.scope == "moment"]

    @staticmethod
    def match_trait(said: str | None, allowed: list[str]) -> str | None:
        """The listed trait the model named, or None: it may only name traits we actually know."""
        said = (said or "").strip().strip(".").lower()
        return next((t for t in allowed if t.lower() == said), None)
