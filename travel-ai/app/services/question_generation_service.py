class QuestionGenerationService:

    QUESTIONS = {
        "destination":
            "Which destination are you planning to visit?",

        "traveler_count":
            "How many travelers are going on this trip?",

        "budget":
            "What's your approximate budget for the trip?",

        "travel_style":
            "What kind of travel experience are you looking for?",
            
        "duration":
            "How many days do you plan to spend on this trip?",
    }

    @classmethod
    def generate(
        cls,
        field_name: str
    ) -> str:

        return cls.QUESTIONS.get(
            field_name,
            "Could you tell me more?"
        )