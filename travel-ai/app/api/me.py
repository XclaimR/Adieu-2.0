import hashlib

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.database.models.user import UserModel
from app.database.session import get_db
from app.repositories.user_repository import UserRepository
from app.schemas.api import TasteRequest, UserOut
from app.schemas.starter_idea import StarterIdea
from app.schemas.taste import Vibe
from app.services.places_service import PlacesService
from app.services.starter_idea_service import StarterIdeaService
from app.services.taste_service import VIBES, TasteService

router = APIRouter(tags=["me"])


@router.get("/me", response_model=UserOut)
def me(user: UserModel = Depends(current_user)):
    return UserRepository.to_out(user)


@router.get("/vibes", response_model=list[Vibe])
def vibes():
    return VIBES


@router.post("/me/taste", response_model=UserOut)
def set_taste(request: TasteRequest, user: UserModel = Depends(current_user), db: Session = Depends(get_db)):
    repo = UserRepository(db)
    repo.set_taste(user, TasteService.set_vibes(repo.taste(user), request.vibes))
    return repo.to_out(user)


@router.get("/starter-ideas", response_model=list[StarterIdea])
def starter_ideas(user: UserModel = Depends(current_user), db: Session = Depends(get_db)):
    """Home-screen ideas. Cached per user and taste for a day: each set costs an LLM call and destination lookups."""
    taste = UserRepository.taste(user)
    places = PlacesService(db)
    key = f"open:ideas:{user.id}:{hashlib.sha256(taste.model_dump_json().encode()).hexdigest()[:16]}"
    cached = places._cached(key, ttl_hours=24)
    if cached is not None:
        return cached["ideas"]
    ideas = StarterIdeaService(places).generate(taste)
    if ideas:
        places._store(key, {"ideas": [i.model_dump(mode="json") for i in ideas]})
    return ideas
