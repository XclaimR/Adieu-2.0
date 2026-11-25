"""Ticket #87: the route honours what the traveler said about the kind of place to stay (the Bali beach town)."""
import pytest

from app.schemas.dayplan import DayPlan
from app.schemas.draft import Draft
from app.schemas.itinerary import Itinerary
from app.schemas.personality import Note, Personality
from app.schemas.trip_profile import TripProfile
from app.services import route_service
from app.services.draft_service import DraftService
from app.services.llm_client import LLMClient
from app.services.route_service import RouteService

BALI = {"Ubud": (-8.51, 115.26), "Pemuteran": (-8.14, 114.65), "Amed": (-8.35, 115.65), "Lovina": (-8.16, 115.02)}
BEACH = Personality(notes=[Note(id="ne025a4", category="do", text="small beach town", mood="beach")])


def _library():
    places = [{"id": f"{n}-{i}", "name": f"{n} {i}", "category": "do", "lat": lat + i / 1000, "lng": lng}
              for n, (lat, lng) in BALI.items() for i in range(12)]
    return {"places": places, "hubs": [{"name": n, "point": list(p), "district": False} for n, p in BALI.items()]}


@pytest.fixture
def answers(monkeypatch):
    """The route model's answers in turn, each {cities, misses}; records the prompts."""
    queue, prompts = [], []

    def fake(prompt, schema, **_):
        prompts.append(prompt)
        a = queue.pop(0)
        return schema.model_validate({"cities": a["cities"], "legs": [], "airports": [],
                                      "misses": [{"city": c, "wish": w} for c, w in a.get("misses", {}).items()]})
    monkeypatch.setattr(LLMClient, "generate_structured", staticmethod(fake))
    monkeypatch.setattr(route_service, "transit", lambda a, b: None)
    return queue, prompts


def _plan():
    return RouteService.plan({"kind": "region", "code": "ID"}, _library(), 10,
                             TripProfile(destination="Bali", duration_days=10), BEACH)


def test_a_route_that_breaks_the_beach_town_wish_is_redone_once(answers):
    queue, prompts = answers
    queue += [{"cities": ["Ubud", "Pemuteran"], "misses": {"Ubud": "small beach town"}}, {"cities": ["Pemuteran", "Amed"]}]
    route, *_ = _plan()
    assert "Ubud" not in [c.name for c in route] and not any(c.unmet for c in route)
    assert len(prompts) == 2
    assert "small beach town" in prompts[0] and "comes before fame" in prompts[0]
    assert "Ubud doesn't fit 'small beach town'" in prompts[1]


def test_a_route_that_fits_is_asked_for_once(answers):
    queue, prompts = answers
    queue.append({"cities": ["Pemuteran", "Amed"]})
    _plan()
    assert len(prompts) == 1


def test_a_city_flagged_once_keeps_its_warning_if_the_second_try_forgets_it(answers):
    queue, _ = answers
    queue += [{"cities": ["Ubud"], "misses": {"Ubud": "small beach town"}}, {"cities": ["Ubud"]}]
    route, *_ = _plan()
    assert next(c for c in route if c.name == "Ubud").unmet == "small beach town"


def test_after_the_redo_a_city_that_still_breaks_the_wish_is_dropped_when_others_fit(answers):
    # The live Bali library has only Ubud, Amed and Lovina: three bases for 10 days would force Ubud in.
    queue, _ = answers
    queue += [{"cities": ["Amed", "Ubud", "Lovina"], "misses": {"Ubud": "small beach town"}}] * 2
    route, *_ = _plan()
    assert sorted(c.name for c in route) == ["Amed", "Lovina"] and sum(c.days for c in route) == 10


def test_a_wish_still_unmet_after_the_redo_is_said_plainly_on_the_plan(answers):
    queue, _ = answers
    queue += [{"cities": ["Ubud"], "misses": {"Ubud": "want: small beach town"}}] * 2
    route, *_ = _plan()
    assert next(c for c in route if c.name == "Ubud").unmet == "small beach town"
    draft = Draft(id="d", flavor="x", title="t", summary="s",
                  itinerary=Itinerary(destination="Bali", route=route, days=[
                      DayPlan(day_number=0, city=c.name, activities=[]) for c in route for _ in range(c.days)]))
    reviewed = DraftService(None).review(draft, [], TripProfile(destination="Bali", duration_days=10), BEACH)
    assert ("I couldn't find a town with enough to do that fits “small beach town”, "
                                    "so Ubud stands in.") in reviewed.warnings


# The owner's rule (Ticket #87 comment): a message that goes against an earlier wish waits for a yes.

def _held(client, auth, fake_llm, db_session, monkeypatch):
    """A route trip with the beach-town wish, then "put me in Kyoto for most of it", which goes against it."""
    from app.database.models import TripModel
    from app.schemas.turn import Conflict, RouteChange, TurnAction, TurnDecision
    from app.services.draft_service import RouteEdit
    from test_api import ready_trip, say
    from test_route_edits import _trip
    edits = []
    monkeypatch.setattr(DraftService, "edit_route",
                        lambda self, it, changes, *a: edits.append(changes) or RouteEdit(plan=it, said=["Moved."]))
    trip = ready_trip(client, auth, fake_llm)
    row = db_session.get(TripModel, trip["id"])
    row.itinerary = _trip().model_dump(mode="json")
    row.personality = BEACH.model_dump(mode="json")
    db_session.commit()
    fake_llm.decisions.append(TurnDecision(
        action=TurnAction.EDIT_ITINERARY, reply="Sure!", route_changes=[RouteChange(op="swap", city="Kyoto",
                                                                                    leaving="Tokyo")],
        conflict=Conflict(note="ne025a4", now="put me in Kyoto", keep="the beach town", new="Kyoto")))
    turn = say(client, auth, trip["id"], "put me in Kyoto for most of it")
    return trip["id"], turn, edits, lambda text: say(client, auth, trip["id"], text)


def _notes(turn):
    return [n["text"] for n in turn["trip"]["personality"]["notes"]]


def test_a_contradiction_is_pointed_out_and_nothing_changes_until_they_answer(client, auth, fake_llm, db_session,
                                                                              monkeypatch):
    _, turn, edits, _ = _held(client, auth, fake_llm, db_session, monkeypatch)
    assert turn["reply"] == "I haven't changed anything yet."
    assert turn["next_question"] == "These two don't fit together. Which should I go with?"
    assert turn["answer_options"] == ["Keep the beach town", "Go with Kyoto"]
    assert turn["trip"]["conflict"] == {"earlier": "small beach town", "now": "put me in Kyoto"}
    assert edits == [] and _notes(turn) == ["small beach town"]


def test_going_with_the_new_ask_applies_it_and_forgets_the_old_wish(client, auth, fake_llm, db_session, monkeypatch):
    _, _, edits, say = _held(client, auth, fake_llm, db_session, monkeypatch)
    asked = len(fake_llm.prompts)
    turn = say("Go with Kyoto")
    assert [c.city for c in edits[0]] == ["Kyoto"] and turn["reply"] == "Moved."
    assert "small beach town" not in _notes(turn) and turn["trip"]["conflict"] is None
    assert not [n for n, _ in fake_llm.prompts[asked:] if n == "TurnDecision"]  # the held decision, not a new one
    assert [m["content"] for m in turn["trip"]["messages"] if m["role"] == "user"][-2:] == [
        "put me in Kyoto for most of it", "Go with Kyoto"]


def test_keeping_the_earlier_wish_drops_the_change(client, auth, fake_llm, db_session, monkeypatch):
    _, _, edits, say = _held(client, auth, fake_llm, db_session, monkeypatch)
    turn = say("Keep the beach town")
    assert turn["reply"] == "Okay, I kept the beach town. Nothing changed."
    assert edits == [] and _notes(turn) == ["small beach town"] and turn["trip"]["conflict"] is None


def test_saying_it_again_after_being_asked_is_the_answer(client, auth, fake_llm, db_session, monkeypatch):
    from app.schemas.turn import Conflict, RouteChange, TurnAction, TurnDecision
    _, _, edits, say = _held(client, auth, fake_llm, db_session, monkeypatch)
    fake_llm.decisions.append(TurnDecision(
        action=TurnAction.EDIT_ITINERARY, reply="Sure!", route_changes=[RouteChange(op="swap", city="Kyoto",
                                                                                    leaving="Tokyo")],
        conflict=Conflict(note="ne025a4", now="Kyoto", keep="the beach town", new="Kyoto")))
    turn = say("No really, Kyoto")
    assert [c.city for c in edits[0]] == ["Kyoto"] and "small beach town" not in _notes(turn)
    assert turn["trip"]["conflict"] is None
