from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.database.models.trip import MessageModel, PickFeedbackModel, TripModel
from app.schemas.api import MessageOut, TripOut, TripSummary
from app.schemas.draft import Draft
from app.schemas.personality import Note, Personality
from app.schemas.itinerary import Itinerary
from app.schemas.place import OptionSet, Pick
from app.schemas.trip_profile import TripProfile
from app.services.personality_service import PersonalityService


class TripRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, user_id: str, title: str, profile: TripProfile) -> TripModel:
        trip = TripModel(user_id=user_id, title=title, profile=profile.model_dump(mode="json"), drafts=[], state={})
        self.db.add(trip)
        self.db.commit()
        self.db.refresh(trip)
        return trip

    def get_for_user(self, trip_id: str, user_id: str) -> TripModel | None:
        trip = self.db.get(TripModel, trip_id)
        return trip if trip and trip.user_id == user_id else None

    def list_for_user(self, user_id: str) -> list[TripSummary]:
        trips = self.db.scalars(
            select(TripModel).where(TripModel.user_id == user_id).order_by(TripModel.updated_at.desc())
        )
        return [
            TripSummary(
                id=t.id,
                # The chosen plan's name tells trips to the same place apart.
                title=(t.itinerary or {}).get("title") or t.title,
                destination=(t.profile or {}).get("destination"),
                duration_days=(t.profile or {}).get("duration_days"),
                photo_name=(t.destination_area or {}).get("photo_name"),
                updated_at=t.updated_at,
            )
            for t in trips
        ]

    def add_message(self, trip_id: str, role: str, content: str) -> MessageModel:
        message = MessageModel(trip_id=trip_id, role=role, content=content)
        self.db.add(message)
        return message

    def messages(self, trip_id: str) -> list[MessageModel]:
        return list(self.db.scalars(
            select(MessageModel).where(MessageModel.trip_id == trip_id).order_by(MessageModel.id)
        ))

    def history(self, trip_id: str) -> list[dict]:
        """Conversation in OpenAI message format, for the model."""
        return [{"role": m.role, "content": m.content} for m in self.messages(trip_id)]

    def set_feedback(self, trip_id: str, pick: Pick, status: str) -> None:
        self.db.execute(delete(PickFeedbackModel).where(
            PickFeedbackModel.trip_id == trip_id, PickFeedbackModel.place_id == pick.place_id
        ))
        if status in ("liked", "skipped"):
            self.db.add(PickFeedbackModel(
                trip_id=trip_id, place_id=pick.place_id, status=status, pick=pick.model_dump(mode="json")
            ))
        self.db.commit()

    def feedback(self, trip_id: str, status: str) -> list[Pick]:
        rows = self.db.scalars(select(PickFeedbackModel).where(
            PickFeedbackModel.trip_id == trip_id, PickFeedbackModel.status == status
        ).order_by(PickFeedbackModel.id))
        return [Pick.model_validate(r.pick) for r in rows]

    @staticmethod
    def profile(trip: TripModel) -> TripProfile:
        return TripProfile.model_validate(trip.profile or {})

    @staticmethod
    def personality(trip: TripModel) -> Personality:
        personality = Personality.model_validate(trip.personality or {})
        # Trips from before notes kept these in profile.preferences; the next turn saves them as notes.
        ids = {n.id for n in personality.notes}
        for pref in TripProfile.model_validate(trip.profile or {}).preferences:
            key = pref.key.lower()
            if f"p_{key}" in ids:
                continue
            category = ("diet" if "diet" in key else "access" if key in ("mobility", "accessibility") else "general")
            kind = "avoid" if key.startswith("avoid") else "must" if category != "general" else "want"
            text = pref.value if category != "general" or kind == "avoid" else f"{pref.key.replace('_', ' ')}: {pref.value}"
            personality.notes.append(Note(id=f"p_{key}", text=text, category=category, kind=kind,
                                          source="inferred" if pref.source == "inferred" else "stated"))
        return personality

    @staticmethod
    def save_personality(trip: TripModel, personality: Personality) -> None:
        trip.personality = personality.model_dump(mode="json", include={"scales", "notes"})

    @staticmethod
    def panel(trip: TripModel) -> OptionSet | None:
        return OptionSet.model_validate(trip.panel) if trip.panel else None

    @staticmethod
    def drafts(trip: TripModel) -> list[Draft]:
        return [Draft.model_validate(d) for d in trip.drafts or []]

    @staticmethod
    def itinerary(trip: TripModel) -> Itinerary | None:
        return Itinerary.model_validate(trip.itinerary) if trip.itinerary else None

    def find_pick(self, trip: TripModel, place_id: str) -> Pick | None:
        """A place the user has seen: in the panel, the itinerary's stay, or earlier feedback."""
        panel = self.panel(trip)
        for pick in panel.items if panel else []:
            if pick.place_id == place_id:
                return pick
        itinerary = self.itinerary(trip)
        if itinerary and itinerary.stay and itinerary.stay.place_id == place_id:
            return itinerary.stay
        row = self.db.scalar(select(PickFeedbackModel).where(
            PickFeedbackModel.trip_id == trip.id, PickFeedbackModel.place_id == place_id
        ))
        return Pick.model_validate(row.pick) if row else None

    def to_out(self, trip: TripModel) -> TripOut:
        return TripOut(
            id=trip.id,
            title=trip.title,
            profile=self.profile(trip),
            personality=PersonalityService.card(self.personality(trip)),
            destination_name=(trip.destination_area or {}).get("name"),
            panel=self.panel(trip),
            drafts=self.drafts(trip),
            liked_place_ids=[p.place_id for p in self.feedback(trip.id, "liked")],
            skipped_place_ids=[p.place_id for p in self.feedback(trip.id, "skipped")],
            next_question=trip.next_question,
            answer_options=(trip.state or {}).get("answer_options", []),
            itinerary=self.itinerary(trip),
            messages=[
                MessageOut(role=m.role, content=m.content, created_at=m.created_at)
                for m in self.messages(trip.id)
            ],
        )
