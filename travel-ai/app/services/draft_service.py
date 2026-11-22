import logging
import re
import unicodedata
import uuid
from collections.abc import Iterator
from typing import Literal

from pydantic import BaseModel, Field, create_model

from app.core.config import settings
from app.schemas.activity import Activity
from app.schemas.dayplan import DayPlan
from app.schemas.draft import Draft
from app.schemas.itinerary import STAY_ID, Itinerary
from app.schemas.personality import Note, NoteChange, Personality
from app.schemas.place import Pick, PickCategory, PlaceCandidate
from app.schemas.trip_profile import TripProfile
from app.services.llm_client import LLMClient
from app.services.personality_service import PersonalityService
from app.services.places_service import PlacesService, tier_rank, traveler_fit

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 4
MAX_DAYS = 10
MAX_STOPS = 4

FLAVORS = [
    ("easy", "Slow and easy: a relaxed pace built around the traveler's main mood, long meals, few must-dos"),
    ("classic", "The highlights: the best-known sights and experiences, efficiently grouped"),
    ("local", "Off the beaten path: hidden gems and local favorites, avoiding the tourist crowds"),
]


class PlanStop(BaseModel):
    # Naming the place first keeps the note about that place; the name is checked against ref.
    place: str = Field(description="the exact name of the place, copied from the list")
    ref: int = Field(description="the [n] number of that same place")
    time_of_day: Literal["morning", "afternoon", "evening"]
    note: str = Field(description="what to do there, max 12 words")
    for_moment: int | None = Field(default=None, description="N of the [mN] moment this stop fulfils, or null")


class PlanDay(BaseModel):
    theme: str = Field(description="2-4 word theme for the day")
    stops: list[PlanStop] = Field(min_length=2, max_length=4)


class DraftPlan(BaseModel):
    title: str = Field(description="catchy 3-6 word name for this version of the trip")
    summary: str = Field(description="one sentence on what this version feels like")
    stay_ref: int | None = Field(default=None, description="[n] of a place to stay from the list, or null")
    trait: str | None = Field(default=None, description="the listed personality trait this version serves most, or null")
    # The cap stops runaway generation (each extra day costs ~10 s on a local model).
    days: list[PlanDay] = Field(min_length=1, max_length=10)


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
    """Lowercase letters and digits only, accents dropped: "Jerónimos Monastery" == "jeronimos monastery"."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", plain.lower())


def _same(a: str, b: str) -> bool:
    # Containment allows "Pantheon" for "National Pantheon"; the length floor stops "Bar" matching everything.
    return bool(a and b) and (a == b or (min(len(a), len(b)) >= 6 and (a in b or b in a)))


def _one_per_slot(activities: list[Activity]) -> list[Activity]:
    """Move a repeated time of day to a free one, then order the day morning → evening."""
    taken: set[str] = set()
    for a in activities:
        if a.time_of_day in taken:
            free = [s for s in _SLOTS if s not in taken]
            if free:  # with 4 stops one slot must repeat
                later = [s for s in free if _SLOTS.index(s) > _SLOTS.index(a.time_of_day)]
                a.time_of_day = (later or free)[0]
        taken.add(a.time_of_day)
    return sorted(activities, key=lambda a: _SLOTS.index(a.time_of_day) if a.time_of_day in _SLOTS else len(_SLOTS))


class PlacePool:
    """Numbered real places the model may use. It can only reference places by number."""

    def __init__(self, places: list[PlaceCandidate]):
        seen: set[str] = set()
        self.places: list[PlaceCandidate] = []
        for p in places:
            if p.place_id not in seen:
                seen.add(p.place_id)
                self.places.append(p)

    def listing(self) -> str:
        lines = []
        for i, p in enumerate(self.places):
            bits = [f"[{i}] {p.name}", p.category.value]
            if p.type_label:
                bits.append(p.type_label)
            if p.rating:
                bits.append(f"{p.rating}★")
            if p.price_level or p.tier:
                bits.append(p.price_level or f"est. {p.tier} price")
            lines.append(" · ".join(bits))
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
        days = []
        used: set[str] = set()
        for day in plan.days:
            activities = []
            for stop in day.stops:
                place = self.resolve(stop)
                # Unknown refs and stays are dropped; repeats of the same place too.
                if place is None or place.category == PickCategory.STAY or place.place_id in used:
                    continue
                used.add(place.place_id)
                activities.append(Activity(
                    name=place.name,
                    category=place.category.value,
                    time_of_day=stop.time_of_day,
                    description=stop.note,
                    for_note=moments[stop.for_moment] if stop.for_moment is not None
                    and 0 <= stop.for_moment < len(moments) else None,
                    place_id=place.place_id,
                    photo_name=place.photo_name,
                    maps_url=place.maps_url,
                ))
            if activities:
                days.append(DayPlan(day_number=len(days) + 1, theme=day.theme, activities=_one_per_slot(activities)))

        stay = self.get(plan.stay_ref)
        return Itinerary(
            destination=destination,
            title=plan.title,
            stay=Pick(**stay.model_dump()) if stay and stay.category == PickCategory.STAY else None,
            days=days,
        )


def fulfilled(itinerary: Itinerary) -> set[str]:
    return {a.for_note for d in itinerary.days for a in d.activities if a.for_note}


class DraftService:

    def __init__(self, places: PlacesService):
        self.places = places

    def pool(self, profile: TripProfile, viewport: dict | None, exclude_ids: set[str],
             personality: Personality | None = None) -> PlacePool:
        """Real candidates inside the destination, one search per category (cached)."""
        places: list[PlaceCandidate] = []
        for category in (PickCategory.DO, PickCategory.EAT, PickCategory.HIDDEN_GEMS, PickCategory.STAY):
            found = self.places.candidates(profile.destination, category, profile, viewport, personality=personality)
            if category == PickCategory.STAY and profile.budget_level:
                limit = tier_rank(profile.budget_level.value)
                found = [c for c in found if tier_rank(c.tier) <= limit] or found
            places += [c for c in found if c.place_id not in exclude_ids]
        return PlacePool(places)

    @staticmethod
    def _prompt(profile: TripProfile, pool: PlacePool, days: int, brief: str,
                personality: Personality | None = None) -> str:
        allowed = PersonalityService.traits(personality)[0]  # only known traits; guesses stay off the cards
        return f"""
Plan a {days}-day trip to {profile.destination}.
This version's style: {brief}

Trip profile:
{profile.model_dump_json(exclude_none=True, exclude={"preferences"})}

Places you may use (refer to them ONLY by [n]):
{pool.listing()}

Rules:
- Exactly {days} days, 2-3 stops each, mixing things to do and places to eat.
- Each day uses morning, afternoon and evening at most once. Lunch is afternoon, dinner is evening.
- Each stop's place and note must be about the same listed place. Don't plan anything that isn't on the list.
- Group nearby places on the same day. Don't repeat a place.
- Respect diet, mobility, kids and budget preferences.
{traveler_fit(profile, personality)}
{f"- trait: copy the one personality trait above that this version serves best (exactly as written), or null. Options: {', '.join(allowed)}." if allowed else "- trait: null."}
- Fit each [mN] moment into one stop on a fitting day and set that stop's for_moment to N. Skip a moment only if
  no listed place fits it.
- stay_ref must be a 'stay' place from the list, or null.
"""

    def generate(self, profile: TripProfile, viewport: dict | None, exclude_ids: set[str],
                 personality: Personality | None = None) -> Iterator[Draft]:
        """Yields 3 drafts with different flavors, one at a time (each is a separate model call)."""
        pool = self.pool(profile, viewport, exclude_ids, personality)
        days = min(profile.duration_days or DEFAULT_DAYS, MAX_DAYS)
        allowed = PersonalityService.traits(personality)[0]  # only known traits; guesses stay off the cards
        moments = PersonalityService.moments(personality)
        for flavor, brief in FLAVORS:
            try:
                plan = LLMClient.generate_structured(self._prompt(profile, pool, days, brief, personality), DraftPlan,
                                                     model=settings.LLM_MODEL_PLAN)
            except Exception:
                logger.exception("Draft %s failed", flavor)
                continue
            itinerary = pool.to_itinerary(plan, profile.destination, moments)
            if not itinerary.days:
                logger.warning("Draft %s referenced no valid places", flavor)
                continue
            first_photo = next((a.photo_name for d in itinerary.days for a in d.activities if a.photo_name), None)
            yield Draft(
                id=uuid.uuid4().hex[:12],
                flavor=flavor,
                title=plan.title,
                summary=plan.summary,
                photo_name=first_photo,
                trait=PersonalityService.match_trait(plan.trait, allowed),
                unplanned=[m for m in moments if m not in fulfilled(itinerary)],
                itinerary=itinerary,
            )

    def edit(self, itinerary: Itinerary, changes: list[NoteChange], request: str, profile: TripProfile,
             viewport: dict | None, exclude_ids: set[str], personality: Personality) -> Itinerary:
        """Change only what the changes point at. The model returns operations; code copies everything else."""
        plan = itinerary.model_copy(deep=True)
        before = len(plan.days)
        wanted = min(profile.duration_days or before, MAX_DAYS)
        plan.days = [d for d in plan.days if d.day_number <= wanted]
        new_days = [DayPlan(day_number=n, activities=[]) for n in range(before + 1, wanted + 1)]
        plan.days += new_days

        targets = {c.target for c in changes}
        whole = not changes or "trip" in targets
        day_of = {a.id: d for d in plan.days for a in d.activities}
        scope_days = {d.id for d in plan.days if whole or d.id in targets or d in new_days
                      or any(a.id in targets for a in d.activities)}
        editable = {a.id for d in plan.days for a in d.activities
                    if whole or a.id in targets or d.id in targets}
        if whole or STAY_ID in targets:
            editable.add(STAY_ID)
        # One item may move to any day; only whole days take new stops.
        add_to = {d.id for d in plan.days if whole or d.id in targets or d in new_days}
        move_to = {d.id for d in plan.days} if targets & set(day_of) else add_to

        places = self._edit_candidates(plan, changes, editable, add_to, profile, viewport, exclude_ids, personality)
        if not editable and not add_to:
            return plan
        edit = create_model(
            "PlanEdit", __base__=PlanEdit,
            item_id=(Literal[tuple(sorted(editable))] | None if editable else str | None, None),
            # Left open when nothing may move: models still fill it in, and _apply_edits checks it anyway.
            day_id=(Literal[tuple(sorted(move_to))] | None if move_to else str | None, None),
            place=(Literal[tuple(places)] | None if places else str | None, None),
        )
        schema = create_model("PlanEdits", edits=(list[edit], ...))

        asked = "\n".join(f"- at [{c.target}]: {c.text}" for c in changes) or f"- {request}"
        listing = "\n".join(
            f"[{key}] {p.name} · {p.category.value} · {p.type_label or ''} · "
            f"{p.price_level or (p.tier and f'est. {p.tier} price') or 'price unknown'} · {p.summary or ''}"
            for key, p in places.items())
        prompt = f"""
You edit part of a {len(plan.days)}-day trip to {profile.destination}.

Trip profile:
{profile.model_dump_json(exclude_none=True, exclude={"preferences"})}

The part you work on, with what the traveler said under what it is about:
{PersonalityService.plan_view(plan, personality, scope_days)}

The traveler's message: {request}
Make these changes:
{asked}
{f"Fill the new days {', '.join(d.id for d in new_days)} with 2-3 stops each." if new_days else ""}

Places you may use:
{listing}

Rules:
- Return the fewest edits that do what was asked. For a new trip-wide rule, change only the items that break it.
- A note on the narrower thing wins over a wider one: item over day, day over trip.
- Never choose a place that breaks a stated must or avoid; judge what the place is, not whether its name looks alike.
- An inferred note is a guess: it never removes something they asked for.
- "cheaper" means a lower price level than the current place, whatever the trip's budget.
- replace keeps the slot and its notes. move changes an item's day or time of day. Don't use stays as activities.
- If no listed place fits, return no edit for it.
"""
        result = LLMClient.generate_structured(prompt, schema, model=settings.LLM_MODEL_PLAN)
        self._apply_edits(plan, result.edits, places, editable, add_to, move_to)
        plan.days = [d for d in plan.days if d.activities or d not in new_days]
        return plan

    def _edit_candidates(self, plan: Itinerary, changes: list[NoteChange], editable: set[str], add_to: set[str],
                         profile: TripProfile, viewport: dict | None, exclude_ids: set[str],
                         personality: Personality) -> dict[str, PlaceCandidate]:
        """Places for just the part being changed, as {c0: place, ...}."""
        items = [a for d in plan.days for a in d.activities if a.id in editable]
        wanted = {PickCategory(a.category) for a in items if a.category in PickCategory._value2member_map_}
        if STAY_ID in editable:
            wanted.add(PickCategory.STAY)
        if add_to:
            wanted |= {PickCategory.DO, PickCategory.EAT, PickCategory.HIDDEN_GEMS}
        # "Similar but cheaper" must not inherit the trip's "luxury" search hint.
        neutral = profile.model_copy(update={"budget_level": None})
        found: list[PlaceCandidate] = []
        for change in changes:
            if not change.search:
                continue
            category = next((PickCategory(a.category) for a in items
                             if a.id == change.target and a.category in PickCategory._value2member_map_), None)
            category = (PickCategory.STAY if change.target == STAY_ID or change.category == "stay" else
                        PickCategory.EAT if change.category in ("eat", "diet") else category or PickCategory.DO)
            found += self.places.candidates(profile.destination, category, neutral, viewport, change.search, personality)
        for category in [c for c in PickCategory if c in wanted]:
            found += self.places.candidates(profile.destination, category, neutral, viewport, personality=personality)
        used = {a.place_id for d in plan.days for a in d.activities} | exclude_ids
        if plan.stay:
            used.add(plan.stay.place_id)
        fresh = list({p.place_id: p for p in found if p.place_id not in used}.values())
        return {f"c{i}": p for i, p in enumerate(fresh)}

    @staticmethod
    def _apply_edits(plan: Itinerary, edits: list[PlanEdit], places: dict[str, PlaceCandidate],
                     editable: set[str], add_to: set[str], move_to: set[str]) -> None:
        days = {d.id: d for d in plan.days}
        used: set[str] = set()
        touched: dict[str, DayPlan] = {}
        for e in edits:
            place = places.get(e.place) if e.op in ("replace", "add") else None
            if place and place.place_id in used:
                continue
            if e.op == "replace" and e.item_id == STAY_ID and STAY_ID in editable:
                if place and place.category == PickCategory.STAY:
                    plan.stay = Pick(**place.model_dump(), why=e.note)
                    used.add(place.place_id)
                continue
            if e.op == "add":
                day = days.get(e.day_id) if e.day_id in add_to else None
                if day and place and place.category != PickCategory.STAY and len(day.activities) < MAX_STOPS:
                    day.activities.append(Activity(
                        name=place.name, category=place.category.value, time_of_day=e.time_of_day, description=e.note,
                        place_id=place.place_id, photo_name=place.photo_name, maps_url=place.maps_url))
                    used.add(place.place_id)
                    touched[day.id] = day
                continue
            if e.item_id not in editable:
                continue
            day, item = next(((d, a) for d in plan.days for a in d.activities if a.id == e.item_id), (None, None))
            if item is None:
                continue
            if e.op == "replace" and place and place.category != PickCategory.STAY:
                item.name, item.category, item.description = place.name, place.category.value, e.note
                item.place_id, item.photo_name, item.maps_url = place.place_id, place.photo_name, place.maps_url
                item.time_of_day = e.time_of_day or item.time_of_day
                used.add(place.place_id)
            elif e.op == "remove":
                day.activities.remove(item)
            elif e.op == "move":
                to = days.get(e.day_id) if e.day_id in move_to else None
                if to and to is not day:
                    if len(to.activities) >= MAX_STOPS:
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
        """Add a place the user tapped ＋ on: stays become the stay, others go to the lightest day."""
        itinerary = itinerary.model_copy(deep=True)
        if pick.category == PickCategory.STAY:
            itinerary.stay = pick
            return itinerary
        if any(a.place_id == pick.place_id for d in itinerary.days for a in d.activities):
            return itinerary

        activity = Activity(name=pick.name, category=pick.category.value, time_of_day=None,
                            description=pick.why, place_id=pick.place_id, photo_name=pick.photo_name,
                            maps_url=pick.maps_url)
        if not itinerary.days:
            itinerary.days.append(DayPlan(day_number=1, theme="Your picks", activities=[activity]))
        else:
            min(itinerary.days, key=lambda d: len(d.activities)).activities.append(activity)
        return itinerary
