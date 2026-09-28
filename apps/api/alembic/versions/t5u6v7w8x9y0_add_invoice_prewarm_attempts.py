"""Failure memory for the background extraction pre-warm.

Only failures are stored: success is recorded by the extraction cache itself.
Without this, an invoice that can never be extracted stays a newest-first
candidate forever and consumes a pre-warm slot on every tick.

Revision ID: t5u6v7w8x9y0
Revises: s4t5u6v7w8x9
"""

import sqlalchemy as sa
from alembic import op

revision = "t5u6v7w8x9y0"
down_revision = "s4t5u6v7w8x9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoice_prewarm_attempts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("invoice_id", sa.String(), nullable=False),
        sa.Column("warmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("venue_id", "invoice_id", name="uq_prewarm_attempt"),
    )
    op.create_index(
        "ix_invoice_prewarm_attempts_venue_id",
        "invoice_prewarm_attempts",
        ["venue_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_invoice_prewarm_attempts_venue_id", table_name="invoice_prewarm_attempts"
    )
    op.drop_table("invoice_prewarm_attempts")
