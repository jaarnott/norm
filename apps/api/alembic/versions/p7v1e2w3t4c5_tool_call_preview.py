"""A write waiting for approval keeps the preview the person was shown.

The approval card used to show a tool name and its raw JSON arguments. A
write now previews itself first — what it will change, before → after, read
from live data — and that preview is what the person approves. It is kept on
the call so the approved run can check the live data still matches it, and
re-propose instead of writing over a change made in the meantime.

Revision ID: p7v1e2w3t4c5
Revises: v7w8x9y0z1a2
"""

import sqlalchemy as sa
from alembic import op

revision = "p7v1e2w3t4c5"
down_revision = "v7w8x9y0z1a2"  # add_thread_memory_files
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tool_calls", sa.Column("preview", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("tool_calls", "preview")
