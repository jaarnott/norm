"""Add memory_signals

Raw evidence that something might be worth remembering, captured before
interpretation. Chiefly the delta between what Norm drafted and what the human
corrected — previously destroyed when `pending_ops` drained on sync.

Revision ID: y6f7a8b9c0d1
Revises: x5e6f7a8b9c0
Create Date: 2026-07-20 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "y6f7a8b9c0d1"
down_revision = "x5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_signals",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "thread_id",
            sa.String(),
            sa.ForeignKey("threads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column(
            "promoted_to_memory_id",
            sa.String(),
            sa.ForeignKey("memories.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_memory_signals_organization_id", "memory_signals", ["organization_id"]
    )
    op.create_index("ix_memory_signals_user_id", "memory_signals", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_memory_signals_user_id", table_name="memory_signals")
    op.drop_index("ix_memory_signals_organization_id", table_name="memory_signals")
    op.drop_table("memory_signals")
