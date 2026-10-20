"""Outcomes: one row each time an invoice goes the same way.

invoice_autopilot_outcomes kept one row per (venue, invoice, outcome), so when
Loaded restored an invoice and it was received or deleted again for the same
reason, the second time left no trace — Norm deleting an invoice the owner had
restored was invisible on the activity page (owner, 19 Oct 2026). Existing rows
are occurrence 0; the unique key now includes it.

Revision ID: ja5d7f9h1j3l
Revises: ia4c6e8g0i2k
"""

import sqlalchemy as sa

from alembic import op

revision = "ja5d7f9h1j3l"
down_revision = "ia4c6e8g0i2k"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoice_autopilot_outcomes",
        sa.Column("occurrence", sa.Integer(), nullable=False, server_default="0"),
    )
    op.drop_constraint(
        "uq_autopilot_outcome", "invoice_autopilot_outcomes", type_="unique"
    )
    op.create_unique_constraint(
        "uq_autopilot_outcome",
        "invoice_autopilot_outcomes",
        ["venue_id", "invoice_id", "outcome", "occurrence"],
    )


def downgrade() -> None:
    # Back to one row per outcome: the later times go.
    op.execute("DELETE FROM invoice_autopilot_outcomes WHERE occurrence > 0")
    op.drop_constraint(
        "uq_autopilot_outcome", "invoice_autopilot_outcomes", type_="unique"
    )
    op.create_unique_constraint(
        "uq_autopilot_outcome",
        "invoice_autopilot_outcomes",
        ["venue_id", "invoice_id", "outcome"],
    )
    op.drop_column("invoice_autopilot_outcomes", "occurrence")
