"""Ticket #67: plans shaped like a person who knows the place would plan them."""
from app.schemas.activity import Activity
from app.schemas.dayplan import DayPlan
from app.schemas.itinerary import City, Itinerary
from app.schemas.personality import Personality, Scale
from app.schemas.place import PickCategory, PlaceCandidate
from app.schemas.trip_profile import TripProfile
from app.services import day_rhythm, wikivoyage
from app.services.draft_service import DraftService, MustSeeCheck, PlacePool, PlanDay, PlanStop, DraftPlan
from app.services.itinerary_critique_service import ItineraryCritiqueService
from app.services.llm_client import LLMClient


def stop(name, category="do", when="morning", **kw):
    kw.setdefault("minutes", 30)
    return Activity(name=name, category=category, time_of_day=when, place_id=kw.pop("place_id", name), **kw)


def plan(*days, route=()):
    return Itinerary(destination="Japan", route=list(route),
                     days=[DayPlan(day_number=i, theme=t, activities=list(a), city=c)
                           for i, (t, a, c) in enumerate(days, 1)])


def problems(it, **kw):
    return [x.problem for x in ItineraryCritiqueService.counted(it, [], **kw)]


# ---------- repeats ----------

def test_the_same_dish_twice_in_a_day_or_too_often_in_a_trip_is_flagged():
    it = plan(("Osaka", [stop("Mizuno", "eat", "afternoon", description="Okonomiyaki at the counter"),
                         stop("Kuromon", "do", "afternoon"),
                         stop("Chibo", "eat", "evening", description="okonomiyaki"), stop("Dotonbori", "do", "evening")],
               None),
              ("Hiroshima", [stop("Micchan", "eat", "afternoon", description="okonomiyaki"),
                             stop("Peace Park", "do", "afternoon")], None),
              ("Hiroshima", [stop("Okonomimura", "eat", "afternoon", description="Okonomiyaki floors"),
                             stop("Castle", "do", "afternoon")], None))
    found = day_rhythm.repeats(it, {}, "")
    assert [(d.day_number, [a.name for a in s], why) for d, s, why in found] == [
        (1, ["Chibo"], "okonomiyaki twice on day 1"),
        (3, ["Okonomimura"], "okonomiyaki more than 2 times in the trip")]
    assert "The plan has okonomiyaki twice on day 1" in problems(it)


def test_a_dish_they_asked_for_may_repeat_and_two_shrines_a_day_are_fine():
    it = plan(("Ramen", [stop("Ichiran", "eat", "afternoon", description="ramen"), stop("Fuunji", "eat", "evening",
                                                                                    description="Tsukemen ramen")],
               None))
    assert day_rhythm.repeats(it, {}, "ramen every day") == []
    shrines = plan(("Kyoto", [stop("Heian Shrine"), stop("Yasaka Shrine", when="afternoon")], None))
    assert day_rhythm.repeats(shrines, {}, "") == []
    museums = plan(("Ueno", [stop("Tokyo National Museum"), stop("Science Museum"),
                             stop("Western Art Museum", when="afternoon")], None))
    [(_, [third], why)] = day_rhythm.repeats(museums, {}, "")
    assert third.name == "Western Art Museum" and why == "more than 2 museum visits on day 1"


# ---------- titles ----------

def test_a_title_that_promises_what_the_day_lacks_is_replaced_by_code():
    known = {"Pura Gunung Lebah": PlaceCandidate(place_id="Pura Gunung Lebah", name="Pura Gunung Lebah",
                                                 category=PickCategory.DO, address="Campuhan, Ubud")}
    day = DayPlan(day_number=9, theme="Swimming Beach Day",
                  activities=[stop("Pura Gunung Lebah"), stop("Warung Enak", "eat", "afternoon")])
    assert day_rhythm.promise_broken(day, known)
    assert day_rhythm.honest_theme(day, known) == "Pura Gunung Lebah and Campuhan"
    fine = DayPlan(day_number=2, theme="Temples of Ubud", activities=[stop("Pura Gunung Lebah")])
    assert not day_rhythm.promise_broken(fine, known)


# ---------- day shape ----------

def test_a_full_day_needs_the_paces_stops_but_rest_and_light_days_do_not():
    it = plan(("Kyoto", [stop("Kinkaku-ji", when="afternoon"), stop("Lunch", "eat", "afternoon")], None),
              ("Rest day", [stop("Onsen"), stop("Lunch", "eat", "afternoon")], None))
    assert problems(it, days=2, fewest=4) == ["Day 1 has only 2 stops"]
    assert problems(it, days=2, fewest=2) == []


def test_a_rest_day_holds_two_calm_places_besides_meals():
    it = plan(("Rest day", [stop("Spa", place_id="spa"), stop("Garden", place_id="garden"),
                            stop("Lunch", "eat", "afternoon", place_id="lunch"),
                            stop("Club", "do", "evening", place_id="club")], None))
    assert problems(it) == ["Day 1 is a rest day with 3 things to do", "Day 1 is a rest day, but Club isn't a place to rest"]
    museum = plan(("Rest day", [stop("Osaka Science Museum"), stop("Lunch", "eat", "afternoon")], None))
    assert problems(museum) == ["Day 1 is a rest day, but Osaka Science Museum isn't a place to rest"]
    calm = plan(("Rest day", [stop("Beach"), stop("Lunch", "eat", "afternoon")], None))
    assert problems(calm) == []
    bars = plan(("Rest day", [stop("Whisky bar", "eat", "afternoon"), stop("Café", "eat", "afternoon")], None))
    assert problems(bars) == ["Day 1 is a rest day with only meals and drinks"]


def test_a_sunrise_tip_is_an_early_start_not_an_evening_stop():
    assert Activity(name="Fushimi Inari", category="do", tip="Visit at sunrise to beat the crowds").tip_kind == "early"
    assert Activity(name="Gion", category="do", tip="Best just after sunset").tip_kind == "sunset"


def test_rest_days_follow_the_pace_and_skip_light_days():
    assert day_rhythm.roles(10, [], (True, True), pace=1) == {3: "rest", 7: "rest"}
    assert day_rhythm.roles(7, [], (False, False), pace=3) == {4: "rest"}
    assert day_rhythm.roles(6, [], (False, False), pace=3) == {}
    assert day_rhythm.roles(14, [], (False, False), pace=4) == {}
    assert day_rhythm.pace_step(Personality(scales={"pace": Scale(value=-1, confidence=0.9)})) == 1
    assert day_rhythm.pace_step(None) == 3


def test_a_day_trip_goes_from_a_city_with_three_nights_to_its_most_famous_nearby_town():
    kyoto, osaka = City(name="Kyoto", lat=35.01, lng=135.77, days=4, nights=4), \
        City(name="Osaka", lat=34.69, lng=135.50, days=2, nights=1)
    todai = PlaceCandidate(place_id="t", name="Tōdai-ji", category=PickCategory.DO, town="Nara", lat=34.689,
                           lng=135.84, fame=44)
    kinkaku = PlaceCandidate(place_id="k", name="Kinkaku-ji", category=PickCategory.DO, town="Kyoto", lat=35.039,
                             lng=135.729, fame=58)
    trips = day_rhythm.day_trips([kyoto, osaka], {"Kyoto": [kinkaku, todai], "Osaka": [todai, kinkaku]},
                                 {"Kyoto": 4, "Osaka": 3})
    assert trips == {"Kyoto": "Nara"}  # never "Kyoto" from Osaka: the route stays there; and Nara only once
    roles = day_rhythm.roles(6, [kyoto, osaka], (False, False), pace=3, trips=trips)
    assert roles == {3: "trip:Nara"}  # Kyoto's second full day; day 5 is the move to Osaka


# ---------- open data ----------

def test_sentence_fragments_and_town_names_are_not_stops_and_tips_come_from_the_guide():
    page = wikivoyage.parse("Amed", """== Do ==
* {{do | name=in front of Amed Cafe | content=Snorkel off the beach.}}
* {{do | name=small wooden shipwreck | content=A wreck.}}
* {{see | name=Jemeluk Bay | wikidata=Q123 | content=Coral gardens. Arrive early, before the dive boats.}}
* {{eat | name=Sushi Kaji | content=Tiny counter. Reservations essential, a week ahead.}}
""")
    assert [(i["name"], i["wikidata"], i["tip"]) for i in page["items"]] == [
        ("Jemeluk Bay", "Q123", "Arrive early, before the dive boats."),
        ("Sushi Kaji", None, "Reservations essential, a week ahead.")]


def test_fame_counts_wikipedias_only(monkeypatch):
    monkeypatch.setattr(wikivoyage, "wikimedia", lambda host, **p: {"entities": {"Q1": {"sitelinks": {
        "enwiki": {}, "jawiki": {}, "frwiki": {}, "enwikivoyage": {}, "commonswiki": {}, "enwikiquote": {}}}}})
    assert wikivoyage.fame(["Q1", "nonsense"]) == {"Q1": 3}


# ---------- must-sees and tips in a draft ----------

def place(pid, name, category=PickCategory.DO, **kw):
    return PlaceCandidate(place_id=pid, name=name, category=category, **kw)


def test_the_must_see_check_drops_odd_picks_adds_tips_and_swaps_only_on_a_strong_wish_for_quiet(monkeypatch):
    pool = PlacePool([place("f", "Fushimi Inari Taisha", fame=39), place("s", "Tokyo Dome", fame=30),
                      place("k", "Kiyomizu-dera", fame=40), place("q", "Kōdai-ji", fame=14)], must={"f", "s", "k"})
    answers = []
    monkeypatch.setattr(LLMClient, "generate_structured", staticmethod(
        lambda prompt, schema, **kw: answers.append(prompt) or schema(sights=[
            MustSeeCheck(ref=0, keep=True, tip="Start before 8 am"), MustSeeCheck(ref=1, keep=False),
            MustSeeCheck(ref=2, keep=True, instead=3)])))
    quiet = Personality(scales={"crowds": Scale(value=-0.9, confidence=0.8)})
    DraftService._check_must_sees(pool, TripProfile(destination="Kyoto"), quiet)
    assert pool.must == {"f", "q"} and pool.instead == {"q": "Kiyomizu-dera"}
    assert [p.name for p in pool.places] == ["Fushimi Inari Taisha", "Tokyo Dome", "Kōdai-ji"]
    assert pool.places[0].tip == "Start before 8 am" and "quieter alternative" in answers[0]


def test_a_stop_follows_its_tip_and_carries_it_to_the_traveler():
    pool = PlacePool([place("f", "Fushimi Inari Taisha", tip="Arrive early, before the tour groups."),
                      place("m", "Nishiki Market"), place("g", "Gion", tip="Best just after sunset."),
                      place("k", "Kōdai-ji")])
    pool.instead = {"k": "Kiyomizu-dera"}
    draft = DraftPlan(title="t", summary="s", days=[PlanDay(theme="Kyoto", stops=[
        PlanStop(place="Nishiki Market", ref=1, time_of_day="morning", note="Graze"),
        PlanStop(place="Fushimi Inari Taisha", ref=0, time_of_day="afternoon", note="Gates"),
        PlanStop(place="Gion", ref=2, time_of_day="afternoon", note="Lanes"),
        PlanStop(place="Kōdai-ji", ref=3, time_of_day="afternoon", note="Garden")])])
    [day] = pool.to_itinerary(draft, "Kyoto").days
    assert [(a.name, a.time_of_day, a.tip_kind) for a in day.activities] == [
        ("Fushimi Inari Taisha", "morning", "early"), ("Nishiki Market", "morning", None),
        ("Kōdai-ji", "afternoon", None), ("Gion", "evening", "sunset")]
    assert day.activities[2].instead_of == "Kiyomizu-dera"


def test_rest_and_day_trip_days_are_named_by_code_and_a_day_trips_town_is_allowed_that_day():
    kyoto = City(name="Kyoto", lat=35.01, lng=135.77, days=3, nights=2)
    nara = place("t", "Tōdai-ji", town="Nara", lat=34.689, lng=135.84)
    pool = PlacePool([place("k", "Kinkaku-ji", town="Kyoto", lat=35.039, lng=135.729), nara], route=[kyoto],
                     trips={"Kyoto": "Nara"})
    pool.roles = {2: "trip:Nara", 3: "rest"}
    draft = DraftPlan(title="t", summary="s", days=[
        PlanDay(theme="Gold", stops=[PlanStop(place="Kinkaku-ji", ref=0, time_of_day="morning", note="x"),
                                     PlanStop(place="Tōdai-ji", ref=1, time_of_day="afternoon", note="x")]),
        PlanDay(theme="Deer", stops=[PlanStop(place="Tōdai-ji", ref=1, time_of_day="morning", note="x"),
                                     PlanStop(place="Kinkaku-ji", ref=0, time_of_day="afternoon", note="x")]),
        PlanDay(theme="Lazy", stops=[PlanStop(place="Kinkaku-ji", ref=0, time_of_day="morning", note="x"),
                                     PlanStop(place="Kinkaku-ji", ref=0, time_of_day="morning", note="x")])])
    days = pool.to_itinerary(draft, "Japan").days
    assert [(d.theme, [a.name for a in d.activities]) for d in days] == [
        ("Gold", ["Kinkaku-ji"]), ("Day trip to Nara", ["Tōdai-ji"]), ("Rest day", [])]
    assert "Tōdai-ji" in pool.listing().split("Nara, a day trip from Kyoto:")[1]
