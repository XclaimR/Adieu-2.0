"""The swipe deck (Ticket #21). Pure code, no model calls: the cards' effects are fixed in swipe_service."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.api.trips import _trip_or_404
from app.database.models.user import UserModel
from app.database.session import get_db
from app.repositories.trip_repository import TripRepository
from app.schemas.api import TripOut
from app.services import swipe_service as swipes

router = APIRouter(tags=["swipes"])


class SwipeCardOut(BaseModel):
    id: str
    title: str
    photo: str  # served by the web app from public/swipe/
    credit: dict


class DeckOut(BaseModel):
    card: SwipeCardOut | None  # None: enough said, or the limit reached
    answered: int
    limit: int = swipes.LIMIT


class SwipeRequest(BaseModel):
    card_id: str
    answer: Literal["right", "left", "skip"]


def _deck(trip) -> DeckOut:
    answers = (trip.state or {}).get("swipes", {})
    card = swipes.next_card(TripRepository.personality(trip), answers)
    out = card and SwipeCardOut(id=card.id, title=card.title, photo=f"/swipe/{card.id}.jpg", credit=card.credit)
    return DeckOut(card=out, answered=len(answers))


@router.get("/trips/{trip_id}/swipes", response_model=DeckOut)
def deck(trip_id: str, user: UserModel = Depends(current_user), db: Session = Depends(get_db)):
    return _deck(_trip_or_404(trip_id, user, db))


@router.post("/trips/{trip_id}/swipes", response_model=DeckOut)
def swipe(trip_id: str, request: SwipeRequest, user: UserModel = Depends(current_user), db: Session = Depends(get_db)):
    trip = _trip_or_404(trip_id, user, db)
    state = dict(trip.state or {})
    answers = {**state.get("swipes", {}), request.card_id: request.answer}
    if request.card_id not in swipes.BY_ID or len(answers) > swipes.LIMIT:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown card or deck finished")
    trip.state = {**state, "swipes": answers}
    TripRepository.save_personality(trip, swipes.apply(TripRepository.personality(trip), answers))
    db.commit()
    return _deck(trip)


@router.post("/trips/{trip_id}/swipes/finish", response_model=TripOut)
def finish(trip_id: str, user: UserModel = Depends(current_user), db: Session = Depends(get_db)):
    """Says the one-line summary in the chat; the client then continues as usual ("show me plans")."""
    trip = _trip_or_404(trip_id, user, db)
    repo, state = TripRepository(db), dict(trip.state or {})
    answers = state.get("swipes", {})
    profile = repo.profile(trip)
    if not profile.mood and (found := swipes.moods(answers)):
        trip.profile = profile.model_copy(update={"mood": found}).model_dump(mode="json")
    chips = [c for c in state.get("answer_options", []) if c != swipes.SWIPE_CHIP]
    trip.state = {**state, "swipes_done": True, "answer_options": chips}
    repo.add_message(trip.id, "assistant", swipes.summary(repo.personality(trip)))
    db.commit()
    return repo.to_out(trip)
