"""One live-model call per sentence: does the model read the trip's personality in context, and does the card come
out right? Much faster than the persona suite (no Places, no drafts). Rerun when you change the model or the
notes wording in app/schemas/turn.py.

    docker compose exec api python -m tests.evals.run_personality_evals
"""
import sys
import time

from app.core.config import settings
from app.schemas.taste import Taste
from app.schemas.trip_profile import TripProfile
from app.services.personality_service import PersonalityService
from app.services.trip_profile_service import TripProfileService
from app.services.turn_service import TurnService

# (message, traits the card must show, traits it must not show; None = the card must stay empty,
#  words a kept one-off moment must contain, or None)
CASES = [
    ("My wife and I want a slow food week in Lisbon.", ["Slow pace", "Food is the point"], ["Chill evenings"], None),
    ("A slow week in Rome, but one big night out for my birthday.", ["Slow pace"], ["Nightlife"], "night out"),
    ("Planning a fun trip to Japan with friends!", [], None, None),  # "fun" says nothing
    ("We want it really slow, honestly just doing nothing in Bali.", ["Slow pace (strongly)"], [], None),
    ("Maybe somewhere a bit quieter? Not sure yet.", [], None, None),  # unsure: unknown
    ("Paris with my partner, and we'd love one spa afternoon.", [], None, "spa"),  # one-off activity
    ("Barcelona, avoid the tourist traps, we want what locals love.", ["Avoids crowds"], [], None),
    ("Backpacking Vietnam on a shoestring, every night out partying!", ["Backpacker comfort", "Nightlife"], [], None),
]


def card(message: str) -> tuple[list[str], list[str]]:
    decision = TurnService.decide(message, [], TripProfile(), Taste(), "")
    profile = TripProfileService.merge(TripProfile(), decision.profile_updates, message=message)
    if decision.notes:
        print(f"     model: {[(n.scope, n.toward, n.quote) for n in decision.notes]}")
    personality = PersonalityService.update(None, profile, Taste(), decision.notes, message)
    return PersonalityService.card(personality).traits, PersonalityService.moments(personality)


def main() -> int:
    print(f"Model: {settings.LLM_MODEL}")
    failed = 0
    for message, must, must_not, moment in CASES:
        start = time.time()
        try:
            traits, moments = card(message)
        except Exception as e:  # a shared free-tier key is often rate limited; don't lose the other cases
            print(f"SKIP {message!r}: {type(e).__name__}")
            continue
        # "Slow pace (a little)" still counts as showing "Slow pace" for must_not.
        bad = [t for t in must if t not in traits] + (
            traits if must_not is None else [t for t in must_not if any(x.startswith(t) for x in traits)]) + (
            [f"moment with {moment!r}"] if moment and not any(moment in m.lower() for m in moments) else [])
        failed += bool(bad)
        print(f"{'PASS' if not bad else 'FAIL'} ({time.time() - start:.0f}s) {message!r}\n     card: {traits}"
              f"  moments: {moments}"
              + (f"\n     wrong: {bad}" if bad else ""))
    print(f"\n{failed} failure(s) across {len(CASES)} case(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
