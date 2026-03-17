"""add tools_provided column to llm_calls

Revision ID: h1a2b3c4d5e6
Revises: g1a2b3c4d5e6
Create Date: 2026-03-15 18:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "h1a2b3c4d5e6"
down_revision = "g1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("llm_calls", sa.Column("tools_provided", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("llm_calls", "tools_provided")
