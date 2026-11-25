"""Ticket #79: chat edits swap, add or remove a city, or add a day, and say only what really changed."""
import re

import pytest

from app.schemas.activity import Activity
from app.schemas.itinerary import City, Itinerary, Leg
from app.schemas.personality import Note, Personality
from app.schemas.place import PickCategory, PlaceCandidate
from app.schemas.trip_profile import TripProfile
from app.schemas.turn import RouteChange
from app.services import route_service
from app.services.draft_service import DraftService, PlanEdit
from app.services.llm_client import LLMClient
from app.services.route_service import insert_at

TOWNS = {"Tokyo": (35.69, 139.69), "Kyoto": (35.01, 135.77), "Osaka": (34.69, 135.50),
         "Hiroshima": (34.39, 132.46), "Sapporo": (43.06, 141.35), "Nara": (34.68, 135.80)}


def _places():
    out = [PlaceCandidate(place_id=f"{t}-{c.value}-{i}", name=f"{t} {c.value} {i}", category=c, lat=lat, lng=lng)
           for t, (lat, lng) in TOWNS.items() if t != "Nara"
           for c in (PickCategory.DO, PickCategory.EAT, PickCategory.STAY) for i in range(4)]
    # As in the open data: a guide listing in Osaka, with no coordinates.
    return out + [PlaceCandidate(place_id="usj", name="Universal Studios Japan", category=PickCategory.DO,
                                 address="Osaka")]


class _Places:
    def __init__(self, found):
        self.found = found

    def area_for(self, destination, viewport):
        return {"kind": "country", "code": "JP", "place_id": "jp"}

    def library(self, area):
        return {"hubs": [{"name": t, "point": list(p), "district": False} for t, p in TOWNS.items()], "places": []}

    def candidates(self, destination, category, profile=None, viewport=None, query=None, personality=None, limit=30):
        town = re.search(r"near (\w+)", query or "")
        named = (query or "").split(" near ")[0]
        hits = [p for p in self.found if p.category == category and (not town or p.name.startswith(town[1])
                                                                       or p.name == named)]
        return sorted(hits, key=lambda p: p.name != named)


@pytest.fixture(autouse=True)
def no_transitous(monkeypatch):
    monkeypatch.setattr(route_service, "transit", lambda a, b: None)


@pytest.fixture
def llm(fake_llm, monkeypatch):
    """The edit model fills each day it is asked about with two places from that day's city, the named one first."""
    fake_llm.fill = True

    def answer(prompt, schema, **kw):
        if schema.__name__ != "PlanEdits" or not fake_llm.fill:
            return fake_llm(prompt, schema, **kw)
        fake_llm.prompts.append((schema.__name__, prompt))
        listed = re.findall(r"^\[(c\d+)\] ([^·]+?) · (\w+)", prompt, re.M)
        edits = []
        for day, city, around in re.findall(r"at \[(d\d+)\]: plan this day in (\w+)(?: around (.+))?", prompt):
            used = {e["place"] for e in edits}
            mine = [key for key, name, cat in listed if cat != "stay" and key not in used
                    and (name.startswith(city) or name == around)]
            mine.sort(key=lambda k: dict((k2, n) for k2, n, _ in listed)[k] != around)
            edits += [{"op": "add", "day_id": day, "place": k, "time_of_day": "afternoon", "note": "go"}
                      for k in mine[:2]]
        return schema.model_validate({"edits": edits})
    monkeypatch.setattr(LLMClient, "generate_structured", staticmethod(answer))
    return fake_llm


def _trip():
    """The owner's shape: Kyoto → Hiroshima → Tokyo → Sapporo, every day with stops."""
    lengths = {"Kyoto": 3, "Hiroshima": 3, "Tokyo": 3, "Sapporo": 2}
    route = [City(name=t, lat=TOWNS[t][0], lng=TOWNS[t][1], leg=None if t == "Kyoto" else Leg(mode="train", minutes=99))
             for t in lengths]
    days = [{"day_number": 0, "city": t, "activities": [Activity(name=f"{t} old {i}", category="do",
                                                                  lat=TOWNS[t][0], lng=TOWNS[t][1])]}
            for t, k in lengths.items() for i in range(k)]
    for n, d in enumerate(days, 1):
        d["day_number"] = n
    return Itinerary(destination="Japan", route=route, days=days)


def _edit(trip, *changes):
    service = DraftService(_Places(_places()))
    return service.edit_route(trip, list(changes), "edit", TripProfile(destination="Japan", duration_days=11),
                              None, set(), Personality())


def _stops(plan, city):
    return [a.name for d in plan.days if d.city == city for a in d.activities]


def test_replace_hiroshima_with_osaka_changes_the_route_and_only_its_days(llm):
    trip = _trip()
    done = _edit(trip, RouteChange(op="swap", city="Osaka", leaving="Hiroshima"))
    plan = done.plan
    assert [c.name for c in plan.route] == ["Kyoto", "Osaka", "Tokyo", "Sapporo"]
    osaka = plan.route[1]
    assert osaka.days == 3 and osaka.stay and osaka.stay.name.startswith("Osaka") and osaka.leg
    assert plan.route[2].leg.mode == "high-speed train"  # Osaka → Tokyo is a new leg
    assert all(d.activities and all(a.name.startswith("Osaka") for a in d.activities)
               for d in plan.days if d.city == "Osaka")
    assert len(plan.days) == 11 and "Hiroshima" not in {d.city for d in plan.days}
    for city in ("Kyoto", "Tokyo", "Sapporo"):
        assert _stops(plan, city) == _stops(trip, city)
    assert done.said[0] == "Your route is now Kyoto → Osaka → Tokyo → Sapporo."
    assert "Osaka replaces Hiroshima." in done.said


def test_add_osaka_keep_hiroshima_takes_days_from_others_or_makes_the_trip_longer(llm):
    added = _edit(_trip(), RouteChange(op="add", city="Osaka"))
    same = added.plan
    # Hiroshima → Osaka → Tokyo is on the way back east: the least added distance
    assert [c.name for c in same.route] == ["Kyoto", "Hiroshima", "Osaka", "Tokyo", "Sapporo"]
    assert len(same.days) == 11 and same.route[2].days == 2
    assert "Kyoto gave a day to Osaka." in added.said
    longer = _edit(_trip(), RouteChange(op="add", city="Osaka", longer=True))
    assert len(longer.plan.days) == 13 and "now 13 days" in longer.said[0]
    assert _stops(longer.plan, "Hiroshima") == _stops(_trip(), "Hiroshima")


def test_add_a_day_for_universal_studios_puts_it_on_a_new_osaka_day(llm):
    trip = _edit(_trip(), RouteChange(op="swap", city="Osaka", leaving="Hiroshima")).plan
    done = _edit(trip, RouteChange(op="add_day", city="Osaka", place="Universal Studios Japan"))
    plan = done.plan
    assert len(plan.days) == 12 and plan.route[1].days == 4
    new = [d for d in plan.days if d.city == "Osaka"][-1]
    assert new.day_number == 7 and "Universal Studios Japan" in [a.name for a in new.activities]
    assert _stops(plan, "Tokyo") == _stops(trip, "Tokyo")
    assert "I added 1 day in Osaka for Universal Studios Japan." in done.said


def test_a_day_for_a_place_in_a_city_off_the_route_adds_that_city(llm):
    done = _edit(_trip(), RouteChange(op="add_day", city="Osaka", place="Universal Studios Japan"))
    assert "Osaka" in [c.name for c in done.plan.route] and len(done.plan.days) == 12
    assert "Universal Studios Japan" in _stops(done.plan, "Osaka")


def test_removing_a_city_gives_its_days_to_the_one_before(llm):
    done = _edit(_trip(), RouteChange(op="remove", leaving="Hiroshima"))
    assert [c.name for c in done.plan.route] == ["Kyoto", "Tokyo", "Sapporo"] and done.plan.route[0].days == 6
    assert all(d.activities for d in done.plan.days)


def test_a_day_that_cant_be_filled_keeps_the_old_plan_and_the_reply_says_so(llm):
    llm.fill = False
    llm.edits = []
    trip = _trip()
    done = _edit(trip, RouteChange(op="swap", city="Osaka", leaving="Hiroshima"))
    assert done.plan == trip
    assert "so I left your route as it is" in done.said[0] and not any("now" in s for s in done.said)


def test_an_unknown_city_is_refused_in_words(llm):
    trip = _trip()
    done = _edit(trip, RouteChange(op="swap", city="Atlantis", leaving="Hiroshima"))
    assert done.plan == trip and done.said == ["I couldn't add Atlantis: I don't have places there."]


def test_a_place_that_isnt_found_is_admitted(llm):
    done = _edit(_trip(), RouteChange(op="add_day", city="Tokyo", place="Ghibli Museum"))
    assert any("couldn't find Ghibli Museum" in s for s in done.said)


def test_a_new_city_goes_where_it_adds_the_least_distance_never_before_the_start():
    route = [City(name=t, lat=TOWNS[t][0], lng=TOWNS[t][1]) for t in ("Tokyo", "Kyoto", "Hiroshima")]
    nara = {"name": "Nara", "lat": 34.68, "lng": 135.80}
    assert insert_at(route, nara) == 2  # between Kyoto and Hiroshima
    assert insert_at(route[:1], nara) == 1


def test_a_plain_edit_never_empties_a_day(llm):
    llm.fill = False
    trip = _trip()
    gone = trip.days[3].activities[0].id
    llm.edits = [PlanEdit(op="remove", item_id=gone).model_dump()]
    from app.schemas.personality import NoteChange
    change = NoteChange(op="do", target=gone, text="drop it", quote="drop it", then="change")
    plan, _, _ = DraftService(_Places(_places())).edit(trip, [change], "drop it", TripProfile(
        destination="Japan", duration_days=11), None, set(), Personality())
    assert [a.id for a in plan.days[3].activities] == [gone]


def test_notes_follow_their_day_when_days_are_renumbered():
    from app.workflows.travel_planner_workflow import TravelPlannerWorkflow
    old = _trip()
    saved = {}
    flow = TravelPlannerWorkflow.__new__(TravelPlannerWorkflow)
    flow.trips = type("T", (), {"save_personality": lambda self, trip, p: saved.setdefault("p", p)})()
    after = old.model_copy(deep=True)
    after.days = after.days[:3] + after.days[6:]  # Hiroshima's days 4-6 are gone
    for n, d in enumerate(after.days, 1):
        d.day_number = n
    notes = [Note(id="n1", text="sushi", target="d7"), Note(id="n2", text="anime", target="d4")]
    flow._keep_notes_attached(None, Personality(notes=notes), old, after, {"d1": "d1", "d7": "d4"})
    assert [n.target for n in saved["p"].notes] == ["d4", "trip"]


def test_the_turn_s_reply_is_what_the_route_edit_did_and_the_trip_length_follows(client, auth, fake_llm, db_session,
                                                                                    monkeypatch):
    from app.database.models import TripModel
    from app.schemas.turn import TurnAction, TurnDecision
    from app.services.draft_service import RouteEdit
    from test_api import ready_trip, say
    trip = ready_trip(client, auth, fake_llm)
    row = db_session.get(TripModel, trip["id"])
    row.itinerary = _trip().model_dump(mode="json")
    db_session.commit()
    longer = _trip()
    longer.days.append(longer.days[-1].model_copy(update={"day_number": 12}))
    longer.sync_route()
    said = ["Your route is now Kyoto → Hiroshima → Tokyo → Sapporo, now 12 days.", "I added 1 day in Sapporo."]
    monkeypatch.setattr(DraftService, "edit_route", lambda self, *a: RouteEdit(plan=longer, said=said))
    fake_llm.decisions.append(TurnDecision(action=TurnAction.EDIT_ITINERARY, reply="I'm swapping things around!",
                                           route_changes=[RouteChange(op="add_day", city="Sapporo")]))

    turn = say(client, auth, trip["id"], "add a day in Sapporo")

    assert turn["reply"] == " ".join(said)  # not the model's promise
    assert len(turn["trip"]["itinerary"]["days"]) == 12 and turn["trip"]["profile"]["duration_days"] == 12


def test_skip_hiroshima_and_add_osaka_in_one_message(llm):
    done = _edit(_trip(), RouteChange(op="remove", leaving="Hiroshima"), RouteChange(op="add", city="Osaka"))
    assert [c.name for c in done.plan.route] == ["Kyoto", "Osaka", "Tokyo", "Sapporo"]
    assert len(done.plan.days) == 11 and all(d.activities for d in done.plan.days)


def test_swap_a_day_for_universal_studios_keeps_the_trip_length(llm):
    trip = _edit(_trip(), RouteChange(op="swap", city="Osaka", leaving="Hiroshima")).plan
    done = _edit(trip, RouteChange(op="swap_day", city="Osaka", place="Universal Studios Japan"))
    plan = done.plan
    assert len(plan.days) == 11 and plan.route[1].days == 3
    last = [d for d in plan.days if d.city == "Osaka"][-1]
    assert "Universal Studios Japan" in [a.name for a in last.activities]
    assert [d.activities for d in plan.days if d.city == "Osaka"][:2] == [
        d.activities for d in trip.days if d.city == "Osaka"][:2]  # the other Osaka days keep their stops
    assert done.said[0] == "Your route is now Kyoto → Osaka → Tokyo → Sapporo."  # no "now N days"
    assert "One of your days in Osaka is now for Universal Studios Japan, in place of its old stops." in done.said


def test_swap_a_day_in_a_city_off_the_route_borrows_the_day(llm):
    done = _edit(_trip(), RouteChange(op="swap_day", city="Osaka", place="Universal Studios Japan"))
    assert len(done.plan.days) == 11 and "Universal Studios Japan" in _stops(done.plan, "Osaka")
    assert "Kyoto gave a day to Osaka." in done.said


def test_replacing_the_first_city_starts_the_trip_in_the_new_one(llm):
    done = _edit(_trip(), RouteChange(op="swap", city="Osaka", leaving="Kyoto"))
    assert [c.name for c in done.plan.route][0] == "Osaka" and done.plan.route[0].leg is None
    assert done.plan.route[1].leg is not None  # Osaka to Hiroshima is a new leg


def _route_turn(client, auth, fake_llm, db_session, itinerary, decision, message):
    from app.database.models import TripModel
    from test_api import ready_trip, say
    trip = ready_trip(client, auth, fake_llm)
    row = db_session.get(TripModel, trip["id"])
    row.itinerary = itinerary.model_dump(mode="json")
    db_session.commit()
    fake_llm.decisions.append(decision)
    return say(client, auth, trip["id"], message)


def test_a_one_city_trip_ignores_route_changes(client, auth, fake_llm, db_session, monkeypatch):
    from app.schemas.turn import TurnAction, TurnDecision
    monkeypatch.setattr(DraftService, "edit_route", lambda *a: pytest.fail("no route to change"))
    lisbon = Itinerary(destination="Lisbon", days=[{"day_number": 1, "activities": [
        Activity(name="Belem", category="do")]}])
    turn = _route_turn(client, auth, fake_llm, db_session, lisbon, TurnDecision(
        action=TurnAction.CHAT, reply="Nice.", route_changes=[RouteChange(op="add", city="Porto")]), "add Porto")
    assert turn["reply"].startswith("Nice.") and not any(n == "PlanEdits" for n, _ in fake_llm.prompts)


def test_a_route_edit_says_when_other_changes_in_the_message_wait(client, auth, fake_llm, db_session, monkeypatch):
    from app.schemas.personality import NoteChange
    from app.schemas.turn import TurnAction, TurnDecision
    from app.services.draft_service import RouteEdit
    monkeypatch.setattr(DraftService, "edit_route", lambda self, it, *a: RouteEdit(plan=it, said=["Your route is ..."]))
    sushi = NoteChange(op="do", target="d2", text="a sushi place", quote="add a sushi place", then="change")
    turn = _route_turn(client, auth, fake_llm, db_session, _trip(), TurnDecision(
        action=TurnAction.EDIT_ITINERARY, reply="Done!", note_changes=[sushi],
        route_changes=[RouteChange(op="swap", city="Osaka", leaving="Hiroshima")]),
        "Swap Hiroshima for Osaka and add a sushi place on day 2")
    assert "ask me again for the other changes" in turn["reply"]
