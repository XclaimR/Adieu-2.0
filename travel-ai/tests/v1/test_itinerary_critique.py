import json

import httpx
import pytest
from openai import APITimeoutError

from app.repositories.trip_repository import TripRepository
from app.schemas.activity import Activity
from app.schemas.dayplan import DayPlan
from app.schemas.draft import Draft
from app.schemas.itinerary import Itinerary
from app.schemas.personality import Note, Personality
from app.schemas.place import Pick, PickCategory, PlaceCandidate
from app.schemas.trip_profile import BudgetLevel, TripProfile
from app.schemas.turn import TurnAction, TurnDecision
from app.services import llm_client
from app.services.draft_service import DraftService, PlacePool, PlanEdit
from app.services.itinerary_critique_service import ItineraryCritiqueService, travel_minutes
from tests.v1.test_api import new_trip, ready_trip, say


def place(id, name, category=PickCategory.DO, **kw):
    return PlaceCandidate(place_id=id, name=name, category=category, **kw)


PLACES = [
    place("hotel", "City Hotel", PickCategory.STAY, tier="mid"),
    place("panini", "Pane e Salame", PickCategory.EAT, type_label="Sandwich Shop"),
    place("club", "Night Owl", type_label="Night Club"),
    place("museum", "Museum", type_label="Museum"),
    place("park", "Park"),
    place("garden", "Garden"),
]
PROFILE = TripProfile(destination="Rome", duration_days=2)
NIGHT_OUT = Personality(notes=[Note(id="n1", text="one big night out", once=True)])
MISFIT = {"problem": "i2 is a sandwich shop, not a big night out", "where": ["[i2]"], "fix": "a club", "passed": False}


def stop(id, place_id, when="morning", **kw):
    p = next(p for p in PLACES if p.place_id == place_id)
    return Activity(id=id, name=p.name, category=p.category.value, time_of_day=when, place_id=place_id, **kw)


def draft(**kw):
    plan = Itinerary(destination="Rome", stay=Pick(**PLACES[0].model_dump()), days=[
        DayPlan(day_number=1, activities=[stop("i1", "museum"),
                                          stop("i2", "panini", "evening", for_note="one big night out")]),
        DayPlan(day_number=2, activities=[stop("i3", "park"), stop("i4", "garden", "afternoon")]),
    ])
    return Draft(id="a", flavor="easy", title="Rome", summary="Nice", itinerary=kw.get("plan", plan))


@pytest.fixture
def drafter(monkeypatch):
    service = DraftService(places=None)
    monkeypatch.setattr(service, "pool", lambda *args: PlacePool(PLACES))
    return service


def review(drafter, *drafts, profile=PROFILE, personality=NIGHT_OUT):
    return drafter.review(list(drafts) or [draft()], profile, None, set(), personality)


def calls(fake_llm, name):
    return [p for n, p in fake_llm.prompts if n == name]


def timeout():
    return APITimeoutError(request=httpx.Request("POST", "http://llm"))


def test_good_plans_pass_untouched_with_one_call_for_all_of_them(drafter, fake_llm):
    made = [draft(), draft(), draft()]
    reviewed, checked = review(drafter, *made)
    assert checked and reviewed == made
    assert [n for n, _ in fake_llm.prompts] == ["PlanReviews"]


def test_a_misfit_is_repaired_once_before_it_is_shown(drafter, fake_llm):
    fake_llm.fails = {"plan_0": {"purpose": MISFIT}}
    fake_llm.fixes = {"plan_0": [{"op": "replace", "item_id": "i2", "place": "c2", "note": "Dance late"}]}
    [fixed], checked = review(drafter)
    first, second = fixed.itinerary.days
    assert checked and fixed.warnings == []
    assert (first.activities[1].id, first.activities[1].name) == ("i2", "Night Owl")
    assert second == draft().itinerary.days[1]
    assert [n for n, _ in fake_llm.prompts] == ["PlanReviews", "PlanFixes"]


def test_a_repair_cannot_touch_what_was_not_flagged(drafter, fake_llm):
    fake_llm.fails = {"plan_0": {"purpose": MISFIT}}
    fake_llm.fixes = {"plan_0": [{"op": "replace", "item_id": "i3", "place": "c2"}]}
    [kept], _ = review(drafter)
    assert kept.itinerary == draft().itinerary and kept.warnings


def test_a_repair_cannot_use_a_place_the_plan_already_has(drafter, fake_llm):
    fake_llm.fails = {"plan_0": {"purpose": MISFIT}}
    fake_llm.fixes = {"plan_0": [{"op": "replace", "item_id": "i2", "place": "c3"}]}  # the museum of that morning
    [kept], _ = review(drafter)
    assert kept.itinerary == draft().itinerary and kept.warnings


def test_a_repair_that_makes_the_plan_worse_is_thrown_away(drafter, fake_llm):
    fake_llm.fails = {"plan_0": {"purpose": MISFIT}}
    fake_llm.fixes = {"plan_0": [{"op": "remove", "item_id": "i2"}]}  # leaves day 1 with one stop
    [kept], _ = review(drafter)
    assert kept.itinerary == draft().itinerary and kept.warnings


def test_what_could_not_be_fixed_is_shown_in_words_not_ids(drafter, fake_llm):
    fake_llm.fails = {"plan_0": {"purpose": MISFIT}}
    [kept], checked = review(drafter)
    assert checked and kept.warnings == ["Pane e Salame is a sandwich shop, not a big night out"]


def test_a_failed_repair_still_shows_the_plan_with_a_warning(drafter, fake_llm):
    fake_llm.fails = {"plan_0": {"purpose": MISFIT}}
    fake_llm.errors = {"PlanFixes": timeout()}
    [kept], checked = review(drafter)
    assert checked and kept.itinerary == draft().itinerary and kept.warnings


@pytest.mark.parametrize("error", [timeout(), RuntimeError("bad output")])
def test_a_broken_critic_returns_every_plan_unchanged(drafter, fake_llm, error):
    fake_llm.errors = {"PlanReviews": error}
    reviewed, checked = review(drafter, draft(), draft())
    assert not checked and reviewed == [draft(), draft()]
    assert not calls(fake_llm, "PlanFixes")


def test_a_failing_place_lookup_during_review_loses_no_plan(drafter, fake_llm, monkeypatch):
    monkeypatch.setattr(drafter, "pool", lambda *args: 1 / 0)
    assert review(drafter) == ([draft()], False)


def test_an_error_that_points_at_nothing_real_is_ignored(drafter, fake_llm):
    fake_llm.fails = {"plan_0": {"logic": {**MISFIT, "where": ["i999"]}, "balance": {**MISFIT, "where": []}}}
    assert review(drafter) == ([draft()], True)
    assert not calls(fake_llm, "PlanFixes")


def test_a_missing_must_can_be_added_to_any_day(drafter, fake_llm):
    fake_llm.fails = {"plan_0": {"note_0": {"problem": "No night club", "where": [], "passed": False}}}
    fake_llm.fixes = {"plan_0": [{"op": "add", "day_id": "d2", "place": "c2", "time_of_day": "evening"}]}
    [fixed], _ = review(drafter)
    assert fixed.itinerary.days[1].activities[-1].name == "Night Owl" and fixed.warnings == []


def test_the_budget_is_only_judged_when_the_traveler_stated_one(drafter, fake_llm):
    review(drafter)
    assert "- budget:" not in calls(fake_llm, "PlanReviews")[0]
    review(drafter, profile=PROFILE.model_copy(update={"budget_level": BudgetLevel.BUDGET}))
    assert "- budget:" in calls(fake_llm, "PlanReviews")[1]


def test_each_stated_note_gets_its_own_verdict_and_a_guess_gets_none(drafter, fake_llm):
    notes = [Note(id="n1", kind="avoid", text="beach"), Note(id="n2", text="jazz"),
             Note(id="n3", text="maybe bars", source="inferred")]
    review(drafter, personality=Personality(notes=notes))
    [prompt] = calls(fake_llm, "PlanReviews")
    assert '- note_0: they said "avoid: beach"' in prompt and '- note_1: they said "want: jazz"' in prompt
    assert "note_2" not in prompt


def test_missing_days_are_counted_by_code_and_filled(drafter, fake_llm):
    fake_llm.fixes = {"plan_0": [{"op": "add", "day_id": "d3", "place": "c2", "time_of_day": "evening"},
                                 {"op": "add", "day_id": "d3", "place": "c9"}]}
    [fixed], _ = review(drafter, profile=PROFILE.model_copy(update={"duration_days": 3}))
    assert [a.name for a in fixed.itinerary.days[2].activities] == ["Night Owl"]
    assert fixed.warnings == ["Day 3 has only one stop"]
    assert "nothing planned" not in calls(fake_llm, "PlanReviews")[0]  # it would parrot the result


def test_days_nobody_could_fill_are_not_shown_empty(drafter, fake_llm):
    [short], _ = review(drafter, profile=PROFILE.model_copy(update={"duration_days": 4}))
    assert len(short.itinerary.days) == 2 and short.warnings == ["This plan has 2 of the 4 days"]


def test_extra_days_are_dropped_without_a_repair_call(drafter, fake_llm):
    [cut], _ = review(drafter, profile=PROFILE.model_copy(update={"duration_days": 1}))
    assert len(cut.itinerary.days) == 1 and not calls(fake_llm, "PlanFixes")


def located(**at):
    plan = draft().itinerary
    for item in (a for d in plan.days for a in d.activities):
        item.lat, item.lng = at.get(item.id, (41.9, 12.5))
    return plan


def test_neighbouring_stops_with_normal_visits_are_doable():
    plan = located(i2=(41.905, 12.505))
    assert ItineraryCritiqueService.counted(plan, [], visits={"museum": 180, "panini": 90}) == []


def test_travel_grows_with_distance():
    minutes = [travel_minutes((41.9, 12.5), (41.9 + step, 12.5)) for step in (0, 0.005, 0.05, 0.2, 1, 4)]
    assert minutes == sorted(minutes) and minutes[0] == 0
    assert minutes[1] < 15 and 20 < minutes[2] < 60 and minutes[-1] > 300


def test_too_much_travel_in_a_day_is_an_error():
    [issue] = ItineraryCritiqueService.counted(located(i2=(45.46, 9.19)), [])  # Rome and Milan
    assert "travel" in issue.problem and issue.where == ["i1", "i2"]


def test_a_visit_too_long_for_the_next_stop_is_an_error():
    [issue] = ItineraryCritiqueService.counted(located(), [], visits={"park": 540})
    assert "Garden" in issue.problem and "afternoon" in issue.problem and issue.where == ["i3", "i4"]


def test_a_visit_that_runs_into_the_night_is_an_error():
    [issue] = ItineraryCritiqueService.counted(located(), [], visits={"panini": 480})
    assert "past 1 am" in issue.problem


def test_visit_and_travel_time_count_together():
    plan = located(i4=(42.5, 12.5))
    assert ItineraryCritiqueService.counted(plan, [], visits={"park": 180}) == []  # 2 h away, still by 17:00
    [issue] = ItineraryCritiqueService.counted(plan, [], visits={"park": 420})
    assert "Garden" in issue.problem


def test_a_stay_too_far_from_the_days_stops_is_an_error():
    plan = located()
    plan.stay.lat, plan.stay.lng = 45.46, 9.19
    issues = ItineraryCritiqueService.counted(plan, [])
    assert len(issues) == 2 and all("s1" in issue.where for issue in issues)


def test_the_models_visit_times_reach_the_check_and_a_tight_day_is_repaired(drafter, fake_llm):
    fake_llm.minutes = {"i3": 540}
    fake_llm.fixes = {"plan_0": [{"op": "replace", "item_id": "i3", "place": "c2", "time_of_day": "morning"}]}
    [fixed], checked = review(drafter)
    assert "Garden" in calls(fake_llm, "PlanFixes")[0].split("What the reviewer found:")[1]
    assert checked and fixed.itinerary.days[1].activities[0].name == "Night Owl" and fixed.warnings == []


def test_a_tight_day_nobody_could_fix_is_shown(drafter, fake_llm):
    fake_llm.minutes = {"i3": 540}
    [kept], _ = review(drafter)
    assert len(kept.warnings) == 1 and "Garden" in kept.warnings[0]


def test_stops_remember_where_they_are(client, auth, fake_llm):
    trip, days = chosen(client, auth, fake_llm)
    stops = [a for d in days for a in d["activities"]]
    assert all((a["lat"], a["lng"]) == (1.5, 1.5) for a in stops)


def test_a_replaced_stop_takes_the_new_places_position():
    plan = located()
    club = place("club", "Night Owl", lat=45.0, lng=9.0)
    DraftService._apply_edits(plan, [PlanEdit(op="replace", item_id="i2", place="c0")], {"c0": club}, {"i2"}, set(), set())
    assert (plan.days[0].activities[1].lat, plan.days[0].activities[1].lng) == (45.0, 9.0)


def test_the_stream_shows_plans_only_after_the_review(client, auth, fake_llm):
    trip = ready_trip(client, auth, fake_llm)
    with client.stream("POST", f"/trips/{trip['id']}/messages/stream", json={"message": "2 days"}, headers=auth) as r:
        events = [json.loads(line) for line in r.iter_lines() if line]
    checking = next(i for i, e in enumerate(events) if e.get("text") == "Checking your plans…")
    shown = [i for i, e in enumerate(events) if e["type"] == "draft"]
    assert len(shown) == 3 and checking < shown[0]
    assert len(calls(fake_llm, "PlanReviews")) == 1


def test_a_critic_timeout_still_shows_three_plans_and_says_so(client, auth, fake_llm):
    trip = ready_trip(client, auth, fake_llm)
    fake_llm.errors = {"PlanReviews": timeout()}
    turn = say(client, auth, trip["id"], "2 days")
    assert len(turn["drafts"]) == 3 and "couldn't double-check" in turn["reply"]
    assert "couldn't double-check" in turn["trip"]["messages"][-1]["content"]


def chosen(client, auth, fake_llm):
    trip = ready_trip(client, auth, fake_llm)
    made = say(client, auth, trip["id"], "2 days")["drafts"][0]
    client.post(f"/trips/{trip['id']}/drafts/{made['id']}/choose", headers=auth)
    fake_llm.prompts.clear()
    fake_llm.decisions.append(TurnDecision(action=TurnAction.EDIT_ITINERARY, reply="Done.", edit_request="change it"))
    return trip, made["itinerary"]["days"]


def test_an_edit_is_reviewed_and_repaired_only_where_it_changed(client, auth, fake_llm):
    trip, days = chosen(client, auth, fake_llm)  # c0 and c1 are stays, so activities start at c2
    changed, other = days[0]["activities"][0]["id"], days[1]["activities"][0]["id"]
    fake_llm.edits = [{"op": "replace", "item_id": changed, "place": "c2"}]
    fake_llm.fails = {"plan_0": {"purpose": {**MISFIT, "where": [changed]}}}
    fake_llm.fixes = {"plan_0": [{"op": "replace", "item_id": changed, "place": "c3"},
                                 {"op": "replace", "item_id": other, "place": "c4"}]}
    after = say(client, auth, trip["id"], "change it")["trip"]["itinerary"]["days"]
    assert [n for n, _ in fake_llm.prompts if n.startswith("Plan")] == ["PlanEdits", "PlanReviews", "PlanFixes"]
    assert after[1] == days[1]
    assert after[0]["activities"][0]["id"] == changed and after[0]["activities"][0]["name"] != days[0]["activities"][0]["name"]


def test_the_repair_of_an_edit_never_removes_what_was_asked_for(client, auth, fake_llm):
    trip, days = chosen(client, auth, fake_llm)
    changed = days[0]["activities"][0]["id"]
    fake_llm.edits = [{"op": "replace", "item_id": changed, "place": "c2"}]
    fake_llm.fails = {"plan_0": {"purpose": {**MISFIT, "problem": "That doesn't fit", "where": [changed]}}}
    fake_llm.fixes = {"plan_0": [{"op": "remove", "item_id": changed}]}
    turn = say(client, auth, trip["id"], "change it")
    assert len(turn["trip"]["itinerary"]["days"][0]["activities"]) == len(days[0]["activities"])
    assert "Worth a look: That doesn't fit" in turn["reply"]
    assert "Worth a look: That doesn't fit" in turn["trip"]["messages"][-1]["content"]


def test_an_edit_is_kept_when_the_critic_times_out(client, auth, fake_llm):
    trip, days = chosen(client, auth, fake_llm)
    fake_llm.edits = [{"op": "replace", "item_id": days[0]["activities"][0]["id"], "place": "c2"}]
    fake_llm.errors = {"PlanReviews": timeout()}
    turn = say(client, auth, trip["id"], "change it")
    assert turn["trip"]["itinerary"]["days"] != days and "couldn't double-check" in turn["reply"]


def test_an_edit_that_only_removes_costs_no_review(client, auth, fake_llm):
    trip, days = chosen(client, auth, fake_llm)
    fake_llm.edits = [{"op": "remove", "item_id": days[1]["activities"][1]["id"]}]
    say(client, auth, trip["id"], "change it")
    assert not calls(fake_llm, "PlanReviews")


def test_a_timeout_is_passed_to_the_model_call(monkeypatch):
    seen = {}

    def create(**kwargs):
        seen.update(kwargs)
        raise timeout()

    monkeypatch.setattr(llm_client, "_create", create)
    with pytest.raises(APITimeoutError):
        llm_client.LLMClient.generate_structured("p", TripProfile, timeout=45)
    assert seen["timeout"] == 45


def test_the_review_makes_no_places_calls_of_its_own(client, auth, fake_llm, fake_places, monkeypatch):
    trip = ready_trip(client, auth, fake_llm)
    searched = []
    real = DraftService.review

    def counting(self, *args):
        before = len(fake_places.loads) + len(fake_places.resolved)
        result = real(self, *args)
        searched.append(len(fake_places.loads) + len(fake_places.resolved) - before)
        return result

    monkeypatch.setattr(DraftService, "review", counting)
    say(client, auth, trip["id"], "2 days")
    assert searched == [0]


def test_drafting_that_breaks_midway_still_shows_what_was_written(client, auth, fake_llm, monkeypatch):
    trip = ready_trip(client, auth, fake_llm)
    real = DraftService.generate

    def breaks(self, *args):
        yield next(real(self, *args))
        raise RuntimeError("gone")

    monkeypatch.setattr(DraftService, "generate", breaks)
    assert len(say(client, auth, trip["id"], "2 days")["drafts"]) == 1


def test_a_swap_takes_the_new_places_position(client, auth, fake_llm, monkeypatch):
    trip, days = chosen(client, auth, fake_llm)
    pick = Pick(**place("new", "Far Away", lat=9.0, lng=8.0).model_dump())
    monkeypatch.setattr(TripRepository, "find_pick", lambda *args: pick)
    url = f"/trips/{trip['id']}/itinerary/days/1/activities/0/replace"
    assert client.post(url, json={"place_id": "new"}, headers=auth).status_code == 200
    after = client.get(f"/trips/{trip['id']}", headers=auth).json()["itinerary"]["days"][0]["activities"][0]
    assert (after["lat"], after["lng"]) == (9.0, 8.0)


def test_the_critic_sees_what_a_place_is_next_to_what_it_is_planned_for(drafter, fake_llm):
    # Traveler walk 2026-11-22: a cafe was planned as a swimming beach.
    cafe = draft()
    cafe.itinerary.days[1].activities[0].description = "Swim at the beach"
    review(drafter, cafe)
    [prompt] = calls(fake_llm, "PlanReviews")
    assert "- [i2] Sandwich Shop · meant for: one big night out" in prompt
    assert "- [i3] planned: Swim at the beach" in prompt
