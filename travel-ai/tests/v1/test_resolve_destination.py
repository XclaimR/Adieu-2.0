import pytest

from app.services.places_service import PlacesService
from tests.v1.test_api import decision, new_trip, say


@pytest.mark.parametrize("typed, found", [("Jordan", "Jordan"), ("iceland", "Iceland"), (" Jordan ", "Jordan")])
def test_a_country_google_only_finds_as_a_country_resolves(db_session, fake_places, typed, found):
    assert PlacesService(db_session).resolve_destination(typed)["name"] == found


@pytest.mark.parametrize("name", ["Jordan", "Iceland"])
def test_typing_that_country_sets_the_destination(client, auth, fake_llm, name):
    trip = new_trip(client, auth)
    fake_llm.decisions.append(decision(destination=name))

    turn = say(client, auth, trip["id"], f"I want to go to {name}")

    assert turn["trip"]["destination_name"] == name
    assert "couldn't find" not in turn["reply"]


def test_an_old_cached_miss_is_healed_with_one_search(db_session, fake_places):
    places = PlacesService(db_session)
    places._store("resolve-geo:jordan", {"area": None})

    assert places.resolve_destination("Jordan")["name"] == "Jordan"
    assert places.resolve_destination("Jordan")["name"] == "Jordan"
    assert len(fake_places.bodies) == 1


def test_a_place_nobody_knows_is_searched_for_once(db_session, fake_places):
    places = PlacesService(db_session)
    assert places.resolve_destination("Atlantis") is None
    searches = len(fake_places.bodies)

    assert searches == 5  # the four geographic searches, then the country one
    assert places.resolve_destination("Atlantis") is None
    assert len(fake_places.bodies) == searches


@pytest.mark.parametrize("name", ["Lisbon", "Panama City", "Japan", "Georgia"])
def test_a_name_that_already_resolves_costs_no_extra_search(db_session, fake_places, name):
    assert PlacesService(db_session).resolve_destination(name)["name"] == name
    assert len(fake_places.bodies) == 1


@pytest.mark.parametrize("typed", ["Panama Citty", "Panama City"])
@pytest.mark.parametrize("cached_miss", [False, True])
def test_only_the_exact_name_becomes_a_country(db_session, monkeypatch, typed, cached_miss):
    def only_finds_panama(self, body, mask):
        if not body["textQuery"].endswith(" country"):
            return []
        return [{"id": "p", "displayName": {"text": "Panama"}, "viewport": {"low": {}, "high": {}},
                 "types": ["country"]}]

    monkeypatch.setattr(PlacesService, "_post", only_finds_panama)
    places = PlacesService(db_session)
    if cached_miss:
        places._store(f"resolve-geo:{typed.lower()}", {"area": None})

    assert places.resolve_destination(typed) is None
