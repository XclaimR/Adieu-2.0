from app.schemas.traveler_profile import TravelerProfile
from app.services.itinerary_critique_service import ItineraryCritiqueService
from app.services.itinerary_planning_service import ItineraryPlanningService

def test_critique_itinerary():
    profile = TravelerProfile(
        destination="Japan",
        traveler_count=2,
        budget=12000,
        travel_style="luxury"
    )

    itinerary = ItineraryPlanningService.generate(
        profile
    )

    critique = (
        ItineraryCritiqueService.critique(
            profile,
            itinerary
        )
    )
    
    print(
        itinerary.model_dump_json(
            indent=4
        )
    )

    print(
        critique.model_dump_json(
            indent=4
        )
    )