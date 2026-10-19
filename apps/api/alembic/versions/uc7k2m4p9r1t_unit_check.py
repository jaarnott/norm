"""The daily unit check: its runs and the corrections it proposes.

Each morning Norm compares every line it received the day before with the
invoice it read and with its own catalogue, and records the lines it thinks are
booked in the wrong unit, with the unit it proposes, and the lines it booked on
an answer it wasn't sure of. A person applies, confirms, changes or dismisses
each one in the Dojo (owner, 18 Oct 2026). The check itself never writes to
Loaded.

Revision ID: uc7k2m4p9r1t
Revises: u4w6y8a1c3e5
"""

import sqlalchemy as sa

from alembic import op

revision = "uc7k2m4p9r1t"
down_revision = "u4w6y8a1c3e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "unit_check_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("slot", sa.String(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("emailed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("slot", name="uq_unit_check_run_slot"),
    )
    op.create_table(
        "unit_check_proposals",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(),
            sa.ForeignKey("unit_check_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("venue_name", sa.String(), nullable=True),
        sa.Column("invoice_id", sa.String(), nullable=False),
        sa.Column("invoice_number", sa.String(), nullable=True),
        sa.Column("supplier_name", sa.String(), nullable=True),
        sa.Column("invoice_date", sa.String(), nullable=True),
        sa.Column("line_id", sa.String(), nullable=False),
        sa.Column("item_id", sa.String(), nullable=True),
        sa.Column("item_name", sa.String(), nullable=True),
        sa.Column("code", sa.String(), nullable=True),
        sa.Column("printed", sa.JSON(), nullable=True),
        sa.Column("quantity", sa.Numeric(14, 4), nullable=True),
        sa.Column("unit_cost", sa.Numeric(14, 4), nullable=True),
        sa.Column("booked_unit_id", sa.String(), nullable=True),
        sa.Column("booked_unit_name", sa.String(), nullable=True),
        sa.Column("invoice_says", sa.String(), nullable=True),
        sa.Column("catalogue_says", sa.String(), nullable=True),
        sa.Column("proposed_unit_id", sa.String(), nullable=True),
        sa.Column("proposed_unit_name", sa.String(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False, server_default="correct"),
        sa.Column("catalog_key", sa.String(), nullable=True),
        sa.Column("product_key", sa.String(), nullable=True),
        sa.Column("norm_estimate", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("decided_by", sa.String(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_unit_check_proposals_run_id", "unit_check_proposals", ["run_id"]
    )
    op.create_index(
        "ix_unit_check_proposals_venue_id", "unit_check_proposals", ["venue_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_unit_check_proposals_venue_id", table_name="unit_check_proposals")
    op.drop_index("ix_unit_check_proposals_run_id", table_name="unit_check_proposals")
    op.drop_table("unit_check_proposals")
    op.drop_table("unit_check_runs")
