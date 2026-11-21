from app.schemas.personality import Personality, Scale, ScaleSignal
from app.schemas.place import PickCategory, PlaceCandidate
from app.schemas.taste import Taste
from app.schemas.trip_profile import BudgetLevel, Party, TripProfile
from app.schemas.turn import TurnAction, TurnDecision
from app.services.llm_client import LLMClient
from app.services.personality_service import PersonalityService
from app.services.places_service import traveler_fit
from app.services.ranking_service import Ranking, RankedItem, RankingService
from tests.v1.test_api import new_trip, say


def card(message="", signals=(), profile=TripProfile(), taste=Taste(), stored=None):
    sigs = [ScaleSignal(toward=t, quote=q) for t, q in signals]
    return PersonalityService.card(PersonalityService.update(stored, profile, taste, sigs, message))


def test_lasting_taste_seeds_leanings_but_the_card_shows_them_as_unknown():
    p = card(taste=Taste(vibes=["food", "nightlife", "wellness"]))
    assert p.scales["food"].confidence < 0.5 and p.scales["food"].value > 0
    assert p.traits == []
    assert p.unknown == ["Pace", "Money", "Energy", "Crowds", "Food", "Comfort"]
    known, leans = PersonalityService.traits(p)
    assert known == [] and "Food is the point" in leans


def test_the_travelers_own_words_set_the_matching_scales():
    p = card("A slow food week, away from the crowds", profile=TripProfile(budget_level=BudgetLevel.LUXURY))
    assert p.traits == ["Slow pace", "Avoids crowds", "Food is the point", "Luxury"]
    assert p.unknown == ["Money", "Energy"]


def test_model_written_mood_words_dont_count_only_what_they_said():
    # The model stored mood "luxury" for a spa wish; the card reads the message, not the mood.
    assert card("Paris with my partner", profile=TripProfile(mood=["luxury", "relaxed"])).traits == []


def test_vague_words_and_single_activities_set_nothing():
    for message in ("Planning a fun trip to Spain!", "We're adventurous", "Paris, and one spa afternoon",
                    "A slow week, but one big night out for my birthday"):
        traits = card(message, signals=[("luxury", "spa afternoon"), ("nightlife", "one big night out")]).traits
        assert not any(t.startswith(("Luxury", "Nightlife", "Packed")) for t in traits), message
    assert card("A slow week, but one big night out").traits == ["Slow pace"]
    assert card("One week in Lisbon, slow").traits == ["Slow pace"]  # a trip length isn't a one-off


def test_negation_flips_only_where_no_x_means_the_other_pole():
    assert card("No nightlife, lots of food").traits == ["Chill evenings", "Food is the point (strongly)"]
    for message in ("Not cheap", "No luxury please", "Not too packed"):
        assert card(message, signals=[("packed", "not too packed"), ("slow", "not too packed")]).traits == [], message


def test_intensity_and_certainty_come_from_their_words():
    assert card("We want it really slow").traits == ["Slow pace (strongly)"]
    assert card("A bit slow is fine").traits == ["Slow pace (a little)"]
    assert card("Maybe slow? Not sure").traits == []  # a guess stays unknown
    # The model quoted only "quiet"; "really" just before it still counts.
    assert card("we want it really slow and quiet", signals=[("chill", "quiet")]).traits == [
        "Slow pace (strongly)", "Chill evenings (strongly)"]


def test_model_signals_add_what_the_list_doesnt_know_but_only_with_their_words():
    msg = "Barcelona, avoid the tourist traps, we want what locals love"
    assert card(msg).traits == ["Avoids crowds"]  # the list reads "avoid the tourist traps" on its own
    assert card("Barcelona, we want what locals love", signals=[("hidden_gems", "what locals love")]).traits == [
        "Avoids crowds"]
    assert card(msg, signals=[("slow", "avoid tourist traps")]).traits == ["Avoids crowds"]  # "slow": not their words
    assert card("honestly just doing nothing", signals=[("luxury", "prioritizing comfort")]).traits == []  # invented
    assert card("Something fun!", signals=[("nightlife", "fun")]).traits == []  # vague
    assert card("partying", signals=[("icons", "art")]).traits == ["Nightlife"]  # "art" isn't a word in "partying"
    fun = "Planning a fun trip to Japan with friends!"
    assert card(fun, signals=[("slow", fun)]).traits == []  # a whole sentence isn't proof
    assert card(fun, signals=[("slow", "fun trip to Japan")], profile=TripProfile(destination="Japan")).traits == []
    assert card("A slow week", signals=[("nightlife", "slow week")]).traits == ["Slow pace"]  # about another scale


def test_family_and_budget_are_checked_facts():
    assert card(profile=TripProfile(party=Party.FAMILY)).traits == ["Chill evenings"]
    assert card(profile=TripProfile(budget_level=BudgetLevel.MID)).traits == ["Mid-range comfort"]


def test_answers_from_elsewhere_are_kept():
    asked = Personality(scales={"money": Scale(value=0.9, confidence=0.9, source="asked")})
    p = card(profile=TripProfile(budget_level=BudgetLevel.BUDGET), taste=Taste(vibes=["luxury"]), stored=asked)
    assert p.traits == ["Money goes to experiences (strongly)", "Backpacker comfort"]


def test_prompts_get_known_traits_and_weak_guesses_separately():
    p = PersonalityService.update(None, TripProfile(party=Party.SOLO), Taste(vibes=["nightlife"]), message="slow")
    text = traveler_fit(TripProfile(party=Party.SOLO), p)
    assert "Solo traveler" in text
    assert "personality: Slow pace." in text
    assert "Weak guesses from their usual taste (don't over-weight): Nightlife." in text
    assert traveler_fit(TripProfile(), None) == ""


def test_picks_name_only_a_known_trait(monkeypatch):
    monkeypatch.setattr(LLMClient, "generate_structured", staticmethod(lambda prompt, schema: Ranking(ranked=[
        RankedItem(ref=0, why="a", trait="food is the point."),
        RankedItem(ref=1, why="b", trait="Nightlife"),  # only a weak guess: not shown
        RankedItem(ref=2, why="c", trait="fun"),
    ])))
    p = PersonalityService.update(None, TripProfile(), Taste(vibes=["nightlife"]), message="foodie trip")
    places = [PlaceCandidate(place_id=f"p{i}", name=f"P{i}", category=PickCategory.EAT) for i in range(3)]
    picks = RankingService.rank(PickCategory.EAT, places, TripProfile(), Taste(), personality=p)
    assert [x.trait for x in picks] == ["Food is the point", None, None]


def test_what_they_said_persists_across_turns_drives_drafts_and_newest_wins(client, auth, fake_llm):
    client.post("/me/taste", json={"vibes": ["food"]}, headers=auth)
    trip = new_trip(client, auth)
    assert trip["personality"]["traits"] == [] and len(trip["personality"]["unknown"]) == 6
    assert trip["personality"]["scales"]["food"]["source"] == "taste"

    turn = say(client, auth, trip["id"], "Lisbon, and we want it really slow")
    assert turn["trip"]["personality"]["traits"] == ["Slow pace (strongly)"]

    turn = say(client, auth, trip["id"], "what about Porto?")  # says nothing about pace
    assert turn["trip"]["personality"]["traits"] == ["Slow pace (strongly)"]

    fake_llm.decisions.append(TurnDecision(action=TurnAction.ASK, reply="ok", scale_signals=[
        ScaleSignal(toward="packed", quote="lots to see every day")]))
    turn = say(client, auth, trip["id"], "Hmm, actually lots to see every day")
    assert turn["trip"]["personality"]["traits"] == ["Packed days"]
