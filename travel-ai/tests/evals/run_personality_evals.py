"""One live-model call per sentence: does the model read the message into the right notes, on the right targets?
Much faster than the persona suite (no Places, no drafts). Run it before changing the model, the turn prompt or
the note schema in app/schemas/personality.py.

    docker compose exec api python -m tests.evals.run_personality_evals
"""
import sys
import time

from app.core.config import settings
from app.schemas.activity import Activity
from app.schemas.dayplan import DayPlan
from app.schemas.itinerary import Itinerary
from app.schemas.personality import Note, Personality
from app.schemas.place import Pick
from app.schemas.trip_profile import TripProfile
from app.services.personality_service import PersonalityService, check_changes
from app.services.trip_profile_service import TripProfileService
from app.services.turn_service import TurnService

PLAN = Itinerary(
    destination="Tokyo",
    stay=Pick(place_id="p", name="Palace Hotel Tokyo", category="stay", tier="luxury"),
    days=[DayPlan(day_number=1, activities=[
              Activity(id="i1", name="Senso-ji", category="do", time_of_day="morning"),
              Activity(id="i2", name="Afuri Ramen", category="eat", time_of_day="afternoon")]),
          DayPlan(day_number=2, activities=[
              Activity(id="i3", name="teamLab Planets", category="do", time_of_day="morning"),
              Activity(id="i4", name="Narisawa", category="eat", time_of_day="evening")])])
SAVED = Personality(notes=[
    Note(id="n1", text="beach", kind="avoid"),
    Note(id="n2", text="nightlife", kind="avoid"),
    Note(id="n3", text="birthday dinner", target="i4"),
])


def ids(r):
    return {n.id for n in r["after"].notes}


def traits(r):
    return " ".join(PersonalityService.card(r["after"]).traits)


def has(r, target, *words):
    return any(c.op == "add" and c.target == target and any(w in c.text.lower() for w in words) for c in r["changes"])


def changed(r, *targets):
    return {c.target for c in r["changes"] if c.then == "change"} == set(targets)


# (message, on the saved Tokyo plan?, check)
CASES = [
    ("My wife and I want a slow food week in Lisbon.", False,
     lambda r: r["mood"] and "Slow pace" in traits(r) and "Food is the point" in traits(r)),
    ("A slow week in Rome, but one big night out for my birthday.", False,
     lambda r: "Nightlife" not in traits(r) and PersonalityService.moments(r["after"])),
    ("Going solo on a hiking trip in the Dolomites for 5 days.", False, lambda r: r["mood"] and not traits(r)),
    ("I want to go to Japan", False, lambda r: not r["mood"] and not traits(r)),
    ("Planning a fun trip to Japan with friends!", False, lambda r: not r["mood"] and not traits(r)),
    ("Bali for a week, no beach and no nightlife", False,
     lambda r: len([n for n in r["after"].notes if n.kind == "avoid"]) >= 2 and "beach" not in r["mood"]
     and not any(n.text.lower().startswith("no ") for n in r["after"].notes if n.kind == "avoid")),
    ("Sin museos, por favor", False, lambda r: [n for n in r["after"].notes if n.kind == "avoid"]),
    ("Vegetarian food only", False, lambda r: "Food is the point" not in traits(r) and r["after"].notes),
    ("The day 1 stay is too much, find something similar but cheaper", True,
     lambda r: has(r, "s1", "cheap") and changed(r, "s1")),
    ("Make that birthday dinner cheaper, but keep it special", True,
     lambda r: has(r, "i4", "cheap") and "n3" in ids(r) and changed(r, "i4")),
    ("Find a different place for the birthday dinner", True, lambda r: "n3" in ids(r) and changed(r, "i4")),
    ("Nothing early on day 2", True, lambda r: has(r, "d2", "early", "late", "morning")),
    ("Swap the ramen place for sushi", True, lambda r: changed(r, "i2")),
    ("Move Senso-ji to day 2", True, lambda r: "i1" in {c.target for c in r["changes"] if c.then == "change"}),
    ("Actually the beach is fine", True, lambda r: "n1" not in ids(r) and {"n2", "n3"} <= ids(r)),
    ("Thanks, that looks lovely", True, lambda r: not r["changes"]),
    ("We're also bringing our 3 year old", True,
     lambda r: any(n.source == "inferred" for n in r["after"].notes) and {"n1", "n2", "n3"} <= ids(r)),
]


def read(message: str, saved: bool) -> dict:
    itinerary, before = (PLAN, SAVED) if saved else (None, Personality())
    profile = TripProfile(destination="Tokyo", duration_days=2, mood=["romantic"]) if saved else TripProfile()
    decision = TurnService.decide(message, [], profile, PersonalityService.plan_view(itinerary, before), itinerary)
    changes = check_changes(decision.note_changes, message, before, itinerary)
    merged = TripProfileService.merge(profile, decision.profile_updates, message=message)
    after = PersonalityService.update(before, merged, changes)
    return {"changes": changes, "after": after, "mood": PersonalityService.moods(profile.mood, before, after)}


def main() -> int:
    print(f"Model: {settings.LLM_MODEL}")
    failed = skipped = 0
    for message, saved, check in CASES:
        start = time.time()
        try:
            result = read(message, saved)
        except Exception as e:  # a shared free-tier key is often rate limited; don't lose the other cases
            skipped += 1
            print(f"SKIP {message!r}: {type(e).__name__}")
            continue
        ok = bool(check(result))
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'} ({time.time() - start:.0f}s) {message!r}")
        for c in result["changes"]:
            print(f"     {c.op} {c.target} {c.kind} {c.text!r} {c.source} then={c.then} mood={c.mood} toward={c.toward}")
    print(f"\n{failed} failure(s), {skipped} skipped, across {len(CASES)} case(s)")
    return 1 if failed or skipped else 0


if __name__ == "__main__":
    sys.exit(main())
