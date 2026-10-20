"""Invoice activity: the invoices Norm handled that didn't go in or get deleted.

The Dojo is replaced by an invoice activity page that shows every invoice Norm
handled and what happened to it (owner, 19 Oct 2026). Received and deleted
invoices already have a row each in invoice_autopilot_outcomes; this table holds
the ones that stopped, are waiting for a person, or failed, one row per invoice
and kind.

Revision ID: ia4c6e8g0i2k
Revises: v5x7z9b2d4f6
"""

import sqlalchemy as sa

from alembic import op

revision = "ia4c6e8g0i2k"
down_revision = "v5x7z9b2d4f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoice_activity_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("organization_id", sa.String(), nullable=True),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("invoice_id", sa.String(), nullable=False),
        sa.Column("reference_number", sa.String(), nullable=True),
        sa.Column("supplier_name", sa.String(), nullable=True),
        sa.Column("total", sa.Numeric(14, 2), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=True),
        sa.Column("actor", sa.String(), nullable=False, server_default="norm"),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "venue_id", "invoice_id", "kind", name="uq_invoice_activity_event"
        ),
    )
    for col in ("organization_id", "venue_id", "invoice_id", "created_at"):
        op.create_index(
            f"ix_invoice_activity_events_{col}", "invoice_activity_events", [col]
        )


def downgrade() -> None:
    for col in ("organization_id", "venue_id", "invoice_id", "created_at"):
        op.drop_index(
            f"ix_invoice_activity_events_{col}", table_name="invoice_activity_events"
        )
    op.drop_table("invoice_activity_events")
