"""Add delegation lineage to threads.

A thread created by one agent delegating to another records who spawned it and
how deep the chain is. Depth is stored rather than recomputed so the recursion
guard is a single read, and it is written server-side only.

parent_thread_id is intentionally a plain column, not a ForeignKey: the child is
created inside the parent's turn, while the parent row is still uncommitted, so
an FK constraint would fire on flush.

Revision ID: w4d5e6f7a8b9
Revises: v3c4d5e6f7a8
"""

import sqlalchemy as sa
from alembic import op

revision = "w4d5e6f7a8b9"
down_revision = "v3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("threads", sa.Column("parent_thread_id", sa.String(), nullable=True))
    op.add_column(
        "threads",
        sa.Column(
            "delegation_depth", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.create_index(
        "ix_threads_parent_thread_id", "threads", ["parent_thread_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_threads_parent_thread_id", table_name="threads")
    op.drop_column("threads", "delegation_depth")
    op.drop_column("threads", "parent_thread_id")
