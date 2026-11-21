from pydantic import BaseModel, Field

from app.schemas.itinerary_critique import ItineraryCritique
from app.schemas.itinerary_critique import ItineraryCritique
from app.schemas.itinerary import Itinerary
from app.schemas.traveler_profile import TravelerProfile
from app.schemas.workflow_status import WorkflowStatus


class PlannerState(BaseModel):
    profile: TravelerProfile = Field(
        default_factory=TravelerProfile
    )

    workflow_status: WorkflowStatus = (
        WorkflowStatus.COLLECTING_INFORMATION
    )
    itinerary: Itinerary | None = None
    critique: ItineraryCritique | None = None
    current_intent: str | None = None
    missing_fields: list[str] = Field(
        default_factory=list
    )
    next_question: str | None = None
    itinerary_id: str | None = None