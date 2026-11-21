"""trip personality: six scales with confidence

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-21
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trips", sa.Column("personality", sa.JSON()))


def downgrade() -> None:
    op.drop_column("trips", "personality")
