from app.schemas.user_intent import UserIntent
from app.services.intent_classifier_service import IntentClassifierService


class IntentService:

    @staticmethod
    def classify(
        message: str
    ) -> UserIntent:

        lower = message.lower()

        if "start over" in lower:
            return UserIntent.RESET_PROFILE

        if "reset" in lower:
            return UserIntent.RESET_PROFILE

        if "generate itinerary" in lower:
            return UserIntent.GENERATE_ITINERARY


        return IntentClassifierService.classify(message).intent