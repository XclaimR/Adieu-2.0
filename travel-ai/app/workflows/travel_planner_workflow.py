import logging
from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.database.models.trip import TripModel
from app.database.models.user import UserModel
from app.repositories.trip_repository import TripRepository
from app.repositories.user_repository import UserRepository
from app.schemas.api import TurnResponse
from app.schemas.itinerary import Itinerary
from app.schemas.place import OptionSet, Pick, PickCategory, SwapTarget
from app.schemas.taste import Taste, TasteUpdates
from app.schemas.trip_profile import TripProfile
from app.schemas.turn import OptionsKind, OptionsRequest, TurnAction
from app.services.draft_service import DEFAULT_DAYS, DraftService
from app.services.options_service import OptionsService
from app.services.places_service import PlacesService, PlacesUnavailableError, tier_rank
from app.services.trip_profile_service import TripProfileService
from app.services.turn_service import SHOW_PLANS, SHOW_PLANS_TEXT, VIBE_QUESTIONS, TurnService

logger = logging.getLogger(__name__)

RESET_PHRASES = ("start over", "reset")
DRAFT_COUNT = 3

Event = dict


def _same_place(a: str | None, b: str | None) -> bool:
    return (a or "").strip().lower() == (b or "").strip().lower()


def _progress(text: str) -> Event:
    return {"type": "progress", "text": text}


class TripActionError(ValueError):
    pass


class TravelPlannerWorkflow:
    """Runs one conversational turn as a stream of events, plus the card and itinerary actions."""

    def __init__(self, db: Session):
        self.db = db
        self.trips = TripRepository(db)
        self.users = UserRepository(db)
        self.places = PlacesService(db)
        self.options = OptionsService(self.places)
        self.drafter = DraftService(self.places)

    # ---------- conversation ----------

    def process_message(self, trip: TripModel, user: UserModel, message: str) -> TurnResponse:
        """Non-streaming version: runs the turn and collects its events."""
        result: dict = {"drafts": []}
        for event in self.run_turn(trip, user, message):
            if event["type"] == "reply":
                result.update(reply=event["text"], next_question=event["next_question"],
                              answer_options=event["answer_options"])
            elif event["type"] == "options":
                result["options"] = event["options"]
            elif event["type"] == "draft":
                result["drafts"].append(event["draft"])
            elif event["type"] == "note":
                result["reply"] = f"{result['reply']} {event['text']}"
            elif event["type"] == "error":
                raise RuntimeError(event["detail"])
        return TurnResponse(**result, trip=self.trips.to_out(trip))

    def run_turn(self, trip: TripModel, user: UserModel, message: str) -> Iterator[Event]:
        shown_message = SHOW_PLANS_TEXT if message == SHOW_PLANS else message
        if any(p in message.lower() for p in RESET_PHRASES):
            yield from self._reset(trip, message)
            return

        yield _progress("Thinking…")
        profile = self.trips.profile(trip)
        taste = self.users.taste(user)
        state = dict(trip.state or {})
        itinerary = self.trips.itinerary(trip)

        decision = TurnService.decide(
            shown_message, self.trips.history(trip.id), profile, taste, self._context(trip, itinerary)
        )

        # "Surprise me" means the user wants ideas, not a destination the model picked on its own.
        surprise = TurnService.wants_surprise(message)
        if surprise:
            decision.profile_updates.destination = None

        new_profile = TripProfileService.merge(profile, decision.profile_updates, message=shown_message)
        note = ""
        if new_profile.destination and not _same_place(profile.destination, new_profile.destination):
            yield _progress(f"Looking up {new_profile.destination}…")
            area = self._resolve(new_profile.destination)
            if area is None:
                note = f" I couldn't find “{new_profile.destination}” on the map — could you name a city, region or country?"
                new_profile.destination = profile.destination
            else:
                new_profile.destination = area["name"]
                trip.destination_area = area
                trip.title = area["name"]
                trip.drafts, trip.itinerary, trip.panel = [], None, None
                itinerary = None

        self.users.set_taste(user, TripProfileService.merge_taste(taste, decision.taste_updates))
        taste = self.users.taste(user)

        progressed = new_profile != profile
        plan = TurnService.plan(
            decision, new_profile,
            message=message,
            asked=state.get("asked_fields", []),
            questions_streak=state.get("questions_streak", 0),
            has_drafts=bool(trip.drafts),
            has_itinerary=itinerary is not None,
            surprise=surprise,
        )

        if plan.action == TurnAction.OFFER_DRAFTS and not new_profile.duration_days:
            new_profile.duration_days = DEFAULT_DAYS
        trip.profile = new_profile.model_dump(mode="json")

        reply = TurnService.strip_questions(decision.reply, plan.next_question) + note
        if plan.action == TurnAction.OFFER_DRAFTS:
            reply = reply.rstrip() + f" Here are {DRAFT_COUNT} ways your trip could look — pick the one that feels right."
        yield {"type": "reply", "text": reply, "next_question": plan.next_question,
               "answer_options": plan.answer_options}

        # Save the exchange before the slow work (drafts take minutes), so anything the user does
        # meanwhile, like choosing the first draft, lands after it in the conversation.
        asked = list(dict.fromkeys(state.get("asked_fields", []) + ([plan.asked_field] if plan.asked_field else [])))
        streak = state.get("questions_streak", 0) + 1 if plan.action == TurnAction.ASK and not progressed else 0
        trip.state = {**state, "asked_fields": asked, "questions_streak": streak, "answer_options": plan.answer_options}
        trip.next_question = plan.next_question
        self.trips.add_message(trip.id, "user", shown_message)
        answer = self.trips.add_message(trip.id, "assistant", reply if not plan.next_question else f"{reply}\n\n{plan.next_question}")
        self.db.commit()

        try:
            if plan.action == TurnAction.SHOW_OPTIONS:
                yield from self._show_options(trip, new_profile, taste, plan.options_request)
            elif plan.action == TurnAction.OFFER_DRAFTS:
                yield from self._offer_drafts(trip, new_profile, taste)
            elif plan.action == TurnAction.EDIT_ITINERARY:
                yield _progress("Updating your plan…")
                updated = self.drafter.edit(itinerary, decision.edit_request, new_profile, taste,
                                            self._viewport(trip), self._skipped(trip))
                trip.itinerary = updated.model_dump(mode="json")
        except PlacesUnavailableError as e:
            logger.warning("Places unavailable: %s", e)
            extra = " (I couldn't reach the places service just now.)"
            reply += extra
            answer.content = reply if not plan.next_question else f"{reply}\n\n{plan.next_question}"
            yield {"type": "note", "text": extra.strip()}

        self.db.commit()
        # Another request (choosing a draft) may have changed the trip while we worked.
        self.db.refresh(trip)
        yield {"type": "done", "trip": self.trips.to_out(trip).model_dump(mode="json")}

    def _reset(self, trip: TripModel, message: str) -> Iterator[Event]:
        question, chips = VIBE_QUESTIONS["destination"]
        trip.profile = TripProfile().model_dump(mode="json")
        trip.destination_area = trip.panel = trip.itinerary = None
        trip.drafts = []
        trip.title = "New trip"
        trip.state = {"answer_options": chips}
        trip.next_question = question
        self.trips.add_message(trip.id, "user", message)
        self.trips.add_message(trip.id, "assistant", f"Fresh start!\n\n{question}")
        self.db.commit()
        yield {"type": "reply", "text": "Fresh start!", "next_question": question, "answer_options": chips}
        yield {"type": "done", "trip": self.trips.to_out(trip).model_dump(mode="json")}

    def _context(self, trip: TripModel, itinerary: Itinerary | None) -> str:
        liked = [p.name for p in self.trips.feedback(trip.id, "liked")]
        lines = [f"Places they liked: {', '.join(liked) or 'none yet'}"]
        if trip.drafts and not itinerary:
            lines.append("Draft plans were offered: " + ", ".join(d["title"] for d in trip.drafts))
        if itinerary:
            lines.append("Their chosen itinerary: " + "; ".join(
                f"Day {d.day_number}: " + ", ".join(a.name for a in d.activities) for d in itinerary.days))
        return "\n".join(lines)

    def _resolve(self, destination: str) -> dict | None:
        try:
            return self.places.resolve_destination(destination)
        except PlacesUnavailableError:
            # Without Places we can't verify; keep the name so the conversation can continue.
            return {"place_id": None, "name": destination, "formatted_address": None, "viewport": None, "photo_name": None}

    @staticmethod
    def _viewport(trip: TripModel) -> dict | None:
        return (trip.destination_area or {}).get("viewport")

    def _skipped(self, trip: TripModel) -> set[str]:
        return {p.place_id for p in self.trips.feedback(trip.id, "skipped")}

    def _show_options(self, trip: TripModel, profile: TripProfile, taste: Taste, request: OptionsRequest) -> Iterator[Event]:
        label = "destination ideas" if request.kind == OptionsKind.DESTINATIONS else (request.query or request.kind.value.replace("_", " "))
        yield _progress(f"Finding {label}…")
        option_set = self.options.build(request.kind, request.query, profile, taste, self._viewport(trip), self._skipped(trip))
        trip.panel = option_set.model_dump(mode="json")
        self.db.commit()
        yield {"type": "options", "options": trip.panel}

    def _offer_drafts(self, trip: TripModel, profile: TripProfile, taste: Taste) -> Iterator[Event]:
        yield _progress(f"Gathering places in {profile.destination}…")
        trip.drafts = []
        for i, draft in enumerate(self.drafter.generate(profile, taste, self._viewport(trip), self._skipped(trip))):
            trip.drafts = [*trip.drafts, draft.model_dump(mode="json")]
            self.db.commit()
            yield {"type": "draft", "index": i, "draft": trip.drafts[-1]}
            if i + 1 < DRAFT_COUNT:
                yield _progress(f"Drafting plan {i + 2} of {DRAFT_COUNT}…")

    # ---------- actions ----------

    def choose_draft(self, trip: TripModel, user: UserModel, draft_id: str) -> None:
        draft = next((d for d in self.trips.drafts(trip) if d.id == draft_id), None)
        if draft is None:
            raise TripActionError("Draft not found")
        trip.itinerary = draft.itinerary.model_dump(mode="json")
        # Which version they picked says something about their taste.
        self.users.set_taste(user, TripProfileService.merge_taste(self.users.taste(user), TasteUpdates(likes=[draft.title])))
        question = f"Great choice — “{draft.title}” it is! Anything you'd like to change?"
        trip.next_question = question
        trip.state = {**(trip.state or {}), "answer_options": ["More relaxed", "More food spots", "Add a hidden gem", "Looks perfect"]}
        self.trips.add_message(trip.id, "assistant", question)
        self.db.commit()

    def pick_action(self, trip: TripModel, user: UserModel, place_id: str, action: str) -> OptionSet | None:
        pick = self.trips.find_pick(trip, place_id)
        if pick is None:
            raise TripActionError("Place not found")
        profile = self.trips.profile(trip)
        taste = self.users.taste(user)

        if action == "unlike":
            self.trips.set_feedback(trip.id, pick, "none")
        elif action == "skip":
            self.trips.set_feedback(trip.id, pick, "skipped")
        elif action == "add":
            itinerary = self.trips.itinerary(trip) or Itinerary(destination=profile.destination or "", days=[])
            trip.itinerary = DraftService.place_in_itinerary(itinerary, pick).model_dump(mode="json")
            self.db.commit()
        elif action in ("like", "too_pricey"):
            if action == "like":
                self.trips.set_feedback(trip.id, pick, "liked")
                likes = [x for x in (pick.type_label,) if x]
                self.users.set_taste(user, TripProfileService.merge_taste(taste, TasteUpdates(likes=likes)))
            over_budget = (
                pick.category == PickCategory.STAY and profile.budget_level is not None
                and tier_rank(pick.tier) > tier_rank(profile.budget_level.value)
            )
            # A liked stay over budget, or one the user called too pricey: find similar ones that fit.
            if action == "too_pricey" or over_budget:
                option_set = self.options.similar_cheaper(pick, profile, taste, self._skipped(trip))
                trip.panel = option_set.model_dump(mode="json")
                self.db.commit()
                return option_set
        return None

    def activity_action(self, trip: TripModel, user: UserModel, day_number: int, index: int, action: str) -> OptionSet | None:
        itinerary = self.trips.itinerary(trip)
        day = next((d for d in itinerary.days if d.day_number == day_number), None) if itinerary else None
        if day is None or not 0 <= index < len(day.activities):
            raise TripActionError("Activity not found")
        activity = day.activities[index]

        if action == "remove":
            day.activities.pop(index)
            trip.itinerary = itinerary.model_dump(mode="json")
            self.db.commit()
            return None

        used = {a.place_id for d in itinerary.days for a in d.activities if a.place_id}
        option_set = self.options.swap_options(
            SwapTarget(day_number=day_number, index=index), activity.name, activity.category,
            self.trips.profile(trip), self.users.taste(user), self._viewport(trip), used | self._skipped(trip),
        )
        trip.panel = option_set.model_dump(mode="json")
        self.db.commit()
        return option_set

    def replace_activity(self, trip: TripModel, day_number: int, index: int, place_id: str) -> None:
        itinerary = self.trips.itinerary(trip)
        pick: Pick | None = self.trips.find_pick(trip, place_id)
        day = next((d for d in itinerary.days if d.day_number == day_number), None) if itinerary else None
        if pick is None or day is None or not 0 <= index < len(day.activities):
            raise TripActionError("Can't swap that")
        old = day.activities[index]
        day.activities[index] = old.model_copy(update={
            "name": pick.name, "category": pick.category.value, "description": pick.why,
            "place_id": pick.place_id, "photo_name": pick.photo_name, "maps_url": pick.maps_url,
        })
        trip.itinerary = itinerary.model_dump(mode="json")
        trip.panel = None
        self.db.commit()

