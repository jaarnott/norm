"""add chat attachments

Files a user attaches to a chat message: bytes reuse ``uploaded_documents``
(now with a nullable ``thread_id`` so the model can re-fetch them later), and
each user ``messages`` row carries an ``attachments`` reference list for the
attaching-turn injection and the transcript chips.

Revision ID: e7f8a9b0c1d2
Revises: b4c5d6e7f8a9
Create Date: 2026-08-21
"""

from alembic import op
import sqlalchemy as sa

revision = "e7f8a9b0c1d2"
down_revision = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("attachments", sa.JSON(), nullable=True))
    op.add_column(
        "uploaded_documents", sa.Column("thread_id", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("uploaded_documents", "thread_id")
    op.drop_column("messages", "attachments")
