"""Ticket #27: multi-city routes. Order, nights, pace, a stay per city, days inside their city."""
import pytest

from app.schemas.itinerary import City, Itinerary, Leg
from app.schemas.personality import Personality, Scale
from app.schemas.place import PickCategory, PlaceCandidate
from app.schemas.trip_profile import TripProfile
from app.services.draft_service import DraftService, PlacePool, PlanDay, PlanStop, RouteDraftPlan
from app.services.itinerary_critique_service import ItineraryCritiqueService
from app.services import route_service
from app.services.route_service import RouteService, checked_leg, city_count, order, share_days, towns

# Japan's main towns, best known first (as the model would rank them), coordinates from the gazetteer.
JAPAN = {
    "Tokyo": (35.69, 139.69), "Kyoto": (35.01, 135.77), "Osaka": (34.69, 135.50),
    "Hiroshima": (34.39, 132.46), "Kanazawa": (36.57, 136.65), "Sapporo": (43.06, 141.35),
}


def _town(name):
    lat, lng = JAPAN[name]
    return {"name": name, "lat": lat, "lng": lng}


def _library():
    places = [{"id": f"{name}-{i}", "name": f"{name} sight {i}", "category": "do" if i % 2 else "eat",
               "lat": lat + i / 1000, "lng": lng, "town": name}
              for name, (lat, lng) in JAPAN.items() for i in range(12 - list(JAPAN).index(name))]
    hubs = [{"name": n, "point": list(p), "district": False} for n, p in JAPAN.items()]
    hubs.append({"name": "Shinjuku", "point": [35.69, 139.70], "district": True})
    return {"places": places, "hubs": hubs}


@pytest.fixture(autouse=True)
def no_transitous(monkeypatch):
    """Tests never call the real router; one that needs it sets its own answer."""
    monkeypatch.setattr(route_service, "transit", lambda a, b: None)
    monkeypatch.setattr(route_service, "_routed", {})
    monkeypatch.setattr(route_service, "_paused_until", 0.0)


def _pace(value):
    return Personality(scales={"pace": Scale(value=value, confidence=0.8, source="note")})


def test_the_route_goes_one_way_without_zig_zag():
    ranked = [_town(n) for n in ("Tokyo", "Kyoto", "Hiroshima", "Kanazawa", "Osaka")]
    assert [c["name"] for c in order(ranked)] == ["Tokyo", "Kanazawa", "Kyoto", "Osaka", "Hiroshima"]


def test_the_route_starts_where_flights_land_unless_starting_elsewhere_saves_distance():
    # Sapporo is far north: flying in to Tokyo and ending in Sapporo beats flying on to Sapporo first.
    ranked = [_town(n) for n in ("Tokyo", "Sapporo", "Kyoto", "Osaka", "Hiroshima")]
    assert [c["name"] for c in order(ranked)] == ["Tokyo", "Kyoto", "Osaka", "Hiroshima", "Sapporo"]
    # Two cities: start in the main one.
    assert [c["name"] for c in order([_town("Kyoto"), _town("Tokyo")])] == ["Kyoto", "Tokyo"]


def test_days_add_up_to_the_trip_and_extra_days_go_to_the_main_cities():
    ordered = [_town(n) for n in ("Tokyo", "Kanazawa", "Kyoto", "Osaka", "Hiroshima")]
    days = share_days(14, ordered, ["Tokyo", "Kyoto", "Hiroshima", "Kanazawa", "Osaka"])
    assert sum(days) == 14
    assert dict(zip([c["name"] for c in ordered], days)) == {
        "Tokyo": 3, "Kanazawa": 3, "Kyoto": 3, "Osaka": 2, "Hiroshima": 3}


def test_nights_are_strict_the_last_city_has_one_fewer():
    it = Itinerary(destination="Japan", route=[City(name="Tokyo", lat=0, lng=0), City(name="Kyoto", lat=0, lng=1)],
                   days=[{"day_number": n, "city": c, "activities": []}
                         for n, c in enumerate(["Tokyo", "Tokyo", "Kyoto", "Kyoto", "Kyoto"], 1)])
    assert [(c.days, c.nights) for c in it.route] == [(2, 2), (3, 2)]  # 5 days, 4 nights


def test_pace_changes_how_many_cities_and_so_the_nights_in_each():
    assert city_count(12, _pace(-1)) == 3  # slow: about 4 nights a city
    assert city_count(12, None) == 4  # steady: about 3
    assert city_count(12, _pace(1)) == 6  # packed: about 2
    assert city_count(3, _pace(1)) == 1  # never a city a night


def test_towns_are_real_towns_with_enough_to_do_best_known_first():
    found = [t["name"] for t in towns(_library())]
    assert "Shinjuku" not in found  # a district is part of Tokyo, not a base
    assert found[0] == "Tokyo"


def test_a_town_within_reach_of_a_base_is_a_day_trip_not_a_base():
    library = _library()
    library["hubs"].insert(1, {"name": "Nara", "point": [34.68, 135.80], "district": False})  # 37 km from Kyoto
    library["places"] += [{**p, "lat": 34.68, "lng": 135.80} for p in library["places"][:8]]  # fewer than Kyoto
    assert "Nara" not in [t["name"] for t in towns(library)]


def test_the_plan_falls_back_to_the_map_when_the_model_fails(fake_llm):
    fake_llm.errors["RouteChoice"] = RuntimeError("quota")
    route, tip, _ = RouteService.plan({"kind": "country"}, _library(), 10, TripProfile(destination="Japan"), _pace(0))
    assert sum(c.days for c in route) == 10
    assert route[0].leg is None and all(c.leg and c.leg.minutes > 0 for c in route[1:])
    assert tip is None


def test_a_city_trip_has_no_route():
    assert RouteService.plan({"kind": "city"}, _library(), 10, TripProfile(destination="Tokyo"), None) == (
        [], None, (None, None))


ROUTE = [City(name="Tokyo", lat=35.69, lng=139.69, days=2),
         City(name="Kyoto", lat=35.01, lng=135.77, days=2,
              leg=Leg(mode="bullet train", minutes=135, cost="~¥14,000", note="covered by the JR Pass"))]


def _place(pid, name, category, city):
    lat, lng = JAPAN[city]
    return PlaceCandidate(place_id=pid, name=name, category=category, lat=lat, lng=lng)


POOL = PlacePool([
    _place("t1", "Senso-ji", PickCategory.DO, "Tokyo"), _place("t2", "Tsukiji", PickCategory.EAT, "Tokyo"),
    _place("t3", "Tokyo Inn", PickCategory.STAY, "Tokyo"),
    _place("k1", "Fushimi Inari", PickCategory.DO, "Kyoto"), _place("k2", "Nishiki", PickCategory.EAT, "Kyoto"),
    _place("k3", "Kyoto Ryokan", PickCategory.STAY, "Kyoto"), _place("k4", "Kyoto Hostel", PickCategory.STAY, "Kyoto"),
    _place("s1", "Sapporo Beer", PickCategory.DO, "Sapporo"),  # in no city of the route
], ROUTE, "A 7-day JR Pass covers the trains")


def _stop(name, ref, when="afternoon"):
    return PlanStop(place=name, ref=ref, time_of_day=when, note="go")


def _plan():
    return RouteDraftPlan(title="t", summary="s", stay_refs=[2, 6], days=[
        PlanDay(theme="a", stops=[_stop("Senso-ji", 0, "morning"), _stop("Fushimi Inari", 3)]),
        PlanDay(theme="b", stops=[_stop("Tsukiji", 1), _stop("Senso-ji", 0)]),
        PlanDay(theme="c", stops=[_stop("Fushimi Inari", 3), _stop("Nishiki", 4)]),
    ])


def test_a_route_plan_has_every_day_each_in_its_city_and_a_stay_per_city():
    it = POOL.to_itinerary(_plan(), "Japan")
    assert [d.city for d in it.days] == ["Tokyo", "Tokyo", "Kyoto", "Kyoto"]  # day 4, skipped, stays to be filled
    assert [a.name for a in it.days[0].activities] == ["Senso-ji"]  # Fushimi Inari isn't in Tokyo
    assert [c.stay.name for c in it.route] == ["Tokyo Inn", "Kyoto Hostel"]  # the model's pick in each city
    assert [sid for sid, *_ in it.stay_slots()] == ["s1", "s2"]
    assert it.stay_for(it.days[0]).name == "Tokyo Inn" and it.stay_for(it.days[3]).name == "Kyoto Hostel"
    assert it.route_tip == "A 7-day JR Pass covers the trains"
    assert "Sapporo Beer" not in POOL.listing()


def test_a_wrong_city_stay_is_replaced_by_one_in_that_city():
    plan = _plan()
    plan.stay_refs = [6, 2]  # the Kyoto hostel for Tokyo and the other way round
    it = POOL.to_itinerary(plan, "Japan")
    assert [c.stay.name for c in it.route] == ["Tokyo Inn", "Kyoto Ryokan"]


def test_nights_follow_the_days_when_the_trip_changes_length():
    it = POOL.to_itinerary(_plan(), "Japan")
    it.days = it.days[:2]
    it.sync_route()
    assert [(c.name, c.days) for c in it.route] == [("Tokyo", 2)]  # Kyoto is no longer reached
    data = POOL.to_itinerary(_plan(), "Japan").model_dump()
    data["days"].append({"day_number": 5, "activities": []})
    assert [c.days for c in Itinerary.model_validate(data).route] == [2, 3]  # an added day stays in Kyoto


def test_the_review_flags_a_stop_in_another_city_a_missing_stay_and_a_busy_moving_day():
    it = POOL.to_itinerary(_plan(), "Japan")
    it.route[1].stay = None
    kyoto_day = it.days[2]
    kyoto_day.activities.append(it.days[0].activities[0].model_copy(update={"id": "far"}))  # Senso-ji in Kyoto
    problems = [x.problem for x in ItineraryCritiqueService.counted(it, POOL.places, days=4)]
    assert any("outside Kyoto" in p for p in problems)
    assert "This plan has no place to stay in Kyoto" in problems
    for i in range(2):
        kyoto_day.activities.append(kyoto_day.activities[0].model_copy(update={"id": f"extra{i}"}))
    problems = [x.problem for x in ItineraryCritiqueService.counted(it, POOL.places, days=4)]
    assert any("moving day to Kyoto" in p for p in problems)


def test_edits_keep_places_in_their_city():
    it = POOL.to_itinerary(_plan(), "Japan")
    from app.services.draft_service import PlanEdit
    places = {"c0": POOL.places[3], "c1": POOL.places[0], "c2": POOL.places[6]}  # Fushimi, Senso-ji, Kyoto Hostel
    DraftService._apply_edits(it, [
        PlanEdit(op="add", day_id="d1", place="c0", time_of_day="evening"),  # Kyoto place on a Tokyo day: refused
        PlanEdit(op="replace", item_id="s1", place="c2"),  # a Kyoto stay for Tokyo: refused
    ], places, {"s1", "s2"}, {"d1"}, {"d1"})
    assert [a.name for a in it.days[0].activities] == ["Senso-ji"]
    assert it.route[0].stay.name == "Tokyo Inn"


def test_a_liked_stay_goes_to_its_own_city():
    it = POOL.to_itinerary(_plan(), "Japan")
    from app.schemas.place import Pick
    it = DraftService.place_in_itinerary(it, Pick(**POOL.places[5].model_dump()))
    assert [c.stay.name for c in it.route] == ["Tokyo Inn", "Kyoto Ryokan"]


def test_the_drafting_prompt_names_the_cities_and_the_moving_day():
    from app.services.draft_service import _route_rules
    text = _route_rules(ROUTE)
    assert "Day 1-2: Tokyo" in text and "Day 3-4: Kyoto" in text
    assert "Day 3 is a moving day: Tokyo to Kyoto by bullet train (~2h 15m)" in text


def test_transitous_time_replaces_the_estimate_and_a_short_ride_replaces_a_flight(monkeypatch):
    tokyo, kyoto = ROUTE
    monkeypatch.setattr(route_service, "transit", lambda a, b: Leg(mode="high-speed train", minutes=137))
    kept = checked_leg(Leg(mode="bullet train", minutes=300, cost="~¥14,000"), tokyo, kyoto)
    assert (kept.mode, kept.minutes, kept.cost) == ("bullet train", 137, "~¥14,000")  # the model's fare stays
    flown = checked_leg(Leg(mode="flight", minutes=120, cost="~¥15,000"), tokyo, kyoto)
    assert (flown.mode, flown.minutes, flown.cost) == ("high-speed train", 137, None)  # a flight fare doesn't fit
    monkeypatch.setattr(route_service, "transit", lambda a, b: Leg(mode="train", minutes=1400))
    assert checked_leg(Leg(mode="flight", minutes=150), tokyo, kyoto).mode == "flight"  # a day on trains loses


class _Places:
    """Records the searches an edit makes."""

    def __init__(self, found):
        self.found, self.queries = found, []

    def candidates(self, destination, category, profile=None, viewport=None, query=None, personality=None,
                   limit=30):
        self.queries.append(query)
        return [p for p in self.found if p.category == category]


def _edit_queries(plan, changes, editable, add_to):
    places = _Places(POOL.places)
    DraftService(places)._edit_candidates(plan, changes, editable, add_to, TripProfile(destination="x"), None,
                                          set(), Personality())
    return places.queries


def test_an_edit_searches_near_the_place_asked_for_and_on_a_route_near_its_city():
    from app.schemas.personality import NoteChange
    lisbon = Itinerary(destination="Lisbon", days=[{"day_number": 1, "activities": []}])
    cafes = NoteChange(op="add", text="cafés", quote="cafés", category="eat", search="cafés in Alfama", target="d1")
    queries = _edit_queries(lisbon, [cafes], set(), {"d1"})
    assert "cafés in Alfama" in queries and not any("None" in (q or "") for q in queries)
    japan = POOL.to_itinerary(_plan(), "Japan")
    assert set(_edit_queries(japan, [], set(), {"d3"})) == {"near Kyoto"}


def test_a_swap_only_offers_places_in_the_day_s_city(monkeypatch):
    from app.schemas.place import PickCategory as Cat
    from app.schemas.place import SwapTarget
    from app.services import options_service
    from app.services.ranking_service import RankingService
    seen = {}
    monkeypatch.setattr(RankingService, "rank", staticmethod(lambda cat, found, *a, **k: seen.setdefault("c", found) and []))
    service = options_service.OptionsService.__new__(options_service.OptionsService)
    service.places = _Places(POOL.places)
    service.swap_options(SwapTarget(day_number=3, index=0), "Nishiki", Cat.DO.value, TripProfile(destination="x"),
                         None, set(), near="Kyoto", route=ROUTE)
    assert [c.name for c in seen["c"]] == ["Fushimi Inari"]


def test_a_shortened_trip_never_ends_in_a_city_for_0_nights():
    it = Itinerary(destination="Japan", route=[City(name="Tokyo", lat=0, lng=0), City(name="Kyoto", lat=0, lng=1)],
                   days=[{"day_number": n, "city": c, "activities": []}
                         for n, c in enumerate(["Tokyo", "Tokyo", "Kyoto"], 1)])
    assert [(c.name, c.days, c.nights) for c in it.route] == [("Tokyo", 3, 2)]
    assert [d.city for d in it.days] == ["Tokyo"] * 3


def test_a_leg_takes_the_router_s_vehicle_when_it_is_another_one(monkeypatch):
    tokyo, kyoto = ROUTE
    monkeypatch.setattr(route_service, "transit", lambda a, b: Leg(mode="bus", minutes=360))
    leg = checked_leg(Leg(mode="train", minutes=480, cost="~₫500,000"), tokyo, kyoto)
    assert (leg.mode, leg.minutes, leg.cost) == ("bus", 360, None)


def test_a_broken_router_answer_keeps_the_estimate(monkeypatch):
    class Answer:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return [{"unexpected": True}]
    monkeypatch.undo()  # the real transit(), with a fake HTTP answer
    monkeypatch.setattr(route_service.httpx, "get", lambda *a, **k: Answer())
    tokyo, kyoto = ROUTE
    assert route_service.transit(tokyo, kyoto) is None


def test_shortening_moves_the_last_lone_day_and_clears_its_stops_for_the_edit_to_refill():
    it = POOL.to_itinerary(_plan(), "Japan")
    it.days[2].activities = it.days[2].activities[:1]  # Kyoto has stops on day 3
    data = it.model_dump()
    data["days"] = data["days"][:3]  # 4 days down to 3: Kyoto keeps one day
    short = Itinerary.model_validate(data)
    assert [(c.name, c.nights) for c in short.route] == [("Tokyo", 2)]
    assert short.days[2].city == "Tokyo" and short.days[2].activities == []  # no Kyoto stops on a Tokyo day


def test_a_day_trip_needs_no_hotel():
    one = Itinerary(destination="Japan", route=[City(name="Tokyo", lat=35.69, lng=139.69)],
                    days=[{"day_number": 1, "city": "Tokyo", "activities": []}])
    problems = [x.problem for x in ItineraryCritiqueService.counted(one, POOL.places, days=1)]
    assert not any("place to stay" in p for p in problems)


def test_a_car_estimate_takes_the_train_s_name_with_its_time(monkeypatch):
    tokyo, kyoto = ROUTE
    monkeypatch.setattr(route_service, "transit", lambda a, b: Leg(mode="train", minutes=180))
    assert checked_leg(Leg(mode="car", minutes=240, cost="~$60"), tokyo, kyoto).mode == "train"
    assert route_service._kind("flight + airport bus") == "flight"


# Ticket #78: the owner's Japan trip (107ba016), as towns() found it: name, point, listed places. The library lists
# towns longest travel guide first (Tokyo first); towns() ranks them by places (Kyoto first).
OWNER_TOWNS = [
    ("Tokyo", 35.6895, 139.6917, 110), ("Hiroshima", 34.3853, 132.4553, 73), ("Kanazawa", 36.566, 136.65, 22),
    ("Kyoto", 35.0117, 135.7683, 127), ("Osaka", 34.6939, 135.5022, 23), ("Sapporo", 43.0614, 141.3544, 34),
    ("Sendai", 38.2683, 140.8694, 25), ("Nagasaki", 32.7833, 129.8667, 23), ("Yakushima", 30.3586, 130.5286, 14),
    ("Naoshima", 34.4667, 134.0, 12), ("Mount Koya", 34.2125, 135.5864, 17),
]


def _owner_library():
    return {"hubs": [{"name": n, "point": [lat, lng], "district": False} for n, lat, lng, _ in OWNER_TOWNS],
            "places": [{"category": "do", "lat": lat, "lng": lng} for _, lat, lng, k in OWNER_TOWNS for _ in range(k)]}


def _city(name):
    return next(City(name=n, lat=lat, lng=lng) for n, lat, lng, _ in OWNER_TOWNS + [("Sapporo", 43.06, 141.35, 0)]
                if n == name)


def test_the_owner_s_trip_without_the_model_or_router_goes_one_way_from_tokyo(fake_llm):
    fake_llm.errors["RouteChoice"] = RuntimeError("503")
    profile = TripProfile(destination="Japan", duration_days=14, party="solo")
    route, *_ = RouteService.plan({"kind": "country", "code": "JP"}, _owner_library(), 14, profile, Personality())
    # Was Kyoto, Hiroshima, Tokyo, Sendai, Sapporo: west, back east, then north.
    assert [c.name for c in route] == ["Tokyo", "Kyoto", "Osaka", "Hiroshima", "Nagasaki"]
    assert sum(c.days for c in route) == 14
    assert [c.leg.mode for c in route[1:]] == ["high-speed train", "train", "high-speed train", "high-speed train"]
    assert all(c.leg.cost is None for c in route[1:])  # no fares to show: the card then doesn't mention them


def test_without_the_model_a_town_that_means_doubling_back_is_left_out():
    found = towns(_owner_library())
    assert found[0]["name"] == "Kyoto" and route_service.gateway(_owner_library(), found)["name"] == "Tokyo"
    north = [t for t in found if t["name"] in ("Tokyo", "Sendai", "Sapporo")]
    assert [t["name"] for t in route_service.one_way(north, north[0], 3)] == ["Tokyo", "Sapporo", "Sendai"]
    assert [t["name"] for t in order(route_service.one_way(north, north[0], 3))] == ["Tokyo", "Sendai", "Sapporo"]


@pytest.mark.parametrize("a, b, mode, low, high", [
    ("Kyoto", "Hiroshima", "high-speed train", 95, 115),  # the bullet train: ~1h40
    ("Hiroshima", "Tokyo", "high-speed train", 220, 250),  # ~4h, not a flight
    ("Tokyo", "Sapporo", "flight", 180, 240),  # 830 km: flying is the usual way
    ("Kyoto", "Osaka", "train", 30, 30),
])
def test_rough_legs_in_japan_take_the_bullet_train_and_fly_only_far(a, b, mode, low, high):
    leg = route_service.rough_leg(_city(a), _city(b), "JP")
    assert leg.mode == mode and low <= leg.minutes <= high


def test_rough_legs_without_fast_trains_fly_sooner():
    assert route_service.rough_leg(_city("Hiroshima"), _city("Tokyo"), "VN").mode == "flight"
    assert route_service.rough_leg(_city("Kyoto"), _city("Hiroshima"), None).mode == "train or bus"


class _Reply:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self.body, self.headers = status, body or {}, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def json(self):
        return self.body


def _real_transit(monkeypatch, replies):
    calls = []
    monkeypatch.setattr(route_service, "transit", TRANSIT)
    monkeypatch.setattr(route_service.httpx, "get", lambda *a, **k: calls.append(1) or replies.pop(0))
    return calls


TRANSIT = route_service.transit
TRIP = {"itineraries": [{"duration": 8340, "legs": [
    {"mode": "WALK", "duration": 840}, {"mode": "REGIONAL_RAIL", "duration": 300},
    {"mode": "HIGHSPEED_RAIL", "duration": 5760}, {"mode": "WALK", "duration": 360}, {"mode": "BUS", "duration": 720}]}]}


def test_transitous_gives_the_time_on_board_and_is_asked_once_per_pair(monkeypatch):
    calls = _real_transit(monkeypatch, [_Reply(200, TRIP)])
    kyoto, hiroshima = _city("Kyoto"), _city("Hiroshima")
    first = route_service.transit(kyoto, hiroshima)
    assert (first.mode, first.minutes) == ("high-speed train", 113)  # 2h19 door to door, but 1h53 on board
    assert route_service.transit(kyoto, hiroshima) == first and len(calls) == 1  # the next draft reads the cache


def test_transitous_too_many_requests_pauses_every_call(monkeypatch):
    calls = _real_transit(monkeypatch, [_Reply(429, headers={"Retry-After": "120"}), _Reply(200, TRIP)])
    assert route_service.transit(_city("Kyoto"), _city("Hiroshima")) is None
    assert route_service.transit(_city("Tokyo"), _city("Kyoto")) is None and len(calls) == 1  # paused, not asked
    monkeypatch.setattr(route_service, "_paused_until", 0.0)  # two minutes later
    assert route_service.transit(_city("Kyoto"), _city("Hiroshima")).minutes == 113  # the 429 wasn't kept


def test_the_model_s_picks_also_go_one_way(monkeypatch):
    # The model picked Sapporo too: a flight back north after Hiroshima. It gives way to the next town on the line.
    picks = ["Tokyo", "Kyoto", "Hiroshima", "Sapporo"]
    monkeypatch.setattr(RouteService, "_ask", staticmethod(lambda *a: (picks, {}, None)))
    route, *_ = RouteService.plan({"kind": "country", "code": "JP"}, _owner_library(), 12,
                                 TripProfile(destination="Japan"), Personality())
    assert [c.name for c in route] == ["Tokyo", "Kyoto", "Osaka", "Hiroshima"]
