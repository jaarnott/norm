"""add display_blocks column to messages

Revision ID: i1a2b3c4d5e6
Revises: h1a2b3c4d5e6
Create Date: 2026-03-16 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "i1a2b3c4d5e6"
down_revision = "h1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("display_blocks", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "display_blocks")
