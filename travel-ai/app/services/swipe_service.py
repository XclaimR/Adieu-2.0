"""The swipe deck (Ticket #21): photo cards for travelers who can't describe their trip style.

Every card's effect on the six scales is fixed here and tested, never guessed by the model. Answers become this
trip's scales with source "swiped": weaker than anything the traveler said, so their own words always win.
"""
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from app.schemas.personality import Personality, Scale
from app.services.personality_service import KNOWN, SCALES, leaning, trait

Answer = Literal["right", "left", "skip"]
LIMIT = 12
SOURCE = "swiped"
MAX_CONFIDENCE = 0.6  # above KNOWN, below anything said in chat (0.7) or a stated note (0.75)
PER_VOTE = 0.3  # two agreeing swipes on a card's main scale make it known
LEFT = 0.5  # "not for me" says less than "that's me": not liking rooftop bars doesn't make you a homebody
VOTE = {"right": 1.0, "left": -LEFT, "skip": 0.0}
SWIPE_CHIP = "Swipe to find my style"
_UNSURE = re.compile(r"\b(not sure|unsure|no idea|idk|don'?t know|dunno|whatever|anything|you decide|surprise me|"
                     r"just chat)\b", re.I)


@dataclass(frozen=True)
class Card:
    id: str
    title: str
    effects: dict[str, float]  # scale -> -1..1, toward that scale's right pole when swiped right
    moods: tuple[str, ...] = ()
    credit: dict = field(default_factory=dict)  # by, licence, source (Wikimedia Commons page)


def _commons(by: str, licence: str, file: str) -> dict:
    return {"by": by, "licence": licence, "source": f"https://commons.wikimedia.org/wiki/File:{file}"}


# The order breaks ties when choosing the next card, so the first ones are the most telling.
CARDS: list[Card] = [
    Card("slow_cafe", "Slow café morning", {"pace": -1, "energy": -0.3}, ("relaxed",),
         _commons("Shixart1985", "CC BY 2.0",
                  "Enjoying_a_croissant_and_coffee_while_sitting_at_a_table_in_a_cafe.jpg")),
    Card("rooftop_bar", "Rooftop bar at night", {"energy": 1, "money": 0.3}, ("nightlife",),
         _commons("Raita Futo from Tokyo, Japan", "CC BY 2.0", "Ho_Chi_Minh%27s_rooftop_bar_(31663805138).jpg")),
    Card("street_food", "Street-food market", {"food": 1, "money": -0.5}, ("foodie",),
         _commons("ZhuWahDaSeeMcYanSze", "CC0",
                  "HK_%E4%BD%90%E6%95%A6_Jordan_%E5%BB%9F%E8%A1%97_Temple_Street_%E5%A4%9C%E5%B8%82_market_food_"
                  "stalls_night_December_2023_R12S_18.jpg")),
    Card("empty_beach", "Beach with nobody around", {"crowds": -1, "pace": -0.5}, ("beach",),
         _commons("Nnthurber", "CC BY-SA 4.0", "Wide_panoramic_view_of_empty_Maya_Bay_beach_and_cliffs.jpg")),
    Card("luxury_suite", "Overwater villa, all to yourself", {"comfort": 1}, ("indulgent",),
         _commons("Martin Falbisoner", "CC BY-SA 4.0", "Diamonds_Thudufushi_Beach_and_Water_Villas,_May_2017_-04.jpg")),
    Card("landmark", "Famous landmark", {"crowds": 1}, ("culture",),
         _commons("Eutouring", "CC BY-SA 4.0", "Tourists_visiting_the_Eiffel_Tower.jpg")),
    Card("hostel_friends", "Hostel with new friends", {"comfort": -1, "energy": 0.5}, (),
         _commons("Unknown author", "CC0", "Reception_backpackers_Onefam_Madrid_Hostel.jpg")),
    Card("balloon_ride", "Once-in-a-lifetime balloon ride", {"money": 1}, ("adventurous",),
         _commons("Kyle Lamothe", "CC BY 3.0", "Great_Float_Over_G%C3%B6reme_(55651132).jpeg")),
    Card("big_day", "Six sights in one day", {"pace": 1, "crowds": 0.5}, ("culture",),
         _commons("Daniel Case", "CC BY-SA 3.0", "Norwegian_tour_group_being_led_by_guide_at_Temple_of_Heaven.jpg")),
    Card("tasting_menu", "Tasting menu", {"food": 1, "money": 0.7}, ("foodie", "indulgent"),
         _commons("Asok5 from Budapest, Hungary", "CC BY-SA 2.0",
                  "Water_Buffalo_and_Sturgeon_Caviar_(Onyx)_(41317260975).jpg")),
    Card("sunrise_hike", "Sunrise hike", {"pace": 0.5, "crowds": -0.5}, ("nature", "adventurous"),
         _commons("Aneta Ivanova", "CC0", "Sunrise_hiking_views_(Unsplash).jpg")),
    Card("spa_day", "Spa day", {"pace": -0.7, "money": 0.5, "energy": -0.5}, ("wellness",),
         _commons("Paradisus Bali", "CC BY-SA 4.0", "YHI_Spa_treatment_room_at_Paradisus_by_Meli%C3%A1_Bali.jpg")),
    Card("back_streets", "Back streets with no tourists", {"crowds": -1}, (),
         _commons("PattayaPatrol", "CC BY-SA 4.0",
                  "DZ6_0497_Quiet_early_morning_on_a_narrow_cobblestone_street_lined_with_shuttered_wooden_shops_a_"
                  "lone_dog_padding_down_the_center_while_a_resident_stands_at_a_doorway_with_a_smile.jpg")),
    Card("dancing", "Dancing till 3 am", {"energy": 1, "pace": 0.3}, ("nightlife",),
         _commons("Arnold Jaeger Werner", "CC BY 2.0", "Bob_Beaman_Nightclub_Munich_1.jpg")),
    Card("camping", "A night in a tent", {"comfort": -1, "crowds": -0.5}, ("nature",),
         _commons("Joshua Tree National Park", "Public domain",
                  "Tent_in_Hidden_Valley_Campground_-_Flickr_-_Joshua_Tree_National_Park.jpg")),
    Card("quick_bite", "Quick bite between sights", {"food": -1, "pace": 0.3}, (),
         _commons("Daniel Case", "CC BY-SA 3.0", "People_at_taco_cart_in_Mexico_City.jpg")),
    Card("picnic", "Picnic in the park", {"money": -1, "pace": -0.3}, ("relaxed",),
         _commons("Billie Grace Ward from New York, USA", "CC BY 2.0",
                  "An_afternoon_in_the_park..._(37313150922).jpg")),
]
BY_ID = {c.id: c for c in CARDS}


def offer(message: str, stuck: bool, mood_asked_again: bool, state: dict, has_itinerary: bool) -> bool:
    """Offer the deck to a traveler who seems unsure, until they have used it once."""
    if has_itinerary or state.get("swipes_done") or state.get("swipes"):
        return False
    return stuck or mood_asked_again or bool(_UNSURE.search(message))


def scales(answers: dict[str, str]) -> dict[str, Scale]:
    """The scales the swipes alone say. Disagreeing swipes cancel out: confidence falls, the side never flips on
    one swipe. value = how one-sided the votes are; confidence = how much was said x how much it agrees."""
    votes: dict[str, list[float]] = {}
    for card_id, answer in answers.items():
        for name, effect in BY_ID[card_id].effects.items():
            if answer != "skip":
                votes.setdefault(name, []).append(effect * VOTE[answer])
    out = {}
    for name, vs in votes.items():
        total, weight = sum(vs), sum(abs(v) for v in vs)
        agreement = abs(total) / weight
        out[name] = Scale(value=round(0.8 * total / weight, 3),
                          confidence=round(min(MAX_CONFIDENCE, PER_VOTE * weight) * agreement, 3), source=SOURCE)
    return out


def apply(personality: Personality, answers: dict[str, str]) -> Personality:
    """Swipes fill a scale only where nothing surer is known: a scale from words is never replaced."""
    merged = dict(personality.scales)
    for name, scale in scales(answers).items():
        old = merged.get(name)
        if old is None or old.source == SOURCE or old.confidence < scale.confidence:
            merged[name] = scale
    for name in [n for n, s in merged.items() if s.source == SOURCE and n not in scales(answers)]:
        del merged[name]  # all its swipes became skips
    return personality.model_copy(update={"scales": merged})


def next_card(personality: Personality, answers: dict[str, str]) -> Card | None:
    """The unanswered card that says the most about the scales we're least sure of, or None when done."""
    known = {n: s.confidence for n, s in personality.scales.items()}
    if len(answers) >= LIMIT or all(known.get(n, 0) >= KNOWN for n in SCALES):
        return None
    left = [c for c in CARDS if c.id not in answers]
    gain = lambda c: sum(abs(e) * (1 - min(1.0, known.get(n, 0) / KNOWN)) for n, e in c.effects.items())
    best = max(left, key=gain, default=None)  # max keeps the first of equals, so CARDS order breaks ties
    return best if best and gain(best) > 0 else None


def moods(answers: dict[str, str]) -> list[str]:
    """The kinds of trip the traveler said "that's me" to most, at most two."""
    count = Counter(m for card_id, a in answers.items() if a == "right" for m in BY_ID[card_id].moods)
    return [m for m, _ in count.most_common(2)]


def summary(personality: Personality) -> str:
    """One line from the swiped scales: "You lean: slow pace, avoids crowds"."""
    words = []
    for name in SCALES:
        s = personality.scales.get(name)
        if s and s.source == SOURCE:
            said = trait(name, s) or leaning(name, s)
            if said:
                words.append(said.split(" (")[0].lower())
    if not words:
        return "No clear lean from your swipes, so I'll keep the trip balanced."
    return f"You lean: {', '.join(words)}."
