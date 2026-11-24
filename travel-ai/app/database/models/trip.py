from datetime import datetime
import uuid

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class TripModel(Base):
    __tablename__ = "trips"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String, nullable=False, default="New trip")
    # Serialized app.schemas.trip_profile.TripProfile
    profile: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Resolved destination: {place_id, name, formatted_address, viewport (with "id" since open data), kind, ...}
    destination_area: Mapped[dict | None] = mapped_column(JSON)
    # The last OptionSet shown in the panel (for "Last options").
    panel: Mapped[dict | None] = mapped_column(JSON)
    # Draft itineraries offered to the user: [Draft, ...]
    drafts: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Conversation bookkeeping: {questions_streak, asked_fields, answer_options, no_plans, before_reset}
    state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    next_question: Mapped[str | None] = mapped_column(Text)
    itinerary: Mapped[dict | None] = mapped_column(JSON)
    # Serialized app.schemas.personality.Personality: six scales with confidence (null on trips made before it).
    personality: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MessageModel(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String, nullable=False)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PickFeedbackModel(Base):
    __tablename__ = "pick_feedback"
    __table_args__ = (UniqueConstraint("trip_id", "place_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    place_id: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)  # "liked" | "skipped"
    # Snapshot of the Pick so itineraries don't need to refetch it.
    pick: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
