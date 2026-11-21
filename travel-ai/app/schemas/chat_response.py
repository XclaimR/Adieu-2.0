from pydantic import BaseModel

from app.schemas.itinerary import Itinerary
from app.schemas.traveler_profile import TravelerProfile


class ChatResponse(BaseModel):
    conversation_id: str
    assistant_message: str
    profile: TravelerProfile
    itinerary: Itinerary | None = None