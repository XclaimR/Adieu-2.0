import pytest

from app.schemas.place import PickCategory, PlaceCandidate
from app.schemas.trip_profile import TripProfile
from app.services.llm_client import LLMClient
from app.services.ranking_service import RankingService


def candidates():
    return [
        PlaceCandidate(place_id=f"p{i}", name=f"Place {i}", category=PickCategory.EAT)
        for i in range(3)
    ]


def test_ranking_ignores_unknown_refs_and_keeps_order(fake_llm):
    picks = RankingService.rank(PickCategory.EAT, candidates(), TripProfile(destination="Lisbon"))

    assert [p.place_id for p in picks] == ["p1", "p0"]
    assert picks[0].why == "fits you"


def test_ranking_falls_back_to_the_given_order_when_model_fails(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("model down")

    monkeypatch.setattr(LLMClient, "generate_structured", staticmethod(boom))
    picks = RankingService.rank(PickCategory.EAT, candidates(), TripProfile(destination="Lisbon"))

    assert [p.place_id for p in picks] == ["p0", "p1", "p2"]
    assert all(p.why is None for p in picks)


def test_search_hints_steer_queries_toward_the_traveler():
    from app.schemas.personality import Note, Personality
    from app.schemas.trip_profile import BudgetLevel
    from app.services.places_service import search_hints

    profile = TripProfile(destination="Lisbon", budget_level=BudgetLevel.BUDGET, kids=True)
    diet = Personality(notes=[Note(id="n1", text="vegetarian", category="diet", kind="must")])
    assert search_hints(PickCategory.EAT, profile, diet) == "vegetarian affordable"
    assert search_hints(PickCategory.STAY, profile) == "affordable family-friendly"
    assert search_hints(PickCategory.HIDDEN_GEMS, profile) == ""


@pytest.mark.parametrize("name,type_label,summary,expected", [
    ("Sunny Hostel", "Hostel", None, "budget"),
    ("Casa Azul", "Guest House", None, "budget"),
    ("City Hotel", "Hotel", None, None),  # nothing says: unknown, not "mid"
    ("Old Town Hotel", "Hotel", "A 3-star hotel by the station.", "mid"),
    ("Grand Palace Resort", "Resort Hotel", None, "luxury"),
    ("Quiet Inn", "Hotel", "A five-star retreat", "luxury"),
])
def test_estimate_stay_tier(name, type_label, summary, expected):
    from app.services.places_service import estimate_stay_tier
    assert estimate_stay_tier(name, type_label, summary) == expected
