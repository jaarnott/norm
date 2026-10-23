"""Loaded Payroll: the pay_* tables (app/db/payroll_models.py).

Sixteen tables: the payroll entities and their dated statutory rules, pay
weeks with their frozen Loaded snapshots and shift facts, leave payouts and
leave styles, gratuity batches and allocations, the Ace exports, the week's
ACE inputs, adjustments and accruals, the Xero journal drafts and every push
to Xero, and the bond-consent files. Every table is organisation-scoped in
the schema (UNIQUE (organization_id, id), composite references to venues
and parents), money is integer cents, and the "already pushed" guard is the
partial unique index on pay_xero_pushes WHERE voided_at IS NULL.

Idempotent on purpose. The test suite builds tables with create_all on a
persistent local database, so a dev database can already hold every pay_*
table by the time this runs; if_not_exists lets `alembic upgrade head` pass
over them instead of failing on "already exists".

Revision ID: lc7f9h1j3l5n
Revises: kb6e8g0i2k4m
"""

import sqlalchemy as sa
from alembic import op

revision = "lc7f9h1j3l5n"
down_revision = "kb6e8g0i2k4m"
branch_labels = None
depends_on = None

#: Creation order (parents first); downgrade drops in reverse. Pinned by
#: tests/test_finance_foundation.py against the model module.
PAY_TABLES = (
    "pay_files",
    "pay_rules",
    "pay_entities",
    "pay_gratuity_batches",
    "pay_leave_payouts",
    "pay_leave_styles",
    "pay_weeks",
    "pay_accruals",
    "pay_ace_inputs",
    "pay_adjustments",
    "pay_gratuity_allocations",
    "pay_snapshots",
    "pay_exports",
    "pay_journal_drafts",
    "pay_shift_facts",
    "pay_xero_pushes",
)


def upgrade() -> None:
    op.create_table(
        "pay_files",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=True),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('bond_consent')", name="ck_pay_files_kind"),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_pay_files_created_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_pay_files_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_pay_files_org_id"),
        sa.UniqueConstraint("organization_id", "sha256", name="uq_pay_files_sha"),
        if_not_exists=True,
    )
    op.create_table(
        "pay_rules",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=True),
        sa.Column("rule", sa.String(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_pay_rules_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )
    op.create_table(
        "pay_entities",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("loaded_company_id", sa.String(), nullable=True),
        sa.Column(
            "pay_week_start_weekday",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("payroll_system", sa.String(), server_default="ace", nullable=False),
        sa.Column(
            "ace_pay_type_code", sa.String(), server_default="50001", nullable=False
        ),
        sa.Column("ace_gratuity_allowance_code", sa.String(), nullable=True),
        sa.Column(
            "security_pay_code", sa.String(), server_default="SECURITY", nullable=False
        ),
        sa.Column(
            "uniform_bond_cents",
            sa.Integer(),
            server_default=sa.text("15000"),
            nullable=False,
        ),
        sa.Column(
            "ico_leave_uplift_bp",
            sa.Integer(),
            server_default=sa.text("1200"),
            nullable=False,
        ),
        sa.Column(
            "ico_leave_tax",
            sa.String(),
            server_default="account_default",
            nullable=False,
        ),
        sa.Column("sick_approver_name", sa.String(), nullable=True),
        sa.Column("payroll_contact_email", sa.String(), nullable=True),
        sa.Column(
            "payroll_on", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_pay_entities_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_pay_entities_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_pay_entities_org_id"),
        sa.UniqueConstraint("venue_id", name="uq_pay_entities_venue"),
        sa.CheckConstraint(
            "ico_leave_tax IN ('account_default', 'no_gst', 'standard_gst')",
            name="ck_pay_entities_ico_leave_tax",
        ),
        if_not_exists=True,
    )
    op.create_table(
        "pay_gratuity_batches",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("total_cents", sa.Integer(), nullable=False),
        sa.Column("gl_code", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("source", sa.String(), server_default="norm", nullable=False),
        sa.Column(
            "rehearsal", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('norm', 'orbit')", name="ck_pay_gratuity_batches_source"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_pay_gratuity_batches_created_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_pay_gratuity_batches_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_pay_gratuity_batches_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_pay_gratuity_batches_org_id"
        ),
        if_not_exists=True,
    )
    op.create_table(
        "pay_leave_payouts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("loaded_request_id", sa.String(), nullable=False),
        sa.Column("staff_member_id", sa.String(), nullable=True),
        sa.Column("staff_name", sa.String(), nullable=True),
        sa.Column("leave_type", sa.String(), nullable=True),
        sa.Column(
            "requested_cents", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "paid_cents", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "bond_deducted_cents",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("bond_consent_file_id", sa.String(), nullable=True),
        sa.Column("workflow_kind", sa.String(), nullable=False),
        sa.Column("workflow_inputs", sa.JSON(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.String(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(), server_default="norm", nullable=False),
        sa.Column(
            "rehearsal", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('norm', 'orbit', 'orbit_legacy')",
            name="ck_pay_leave_payouts_source",
        ),
        sa.CheckConstraint(
            "workflow_kind IN ('annual', 'sick', 'generic')",
            name="ck_pay_leave_payouts_workflow_kind",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["users.id"],
            name="fk_pay_leave_payouts_decided_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "bond_consent_file_id"],
            ["pay_files.organization_id", "pay_files.id"],
            name="fk_pay_leave_payouts_bond_consent_file_id",
            ondelete="SET NULL (bond_consent_file_id)",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_pay_leave_payouts_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_pay_leave_payouts_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_pay_leave_payouts_org_id"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "loaded_request_id",
            "week_start",
            name="uq_pay_leave_payouts_request_week",
        ),
        if_not_exists=True,
    )
    op.create_table(
        "pay_leave_styles",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("loaded_leave_type_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("ace_style", sa.String(), nullable=False),
        sa.Column("set_by", sa.String(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_pay_leave_styles_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_pay_leave_styles_organization_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["set_by"],
            ["users.id"],
            name="fk_pay_leave_styles_set_by",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_pay_leave_styles_org_id"),
        sa.UniqueConstraint(
            "venue_id", "loaded_leave_type_id", name="uq_pay_leave_styles_venue_type"
        ),
        if_not_exists=True,
    )
    op.create_table(
        "pay_weeks",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.String(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["closed_by"],
            ["users.id"],
            name="fk_pay_weeks_closed_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_pay_weeks_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_pay_weeks_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_pay_weeks_org_id"),
        sa.UniqueConstraint("venue_id", "week_start", name="uq_pay_weeks_venue_week"),
        if_not_exists=True,
    )
    op.create_table(
        "pay_accruals",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("week_id", sa.String(), nullable=False),
        sa.Column("opening_cents", sa.Integer(), nullable=True),
        sa.Column(
            "accrued_cents", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "paid_cents", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("source", sa.String(), server_default="norm", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('norm', 'orbit')", name="ck_pay_accruals_source"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "week_id"],
            ["pay_weeks.organization_id", "pay_weeks.id"],
            name="fk_pay_accruals_week_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_pay_accruals_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_pay_accruals_org_id"),
        sa.UniqueConstraint("week_id", name="uq_pay_accruals_week"),
        if_not_exists=True,
    )
    op.create_table(
        "pay_ace_inputs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("week_id", sa.String(), nullable=False),
        sa.Column(
            "gross_cents", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "net_cents", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "esct_cents", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "kiwisaver_cents", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("entered_by", sa.String(), nullable=True),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(), server_default="norm", nullable=False),
        sa.Column(
            "rehearsal", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('norm', 'orbit')", name="ck_pay_ace_inputs_source"
        ),
        sa.ForeignKeyConstraint(
            ["entered_by"],
            ["users.id"],
            name="fk_pay_ace_inputs_entered_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "week_id"],
            ["pay_weeks.organization_id", "pay_weeks.id"],
            name="fk_pay_ace_inputs_week_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_pay_ace_inputs_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_pay_ace_inputs_org_id"),
        sa.UniqueConstraint("week_id", name="uq_pay_ace_inputs_week"),
        if_not_exists=True,
    )
    op.create_table(
        "pay_adjustments",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("week_id", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("xero_account_code", sa.String(), nullable=True),
        sa.Column("xero_account_name", sa.String(), nullable=True),
        sa.Column("report_target", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("source", sa.String(), server_default="norm", nullable=False),
        sa.Column(
            "rehearsal", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "report_target IN ('wages', 'leave')",
            name="ck_pay_adjustments_report_target",
        ),
        sa.CheckConstraint(
            "source IN ('norm', 'orbit')", name="ck_pay_adjustments_source"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_pay_adjustments_created_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "week_id"],
            ["pay_weeks.organization_id", "pay_weeks.id"],
            name="fk_pay_adjustments_week_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_pay_adjustments_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_pay_adjustments_org_id"),
        if_not_exists=True,
    )
    op.create_table(
        "pay_gratuity_allocations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("batch_id", sa.String(), nullable=False),
        sa.Column("staff_member_id", sa.String(), nullable=False),
        sa.Column("staff_name", sa.String(), nullable=True),
        sa.Column("pay_code", sa.String(), nullable=True),
        sa.Column(
            "hours",
            sa.Numeric(precision=8, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "amount_cents", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "is_payable", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("payroll_venue_id", sa.String(), nullable=True),
        sa.Column("excluded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("excluded_by", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["excluded_by"],
            ["users.id"],
            name="fk_pay_gratuity_allocations_excluded_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "batch_id"],
            ["pay_gratuity_batches.organization_id", "pay_gratuity_batches.id"],
            name="fk_pay_gratuity_allocations_batch_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "payroll_venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_pay_gratuity_allocations_payroll_venue_id",
            ondelete="SET NULL (payroll_venue_id)",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_pay_gratuity_allocations_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_pay_gratuity_allocations_org_id"
        ),
        if_not_exists=True,
    )
    op.create_table(
        "pay_snapshots",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("week_id", sa.String(), nullable=False),
        sa.Column(
            "taken_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("taken_by", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column(
            "shift_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "dropped_deleted", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "dropped_unfinished",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('monday', 'close', 'manual', 'orbit')",
            name="ck_pay_snapshots_source",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "week_id"],
            ["pay_weeks.organization_id", "pay_weeks.id"],
            name="fk_pay_snapshots_week_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_pay_snapshots_organization_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["taken_by"],
            ["users.id"],
            name="fk_pay_snapshots_taken_by",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_pay_snapshots_org_id"),
        if_not_exists=True,
    )
    op.create_table(
        "pay_exports",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("week_id", sa.String(), nullable=False),
        sa.Column("snapshot_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "line_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("totals", sa.JSON(), nullable=True),
        sa.Column("generated_by", sa.String(), nullable=True),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('import', 'leave', 'gratuities', 'entertainers')",
            name="ck_pay_exports_kind",
        ),
        sa.ForeignKeyConstraint(
            ["generated_by"],
            ["users.id"],
            name="fk_pay_exports_generated_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "snapshot_id"],
            ["pay_snapshots.organization_id", "pay_snapshots.id"],
            name="fk_pay_exports_snapshot_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "week_id"],
            ["pay_weeks.organization_id", "pay_weeks.id"],
            name="fk_pay_exports_week_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_pay_exports_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_pay_exports_org_id"),
        if_not_exists=True,
    )
    op.create_table(
        "pay_journal_drafts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("week_id", sa.String(), nullable=False),
        sa.Column("snapshot_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("counterparty_venue_id", sa.String(), nullable=True),
        sa.Column("lines", sa.JSON(), nullable=False),
        sa.Column(
            "debit_cents", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "credit_cents", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "residual_cents", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("basis_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "built_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("built_by", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('journal', 'ico_invoice', 'ico_bill')",
            name="ck_pay_journal_drafts_kind",
        ),
        sa.ForeignKeyConstraint(
            ["built_by"],
            ["users.id"],
            name="fk_pay_journal_drafts_built_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "counterparty_venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_pay_journal_drafts_counterparty_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "snapshot_id"],
            ["pay_snapshots.organization_id", "pay_snapshots.id"],
            name="fk_pay_journal_drafts_snapshot_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "week_id"],
            ["pay_weeks.organization_id", "pay_weeks.id"],
            name="fk_pay_journal_drafts_week_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_pay_journal_drafts_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_pay_journal_drafts_org_id"
        ),
        if_not_exists=True,
    )
    op.create_table(
        "pay_shift_facts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("snapshot_id", sa.String(), nullable=False),
        sa.Column("source_shift_id", sa.String(), nullable=False),
        sa.Column("staff_member_id", sa.String(), nullable=True),
        sa.Column("staff_name", sa.String(), nullable=True),
        sa.Column("pay_code", sa.String(), nullable=True),
        sa.Column("shift_type", sa.String(), nullable=True),
        sa.Column("remuneration_type", sa.String(), nullable=True),
        sa.Column("role_id", sa.String(), nullable=True),
        sa.Column("role_name", sa.String(), nullable=True),
        sa.Column("cost_group", sa.String(), nullable=True),
        sa.Column("worked_venue_loaded_id", sa.String(), nullable=True),
        sa.Column("worked_venue_id", sa.String(), nullable=True),
        sa.Column(
            "is_from_other_venue",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("clockin_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("clockout_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trading_date", sa.Date(), nullable=True),
        sa.Column(
            "hours",
            sa.Numeric(precision=8, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "cost_cents", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "ph_hours",
            sa.Numeric(precision=8, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "ph_cost_cents", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("leave_request_id", sa.String(), nullable=True),
        sa.Column("leave_type_name", sa.String(), nullable=True),
        sa.Column("is_approved", sa.Boolean(), nullable=True),
        sa.Column("breakdown", sa.JSON(), nullable=True),
        sa.Column("payload_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "cost_group IN ('bar_floor', 'kitchen', 'other')",
            name="ck_pay_shift_facts_cost_group",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "snapshot_id"],
            ["pay_snapshots.organization_id", "pay_snapshots.id"],
            name="fk_pay_shift_facts_snapshot_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "worked_venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_pay_shift_facts_worked_venue_id",
            ondelete="SET NULL (worked_venue_id)",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_pay_shift_facts_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_pay_shift_facts_org_id"),
        sa.UniqueConstraint(
            "snapshot_id", "source_shift_id", name="uq_pay_shift_facts_shift"
        ),
        if_not_exists=True,
    )
    op.create_table(
        "pay_xero_pushes",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("week_id", sa.String(), nullable=True),
        sa.Column("draft_id", sa.String(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=True),
        sa.Column("counterparty_venue_id", sa.String(), nullable=True),
        sa.Column("entertainer_performance_id", sa.String(), nullable=True),
        sa.Column("xero_tenant_id", sa.String(), nullable=False),
        sa.Column("xero_id", sa.String(), nullable=True),
        sa.Column("xero_number", sa.String(), nullable=True),
        sa.Column("xero_status", sa.String(), nullable=True),
        sa.Column("request_payload", sa.JSON(), nullable=True),
        sa.Column("response", sa.JSON(), nullable=True),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("pushed_by", sa.String(), nullable=True),
        sa.Column(
            "pushed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_by", sa.String(), nullable=True),
        sa.Column("source", sa.String(), server_default="norm", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('journal', 'ico_invoice', 'ico_bill', 'entertainer_bill')",
            name="ck_pay_xero_pushes_kind",
        ),
        sa.CheckConstraint(
            "source IN ('norm', 'orbit')", name="ck_pay_xero_pushes_source"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "counterparty_venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_pay_xero_pushes_counterparty_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "draft_id"],
            ["pay_journal_drafts.organization_id", "pay_journal_drafts.id"],
            name="fk_pay_xero_pushes_draft_id",
            ondelete="SET NULL (draft_id)",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_pay_xero_pushes_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "week_id"],
            ["pay_weeks.organization_id", "pay_weeks.id"],
            name="fk_pay_xero_pushes_week_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_pay_xero_pushes_organization_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["pushed_by"],
            ["users.id"],
            name="fk_pay_xero_pushes_pushed_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["voided_by"],
            ["users.id"],
            name="fk_pay_xero_pushes_voided_by",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_pay_xero_pushes_key"),
        sa.UniqueConstraint("organization_id", "id", name="uq_pay_xero_pushes_org_id"),
        if_not_exists=True,
    )
    op.create_index(
        "uq_pay_rules_scope",
        "pay_rules",
        ["organization_id", "rule", "effective_from"],
        unique=True,
        postgresql_nulls_not_distinct=True,
        if_not_exists=True,
    )
    op.create_index(
        "ix_pay_gratuity_batches_venue_week",
        "pay_gratuity_batches",
        ["organization_id", "venue_id", "week_start"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_pay_leave_payouts_venue_week",
        "pay_leave_payouts",
        ["organization_id", "venue_id", "week_start"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_pay_adjustments_week_id",
        "pay_adjustments",
        ["organization_id", "week_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_pay_gratuity_allocations_batch_id",
        "pay_gratuity_allocations",
        ["organization_id", "batch_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_pay_snapshots_week_id",
        "pay_snapshots",
        ["organization_id", "week_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_pay_exports_week_id",
        "pay_exports",
        ["organization_id", "week_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_pay_journal_drafts_week_id",
        "pay_journal_drafts",
        ["organization_id", "week_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_pay_shift_facts_staff",
        "pay_shift_facts",
        ["organization_id", "snapshot_id", "staff_member_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_pay_xero_pushes_venue_week",
        "pay_xero_pushes",
        ["organization_id", "venue_id", "week_start"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "uq_pay_xero_pushes_live",
        "pay_xero_pushes",
        [
            "organization_id",
            "venue_id",
            "week_start",
            "kind",
            "counterparty_venue_id",
            "entertainer_performance_id",
        ],
        unique=True,
        postgresql_where=sa.text("voided_at IS NULL"),
        postgresql_nulls_not_distinct=True,
        if_not_exists=True,
    )


def downgrade() -> None:
    for table in reversed(PAY_TABLES):
        op.drop_table(table, if_exists=True)
