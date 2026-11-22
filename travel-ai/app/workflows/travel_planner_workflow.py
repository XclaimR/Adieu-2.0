import logging
import re
from collections.abc import Iterator

import httpx
from openai import RateLimitError
from sqlalchemy.orm import Session

from app.database.models.trip import MessageModel, TripModel
from app.database.models.user import UserModel
from app.repositories.trip_repository import TripRepository
from app.schemas.api import TurnResponse
from app.schemas.draft import NoPlans
from app.schemas.itinerary import Itinerary
from app.schemas.personality import NoteChange, Personality
from app.schemas.place import OptionSet, Pick, PickCategory, SwapTarget
from app.schemas.trip_profile import TripProfile
from app.schemas.turn import OptionsKind, OptionsRequest, TurnAction
from app.services.draft_service import DEFAULT_DAYS, DraftService, NotEnoughPlacesError, NoUsableDraftError
from app.services.options_service import OptionsService
from app.services.personality_service import PersonalityService, check_changes, describe_note
from app.services.places_service import PlacesService, PlacesUnavailableError, tier_rank
from app.services.trip_profile_service import TripProfileService
from app.services.turn_service import SHOW_PLANS, SHOW_PLANS_TEXT, VIBE_QUESTIONS, TurnService

logger = logging.getLogger(__name__)

RESET_PHRASES = ("start over", "reset")
DRAFT_COUNT = 3
PLANS_READY = f"Here are {DRAFT_COUNT} ways your trip could look — pick the one that feels right."
# Google says "SearchTextRequest per day", Gemini "GenerateRequestsPerDayPerProjectPerModel".
_DAILY_LIMIT = re.compile(r"per ?day", re.I)

Event = dict


def _same_place(a: str | None, b: str | None) -> bool:
    return (a or "").strip().lower() == (b or "").strip().lower()


def _progress(text: str) -> Event:
    return {"type": "progress", "text": text}


def _reply(text: str, question: str | None = None, options: list[str] | None = None) -> Event:
    return {"type": "reply", "text": text, "next_question": question, "answer_options": options or []}


def _why_no_plans(error: Exception, destination: str) -> NoPlans:
    cause = error.__cause__ if isinstance(error, NoUsableDraftError) else error
    if isinstance(cause, (PlacesUnavailableError, RateLimitError)) and _DAILY_LIMIT.search(str(cause)):
        used_up = "place searches" if isinstance(cause, PlacesUnavailableError) else "the AI planner"
        return NoPlans(retry_now=False, reason=f"Plans are unavailable for now: I've reached today's limit for "
                                               f"{used_up}. It resets once a day, so please come back later.")
    if isinstance(error, NotEnoughPlacesError):
        return NoPlans(reason=f"I couldn't find enough places in {destination} to build a plan. "
                              "Try naming a town or a bigger area there.")
    if isinstance(error, (PlacesUnavailableError, httpx.HTTPError)):
        return NoPlans(reason="I couldn't reach the places service, so I have no plans to show yet. "
                              "Please try again in a moment.")
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
        if any(p in message.lower() for p in RESET_PHRASES):
            yield from self._reset(trip, message)
            return

        yield _progress("Thinking…")
        profile = self.trips.profile(trip)
        before = self.trips.personality(trip)
        state = dict(trip.state or {})
        itinerary = self.trips.itinerary(trip)

        decision = TurnService.decide(
            shown_message, self.trips.history(trip.id), profile, self._context(trip, itinerary, before),
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

        if plan.action == TurnAction.OFFER_DRAFTS and not new_profile.duration_days:
            new_profile.duration_days = DEFAULT_DAYS
        trip.profile = new_profile.model_dump(mode="json")

        reply = TurnService.strip_questions(decision.reply, plan.next_question) + note
        if plan.action == TurnAction.OFFER_DRAFTS:
            # The model talks as if the plans exist ("I've put together a few"), so its words wait for the first draft.
            reply_with_plans = f"{reply.rstrip()} {PLANS_READY}"
            reply = f"Let me put some plans together for {new_profile.destination}.{note}"
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
                updated = self.drafter.edit(itinerary, changing, decision.edit_request or shown_message, new_profile,
                                            self._viewport(trip), self._skipped(trip), personality)
                if updated == itinerary:
                    extra = "I left the plan as it is: I found nothing that needed changing or fitted better."
                    answer.content = f"{reply} {extra}" + (f"\n\n{plan.next_question}" if plan.next_question else "")
                    yield {"type": "note", "text": extra}
                else:
                    trip.itinerary = updated.model_dump(mode="json")
                    self._keep_notes_attached(trip, personality, itinerary, updated)
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
        trip.state = {"answer_options": chips}
        trip.next_question = question
        self.trips.add_message(trip.id, "user", message)
        self.trips.add_message(trip.id, "assistant", f"Fresh start!\n\n{question}")
        self.db.commit()
        yield {"type": "reply", "text": "Fresh start!", "next_question": question, "answer_options": chips}
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
        trip.drafts = []
        try:
            for i, draft in enumerate(self.drafter.generate(profile, self._viewport(trip), self._skipped(trip), personality)):
                trip.drafts = [*trip.drafts, draft.model_dump(mode="json")]
                if i == 0:
                    answer.content = reply_with_plans
                self.db.commit()
                if i == 0:
                    yield _reply(reply_with_plans)  # replaces the one shown while we worked
                yield {"type": "draft", "index": i, "draft": trip.drafts[-1]}
                if i + 1 < DRAFT_COUNT:
                    yield _progress(f"Drafting plan {i + 2} of {DRAFT_COUNT}…")
        except Exception as e:
            if trip.drafts:
                raise
            logger.warning("No plans for %s: %r", profile.destination, e, exc_info=True)
            no_plans = _why_no_plans(e, profile.destination)
            self.db.refresh(trip)  # an older draft may have been chosen while we worked
            trip.drafts = []
            trip.state = {**(trip.state or {}), "no_plans": no_plans.model_dump()}
            answer.content = f"{answer.content} {no_plans.reason}"
            self.db.commit()
            yield {"type": "note", "text": no_plans.reason}

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
            trip.itinerary = DraftService.place_in_itinerary(itinerary, pick).model_dump(mode="json")
            self.db.commit()
        elif action in ("like", "too_pricey"):
            if action == "like":
                self.trips.set_feedback(trip.id, pick, "liked")
                if pick.type_label:
                    self._remember_like(trip, profile, pick)
            over_budget = (
                pick.category == PickCategory.STAY and profile.budget_level is not None
                and tier_rank(pick.tier) > tier_rank(profile.budget_level.value)
            )
            # A liked stay over budget, or one the user called too pricey: find similar ones that fit.
            if action == "too_pricey" or over_budget:
                option_set = self.options.similar_cheaper(pick, profile, self._skipped(trip),
                                                          self.trips.personality(trip))
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
            personality, about,
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
            "for_note": None,  # the replacement wasn't chosen for that moment
        })
        trip.itinerary = itinerary.model_dump(mode="json")
        trip.panel = None
        self.db.commit()

