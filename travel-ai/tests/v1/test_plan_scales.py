"""Ticket #83: each plan's own markers on the six scales and the $ badge, next to what the traveler asked."""
from app.schemas.activity import Activity
from app.schemas.dayplan import DayPlan
from app.schemas.itinerary import Itinerary
from app.schemas.personality import Note, Personality, Scale
from app.schemas.place import Pick
from app.schemas.trip_profile import BudgetLevel
from app.schemas.personality import NoteChange
from app.services.plan_scales import mark_closest, measure, read, step
from tests.v1.test_api import decision, mood, ready_trip, say


def stop(name, category="do", when="morning", minutes=90, **kw):
    return Activity(name=name, category=category, time_of_day=when, minutes=minutes, **kw)


def plan(*days, tier="mid"):
    stay = Pick(place_id="s", name="Stay", category="stay", tier=tier) if tier else None
    return Itinerary(destination="X", stay=stay,
                     days=[DayPlan(day_number=i, activities=list(d)) for i, d in enumerate(days, 1)])


SLOW = plan([stop("Garden", place_id="ov-1"), stop("Lunch", "eat", "afternoon", 60)],
            [stop("Beach", place_id="ov-2"), stop("Cafe", "eat", "afternoon", 60)], tier="budget")
PACKED = plan([stop("Castle", minutes=180, place_id="wv-1", photo_name="c.jpg"),
               stop("Palace", minutes=150, place_id="wv-2", photo_name="p.jpg"),
               stop("Boat tour", "do", "afternoon", 120, place_id="wv-3", photo_name="b.jpg"),
               stop("Tiny Bar", "eat", "evening", 120)], tier="luxury")


def test_steps_round_halves_up_and_stay_in_range():
    assert [step(v) for v in (-1, -0.75, -0.25, 0, 0.25, 0.7, 1, 3)] == [1, 2, 3, 3, 4, 4, 5, 5]


def test_rules_read_each_scale_and_the_same_plan_reads_the_same():
    slow, packed = read(SLOW, Personality()), read(PACKED, Personality())
    plan_of = lambda r: {n: s.plan for n, s in r.scales.items()}  # noqa: E731
    assert plan_of(slow) == {"pace": 1, "experiences": 1, "energy": 1, "crowds": 1, "food": 5, "comfort": 1}
    assert plan_of(packed) == {"pace": 5, "experiences": 5, "energy": 5, "crowds": 5, "food": 3, "comfort": 5}
    assert slow == read(SLOW, Personality())
    assert (slow.cost.plan, slow.why) == (1, "Budget stay, 0 paid activities")
    assert packed.cost.plan == 4 and packed.why == "Luxury stay, 2 paid activities"
    assert packed.summary.endswith("· $$$$")


def test_energy_and_cost_use_the_critic_ratings_before_the_words():
    bar = stop("Tiny Bar", "eat", "evening")
    assert measure(plan([bar]))[0]["energy"] == 1  # no rating: a bar in the evening is a night out
    bar.energy, bar.cost = 0, 3  # the critic says: a calm, pricey dinner
    values, level, why = measure(plan([bar], tier=None))
    assert values["energy"] == -1 and level == 5 and why == "Stay price unknown, 0 paid activities, 1 fine dining"
    assert values["comfort"] is None  # no stay tier: no plan marker


def test_empty_plan_has_no_markers():
    reading = read(plan([]), Personality())
    assert all(s.plan is None for s in reading.scales.values()) and reading.cost.plan is None and not reading.summary


def test_ghosts_come_only_from_what_was_stated():
    p = Personality(scales={"pace": Scale(value=-0.7, confidence=0.75, source="note"),
                            "crowds": Scale(value=0.7, confidence=0.4, source="note"),  # inferred: a guess
                            "money": Scale(value=-0.8, confidence=0.7, source="chat")})
    reading = read(None, p)
    assert reading.scales["pace"].wish == 2 and reading.scales["crowds"].wish is None
    assert reading.cost.wish == 1 and all(s.plan is None for s in reading.scales.values())


def test_an_old_trip_with_cheap_experiences_still_loads_and_feeds_the_money_ghost():
    old = Personality.model_validate({
        "scales": {"money": {"value": -0.7, "confidence": 0.75, "source": "note"}},
        "notes": [{"id": "n1", "text": "cheap activities", "toward": "cheap_experiences"}]})
    reading = read(SLOW, old)
    assert reading.scales["experiences"].wish is None and reading.cost.wish == 2


def test_closest_needs_three_stated_scales_and_a_clear_winner():
    stated = Personality(scales={n: Scale(value=-0.9, confidence=0.75, source="note")
                                 for n in ("pace", "energy", "crowds")})
    readings = [read(PACKED, stated), read(SLOW, stated)]
    mark_closest(readings)
    assert [r.closest for r in readings] == [False, True]
    tie = [read(SLOW, stated), read(SLOW, stated)]
    mark_closest(tie)
    assert not any(r.closest for r in tie)
    two = Personality(scales={n: Scale(value=-0.9, confidence=0.75, source="note") for n in ("pace", "energy")})
    few = [read(PACKED, two), read(SLOW, two)]
    mark_closest(few)
    assert not any(r.closest for r in few)


def test_the_critic_rates_each_stop_and_drafts_show_their_scales(client, auth, fake_llm):
    fake_llm.ratings = {}  # every stop calm and cheap
    trip = ready_trip(client, auth, fake_llm)
    drafts = say(client, auth, trip["id"], "4 days")["trip"]["drafts"]
    assert drafts and all(d["scales"]["summary"] for d in drafts)
    stops = [a for d in drafts[0]["itinerary"]["days"] for a in d["activities"]]
    assert all((a["energy"], a["cost"]) == (0, 1) for a in stops)


def test_picking_and_editing_a_plan_moves_its_markers_never_the_ghosts(client, auth, fake_llm):
    trip = ready_trip(client, auth, fake_llm, budget_level=BudgetLevel.BUDGET)
    fake_llm.decisions.append(decision(notes=[NoteChange(op="add", text="slow days", quote="slow", toward="slow")]))
    out = say(client, auth, trip["id"], "slow, 4 days")["trip"]
    before = out["scales"]
    assert before["scales"]["pace"]["wish"] == 2 and before["cost"]["wish"] == 1 and before["scales"]["pace"]["plan"] is None

    chosen = client.post(f"/trips/{trip['id']}/drafts/{out['drafts'][0]['id']}/choose", headers=auth).json()
    picked = chosen["scales"]
    assert picked["scales"]["pace"]["plan"] and chosen["personality"]["scales"] == out["personality"]["scales"]
    assert {n: s["wish"] for n, s in picked["scales"].items()} == {n: s["wish"] for n, s in before["scales"].items()}

    edited = client.post(f"/trips/{trip['id']}/itinerary/days/1/activities/1", json={"action": "remove"},
                         headers=auth).json()["trip"]
    assert edited["scales"]["scales"]["pace"]["plan"] <= picked["scales"]["pace"]["plan"]
    assert edited["scales"] != picked  # the markers follow the plan
    assert edited["personality"]["scales"] == out["personality"]["scales"]
