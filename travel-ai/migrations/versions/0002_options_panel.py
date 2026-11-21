"""options panel, drafts and conversation state on trips

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-02
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trips", sa.Column("destination_area", sa.JSON()))
    op.add_column("trips", sa.Column("panel", sa.JSON()))
    op.add_column("trips", sa.Column("drafts", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("trips", sa.Column("state", sa.JSON(), nullable=False, server_default="{}"))
    op.drop_column("trips", "picks")
    # "saved" meant "add to itinerary" in v1; it now means "liked" (a taste signal).
    op.execute("UPDATE pick_feedback SET status = 'liked' WHERE status = 'saved'")


def downgrade() -> None:
    op.execute("UPDATE pick_feedback SET status = 'saved' WHERE status = 'liked'")
    op.add_column("trips", sa.Column("picks", sa.JSON(), nullable=False, server_default="{}"))
    op.drop_column("trips", "state")
    op.drop_column("trips", "drafts")
    op.drop_column("trips", "panel")
    op.drop_column("trips", "destination_area")
