import logging
import re
import unicodedata
import uuid
from collections.abc import Iterator
from typing import Literal

from pydantic import BaseModel, Field, create_model

from app.core.config import settings
from app.schemas.activity import Activity, default_minutes
from app.schemas.dayplan import DayPlan
from app.schemas.draft import Draft
from app.schemas.itinerary import City, Itinerary, city_of
from app.schemas.itinerary_critique import Issue, ItineraryCritique
from app.schemas.personality import Note, NoteChange, Personality
from app.schemas.place import Pick, PickCategory, PlaceCandidate
from app.schemas.trip_profile import TripProfile
from app.services.itinerary_critique_service import (MOVING_DAY_STOPS, REVIEW_TIMEOUT, ItineraryCritiqueService,
                                                      plan_facts)
from app.services.llm_client import LLMClient
from app.services.personality_service import PersonalityService
from app.services.places_service import FACTS_RULE, PlacesService, tier_rank, traveler_fit, unclaimed
from app.schemas.turn import RouteChange
from app.services.route_service import RouteService, checked_leg, find_town, insert_at, rough_leg

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 4
MAX_DAYS = 21  # three weeks; a longer trip is planned as its first three weeks
MAX_STOPS = 5  # after edits: a thing to do in each time of day, and lunch and dinner beside two of them
MIN_PLACES = 2
# How many of each kind the drafting model sees: enough to plan from, few enough for a free-tier prompt.
POOL = {PickCategory.DO: 18, PickCategory.EAT: 14, PickCategory.HIDDEN_GEMS: 8, PickCategory.STAY: 8}  # places to visit or eat at: one day's minimum
# On a route, per city: (base, per night) of each kind.
CITY_POOL = {PickCategory.DO: (4, 2), PickCategory.EAT: (2, 2), PickCategory.HIDDEN_GEMS: (2, 0), PickCategory.STAY: (4, 0)}

FLAVORS = [
    ("easy", "Slow and easy: a relaxed pace built around the traveler's main mood, long meals, few must-dos"),
    ("classic", "The highlights: the best-known sights and experiences, efficiently grouped"),
    ("local", "Off the beaten path: lesser-known listed places and neighbourhood food, away from the famous sights"),
]


class NotEnoughPlacesError(RuntimeError):
    pass


class NoUsableDraftError(RuntimeError):
    """Every draft was lost. The cause is the model's last error, or None if all its plans were rejected."""


class PlanStop(BaseModel):
    # Naming the place first keeps the note about that place; the name is checked against ref.
    place: str = Field(description="the exact name of the place, copied from the list")
    ref: int = Field(description="the [n] number of that same place")
    time_of_day: Literal["morning", "afternoon", "evening"]
    note: str = Field(description="what to do there, max 12 words")
    for_moment: int | None = Field(default=None, description="N of the [mN] moment this stop fulfils, or null")


class PlanDay(BaseModel):
    theme: str = Field(description="2-4 word theme for the day")
    # 4, not MAX_STOPS: Gemini refuses a longer list nested in days ("too many states"); an empty slot is free time.
    stops: list[PlanStop] = Field(min_length=2, max_length=4)


class DraftPlan(BaseModel):
    title: str = Field(description="catchy 3-6 word name for this version of the trip")
    summary: str = Field(description="one sentence on what this version feels like")
    stay_ref: int | None = Field(default=None, description="[n] of a place to stay from the list, or null")
    trait: str | None = Field(default=None, description="the listed personality trait this version serves most, or null")
    # No max_length: Gemini refuses a day cap over 10 with nested stops ("too many states"); code keeps the
    # requested number of days.
    days: list[PlanDay] = Field(min_length=1)


class RouteDraftPlan(DraftPlan):
    stay_refs: list[int] = Field(default_factory=list,
                                 description="the [n] of one 'stay' place for each city, in route order")


class PlanEdit(BaseModel):
    op: Literal["replace", "remove", "add", "move"] = Field(
        description="move also covers changing only an item's time of day")
    item_id: str | None = Field(default=None, description="the item changed; null for add")
    day_id: str | None = Field(default=None, description="for add and move: the day it goes to")
    place: str | None = Field(default=None, description="for replace and add: the [cN] id of the new place")
    time_of_day: Literal["morning", "afternoon", "evening"] | None = None
    note: str | None = Field(default=None, description="what to do at the new place, max 12 words")


_SLOTS = ["morning", "afternoon", "evening"]


def _norm(name: str) -> str:
    """Letters and digits only, accents dropped, any script: "Jerónimos Monastery" == "jeronimos monastery"."""
    plain = "".join(ch for ch in unicodedata.normalize("NFKD", name.casefold()) if not unicodedata.combining(ch))
    return re.sub(r"[\W_]", "", plain)


def _same(a: str, b: str) -> bool:
    # Containment allows "Pantheon" for "National Pantheon"; the length floor stops "Bar" matching everything.
    return bool(a and b) and (a == b or (min(len(a), len(b)) >= 6 and (a in b or b in a)))


def _hours(minutes: int) -> str:
    return f"~{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"~{minutes} min"


def _route_rules(route: list[City]) -> str:
    """The fixed route, for the drafting prompt: which days are in which city, and the moving days."""
    if not route:
        return ""
    lines, start = ["The route is fixed. Plan each day only with places listed under that day's city:"], 1
    for before, city in zip([None, *route], route):
        end = start + city.days - 1
        lines.append(f"- Day {start}{f'-{end}' if end > start else ''}: {city.name}")
        if before and city.leg:
            lines.append(f"  Day {start} is a moving day: {before.name} to {city.name} by {city.leg.mode} "
                         f"({_hours(city.leg.minutes)}). Plan only 1-{MOVING_DAY_STOPS - 1} stops in {city.name}"
                         + (", none in the morning." if city.leg.minutes >= 120 else "."))
        start = end + 1
    return "\n".join(lines) + "\n"


def _in_order(activities: list[Activity]) -> list[Activity]:
    """Morning → evening. A time of day may hold several stops; the timing check says when it's too many."""
    return sorted(activities, key=lambda a: _SLOTS.index(a.time_of_day) if a.time_of_day in _SLOTS else len(_SLOTS))


def describe(ref: str, p: PlaceCandidate) -> str:
    """A place as the model sees it: only what the data says."""
    bits = [f"{ref} {p.name}", p.category.value, p.type_label, p.address and f"area: {p.address}"]
    if p.category == PickCategory.STAY:
        bits.append(f"est. {p.tier} stay" if p.tier else "price unknown")
    bits.append(p.summary)
    return " · ".join(b for b in bits if b)


class PlacePool:
    """Numbered real places the model may use. It can only reference places by number."""

    def __init__(self, places: list[PlaceCandidate], route: list[City] = (), route_tip: str | None = None):
        seen: set[str] = set()
        self.route, self.route_tip = list(route), route_tip
        self.places: list[PlaceCandidate] = []
        for p in places:
            # On a route every place belongs to one city, so each city's days stay inside it.
            if p.place_id not in seen and (not self.route or city_of(self.route, p)):
                seen.add(p.place_id)
                self.places.append(p)

    def listing(self) -> str:
        """One line per place, grouped by city and area so nearby places can share a day."""
        def key(i: int) -> tuple:
            p = self.places[i]
            city = city_of(self.route, p) if self.route else ""
            return [c.name for c in self.route].index(city) if city else 0, (p.address or "").split(",")[0]
        order = sorted(range(len(self.places)), key=key)
        lines, heading = [], None
        for i in order:
            if self.route and (city := city_of(self.route, self.places[i])) != heading:
                heading = city
                lines.append(f"{city}:")
            lines.append(describe(f"[{i}]", self.places[i]))
        return "\n".join(lines)

    def get(self, ref: int | None) -> PlaceCandidate | None:
        return self.places[ref] if ref is not None and 0 <= ref < len(self.places) else None

    def resolve(self, stop: PlanStop) -> PlaceCandidate | None:
        """The place the stop's note is about: its ref if the names agree, else the place it names, else None."""
        named = _norm(stop.place)
        place = self.get(stop.ref)
        if place and _same(named, _norm(place.name)):
            return place
        return next((p for p in self.places if _same(named, _norm(p.name))), None)

    def to_itinerary(self, plan: DraftPlan, destination: str, moments: list[str] = ()) -> Itinerary:
        if self.route:
            return self._to_route_itinerary(plan, destination, moments)
        days = []
        used: set[str] = set()
        for day in plan.days:
            activities = self._activities(day, used, moments)
            if activities:
                days.append(DayPlan(day_number=len(days) + 1, theme=day.theme, activities=_in_order(activities)))

        stay = self.get(plan.stay_ref)
        return Itinerary(
            destination=destination,
            title=plan.title,
            stay=Pick(**stay.model_dump()) if stay and stay.category == PickCategory.STAY else None,
            days=days,
        )

    def _to_route_itinerary(self, plan: DraftPlan, destination: str, moments: list[str]) -> Itinerary:
        """Every day of the route, each in its own city: a stop in another city is dropped, a day the model
        skipped stays empty for the review to fill, and each city gets a stay (the model's, else its first)."""
        cities = [c.name for c in self.route for _ in range(c.days)]
        used: set[str] = set()
        days = []
        for number, city in enumerate(cities, 1):
            day = plan.days[number - 1] if number <= len(plan.days) else None
            activities = self._activities(day, used, moments, city) if day else []
            days.append(DayPlan(day_number=number, theme=day and day.theme, city=city,
                                activities=_in_order(activities)))
        route = [c.model_copy(deep=True) for c in self.route]
        refs = getattr(plan, "stay_refs", [])
        for i, city in enumerate(route):
            here = [p for p in self.places if p.category == PickCategory.STAY and city_of(self.route, p) == city.name]
            chosen = self.get(refs[i]) if i < len(refs) else None
            stay = chosen if chosen in here else next(iter(here), None)
            city.stay = Pick(**stay.model_dump()) if stay else None
        return Itinerary(destination=destination, title=plan.title, route=route, route_tip=self.route_tip, days=days)

    def _activities(self, day: PlanDay, used: set[str], moments: list[str], city: str | None = None) -> list[Activity]:
        activities = []
        for stop in day.stops:
            place = self.resolve(stop)
            # Unknown refs and stays are dropped; repeats of the same place too, and places in another city.
            if place is None or place.category == PickCategory.STAY or place.place_id in used \
                    or (city and city_of(self.route, place) != city):
                continue
            used.add(place.place_id)
            activities.append(Activity(
                name=place.name,
                category=place.category.value,
                time_of_day=stop.time_of_day,
                description=unclaimed(stop.note, place.summary),
                for_note=moments[stop.for_moment] if stop.for_moment is not None
                and 0 <= stop.for_moment < len(moments) else None,
                place_id=place.place_id,
                photo_name=place.photo_name,
                maps_url=place.maps_url,
                lat=place.lat,
                lng=place.lng,
                source_url=place.source_url,
            ))
        return activities


def _present(plan: Itinerary) -> set[str]:
    """Ids of the stops and the stays the plan has."""
    return {a.id for d in plan.days for a in d.activities} | {sid for sid, stay, _ in plan.stay_slots() if stay}


def fulfilled(itinerary: Itinerary) -> set[str]:
    return {a.for_note for d in itinerary.days for a in d.activities if a.for_note}


def _changed(before: Itinerary, after: Itinerary) -> set[str]:
    """Ids of the stops, days and stays that differ, including what is gone."""
    def parts(plan: Itinerary) -> dict:
        items = {a.id: (d.id, a) for d in plan.days for a in d.activities}
        return {**{sid: stay for sid, stay, _ in plan.stay_slots()}, **{d.id: d.activities for d in plan.days},
                **items}
    was, now = parts(before), parts(after)
    return {key for key in was.keys() | now.keys() if was.get(key) != now.get(key)}


UNREVIEWED = "I couldn't double-check this plan."
CITY_DAYS = 2  # a city added without a number of days: one night, and the day after


def _span(days: list[DayPlan]) -> str:
    first, last = days[0].day_number, days[-1].day_number
    return f"day {first}" if first == last else f"days {first}–{last}"


class RouteEdit(BaseModel):
    """What a route edit did, for the reply: every sentence is about the saved plan."""
    plan: Itinerary
    said: list[str] = []
    warnings: list[str] = []
    checked: bool = True
    day_ids: dict[str, str] = {}  # old day id -> new day id, for the notes on days that moved


class DraftService:

    def __init__(self, places: PlacesService):
        self.places = places

    def route(self, profile: TripProfile, viewport: dict | None, personality: Personality | None,
              days: int) -> tuple[list[City], str | None]:
        """The cities of a country or region trip and a tip that covers the legs; ([], None) for one place."""
        area = self.places.area_for(profile.destination, viewport)
        if not area:
            return [], None
        return RouteService.plan(area, self.places.library(area), days, profile, personality)

    def pool(self, profile: TripProfile, viewport: dict | None, exclude_ids: set[str],
             personality: Personality | None = None, route: list[City] = (),
             route_tip: str | None = None) -> PlacePool:
        """Real candidates inside the destination, one search per category (cached). On a route, per city."""
        searches = [(None, category, limit) for category, limit in POOL.items()] if not route else [
            (city.name, category, base + per_night * city.days)
            for city in route for category, (base, per_night) in CITY_POOL.items()]
        places: list[PlaceCandidate] = []
        for city, category, limit in searches:
            found = self.places.candidates(profile.destination, category, profile, viewport,
                                           query=city and f"near {city}", personality=personality, limit=limit)
            if category == PickCategory.STAY and profile.budget_level:
                limit = tier_rank(profile.budget_level.value)
                found = [c for c in found if tier_rank(c.tier) <= limit] or found
            places += [c for c in found if c.place_id not in exclude_ids]
        return PlacePool(places, route, route_tip)

    @staticmethod
    def _prompt(profile: TripProfile, pool: PlacePool, days: int, brief: str,
                personality: Personality | None = None) -> str:
        allowed = PersonalityService.traits(personality)[0]  # only known traits; guesses stay off the cards
        return f"""
Plan a {days}-day trip to {profile.destination}.
This version's style: {brief}
{_route_rules(pool.route)}
Trip profile:
{profile.model_dump_json(exclude_none=True, exclude={"preferences"})}

Places you may use (refer to them ONLY by [n]):
{pool.listing()}

Rules:
- Exactly {days} days, each with 2-3 things to do or see and places to eat beside them.
- A time of day may hold several stops when they are close by. Lunch is afternoon, dinner is evening.
- Plan meals in pairs: lunch with a thing to do in the same afternoon, dinner with one in the same evening.
  Leave out a meal rather than give it a time of day alone.
- A meal takes an hour or two, so it never has a morning, afternoon or evening to itself: it shares that time
  of day with a thing to do or see (in the evening a bar, a show or a walk counts).
- Each stop's place and note must be about the same listed place. Don't plan anything that isn't on the list.
- Group places from the same area on the same day, and keep each day to one or two nearby areas.
  Don't repeat a place.
- A note says only what the place's line supports; with no description, say what kind of place it is.
{FACTS_RULE}
- Respect diet, mobility, kids and budget preferences.
{traveler_fit(profile, personality)}
{f"- trait: copy the one personality trait above that this version serves best (exactly as written), or null. Options: {', '.join(allowed)}." if allowed else "- trait: null."}
- Fit each [mN] moment into one stop on a fitting day and set that stop's for_moment to N. Skip a moment only if
  no listed place fits it.
{"- stay_refs: one 'stay' place for each city, in route order. stay_ref is null." if pool.route else
   "- stay_ref must be a 'stay' place from the list, or null."}
"""

    def generate(self, profile: TripProfile, viewport: dict | None, exclude_ids: set[str],
                 personality: Personality | None = None) -> Iterator[Draft]:
        """Yields 3 drafts with different flavors, one at a time (each is a separate model call).
        A country or region trip first gets its route (one more call), the same for all three. Each round asks
        again, so new wishes can change the cities."""
        days = min(profile.duration_days or DEFAULT_DAYS, MAX_DAYS)
        pool = self.pool(profile, viewport, exclude_ids, personality, *self.route(profile, viewport, personality, days))
        if sum(p.category != PickCategory.STAY for p in pool.places) < MIN_PLACES:
            raise NotEnoughPlacesError(f"Too few places in {profile.destination}")
        allowed = PersonalityService.traits(personality)[0]  # only known traits; guesses stay off the cards
        moments = PersonalityService.moments(personality)
        made, failure = False, None
        for flavor, brief in FLAVORS:
            try:
                plan = LLMClient.generate_structured(self._prompt(profile, pool, days, brief, personality),
                                                     RouteDraftPlan if pool.route else DraftPlan,
                                                     model=settings.LLM_MODEL_PLAN)
            except Exception as e:
                logger.exception("Draft %s failed", flavor)
                failure = e
                continue
            itinerary = pool.to_itinerary(plan, profile.destination, moments)
            summary = unclaimed(plan.summary, *(a.description for d in itinerary.days for a in d.activities)) or ""
            if not itinerary.days:
                logger.warning("Draft %s referenced no valid places", flavor)
                continue
            made = True
            first_photo = next((a.photo_name for d in itinerary.days for a in d.activities if a.photo_name), None)
            yield Draft(
                id=uuid.uuid4().hex[:12],
                flavor=flavor,
                title=plan.title,
                summary=summary,
                photo_name=first_photo,
                trait=PersonalityService.match_trait(plan.trait, allowed),
                unplanned=[m for m in moments if m not in fulfilled(itinerary)],
                itinerary=itinerary,
            )
        if not made:
            raise NoUsableDraftError(f"No draft for {profile.destination}") from failure

    def edit(self, itinerary: Itinerary, changes: list[NoteChange], request: str, profile: TripProfile,
             viewport: dict | None, exclude_ids: set[str],
             personality: Personality) -> tuple[Itinerary, list[str], bool]:
        """Change only what the changes point at. The model returns operations; code copies everything else.
        Returns the plan, what the review couldn't fix, and whether the review worked."""
        plan = itinerary.model_copy(deep=True)
        before = len(plan.days)
        wanted = min(profile.duration_days or before, MAX_DAYS)
        plan.days = [d for d in plan.days if d.day_number <= wanted]
        new_days = [DayPlan(day_number=n, activities=[]) for n in range(before + 1, wanted + 1)]
        plan.days += new_days
        plan.sync_route()  # added days stay on in the last city; a shorter trip may end sooner
        empty = [d for d in plan.days if not d.activities]  # new days, and one a shorter route moved to another city

        targets = {c.target for c in changes}
        whole = not changes or "trip" in targets
        day_of = {a.id: d for d in plan.days for a in d.activities}
        scope_days = {d.id for d in plan.days if whole or d.id in targets or d in empty
                      or any(a.id in targets for a in d.activities)}
        editable = {a.id for d in plan.days for a in d.activities
                    if whole or a.id in targets or d.id in targets}
        editable |= {sid for sid, _, _ in plan.stay_slots() if whole or sid in targets}
        # One item may move to any day; only whole days take new stops.
        add_to = {d.id for d in plan.days if whole or d.id in targets or d in empty}
        move_to = {d.id for d in plan.days} if targets & set(day_of) else add_to

        places = self._edit_candidates(plan, changes, editable, add_to, profile, viewport, exclude_ids, personality)
        if not editable and not add_to:
            return plan, [], True
        edit = create_model(
            "PlanEdit", __base__=PlanEdit,
            item_id=(Literal[tuple(sorted(editable))] | None if editable else str | None, None),
            # Left open when nothing may move: models still fill it in, and _apply_edits checks it anyway.
            day_id=(Literal[tuple(sorted(move_to))] | None if move_to else str | None, None),
            place=(Literal[tuple(places)] | None if places else str | None, None),
        )
        schema = create_model("PlanEdits", edits=(list[edit], ...))

        asked = "\n".join(f"- at [{c.target}]: {c.text}" for c in changes) or f"- {request}"
        listing = "\n".join(describe(f"[{key}]", p) for key, p in places.items())
        prompt = f"""
You edit part of a {len(plan.days)}-day trip to {profile.destination}.

Trip profile:
{profile.model_dump_json(exclude_none=True, exclude={"preferences"})}

The part you work on, with what the traveler said under what it is about:
{PersonalityService.plan_view(plan, personality, scope_days)}

The traveler's message: {request}
Make these changes:
{asked}
{f"Fill the empty days {', '.join(d.id for d in empty)} with 2-3 stops each." if empty else ""}

Places you may use:
{listing}

Rules:
- Return the fewest edits that do what was asked. For a new trip-wide rule, change only the items that break it.
- A note on the narrower thing wins over a wider one: item over day, day over trip.
- Never choose a place that breaks a stated must or avoid; judge what the place is, not whether its name looks alike.
- An inferred note is a guess: it never removes something they asked for.
- "cheaper" means a lower estimated tier than the current place, whatever the trip's budget. Prices are unknown:
  if no listed stay has a lower estimated tier, return no edit for it.
- A note says only what the place's line supports.
{FACTS_RULE}
- A meal takes an hour or two, so it never has a morning, afternoon or evening to itself: it shares that time
  of day with a thing to do or see (in the evening a bar, a show or a walk counts).
- replace keeps the slot and its notes. move changes an item's day or time of day. Don't use stays as activities.
- If no listed place fits, return no edit for it.
"""
        result = LLMClient.generate_structured(prompt, schema, model=settings.LLM_MODEL_PLAN)
        self._apply_edits(plan, result.edits, places, editable, add_to, move_to)
        was = {d.id: d for d in itinerary.days}
        for day in plan.days:  # an edit never leaves a day with nothing to do: it keeps its old stops
            if not day.activities and (old := was.get(day.id)) and old.activities and old.city == day.city:
                day.activities = [a.model_copy() for a in old.activities]

        # Only what this edit changed is reviewed; the rest of the plan is the traveler's.
        new_ids = {d.id for d in new_days}
        scope = (_changed(itinerary, plan) & _present(plan)) | {d.id for d in empty}
        warnings, checked = [], True
        if scope:
            try:
                plan, warnings, checked = self._check_and_fix(
                    plan, list(places.values()), profile, personality, scope=scope, request=request)
            except Exception:
                logger.exception("Couldn't review the edit")
                checked = False
        plan.days = [d for d in plan.days if d.activities or d.id not in new_ids]
        plan.sync_route()
        return plan, warnings, checked

    def edit_route(self, itinerary: Itinerary, changes: list[RouteChange], request: str, profile: TripProfile,
                   viewport: dict | None, exclude_ids: set[str], personality: Personality) -> RouteEdit:
        """Swap, add or remove cities, or add days, then fill only the new days with the usual edit.
        Other cities keep their stops. All or nothing: if a new day can't be filled, the plan stays as it was."""
        area = self.places.area_for(profile.destination, viewport)
        library = self.places.library(area) if area else {"hubs": []}
        plan = itinerary.model_copy(deep=True)
        old_ids = {id(d): d.id for d in plan.days}
        by_city = {c.name: [d for d in plan.days if d.city == c.name] for c in plan.route}
        fresh: list[tuple[DayPlan, str | None]] = []  # new days, and the place each is for
        said, failed, longer, added = [], [], 0, []

        def new_days(city: str, k: int, place: str | None = None) -> list[DayPlan]:
            made = [DayPlan(day_number=0, city=city, activities=[]) for _ in range(k)]
            fresh.extend((d, place) for d in made)
            return made

        def add_city(name: str, k: int, at: int | None = None) -> City | None:
            town = find_town(library, name)
            if town is None:
                failed.append(f"I couldn't add {name}: I don't have places there.")
                return None
            if town["name"] in by_city:
                failed.append(f"{town['name']} is already on your route.")
                return None
            city = City(name=town["name"], lat=town["lat"], lng=town["lng"])
            # one day last would be 0 nights
            plan.route.insert(insert_at(plan.route, town, last=k > 1) if at is None else at, city)
            by_city[city.name] = new_days(city.name, k)
            added.append(city.name)
            return city

        def route_city(name: str | None) -> str | None:
            return next((c for c in by_city if name and c.casefold() == name.casefold().strip()), None)

        for change in changes:
            k = max(1, min(change.days or (1 if change.op in ("add_day", "swap_day") else CITY_DAYS), MAX_DAYS))
            leaving = route_city(change.leaving)
            if change.op in ("swap", "remove"):
                if leaving is None or len(by_city) < 2 and change.op == "remove":
                    failed.append(f"I couldn't remove {change.leaving or 'that city'}: "
                                  + ("it isn't on your route." if leaving is None else "it's your only city."))
                    continue
                if change.op == "swap" and route_city(change.city) is None:
                    if not (town := find_town(library, change.city or "")) or town["name"] in by_city:
                        add_city(change.city or "", 1)  # says why it can't come
                        continue
                    first = plan.route[0].name == leaving  # the new city is where the trip starts, as the old was
                    gone = by_city.pop(leaving)
                    plan.route = [c for c in plan.route if c.name != leaving]
                    add_city(town["name"], len(gone), 0 if first else None)
                    said.append(f"{town['name']} replaces {leaving}.")
                    continue
                # A removed city's days go to the city before it (the next one if it was first), to fill anew.
                names = list(by_city)
                gone = by_city.pop(leaving)
                plan.route = [c for c in plan.route if c.name != leaving]
                host = names[names.index(leaving) - 1] if names.index(leaving) else names[1]
                by_city[host] += new_days(host, len(gone))
                said.append(f"I took {leaving} off your route; its days are now in {host}.")
            elif change.op == "swap_day" and (host := route_city(change.city)
                                              or (None if change.city else list(by_city)[-1])):
                taken = by_city[host].pop()  # the city's last day: its stops give way to the place
                by_city[host] += new_days(host, 1, change.place)
                said.append(f"One of your days in {host} is now for {change.place or 'something new'}"
                            + (", in place of its old stops." if taken.activities else "."))
            elif change.op == "add" or (change.city and route_city(change.city) is None):  # a day in a new city
                if not (city := add_city(change.city or "", k)):
                    continue
                if change.op != "add":
                    fresh[-k:] = [(d, change.place) for d, _ in fresh[-k:]]
                if change.longer or change.op == "add_day":
                    longer += k
                    said.append(f"I added {city.name} for {k} {'day' if k == 1 else 'days'}.")
                    continue
                # Days come from the cities with the most, from their end; a city keeps two.
                for _ in range(k):
                    giver = max((c for c in by_city if c != city.name), key=lambda c: len(by_city[c]))
                    if len(by_city[giver]) <= CITY_DAYS:
                        longer += 1
                        continue
                    by_city[giver].pop()
                    said.append(f"{giver} gave a day to {city.name}.")
                said.append(f"I added {city.name} for {k} {'day' if k == 1 else 'days'}.")
            else:  # add_day in a route city, or the last one when none was named
                host = route_city(change.city) or list(by_city)[-1]
                by_city[host] += new_days(host, k, change.place)
                longer += k
                said.append(f"I added {k} {'day' if k == 1 else 'days'} in {host}"
                            + (f" for {change.place}." if change.place else "."))

        if not fresh:
            return RouteEdit(plan=itinerary, said=failed or ["I left your route as it is."])
        plan.days = [d for c in plan.route for d in by_city.get(c.name, [])]
        if len(plan.days) > MAX_DAYS:
            return RouteEdit(plan=itinerary, said=[f"I can't plan more than {MAX_DAYS} days, so I left your "
                                                   "route as it is."] + failed)
        for number, day in enumerate(plan.days, 1):
            day.day_number = number
        fresh = [(d, p) for d, p in fresh if d.day_number]  # a new day a later change took back isn't in the plan
        before = {c.name: p.name for p, c in zip(itinerary.route, itinerary.route[1:])}
        plan.sync_route()
        self._legs(plan, before, area)
        for city in plan.route:
            if city.name in added:
                city.stay = self._stay(plan, city.name, profile, viewport, personality)

        # The usual edit fills the new days, and only them: each is a change it must make.
        asks = [NoteChange(op="do", target=d.id, text=f"plan this day in {d.city}" + (f" around {p}" if p else ""),
                           quote=request, then="change", search=p) for d, p in fresh]
        sized = profile.model_copy(update={"duration_days": len(plan.days)})
        filled, warnings, checked = self.edit(plan, asks, request, sized, viewport, exclude_ids, personality)
        new_ids = {d.id for d, _ in fresh}
        empty = [d for d in filled.days if d.id in new_ids and not d.activities]
        if empty or len(filled.days) != len(plan.days):
            return RouteEdit(plan=itinerary, said=[
                f"I couldn't find enough to do for {_span(empty or filled.days[-1:])} in "
                f"{(empty or filled.days[-1:])[0].city}, so I left your route as it is."] + failed)
        for d, place in fresh:
            day = next(x for x in filled.days if x.id == d.id)
            if place and not any(_same(_norm(place), _norm(a.name)) for a in day.activities):
                failed.append(f"I couldn't find {place} in the places I know, so {_span([day])} has other stops.")
        route = " → ".join(c.name for c in filled.route)
        length = f", now {len(filled.days)} days" if longer else ""
        said = [f"Your route is now {route}{length}."] + said + failed
        ids = {old_ids[id(d)]: d.id for d in plan.days if id(d) in old_ids}
        return RouteEdit(plan=filled, said=said, warnings=warnings, checked=checked, day_ids=ids)

    def _legs(self, plan: Itinerary, before: dict[str, str], area: dict | None) -> None:
        """A new leg wherever a city now comes after another one than before."""
        plan.route[0].leg = None
        for prev, city in zip(plan.route, plan.route[1:]):
            if before.get(city.name) != prev.name or city.leg is None:
                city.leg = checked_leg(rough_leg(prev, city, (area or {}).get("code")), prev, city)

    def _stay(self, plan: Itinerary, city: str, profile: TripProfile, viewport: dict | None,
              personality: Personality) -> Pick | None:
        """A new city's stay: the best-ranked one there in the trip's budget, as a draft would choose."""
        found = self.places.candidates(profile.destination, PickCategory.STAY, profile, viewport,
                                       query=f"near {city}", personality=personality, limit=4)
        if profile.budget_level:
            found = [c for c in found if tier_rank(c.tier) <= tier_rank(profile.budget_level.value)] or found
        stay = next((p for p in found if city_of(plan.route, p) == city), None)
        return Pick(**stay.model_dump()) if stay else None

    def review(self, draft: Draft, places: list[PlaceCandidate], profile: TripProfile,
               personality: Personality | None = None) -> Draft:
        """The draft with its errors fixed or listed. Never raises, so a draft is never lost.
        Safe to run in a thread: it only calls the model."""
        try:
            days = min(profile.duration_days or DEFAULT_DAYS, MAX_DAYS)
            plan = draft.itinerary.model_copy(update={"days": draft.itinerary.days[:days]}, deep=True)
            plan, warnings, checked = self._check_and_fix(plan, places, profile, personality, days=days)
        except Exception:
            logger.exception("Couldn't review a draft")
            plan, warnings, checked = draft.itinerary, [], False
        moments = PersonalityService.moments(personality)
        return draft.model_copy(update={
            "itinerary": plan,
            "warnings": warnings + ([] if checked else [UNREVIEWED]),
            "photo_name": next((a.photo_name for d in plan.days for a in d.activities if a.photo_name), None),
            "unplanned": [m for m in moments if m not in fulfilled(plan)],
        })

    def _check_and_fix(self, plan: Itinerary, places: list[PlaceCandidate], profile: TripProfile,
                       personality: Personality | None, days: int | None = None, scope: set[str] | None = None,
                       request: str = "") -> tuple[Itinerary, list[str], bool]:
        """Review, at most one repair, and one re-check of what the repair changed.
        `days` is set for drafts, `scope` for an edit. Returns the plan, what is still wrong, and if the review worked."""
        try:
            critique = ItineraryCritiqueService.critique(plan, profile, personality, places, scope, request)
            checked = True
        except Exception:
            logger.exception("The critic failed")
            critique, checked = ItineraryCritique(), False

        # Missing days are added only now, so the critic judges the plan and not our count.
        plan = plan.model_copy(deep=True)
        if days:
            plan.days += [DayPlan(day_number=n, activities=[]) for n in range(len(plan.days) + 1, days + 1)]
            plan.sync_route()
        visits = dict(critique.visit_minutes)
        ratings = dict(critique.ratings)
        issues = ItineraryCritiqueService.counted(plan, places, days, scope, visits) + critique.issues

        fixed = plan
        if checked and issues:  # a model that just failed won't repair either
            try:
                edits = self._repairs(plan, issues, places, profile, personality, scope, request)
                fixed = self._repaired(plan, issues, edits, places, days, scope, visits)
                if redo := _changed(plan, fixed) & _present(fixed):
                    # A repair is the model's own guess too: check just what it changed, once. No loop: fixes can
                    # chase each other, and every round is another 3-50 s.
                    again = ItineraryCritiqueService.critique(fixed, profile, personality, places, redo, request)
                    visits |= again.visit_minutes
                    ratings |= again.ratings
                    if again.issues or len(ItineraryCritiqueService.counted(fixed, places, days, scope, visits)) > \
                            len(ItineraryCritiqueService.counted(plan, places, days, scope, visits)):
                        logger.warning("Repair undone, its re-check found: %s", [x.problem for x in again.issues])
                        fixed = plan
            except Exception:
                logger.exception("The repair or its re-check failed")
                fixed = plan

        changed = _changed(plan, fixed)
        added = {a.id for d in fixed.days for a in d.activities} - {a.id for d in plan.days for a in d.activities}
        unfixed = [x.problem for x in critique.issues if not (changed & set(x.where) if x.where else added)]
        if days and not fixed.route:  # a route keeps every day, so its nights still add up
            fixed.days = [d for d in fixed.days if d.activities]
            for number, day in enumerate(fixed.days, 1):
                day.day_number = number
        counted = [x.problem for x in ItineraryCritiqueService.counted(
            fixed, places, days, scope and scope | changed, visits)]
        for stop in (a for d in fixed.days for a in d.activities):
            stop.minutes = visits.get(stop.place_id, stop.minutes)  # the critic's estimate is what the traveler sees
            stop.energy, stop.cost = ratings.get(stop.place_id, (stop.energy, stop.cost))
        return fixed, list(dict.fromkeys(counted + unfixed)), checked

    @staticmethod
    def _repairs(plan: Itinerary, issues: list[Issue], places: list[PlaceCandidate], profile: TripProfile,
                 personality: Personality | None, scope: set[str] | None, request: str) -> list[PlanEdit]:
        found = "\n".join(f"- {x.problem} (at {', '.join(x.where) or 'the whole plan'})"
                          + (f". Suggested: {x.suggestion}" if x.suggestion else "") for x in issues)
        listing = "\n".join(
            f"[c{i}] " + " · ".join(b for b in (p.name, p.category.value, p.type_label, p.price_level
                                               or (p.tier and f"est. {p.tier} price"), p.address,
                                               p.lat is not None and f"at {p.lat:.3f},{p.lng:.3f}") if b)
            for i, p in enumerate(places))
        asked = "" if scope is None else (
            f'- The traveler just asked: "{request}". Keep what they asked for, and never remove a stop.')
        prompt = f"""
A reviewer found errors in a trip plan for {profile.destination}. Fix them before the traveler sees it.

What we know about the traveler:
{profile.model_dump_json(exclude_none=True, exclude={"preferences"})}
{traveler_fit(profile, personality)}

The plan:
{PersonalityService.plan_view(plan, personality)}
Facts about these places:
{plan_facts(plan, places)}

What the reviewer found:
{found}

Places you may use:
{listing}

Rules:
- Return the fewest edits that fix what the reviewer found. Change nothing else.
- Use only listed places, and none that the plan already has. Give each new place a note.
- For a day with too much travel or too little time, use places close by (see "at lat,lng") or quicker ones,
  remove a stop, or move one to a day or time with room.
- An empty day needs 2-3 stops added. A stay is replaced at its STAY id (s1, s2, ...), and is never an activity.
- On a trip with several cities, a day's stops and a city's stay must be in that city.
- Never choose a place that breaks a stated must or avoid.
- A meal takes an hour or two, so it never has a morning, afternoon or evening to itself: it shares that time
  of day with a thing to do or see (in the evening a bar, a show or a walk counts).
- A new place must suit its slot's time of day and who is going: no night club in the morning, no zoo for a stag party.
- If nothing listed fixes an error, return no edit for it.
{asked}
"""
        schema = create_model("PlanFixes", edits=(list[PlanEdit], ...))
        return LLMClient.generate_structured(prompt, schema, model=settings.LLM_MODEL_PLAN, timeout=REVIEW_TIMEOUT).edits

    def _repaired(self, plan: Itinerary, issues: list[Issue], edits: list[PlanEdit], places: list[PlaceCandidate],
                  days: int | None, scope: set[str] | None, visits: dict[str, int]) -> Itinerary:
        """The repair applied to just what was flagged, or the plan as it was if that makes it worse."""
        where = {w for issue in issues for w in issue.where}
        day_ids = {d.id for d in plan.days}
        editable = (where - day_ids) | {a.id for d in plan.days if d.id in where for a in d.activities}
        add_to = where & day_ids
        if scope is not None:
            editable &= scope
            edits = [e for e in edits if e.op != "remove"]  # it could undo what the traveler just asked for
        elif any(not issue.where for issue in issues):
            add_to = day_ids  # something is missing from the whole plan
        move_to = day_ids if scope is None else add_to

        used = {a.place_id for d in plan.days for a in d.activities} | {
            stay.place_id for _, stay, _ in plan.stay_slots() if stay}
        free = {f"c{i}": p for i, p in enumerate(places) if p.place_id not in used}
        # These ids aren't enums in the schema, so models sometimes copy the brackets.
        edits = [e.model_copy(update={k: v.strip("[] ") for k in ("item_id", "day_id", "place") if (v := getattr(e, k))})
                 for e in edits]
        fixed = plan.model_copy(deep=True)
        self._apply_edits(fixed, edits, free, editable, add_to, move_to)

        emptied = any(was.activities and not now.activities for was, now in zip(plan.days, fixed.days))
        def errors(p: Itinerary) -> int:
            return len(ItineraryCritiqueService.counted(p, places, days, scope, visits))
        return plan if emptied or errors(fixed) > errors(plan) else fixed

    def _edit_candidates(self, plan: Itinerary, changes: list[NoteChange], editable: set[str], add_to: set[str],
                         profile: TripProfile, viewport: dict | None, exclude_ids: set[str],
                         personality: Personality) -> dict[str, PlaceCandidate]:
        """Places for just the part being changed, as {c0: place, ...}."""
        items = [a for d in plan.days for a in d.activities if a.id in editable]
        wanted = {PickCategory(a.category) for a in items if a.category in PickCategory._value2member_map_}
        stays = {sid for sid, _, _ in plan.stay_slots()}
        if stays & editable:
            wanted.add(PickCategory.STAY)
        if add_to:
            wanted |= {PickCategory.DO, PickCategory.EAT, PickCategory.HIDDEN_GEMS}
        # On a route, search around each city being changed; a place elsewhere can't go on its days.
        cities = sorted(({d.city for d in plan.days if d.id in add_to or any(a.id in editable for a in d.activities)}
                         | {city for sid, _, city in plan.stay_slots() if sid in editable}) - {None})
        near = [f" near {city}" for city in cities] or [""]
        limit = max(8, 30 // len(near))
        # "Similar but cheaper" must not inherit the trip's "luxury" search hint.
        neutral = profile.model_copy(update={"budget_level": None})
        found: list[PlaceCandidate] = []
        named: set[str] = set()  # places the traveler asked for by name
        for change in changes:
            if not change.search:
                continue
            category = next((PickCategory(a.category) for a in items
                             if a.id == change.target and a.category in PickCategory._value2member_map_), None)
            category = (PickCategory.STAY if change.target in stays or change.category == "stay" else
                        PickCategory.EAT if change.category in ("eat", "diet") else category or PickCategory.DO)
            for where in near:
                hits = self.places.candidates(profile.destination, category, neutral, viewport,
                                              change.search + where, personality, limit=limit)
                named |= {p.place_id for p in hits if _same(_norm(change.search), _norm(p.name))}
                found += hits
        for category in [c for c in PickCategory if c in wanted]:
            for where in near:
                found += self.places.candidates(profile.destination, category, neutral, viewport,
                                                where.strip() or None, personality=personality, limit=limit)
        used = {a.place_id for d in plan.days for a in d.activities} | exclude_ids
        used |= {stay.place_id for _, stay, _ in plan.stay_slots() if stay}
        if plan.route:
            # shortcut: a place asked for by name that the guide lists in a city without coordinates (Universal
            # Studios Japan) sits at that city's centre, so travel times to it are rough; fine until guides geocode.
            centre = {c.name.casefold(): (c.lat, c.lng) for c in plan.route if c.name in cities}
            found = [p.model_copy(update=dict(zip(("lat", "lng"), centre[town])))
                     if p.lat is None and p.place_id in named and (town := (p.address or "").split(",")[-1].strip()
                                                                    .casefold()) in centre else p for p in found]
            found = [p for p in found if city_of(plan.route, p) in cities]
        fresh = list({p.place_id: p for p in found if p.place_id not in used}.values())
        return {f"c{i}": p for i, p in enumerate(fresh)}

    @staticmethod
    def _apply_edits(plan: Itinerary, edits: list[PlanEdit], places: dict[str, PlaceCandidate],
                     editable: set[str], add_to: set[str], move_to: set[str]) -> None:
        days = {d.id: d for d in plan.days}
        stays = {sid: city for sid, _, city in plan.stay_slots()}
        used: set[str] = set()
        touched: dict[str, DayPlan] = {}

        def fits(thing, city: str | None) -> bool:
            """On a route, a place can only go where its city is."""
            return not plan.route or city_of(plan.route, thing) == city

        for e in edits:
            place = places.get(e.place) if e.op in ("replace", "add") else None
            if place and place.place_id in used:
                continue
            if e.op == "replace" and e.item_id in stays and e.item_id in editable:
                if place and place.category == PickCategory.STAY and fits(place, stays[e.item_id]):
                    plan.set_stay(e.item_id, Pick(**place.model_dump(), why=unclaimed(e.note, place.summary)))
                    used.add(place.place_id)
                continue
            if e.op == "add":
                day = days.get(e.day_id) if e.day_id in add_to else None
                if day and place and place.category != PickCategory.STAY and len(day.activities) < MAX_STOPS \
                        and fits(place, day.city):
                    day.activities.append(Activity(
                        name=place.name, category=place.category.value, time_of_day=e.time_of_day,
                        description=unclaimed(e.note, place.summary), place_id=place.place_id,
                        photo_name=place.photo_name, maps_url=place.maps_url, source_url=place.source_url,
                        lat=place.lat, lng=place.lng))
                    used.add(place.place_id)
                    touched[day.id] = day
                continue
            if e.item_id not in editable:
                continue
            day, item = next(((d, a) for d in plan.days for a in d.activities if a.id == e.item_id), (None, None))
            if item is None:
                continue
            if e.op == "replace" and place and place.category != PickCategory.STAY and fits(place, day.city):
                item.name, item.category = place.name, place.category.value
                item.description = unclaimed(e.note, place.summary)
                item.place_id, item.photo_name, item.maps_url = place.place_id, place.photo_name, place.maps_url
                item.lat, item.lng = place.lat, place.lng
                item.source_url = place.source_url
                item.minutes = default_minutes(item.category)  # the old place's time doesn't carry over
                item.time_of_day = e.time_of_day or item.time_of_day
                used.add(place.place_id)
            elif e.op == "remove":
                day.activities.remove(item)
            elif e.op == "move":
                to = days.get(e.day_id) if e.day_id in move_to else None
                if to and to is not day:
                    if len(to.activities) >= MAX_STOPS or to.city != day.city:
                        continue
                    day.activities.remove(item)
                    to.activities.append(item)
                    touched[to.id] = to
                item.time_of_day = e.time_of_day or item.time_of_day
            else:
                continue
            touched[day.id] = day
        # Sort only; re-timing would change stops nobody asked about.
        for day in touched.values():
            day.activities.sort(key=lambda a: _SLOTS.index(a.time_of_day) if a.time_of_day in _SLOTS else len(_SLOTS))

    @staticmethod
    def map_moments(itinerary: Itinerary, notes: list[Note]) -> list[Note]:
        """Once a plan is chosen, each thing to fit in points at the stop that fulfils it."""
        stop = {a.for_note: a.id for d in itinerary.days for a in d.activities if a.for_note}
        return [n.model_copy(update={"target": stop[n.text]}) if n.once and n.target == "trip" and n.text in stop
                else n for n in notes]

    @staticmethod
    def place_in_itinerary(itinerary: Itinerary, pick: Pick) -> Itinerary:
        """Add a place the user tapped ＋ on: stays become the stay, others go to the lightest day.
        On a route, both go to the city the place is in; a place in none of them isn't added."""
        itinerary = itinerary.model_copy(deep=True)
        city = city_of(itinerary.route, pick)
        if itinerary.route and city is None:
            return itinerary
        if pick.category == PickCategory.STAY:
            itinerary.set_stay(next(sid for sid, _, c in itinerary.stay_slots() if c == city), pick)
            return itinerary
        if any(a.place_id == pick.place_id for d in itinerary.days for a in d.activities):
            return itinerary

        activity = Activity(name=pick.name, category=pick.category.value, time_of_day=None,
                            description=pick.why, place_id=pick.place_id, photo_name=pick.photo_name,
                            maps_url=pick.maps_url, source_url=pick.source_url,
                            lat=pick.lat, lng=pick.lng)
        if not itinerary.days:
            itinerary.days.append(DayPlan(day_number=1, theme="Your picks", activities=[activity]))
        else:
            here = [d for d in itinerary.days if not itinerary.route or d.city == city] or itinerary.days
            min(here, key=lambda d: len(d.activities)).activities.append(activity)
        return itinerary
