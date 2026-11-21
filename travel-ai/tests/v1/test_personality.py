from app.schemas.personality import Personality, Scale
from app.schemas.place import PickCategory, PlaceCandidate
from app.schemas.taste import Taste
from app.schemas.trip_profile import BudgetLevel, Party, Preference, TripProfile
from app.services.llm_client import LLMClient
from app.services.personality_service import PersonalityService
from app.services.places_service import traveler_fit
from app.services.ranking_service import Ranking, RankedItem, RankingService
from tests.v1.test_api import decision, new_trip, say


def card(profile=TripProfile(), taste=Taste(), stored=None):
    return PersonalityService.card(PersonalityService.update(stored, profile, taste))


def test_lasting_taste_seeds_leanings_but_the_card_shows_them_as_unknown():
    p = card(taste=Taste(vibes=["food", "nightlife", "wellness"]))
    assert p.scales["food"].confidence < 0.5 and p.scales["food"].value > 0
    assert p.traits == []
    assert p.unknown == ["Pace", "Money", "Energy", "Crowds", "Food", "Comfort"]
    known, leans = PersonalityService.traits(p)
    assert known == [] and "Food is the point" in leans


def test_what_the_chat_says_sets_the_matching_scales():
    p = card(TripProfile(mood=["slow", "foodie"], budget_level=BudgetLevel.LUXURY,
                         preferences=[Preference(key="crowds", value="avoid crowds")]))
    assert p.traits == ["Slow pace", "Avoids crowds", "Food is the point", "Luxury"]
    assert p.unknown == ["Money", "Energy"]


def test_words_that_dont_say_which_way_stay_unknown():
    p = card(TripProfile(mood=["fun", "amazing"], destination="Spain"))
    assert p.traits == []
    # An inferred preference is a guess, not something the traveler said.
    p = card(TripProfile(preferences=[Preference(key="pace", value="relaxed", source="inferred")]))
    assert p.traits == []


def test_negation_stays_in_its_clause_and_family_means_chill_evenings():
    p = card(TripProfile(mood=["no nightlife, lots of food"]))
    assert p.traits == ["Chill evenings", "Food is the point"]
    assert card(TripProfile(party=Party.FAMILY)).traits == ["Chill evenings"]
    assert card(TripProfile(budget_level=BudgetLevel.MID)).traits == ["Mid-range comfort"]


def test_chat_overrides_taste_and_answers_from_elsewhere_are_kept():
    asked = Personality(scales={"money": Scale(value=0.9, confidence=0.9, source="asked")})
    p = card(TripProfile(budget_level=BudgetLevel.BUDGET), Taste(vibes=["luxury"]), stored=asked)
    assert p.traits == ["Money goes to experiences", "Backpacker comfort"]


def test_prompts_get_known_traits_and_weak_guesses_separately():
    p = PersonalityService.update(None, TripProfile(mood=["slow"], party=Party.SOLO), Taste(vibes=["nightlife"]))
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
    p = PersonalityService.update(None, TripProfile(mood=["foodie"]), Taste(vibes=["nightlife"]))
    places = [PlaceCandidate(place_id=f"p{i}", name=f"P{i}", category=PickCategory.EAT) for i in range(3)]
    picks = RankingService.rank(PickCategory.EAT, places, TripProfile(mood=["foodie"]), Taste(), personality=p)
    assert [x.trait for x in picks] == ["Food is the point", None, None]


def test_trip_personality_is_stored_and_drives_the_draft_prompts(client, auth, fake_llm):
    client.post("/me/taste", json={"vibes": ["food"]}, headers=auth)
    trip = new_trip(client, auth)
    assert trip["personality"]["traits"] == [] and len(trip["personality"]["unknown"]) == 6
    assert trip["personality"]["scales"]["food"]["source"] == "taste"

    fake_llm.decisions.append(decision(destination="Lisbon", mood=["relaxed", "foodie"], party=Party.COUPLE))
    turn = say(client, auth, trip["id"], "Lisbon, slow and foodie, with my partner")
    assert turn["trip"]["personality"]["traits"] == ["Slow pace", "Chill evenings", "Food is the point"]
    say(client, auth, trip["id"], "not sure")  # drafts

    draft_prompts = [p for name, p in fake_llm.prompts if name == "DraftPlan"]
    assert draft_prompts and all("personality: Slow pace; Chill evenings; Food is the point" in p for p in draft_prompts)
