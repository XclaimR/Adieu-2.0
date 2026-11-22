import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["ALLOW_DEV_LOGIN"] = "true"
os.environ["GOOGLE_PLACES_API_KEY"] = "test-key"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.database.models  # noqa: F401
from app.core.config import settings
from app.database.base import Base
from app.database.session import get_db, get_session_factory
from app.main import app
from app.schemas.starter_idea import StarterIdea, StarterIdeas
from app.schemas.turn import TurnAction, TurnDecision
from app.services import places_service
from app.services.draft_service import DraftPlan, PlanDay, PlanStop
from app.services.llm_client import LLMClient
from app.services.places_service import PlacesService
from app.services.ranking_service import Ranking, RankedItem

settings.ALLOW_DEV_LOGIN = True
settings.GOOGLE_PLACES_API_KEY = "test-key"

VIEWPORT = {"low": {"latitude": 1.0, "longitude": 1.0}, "high": {"latitude": 2.0, "longitude": 2.0}}

# Per category: (name, type label) — stays cover all three price tiers.
FAKE_PLACES = {
    "stay": [("Sunny Hostel", "Hostel"), ("City Hotel", "Hotel"), ("Grand Palace Resort", "Resort Hotel")],
    "eat": [("Veg Corner", "Vegetarian Restaurant"), ("Fish House", "Seafood Restaurant"), ("Old Tavern", "Restaurant")],
    "do": [("Castle", "Tourist Attraction"), ("Museum", "Museum"), ("Viewpoint", "Scenic Point")],
    "gem": [("Tiny Bar", "Bar"), ("Secret Garden", "Garden"), ("Tile Shop", "Store")],
}
CITIES = ("lisbon", "panama city")
# What Google returned for these on 2026-11-22: another name for the country.
COUNTRY_NAMES = {"turkey": "Türkiye", "panama city": "Panama"}


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)


@pytest.fixture
def db_session(session_factory):
    session = session_factory()
    yield session
    session.close()


class FakeLLM:
    """Stands in for the model. Tests queue TurnDecisions; other schemas get canned answers."""

    def __init__(self):
        self.decisions: list[TurnDecision] = []
        self.prompts: list[tuple[str, str]] = []
        self.edits: list[dict] = []
        self.ideas = [("Slow food week", "Lisbon, Portugal"), ("Nowhere", "Atlantis"), ("Temples and ramen", "Japan")]

    def __call__(self, prompt, schema, history=None, system=None, model=None):
        self.prompts.append((schema.__name__, prompt))
        if schema.__name__ == "PlanEdits":
            return schema.model_validate({"edits": self.edits})
        if issubclass(schema, TurnDecision):
            return self.decisions.pop(0) if self.decisions else TurnDecision(action=TurnAction.CHAT, reply="ok")
        if schema is Ranking:
            # 99 doesn't exist and must be ignored; 1 is ranked above 0.
            return Ranking(ranked=[RankedItem(ref=1, why="fits you"), RankedItem(ref=99, why="bogus"),
                                   RankedItem(ref=0, why="also fits")])
        if schema is DraftPlan:
            n = sum(1 for name, _ in self.prompts if name == "DraftPlan")
            return DraftPlan(title=f"Plan {n}", summary="A nice trip", stay_ref=9, days=[
                PlanDay(theme="Day one", stops=[PlanStop(place="Castle", ref=0, time_of_day="morning", note="go"),
                                                 PlanStop(place="Veg Corner", ref=3, time_of_day="evening", note="eat"),
                                                 PlanStop(place="Invented Place", ref=999, time_of_day="evening", note="invented")]),
                PlanDay(theme="Day two", stops=[PlanStop(place="Museum", ref=1, time_of_day="morning", note="go"),
                                                 PlanStop(place="Tiny Bar", ref=6, time_of_day="afternoon", note="gem")]),
            ])
        if schema is StarterIdeas:
            return StarterIdeas(ideas=[StarterIdea(title=t, destination=d, pitch="It fits") for t, d in self.ideas])
        raise AssertionError(f"unexpected schema {schema}")


class FakePlaces:
    """Replaces the HTTP call to Places and records every request body."""

    def __init__(self):
        self.bodies: list[dict] = []

    def __call__(self, service, body, field_mask):
        self.bodies.append(body)
        query = body["textQuery"].lower()
        if "atlantis" in query:
            return []
        if field_mask == places_service._RESOLVE_FIELD_MASK:
            as_country = query.endswith(" country")
            place = query.removesuffix(" country").strip()
            wanted = body.get("includedType")
            kind = "locality" if place.startswith(CITIES) else "country"
            if as_country and place == "panama city":
                kind = "country"  # like the real API: "Panama City country" finds Panama
            if place in ("jordan", "iceland") and not as_country:
                # Like the real API: shops, and nothing at all when filtered to a type.
                return [] if wanted else [{"id": "shop", "viewport": VIEWPORT, "types": ["store"]}]
            if wanted and wanted != kind and "korea" not in place:
                return []
            name = place.split(",")[0].title()
            if kind == "country":
                name = COUNTRY_NAMES.get(place, name)
            area = {"id": f"area-{name}", "displayName": {"text": name}, "formattedAddress": name,
                    "viewport": VIEWPORT, "photos": [{"name": f"places/area-{name}/photos/p"}],
                    "types": [kind, "political"]}
            if "korea" in query:
                # Like the real API: a nearby restaurant ranks above the country.
                restaurant = {**area, "id": "streets-of-korea", "displayName": {"text": "Streets Of Korea"},
                              "types": ["restaurant", "food"]}
                return [restaurant] if body.get("includedType") != "country" else [{**area, "displayName": {"text": "South Korea"}}]
            return [area]
        tag = ("stay" if any(w in query for w in ("hotel", "stay", "hostel", "resort")) else
               "eat" if any(w in query for w in ("restaurant", "caf", "food")) else
               "gem" if "hidden" in query else "do")
        return [
            {"id": f"{tag}-{i}", "displayName": {"text": name}, "rating": 4.5 - i / 10, "userRatingCount": 100,
             "primaryTypeDisplayName": {"text": label}, "photos": [{"name": f"places/{tag}-{i}/photos/p"}],
             "location": {"latitude": 1.5, "longitude": 1.5}}
            for i, (name, label) in enumerate(FAKE_PLACES[tag])
        ][:body["maxResultCount"]]


@pytest.fixture
def fake_llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(LLMClient, "generate_structured", staticmethod(fake))
    return fake


@pytest.fixture
def fake_places(monkeypatch):
    fake = FakePlaces()
    monkeypatch.setattr(PlacesService, "_post", lambda self, body, mask: fake(self, body, mask))
    return fake


@pytest.fixture
def client(db_session, session_factory, fake_llm, fake_places):
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def auth(client):
    token = client.post("/auth/dev", json={"email": "a@example.com", "name": "A"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}
