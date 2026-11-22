import hashlib

import pytest

from app.schemas.starter_idea import StarterIdea
from app.schemas.taste import Taste
from app.schemas.trip_profile import Party
from app.services.places_service import PlacesService, PlacesUnavailableError
from app.services.starter_idea_service import StarterIdeaService
from tests.v1.test_api import decision, mood, new_trip, say


def suggested(db_session, fake_llm, *names):
    fake_llm.ideas = [(f"Trip {i}", name) for i, name in enumerate(names)]
    return [idea.destination for idea in StarterIdeaService(PlacesService(db_session)).generate(Taste())]


@pytest.mark.parametrize("city", ["Lisbon", "Lisbon, Portugal", "Panama City"])
def test_a_city_from_the_model_is_dropped(db_session, fake_llm, fake_places, city):
    assert suggested(db_session, fake_llm, city, "Japan") == ["Japan"]


@pytest.mark.parametrize("country", ["Georgia", "Singapore", "Vatican City", "Jordan"])
def test_a_country_is_kept(db_session, fake_llm, fake_places, country):
    assert suggested(db_session, fake_llm, country) == [country]
    # The bare name is ambiguous: "Georgia" is a US state from there, "Jordan" a shoe shop from here.
    assert any(b["textQuery"] == f"{country} country" and b.get("includedType") == "country"
               for b in fake_places.bodies)


def test_a_country_is_suggested_once_under_googles_name(db_session, fake_llm, fake_places):
    assert suggested(db_session, fake_llm, "Turkey", "Türkiye", "Japan", "japan") == ["Türkiye", "Japan"]


def test_unverified_ideas_are_never_shown_when_places_is_down(client, auth, fake_llm, monkeypatch):
    def down(*args):
        raise PlacesUnavailableError("quota")

    monkeypatch.setattr(PlacesService, "_post", down)

    assert client.get("/starter-ideas", headers=auth).json() == []

    trip = new_trip(client, auth)
    turn = say(client, auth, trip["id"], "surprise me")
    assert turn.get("options") is None
    assert "couldn't reach" in turn["reply"]


def test_cached_city_ideas_are_not_served(client, auth, db_session):
    user = client.get("/me", headers=auth).json()
    taste = Taste.model_validate(user["taste"])
    old_key = f"ideas:{user['id']}:{hashlib.sha256(taste.model_dump_json().encode()).hexdigest()[:16]}"
    old = StarterIdea(title="Slow food week", destination="Lisbon, Portugal", pitch="You love food")
    PlacesService(db_session)._store(old_key, {"ideas": [old.model_dump(mode="json")]})

    assert [i["destination"] for i in client.get("/starter-ideas", headers=auth).json()] == ["Japan"]


def test_picking_a_suggested_country_leads_to_plans(client, auth, fake_llm):
    fake_llm.ideas = [("Desert nights", "Jordan")]
    trip = new_trip(client, auth)
    [idea] = say(client, auth, trip["id"], "surprise me")["options"]["ideas"]
    fake_llm.decisions.append(decision(destination=idea["destination"], notes=[mood("relaxed", "relaxed")],
                                       party=Party.COUPLE, duration_days=4))

    turn = say(client, auth, trip["id"], f"Let's go to {idea['destination']}, relaxed, with my partner, 4 days")

    assert turn["trip"]["destination_name"] == "Jordan"
    assert len(turn["drafts"]) == 3


@pytest.mark.parametrize("typed, found", [("Lisbon", "Lisbon"), ("Panama City", "Panama City")])
def test_a_typed_city_still_resolves_to_that_city(db_session, fake_places, typed, found):
    assert PlacesService(db_session).resolve_destination(typed)["name"] == found


def test_a_typed_place_nobody_knows_does_not_become_a_similar_country(db_session, monkeypatch):
    def only_finds_a_country(self, body, mask):
        if not body["textQuery"].endswith(" country"):
            return []
        return [{"id": "p", "displayName": {"text": "Panama"}, "viewport": {"low": {}, "high": {}}, "types": ["country"]}]

    monkeypatch.setattr(PlacesService, "_post", only_finds_a_country)

    assert PlacesService(db_session).resolve_destination("Panama Citty") is None
