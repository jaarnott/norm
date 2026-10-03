"""A tool call needn't belong to a conversation.

Every write a working document syncs is recorded as a ToolCall, and a ToolCall
had to have a thread. A document opened outside a conversation — the Roster
page — has none, so every edit made there failed on the insert and nothing
reached Loaded (Oct 2026). The record of the write matters more than the
conversation it came from, so the thread becomes optional.

Revision ID: u6v7w8x9y0z1
Revises: t5u6v7w8x9y0
"""

import sqlalchemy as sa
from alembic import op

revision = "u6v7w8x9y0z1"
down_revision = "t5u6v7w8x9y0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("tool_calls", "thread_id", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    # Page edits made since have no thread; they can't satisfy NOT NULL.
    op.execute("DELETE FROM tool_calls WHERE thread_id IS NULL")
    op.alter_column(
        "tool_calls", "thread_id", existing_type=sa.String(), nullable=False
    )
