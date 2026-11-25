"""What a plan is like on the six scales and the $ to $$$$$ badge, next to what the traveler asked (Ticket #83).

Rules only, so the same plan always reads the same and it works with Gemini down. Energy and cost per stop come
from the critic's ratings (Activity.energy/cost) when it gave them, else from the words below. Every reading is a
step from 1 (left pole) to 5 (right pole); the wish is the same step from the stated scales, never from the plan.
"""
import re

from app.schemas.activity import Activity
from app.schemas.draft import PlanScales, ScaleReading
from app.schemas.itinerary import Itinerary
from app.schemas.personality import Personality
from app.services.itinerary_critique_service import NIGHT_OUT
from app.services.personality_service import KNOWN

CARD = ("pace", "experiences", "energy", "crowds", "food", "comfort")
# Words for the draft summary line: (left, right) pole.
SHORT = {"pace": ("Slow", "Packed"), "experiences": ("Hotel time", "Out exploring"), "energy": ("Chill", "Nightlife"),
         "crowds": ("Hidden gems", "Icons"), "food": ("Food as fuel", "Foodie"), "comfort": ("Budget stays", "Luxe stays")}
STATED_FOR_CLOSEST = 3  # the "Closest to what you asked" tag needs this many stated scales
_TIER = {"budget": -0.8, "mid": 0.0, "luxury": 0.8}
_TIER_WORD = {"budget": "Budget", "mid": "Mid-range", "luxury": "Luxury"}
# shortcut: keyword guesses for stops the critic hasn't rated (it failed, or a swap); fine while the critic rates most.
_PAID = re.compile(r"\b(spa|massage|hammam|onsen|tasting|tour|cruise|boat|show|concert|theat(re|er)|opera|"
                   r"theme park|class|workshop|safari|ski|diving|scuba|balloon|cable car|museum|gallery|palace)\b", re.I)
_FINE = re.compile(r"\b(fine dining|michelin|tasting menu|omakase|gourmet)\b", re.I)
_EXPERIENCE = re.compile(r"\b(tour|cruise|boat|class|workshop|tasting|show|concert|safari|hike|kayak|surf|"
                         r"diving|scuba|balloon|ride|festival|market)\b", re.I)


def _clamp(v: float) -> float:
    return max(-1.0, min(1.0, v))


def step(v: float) -> int:
    """-1..1 to 1..5; halves round up (not Python's banker's rounding)."""
    return max(1, min(5, int((v + 1) * 2 + 0.5) + 1))


def _words(stop: Activity) -> str:
    return f"{stop.name} {stop.description or ''}"


def energy(stop: Activity) -> int:
    if stop.energy is not None:
        return stop.energy
    return 2 if stop.time_of_day == "evening" and NIGHT_OUT.search(_words(stop)) else 1 if stop.time_of_day == "evening" else 0


def cost(stop: Activity) -> int:
    if stop.cost is not None:
        return stop.cost
    if stop.category == "eat":
        return 3 if _FINE.search(_words(stop)) else 1
    return 2 if _PAID.search(_words(stop)) else 0


def _icon(stop: Activity) -> float:
    # shortcut: no popularity data in open sources; a travel-guide listing with a photo reads as famous, a place only
    # on the map as a hidden gem. Use real popularity if a source for it is ever added (PRODUCT.md).
    pid = stop.place_id or ""
    if pid.startswith("wv-"):
        return 1.0 if stop.photo_name else 0.0
    return -1.0 if pid.startswith("ov-") else 0.0


def measure(plan: Itinerary) -> tuple[dict[str, float | None], int, str]:
    """(-1..1 per card scale, None when the plan says nothing), the $ level 1-5, and why."""
    days = [d for d in plan.days if d.activities]
    stops = [a for d in days for a in d.activities]
    if not stops:
        return {n: None for n in CARD}, 0, ""
    hours = sum(a.minutes or 0 for a in stops) / 60 / len(days)
    per_day = len(stops) / len(days)
    sights = [a for a in stops if a.category != "eat"]
    unique = sum(1 for a in sights if a.cost and a.cost >= 2 or _EXPERIENCE.search(_words(a))) / len(days)
    tiers = [_TIER[s.tier] for _, s, _ in plan.stay_slots() if s and s.tier in _TIER]
    scales = {
        # 3 hours a day reads slow, 9 packed; 2 stops slow, 4 packed.
        "pace": (_clamp((hours - 6) / 3) + _clamp(per_day - 3)) / 2,
        # The rest of a 12-hour day is time at the hotel; one unique experience a day reads as out exploring.
        "experiences": (_clamp((hours - 6) / 3) + _clamp(unique * 2 - 1)) / 2,
        "energy": sum({0: -1, 1: 0, 2: 1}[max(energy(a) for a in d.activities)] for d in days) / len(days),
        "crowds": sum(map(_icon, sights)) / len(sights) if sights else None,
        # A quarter of the stops being meals is ordinary; none is fuel, nearly half is the point of the trip.
        "food": _clamp((sum(a.category == "eat" for a in stops) / len(stops) - 0.25) / 0.2),
        "comfort": sum(tiers) / len(tiers) if tiers else None,
    }

    stay = plan.stay.tier if plan.stay else next((s.tier for _, s, _ in plan.stay_slots() if s and s.tier), None)
    paid = sum(1 for a in sights if cost(a) >= 2)
    fine = sum(1 for a in stops if a.category == "eat" and cost(a) >= 3)
    spend = 1 + 4 * sum(map(cost, stops)) / len(stops) / 3  # 1-5 from the stops' average cost
    parts = [spend] + ([{"budget": 1, "mid": 3, "luxury": 5}[stay]] if stay in _TIER else [])
    level = max(1, min(5, int(sum(parts) / len(parts) + 0.5)))
    why = [f"{_TIER_WORD[stay]} stay" if stay in _TIER else "Stay price unknown",
           f"{paid} paid {'activity' if paid == 1 else 'activities'}"] + ([f"{fine} fine dining"] if fine else [])
    return scales, level, ", ".join(why)


def wishes(personality: Personality) -> tuple[dict[str, int | None], int | None]:
    """Only what the traveler stated (notes, profile, swipes): a guess or a plan never makes a ghost."""
    def known(name: str) -> int | None:
        s = personality.scales.get(name)
        return step(s.value) if s and s.confidence >= KNOWN else None
    return {n: known(n) for n in CARD}, known("money")


def read(plan: Itinerary | None, personality: Personality) -> PlanScales:
    wish, money = wishes(personality)
    if plan is None:
        return PlanScales(scales={n: ScaleReading(wish=wish[n]) for n in CARD}, cost=ScaleReading(wish=money))
    values, level, why = measure(plan)
    steps = {n: v if v is None else step(v) for n, v in values.items()}
    leaning = sorted((n for n in CARD if steps[n] not in (None, 3)), key=lambda n: -abs(steps[n] - 3))[:2]
    summary = [SHORT[n][steps[n] > 3] for n in sorted(leaning, key=CARD.index)] or ["Balanced"]
    return PlanScales(scales={n: ScaleReading(plan=steps[n], wish=wish[n]) for n in CARD},
                      cost=ScaleReading(plan=level or None, wish=money), why=why,
                      summary=" · ".join(summary + ["$" * level]) if level else "")


def mark_closest(readings: list[PlanScales]) -> None:
    """Tags the one plan nearest what they asked, once 3 or more scales are stated; a tie tags none."""
    if not readings or sum(r.wish is not None for r in readings[0].scales.values()) < STATED_FOR_CLOSEST:
        return
    def gap(r: PlanScales) -> int:
        return sum(abs(s.plan - s.wish) for s in [*r.scales.values(), r.cost] if s.plan and s.wish)
    gaps = [gap(r) for r in readings]
    if gaps.count(min(gaps)) == 1:
        readings[gaps.index(min(gaps))].closest = True
