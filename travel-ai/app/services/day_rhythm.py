"""What a human planner does with a day (Ticket #67): stops by pace, labelled rest days, day trips to famous towns
nearby, no dish twice in a day, and titles that only promise what the day has. Rules only, no model calls."""
import re

from app.schemas.activity import Activity
from app.schemas.dayplan import DayPlan
from app.schemas.itinerary import City, Itinerary
from app.schemas.personality import Personality
from app.schemas.place import PlaceCandidate
from app.services.open_data import distance_km
from app.services.personality_service import KNOWN

REST_THEME = "Rest day"
# Pace step (1 slow .. 5 packed): (fewest stops on a full day, a rest day every N days or None).
PACE = {1: (3, 3.5), 2: (3, 5), 3: (4, 7), 4: (4, None), 5: (5, None)}
REST_STOPS = 2  # calm places on a rest day, besides lunch and dinner
# Where a rest day may go: places to linger. The critic's calm rating proved too noisy for this (live, 2026-11-25:
# a science museum rated calm, a snorkelling bay lively), so the kind of place decides.
# Name and type only: a guide's description mentions gardens at castles and museums too.
LINGER = re.compile(r"\b(beach(es)?|bay|cove|coral|reef|snorkel\w*|spa|massage|onsen|sent[oō]|baths?|hot springs?|"
                    r"health resort|wellness|yoga|gardens?|park|grove|lake|pond|river|waterfront|promenade|island|"
                    r"caf[eé]|tea ?house|pool|meadow|forest)\b", re.I)
REST_SEARCH = "spa garden park beach onsen lake"
DAY_TRIP_NIGHTS = 3  # a city needs this many nights before one of its days goes elsewhere
DAY_TRIP_KM = 15  # a must-see this far from the city's centre is in another town: Nara from Kyoto
SIGHTS_OF_A_KIND = 2  # two shrines a day is Kyoto; three museums is a slog

# The first match names the dish or kind of place, from its name, type and notes.
DISHES = [(k, re.compile(p, re.I)) for k, p in [
    ("sushi", r"\b(sushi|sashimi|omakase)\b"), ("ramen", r"\bramen\b"), ("udon", r"\budon\b"), ("soba", r"\bsoba\b"),
    ("okonomiyaki", r"\bokonomiyaki\b"), ("takoyaki", r"\btakoyaki\b"), ("tempura", r"\btempura\b"),
    ("yakitori", r"\byakitori\b"), ("yakiniku", r"\b(yakiniku|wagyu|sukiyaki|shabu)\b"), ("kaiseki", r"\bkaiseki\b"),
    ("curry", r"\bcurry\b"), ("pho", r"\bpho\b"), ("banh mi", r"\bb[aá]nh m[iì]\b"),
    ("noodles", r"\bnoodles?\b"), ("dumplings", r"\b(dumplings?|dim sum|gyoza)\b"), ("pizza", r"\bpizz(a|eria)\b"),
    ("pasta", r"\bpasta\b"), ("burgers", r"\bburgers?\b"), ("tacos", r"\btac(o|os|queria)\b"),
    ("tapas", r"\b(tapas|petiscos)\b"), ("seafood", r"\b(seafood|oysters?|fish market)\b"),
    ("steak", r"\b(steak|churrasc\w*|grill house)\b"), ("barbecue", r"\b(bbq|barbecue)\b"),
    ("thai", r"\bthai\b"), ("indian", r"\b(indian|tandoor\w*)\b"), ("mexican", r"\bmexican\b"),
    ("vegan", r"\bvegan\b"), ("gelato", r"\b(gelato|gelateria)\b"),
    ("bakery", r"\b(bakery|pastel(aria|ería)?|patisserie|p[aâ]tisserie)\b"), ("brunch", r"\bbrunch\b"),
]]
SIGHTS = [(k, re.compile(p, re.I)) for k, p in [
    ("museum", r"\b(museum|gallery|exhibition)\b"), ("shrine", r"\b(shrine|jinja|taisha|jing[uū])\b"),
    ("temple", r"\b(temple|pagoda|pura|wat|-ji|monastery)\b|\w+-ji\b"),
    ("church", r"\b(church|cathedral|basilica|chapel)\b"), ("castle", r"\b(castle|fort(ress)?)\b"),
    ("palace", r"\bpalace\b"), ("garden", r"\b(garden|park)\b"), ("beach", r"\bbeach\b"),
    ("market", r"\bmarket\b"), ("view", r"\b(tower|observatory|viewpoint|lookout|observation deck)\b"),
]]
# A day title's promise, and what some stop must be for it to hold.
PROMISES = [(re.compile(p, re.I), re.compile(q, re.I)) for p, q in [
    (r"\b(beach|swim\w*|snorkel\w*)", r"beach|swim|snorkel|coast|bay|cove|reef|dive|diving"),
    (r"\b(club\w*|nightlife|party|parties|bar hop\w*|pub crawl)", r"club|bar|pub|lounge|karaoke|nightlife|brewery|live"),
    (r"\b(temples?|shrines?)\b", r"temple|shrine|-ji\b|jinja|taisha|jing[uū]|pura|wat\b|pagoda|monastery"),
    (r"\b(markets?)\b", r"market|ichiba|yokoch[oō]|bazaar|souk"),
    (r"\b(hik\w*|treks?|trekking|trails?)\b", r"hike|hiking|trail|trek|walk|mount|peak|ridge|valley|summit"),
    (r"\b(spa|onsen|wellness|massage)\b", r"spa|onsen|bath|massage|wellness|sento|hammam"),
    (r"\b(museums?|galler\w*)\b", r"museum|gallery|exhibition"),
    (r"\b(castles?)\b", r"castle|fort"), (r"\b(gardens?|parks?)\b", r"garden|park|-en\b|grove"),
    (r"\b(sunset|sunrise)\b", r"sunset|sunrise|view|lookout|beach|tower|deck"),
    (r"\b(shopping|shops?)\b", r"shop|market|mall|store|arcade|boutique|street"),
]]


def pace_step(personality: Personality | None) -> int:
    """1 slow .. 5 packed, from what the traveler stated; 3 when it isn't known."""
    pace = personality.scales.get("pace") if personality else None
    return max(1, min(5, int((pace.value + 1) * 2 + 0.5) + 1)) if pace and pace.confidence >= KNOWN else 3


def min_stops(personality: Personality | None) -> int:
    return PACE[pace_step(personality)][0]


def _light(number: int, days: int, cities: list[str], ends: tuple[bool, bool]) -> bool:
    """Arrival, departure and moving days are light already."""
    flown_in, flown_out = ends
    moving = 1 < number <= len(cities) and cities[number - 1] != cities[number - 2]
    return moving or (flown_in and number == 1) or (flown_out and number == days)


def roles(days: int, route: list[City], ends: tuple[bool, bool], pace: int,
          trips: dict[str, str] | None = None) -> dict[int, str]:
    """Day number -> "rest", or "trip:<town>" for a day trip from that day's city. Light days keep their rules."""
    cities = [c.name for c in route for _ in range(c.days)] or [""] * days
    free = [n for n in range(1, days + 1) if not _light(n, days, cities, ends) and 1 < n < days]
    out: dict[int, str] = {}
    for city, town in (trips or {}).items():
        here = [n for n in free if cities[n - 1] == city]
        if len(here) >= 2:  # never the city's only full day
            out[here[1] if len(here) > 2 else here[-1]] = f"trip:{town}"
    every = PACE[pace][1]
    wanted = int(days // every) if every and days >= 4 else 0
    open_days = [n for n in free if n not in out]
    for i in range(wanted):
        target = (i + 1) * days / (wanted + 1)  # spread out: day 5 of 10 for one, days 3 and 7 for two
        if open_days:
            day = min(open_days, key=lambda n: (abs(n - target), -n))  # a tie goes to the later day
            out[day] = "rest"
            open_days.remove(day)
    return out


def day_trips(route: list[City], must_sees: dict[str, list[PlaceCandidate]], nights: dict[str, int]
              ) -> dict[str, str]:
    """City -> the town a day trip goes to: the most famous town of its must-sees beyond the city itself,
    for cities with enough nights (Nara from 3 nights in Kyoto)."""
    trips = {}
    stops = {c.name for c in route}  # a town the route already stays in is no day trip
    for city in route:
        if nights.get(city.name, 0) < DAY_TRIP_NIGHTS or city.lat is None:
            continue
        fame: dict[str, int] = {}
        for place in must_sees.get(city.name, []):
            if place.town and place.lat is not None and place.town not in stops and \
                    distance_km((city.lat, city.lng), (place.lat, place.lng)) >= DAY_TRIP_KM:
                fame[place.town] = fame.get(place.town, 0) + place.fame
        fame = {town: f for town, f in fame.items() if town not in trips.values()}  # Nara once, not from two cities
        if fame:
            trips[city.name] = max(fame, key=fame.get)
    return trips


def text_of(stop: Activity, place: PlaceCandidate | None) -> str:
    return " ".join(filter(None, [stop.name, stop.description, place and place.type_label, place and place.summary]))


def lingers(stop: Activity, place: PlaceCandidate | None) -> bool:
    """A place to spend a rest day at: a beach, spa, garden, park or café, by its name or type."""
    return bool(LINGER.search(" ".join(filter(None, [stop.name, place and place.type_label]))))


def kind_of(stop: Activity, place: PlaceCandidate | None) -> str | None:
    """The dish (for a meal) or the kind of sight, or None when nothing says."""
    text = text_of(stop, place)
    return next((k for k, pattern in (DISHES if stop.category == "eat" else SIGHTS) if pattern.search(text)), None)


def repeats(plan: Itinerary, known: dict[str, PlaceCandidate], wants: str, days: set[str] | None = None
            ) -> list[tuple[DayPlan, list[Activity], str]]:
    """(day, the stops too many, why): a dish twice a day or too often in the trip, three sights of a kind a day.
    Anything the traveler asked for ("ramen every day") may repeat."""
    asked = wants.lower()
    trip_cap = max(2, len(plan.days) // 4)
    eaten: dict[str, int] = {}
    found = []
    for day in plan.days:
        today: dict[str, int] = {}
        bad: dict[str, list[Activity]] = {}
        for stop in day.activities:
            kind = kind_of(stop, known.get(stop.place_id))
            if not kind or re.search(rf"\b{re.escape(kind)}\b", asked):
                continue
            if stop.category == "eat":
                if today.get(f"eat:{kind}"):
                    bad.setdefault(f"{kind} twice on day {day.day_number}", []).append(stop)
                elif eaten.get(kind, 0) >= trip_cap:
                    bad.setdefault(f"{kind} more than {trip_cap} times in the trip", []).append(stop)
                else:
                    eaten[kind] = eaten.get(kind, 0) + 1
            elif today.get(kind, 0) >= SIGHTS_OF_A_KIND:
                bad.setdefault(f"more than {SIGHTS_OF_A_KIND} {kind} visits on day {day.day_number}", []).append(stop)
            key = f"eat:{kind}" if stop.category == "eat" else kind
            today[key] = today.get(key, 0) + 1
        if days is None or day.id in days:
            found += [(day, stops, why) for why, stops in bad.items()]
    return found


def promise_broken(day: DayPlan, known: dict[str, PlaceCandidate]) -> bool:
    """The day's title promises a kind of place (a beach, a club) that none of its stops is."""
    if not day.theme or not day.activities:
        return False
    text = " ".join(text_of(a, known.get(a.place_id)) for a in day.activities)
    return any(promise.search(day.theme) and not keeps.search(text) for promise, keeps in PROMISES)


def honest_theme(day: DayPlan, known: dict[str, PlaceCandidate]) -> str:
    """"Fushimi Inari Taisha and Fushimi": the day's main sight and its area, which is always true."""
    main = next((a for a in day.activities if a.category != "eat"), day.activities[0])
    place = known.get(main.place_id)
    area = place and place.address and place.address.split(",")[0].strip()
    return f"{main.name} and {area}" if area and area.lower() not in main.name.lower() and \
        area.lower() != (day.city or "").lower() else main.name
