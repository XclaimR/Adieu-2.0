"""The persona suite's own rule checks, on canned trips (the suite itself runs against the live app)."""
from tests.personas.personas import PERSONAS
from tests.personas.run import invented_facts, misfit_picks, pick_reply

BACHELOR = next(p for p in PERSONAS if p["name"] == "bachelor")
BACKPACKER = next(p for p in PERSONAS if p["name"] == "budget_backpacker")


def test_group_of_friends_with_a_size_is_invented():
    profile = {"party": "friends", "party_size": 6, "mood": ["nightlife"]}
    assert invented_facts(profile, BACHELOR["facts"]) == ["party_size=6"]


def test_unset_fields_and_stated_moods_pass():
    assert invented_facts({"party": "friends", "mood": ["Fun", "party"]}, BACHELOR["facts"]) == []
    assert invented_facts({"mood": ["romantic"]}, BACHELOR["facts"]) == ["mood='romantic'"]


def test_misfit_picks_flags_words_and_pricey_stays():
    stay = {"name": "Grand Hotel", "tier": "luxury", "price_level": "PRICE_LEVEL_VERY_EXPENSIVE"}
    trip = {"panel": None, "drafts": [{"title": "Saigon", "summary": "A luxury escape", "itinerary": {
        "stay": stay, "days": [{"activities": [{"name": "Street food walk", "description": "Barcelona-style"}]}]}}]}
    assert misfit_picks(trip, BACKPACKER) == [
        "Grand Hotel: $$$$", "Grand Hotel: tier luxury", "draft 'Saigon': 'luxury'"]


def test_pick_reply_uses_script_once_then_chips_then_gives_up():
    used = set()
    assert pick_reply(BACKPACKER, "Who's coming along?", ["Just me", "Friends"], used)[0] == "Just me."
    assert pick_reply(BACKPACKER, "Who's coming along?", ["Just me", "Friends"], used) == ("Just me", {})
    assert pick_reply(BACKPACKER, "Any diet needs?", ["Vegan"], used)[0] == "just show me plans"
