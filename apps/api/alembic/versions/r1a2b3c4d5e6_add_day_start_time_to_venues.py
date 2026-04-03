"""add day_start_time to venues

Revision ID: r1a2b3c4d5e6
Revises: q1a2b3c4d5e6
Create Date: 2026-04-02 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "r1a2b3c4d5e6"
down_revision = "q1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("venues", sa.Column("day_start_time", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("venues", "day_start_time")
