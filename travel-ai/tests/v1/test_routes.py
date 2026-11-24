"""Ticket #27: multi-city routes. Order, nights, pace, a stay per city, days inside their city."""
from app.schemas.itinerary import City, Itinerary, Leg
from app.schemas.personality import Personality, Scale
from app.schemas.place import PickCategory, PlaceCandidate
from app.schemas.trip_profile import TripProfile
from app.services.draft_service import DraftService, PlacePool, PlanDay, PlanStop, RouteDraftPlan
from app.services.itinerary_critique_service import ItineraryCritiqueService
from app.services.route_service import RouteService, city_count, order, share_nights, towns

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


def _pace(value):
    return Personality(scales={"pace": Scale(value=value, confidence=0.8, source="note")})


def test_the_route_goes_one_way_without_zig_zag():
    ranked = [_town(n) for n in ("Tokyo", "Kyoto", "Hiroshima", "Kanazawa", "Osaka")]
    assert [c["name"] for c in order(ranked)] == ["Tokyo", "Kanazawa", "Kyoto", "Osaka", "Hiroshima"]


def test_nights_add_up_to_the_trip_and_extra_nights_go_to_the_main_cities():
    ordered = [_town(n) for n in ("Tokyo", "Kanazawa", "Kyoto", "Osaka", "Hiroshima")]
    nights = share_nights(14, ordered, ["Tokyo", "Kyoto", "Hiroshima", "Kanazawa", "Osaka"])
    assert sum(nights) == 14
    assert nights == [3, 3, 3, 3, 2] or nights[0] >= nights[-1]
    assert dict(zip([c["name"] for c in ordered], nights))["Tokyo"] == 3


def test_pace_changes_how_many_cities_and_so_the_nights_in_each():
    assert city_count(12, _pace(-1)) == 3  # slow: about 4 nights a city
    assert city_count(12, None) == 4  # steady: about 3
    assert city_count(12, _pace(1)) == 6  # packed: about 2
    assert city_count(3, _pace(1)) == 1  # never a city a night


def test_towns_are_real_towns_with_enough_to_do_best_known_first():
    found = [t["name"] for t in towns(_library())]
    assert "Shinjuku" not in found  # a district is part of Tokyo, not a base
    assert found[0] == "Tokyo"


def test_the_plan_falls_back_to_the_map_when_the_model_fails(fake_llm):
    fake_llm.errors["RouteChoice"] = RuntimeError("quota")
    route, tip = RouteService.plan({"kind": "country"}, _library(), 10, TripProfile(destination="Japan"), _pace(0))
    assert sum(c.nights for c in route) == 10
    assert route[0].leg is None and all(c.leg and c.leg.minutes > 0 for c in route[1:])
    assert tip is None


def test_a_city_trip_has_no_route():
    assert RouteService.plan({"kind": "city"}, _library(), 10, TripProfile(destination="Tokyo"), None) == ([], None)


ROUTE = [City(name="Tokyo", lat=35.69, lng=139.69, nights=2),
         City(name="Kyoto", lat=35.01, lng=135.77, nights=2,
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
    assert [(c.name, c.nights) for c in it.route] == [("Tokyo", 2)]  # Kyoto is no longer reached
    data = POOL.to_itinerary(_plan(), "Japan").model_dump()
    data["days"].append({"day_number": 5, "activities": []})
    assert [c.nights for c in Itinerary.model_validate(data).route] == [2, 3]  # an added day stays in Kyoto


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
