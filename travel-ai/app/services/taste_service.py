from app.schemas.taste import Taste, Vibe

VIBES: list[Vibe] = [
    Vibe(id="beach", label="Beach & sun", emoji="🏖️", description="Sand, sea and slow afternoons"),
    Vibe(id="mountains", label="Mountains", emoji="🏔️", description="Hikes, views and fresh air"),
    Vibe(id="city", label="Big city energy", emoji="🌆", description="Neighbourhoods, buzz and late nights"),
    Vibe(id="food", label="Food first", emoji="🍜", description="Trips planned around meals"),
    Vibe(id="culture", label="History & culture", emoji="🏛️", description="Museums, old towns and stories"),
    Vibe(id="nightlife", label="Nightlife", emoji="🪩", description="Bars, clubs and live music"),
    Vibe(id="nature", label="Wild nature", emoji="🌿", description="Parks, wildlife and quiet places"),
    Vibe(id="adventure", label="Adventure", emoji="🧗", description="Diving, climbing, rafting"),
    Vibe(id="wellness", label="Rest & wellness", emoji="🧖", description="Spas, yoga and doing nothing"),
    Vibe(id="luxury", label="Treat myself", emoji="🥂", description="Beautiful hotels and fine dining"),
    Vibe(id="budget", label="Smart on budget", emoji="🎒", description="Hostels, street food, great value"),
    Vibe(id="photo", label="Instagrammable", emoji="📸", description="Views and spots worth sharing"),
]

_VIBE_IDS = {v.id for v in VIBES}
_VIBE_BY_ID = {v.id: v for v in VIBES}


class TasteService:

    @staticmethod
    def set_vibes(taste: Taste, vibe_ids: list[str]) -> Taste:
        valid = [v for v in dict.fromkeys(vibe_ids) if v in _VIBE_IDS]
        return taste.model_copy(update={"vibes": valid})

    @staticmethod
    def describe(taste: Taste) -> str:
        """Compact text for prompts."""
        parts = []
        if taste.vibes:
            parts.append("Vibes: " + ", ".join(_VIBE_BY_ID[v].label for v in taste.vibes if v in _VIBE_BY_ID))
        if taste.likes:
            parts.append("Likes: " + ", ".join(taste.likes))
        if taste.dislikes:
            parts.append("Dislikes: " + ", ".join(taste.dislikes))
        return "\n".join(parts) or "Nothing known yet."
