"""users, trips, messages, pick feedback, place cache

Revision ID: 0001
Revises:
Create Date: 2026-10-01
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("google_sub", sa.String(), nullable=False, unique=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("name", sa.String()),
        sa.Column("picture", sa.String()),
        sa.Column("taste", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_table(
        "trips",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("profile", sa.JSON(), nullable=False),
        sa.Column("picks", sa.JSON(), nullable=False),
        sa.Column("next_question", sa.Text()),
        sa.Column("itinerary", sa.JSON()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_index("ix_trips_user_id", "trips", ["user_id"])
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("trip_id", sa.String(), sa.ForeignKey("trips.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_index("ix_messages_trip_id", "messages", ["trip_id"])
    op.create_table(
        "pick_feedback",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("trip_id", sa.String(), sa.ForeignKey("trips.id", ondelete="CASCADE"), nullable=False),
        sa.Column("place_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("pick", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime()),
        sa.UniqueConstraint("trip_id", "place_id"),
    )
    op.create_index("ix_pick_feedback_trip_id", "pick_feedback", ["trip_id"])
    op.create_table(
        "place_cache",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime()),
    )


def downgrade() -> None:
    op.drop_table("place_cache")
    op.drop_table("pick_feedback")
    op.drop_table("messages")
    op.drop_table("trips")
    op.drop_table("users")
