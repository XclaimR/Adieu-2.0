from app.schemas.itinerary import Itinerary
from app.schemas.itinerary_critique import ItineraryCritique
from app.schemas.place import Pick
from app.schemas.taste import Taste
from app.schemas.trip_profile import TripProfile
from app.services.llm_client import LLMClient
from app.services.taste_service import TasteService

DEFAULT_DAYS = 3
MAX_DAYS = 14


def _pick_lines(picks: list[Pick]) -> str:
    return "\n".join(
        f"- {p.name} (place_id={p.place_id}, {p.category.value}{', ' + p.address if p.address else ''})"
        for p in picks
    ) or "- none"


class ItineraryPlanningService:

    @staticmethod
    def generate(
        profile: TripProfile,
        taste: Taste,
        saved: list[Pick],
        extras: list[Pick],
        critique: ItineraryCritique | None = None,
    ) -> Itinerary:
        days = min(profile.duration_days or DEFAULT_DAYS, MAX_DAYS)
        feedback = ""
        if critique and not critique.is_valid:
            feedback = "\nA reviewer found these problems with the previous draft; fix them:\n" + "\n".join(
                f"- {issue}" for issue in critique.issues + critique.suggestions
            )

        prompt = f"""
Build a {days}-day itinerary for {profile.destination}.

Trip profile:
{profile.model_dump_json(exclude_none=True)}

Traveler taste:
{TasteService.describe(taste)}

Places the traveler SAVED (include every one of them):
{_pick_lines(saved)}

Other good options you may use to fill gaps:
{_pick_lines(extras)}

Rules:
- Group places that are near each other on the same day.
- Each day gets a short theme and activities with time_of_day (morning/afternoon/evening).
- When an activity is one of the places above, copy its place_id exactly. Otherwise leave place_id null.
- Respect every preference in the profile (diet, pace, mobility, budget level, kids...).
{feedback}
"""
        return LLMClient.generate_structured(prompt, Itinerary)
