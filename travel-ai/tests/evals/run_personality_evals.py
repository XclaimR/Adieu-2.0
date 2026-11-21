"""One live-model call per sentence: does the model read the trip's personality in context, and does the card come
out right? Much faster than the persona suite (no Places, no drafts). Rerun when you change the model or the
scale_signals wording in app/schemas/turn.py.

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

# (message, traits the card must show, traits it must not show; None = the card must stay empty)
CASES = [
    ("My wife and I want a slow food week in Lisbon.", ["Slow pace", "Food is the point"], ["Chill evenings"]),
    ("A slow week in Rome, but one big night out for my birthday.", ["Slow pace"], ["Nightlife"]),
    ("Planning a fun trip to Japan with friends!", [], None),  # "fun" says nothing
    ("We want it really slow, honestly just doing nothing in Bali.", ["Slow pace (strongly)"], []),
    ("Maybe somewhere a bit quieter? Not sure yet.", [], None),  # unsure: unknown
    ("Paris with my partner, and we'd love one spa afternoon.", [], None),  # one-off activity
    ("Barcelona, avoid the tourist traps, we want what locals love.", ["Avoids crowds"], []),
    ("Backpacking Vietnam on a shoestring, every night out partying!", ["Backpacker comfort", "Nightlife"], []),
]


def card(message: str) -> list[str]:
    decision = TurnService.decide(message, [], TripProfile(), Taste(), "")
    profile = TripProfileService.merge(TripProfile(), decision.profile_updates, message=message)
    if decision.scale_signals:
        print(f"     model: {[(s.toward, s.quote) for s in decision.scale_signals]}")
    personality = PersonalityService.update(None, profile, Taste(), decision.scale_signals, message)
    return PersonalityService.card(personality).traits


def main() -> int:
    print(f"Model: {settings.LLM_MODEL}")
    failed = 0
    for message, must, must_not in CASES:
        start = time.time()
        traits = card(message)
        # "Slow pace (a little)" still counts as showing "Slow pace" for must_not.
        bad = [t for t in must if t not in traits] + (
            traits if must_not is None else [t for t in must_not if any(x.startswith(t) for x in traits)])
        failed += bool(bad)
        print(f"{'PASS' if not bad else 'FAIL'} ({time.time() - start:.0f}s) {message!r}\n     card: {traits}"
              + (f"\n     wrong: {bad}" if bad else ""))
    print(f"\n{failed} failure(s) across {len(CASES)} case(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
