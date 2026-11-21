import logging

from app.schemas.starter_idea import StarterIdea, StarterIdeas
from app.schemas.taste import Taste
from app.services.llm_client import LLMClient
from app.services.places_service import PlacesService, PlacesUnavailableError
from app.services.taste_service import TasteService

logger = logging.getLogger(__name__)

MAX_IDEAS = 4


class StarterIdeaService:

    def __init__(self, places: PlacesService):
        self.places = places

    def generate(self, taste: Taste, hint: str | None = None) -> list[StarterIdea]:
        prompt = f"""
Suggest {MAX_IDEAS + 2} varied trip ideas for this traveler. Mix well-known and less obvious destinations
around the world. Each must be a real city or region.

What we know about the traveler:
{TasteService.describe(taste)}
{f"They also said: {hint}" if hint else ""}
"""
        ideas = LLMClient.generate_structured(prompt, StarterIdeas).ideas

        verified: list[StarterIdea] = []
        for idea in ideas:
            try:
                match = self.places.verify_destination(idea.destination)
            except PlacesUnavailableError:
                # Without Places we can't verify; keep the idea rather than show nothing.
                verified.append(idea)
            else:
                if match:
                    verified.append(idea.model_copy(update={"photo_name": match.photo_name}))
                else:
                    logger.info("Dropping unverifiable destination %r", idea.destination)
            if len(verified) == MAX_IDEAS:
                break

        return verified
