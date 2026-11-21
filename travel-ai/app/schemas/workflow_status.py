from enum import Enum


class WorkflowStatus(str, Enum):

    COLLECTING_INFORMATION = "collecting_information"

    READY_FOR_PLANNING = "ready_for_planning"

    GENERATING_ITINERARY = "generating_itinerary"

    REVIEWING_ITINERARY = "reviewing_itinerary"

    COMPLETED = "completed"