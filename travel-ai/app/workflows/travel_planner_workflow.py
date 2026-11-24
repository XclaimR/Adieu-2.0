import logging
import re
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from collections.abc import Iterator

import httpx
from openai import RateLimitError
from sqlalchemy.orm import Session

from app.database.models.trip import MessageModel, TripModel
from app.database.models.user import UserModel
from app.repositories.trip_repository import TripRepository
from app.schemas.activity import default_minutes
from app.schemas.api import TurnResponse
from app.schemas.draft import NoPlans
from app.schemas.itinerary import Itinerary, city_of
from app.schemas.personality import NoteChange, Personality
from app.schemas.place import OptionSet, Pick, PickCategory, SwapTarget
from app.schemas.trip_profile import TripProfile
from app.schemas.turn import OptionsKind, OptionsRequest, TurnAction
from app.services.draft_service import DEFAULT_DAYS, DraftService, NotEnoughPlacesError, NoUsableDraftError
from app.services.options_service import OptionsService
from app.services import swipe_service
from app.services.personality_service import PersonalityService, check_changes, describe_note
from app.services.places_service import PlacesService, PlacesUnavailableError, tier_rank
from app.services.trip_profile_service import TripProfileService
from app.services.turn_service import SHOW_PLANS, SHOW_PLANS_TEXT, VIBE_QUESTIONS, TurnService

logger = logging.getLogger(__name__)

# The whole message must be the command: a reset deletes the plan, and "I need a mental reset" isn't one.
_RESET_COMMAND = re.compile(r"\W*(please\W+)?(start over|reset)(\W+please)?\W*", re.I)
_RESET_QUESTION = ("Are you sure you want to start from scratch? This clears the destination, plans and notes, "
                   "and can't be undone.")
_RESET_ANSWERS = ["Yes, start from scratch", "No, keep my trip"]
# Both must be the whole answer, and "yes?" is not a yes.
_CONFIRM_RESET = re.compile(r"[^\w?]*((yes|yeah|yep|yup)(\W+(please|i\W?m sure|start over|start from scratch|reset))?"
                            r"|sure|i\W?m sure|confirm)[^\w?]*", re.I)
_DECLINE_RESET = re.compile(r"\W*(no(\W+(thanks|keep my trip))?|nope|nah|cancel|never ?mind|don\W?t)\W*", re.I)
DRAFT_COUNT = 3
PLANS_READY = f"Here are {DRAFT_COUNT} ways your trip could look — pick the one that feels right."
# A source that is out for the day says "per day" or "PerDay": trying again right away can't work.
_DAILY_LIMIT = re.compile(r"per ?day", re.I)
UNCHECKED = "I couldn't double-check this, so give it a look."

Event = dict


def _same_place(a: str | None, b: str | None) -> bool:
    return (a or "").strip().lower() == (b or "").strip().lower()


def _progress(text: str) -> Event:
    return {"type": "progress", "text": text}


def _reply(text: str, question: str | None = None, options: list[str] | None = None) -> Event:
    return {"type": "reply", "text": text, "next_question": question, "answer_options": options or []}


def _why_no_plans(error: Exception, destination: str) -> NoPlans:
    cause = error.__cause__ if isinstance(error, NoUsableDraftError) else error
    no_place_data = isinstance(cause, (PlacesUnavailableError, httpx.HTTPError))
    out_for_today = isinstance(cause, (PlacesUnavailableError, RateLimitError)) and bool(_DAILY_LIMIT.search(str(cause)))
    if no_place_data or out_for_today:
        what = "Place data" if no_place_data else "The AI planner"
        when = "Please come back later." if out_for_today else "Please try again in a moment."
        return NoPlans(retry_now=not out_for_today,
                       reason=f"{what} is unavailable for now, so I can't make plans. {when}")
    if isinstance(error, NotEnoughPlacesError):
        return NoPlans(reason=f"I couldn't find enough places in {destination} to build a plan. "
                              "Try naming a town or a bigger area there.")
    if isinstance(error, NoUsableDraftError):
        if cause:
            return NoPlans(reason="The AI planner couldn't write your plans just now. Please try again in a moment.")
        return NoPlans(reason=f"The plans I wrote didn't stick to real places in {destination}, so I threw them out. "
                              "Please try again.")
    return NoPlans(reason="Something went wrong while I was making your plans. Please try again.")


class TripActionError(ValueError):
    pass


class TravelPlannerWorkflow:
    """Runs one conversational turn as a stream of events, plus the card and itinerary actions."""

    def __init__(self, db: Session):
        self.db = db
        self.trips = TripRepository(db)
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
        state = dict(trip.state or {})
        before_reset = state.pop("before_reset", None)
        # A yes counts only while that question is the current one: choosing a plan, for one, replaces it.
        confirming = before_reset is not None and trip.next_question == _RESET_QUESTION
        if confirming and _CONFIRM_RESET.fullmatch(message):
            yield from self._reset(trip, message)
            return
        if confirming and _DECLINE_RESET.fullmatch(message):
            trip.state = state
            yield from self._answer(trip, message, "Okay, I kept your trip as it is.", *before_reset)
            return
        if _RESET_COMMAND.fullmatch(message):
            if not confirming:  # asked twice, a "no" still returns to the question before the first
                before_reset = [trip.next_question, state.get("answer_options", [])]
            trip.state = {**state, "before_reset": before_reset}
            yield from self._answer(trip, message, "I haven't deleted anything yet.", _RESET_QUESTION, _RESET_ANSWERS)
            return
        not_reset = "I didn't start over. " if confirming else ""

        yield _progress("Thinking…")
        profile = self.trips.profile(trip)
        before = self.trips.personality(trip)
        itinerary = self.trips.itinerary(trip)

        history = self.trips.history(trip.id)
        if not_reset:
            # Or the model reads "ok" as a yes to the question above and talks, or edits, as if it had reset.
            history.append({"role": "assistant", "content": not_reset.strip()})
        decision = TurnService.decide(
            shown_message, history, profile, self._context(trip, itinerary, before),
            itinerary,
        )
        decision.note_changes = check_changes(decision.note_changes, shown_message, before, itinerary)
        if any(c.target != "trip" for c in decision.note_changes):
            decision.profile_updates.budget_level = None  # "a cheaper hotel" is not a cheaper trip

        # "Surprise me" means the user wants ideas, not a destination the model picked on its own.
        surprise = TurnService.wants_surprise(message)
        if surprise:
            decision.profile_updates.destination = None

        new_profile = TripProfileService.merge(profile, decision.profile_updates, message=shown_message)
        new_profile.preferences = []  # `before` already holds them as notes
        personality = PersonalityService.update(before, new_profile, decision.note_changes)
        new_profile.mood = PersonalityService.moods(profile.mood, before, personality)
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
                personality = PersonalityService.detach(personality)

        self.trips.save_personality(trip, personality)

        progressed = new_profile != profile or personality.notes != before.notes
        plan = TurnService.plan(
            decision, new_profile,
            message=message,
            asked=state.get("asked_fields", []),
            questions_streak=state.get("questions_streak", 0),
            has_drafts=bool(trip.drafts),
            has_itinerary=itinerary is not None,
            surprise=surprise,
        )

        mood_again = plan.asked_field == "mood" and "mood" in state.get("asked_fields", [])
        if swipe_service.offer(message, decision.user_stuck or surprise, mood_again, state, itinerary is not None):
            plan.answer_options = [*plan.answer_options[:4], swipe_service.SWIPE_CHIP]
        if plan.action == TurnAction.OFFER_DRAFTS and not new_profile.duration_days:
            new_profile.duration_days = DEFAULT_DAYS
        trip.profile = new_profile.model_dump(mode="json")

        reply = not_reset + TurnService.strip_questions(decision.reply, plan.next_question) + note
        if plan.action == TurnAction.OFFER_DRAFTS:
            # The model talks as if the plans exist ("I've put together a few"), so its words wait for the first draft.
            reply_with_plans = f"{reply.rstrip()} {PLANS_READY}"
            reply = f"{not_reset}Let me put some plans together for {new_profile.destination}.{note}"
        yield _reply(reply, plan.next_question, plan.answer_options)

        # Save the exchange before the slow work (drafts take minutes), so anything the user does
        # meanwhile, like choosing the first draft, lands after it in the conversation.
        asked = list(dict.fromkeys(state.get("asked_fields", []) + ([plan.asked_field] if plan.asked_field else [])))
        streak = state.get("questions_streak", 0) + 1 if plan.action == TurnAction.ASK and not progressed else 0
        trip.state = {**state, "asked_fields": asked, "questions_streak": streak, "answer_options": plan.answer_options,
                      "no_plans": None}
        trip.next_question = plan.next_question
        self.trips.add_message(trip.id, "user", shown_message)
        answer = self.trips.add_message(trip.id, "assistant", reply if not plan.next_question else f"{reply}\n\n{plan.next_question}")
        self.db.commit()

        try:
            if plan.action == TurnAction.SHOW_OPTIONS:
                yield from self._show_options(trip, new_profile, plan.options_request, personality)
            elif plan.action == TurnAction.OFFER_DRAFTS:
                yield from self._offer_drafts(trip, new_profile, personality, answer, reply_with_plans)
            elif plan.action == TurnAction.EDIT_ITINERARY:
                yield _progress("Updating your plan…")
                changing = TurnService.to_change(decision)
                updated, warnings, checked = self.drafter.edit(itinerary, changing, decision.edit_request or shown_message, new_profile,
                                                               self._viewport(trip), self._skipped(trip), personality)
                extras = [f"Worth a look: {w.rstrip('.')}." for w in warnings] + ([] if checked else [UNCHECKED])
                if updated == itinerary:
                    extras.insert(0, "I left the plan as it is: I found nothing that needed changing or fitted better.")
                else:
                    trip.itinerary = updated.model_dump(mode="json")
                    self._keep_notes_attached(trip, personality, itinerary, updated)
                if extras:
                    extra = " ".join(extras)
                    answer.content = f"{reply} {extra}" + (f"\n\n{plan.next_question}" if plan.next_question else "")
                    yield {"type": "note", "text": extra}
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
        trip.destination_area = trip.panel = trip.itinerary = trip.personality = None
        trip.drafts = []
        trip.title = "New trip"
        trip.state = {}
        yield from self._answer(trip, message, "Fresh start!", question, chips)

    def _answer(self, trip: TripModel, message: str, text: str, question: str | None, chips: list[str]) -> Iterator[Event]:
        """A whole turn with a fixed reply, no model."""
        trip.state = {**(trip.state or {}), "answer_options": chips}
        trip.next_question = question
        self.trips.add_message(trip.id, "user", message)
        self.trips.add_message(trip.id, "assistant", f"{text}\n\n{question}" if question else text)
        self.db.commit()
        yield _reply(text, question, chips)
        yield {"type": "done", "trip": self.trips.to_out(trip).model_dump(mode="json")}

    def _context(self, trip: TripModel, itinerary: Itinerary | None, personality: Personality) -> str:
        liked = [p.name for p in self.trips.feedback(trip.id, "liked")]
        lines = [f"Places they liked: {', '.join(liked) or 'none yet'}"]
        if trip.drafts and not itinerary:
            lines.append("Draft plans were offered: " + ", ".join(d["title"] for d in trip.drafts))
        lines.append("The trip, with what they told us under what it is about (ids in [] and after 'note'):\n"
                     + PersonalityService.plan_view(itinerary, personality))
        return "\n".join(lines)

    def _keep_notes_attached(self, trip: TripModel, personality: Personality, old: Itinerary, new: Itinerary) -> None:
        """A removed stop's notes move up to its day; a removed day's to the trip."""
        here = set(new.target_ids())
        day_of = {a.id: d.id for d in old.days for a in d.activities if d.id in here}
        moved = [n if n.target in {"trip", *here} else
                 n.model_copy(update={"target": day_of.get(n.target, "trip")}) for n in personality.notes]
        if moved != personality.notes:
            self.trips.save_personality(trip, personality.model_copy(update={"notes": moved}))

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

    def _show_options(self, trip: TripModel, profile: TripProfile, request: OptionsRequest,
                      personality: Personality) -> Iterator[Event]:
        label = "destination ideas" if request.kind == OptionsKind.DESTINATIONS else (request.query or request.kind.value.replace("_", " "))
        yield _progress(f"Finding {label}…")
        option_set = self.options.build(request.kind, request.query, profile, self._viewport(trip),
                                        self._skipped(trip), personality)
        trip.panel = option_set.model_dump(mode="json")
        self.db.commit()
        yield {"type": "options", "options": trip.panel}

    def _offer_drafts(self, trip: TripModel, profile: TripProfile, personality: Personality,
                      answer: MessageModel, reply_with_plans: str) -> Iterator[Event]:
        yield _progress(f"Gathering places in {profile.destination}…")
        previous = self.trips.itinerary(trip) or next((d.itinerary for d in self.trips.drafts(trip)), None)
        trip.drafts = []
        viewport, skipped = self._viewport(trip), self._skipped(trip)
        # Each plan is reviewed in its own thread while the next is written, and shown once its review is done.
        checks = ThreadPoolExecutor(max_workers=DRAFT_COUNT)
        reviews: list[Future] = []
        places = None
        try:
            try:
                for draft in self.drafter.generate(profile, viewport, skipped, personality, previous):
                    if places is None:
                        places = self._review_places(profile, viewport, skipped, personality, draft.itinerary)
                    reviews.append(checks.submit(self.drafter.review, draft, places, profile, personality))
                    yield from self._show_reviewed(trip, reviews, answer, reply_with_plans, wait=False)
                    if len(trip.drafts) + len(reviews) < DRAFT_COUNT:
                        yield _progress(f"Drafting plan {len(trip.drafts) + len(reviews) + 1} of {DRAFT_COUNT}…")
            except Exception as e:
                if not reviews and not trip.drafts:
                    logger.warning("No plans for %s: %r", profile.destination, e, exc_info=True)
                    no_plans = _why_no_plans(e, profile.destination)
                    self.db.refresh(trip)  # an older draft may have been chosen while we worked
                    trip.drafts = []
                    trip.state = {**(trip.state or {}), "no_plans": no_plans.model_dump()}
                    answer.content = f"{answer.content} {no_plans.reason}"
                    self.db.commit()
                    yield {"type": "note", "text": no_plans.reason}
                    return
                logger.exception("Drafting stopped early; showing the plans already written")
            if reviews:
                yield _progress("Checking your plans…")
            yield from self._show_reviewed(trip, reviews, answer, reply_with_plans, wait=True)
        finally:
            checks.shutdown(wait=False, cancel_futures=True)  # a closed stream doesn't wait for the model

    def _review_places(self, profile: TripProfile, viewport: dict | None, skipped: set[str],
                       personality: Personality, itinerary: Itinerary) -> list:
        try:  # cached by generate; all three drafts share the route
            return self.drafter.pool(profile, viewport, skipped, personality, itinerary.route).places
        except Exception:
            logger.exception("No places for the review; it judges without them")
            return []

    def _show_reviewed(self, trip: TripModel, reviews: list[Future], answer: MessageModel, reply: str,
                       wait: bool) -> Iterator[Event]:
        """Shows reviewed plans as they finish: all that are done, or with `wait` every one left."""
        for future in as_completed(list(reviews)) if wait else [f for f in reviews if f.done()]:
            reviews.remove(future)
            # Saved before it is shown: choosing a plan closes the stream.
            trip.drafts = [*trip.drafts, future.result().model_dump(mode="json")]
            first = len(trip.drafts) == 1
            if first:
                answer.content = reply
            self.db.commit()
            if first:
                yield _reply(reply)  # replaces the one shown while we worked
            yield {"type": "draft", "index": len(trip.drafts) - 1, "draft": trip.drafts[-1]}

    # ---------- actions ----------

    def choose_draft(self, trip: TripModel, user: UserModel, draft_id: str) -> None:
        draft = next((d for d in self.trips.drafts(trip) if d.id == draft_id), None)
        if draft is None:
            raise TripActionError("Draft not found")
        trip.itinerary = draft.itinerary.model_dump(mode="json")
        personality = PersonalityService.detach(self.trips.personality(trip), set(draft.itinerary.target_ids()))
        personality.notes = DraftService.map_moments(draft.itinerary, personality.notes)
        self.trips.save_personality(trip, personality)
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
        if action == "unlike":
            self.trips.set_feedback(trip.id, pick, "none")
        elif action == "skip":
            self.trips.set_feedback(trip.id, pick, "skipped")
        elif action == "add":
            itinerary = self.trips.itinerary(trip) or Itinerary(destination=profile.destination or "", days=[])
            if itinerary.route and not city_of(itinerary.route, pick):
                raise TripActionError(f"{pick.name} is outside your route ({', '.join(c.name for c in itinerary.route)})")
            trip.itinerary = DraftService.place_in_itinerary(itinerary, pick).model_dump(mode="json")
            self.db.commit()
        elif action in ("like", "too_pricey"):
            if action == "like":
                self.trips.set_feedback(trip.id, pick, "liked")
                if pick.type_label:
                    self._remember_like(trip, profile, pick)
            over_budget = (  # only an estimated tier can say so: open data has no prices
                pick.category == PickCategory.STAY and profile.budget_level is not None and pick.tier is not None
                and tier_rank(pick.tier) > tier_rank(profile.budget_level.value)
            )
            # A liked stay over budget, or one the user called too pricey: find similar ones that fit.
            if action == "too_pricey" or over_budget:
                option_set = self.options.similar_cheaper(pick, profile, self._skipped(trip),
                                                          self.trips.personality(trip), self._viewport(trip))
                trip.panel = option_set.model_dump(mode="json")
                self.db.commit()
                return option_set
        return None

    def _remember_like(self, trip: TripModel, profile: TripProfile, pick: Pick) -> None:
        category = {"hidden_gems": "do"}.get(pick.category.value, pick.category.value)
        like = NoteChange(op="add", text=f"likes {pick.type_label}", quote=pick.type_label, category=category,
                          source="inferred")
        self.trips.save_personality(trip, PersonalityService.update(self.trips.personality(trip), profile, [like]))
        self.db.commit()

    def activity_action(self, trip: TripModel, user: UserModel, day_number: int, index: int, action: str) -> OptionSet | None:
        itinerary = self.trips.itinerary(trip)
        day = next((d for d in itinerary.days if d.day_number == day_number), None) if itinerary else None
        if day is None or not 0 <= index < len(day.activities):
            raise TripActionError("Activity not found")
        activity = day.activities[index]

        if action == "remove":
            before = itinerary.model_copy(deep=True)
            day.activities.pop(index)
            trip.itinerary = itinerary.model_dump(mode="json")
            self._keep_notes_attached(trip, self.trips.personality(trip), before, itinerary)
            self.db.commit()
            return None

        used = {a.place_id for d in itinerary.days for a in d.activities if a.place_id}
        personality = self.trips.personality(trip)
        about = [describe_note(n) for n in personality.notes if n.target in (activity.id, day.id)]
        option_set = self.options.swap_options(
            SwapTarget(day_number=day_number, index=index), activity.name, activity.category,
            self.trips.profile(trip), self._viewport(trip), used | self._skipped(trip),
            personality, about, near=day.city, route=itinerary.route,
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
            "lat": pick.lat, "lng": pick.lng,
            "source_url": pick.source_url,
            "for_note": None,  # the replacement wasn't chosen for that moment
            "minutes": default_minutes(pick.category.value),
        })
        trip.itinerary = itinerary.model_dump(mode="json")
        trip.panel = None
        self.db.commit()

