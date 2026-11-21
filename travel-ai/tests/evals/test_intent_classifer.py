import pytest

from app.schemas.user_intent import UserIntent

from app.services.intent_classifier_service import (
    IntentClassifierService
)


TEST_CASES = [
    # (
    #     "I want a luxury Japan trip",
    #     UserIntent.UPDATE_PROFILE
    # ),
    (
        "My budget is 12000",
        UserIntent.UPDATE_PROFILE
    ),
    (
        "Actually make it South Korea",
        UserIntent.UPDATE_PROFILE
    ),
    (
        "Generate my itinerary",
        UserIntent.GENERATE_ITINERARY
    ),
    (
        "I'm ready for an itinerary now",
        UserIntent.GENERATE_ITINERARY
    ),
    # (
    #     "When is cherry blossom season?",
    #     UserIntent.ASK_QUESTION
    # ),
    (
        "What's the weather in Tokyo?",
        UserIntent.ASK_QUESTION
    ),
    # (
    #     "Start over",
    #     UserIntent.RESET_PROFILE
    # ),
    (
        "Forget everything",
        UserIntent.RESET_PROFILE
    ),
    (
        "Let's plan a honeymoon",
        UserIntent.UPDATE_PROFILE
    )
]


@pytest.mark.parametrize(
    "message,expected_intent",
    TEST_CASES
)
def test_intent_classifier(
    message,
    expected_intent
):

    result = (
        IntentClassifierService
        .classify(message)
    )
    print(result.intent)

    assert (
        result.intent ==
        expected_intent
    )