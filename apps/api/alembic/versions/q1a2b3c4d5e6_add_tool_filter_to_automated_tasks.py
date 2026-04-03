"""add tool_filter to automated_tasks

Revision ID: q1a2b3c4d5e6
Revises: p1a2b3c4d5e6
Create Date: 2026-04-02 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "q1a2b3c4d5e6"
down_revision = "p1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("automated_tasks", sa.Column("tool_filter", sa.JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("automated_tasks", "tool_filter")
