from app.schemas.traveler_profile import TravelerProfile
from app.services.gemini_service import GeminiService


class ProfileExtractionService:

    @staticmethod
    def extract(user_message: str) -> TravelerProfile:

        prompt = f"""
Extract travel planning information.

Only extract information that is explicitly stated
or can be safely inferred.

Do not guess occasions, budgets,
preferences, or motivations.

User message:
{user_message}
"""

        return GeminiService.generate_structured(
            prompt,
            TravelerProfile
        )