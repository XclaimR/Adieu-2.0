import hashlib

import pytest

from app.schemas.starter_idea import StarterIdea
from app.schemas.taste import Taste
from app.schemas.trip_profile import Party
from app.services import gazetteer, open_data
from app.services.places_service import PlacesService
from app.services.starter_idea_service import MAX_IDEAS, StarterIdeaService
from tests.v1.test_api import decision, mood, new_trip, say


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Checking a country must never ask anyone outside."""
    def no_network(*args, **kwargs):
        raise AssertionError("the country check went online")
    monkeypatch.setattr(open_data, "wikimedia", no_network)
    monkeypatch.setattr(gazetteer, "wikimedia", no_network)


def suggested(db_session, fake_llm, *names):
    fake_llm.ideas = [(f"Trip {i}", name) for i, name in enumerate(names)]
    return [i.destination for i in StarterIdeaService(PlacesService(db_session)).generate(Taste())]


@pytest.mark.parametrize("not_a_country", [
    "Lisbon", "Lisbon, Portugal", "Panama City", "Bali", "Ari Atoll, Maldives", "Tuscany", "Hong Kong", "Puerto Rico",
    "Greenland",
])
def test_anything_but_a_country_is_dropped(db_session, fake_llm, not_a_country):
    assert suggested(db_session, fake_llm, not_a_country, "Japan") == ["Japan"]


@pytest.mark.parametrize("country", ["Georgia", "Singapore", "Vatican City", "Jordan", "Iceland", "Ireland"])
def test_a_country_is_kept(db_session, fake_llm, country):
    assert suggested(db_session, fake_llm, country) == [country]


def test_each_country_once_under_its_usual_name(db_session, fake_llm):
    assert suggested(db_session, fake_llm, "Türkiye", "Turkey", "Japan", "japan", "USA", "Czechia") == [
        "Turkey", "Japan", "United States", "Czech Republic"]


def test_at_most_four_ideas_without_photos_the_model_made_up(db_session, fake_llm):
    fake_llm.ideas = [(f"Trip {i}", c) for i, c in enumerate(["Peru", "Oman", "Chile", "Japan", "Nepal", "Fiji"])]
    fake_llm.ideas_photo = "places/made-up/photos/x"
    ideas = StarterIdeaService(PlacesService(db_session)).generate(Taste())
    assert len(ideas) == MAX_IDEAS
    assert all(i.photo_name is None for i in ideas)


def test_cached_city_ideas_are_not_served(client, auth, db_session):
    user = client.get("/me", headers=auth).json()
    taste = Taste.model_validate(user["taste"])
    old_key = f"open:ideas:{user['id']}:{hashlib.sha256(taste.model_dump_json().encode()).hexdigest()[:16]}"
    old = StarterIdea(title="Slow food week", destination="Lisbon, Portugal", pitch="You love food")
    PlacesService(db_session)._store(old_key, {"ideas": [old.model_dump(mode="json")]})

    assert [i["destination"] for i in client.get("/starter-ideas", headers=auth).json()] == ["Japan"]


@pytest.mark.parametrize("country", ["Jordan", "Georgia"])
def test_picking_a_suggested_country_leads_to_plans_of_that_country(client, auth, fake_llm, country):
    fake_llm.ideas = [("A week away", country)]
    trip = new_trip(client, auth)
    [idea] = say(client, auth, trip["id"], "surprise me")["options"]["ideas"]
    fake_llm.decisions.append(decision(destination=idea["destination"], notes=[mood("relaxed", "relaxed")],
                                       party=Party.COUPLE, duration_days=4))

    turn = say(client, auth, trip["id"], f"Let's go to {idea['destination']}, relaxed, with my partner, 4 days")

    assert turn["trip"]["destination_name"] == country
    assert len(turn["drafts"]) == 3
