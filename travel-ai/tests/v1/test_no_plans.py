import json

import httpx
import pytest
from openai import RateLimitError

from app.database.models.trip import TripModel
from app.schemas.personality import NoteChange
from app.schemas.turn import OptionsKind, OptionsRequest, TurnAction, TurnDecision
from app.services import places_service
from app.services.draft_service import DraftPlan, PlanDay, PlanStop
from app.services.llm_client import LLMClient
from app.services.places_service import PlacesService
from app.workflows.travel_planner_workflow import TravelPlannerWorkflow
from tests.v1.conftest import VIEWPORT
from tests.v1.test_api import ready_trip, say

REAL_POST = PlacesService._post  # taken before the fake_places fixture replaces it


def box(south, west, north, east):
    return {"low": {"latitude": south, "longitude": west}, "high": {"latitude": north, "longitude": east}}


def corners(area):
    return [area[c][axis] for c in ("low", "high") for axis in ("latitude", "longitude")]


def searched_area(db_session, fake_places, viewport):
    PlacesService(db_session).text_search("things to do", viewport=viewport)
    return fake_places.bodies[-1]["locationRestriction"]["rectangle"]


def plan_turn(client, auth, trip_id, message="not sure"):
    with client.stream("POST", f"/trips/{trip_id}/messages/stream", json={"message": message}, headers=auth) as r:
        return [json.loads(line) for line in r.iter_lines() if line]


def assert_no_plans(client, auth, trip_id, events, reason, retry_now=True):
    """No false claim, and the same words live, saved and after a reload."""
    assert [e["type"] for e in events].count("draft") == 0
    assert events[-1]["type"] == "done"
    shown = " ".join(e["text"] for e in events if e["type"] in ("reply", "note"))
    assert "Here are" not in shown
    assert reason in shown
    reloaded = client.get(f"/trips/{trip_id}", headers=auth).json()
    assert reloaded["messages"][-1]["content"] == shown
    assert reloaded["drafts"] == []
    assert reason in reloaded["no_plans"]["reason"]
    assert reloaded["no_plans"]["retry_now"] is retry_now
    assert events[-1]["trip"]["no_plans"] == reloaded["no_plans"]


# ---------- pin-sized areas ----------

def test_a_saved_pin_sized_area_is_widened_for_drafts_options_swaps_and_edits(
        client, auth, db_session, fake_llm, fake_places, monkeypatch):
    # About what Google returned for Ari Atoll, already cached and then saved on the trip.
    pin = box(3.86465, 72.83065, 3.86735, 72.83335)
    PlacesService(db_session)._store("resolve-geo:lisbon", {"area": {
        "place_id": "a", "name": "Lisbon", "formatted_address": None, "viewport": pin, "photo_name": None}})

    def like_google(service, body, mask):
        found = fake_places(service, body, mask)
        area = body.get("locationRestriction")
        return [] if area and area["rectangle"] == pin else found

    monkeypatch.setattr(PlacesService, "_post", like_google)
    trip = ready_trip(client, auth, fake_llm)

    drafts = say(client, auth, trip["id"], "not sure")["drafts"]
    assert len(drafts) == 3
    fake_llm.decisions.append(TurnDecision(action=TurnAction.SHOW_OPTIONS, reply="Sure.",
                                           options_request=OptionsRequest(kind=OptionsKind.EAT, query="seafood")))
    assert say(client, auth, trip["id"], "show me seafood")["options"]["items"]
    client.post(f"/trips/{trip['id']}/drafts/{drafts[0]['id']}/choose", headers=auth)
    swap = client.post(f"/trips/{trip['id']}/itinerary/days/1/activities/0", json={"action": "swap"}, headers=auth)
    assert swap.json()["options"]["items"]
    fake_llm.decisions.append(TurnDecision(
        action=TurnAction.EDIT_ITINERARY, reply="Sure.", edit_request="more boats",
        note_changes=[NoteChange(op="add", text="boats", quote="more boats", then="change", search="boat tours")]))
    say(client, auth, trip["id"], "more boats")

    assert any("boat tours" in b["textQuery"] for b in fake_places.bodies)
    for body in fake_places.bodies:  # no resolve search: the cached area was used as it was
        assert corners(body["locationRestriction"]["rectangle"]) == pytest.approx([3.366, 72.332, 4.366, 73.332])


@pytest.mark.parametrize("viewport", [
    VIEWPORT,
    box(41.9002, 12.4457, 41.9075, 12.4584),  # Vatican City
    box(43.7247, 7.4091, 43.7519, 7.4398),    # Monaco
    box(44.1330, 9.6810, 44.1380, 9.6860),    # a village about 500 m across
    box(44.1330, 9.6810, 44.1355, 9.6900),    # narrow, but not a pin
    box(-19.2, 177.0, -16.0, -178.0),         # crosses the 180th meridian
])
def test_a_normal_area_is_untouched(db_session, fake_places, viewport):
    assert searched_area(db_session, fake_places, viewport) == viewport


@pytest.mark.parametrize("pin, widened", [
    (box(-17.0013, 179.9987, -16.9987, -179.9987), [-17.5, 179.5, -16.5, -179.5]),
    (box(10.0, 179.9, 10.002, 179.902), [9.501, 179.401, 10.501, -179.599]),
    (box(10.0, -179.902, 10.002, -179.9), [9.501, 179.599, 10.501, -179.401]),
    (box(89.997, 20.0, 89.999, 20.002), [89.498, 19.501, 90.0, 20.501]),
    (box(-89.999, 20.0, -89.997, 20.002), [-90.0, 19.501, -89.498, 20.501]),
    # Google leaves zeros out of its JSON.
    ({"low": {}, "high": {"latitude": 0.002, "longitude": 0.002}}, [-0.499, -0.499, 0.501, 0.501]),
])
def test_a_pin_at_the_edge_of_the_map_gets_a_valid_area(db_session, fake_places, pin, widened):
    assert corners(searched_area(db_session, fake_places, pin)) == pytest.approx(widened)


# ---------- zero drafts ----------

def google_says(code, message, **more):
    body = {"error": {"code": code, "message": message, **more}}
    return lambda *a, **k: httpx.Response(code, text=json.dumps(body, indent=2))


def google_times_out(*a, **k):
    raise httpx.ReadTimeout("timed out")


@pytest.mark.parametrize("google, reason, retry_now", [
    (google_says(429, "Quota exceeded for quota metric 'SearchTextRequest' and limit 'SearchTextRequest per day' "
                      "of service 'places.googleapis.com'"), "unavailable for now", False),
    (google_says(429, "Quota exceeded for quota metric 'SearchTextRequest'. " + "x" * 250,
                 details=[{"quota_limit": "SearchTextRequestPerDayPerProject"}]), "unavailable for now", False),
    (google_says(429, "Quota exceeded for limit 'SearchTextRequest per minute'"), "places service", True),
    (google_says(500, "Internal error"), "places service", True),
    (google_times_out, "places service", True),
])
def test_places_failing_before_the_first_draft(client, auth, fake_llm, monkeypatch, google, reason, retry_now):
    trip = ready_trip(client, auth, fake_llm)
    monkeypatch.setattr(places_service.httpx, "post", google)
    monkeypatch.setattr(PlacesService, "_post", REAL_POST)

    events = plan_turn(client, auth, trip["id"])

    assert_no_plans(client, auth, trip["id"], events, reason, retry_now)
    assert "DraftPlan" not in [name for name, _ in fake_llm.prompts]


@pytest.mark.parametrize("found", [
    lambda query: [],
    lambda query: [{"id": "castle", "displayName": {"text": "Castle"}}],
    lambda query: [{"id": f"h{i}"} for i in range(3)] if "hotels" in query else [],
], ids=["nothing", "one place", "only stays"])
def test_too_few_places_makes_no_llm_call(client, auth, fake_llm, monkeypatch, found):
    trip = ready_trip(client, auth, fake_llm)
    monkeypatch.setattr(PlacesService, "_post", lambda self, body, mask: found(body["textQuery"]))

    events = plan_turn(client, auth, trip["id"])

    assert_no_plans(client, auth, trip["id"], events, "couldn't find enough places")
    assert "DraftPlan" not in [name for name, _ in fake_llm.prompts]


def on_draft(monkeypatch, fake_llm, answer):
    def generate(prompt, schema, **kwargs):
        return answer() if schema is DraftPlan else fake_llm(prompt, schema, **kwargs)
    monkeypatch.setattr(LLMClient, "generate_structured", staticmethod(generate))


def invented_plan():
    stop = PlanStop(place="Invented Place", ref=999, time_of_day="morning", note="invented")
    return DraftPlan(title="t", summary="s", days=[PlanDay(theme="x", stops=[stop, stop])])


def llm_down():
    raise RuntimeError("model is down")


def llm_out_for_today():
    response = httpx.Response(429, request=httpx.Request("POST", "http://llm"))
    raise RateLimitError("Error code: 429 - quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                         response=response, body=None)


@pytest.mark.parametrize("answer, reason, retry_now", [
    (invented_plan, "real places", True),
    (llm_down, "AI planner", True),
    (llm_out_for_today, "unavailable for now", False),
])
def test_every_draft_lost(client, auth, fake_llm, monkeypatch, answer, reason, retry_now):
    trip = ready_trip(client, auth, fake_llm)
    on_draft(monkeypatch, fake_llm, answer)

    events = plan_turn(client, auth, trip["id"])

    assert_no_plans(client, auth, trip["id"], events, reason, retry_now)


def test_retry_replaces_the_failure_with_plans(client, auth, fake_llm, fake_places, monkeypatch):
    trip = ready_trip(client, auth, fake_llm)
    with monkeypatch.context() as m:
        m.setattr(PlacesService, "_post", lambda self, body, mask: [])
        assert say(client, auth, trip["id"], "not sure")["trip"]["no_plans"]
    monkeypatch.setattr(PlacesService, "_cached", lambda *a, **k: None)  # as if the cache had expired

    turn = say(client, auth, trip["id"], "__show_plans__")

    assert len(turn["drafts"]) == 3 and turn["trip"]["no_plans"] is None
    assert "Here are 3 ways" in turn["reply"]
    assert turn["trip"]["messages"][-1]["content"] == turn["reply"]


def test_failing_keeps_a_plan_chosen_while_waiting(client, auth, fake_llm, session_factory, monkeypatch):
    trip = ready_trip(client, auth, fake_llm)
    older = say(client, auth, trip["id"], "not sure")["drafts"][0]

    def choose_then_fail():
        with session_factory() as db:
            row = db.get(TripModel, trip["id"])
            if not row.itinerary:
                TravelPlannerWorkflow(db).choose_draft(row, None, older["id"])
        raise RuntimeError("model is down")

    on_draft(monkeypatch, fake_llm, choose_then_fail)
    done = plan_turn(client, auth, trip["id"], "__show_plans__")[-1]["trip"]

    assert done["itinerary"] and "Looks perfect" in done["answer_options"]


CLAIM = "I've put together a few relaxed ways to spend your days. Take a look."


def test_the_models_own_claim_waits_for_a_draft(client, auth, fake_llm, monkeypatch):
    # Seen live with gemini-3.1-flash-lite, right above the quota error.
    trip = ready_trip(client, auth, fake_llm)
    fake_llm.decisions.append(TurnDecision(action=TurnAction.OFFER_DRAFTS, reply=CLAIM))
    monkeypatch.setattr(PlacesService, "_post", lambda self, body, mask: [])

    events = plan_turn(client, auth, trip["id"])

    assert CLAIM not in json.dumps(events)
    assert_no_plans(client, auth, trip["id"], events, "couldn't find enough places")


def test_with_drafts_the_reply_shown_is_the_reply_saved(client, auth, fake_llm):
    trip = ready_trip(client, auth, fake_llm)
    fake_llm.decisions.append(TurnDecision(action=TurnAction.OFFER_DRAFTS, reply=CLAIM))

    events = plan_turn(client, auth, trip["id"])

    replies = [e["text"] for e in events if e["type"] == "reply"]
    assert CLAIM not in replies[0]
    assert replies[-1].startswith(CLAIM) and "Here are 3 ways" in replies[-1]
    assert [e["type"] for e in events].index("draft") > events.index(next(e for e in events if e.get("text") == replies[-1]))
    assert events[-1]["trip"]["messages"][-1]["content"] == replies[-1]
