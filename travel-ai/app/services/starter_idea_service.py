import logging

from app.schemas.starter_idea import StarterIdea, StarterIdeas
from app.schemas.taste import Taste
from app.services.llm_client import LLMClient
from app.services.places_service import PlacesService
from app.services.taste_service import TasteService

logger = logging.getLogger(__name__)

MAX_IDEAS = 4


class StarterIdeaService:

    def __init__(self, places: PlacesService):
        self.places = places

    def generate(self, taste: Taste, hint: str | None = None) -> list[StarterIdea]:
        prompt = f"""
Suggest {MAX_IDEAS + 2} varied trip ideas for this traveler. Mix well-known and less obvious countries
around the world. Each destination is a different country, by its name alone (e.g. "Portugal"), never a city,
region or island within one. If they want cities, mountains or beaches, pick countries that are great for those.
Don't name a city in the title or pitch either.

What we know about the traveler:
{TasteService.describe(taste)}
{f"They also said: {hint}" if hint else ""}
"""
        ideas = LLMClient.generate_structured(prompt, StarterIdeas).ideas

        verified: dict[str, StarterIdea] = {}
        for idea in ideas:
            country = self.places.resolve_country(idea.destination)
            if not country:
                logger.info("Dropping %r: not a country", idea.destination)
                continue
            # The usual name, so "Türkiye" and "Turkey" are one idea, and tapping it finds the same country.
            verified.setdefault(country["place_id"], idea.model_copy(
                update={"destination": country["name"], "photo_name": None}))
            if len(verified) == MAX_IDEAS:
                break

        return list(verified.values())
