from pydantic import BaseModel, model_validator

from app.schemas.dayplan import DayPlan
from app.schemas.place import Pick
from app.services.open_data import distance_km


CITY_REACH_KM = 40.0  # a city's days use places this close: Nara from Kyoto, Sintra from Lisbon
STAY_ID = "s1"  # a one-city plan's stay; a route's cities have s1, s2, ... in route order


class Leg(BaseModel):
    """Getting to a city from the one before it. All rough estimates, shown as such."""
    mode: str
    minutes: int
    cost: str | None = None  # "~¥14,000", in the traveler's words for money there
    note: str | None = None  # "covered by the JR Pass"


class Flight(Leg):
    """Ticket #81: the trip's first and last leg, between home and the trip. Kept for booking links. A flight may
    land at an airport a ride away from the first city (or leave from one near the last): `transfer` is that ride,
    and lat/lng is the airport's town."""
    frm: str
    to: str
    lat: float | None = None
    lng: float | None = None
    transfer: Leg | None = None


class City(BaseModel):
    """One base on a multi-city trip."""
    name: str
    lat: float
    lng: float
    days: int = 0
    nights: int = 0  # the days there, one fewer in the last city: the trip ends on its last day
    stay: Pick | None = None
    leg: Leg | None = None  # how to get here; None for the first city
    unmet: str | None = None  # a stated wish about the kind of place this base doesn't fit (Ticket #87)


def city_of(route: list[City], thing) -> str | None:
    """The route city a place (anything with lat and lng) belongs to: the nearest one in reach."""
    if thing is None or thing.lat is None or thing.lng is None or not route:
        return None
    km, name = min((distance_km((c.lat, c.lng), (thing.lat, thing.lng)), c.name) for c in route)
    return name if km <= CITY_REACH_KM else None


class Itinerary(BaseModel):
    destination: str
    title: str | None = None
    stay: Pick | None = None  # one-city plans only; a route keeps a stay per city
    route: list[City] = []
    route_tip: str | None = None  # e.g. a rail pass that covers the legs
    fly_in: Flight | None = None  # home to the first city of the route
    fly_out: Flight | None = None  # the last city back home
    days: list[DayPlan]

    @model_validator(mode="before")
    @classmethod
    def _ids_for_older_plans(cls, data):
        """Plans saved before ids existed get the same ids on every read until they are next saved."""
        if not isinstance(data, dict):
            return data
        days = []
        for day in data.get("days") or []:
            if isinstance(day, dict):
                day = {**day, "activities": [
                    {**a, "id": f"x{day.get('day_number')}_{i}"} if isinstance(a, dict) and "id" not in a else a
                    for i, a in enumerate(day.get("activities") or [])]}
            days.append(day)
        return {**data, "days": days}

    @model_validator(mode="after")
    def _sync(self):
        self.sync_route()
        return self

    def sync_route(self) -> None:
        """Every day belongs to a city, so the days always add up to the trip, and the nights to one fewer."""
        if not self.route:
            return
        names = [c.name for c in self.route]
        for day in self.days:
            if day.city not in names:
                day.city = names[-1]  # a day added later stays on in the last city
        last = [d for d in self.days if d.city == names[-1]]
        if len(names) > 1 and len(last) == 1:  # one day there would be a hotel for 0 nights: stay where you were
            last[0].city, last[0].activities = names[-2], []  # its stops were in the city it no longer visits
        for city in self.route:
            city.days = city.nights = sum(d.city == city.name for d in self.days)
        if self.days:  # a shortened trip drops the cities it no longer reaches
            self.route = [c for c in self.route if c.days]
        if self.route:
            self.route[-1].nights = max(0, self.route[-1].days - 1)

    def stay_slots(self) -> list[tuple[str, Pick | None, str | None]]:
        """(id, stay, city) for each place to sleep."""
        if self.route:
            return [(f"s{i}", c.stay, c.name) for i, c in enumerate(self.route, 1)]
        return [(STAY_ID, self.stay, None)]

    def stay_id(self, day: DayPlan | None) -> str:
        if self.route and day is not None:
            return next((sid for sid, _, city in self.stay_slots() if city == day.city), STAY_ID)
        return STAY_ID

    def stay_for(self, day: DayPlan | None) -> Pick | None:
        return self.get_stay(self.stay_id(day))

    def get_stay(self, stay_id: str) -> Pick | None:
        return next((stay for sid, stay, _ in self.stay_slots() if sid == stay_id), None)

    def set_stay(self, stay_id: str, pick: Pick) -> None:
        if not self.route:
            self.stay = pick
            return
        for sid, _, city in self.stay_slots():
            if sid == stay_id:
                next(c for c in self.route if c.name == city).stay = pick

    def moving_day(self, day: DayPlan) -> City | None:
        """The city a day arrives in, if it is the first day there after another city."""
        for city in self.route[1:]:
            first = next((d for d in self.days if d.city == city.name), None)
            if first is day and city.leg:
                return city
        return None

    def light_day(self, day: DayPlan) -> str | None:
        """"arrival" or "departure" for the first and last day of a route flown into and out of."""
        if self.fly_in and self.days and day is self.days[0]:
            return "arrival"
        if self.fly_out and len(self.days) > 1 and day is self.days[-1]:
            return "departure"
        return None

    def target_ids(self) -> list[str]:
        """Everything a note or an edit may point at."""
        return [*(sid for sid, _, _ in self.stay_slots()), *(d.id for d in self.days),
                *(a.id for d in self.days for a in d.activities)]
