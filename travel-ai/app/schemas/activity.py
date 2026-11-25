import re
import uuid

from pydantic import BaseModel, Field, model_validator

# Minutes at a stop when nobody estimated it: plans saved before durations, or the critic failed.
DEFAULT_MINUTES = {"eat": 75}
OTHER_MINUTES = 90


def default_minutes(category: str) -> int:
    return DEFAULT_MINUTES.get(category, OTHER_MINUTES)


_BOOK = re.compile(r"\b(reserv\w*|book\w*|sell(s)? out)\b", re.I)
_SUN = re.compile(r"\b(sunset|dusk)\b", re.I)
_EARLY = re.compile(r"\b(early|sunrise|dawn|before \d|crowds?|queues?|opening time|first thing)\b", re.I)


def tip_kind(tip: str | None) -> str | None:
    """book, sunset, early or time: what the tip asks of the traveler, for its mark and where the stop goes."""
    if not tip:
        return None
    return next((kind for kind, pattern in (("book", _BOOK), ("sunset", _SUN), ("early", _EARLY))
                 if pattern.search(tip)), "time")


def new_item_id() -> str:
    return f"i{uuid.uuid4().hex[:6]}"


class Activity(BaseModel):
    # The slot's id: swapping or moving keeps it, so the notes pointing at it stay attached.
    id: str = Field(default_factory=new_item_id)
    name: str
    category: str
    time_of_day: str | None = None
    description: str | None = None
    place_id: str | None = None
    photo_name: str | None = None
    maps_url: str | None = None
    # Where it is, for travel times. None on plans saved before this.
    lat: float | None = None
    lng: float | None = None
    source_url: str | None = None
    # The traveler's words this stop fulfils, e.g. "one big night out for my birthday".
    for_note: str | None = None
    # How long the stop takes: the critic's estimate, else a default for its category.
    minutes: int | None = None
    # The critic's ratings (Ticket #83): 0 calm, 1 lively, 2 nightlife; per person 0 free to 3 pricey. None: not rated.
    energy: int | None = None
    cost: int | None = None
    # Ticket #67: one practical tip (the guide's or the must-see check's), and the must-see this stop stands in for.
    tip: str | None = None
    tip_kind: str | None = None
    instead_of: str | None = None

    @model_validator(mode="after")
    def _default_minutes(self):
        if self.minutes is None:
            self.minutes = default_minutes(self.category)
        if self.tip and not self.tip_kind:
            self.tip_kind = tip_kind(self.tip)
        return self
