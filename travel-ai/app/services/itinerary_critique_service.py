from app.schemas.itinerary import Itinerary
from app.schemas.itinerary_critique import ItineraryCritique
from app.schemas.place import Pick
from app.schemas.trip_profile import TripProfile
from app.services.llm_client import LLMClient


class ItineraryCritiqueService:

    @staticmethod
    def critique(itinerary: Itinerary, profile: TripProfile, saved: list[Pick]) -> ItineraryCritique:
        missing = {p.place_id for p in saved} - {
            a.place_id for day in itinerary.days for a in day.activities if a.place_id
        }
        missing_names = [p.name for p in saved if p.place_id in missing]

        prompt = f"""Review this itinerary against the traveler's profile.

Traveler profile:
{profile.model_dump_json(exclude_none=True)}

Itinerary:
{itinerary.model_dump_json()}

Saved places missing from the itinerary: {", ".join(missing_names) or "none"}

Check:
- every saved place is included
- preferences are respected (diet, pace, mobility, budget level, kids)
- days are realistic: not overpacked, nearby places grouped
- activity balance

Set is_valid to false only for real problems.
"""
        return LLMClient.generate_structured(prompt, ItineraryCritique)
