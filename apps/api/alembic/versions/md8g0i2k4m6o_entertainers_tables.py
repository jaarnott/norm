"""Entertainers: the ent_* tables (app/db/entertainers_models.py).

Eleven tables: performers (identity, lane, the encrypted tax and bank
details, the IR330C declaration record), band members and their fee shares,
Xero contacts per tenant, the stored weekly series and the performances
they make, the field-level change log, files (invoices, certificates), the
public links with their one-time-code state, the email log links, and the
organisation's settings. Bank account, IRD number and date of birth are
EncryptedText columns (plan §6.5: the key goes in first).

Idempotent on purpose, as the Functions and payroll migrations are.

Revision ID: md8g0i2k4m6o
Revises: lc7f9h1j3l5n
"""

import sqlalchemy as sa
from alembic import op

revision = "md8g0i2k4m6o"
down_revision = "lc7f9h1j3l5n"
branch_labels = None
depends_on = None

#: Creation order (parents first); downgrade drops in reverse. Pinned by
#: tests/test_finance_foundation.py against the model module.
ENT_TABLES = (
    "ent_files",
    "ent_settings",
    "ent_performers",
    "ent_band_members",
    "ent_performer_xero_contacts",
    "ent_series",
    "ent_email_links",
    "ent_performances",
    "ent_public_tokens",
    "ent_performance_changes",
    "ent_performance_shares",
)


def upgrade() -> None:
    op.create_table(
        "ent_files",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=True),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("source_ref", sa.String(), nullable=True),
        sa.Column("uploaded_by_user_id", sa.String(), nullable=True),
        sa.Column("invoice_number", sa.String(), nullable=True),
        sa.Column("invoice_cents", sa.Integer(), nullable=True),
        sa.Column(
            "check_status", sa.String(), server_default="pending", nullable=False
        ),
        sa.Column("extracted", sa.JSON(), nullable=True),
        sa.Column("check_findings", sa.JSON(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("waived_by", sa.String(), nullable=True),
        sa.Column("waived_at", sa.DateTime(timezone=True), nullable=True),
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
            "check_status IN ('pending', 'matched', 'query', 'unreadable', 'waived')",
            name="ck_ent_files_check_status",
        ),
        sa.CheckConstraint(
            "kind IN ('invoice', 'exemption_certificate', 'ir330c', 'company_document')",
            name="ck_ent_files_kind",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_ent_files_organization_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_user_id"],
            ["users.id"],
            name="fk_ent_files_uploaded_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["waived_by"],
            ["users.id"],
            name="fk_ent_files_waived_by",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_ent_files_org_id"),
        sa.UniqueConstraint("organization_id", "sha256", name="uq_ent_files_sha"),
        if_not_exists=True,
    )
    op.create_table(
        "ent_settings",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("closing_manager_roles", sa.JSON(), nullable=False),
        sa.Column(
            "mismatch_tolerance_minutes",
            sa.Integer(),
            server_default=sa.text("15"),
            nullable=False,
        ),
        sa.Column(
            "reminder_interval_days",
            sa.Integer(),
            server_default=sa.text("3"),
            nullable=False,
        ),
        sa.Column(
            "reminder_max", sa.Integer(), server_default=sa.text("4"), nullable=False
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
            ["organization_id"],
            ["organizations.id"],
            name="fk_ent_settings_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_ent_settings_org_id"),
        sa.UniqueConstraint("organization_id", name="uq_ent_settings_org"),
        if_not_exists=True,
    )
    op.create_table(
        "ent_performers",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("legal_name", sa.String(), nullable=True),
        sa.Column("trading_name", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("lane", sa.String(), nullable=True),
        sa.Column("performer_type", sa.String(), nullable=True),
        sa.Column("band_mode", sa.String(), nullable=True),
        sa.Column(
            "onboarding_status", sa.String(), server_default="invited", nullable=False
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by", sa.String(), nullable=True),
        sa.Column("bank_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "needs_review",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("review_reason", sa.Text(), nullable=True),
        # EncryptedText columns: TEXT in the database, ciphertext written by
        # the ORM type (app/db/encrypted.py).
        sa.Column("ird_number", sa.Text(), nullable=True),
        sa.Column("bank_account", sa.Text(), nullable=True),
        sa.Column("date_of_birth", sa.Text(), nullable=True),
        sa.Column("tax_code", sa.String(), nullable=True),
        sa.Column("kiwisaver_rate", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("payroll_code", sa.String(), nullable=True),
        sa.Column("gst_registered", sa.Boolean(), nullable=True),
        sa.Column("gst_number", sa.String(), nullable=True),
        sa.Column("company_name", sa.String(), nullable=True),
        sa.Column("company_number", sa.String(), nullable=True),
        sa.Column("company_document_file_id", sa.String(), nullable=True),
        sa.Column("exemption_certificate_number", sa.String(), nullable=True),
        sa.Column("exemption_expiry_date", sa.Date(), nullable=True),
        sa.Column("exemption_file_id", sa.String(), nullable=True),
        sa.Column("nz_tax_resident", sa.Boolean(), nullable=True),
        sa.Column("wt_rate_type", sa.String(), nullable=True),
        sa.Column("wt_rate_bp", sa.Integer(), nullable=True),
        sa.Column("declaration_snapshot", sa.JSON(), nullable=True),
        sa.Column("declaration_file_id", sa.String(), nullable=True),
        sa.Column(
            "band_leader_declaration_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("contractor_confirmations", sa.JSON(), nullable=True),
        sa.Column("invite_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "invite_reminders_sent",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("last_reminder_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("anonymised_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
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
            "band_mode IN ('leader', 'members_split')",
            name="ck_ent_performers_band_mode",
        ),
        sa.CheckConstraint(
            "lane IN ('payroll_employee', 'payroll_contractor', 'invoice', 'exemption')",
            name="ck_ent_performers_lane",
        ),
        sa.CheckConstraint(
            "onboarding_status IN ('invited', 'submitted', 'accepted')",
            name="ck_ent_performers_onboarding_status",
        ),
        sa.CheckConstraint(
            "performer_type IN ('solo', 'band', 'agent')",
            name="ck_ent_performers_performer_type",
        ),
        sa.CheckConstraint(
            "wt_rate_type IN ('standard', 'self_selected', 'tailored')",
            name="ck_ent_performers_wt_rate_type",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by"],
            ["users.id"],
            name="fk_ent_performers_accepted_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_ent_performers_created_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "company_document_file_id"],
            ["ent_files.organization_id", "ent_files.id"],
            name="fk_ent_performers_company_document_file_id",
            ondelete="SET NULL (company_document_file_id)",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "declaration_file_id"],
            ["ent_files.organization_id", "ent_files.id"],
            name="fk_ent_performers_declaration_file_id",
            ondelete="SET NULL (declaration_file_id)",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "exemption_file_id"],
            ["ent_files.organization_id", "ent_files.id"],
            name="fk_ent_performers_exemption_file_id",
            ondelete="SET NULL (exemption_file_id)",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_ent_performers_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_ent_performers_org_id"),
        if_not_exists=True,
    )
    op.create_table(
        "ent_band_members",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("band_performer_id", sa.String(), nullable=False),
        sa.Column("member_performer_id", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column(
            "share_bp", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "invite_status", sa.String(), server_default="invited", nullable=False
        ),
        sa.Column("invite_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "reminders_sent", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("last_reminder_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
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
            "invite_status IN ('invited', 'submitted', 'accepted')",
            name="ck_ent_band_members_invite_status",
        ),
        sa.CheckConstraint(
            "share_bp >= 0 AND share_bp <= 10000", name="ck_ent_band_members_share_bp"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "band_performer_id"],
            ["ent_performers.organization_id", "ent_performers.id"],
            name="fk_ent_band_members_band_performer_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "member_performer_id"],
            ["ent_performers.organization_id", "ent_performers.id"],
            name="fk_ent_band_members_member_performer_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_ent_band_members_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_ent_band_members_org_id"),
        if_not_exists=True,
    )
    op.create_table(
        "ent_performer_xero_contacts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("performer_id", sa.String(), nullable=False),
        sa.Column("xero_tenant_id", sa.String(), nullable=False),
        sa.Column("xero_contact_id", sa.String(), nullable=False),
        sa.Column("xero_contact_name", sa.String(), nullable=True),
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
            ["organization_id", "performer_id"],
            ["ent_performers.organization_id", "ent_performers.id"],
            name="fk_ent_performer_xero_contacts_performer_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_ent_performer_xero_contacts_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_ent_performer_xero_contacts_org_id"
        ),
        sa.UniqueConstraint(
            "performer_id",
            "xero_tenant_id",
            name="uq_ent_performer_xero_contacts_tenant",
        ),
        if_not_exists=True,
    )
    op.create_table(
        "ent_series",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("performer_id", sa.String(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column(
            "ends_next_day",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("rate_cents", sa.Integer(), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("materialised_through", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
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
        sa.CheckConstraint("weekday BETWEEN 1 AND 7", name="ck_ent_series_weekday"),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_ent_series_created_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "performer_id"],
            ["ent_performers.organization_id", "ent_performers.id"],
            name="fk_ent_series_performer_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_ent_series_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_ent_series_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_ent_series_org_id"),
        if_not_exists=True,
    )
    op.create_table(
        "ent_email_links",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("email_log_id", sa.String(), nullable=True),
        sa.Column("performer_id", sa.String(), nullable=True),
        sa.Column("band_member_id", sa.String(), nullable=True),
        sa.Column("venue_id", sa.String(), nullable=True),
        sa.Column("recipient_kind", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("recipient_email", sa.String(), nullable=True),
        sa.Column("subject", sa.String(), nullable=True),
        sa.Column("performance_ids", sa.JSON(), nullable=True),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
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
            "recipient_kind IN ('performer', 'band_member', 'closing_manager', 'venue_manager', 'finance')",
            name="ck_ent_email_links_recipient_kind",
        ),
        sa.CheckConstraint(
            "source IN ('norm', 'orbit')", name="ck_ent_email_links_source"
        ),
        sa.ForeignKeyConstraint(
            ["email_log_id"],
            ["email_logs.id"],
            name="fk_ent_email_links_email_log_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "band_member_id"],
            ["ent_band_members.organization_id", "ent_band_members.id"],
            name="fk_ent_email_links_band_member_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "performer_id"],
            ["ent_performers.organization_id", "ent_performers.id"],
            name="fk_ent_email_links_performer_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_ent_email_links_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_ent_email_links_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_ent_email_links_org_id"),
        if_not_exists=True,
    )
    op.create_table(
        "ent_performances",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("performer_id", sa.String(), nullable=False),
        sa.Column("series_id", sa.String(), nullable=True),
        sa.Column("performance_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column(
            "ends_next_day",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "rate_cents", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "agreed_cents", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("actual_start_time", sa.Time(), nullable=True),
        sa.Column("actual_end_time", sa.Time(), nullable=True),
        sa.Column(
            "actual_ends_next_day",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("actual_cents", sa.Integer(), nullable=True),
        sa.Column(
            "performance_status",
            sa.String(),
            server_default="scheduled",
            nullable=False,
        ),
        sa.Column("dnp_reason", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(), nullable=True),
        sa.Column("performer_actual_start_time", sa.Time(), nullable=True),
        sa.Column("performer_actual_end_time", sa.Time(), nullable=True),
        sa.Column(
            "performer_actual_ends_next_day",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("performer_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "performer_dnp",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("performer_dnp_reason", sa.Text(), nullable=True),
        sa.Column("closing_manager_staff_id", sa.String(), nullable=True),
        sa.Column("closing_manager_name", sa.String(), nullable=True),
        sa.Column("closing_manager_email", sa.String(), nullable=True),
        sa.Column("closing_manager_user_id", sa.String(), nullable=True),
        sa.Column("closing_manager_source", sa.String(), nullable=True),
        sa.Column(
            "closing_manager_resolved_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "payment_status", sa.String(), server_default="pending", nullable=False
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_by", sa.String(), nullable=True),
        sa.Column("paid_reference", sa.String(), nullable=True),
        sa.Column("payroll_week_start", sa.Date(), nullable=True),
        sa.Column("invoice_file_id", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
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
            "closing_manager_source IN ('clockin', 'both', 'rostered_only', 'none', 'orbit')",
            name="ck_ent_performances_closing_manager_source",
        ),
        sa.CheckConstraint(
            "payment_status IN ('pending', 'paid')",
            name="ck_ent_performances_payment_status",
        ),
        sa.CheckConstraint(
            "performance_status IN ('scheduled', 'approved', 'did_not_perform')",
            name="ck_ent_performances_performance_status",
        ),
        sa.ForeignKeyConstraint(
            ["approved_by"],
            ["users.id"],
            name="fk_ent_performances_approved_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["closing_manager_user_id"],
            ["users.id"],
            name="fk_ent_performances_closing_manager_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_ent_performances_created_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "invoice_file_id"],
            ["ent_files.organization_id", "ent_files.id"],
            name="fk_ent_performances_invoice_file_id",
            ondelete="SET NULL (invoice_file_id)",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "performer_id"],
            ["ent_performers.organization_id", "ent_performers.id"],
            name="fk_ent_performances_performer_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "series_id"],
            ["ent_series.organization_id", "ent_series.id"],
            name="fk_ent_performances_series_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_ent_performances_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_ent_performances_organization_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["paid_by"],
            ["users.id"],
            name="fk_ent_performances_paid_by",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_ent_performances_org_id"),
        if_not_exists=True,
    )
    op.create_table(
        "ent_public_tokens",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("performer_id", sa.String(), nullable=True),
        sa.Column("band_member_id", sa.String(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("otp_hash", sa.String(), nullable=True),
        sa.Column("otp_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "otp_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "otp_sends_in_window",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("otp_window_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("otp_verified_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "(purpose = 'band_member' AND band_member_id IS NOT NULL) OR (purpose <> 'band_member' AND performer_id IS NOT NULL)",
            name="ck_ent_public_tokens_target",
        ),
        sa.CheckConstraint(
            "purpose IN ('portal', 'onboarding', 'band_member', 'declaration')",
            name="ck_ent_public_tokens_purpose",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_ent_public_tokens_created_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "band_member_id"],
            ["ent_band_members.organization_id", "ent_band_members.id"],
            name="fk_ent_public_tokens_band_member_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "performer_id"],
            ["ent_performers.organization_id", "ent_performers.id"],
            name="fk_ent_public_tokens_performer_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_ent_public_tokens_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_ent_public_tokens_org_id"
        ),
        sa.UniqueConstraint("token_hash", name="uq_ent_public_tokens_hash"),
        if_not_exists=True,
    )
    op.create_table(
        "ent_performance_changes",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("performance_id", sa.String(), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("changed_by_source", sa.String(), nullable=False),
        sa.Column("changed_by_user_id", sa.String(), nullable=True),
        sa.Column("changed_by_name", sa.String(), nullable=True),
        sa.Column("field", sa.String(), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("ip_hash", sa.String(), nullable=True),
        sa.Column("user_agent", sa.String(), nullable=True),
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
            "changed_by_source IN ('manager', 'performer', 'system', 'import')",
            name="ck_ent_performance_changes_changed_by_source",
        ),
        sa.ForeignKeyConstraint(
            ["changed_by_user_id"],
            ["users.id"],
            name="fk_ent_performance_changes_changed_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "performance_id"],
            ["ent_performances.organization_id", "ent_performances.id"],
            name="fk_ent_performance_changes_performance_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_ent_performance_changes_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_ent_performance_changes_org_id"
        ),
        if_not_exists=True,
    )
    op.create_table(
        "ent_performance_shares",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("performance_id", sa.String(), nullable=False),
        sa.Column("band_member_id", sa.String(), nullable=False),
        sa.Column("member_performer_id", sa.String(), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_by", sa.String(), nullable=True),
        sa.Column("paid_reference", sa.String(), nullable=True),
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
            ["organization_id", "band_member_id"],
            ["ent_band_members.organization_id", "ent_band_members.id"],
            name="fk_ent_performance_shares_band_member_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "member_performer_id"],
            ["ent_performers.organization_id", "ent_performers.id"],
            name="fk_ent_performance_shares_member_performer_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "performance_id"],
            ["ent_performances.organization_id", "ent_performances.id"],
            name="fk_ent_performance_shares_performance_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_ent_performance_shares_organization_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["paid_by"],
            ["users.id"],
            name="fk_ent_performance_shares_paid_by",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_ent_performance_shares_org_id"
        ),
        sa.UniqueConstraint(
            "performance_id", "band_member_id", name="uq_ent_performance_shares_member"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_ent_performers_email",
        "ent_performers",
        ["organization_id", sa.literal_column("lower(email)")],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_ent_band_members_band_performer_id",
        "ent_band_members",
        ["organization_id", "band_performer_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_ent_performer_xero_contacts_performer_id",
        "ent_performer_xero_contacts",
        ["organization_id", "performer_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_ent_series_performer_id",
        "ent_series",
        ["organization_id", "performer_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_ent_email_links_performer_id",
        "ent_email_links",
        ["organization_id", "performer_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_ent_email_links_sent",
        "ent_email_links",
        ["organization_id", "sent_at"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_ent_performances_invoice_file_id",
        "ent_performances",
        ["organization_id", "invoice_file_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_ent_performances_performer_id",
        "ent_performances",
        ["organization_id", "performer_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_ent_performances_series_id",
        "ent_performances",
        ["organization_id", "series_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_ent_performances_venue_date",
        "ent_performances",
        ["organization_id", "venue_id", "performance_date"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_ent_public_tokens_band_member_id",
        "ent_public_tokens",
        ["organization_id", "band_member_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_ent_public_tokens_performer_id",
        "ent_public_tokens",
        ["organization_id", "performer_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_ent_performance_changes_performance_id",
        "ent_performance_changes",
        ["organization_id", "performance_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_ent_performance_shares_performance_id",
        "ent_performance_shares",
        ["organization_id", "performance_id"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    for table in reversed(ENT_TABLES):
        op.drop_table(table, if_exists=True)
