import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["ALLOW_DEV_LOGIN"] = "true"

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
from app.services import gazetteer, place_library
from app.services.draft_service import DraftPlan, PlanDay, PlanStop
from app.services.llm_client import LLMClient
from app.services.ranking_service import Ranking, RankedItem

settings.ALLOW_DEV_LOGIN = True

VIEWPORT = {"low": {"latitude": 1.0, "longitude": 1.0}, "high": {"latitude": 2.0, "longitude": 2.0}}

# Per category: (name, type label) — stays cover all three estimated tiers; gems are things to do.
FAKE_PLACES = {
    "stay": [("Sunny Hostel", "Hostel"), ("City Hotel", "3-star hotel"), ("Grand Palace Resort", "Resort Hotel")],
    "eat": [("Veg Corner", "Vegetarian Restaurant"), ("Fish House", "Seafood Restaurant"), ("Old Tavern", "Restaurant")],
    "do": [("Castle", "Tourist Attraction"), ("Museum", "Museum"), ("Viewpoint", "Scenic Point")],
    "gem": [("Tiny Bar", "Bar"), ("Secret Garden", "Garden"), ("Tile Shop", "Store")],
}


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
        self.ideas_photo = None
        self.ideas = [("Slow food week", "Lisbon, Portugal"), ("Nowhere", "Atlantis"), ("Temples and ramen", "Japan")]
        self.decisions: list[TurnDecision] = []
        self.prompts: list[tuple[str, str]] = []
        self.edits: list[dict] = []

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
            return StarterIdeas(ideas=[StarterIdea(title=t, destination=d, pitch="It fits", photo_name=self.ideas_photo)
                                       for t, d in self.ideas])
        raise AssertionError(f"unexpected schema {schema}")


class FakePlaces:
    """Stands in for the open sources: the name lookup and the one-off load of a destination's places."""

    def __init__(self):
        self.resolved: list[str] = []
        self.loads: list[dict] = []
        self.library: dict | None = None  # set to replace what a load returns

    def find_destination(self, name, near=None):
        self.resolved.append(name)
        if "atlantis" in name.lower():
            return None
        name = "South Korea" if "korea" in name.lower() else name.split(",")[0].strip().title()
        return {"place_id": f"Q-{name}", "name": name, "formatted_address": name, "kind": "city",
                "viewport": {**VIEWPORT, "id": f"Q-{name}"}, "photo_name": None, "code": "XX", "article": None,
                "centre": [1.5, 1.5]}

    def build(self, area, releases):
        self.loads.append(area)
        if self.library is not None:
            return {"photos": {}, **self.library}
        places = [
            {"id": f"{tag}-{i}", "name": name, "category": "do" if tag == "gem" else tag, "kind": label,
             "text": None, "lat": 1.5, "lng": 1.5, "area": "Old Town", "guide": None, "tier": None,
             "score": 1.0 - i / 10 - (tag == "gem") / 2}
            for tag, rows in FAKE_PLACES.items() for i, (name, label) in enumerate(rows)
        ]
        return {"places": places, "hubs": [], "photos": {}, "release": "test", "partial": []}


@pytest.fixture
def fake_llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(LLMClient, "generate_structured", staticmethod(fake))
    return fake


@pytest.fixture
def fake_places(monkeypatch):
    fake = FakePlaces()
    monkeypatch.setattr(gazetteer, "find_destination", fake.find_destination)
    monkeypatch.setattr(place_library, "build", fake.build)
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
