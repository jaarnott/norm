"""Add invoice_autopilot_outcomes

One row per invoice receive attempt, recording whether autopilot (accept every
suggestion, then receive) would have produced the same result the human did.
Every human receive becomes evidence for the decision to switch autopilot on.

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-08-11 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "b9c0d1e2f3a4"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoice_autopilot_outcomes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(),
            sa.ForeignKey("organizations.id"),
            nullable=True,
        ),
        sa.Column("venue_id", sa.String(), sa.ForeignKey("venues.id"), nullable=True),
        # Loaded's invoice id — a foreign system's key, so no FK.
        sa.Column("invoice_id", sa.String(), nullable=False),
        sa.Column("reference_number", sa.String(), nullable=True),
        sa.Column("supplier_name", sa.String(), nullable=True),
        sa.Column("linked_supplier_id", sa.String(), nullable=True),
        # clean | no_suggestions | edited | not_reviewed | dojo
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("received", sa.Boolean(), nullable=False, server_default=sa.false()),
        # interactive | mcp_card | autopilot | approve_fixes
        sa.Column("mode", sa.String(), nullable=False),
        # user | norm
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "working_document_id",
            sa.String(),
            sa.ForeignKey("working_documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "thread_id",
            sa.String(),
            sa.ForeignKey("threads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("suggestion_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accepted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dismissed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pending_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "manual_edit_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "blocking_issue_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "issues_waved_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("confidence", sa.String(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        # An invoice is received once — a second row with the same outcome is
        # always a retry, so make double-counting structurally impossible.
        sa.UniqueConstraint(
            "venue_id", "invoice_id", "outcome", name="uq_autopilot_outcome"
        ),
    )
    op.create_index(
        "ix_invoice_autopilot_outcomes_organization_id",
        "invoice_autopilot_outcomes",
        ["organization_id"],
    )
    op.create_index(
        "ix_invoice_autopilot_outcomes_venue_id",
        "invoice_autopilot_outcomes",
        ["venue_id"],
    )
    op.create_index(
        "ix_invoice_autopilot_outcomes_invoice_id",
        "invoice_autopilot_outcomes",
        ["invoice_id"],
    )
    op.create_index(
        "ix_invoice_autopilot_outcomes_supplier_name",
        "invoice_autopilot_outcomes",
        ["supplier_name"],
    )
    op.create_index(
        "ix_invoice_autopilot_outcomes_user_id",
        "invoice_autopilot_outcomes",
        ["user_id"],
    )
    op.create_index(
        "ix_invoice_autopilot_outcomes_created_at",
        "invoice_autopilot_outcomes",
        ["created_at"],
    )


def downgrade() -> None:
    for name in (
        "ix_invoice_autopilot_outcomes_created_at",
        "ix_invoice_autopilot_outcomes_user_id",
        "ix_invoice_autopilot_outcomes_supplier_name",
        "ix_invoice_autopilot_outcomes_invoice_id",
        "ix_invoice_autopilot_outcomes_venue_id",
        "ix_invoice_autopilot_outcomes_organization_id",
    ):
        op.drop_index(name, table_name="invoice_autopilot_outcomes")
    op.drop_table("invoice_autopilot_outcomes")
