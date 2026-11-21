from app.schemas.intent_classification import IntentClassification
from app.services.gemini_service import GeminiService


class IntentClassifierService:

    @staticmethod
    def classify(user_message: str) -> IntentClassification:

        prompt = f"""
Classify the user message.

Possible intents:

UPDATE_PROFILE
Examples:
- I want a luxury Japan trip
- My budget is 12000
- Actually make it South Korea

ASK_QUESTION
Examples:
- When is cherry blossom season?
- What's the weather in Tokyo?

GENERATE_ITINERARY
Examples:
- Generate my itinerary
- Create my trip plan

RESET_PROFILE
Examples:
- Start over
- Reset everything

Return JSON only.

User message:
{user_message}
"""

        return GeminiService.generate_structured(
            prompt,
            IntentClassification
        )