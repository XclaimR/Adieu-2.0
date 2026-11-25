import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse, StreamingResponse
from openai import APIConnectionError, APIStatusError, APITimeoutError
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.database.models.trip import TripModel
from app.database.models.user import UserModel
from app.database.session import get_db, get_session_factory
from app.repositories.trip_repository import TripRepository
from app.schemas.api import (
    ActivityActionRequest,
    CreateTripRequest,
    PickActionRequest,
    ReplaceActivityRequest,
    SendMessageRequest,
    TripOut,
    TripSummary,
    TripUpdateResponse,
    TurnResponse,
)
from app.schemas.trip_profile import TripProfile
from app.services.llm_client import LLMOutputError
from app.services.places_service import PlacesService, PlacesUnavailableError
from app.services.swipe_service import SWIPE_CHIP
from app.services.turn_service import VIBE_QUESTIONS
from app.workflows.travel_planner_workflow import TravelPlannerWorkflow, TripActionError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["trips"])


def _trip_or_404(trip_id: str, user: UserModel, db: Session) -> TripModel:
    trip = TripRepository(db).get_for_user(trip_id, user.id)
    if trip is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trip not found")
    return trip


@router.get("/trips", response_model=list[TripSummary])
def list_trips(user: UserModel = Depends(current_user), db: Session = Depends(get_db)):
    return TripRepository(db).list_for_user(user.id)


@router.post("/trips", response_model=TripOut)
def create_trip(request: CreateTripRequest, user: UserModel = Depends(current_user), db: Session = Depends(get_db)):
    """Creates an empty trip. The client sends what the user typed as the first message."""
    repo = TripRepository(db)
    profile = TripProfile()
    trip = repo.create(user.id, request.title or "New trip", profile)
    question, chips = VIBE_QUESTIONS["destination"]
    trip.next_question = question
    trip.state = {"answer_options": [*chips, SWIPE_CHIP]}  # "Just chat" starts here: offer the deck
    repo.add_message(trip.id, "assistant", question)
    db.commit()
    return repo.to_out(trip)


@router.get("/trips/{trip_id}", response_model=TripOut)
def get_trip(trip_id: str, user: UserModel = Depends(current_user), db: Session = Depends(get_db)):
    return TripRepository(db).to_out(_trip_or_404(trip_id, user, db))


@router.post("/trips/{trip_id}/messages", response_model=TurnResponse)
def send_message(
    trip_id: str,
    request: SendMessageRequest,
    user: UserModel = Depends(current_user),
    db: Session = Depends(get_db),
):
    trip = _trip_or_404(trip_id, user, db)
    return TravelPlannerWorkflow(db).process_message(trip, user, request.message.strip())


@router.post("/trips/{trip_id}/messages/stream")
def stream_message(
    trip_id: str,
    request: SendMessageRequest,
    user: UserModel = Depends(current_user),
    session_factory=Depends(get_session_factory),
):
    """Same as /messages, but sends progress, reply, options and drafts as NDJSON lines as they happen."""
    user_id, message = user.id, request.message.strip()

    def events():
        # Request-scoped sessions close before a streaming body finishes, so this one is our own.
        db = session_factory()
        try:
            trip = TripRepository(db).get_for_user(trip_id, user_id)
            if trip is None:
                yield json.dumps({"type": "error", "detail": "Trip not found"}) + "\n"
                return
            owner = db.get(UserModel, user_id)
            for event in TravelPlannerWorkflow(db).run_turn(trip, owner, message):
                yield json.dumps(event) + "\n"
        except (APIConnectionError, APITimeoutError, APIStatusError):
            logger.exception("LLM unavailable during stream")
            yield json.dumps({"type": "error", "detail": "I can't think straight right now — please try again in a moment."}) + "\n"
        except LLMOutputError:
            yield json.dumps({"type": "error", "detail": "The AI gave an unusable answer. Please try again."}) + "\n"
        except Exception:
            logger.exception("Turn failed")
            yield json.dumps({"type": "error", "detail": "Something went wrong. Please try again."}) + "\n"
        finally:
            db.close()

    return StreamingResponse(events(), media_type="application/x-ndjson")


@router.post("/trips/{trip_id}/drafts/{draft_id}/choose", response_model=TripOut)
def choose_draft(trip_id: str, draft_id: str, user: UserModel = Depends(current_user), db: Session = Depends(get_db)):
    trip = _trip_or_404(trip_id, user, db)
    try:
        TravelPlannerWorkflow(db).choose_draft(trip, user, draft_id)
    except TripActionError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    return TripRepository(db).to_out(trip)


@router.post("/trips/{trip_id}/picks/{place_id}", response_model=TripUpdateResponse)
def pick_action(
    trip_id: str,
    place_id: str,
    request: PickActionRequest,
    user: UserModel = Depends(current_user),
    db: Session = Depends(get_db),
):
    trip = _trip_or_404(trip_id, user, db)
    try:
        options = TravelPlannerWorkflow(db).pick_action(trip, user, place_id, request.action)
    except TripActionError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    except PlacesUnavailableError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Places service unavailable")
    return TripUpdateResponse(trip=TripRepository(db).to_out(trip), options=options)


@router.delete("/trips/{trip_id}/notes/{note_id}", response_model=TripOut)
def remove_note(trip_id: str, note_id: str, user: UserModel = Depends(current_user), db: Session = Depends(get_db)):
    trip = _trip_or_404(trip_id, user, db)
    try:
        TravelPlannerWorkflow(db).remove_note(trip, note_id)
    except TripActionError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    return TripRepository(db).to_out(trip)


@router.post("/trips/{trip_id}/itinerary/days/{day_number}/activities/{index}", response_model=TripUpdateResponse)
def activity_action(
    trip_id: str,
    day_number: int,
    index: int,
    request: ActivityActionRequest,
    user: UserModel = Depends(current_user),
    db: Session = Depends(get_db),
):
    trip = _trip_or_404(trip_id, user, db)
    try:
        options = TravelPlannerWorkflow(db).activity_action(trip, user, day_number, index, request.action)
    except TripActionError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    except PlacesUnavailableError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Places service unavailable")
    return TripUpdateResponse(trip=TripRepository(db).to_out(trip), options=options)


@router.post("/trips/{trip_id}/itinerary/days/{day_number}/activities/{index}/replace", response_model=TripOut)
def replace_activity(
    trip_id: str,
    day_number: int,
    index: int,
    request: ReplaceActivityRequest,
    user: UserModel = Depends(current_user),
    db: Session = Depends(get_db),
):
    trip = _trip_or_404(trip_id, user, db)
    try:
        TravelPlannerWorkflow(db).replace_activity(trip, day_number, index, request.place_id)
    except TripActionError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    return TripRepository(db).to_out(trip)


def _photo(photo_name: str, db: Session) -> dict:
    try:
        return PlacesService(db).photo(photo_name)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid photo name")
    except PlacesUnavailableError:
        # Also every photo saved from Google: the browser shows the placeholder.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Photo unavailable")


@router.get("/photos/{photo_name:path}")
def photo(photo_name: str, db: Session = Depends(get_db)):
    """Redirects to an openly licensed Wikimedia Commons photo. Unauthenticated so <img> tags work."""
    return RedirectResponse(_photo(photo_name, db)["url"], status_code=status.HTTP_302_FOUND)


@router.get("/photo-credits/{photo_name:path}")
def photo_credit(photo_name: str, db: Session = Depends(get_db)):
    """Who took the photo and its licence, shown on the photo."""
    found = _photo(photo_name, db)
    return {"by": found["by"], "licence": found["licence"], "source": found["source"]}
