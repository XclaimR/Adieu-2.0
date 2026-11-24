import pytest

from app.schemas.personality import NoteChange, Personality, Scale
from app.schemas.trip_profile import Party, TripProfile
from app.services import swipe_service as swipes
from app.services.personality_service import KNOWN, POLES, STATED_CONFIDENCE, PersonalityService
from tests.v1.test_api import decision, new_trip, say

# Each card's direction on each scale it touches, swiped right. Changing a card's effects must change this table.
EXPECTED = {
    "slow_cafe": {"pace": -1, "energy": -1},
    "rooftop_bar": {"energy": 1, "comfort": 1},
    "street_food": {"food": 1, "comfort": -1},
    "empty_beach": {"crowds": -1, "pace": -1},
    "luxury_suite": {"money": -1, "comfort": 1},
    "landmark": {"crowds": 1},
    "hostel_friends": {"comfort": -1, "energy": 1},
    "balloon_ride": {"money": 1},
    "big_day": {"pace": 1, "crowds": 1},
    "tasting_menu": {"food": 1, "comfort": 1, "money": 1},
    "sunrise_hike": {"pace": 1, "crowds": -1},
    "spa_day": {"pace": -1, "comfort": 1, "energy": -1},
    "back_streets": {"crowds": -1},
    "dancing": {"energy": 1, "pace": 1},
    "camping": {"comfort": -1, "crowds": -1},
}


def sign(x):
    return (x > 0) - (x < 0)


def test_every_card_is_listed_with_its_effects():
    assert set(EXPECTED) == set(swipes.BY_ID)


@pytest.mark.parametrize("card_id", EXPECTED)
def test_each_card_moves_only_its_scales_and_left_says_less(card_id):
    right, left, skip = (swipes.scales({card_id: a}) for a in ("right", "left", "skip"))
    assert {n: sign(s.value) for n, s in right.items()} == EXPECTED[card_id]
    assert {n: sign(s.value) for n, s in left.items()} == {n: -d for n, d in EXPECTED[card_id].items()}
    assert skip == {}
    for name in EXPECTED[card_id]:
        assert 0 < left[name].confidence < right[name].confidence < KNOWN  # one swipe is only a lean
        assert right[name].source == "swiped"


@pytest.mark.parametrize("card_id", EXPECTED)
def test_each_card_has_an_open_licence_and_credit(card_id):
    credit = swipes.BY_ID[card_id].credit
    assert credit["by"] and credit["source"].startswith("https://commons.wikimedia.org/wiki/File:")
    assert "NC" not in credit["licence"] and "ND" not in credit["licence"]


def test_agreeing_swipes_become_known_but_stay_below_what_was_said():
    s = swipes.scales({"slow_cafe": "right", "spa_day": "right", "big_day": "left"})["pace"]
    assert s.value < 0 and KNOWN <= s.confidence == swipes.MAX_CONFIDENCE < STATED_CONFIDENCE


def test_contradictory_swipes_lower_confidence_instead_of_flipping():
    one = swipes.scales({"rooftop_bar": "right"})["energy"]
    mixed = swipes.scales({"rooftop_bar": "right", "spa_day": "right"})["energy"]  # nightlife, then a chill spa
    assert mixed.value > 0  # still the side said most strongly, never flipped by the weaker swipe
    assert mixed.confidence < one.confidence
    even = swipes.scales({"rooftop_bar": "right", "spa_day": "right", "dancing": "left"})["energy"]
    assert even.confidence == 0 and even.value == 0


def test_swipes_never_replace_what_the_traveler_said():
    said = PersonalityService.update(None, TripProfile(), [
        NoteChange(op="add", text="nightlife", quote="nightlife", toward="nightlife")])
    after = swipes.apply(said, {"slow_cafe": "right", "spa_day": "right", "dancing": "left", "rooftop_bar": "left"})
    assert after.scales["energy"] == said.scales["energy"]  # said nightlife; swipes said chill
    assert after.scales["pace"].source == "swiped"  # nothing was said about pace

    # A later chat turn keeps the swipes, and a new stated note still wins over them.
    later = PersonalityService.update(after, TripProfile(), [
        NoteChange(op="add", text="packed days", quote="packed", toward="packed")])
    assert later.scales["pace"].value > 0 and later.scales["pace"].source == "note"
    kept = PersonalityService.update(after, TripProfile())
    assert kept.scales["pace"] == after.scales["pace"]


def test_an_inferred_note_does_not_overwrite_surer_swipes():
    swiped = swipes.apply(Personality(), {"slow_cafe": "right", "spa_day": "right"})
    guess = NoteChange(op="add", text="busy days", quote="busy", toward="packed", source="inferred")
    assert PersonalityService.update(swiped, TripProfile(), [guess]).scales["pace"].value < 0


def test_skipping_every_swipe_on_a_scale_removes_it():
    p = swipes.apply(Personality(), {"landmark": "right"})
    assert swipes.apply(p, {"landmark": "skip"}).scales == {}


def test_order_asks_about_the_least_known_scales_and_stops_at_twelve():
    first = swipes.next_card(Personality(), {})
    assert first.id == "luxury_suite"  # nothing known yet: the card that touches the most
    # Pace is already known from words, so a pace-only card is never the most telling one.
    known_pace = Personality(scales={"pace": Scale(value=-0.7, confidence=0.75, source="note")})
    assert "pace" not in swipes.next_card(known_pace, {}).effects or len(swipes.next_card(known_pace, {}).effects) > 1
    twelve = {c.id: "skip" for c in swipes.CARDS[:swipes.LIMIT]}
    assert swipes.next_card(Personality(), twelve) is None
    all_known = Personality(scales={n: Scale(confidence=0.75, source="note") for n in {s for s, _ in POLES.values()}})
    assert swipes.next_card(all_known, {}) is None


def test_summary_and_moods_come_from_the_swipes():
    answers = {"slow_cafe": "right", "spa_day": "right", "empty_beach": "right", "back_streets": "right"}
    p = swipes.apply(Personality(), answers)
    assert swipes.summary(p) == "You lean: slow pace, chill evenings, avoids crowds, luxury."
    assert swipes.moods(answers)[0] in {"relaxed", "wellness", "beach"}
    assert swipes.summary(Personality()).startswith("No clear lean")


def test_offered_when_unsure_and_only_until_used():
    assert swipes.offer("hmm not sure", False, False, {}, False)
    assert swipes.offer("ok", True, False, {}, False)
    assert swipes.offer("ok", False, True, {}, False)
    assert not swipes.offer("Lisbon with my wife", False, False, {}, False)
    assert not swipes.offer("not sure", True, False, {"swipes_done": True}, False)
    assert not swipes.offer("not sure", True, False, {}, True)


def test_deck_over_the_api(client, auth, fake_llm):
    trip = new_trip(client, auth)
    assert swipes.SWIPE_CHIP in trip["answer_options"]  # "Just chat" offers it

    deck = client.get(f"/trips/{trip['id']}/swipes", headers=auth).json()
    seen = []
    while deck["card"]:
        card = deck["card"]
        assert card["photo"] == f"/swipe/{card['id']}.jpg" and card["credit"]["licence"]
        seen.append(card["id"])
        answer = "right" if card["id"] in {"slow_cafe", "spa_day", "empty_beach", "back_streets"} else "left"
        deck = client.post(f"/trips/{trip['id']}/swipes", json={"card_id": card["id"], "answer": answer},
                           headers=auth).json()
        assert deck["answered"] == len(seen) <= swipes.LIMIT
    assert len(set(seen)) == len(seen)
    assert client.post(f"/trips/{trip['id']}/swipes", json={"card_id": "nope", "answer": "right"},
                       headers=auth).status_code == 400

    done = client.post(f"/trips/{trip['id']}/swipes/finish", headers=auth).json()
    assert done["messages"][-1]["content"].startswith("You lean: slow pace")
    assert done["profile"]["mood"] and swipes.SWIPE_CHIP not in done["answer_options"]
    assert any(t.startswith("Slow pace") and "from your swipes" in t for t in done["personality"]["traits"])

    # After the deck it is not offered again, even when unsure.
    fake_llm.decisions.append(decision())
    assert swipes.SWIPE_CHIP not in say(client, auth, trip["id"], "not sure")["answer_options"]


def test_unsure_traveler_is_offered_the_deck(client, auth, fake_llm):
    trip = new_trip(client, auth)
    fake_llm.decisions.append(decision())
    assert swipes.SWIPE_CHIP in say(client, auth, trip["id"], "I don't know what I want")["answer_options"]


class AppApi:
    """The persona runner's Api, answered in-process by the app with the fake model."""
    def __init__(self, client):
        self.client, self.token = client, None

    def post(self, path, body, method="POST"):
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        return self.client.request(method, path, json=body, headers=headers).json()


def test_unsure_persona_swipes_to_a_style_then_gets_plans(client, fake_llm):
    from tests.personas.personas import PERSONAS
    from tests.personas.run import check, converse

    unsure = next(p for p in PERSONAS if p["name"] == "unsure")
    fake_llm.decisions += [decision(party=Party.SOLO, party_size=1, duration_days=7),  # "I don't know what I want"
                           decision(),  # "just show me plans" after the deck -> destination ideas
                           decision(destination="Japan")]  # picks the first idea -> plans
    run = converse(AppApi(client), unsure)
    assert run["swiped"] and run["swiped"]["summary"].startswith("You lean: slow pace")
    rules = dict((r, ok) for r, ok, _ in check(unsure, run))
    assert all(rules.values()), check(unsure, run)
