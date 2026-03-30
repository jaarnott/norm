"""add conversation_summary and summary_through_count to threads

Revision ID: n1a2b3c4d5e6
Revises: m1a2b3c4d5e6
Create Date: 2026-03-30 12:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "n1a2b3c4d5e6"
down_revision = "d4e5f6g7h8i9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("threads", sa.Column("conversation_summary", sa.Text, nullable=True))
    op.add_column("threads", sa.Column("summary_through_count", sa.Integer, nullable=True))


def downgrade() -> None:
    op.drop_column("threads", "summary_through_count")
    op.drop_column("threads", "conversation_summary")
