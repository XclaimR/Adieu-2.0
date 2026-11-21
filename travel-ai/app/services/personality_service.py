import re

from app.schemas.personality import Personality, Scale, ScaleSignal
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
# "said" (a checked ScaleSignal from one message) and, later, "asked" (answers to questions, #20).
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
    "iconic": {"crowds": 0.7}, "the icons": {"crowds": 0.7}, "must-see": {"crowds": 0.7},
    "food": {"food": 0.8}, "culinar": {"food": 0.8}, "gastro": {"food": 0.8},
    "luxur": {"comfort": 0.8}, "upscale": {"comfort": 0.8}, "five-star": {"comfort": 0.8}, "5-star": {"comfort": 0.8},
    "backpack": {"comfort": -0.8}, "shoestring": {"comfort": -0.8}, "cheap": {"comfort": -0.6},
}
_WHOLE_WORD = {"party"}  # would otherwise match "party of four"
# Only for these does "no X" mean the other pole ("no nightlife" = chill evenings). Elsewhere a negation says
# nothing firm ("not cheap" isn't luxury), so the word is ignored.
_FLIPPABLE = {"nightlife", "party", "partying"}
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
# Words that sound positive but don't say which way any scale goes. A quote made only of these proves nothing.
_VAGUE = {"fun", "nice", "amazing", "great", "cool", "good", "awesome", "best", "exciting", "interesting", "special",
          "memorable", "unique", "different", "epic", "lovely", "perfect", "trip", "holiday", "vacation", "a", "an",
          "the", "and", "with", "we", "i", "want", "something", "really", "very", "so", "super"}
_BUDGET = {BudgetLevel.BUDGET: -0.8, BudgetLevel.MID: 0.0, BudgetLevel.LUXURY: 0.8}


def _clauses(message: str) -> list[str]:
    """The message in phrases, so "no" and "one" stay with the words they belong to."""
    return [c for c in (_words(c) for c in re.split(r"[,;.!?:]| but ", message.lower())) if c]


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


def read_message(message: str, signals: list[ScaleSignal] = ()) -> dict[str, Scale]:
    """What the latest message says about the whole trip.
    The word list reads the traveler's own words; the model's signals (read in context) add what the list doesn't
    know. Either way the code checks it's really their words and sizes it from them."""
    clauses = [c for c in _clauses(message) if not _ONE_OFF.search(c)]
    scales: dict[str, Scale] = {}
    for clause in clauses:
        for name, (direction, at) in _listed(clause).items():
            scales[name] = _size(direction, _lead(clause, at, clause[at:].split(" ", 1)[0]), clause)
    for signal in signals:
        name, direction = POLES[signal.toward]
        quote = _words(signal.quote)
        clause = next((c for c in clauses if quote and quote in c), None)
        if name in scales or clause is None or set(quote.split()) <= _VAGUE:
            continue  # the list already read it / not their words or a one-off / "fun" alone says nothing
        listed = _listed(quote)
        if listed and (name not in listed or listed[name][0] != direction):
            continue  # the words point the other way, or are about another scale
        if _NEGATION.search(quote) and any(name in _WORDS[st] for st, _ in _stems(quote)):
            continue  # "not too packed" isn't packed, and isn't firmly slow either
        # Other negations are the model's reading in context: "avoid the tourist traps" is hidden gems.
        scales[name] = _size(direction, _lead(clause, clause.index(quote), quote), clause)
    return scales


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
    def update(stored: Personality | None, profile: TripProfile, taste: Taste,
               signals: list[ScaleSignal] = (), message: str = "") -> Personality:
        """Taste and checked profile facts are recomputed; what earlier messages said is kept; what the latest
        message says wins."""
        scales = {**seed(taste), **from_profile(profile)}
        if stored:
            scales.update({n: s for n, s in stored.scales.items() if s.source not in DERIVED})
        scales.update(read_message(message, signals))
        return Personality(scales=scales)

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
    def match_trait(said: str | None, allowed: list[str]) -> str | None:
        """The listed trait the model named, or None: it may only name traits we actually know."""
        said = (said or "").strip().strip(".").lower()
        return next((t for t in allowed if t.lower() == said), None)
