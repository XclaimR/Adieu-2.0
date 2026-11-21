from app.schemas.user_intent import UserIntent
from app.services.intent_service import IntentService


def test_reset_intent():

    intent = IntentService.classify(
        "start over"
    )

    assert (
        intent ==
        UserIntent.RESET_PROFILE
    )
    
    
def test_question_intent():
    intent = IntentService.classify(
        "What is the weather like in Paris?"
    )

    assert (
        intent ==
        UserIntent.ASK_QUESTION
    )

def test_generate_itinerary_intent():
    intent = IntentService.classify(
        "generate itinerary"
    )

    assert (
        intent ==
        UserIntent.GENERATE_ITINERARY
    )

def test_update_profile_intent():
    intent = IntentService.classify(
        "update profile"
    )

    assert (
        intent ==
        UserIntent.UPDATE_PROFILE
    )