"""Scripted conversations against the live model. Rerun whenever you change LLM_MODEL or the turn prompt.

    docker compose exec api python -m tests.evals.run_turn_evals
"""
import time

from app.core.config import settings
from app.schemas.taste import Taste
from app.schemas.trip_profile import TripProfile
from app.schemas.turn import OptionsKind, TurnAction
from app.services.trip_profile_service import TripProfileService
from app.services.turn_service import TurnService

ITINERARY_CONTEXT = "Their chosen itinerary: Day 1: Castle, Old Tavern; Day 2: Museum, Tiny Bar"

# (messages, has_itinerary, check(profile, plan))
CASES = [
    (["I want to go to Japan"], False,
     lambda p, t: p.destination == "Japan" and t.action == TurnAction.ASK and t.asked_field == "mood"),
    (["Tokyo"], False,
     lambda p, t: p.destination and "tokyo" in p.destination.lower()
     and p.party is None and p.budget_level is None and p.party_size is None),
    (["Tokyo, something relaxed, me and my wife, 5 days"], False,
     lambda p, t: p.party == "couple" and p.duration_days == 5 and any("relax" in m for m in p.mood)
     and t.action == TurnAction.OFFER_DRAFTS),
    (["Lisbon", "hmm not sure, you decide"], False,
     lambda p, t: t.action == TurnAction.OFFER_DRAFTS),
    (["Lisbon", "show me cafés near Alfama"], False,
     lambda p, t: t.action == TurnAction.SHOW_OPTIONS and t.options_request.kind == OptionsKind.EAT),
    (["surprise me, somewhere warm"], False,
     lambda p, t: t.action == TurnAction.SHOW_OPTIONS and t.options_request.kind == OptionsKind.DESTINATIONS),
    (["Rome please", "my mom uses a wheelchair"], False,
     lambda p, t: any("wheelchair" in x.value.lower() or "mobility" in x.key for x in p.preferences)),
    (["Paris", "actually make it Barcelona"], False,
     lambda p, t: p.destination and "barcelona" in p.destination.lower()),
    (["make day 2 more relaxed"], True,
     lambda p, t: t.action == TurnAction.EDIT_ITINERARY),
]


def run_case(messages, has_itinerary):
    profile = TripProfile(destination="Lisbon", mood=["culture"], party="couple", duration_days=2) \
        if has_itinerary else TripProfile()
    history, asked, streak, plan, decision = [], [], 0, None, None
    for message in messages:
        context = ITINERARY_CONTEXT if has_itinerary else ""
        decision = TurnService.decide(message, history, profile, Taste(), context)
        surprise = TurnService.wants_surprise(message)
        if surprise:
            decision.profile_updates.destination = None
        before = profile
        profile = TripProfileService.merge(profile, decision.profile_updates, message=message)
        plan = TurnService.plan(decision, profile, message=message, asked=asked, questions_streak=streak,
                                has_drafts=False, has_itinerary=has_itinerary, surprise=surprise)
        if plan.asked_field:
            asked.append(plan.asked_field)
        streak = streak + 1 if plan.action == TurnAction.ASK and profile == before else 0
        reply = TurnService.strip_questions(decision.reply, plan.next_question)
        history += [{"role": "user", "content": message},
                    {"role": "assistant", "content": f"{reply}\n\n{plan.next_question or ''}"}]
    return profile, plan, decision


def main():
    print(f"model={settings.LLM_MODEL} base={settings.LLM_BASE_URL}\n")
    passed = 0
    for messages, has_itinerary, check in CASES:
        start = time.time()
        try:
            profile, plan, decision = run_case(messages, has_itinerary)
            ok = bool(check(profile, plan))
            detail = (f"action={plan.action.value} q={plan.next_question!r} chips={plan.answer_options}\n"
                      f"     model: action={decision.action.value} stuck={decision.user_stuck} "
                      f"options={decision.options_request} edit={decision.edit_request!r}\n"
                      f"     profile={profile.model_dump_json(exclude_none=True, exclude_defaults=True)}")
        except Exception as e:
            ok, detail = False, f"ERROR {type(e).__name__}: {e}"
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'} ({time.time() - start:.0f}s) {messages}\n     {detail}\n")
    print(f"{passed}/{len(CASES)} passed")


if __name__ == "__main__":
    main()
