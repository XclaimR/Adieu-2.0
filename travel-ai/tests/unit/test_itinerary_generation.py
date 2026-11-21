from app.schemas.traveler_profile import TravelerProfile
from app.services.itinerary_planning_service import ItineraryPlanningService

def test_generate_itinerary():
    profile = TravelerProfile(
        destination="Japan",
        traveler_count=2,
        budget=12000,
        travel_style="luxury"
    )

    itinerary = (
        ItineraryPlanningService.generate(
            profile
        )
    )
    print(itinerary.model_dump_json(indent=4))