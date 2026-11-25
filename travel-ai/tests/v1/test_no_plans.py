import json
import re

import httpx
import pytest
from openai import RateLimitError

from app.database.models.trip import TripModel
from app.schemas.personality import NoteChange
from app.schemas.turn import OptionsKind, OptionsRequest, TurnAction, TurnDecision
from app.services import place_library, places_service
from app.services.draft_service import DraftPlan, PlanDay, PlanStop
from app.services.llm_client import LLMClient
from app.services.open_data import OpenDataError
from app.workflows.travel_planner_workflow import TravelPlannerWorkflow
from tests.v1.test_api import ready_trip, say

def library_of(*places):
    return {"places": [{"id": pid, "name": name, "category": category, "kind": "", "text": None, "lat": 1.5,
                        "lng": 1.5, "area": None, "guide": None, "tier": None, "score": 1.0}
                       for pid, name, category in places], "hubs": [], "release": "test",
            "partial": ["few places"], "version": 2}  # as place_library.build marks it


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
    assert not re.search("google|quota|limit", shown, re.I), "provider detail shown to the traveler"
    reloaded = client.get(f"/trips/{trip_id}", headers=auth).json()
    assert reloaded["messages"][-1]["content"] == shown
    assert reloaded["drafts"] == []
    assert reason in reloaded["no_plans"]["reason"]
    assert reloaded["no_plans"]["retry_now"] is retry_now
    assert events[-1]["trip"]["no_plans"] == reloaded["no_plans"]


# ---------- trips saved with Google's areas ----------

def test_a_trip_saved_with_a_pin_sized_google_area_is_found_again_and_planned(
        client, auth, db_session, fake_llm, fake_places):
    trip = ready_trip(client, auth, fake_llm)
    # Roughly what Google saved for Ari Atoll: a 300 m box around a pin, and no open-data id.
    pin = {"low": {"latitude": 3.86465, "longitude": 72.83065}, "high": {"latitude": 3.86735, "longitude": 72.83335}}
    row = db_session.get(TripModel, trip["id"])
    row.destination_area = {"place_id": "ChIJ-google", "name": "Lisbon", "formatted_address": None,
                            "viewport": pin, "photo_name": "places/ChIJ-google/photos/x"}
    db_session.commit()
    fake_places.resolved.clear()

    drafts = say(client, auth, trip["id"], "not sure")["drafts"]

    assert len(drafts) == 3
    assert fake_places.resolved == ["Lisbon"]  # by name, near the saved box
    assert client.get(f"/trips/{trip['id']}", headers=auth).json()["destination_name"] == "Lisbon"


# ---------- zero drafts ----------

def test_open_data_failing_before_the_first_draft(client, auth, fake_llm, fake_places, monkeypatch):
    trip = ready_trip(client, auth, fake_llm)

    def down(area, releases):
        raise OpenDataError("en.wikivoyage.org answered 503")

    monkeypatch.setattr(place_library, "build", down)

    events = plan_turn(client, auth, trip["id"])

    assert_no_plans(client, auth, trip["id"], events, "Place data is unavailable", True)
    assert "DraftPlan" not in [name for name, _ in fake_llm.prompts]


@pytest.mark.parametrize("places", [
    [],
    [("castle", "Castle", "do")],
    [(f"h{i}", f"Hotel {i}", "stay") for i in range(3)],
], ids=["nothing", "one place", "only stays"])
def test_too_few_places_makes_no_llm_call(client, auth, fake_llm, fake_places, places):
    trip = ready_trip(client, auth, fake_llm)
    fake_places.library = library_of(*places)

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
    (llm_out_for_today, "AI planner is unavailable", False),
])
def test_every_draft_lost(client, auth, fake_llm, monkeypatch, answer, reason, retry_now):
    trip = ready_trip(client, auth, fake_llm)
    on_draft(monkeypatch, fake_llm, answer)

    events = plan_turn(client, auth, trip["id"])

    assert_no_plans(client, auth, trip["id"], events, reason, retry_now)


def test_retry_replaces_the_failure_with_plans(client, auth, fake_llm, fake_places, monkeypatch):
    trip = ready_trip(client, auth, fake_llm)
    fake_places.library = library_of()
    assert say(client, auth, trip["id"], "not sure")["trip"]["no_plans"]
    fake_places.library = None
    monkeypatch.setattr(places_service, "PARTIAL_TTL_HOURS", 0)  # a near-empty library is kept only briefly

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


def test_the_models_own_claim_waits_for_a_draft(client, auth, fake_llm, fake_places):
    # Seen live with gemini-3.1-flash-lite, right above the failure.
    trip = ready_trip(client, auth, fake_llm)
    fake_llm.decisions.append(TurnDecision(action=TurnAction.OFFER_DRAFTS, reply=CLAIM))
    fake_places.library = library_of()

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


# ---------- one draft lost (Ticket #43) ----------

def rate_limited():
    response = httpx.Response(429, request=httpx.Request("POST", "http://llm"))
    return RateLimitError("rate limited", response=response, body=None)


@pytest.mark.parametrize("fails, drafts", [(1, 3), (2, 2)])
def test_a_rate_limited_draft_is_tried_once_more_then_named(client, auth, fake_llm, monkeypatch, fails, drafts):
    trip = ready_trip(client, auth, fake_llm)
    left = [fails]

    def generate(prompt, schema, **kwargs):
        if schema is DraftPlan and "Off the beaten path" in prompt and left[0]:
            left[0] -= 1
            raise rate_limited()
        return fake_llm(prompt, schema, **kwargs)
    monkeypatch.setattr(LLMClient, "generate_structured", staticmethod(generate))

    events = plan_turn(client, auth, trip["id"])

    assert [e["type"] for e in events].count("draft") == drafts
    shown = " ".join(e["text"] for e in events if e["type"] == "note")
    assert ("One plan couldn't be drafted right now" in shown) is (drafts < 3)
    assert client.get(f"/trips/{trip['id']}", headers=auth).json()["messages"][-1]["content"].endswith(shown)
