import re
import uuid

from app.schemas.itinerary import Itinerary
from app.schemas.personality import Note, NoteChange, Personality, Scale
from app.schemas.trip_profile import BudgetLevel, Party, TripProfile

# name: (card label, left pole, right pole, middle) in plain words.
SCALES: dict[str, tuple[str, str, str, str]] = {
    "pace": ("Pace", "Slow pace", "Packed days", "Steady pace"),
    # Two budgets (Ticket #21): what to spend on experiences, and on stays (the key stays "comfort" for saved trips).
    "money": ("Experiences", "Cheap experiences", "Splurge on experiences", "Some paid experiences"),
    "energy": ("Energy", "Chill evenings", "Nightlife", "Some nights out"),
    "crowds": ("Crowds", "Avoids crowds", "Wants the icons", "Mix of icons and hidden gems"),
    "food": ("Food", "Food is just fuel", "Food is the point", "Good food matters"),
    "comfort": ("Comfort", "Cheap stays", "Splurge on stays", "Mid-range stays"),
}
KNOWN = 0.5  # below this a scale is shown as unknown
LEAN = 0.25  # |value| below this reads as the middle
STATED_CONFIDENCE = 0.75
INFERRED_CONFIDENCE = 0.4  # a guess for prompts, "not sure yet" on the card
CHAT_CONFIDENCE = 0.7
# Scales kept from before: older trips' "said" ones, swipes (#21) and, later, answers to questions (#20).
KEPT = {"said", "asked", "swiped"}
# pole -> (scale, direction)
POLES = {
    "slow": ("pace", -1), "packed": ("pace", 1), "cheap_experiences": ("money", -1), "splurge_experiences": ("money", 1),
    "chill": ("energy", -1), "nightlife": ("energy", 1), "hidden_gems": ("crowds", -1), "icons": ("crowds", 1),
    "food_as_fuel": ("food", -1), "food_focus": ("food", 1), "backpacker": ("comfort", -1), "luxury": ("comfort", 1),
}
_BUDGET = {BudgetLevel.BUDGET: -0.8, BudgetLevel.MID: 0.0, BudgetLevel.LUXURY: 0.8}


def _words(text: str) -> str:
    return " ".join(re.findall(r"[\w'-]+", text.lower().replace("’", "'")))


def _clean(text: str) -> str:
    """Small models sometimes leak JSON punctuation into strings."""
    return re.sub(r"[\[\]{}\"]+", "", text.split("',")[0]).strip(" '\t\n,.:;")[:120]


def check_changes(changes: list[NoteChange], message: str, stored: Personality | None,
                  itinerary: Itinerary | None) -> list[NoteChange]:
    """Code checks structure only, never meaning: real words, ids that exist, targets that exist."""
    said = f" {_words(message)} "
    ids = {n.id for n in stored.notes} if stored else set()
    targets = {"trip", *(itinerary.target_ids() if itinerary else ())}
    kept = []
    for change in changes:
        quote, text = _words(change.quote), _clean(change.text)
        # Scripts without spaces (Japanese, Thai) have no word edges to match on.
        real = f" {quote} " in said or (not quote.isascii() and quote in said)
        if not quote or not real or not text:
            continue
        if change.op == "remove" and change.id not in ids:
            continue
        target, then = change.target, change.then
        if itinerary and then == "change" and change.category == "stay" and re.fullmatch(r"d\d+", target):
            # The stay serves every night (in that day's city on a route), whichever day they mention.
            target = itinerary.stay_id(next((d for d in itinerary.days if d.id == target), None))
        if target not in targets:
            # Without a plan everything is about the trip; with one, a wrong id must not edit the whole trip.
            target, then = "trip", "none" if itinerary else then
        kept.append(change.model_copy(update={"text": text, "target": target, "then": then}))
    return kept


def _from_profile(profile: TripProfile) -> dict[str, Scale]:
    scales: dict[str, Scale] = {}
    if profile.budget_level:
        for name in ("comfort", "money"):  # a trip budget is both budgets
            scales[name] = Scale(value=_BUDGET[profile.budget_level], confidence=CHAT_CONFIDENCE, source="chat")
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
    """A weak guess in plain words, or None."""
    if scale is None or scale.confidence >= KNOWN or abs(scale.value) < LEAN:
        return None
    _, left, right, _ = SCALES[name]
    return left if scale.value < 0 else right


def describe_note(note: Note) -> str:
    guess = ", inferred: a guess, never a reason to drop what they said" if note.source == "inferred" else ""
    return f"{note.kind}: {note.text}{guess}"


class PersonalityService:

    @staticmethod
    def update(stored: Personality | None, profile: TripProfile, changes: list[NoteChange] = ()) -> Personality:
        """Applies checked changes. Nothing is ever overwritten: a changed mind is a remove plus an add."""
        old = stored.notes if stored else []
        gone = {c.id for c in changes if c.op == "remove"}
        notes = [n for n in old if n.id not in gone]
        have = {(n.target, n.kind, n.text.lower()) for n in notes}
        for c in changes:
            key = (c.target, c.kind, c.text.lower())
            if c.op != "add" or key in have:
                continue
            have.add(key)
            # shortcut: no cap, dropping the oldest would lose "no beach"; summarise if trips pass ~50 notes.
            notes.append(Note(id=f"n{uuid.uuid4().hex[:6]}",
                              **c.model_dump(include=set(Note.model_fields) - {"id"})))

        scales = _from_profile(profile)
        if stored:
            forgotten = {POLES[n.toward][0] for n in old if n.id in gone and n.toward}
            scales.update({k: s for k, s in stored.scales.items() if s.source in KEPT and k not in forgotten
                           and not (k in scales and scales[k].confidence > s.confidence)})
        for n in notes:
            if n.toward and n.target == "trip" and not n.once and n.category != "diet":
                name, direction = POLES[n.toward]
                confidence = INFERRED_CONFIDENCE if n.source == "inferred" else STATED_CONFIDENCE
                # A guess never overwrites something known or surer (swipes).
                if confidence < KNOWN and name in scales and scales[name].confidence > confidence:
                    continue
                scales[name] = Scale(value=direction * (0.9 if n.strong else 0.7), confidence=confidence, source="note")
        return Personality(scales=scales, notes=notes)

    @staticmethod
    def moods(current: list[str], before: Personality | None, after: Personality) -> list[str]:
        """The trip's moods follow its stated notes: taking a note back takes its mood with it."""
        def said(p: Personality | None) -> list[str]:
            return [n.mood.strip().lower() for n in (p.notes if p else [])
                    if n.mood and n.target == "trip" and n.source == "stated" and not n.once and n.kind != "avoid"]
        now = said(after)
        lost = set(said(before)) - set(now)
        return list(dict.fromkeys([m for m in current if m not in lost] + now))

    @staticmethod
    def detach(personality: Personality, keep: set[str] = frozenset()) -> Personality:
        """Notes whose slot is gone (new destination, another plan) become things to fit in."""
        notes = [n if n.target in {"trip", *keep} else n.model_copy(update={"target": "trip", "once": True})
                 for n in personality.notes]
        return personality.model_copy(update={"notes": notes})

    @staticmethod
    def plan_view(itinerary: Itinerary | None, personality: Personality | None,
                  days: set[str] | None = None) -> str:
        """The trip with each note under what it is about. `days` limits it to the part being worked on."""
        notes = personality.notes if personality else []

        def under(target: str, pad: str = "  ") -> list[str]:
            return [f"{pad}note {n.id} ({describe_note(n)})" for n in notes if n.target == target]

        lines = ["TRIP [trip]", *under("trip")]
        if not itinerary:
            return "\n".join(lines)
        for sid, stay, city in itinerary.stay_slots():
            price = f", est. {stay.tier} price" if stay and stay.tier else ""
            nights = next((f"{c.nights} nights in {c.name}" for c in itinerary.route if c.name == city), "all nights")
            lines += [f"STAY [{sid}] {stay.name if stay else 'none chosen yet'}{price}, {nights}", *under(sid)]
        for day in itinerary.days:
            if days is not None and day.id not in days:
                continue
            where = f" (in {day.city}{', moving day' if itinerary.moving_day(day) else ''})" if day.city else ""
            lines += [f"DAY {day.day_number} [{day.id}]{where} {day.theme or ''}".rstrip(), *under(day.id)]
            for a in day.activities:
                lines += [f"  [{a.id}] {a.name} ({a.category}, {a.time_of_day or 'any time'})", *under(a.id, "    ")]
        return "\n".join(lines)

    @staticmethod
    def card(personality: Personality | None) -> Personality:
        """Adds the plain-word traits and the unknown scales, for the trip screen."""
        p = personality or Personality()
        swiped = {t for n in SCALES if (s := p.scales.get(n)) and s.source == "swiped" and (t := trait(n, s))}
        return p.model_copy(update={
            "traits": [f"{t} (from your swipes)" if t in swiped else t for t in PersonalityService.traits(p)[0]],
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
        return [n.text for n in (personality.notes if personality else [])
                if n.once and n.target == "trip" and n.kind != "avoid"]

    @staticmethod
    def match_trait(said: str | None, allowed: list[str]) -> str | None:
        """The listed trait the model named, or None: it may only name traits we actually know."""
        said = (said or "").strip().strip(".").lower()
        return next((t for t in allowed if t.lower() == said), None)
