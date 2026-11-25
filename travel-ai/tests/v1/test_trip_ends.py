"""Ticket #81: where the trip starts and ends. Asked once, gateways to land in and leave from, a route that runs
one way between them, and light first and last days."""
import pytest

from app.schemas.itinerary import Flight
from app.schemas.personality import Personality
from app.schemas.trip_profile import Party, TripProfile
from app.schemas.turn import TurnAction, TurnDecision
from app.services import route_service
from app.services.draft_service import _route_rules, _trip_ends_rules
from app.services.itinerary_critique_service import ItineraryCritiqueService
from app.services.route_service import RouteService, flights, order
from app.services.turn_service import (BACK_ELSEWHERE, HOME_BOTH_WAYS, MAX_QUESTIONS, OTHER_CITY, START_ELSEWHERE,
                                       TurnService, read_ends)
from tests.v1.test_api import decision, mood, new_trip, say
from tests.v1.test_routes import JAPAN, POOL, _library, _plan, _town

DELHI = {"name": "Delhi", "lat": 28.61, "lng": 77.21}
LOS_ANGELES = {"name": "Los Angeles", "lat": 34.05, "lng": -118.24}


@pytest.fixture(autouse=True)
def no_transitous(monkeypatch):
    monkeypatch.setattr(route_service, "transit", lambda a, b: None)


def _names(cities):
    return [c["name"] if isinstance(c, dict) else c.name for c in cities]


def _plan_with(monkeypatch, picks, airports, home=DELHI, back=DELHI, days=12):
    monkeypatch.setattr(RouteService, "_ask", staticmethod(lambda *a: (picks, {}, None, airports, {})))
    route, _, ends = RouteService.plan({"kind": "country", "code": "JP"}, _library(), days,
                                       TripProfile(destination="Japan", origin="Delhi"), Personality(), home, back)
    return route, ends


def _backtracks(route) -> bool:
    """A city closer to the start than the one before it: the trip turned back."""
    start = (route[0].lat, route[0].lng)
    far = [route_service.distance_km(start, (c.lat, c.lng)) for c in route]
    return any(b < a for a, b in zip(far, far[1:]))


# ---------- the route between the gateways ----------

def test_a_zig_zag_from_the_model_is_straightened_between_the_gateways(monkeypatch):
    # The faked model lists Hiroshima, then Tokyo, then Osaka: west, east, west again.
    asked = []

    def fake_model(prompt, schema, **kwargs):
        asked.append(prompt)
        return schema.model_validate({"cities": ["Hiroshima", "Tokyo", "Osaka", "Kyoto"], "legs": [],
                                      "airports": ["Tokyo", "Osaka", "Hiroshima"]})
    monkeypatch.setattr(route_service.LLMClient, "generate_structured", staticmethod(fake_model))
    route, *_ = RouteService.plan({"kind": "country", "code": "JP"}, _library(), 12,
                                 TripProfile(destination="Japan", origin="Delhi"), Personality(), DELHI, DELHI)
    assert "They fly in from Delhi and back to Delhi." in asked[0]
    assert _names(route) == ["Hiroshima", "Osaka", "Kyoto", "Tokyo"]
    assert not _backtracks(route)
    assert sum(c.days for c in route) == 12


def test_open_jaw_lands_in_tokyo_and_leaves_from_osaka(monkeypatch):
    route, (fly_in, fly_out) = _plan_with(monkeypatch, ["Tokyo", "Kyoto", "Osaka"], ["Tokyo", "Osaka"], days=9)
    assert _names(route) == ["Tokyo", "Kyoto", "Osaka"]
    assert (fly_in.frm, fly_in.to, fly_out.frm, fly_out.to) == ("Delhi", "Tokyo", "Osaka", "Delhi")
    assert fly_in.transfer is None and fly_out.transfer is None  # both cities have the airport
    assert fly_in.mode == fly_out.mode == "flight" and 7 * 60 <= fly_in.minutes <= 8 * 60 + 30  # as quoted: ~7h50


PORTS = [_town("Tokyo"), _town("Osaka")]


def test_the_route_ends_at_an_airport_rather_than_where_the_cities_run_out():
    # Kyoto has no airport: Tokyo, Osaka, Kyoto would mean a trip back to Osaka for the flight home.
    cities = [_town(n) for n in ("Tokyo", "Kyoto", "Osaka")]
    assert _names(order(cities, DELHI, DELHI, PORTS)) == ["Tokyo", "Kyoto", "Osaka"]
    # Without the airports (the model failed) the route still runs one way from the main city.
    assert _names(order(cities, DELHI, DELHI)) == ["Tokyo", "Kyoto", "Osaka"]


def test_flying_home_somewhere_else_turns_the_route_toward_it():
    cities = [_town(n) for n in ("Tokyo", "Kyoto", "Osaka")]
    # From Delhi, home to Los Angeles: land in Osaka and leave from Tokyo, the side nearer Los Angeles.
    assert _names(order(cities, DELHI, LOS_ANGELES, PORTS)) == ["Osaka", "Kyoto", "Tokyo"]


def _city(name):
    return route_service.City(name=name, lat=JAPAN[name][0], lng=JAPAN[name][1])


def test_a_trip_can_end_in_a_city_without_an_airport_and_ride_to_one():
    # Tokyo then Kyoto: the flight home leaves from Osaka, a train ride from Kyoto.
    assert _names(order([_town("Tokyo"), _town("Kyoto")], DELHI, DELHI, PORTS)) == ["Tokyo", "Kyoto"]
    fly_in, fly_out = flights([_city("Tokyo"), _city("Kyoto")], DELHI, DELHI, "JP", PORTS)
    assert (fly_in.to, fly_in.transfer) == ("Tokyo", None)
    assert (fly_out.frm, fly_out.to, fly_out.mode) == ("Osaka", "Delhi", "flight")
    assert fly_out.transfer.mode == "train" and fly_out.transfer.minutes <= 60
    assert (fly_out.lat, fly_out.lng) == JAPAN["Osaka"]  # kept, so a route edit still knows the airport


def test_a_home_close_by_goes_by_land_any_vehicle():
    osaka = {"name": "Osaka", "lat": 34.69, "lng": 135.50}
    fly_in, fly_out = flights([_city("Tokyo")], osaka, osaka, "JP", PORTS)
    assert (fly_in.frm, fly_in.to) == ("Osaka", "Tokyo") and fly_in.mode == "high-speed train"
    assert fly_out.mode == "high-speed train" and fly_out.transfer is None


def test_without_a_home_there_are_no_flights():
    assert flights([route_service.City(name="Tokyo", lat=35.69, lng=139.69)], None, None) == (None, None)


# ---------- light first and last days ----------

def _flown():
    it = POOL.to_itinerary(_plan(), "Japan")
    it.fly_in = Flight(mode="flight", minutes=600, frm="Delhi", to="Tokyo")
    it.fly_out = Flight(mode="flight", minutes=560, frm="Kyoto", to="Delhi")
    return it


def test_the_drafting_prompt_makes_the_first_and_last_days_light():
    it = _flown()
    rules = _trip_ends_rules(it.fly_in, it.fly_out, 4)
    assert "Day 1 is the arrival day: Delhi to Tokyo by flight (~10h 00m)" in rules
    assert "Day 4 is the departure day: Kyoto to Delhi by flight" in rules
    assert "arrival" not in _route_rules(it.route) + _trip_ends_rules(None, None, 4)  # trips from before Ticket #81
    ride = Flight(mode="flight", minutes=560, frm="Osaka", to="Delhi",
                  transfer=route_service.Leg(mode="train", minutes=30))
    assert "~30 min by train to Osaka, then Osaka to Delhi by flight" in _trip_ends_rules(None, ride, 4)


def test_the_review_flags_a_busy_arrival_or_departure_day():
    it = _flown()
    first, last = it.days[0], it.days[-1]
    first.activities = [first.activities[0].model_copy(update={"id": f"a{i}", "time_of_day": "evening"})
                        for i in range(4)]
    last.activities = [first.activities[0].model_copy(update={"id": f"b{i}", "time_of_day": "morning"})
                       for i in range(4)]
    problems = [x.problem for x in ItineraryCritiqueService.counted(it, POOL.places, days=4)]
    assert "Day 1 is the arrival day with 4 stops" in problems
    assert "Day 4 is the departure day with 4 stops" in problems


# ---------- the questions ----------

READY = TripProfile(destination="Japan", mood=["relaxed"], party=Party.COUPLE, duration_days=9)


def _plan_turn(profile, message="hi", asked=("duration",), home=None, questions=0):
    return TurnService.plan(TurnDecision(action=TurnAction.CHAT, reply="ok"), profile, message=message,
                            asked=list(asked), questions_streak=0, has_drafts=False, has_itinerary=False,
                            questions=questions, home=home)


def test_the_first_trip_asks_for_the_start_city_then_the_return():
    asked = _plan_turn(READY)
    assert (asked.asked_field, asked.answer_options) == ("origin", [])
    assert "starting from" in asked.next_question
    with_start = READY.model_copy(update={"origin": "Delhi"})
    back = _plan_turn(with_start, asked=("duration", "origin"))
    assert back.asked_field == "return" and back.answer_options == ["Back to Delhi", OTHER_CITY]
    assert read_ends(with_start, "Back to Delhi", "return", None).return_to == "Delhi"
    elsewhere = _plan_turn(with_start, message=OTHER_CITY, asked=("duration", "origin", "return"))
    assert elsewhere.asked_field == "return_city"


def test_a_known_home_is_one_tap_to_confirm():
    asked = _plan_turn(READY, home="Delhi")
    assert asked.next_question == "Starting from Delhi and flying back there?"
    assert asked.answer_options == ["Yes, Delhi both ways", START_ELSEWHERE, BACK_ELSEWHERE]
    both = read_ends(READY, HOME_BOTH_WAYS.format(home="Delhi"), "origin", "Delhi")
    assert (both.origin, both.return_to) == ("Delhi", "Delhi")
    assert _plan_turn(both, asked=("duration", "origin"), home="Delhi").action == TurnAction.OFFER_DRAFTS


def test_starting_or_ending_elsewhere_asks_for_that_city():
    assert _plan_turn(READY, message=START_ELSEWHERE, asked=("duration", "origin"), home="Delhi").asked_field \
        == "origin_city"
    leaving = read_ends(READY, BACK_ELSEWHERE, "origin", "Delhi")
    assert (leaving.origin, leaving.return_to) == ("Delhi", None)
    assert _plan_turn(leaving, message=BACK_ELSEWHERE, asked=("duration", "origin"), home="Delhi").asked_field \
        == "return_city"
    assert read_ends(leaving, "to Mumbai", "return_city", "Delhi").return_to == "Mumbai"


def test_a_long_or_unsure_answer_is_left_to_the_model():
    assert read_ends(READY, "Show me some plans", "origin", None).origin is None
    assert read_ends(READY, "honestly I have not decided that yet", "origin", None).origin is None
    assert read_ends(READY, "dunno", "origin", None, stuck=True).origin is None
    assert read_ends(READY, "Delhi", "mood", None).origin is None  # not answering that question


def test_these_questions_count_toward_the_cap():
    assert _plan_turn(READY, questions=MAX_QUESTIONS - 1).action == TurnAction.ASK
    assert _plan_turn(READY, questions=MAX_QUESTIONS).action == TurnAction.OFFER_DRAFTS


def test_the_start_city_is_asked_once_and_remembered_for_the_next_trip(client, auth, fake_llm, fake_places):
    trip = new_trip(client, auth)
    fake_llm.decisions.append(decision(destination="Japan", notes=[mood("relaxed", "relaxed")], party=Party.COUPLE,
                                       duration_days=9))
    turn = say(client, auth, trip["id"], "Japan, relaxed, with my partner, 9 days")
    assert turn["next_question"].startswith("Which city are you starting from?")
    # The model leaves "Delhi" alone (or takes it for a destination): code reads it as the answer.
    fake_llm.decisions.append(decision(destination="Delhi"))
    turn = say(client, auth, trip["id"], "Delhi")
    assert turn["trip"]["profile"]["destination"] == "Japan" and turn["trip"]["profile"]["origin"] == "Delhi"
    assert turn["answer_options"] == ["Back to Delhi", OTHER_CITY]
    turn = say(client, auth, trip["id"], "Back to Delhi")
    assert turn["drafts"] and turn["trip"]["profile"]["return_to"] == "Delhi"
    assert client.get("/me", headers=auth).json()["taste"]["home"] == "Delhi"

    again = new_trip(client, auth)
    fake_llm.decisions.append(decision(destination="Lisbon", notes=[mood("foodie", "food")], party=Party.COUPLE,
                                       duration_days=4))
    turn = say(client, auth, again["id"], "Lisbon, food, with my partner, 4 days")
    assert turn["next_question"] == "Starting from Delhi and flying back there?"
    turn = say(client, auth, again["id"], "Yes, Delhi both ways")
    assert turn["drafts"]
    one_city = turn["drafts"][0]["itinerary"]  # a one-place trip has a way in and out too
    assert (one_city["fly_in"]["frm"], one_city["fly_in"]["to"], one_city["fly_out"]["to"]) == ("Delhi", "Lisbon", "Delhi")
    assert any("Day 1 is the arrival day" in prompt for name, prompt in fake_llm.prompts if name == "DraftPlan")
    assert (turn["trip"]["profile"]["origin"], turn["trip"]["profile"]["return_to"]) == ("Delhi", "Delhi")


def test_skipping_the_question_plans_from_home_anyway(client, auth, fake_llm, fake_places):
    client.post("/me/taste", json={"vibes": ["food"]}, headers=auth)
    trip = new_trip(client, auth)
    fake_llm.decisions.append(decision(destination="Lisbon", notes=[mood("foodie", "food")], party=Party.COUPLE,
                                       duration_days=4, origin="Delhi"))
    say(client, auth, trip["id"], "Lisbon, food, with my partner, 4 days, from Delhi")
    turn = say(client, auth, trip["id"], "__show_plans__")
    assert turn["drafts"] and turn["trip"]["profile"]["return_to"] == "Delhi"  # back where they started


def test_an_end_far_from_every_known_airport_flies_from_the_city_itself():
    # Sapporo is a flight from Tokyo and Osaka: never a flight to an airport and then another one home.
    _, fly_out = flights([_city("Tokyo"), _city("Sapporo")], DELHI, DELHI, "JP", PORTS)
    assert (fly_out.frm, fly_out.transfer) == ("Sapporo", None)


def test_a_typed_yes_or_no_is_an_answer_never_a_city():
    assert (read_ends(READY, "yes", "origin", "Delhi").origin, read_ends(READY, "Yep!", "origin", "Delhi").return_to) \
        == ("Delhi", "Delhi")
    assert read_ends(READY, "no", "origin", "Delhi").origin is None
    assert _plan_turn(READY, message="no", asked=("duration", "origin"), home="Delhi").asked_field == "origin_city"
    with_start = READY.model_copy(update={"origin": "Delhi"})
    assert read_ends(with_start, "yes", "return", None).return_to == "Delhi"
    assert read_ends(with_start, "nope", "return", None).return_to is None
    assert _plan_turn(with_start, message="nope", asked=("duration", "origin", "return")).asked_field == "return_city"
    assert read_ends(READY, "yes", "origin", None).origin is None  # no home to say yes to
