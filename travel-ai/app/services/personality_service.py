import re

from app.schemas.personality import Personality, Scale
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
DERIVED = {"taste", "chat"}  # recomputed every turn; other sources (answers to questions, #20) are kept

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
# Preference topics that are about the whole trip. Others are single wishes or rules (a diet, a spa day).
_TRIP_WIDE_KEYS = {"pace", "crowds", "nightlife"}
_WHOLE_WORD = {"party"}  # would otherwise match "party of four"
# Only for these does "no X" mean the other pole ("no nightlife" = chill evenings). Elsewhere a negation says
# nothing firm ("not cheap" isn't luxury), so the word is ignored.
_FLIPPABLE = {"nightlife", "party", "partying"}
_NEGATION = re.compile(r"\b(no|not|avoid\w*|without|skip|less|hate|away from|don't|dont)\b")
_BUDGET = {BudgetLevel.BUDGET: -0.8, BudgetLevel.MID: 0.0, BudgetLevel.LUXURY: 0.8}


def _average(signals: list[dict[str, float]], confidence: float, source: str) -> dict[str, Scale]:
    values: dict[str, list[float]] = {}
    for signal in signals:
        for name, v in signal.items():
            values.setdefault(name, []).append(v)
    return {name: Scale(value=round(sum(vs) / len(vs), 2), confidence=confidence, source=source)
            for name, vs in values.items()}


def _word_signals(text: str) -> dict[str, float]:
    """Scale pushes from one mood word or preference, e.g. "avoid crowds" -> crowds -0.6."""
    out: dict[str, float] = {}
    for clause in re.split(r"[,;]| but ", text.lower()):  # "no nightlife, lots of food": negation stays in its clause
        for stem, effects in _WORDS.items():
            match = re.search(rf"\b{re.escape(stem)}" + (r"\b" if stem in _WHOLE_WORD else ""), clause)
            if not match:
                continue
            if _NEGATION.search(clause[:match.start()]):
                if stem in _FLIPPABLE:
                    out.update({name: -v for name, v in effects.items()})
            else:
                out.update(effects)
    return out


def seed(taste: Taste) -> dict[str, Scale]:
    """Leanings from lasting taste, at low confidence: a starting point, never shown as known."""
    return _average([_VIBES[v] for v in taste.vibes if v in _VIBES], TASTE_CONFIDENCE, "taste")


def from_profile(profile: TripProfile) -> dict[str, Scale]:
    """What this trip's chat already says: budget, who's going, mood words and preferences."""
    scales: dict[str, Scale] = {}
    if profile.budget_level:
        scales["comfort"] = Scale(value=_BUDGET[profile.budget_level], confidence=CHAT_CONFIDENCE, source="chat")
    if profile.party == Party.FAMILY or profile.kids:
        scales["energy"] = Scale(value=-0.6, confidence=CHAT_CONFIDENCE, source="chat")
    texts = profile.mood + [p.value for p in profile.preferences if p.source == "stated" and p.key in _TRIP_WIDE_KEYS]
    # Words override budget and party: "a luxury trip on points" or "family, but we love a night out".
    scales.update(_average([_word_signals(t) for t in texts], CHAT_CONFIDENCE, "chat"))
    return scales


def trait(name: str, scale: Scale | None) -> str | None:
    """The scale in plain words, or None while it's unknown."""
    if scale is None or scale.confidence < KNOWN:
        return None
    _, left, right, middle = SCALES[name]
    return left if scale.value <= -LEAN else right if scale.value >= LEAN else middle


def leaning(name: str, scale: Scale | None) -> str | None:
    """A weak guess (from lasting taste) in plain words, or None."""
    if scale is None or scale.confidence >= KNOWN or abs(scale.value) < LEAN:
        return None
    _, left, right, _ = SCALES[name]
    return left if scale.value < 0 else right


class PersonalityService:

    @staticmethod
    def update(stored: Personality | None, profile: TripProfile, taste: Taste) -> Personality:
        """Recompute the derived scales from taste and the chat, keeping any set another way."""
        scales = {**seed(taste), **from_profile(profile)}
        if stored:
            scales.update({n: s for n, s in stored.scales.items() if s.source not in DERIVED})
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
