from enum import Enum


class UserIntent(str, Enum):
    UPDATE_PROFILE = "update_profile"

    ASK_QUESTION = "ask_question"

    GENERATE_ITINERARY = "generate_itinerary"

    RESET_PROFILE = "reset_profile"