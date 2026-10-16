"""Norm Functions: the fn_* tables and company settings.

* ``organizations`` gains the company settings Functions prices and dates
  with — ``currency``, ``default_timezone``, ``tax_label`` — and
  ``functions_mode`` (off | shadow | live), the per-organisation switch.
* ``venues`` gains ``UNIQUE (organization_id, id)`` so every Functions table
  can reference ``(organization_id, venue_id)`` and a cross-organisation venue
  reference becomes impossible.
* The 43 ``fn_*`` tables (app/db/functions_models.py): contacts, enquiries,
  minimum spends with their approval and response facts, events and their
  sections, pre-order menus, proposals, the venue functions profile, venue
  mailboxes and the outbox, drafts, public tokens and the client portal, web
  forms.

Idempotent on purpose. The test suite builds tables with create_all on the
shared local database, so a dev database can already hold every fn_* table,
the new columns and the venue key by the time this runs; ``if_not_exists`` /
``IF NOT EXISTS`` let ``alembic upgrade head`` (and ./scripts/dev.sh) pass
over them instead of failing on "already exists".

Revision ID: fa0b1c2d3e4f
Revises: zc4f1b2a9e7d
"""

import sqlalchemy as sa
from alembic import op

revision = "fa0b1c2d3e4f"
down_revision = "zc4f1b2a9e7d"
branch_labels = None
depends_on = None

#: Creation order (parents first); downgrade drops in reverse.
FN_TABLES = (
    "fn_contacts",
    "fn_files",
    "fn_contact_emails",
    "fn_enquiries",
    "fn_preorder_templates",
    "fn_proposal_images",
    "fn_reply_templates",
    "fn_venue_areas",
    "fn_venue_brands",
    "fn_venue_function_settings",
    "fn_web_forms",
    "fn_enquiry_closures",
    "fn_events",
    "fn_form_submissions",
    "fn_mailbox_accounts",
    "fn_preorder_template_items",
    "fn_proposals",
    "fn_venue_area_photos",
    "fn_web_form_fields",
    "fn_email_drafts",
    "fn_event_av",
    "fn_event_changes",
    "fn_event_entertainment",
    "fn_event_section_confirmations",
    "fn_event_security",
    "fn_event_timings",
    "fn_mailbox_labels",
    "fn_mailbox_retry",
    "fn_mailbox_threads",
    "fn_min_spends",
    "fn_portal_access",
    "fn_portal_sessions",
    "fn_preorder_menus",
    "fn_proposal_versions",
    "fn_thread_folders",
    "fn_thread_links",
    "fn_mailbox_messages",
    "fn_min_spend_approvals",
    "fn_outbox",
    "fn_preorder_menu_items",
    "fn_public_tokens",
    "fn_min_spend_responses",
    "fn_proposal_events",
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE organizations "
        "ADD COLUMN IF NOT EXISTS currency VARCHAR(3) NOT NULL DEFAULT 'NZD', "
        "ADD COLUMN IF NOT EXISTS default_timezone VARCHAR NOT NULL "
        "DEFAULT 'Pacific/Auckland', "
        "ADD COLUMN IF NOT EXISTS tax_label VARCHAR NOT NULL DEFAULT 'incl. GST', "
        "ADD COLUMN IF NOT EXISTS functions_mode VARCHAR NOT NULL DEFAULT 'off' "
        "CONSTRAINT ck_organizations_functions_mode "
        "CHECK (functions_mode IN ('off', 'shadow', 'live'))"
    )
    op.execute(
        "DO $$ BEGIN "
        "ALTER TABLE venues ADD CONSTRAINT uq_venues_org_id "
        "UNIQUE (organization_id, id); "
        "EXCEPTION WHEN duplicate_table OR duplicate_object THEN NULL; "
        "END $$"
    )
    op.create_table(
        "fn_contacts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("company", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("anonymised_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
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
            ["created_by_user_id"],
            ["users.id"],
            name="fk_fn_contacts_created_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_contacts_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_fn_contacts_org_id"),
        if_not_exists=True,
    )
    op.create_table(
        "fn_files",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(), nullable=True),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("source_ref", sa.String(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
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
            ["created_by_user_id"],
            ["users.id"],
            name="fk_fn_files_created_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_files_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_fn_files_org_id"),
        sa.UniqueConstraint("organization_id", "sha256", name="uq_fn_files_sha"),
        if_not_exists=True,
    )
    op.create_table(
        "fn_contact_emails",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("contact_id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column(
            "is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
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
            ["organization_id", "contact_id"],
            ["fn_contacts.organization_id", "fn_contacts.id"],
            name="fk_fn_contact_emails_contact_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_contact_emails_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fn_contact_emails_org_id"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_contact_emails_contact_id",
        "fn_contact_emails",
        ["organization_id", "contact_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "uq_fn_contact_emails_org_email",
        "fn_contact_emails",
        ["organization_id", sa.literal_column("lower(email)")],
        unique=True,
        if_not_exists=True,
    )
    op.create_table(
        "fn_enquiries",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("contact_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=True),
        sa.Column("booking_type", sa.String(), nullable=True),
        sa.Column("subject", sa.String(), nullable=True),
        sa.Column("guests", sa.Integer(), nullable=True),
        sa.Column("preferred_date_start", sa.Date(), nullable=True),
        sa.Column("preferred_date_end", sa.Date(), nullable=True),
        sa.Column("preferred_start_time", sa.Time(), nullable=True),
        sa.Column("preferred_end_time", sa.Time(), nullable=True),
        sa.Column(
            "preferred_ends_next_day",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("utm_source", sa.String(), nullable=True),
        sa.Column("utm_medium", sa.String(), nullable=True),
        sa.Column("utm_campaign", sa.String(), nullable=True),
        sa.Column("utm_term", sa.String(), nullable=True),
        sa.Column("utm_content", sa.String(), nullable=True),
        sa.Column("gclid", sa.String(), nullable=True),
        sa.Column("referrer", sa.Text(), nullable=True),
        sa.Column("landing_page", sa.Text(), nullable=True),
        sa.Column("owner_user_id", sa.String(), nullable=True),
        sa.Column("follow_up_date", sa.Date(), nullable=True),
        sa.Column("follow_up_reason", sa.Text(), nullable=True),
        sa.Column("client_note", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("created_by_label", sa.String(), nullable=True),
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
            "booking_type IN ('seated_dining', 'group_booking', 'large_event_partial', 'large_event_exclusive')",
            name="ck_fn_enquiries_booking_type",
        ),
        sa.CheckConstraint(
            "source IN ('email', 'web_form', 'phone', 'walk_in', 'manual', 'agent', 'import', 'other')",
            name="ck_fn_enquiries_source",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_fn_enquiries_created_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "contact_id"],
            ["fn_contacts.organization_id", "fn_contacts.id"],
            name="fk_fn_enquiries_contact_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_fn_enquiries_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_enquiries_organization_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_fn_enquiries_owner_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_fn_enquiries_org_id"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_enquiries_contact_id",
        "fn_enquiries",
        ["organization_id", "contact_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_enquiries_created",
        "fn_enquiries",
        ["organization_id", "created_at"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_enquiries_venue_id",
        "fn_enquiries",
        ["organization_id", "venue_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_preorder_templates",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_set_menu", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("price_per_person_cents", sa.Integer(), nullable=True),
        sa.Column("loaded_menu_id", sa.String(), nullable=True),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False
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
            name="fk_fn_preorder_templates_venue_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_preorder_templates_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fn_preorder_templates_org_id"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_preorder_templates_venue_id",
        "fn_preorder_templates",
        ["organization_id", "venue_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_proposal_images",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("file_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("caption", sa.String(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
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
            ["created_by_user_id"],
            ["users.id"],
            name="fk_fn_proposal_images_created_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "file_id"],
            ["fn_files.organization_id", "fn_files.id"],
            name="fk_fn_proposal_images_file_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_fn_proposal_images_venue_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_proposal_images_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fn_proposal_images_org_id"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_proposal_images_file_id",
        "fn_proposal_images",
        ["organization_id", "file_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_proposal_images_venue_id",
        "fn_proposal_images",
        ["organization_id", "venue_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_reply_templates",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=True),
        sa.Column("booking_type", sa.String(), nullable=True),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=True),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False
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
            "booking_type IN ('seated_dining', 'group_booking', 'large_event_partial', 'large_event_exclusive')",
            name="ck_fn_reply_templates_booking_type",
        ),
        sa.CheckConstraint(
            "stage IN ('first_reply', 'follow_up')", name="ck_fn_reply_templates_stage"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_fn_reply_templates_venue_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_reply_templates_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fn_reply_templates_org_id"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_reply_templates_venue_id",
        "fn_reply_templates",
        ["organization_id", "venue_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_venue_areas",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("capacity_seated", sa.Integer(), nullable=True),
        sa.Column("capacity_standing", sa.Integer(), nullable=True),
        sa.Column("booking_types", sa.JSON(), nullable=False),
        sa.Column(
            "requires_approval",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("min_spend_notes", sa.Text(), nullable=True),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False
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
            name="fk_fn_venue_areas_venue_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_venue_areas_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_fn_venue_areas_org_id"),
        sa.UniqueConstraint(
            "organization_id", "venue_id", "id", name="uq_fn_venue_areas_venue_id"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_venue_areas_venue_id",
        "fn_venue_areas",
        ["organization_id", "venue_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_venue_brands",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("logo_file_id", sa.String(), nullable=True),
        sa.Column("cover_file_id", sa.String(), nullable=True),
        sa.Column("closing_file_id", sa.String(), nullable=True),
        sa.Column("wordmark", sa.String(), nullable=True),
        sa.Column("tagline", sa.String(), nullable=True),
        sa.Column("colours", sa.JSON(), nullable=False),
        sa.Column("fonts", sa.JSON(), nullable=False),
        sa.Column("terms", sa.JSON(), nullable=False),
        sa.Column("inclusions", sa.JSON(), nullable=False),
        sa.Column("signoff", sa.JSON(), nullable=False),
        sa.Column("style", sa.JSON(), nullable=False),
        sa.Column("footer_contact", sa.Text(), nullable=True),
        sa.Column(
            "valid_days", sa.Integer(), server_default=sa.text("14"), nullable=False
        ),
        sa.Column("template_html", sa.Text(), nullable=True),
        sa.Column("template_css", sa.Text(), nullable=True),
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
            ["organization_id", "closing_file_id"],
            ["fn_files.organization_id", "fn_files.id"],
            name="fk_fn_venue_brands_closing_file_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "cover_file_id"],
            ["fn_files.organization_id", "fn_files.id"],
            name="fk_fn_venue_brands_cover_file_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "logo_file_id"],
            ["fn_files.organization_id", "fn_files.id"],
            name="fk_fn_venue_brands_logo_file_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_fn_venue_brands_venue_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_venue_brands_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_fn_venue_brands_org_id"),
        sa.UniqueConstraint(
            "organization_id", "venue_id", name="uq_fn_venue_brands_venue"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_venue_brands_closing_file_id",
        "fn_venue_brands",
        ["organization_id", "closing_file_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_venue_brands_cover_file_id",
        "fn_venue_brands",
        ["organization_id", "cover_file_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_venue_brands_logo_file_id",
        "fn_venue_brands",
        ["organization_id", "logo_file_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_venue_function_settings",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("approver_user_id", sa.String(), nullable=True),
        sa.Column("override_approver_user_id", sa.String(), nullable=True),
        sa.Column("policies", sa.Text(), nullable=True),
        sa.Column("menu_links", sa.JSON(), nullable=False),
        sa.Column("enabled_fields", sa.JSON(), nullable=False),
        sa.Column("notify_emails", sa.JSON(), nullable=False),
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
            ["approver_user_id"],
            ["users.id"],
            name="fk_fn_venue_function_settings_approver_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_fn_venue_function_settings_venue_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_venue_function_settings_organization_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["override_approver_user_id"],
            ["users.id"],
            name="fk_fn_venue_function_settings_override_approver_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fn_venue_function_settings_org_id"
        ),
        sa.UniqueConstraint(
            "organization_id", "venue_id", name="uq_fn_venue_function_settings_venue"
        ),
        if_not_exists=True,
    )
    op.create_table(
        "fn_web_forms",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("theme", sa.JSON(), nullable=False),
        sa.Column("intro_html", sa.Text(), nullable=True),
        sa.Column("submit_label", sa.String(), nullable=False),
        sa.Column("success_message", sa.Text(), nullable=True),
        sa.Column("recipient_email", sa.String(), nullable=True),
        sa.Column("subject_template", sa.String(), nullable=True),
        sa.Column("reply_to_field_key", sa.String(), nullable=True),
        sa.Column("honeypot_field", sa.String(), nullable=False),
        sa.Column("booking_type", sa.String(), nullable=True),
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
            "booking_type IN ('seated_dining', 'group_booking', 'large_event_partial', 'large_event_exclusive')",
            name="ck_fn_web_forms_booking_type",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_fn_web_forms_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_web_forms_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_fn_web_forms_org_id"),
        sa.UniqueConstraint("slug", name="uq_fn_web_forms_slug"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_web_forms_venue_id",
        "fn_web_forms",
        ["organization_id", "venue_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_enquiry_closures",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("enquiry_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.String(), nullable=True),
        sa.Column("actor_label", sa.String(), nullable=True),
        sa.Column(
            "occurred_at",
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
            "kind IN ('lost', 'reopened')", name="ck_fn_enquiry_closures_kind"
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_fn_enquiry_closures_actor_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "enquiry_id"],
            ["fn_enquiries.organization_id", "fn_enquiries.id"],
            name="fk_fn_enquiry_closures_enquiry_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_enquiry_closures_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fn_enquiry_closures_org_id"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_enquiry_closures_enquiry_id",
        "fn_enquiry_closures",
        ["organization_id", "enquiry_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("enquiry_id", sa.String(), nullable=True),
        sa.Column("contact_id", sa.String(), nullable=False),
        sa.Column("invoice_contact_id", sa.String(), nullable=True),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("area_id", sa.String(), nullable=True),
        sa.Column("booking_type", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column(
            "ends_next_day",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("guests", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("special_instructions", sa.Text(), nullable=True),
        sa.Column("allergies", sa.Text(), nullable=True),
        sa.Column(
            "bar_tab_cents", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("bar_tab_authorised_people", sa.Text(), nullable=True),
        sa.Column("bar_tab_restrictions", sa.Text(), nullable=True),
        sa.Column("bar_tab_notes", sa.Text(), nullable=True),
        sa.Column("arrival_drinks", sa.Text(), nullable=True),
        sa.Column(
            "table_ordering",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("table_ordering_limit_cents", sa.Integer(), nullable=True),
        sa.Column("payment_method", sa.String(), nullable=True),
        sa.Column("deposit_cents", sa.Integer(), nullable=True),
        sa.Column("deposit_status", sa.String(), nullable=True),
        sa.Column("food_timing_type", sa.String(), nullable=True),
        sa.Column("food_time", sa.Time(), nullable=True),
        sa.Column("food_end_time", sa.Time(), nullable=True),
        sa.Column("food_notes", sa.Text(), nullable=True),
        sa.Column("arrival_time", sa.Time(), nullable=True),
        sa.Column("seated_time", sa.Time(), nullable=True),
        sa.Column("timing_preference", sa.Text(), nullable=True),
        sa.Column("timing_notes", sa.Text(), nullable=True),
        sa.Column("table_plan", sa.Text(), nullable=True),
        sa.Column("table_plan_file_id", sa.String(), nullable=True),
        sa.Column("created_via", sa.String(), nullable=False),
        sa.Column("confirmation_channel", sa.String(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("created_by_label", sa.String(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("cancelled_by_user_id", sa.String(), nullable=True),
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
            "booking_type IN ('seated_dining', 'group_booking', 'large_event_partial', 'large_event_exclusive')",
            name="ck_fn_events_booking_type",
        ),
        sa.CheckConstraint(
            "confirmation_channel IN ('quote_page', 'email', 'phone', 'in_person', 'import')",
            name="ck_fn_events_confirmation_channel",
        ),
        sa.CheckConstraint(
            "created_via IN ('min_spend', 'booking', 'direct', 'import')",
            name="ck_fn_events_created_via",
        ),
        sa.CheckConstraint(
            "food_timing_type IN ('all_at_once_arrival', 'staggered_arrival', 'all_at_once_time', 'staggered_time')",
            name="ck_fn_events_food_timing_type",
        ),
        sa.CheckConstraint("bar_tab_cents >= 0", name="ck_fn_events_bar_tab"),
        sa.ForeignKeyConstraint(
            ["cancelled_by_user_id"],
            ["users.id"],
            name="fk_fn_events_cancelled_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_fn_events_created_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "contact_id"],
            ["fn_contacts.organization_id", "fn_contacts.id"],
            name="fk_fn_events_contact_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "enquiry_id"],
            ["fn_enquiries.organization_id", "fn_enquiries.id"],
            name="fk_fn_events_enquiry_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "invoice_contact_id"],
            ["fn_contacts.organization_id", "fn_contacts.id"],
            name="fk_fn_events_invoice_contact_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "table_plan_file_id"],
            ["fn_files.organization_id", "fn_files.id"],
            name="fk_fn_events_table_plan_file_id",
            ondelete="SET NULL (table_plan_file_id)",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id", "area_id"],
            [
                "fn_venue_areas.organization_id",
                "fn_venue_areas.venue_id",
                "fn_venue_areas.id",
            ],
            name="fk_fn_events_area_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_fn_events_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_events_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_fn_events_org_id"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_events_area_id",
        "fn_events",
        ["organization_id", "venue_id", "area_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_events_contact_id",
        "fn_events",
        ["organization_id", "contact_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_events_enquiry_id",
        "fn_events",
        ["organization_id", "enquiry_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_events_invoice_contact_id",
        "fn_events",
        ["organization_id", "invoice_contact_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_events_table_plan_file_id",
        "fn_events",
        ["organization_id", "table_plan_file_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_events_venue_date",
        "fn_events",
        ["organization_id", "venue_id", "event_date"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "uq_fn_events_live_enquiry",
        "fn_events",
        ["organization_id", "enquiry_id"],
        unique=True,
        postgresql_where=sa.text("enquiry_id IS NOT NULL AND cancelled_at IS NULL"),
        if_not_exists=True,
    )
    op.create_table(
        "fn_form_submissions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("form_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("utm_source", sa.String(), nullable=True),
        sa.Column("utm_medium", sa.String(), nullable=True),
        sa.Column("utm_campaign", sa.String(), nullable=True),
        sa.Column("utm_term", sa.String(), nullable=True),
        sa.Column("utm_content", sa.String(), nullable=True),
        sa.Column("gclid", sa.String(), nullable=True),
        sa.Column("gbraid", sa.String(), nullable=True),
        sa.Column("wbraid", sa.String(), nullable=True),
        sa.Column("referrer", sa.Text(), nullable=True),
        sa.Column("landing_page", sa.Text(), nullable=True),
        sa.Column("ref_code", sa.String(), nullable=True),
        sa.Column("ip_hash", sa.String(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("spam_reason", sa.String(), nullable=True),
        sa.Column("delivery_status", sa.String(), nullable=False),
        sa.Column("delivery_error", sa.Text(), nullable=True),
        sa.Column("delivery_message_id", sa.String(), nullable=True),
        sa.Column("enquiry_id", sa.String(), nullable=True),
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
            "delivery_status IN ('pending', 'sent', 'failed', 'skipped')",
            name="ck_fn_form_submissions_delivery_status",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "enquiry_id"],
            ["fn_enquiries.organization_id", "fn_enquiries.id"],
            name="fk_fn_form_submissions_enquiry_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "form_id"],
            ["fn_web_forms.organization_id", "fn_web_forms.id"],
            name="fk_fn_form_submissions_form_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_fn_form_submissions_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_form_submissions_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fn_form_submissions_org_id"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_form_submissions_enquiry_id",
        "fn_form_submissions",
        ["organization_id", "enquiry_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_form_submissions_form_id",
        "fn_form_submissions",
        ["organization_id", "form_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_form_submissions_submitted",
        "fn_form_submissions",
        ["organization_id", "submitted_at"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_form_submissions_venue_id",
        "fn_form_submissions",
        ["organization_id", "venue_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_mailbox_accounts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("connection_id", sa.String(), nullable=True),
        sa.Column("google_email", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("history_id", sa.BigInteger(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("reconnect_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signature_html", sa.Text(), nullable=True),
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
            "status IN ('active', 'needs_reconnect', 'resyncing', 'disabled')",
            name="ck_fn_mailbox_accounts_status",
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["connector_configs.id"],
            name="fk_fn_mailbox_accounts_connection_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_fn_mailbox_accounts_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_mailbox_accounts_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fn_mailbox_accounts_org_id"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_mailbox_accounts_lease",
        "fn_mailbox_accounts",
        ["status", "lease_until"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_mailbox_accounts_venue_id",
        "fn_mailbox_accounts",
        ["organization_id", "venue_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "uq_fn_mailbox_accounts_email",
        "fn_mailbox_accounts",
        ["organization_id", sa.literal_column("lower(google_email)")],
        unique=True,
        if_not_exists=True,
    )
    op.create_table(
        "fn_preorder_template_items",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("template_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("unit_price_cents", sa.Integer(), nullable=True),
        sa.Column("default_percent", sa.Integer(), nullable=True),
        sa.Column("dietaries", sa.JSON(), nullable=False),
        sa.Column("loaded_recipe_id", sa.String(), nullable=True),
        sa.Column("loaded_stock_item_id", sa.String(), nullable=True),
        sa.Column(
            "sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False
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
            "kind IN ('header', 'item')", name="ck_fn_preorder_template_items_kind"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "template_id"],
            ["fn_preorder_templates.organization_id", "fn_preorder_templates.id"],
            name="fk_fn_preorder_template_items_template_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_preorder_template_items_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fn_preorder_template_items_org_id"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_preorder_template_items_template_id",
        "fn_preorder_template_items",
        ["organization_id", "template_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_proposals",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("enquiry_id", sa.String(), nullable=False),
        sa.Column("html", sa.Text(), nullable=False),
        sa.Column("css", sa.Text(), nullable=False),
        sa.Column("based_on_brand_version", sa.Integer(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
        sa.Column("updated_by_label", sa.String(), nullable=True),
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
            ["organization_id", "enquiry_id"],
            ["fn_enquiries.organization_id", "fn_enquiries.id"],
            name="fk_fn_proposals_enquiry_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_proposals_organization_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name="fk_fn_proposals_updated_by_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "enquiry_id", name="uq_fn_proposals_enquiry"
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_fn_proposals_org_id"),
        if_not_exists=True,
    )
    op.create_table(
        "fn_venue_area_photos",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("area_id", sa.String(), nullable=False),
        sa.Column("file_id", sa.String(), nullable=False),
        sa.Column("caption", sa.String(), nullable=True),
        sa.Column(
            "sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False
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
        sa.ForeignKeyConstraint(
            ["organization_id", "area_id"],
            ["fn_venue_areas.organization_id", "fn_venue_areas.id"],
            name="fk_fn_venue_area_photos_area_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "file_id"],
            ["fn_files.organization_id", "fn_files.id"],
            name="fk_fn_venue_area_photos_file_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_venue_area_photos_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fn_venue_area_photos_org_id"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_venue_area_photos_area_id",
        "fn_venue_area_photos",
        ["organization_id", "area_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_venue_area_photos_file_id",
        "fn_venue_area_photos",
        ["organization_id", "file_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_web_form_fields",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("form_id", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column(
            "required", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("options", sa.JSON(), nullable=True),
        sa.Column("placeholder", sa.String(), nullable=True),
        sa.Column("help_text", sa.String(), nullable=True),
        sa.Column("maps_to", sa.String(), nullable=True),
        sa.Column(
            "sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False
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
            "type IN ('name', 'email', 'phone', 'text', 'long_text', 'number', 'date', 'dates', 'time_range', 'choice', 'checkbox', 'section')",
            name="ck_fn_web_form_fields_type",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "form_id"],
            ["fn_web_forms.organization_id", "fn_web_forms.id"],
            name="fk_fn_web_form_fields_form_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_web_form_fields_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("form_id", "key", name="uq_fn_web_form_fields_key"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fn_web_form_fields_org_id"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_web_form_fields_form_id",
        "fn_web_form_fields",
        ["organization_id", "form_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_email_drafts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("enquiry_id", sa.String(), nullable=True),
        sa.Column("event_id", sa.String(), nullable=True),
        sa.Column("account_id", sa.String(), nullable=True),
        sa.Column("gmail_thread_id", sa.String(), nullable=True),
        sa.Column("in_reply_to_gmail_message_id", sa.String(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=True),
        sa.Column("to_emails", sa.JSON(), nullable=False),
        sa.Column("cc_emails", sa.JSON(), nullable=False),
        sa.Column("bcc_emails", sa.JSON(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("owner", sa.String(), nullable=False),
        sa.Column("gmail_draft_id", sa.String(), nullable=True),
        sa.Column("handed_to_gmail_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_gmail_message_id", sa.String(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("created_by_label", sa.String(), nullable=True),
        sa.Column("updated_by_user_id", sa.String(), nullable=True),
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
            "kind IN ('email', 'proposal')", name="ck_fn_email_drafts_kind"
        ),
        sa.CheckConstraint(
            "mode IN ('reply', 'reply_all', 'forward', 'new')",
            name="ck_fn_email_drafts_mode",
        ),
        sa.CheckConstraint(
            "owner IN ('norm', 'gmail')", name="ck_fn_email_drafts_owner"
        ),
        sa.CheckConstraint(
            "status IN ('open', 'sent', 'discarded')", name="ck_fn_email_drafts_status"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_fn_email_drafts_created_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "account_id"],
            ["fn_mailbox_accounts.organization_id", "fn_mailbox_accounts.id"],
            name="fk_fn_email_drafts_account_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "enquiry_id"],
            ["fn_enquiries.organization_id", "fn_enquiries.id"],
            name="fk_fn_email_drafts_enquiry_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "event_id"],
            ["fn_events.organization_id", "fn_events.id"],
            name="fk_fn_email_drafts_event_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_email_drafts_organization_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name="fk_fn_email_drafts_updated_by_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_fn_email_drafts_org_id"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_email_drafts_account_id",
        "fn_email_drafts",
        ["organization_id", "account_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_email_drafts_enquiry_id",
        "fn_email_drafts",
        ["organization_id", "enquiry_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_email_drafts_event_id",
        "fn_email_drafts",
        ["organization_id", "event_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "uq_fn_email_drafts_gmail",
        "fn_email_drafts",
        ["account_id", "gmail_draft_id"],
        unique=True,
        postgresql_where=sa.text("gmail_draft_id IS NOT NULL"),
        if_not_exists=True,
    )
    op.create_table(
        "fn_event_av",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column(
            "sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False
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
            "kind IN ('in_house_mic', 'in_house_tv', 'external_av')",
            name="ck_fn_event_av_kind",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "event_id"],
            ["fn_events.organization_id", "fn_events.id"],
            name="fk_fn_event_av_event_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_event_av_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_fn_event_av_org_id"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_event_av_event_id",
        "fn_event_av",
        ["organization_id", "event_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_event_changes",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("actor_kind", sa.String(), nullable=False),
        sa.Column("actor_user_id", sa.String(), nullable=True),
        sa.Column("client_email", sa.String(), nullable=True),
        sa.Column("actor_label", sa.String(), nullable=True),
        sa.Column("section", sa.String(), nullable=True),
        sa.Column("changes", sa.JSON(), nullable=True),
        sa.Column(
            "after_confirmation",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "occurred_at",
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
            "actor_kind IN ('staff', 'client', 'system', 'import')",
            name="ck_fn_event_changes_actor_kind",
        ),
        sa.CheckConstraint(
            "kind IN ('created', 'edited', 'link_sent', 'cancelled', 'reinstated')",
            name="ck_fn_event_changes_kind",
        ),
        sa.CheckConstraint(
            "section IN ('details', 'beverages', 'food')",
            name="ck_fn_event_changes_section",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_fn_event_changes_actor_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "event_id"],
            ["fn_events.organization_id", "fn_events.id"],
            name="fk_fn_event_changes_event_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_event_changes_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_fn_event_changes_org_id"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_event_changes_event_id",
        "fn_event_changes",
        ["organization_id", "event_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_event_changes_kind",
        "fn_event_changes",
        ["organization_id", "event_id", "kind", "occurred_at"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_event_entertainment",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column(
            "ends_next_day",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False
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
            "kind IN ('dj', 'playlist')", name="ck_fn_event_entertainment_kind"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "event_id"],
            ["fn_events.organization_id", "fn_events.id"],
            name="fk_fn_event_entertainment_event_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_event_entertainment_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fn_event_entertainment_org_id"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_event_entertainment_event_id",
        "fn_event_entertainment",
        ["organization_id", "event_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_event_section_confirmations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("section", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("actor_kind", sa.String(), nullable=False),
        sa.Column("client_email", sa.String(), nullable=True),
        sa.Column("actor_user_id", sa.String(), nullable=True),
        sa.Column("actor_label", sa.String(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "occurred_at",
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
            "actor_kind IN ('staff', 'client', 'system', 'import')",
            name="ck_fn_event_section_confirmations_actor_kind",
        ),
        sa.CheckConstraint(
            "kind IN ('confirmed', 'skipped', 'reopened')",
            name="ck_fn_event_section_confirmations_kind",
        ),
        sa.CheckConstraint(
            "section IN ('details', 'beverages', 'food')",
            name="ck_fn_event_section_confirmations_section",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_fn_event_section_confirmations_actor_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "event_id"],
            ["fn_events.organization_id", "fn_events.id"],
            name="fk_fn_event_section_confirmations_event_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_event_section_confirmations_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fn_event_section_confirmations_org_id"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_event_section_confirmations_event_id",
        "fn_event_section_confirmations",
        ["organization_id", "event_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_event_security",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column(
            "ends_next_day",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False
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
        sa.ForeignKeyConstraint(
            ["organization_id", "event_id"],
            ["fn_events.organization_id", "fn_events.id"],
            name="fk_fn_event_security_event_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_event_security_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fn_event_security_org_id"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_event_security_event_id",
        "fn_event_security",
        ["organization_id", "event_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_event_timings",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("at_time", sa.Time(), nullable=True),
        sa.Column(
            "ends_next_day",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("activity", sa.Text(), nullable=False),
        sa.Column(
            "is_heading", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False
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
        sa.ForeignKeyConstraint(
            ["organization_id", "event_id"],
            ["fn_events.organization_id", "fn_events.id"],
            name="fk_fn_event_timings_event_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_event_timings_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_fn_event_timings_org_id"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_event_timings_event_id",
        "fn_event_timings",
        ["organization_id", "event_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_mailbox_labels",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("gmail_label_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("label_type", sa.String(), nullable=True),
        sa.Column("folder", sa.String(), nullable=True),
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
            "folder IN ('enquiry', 'reservation', 'other')",
            name="ck_fn_mailbox_labels_folder",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "account_id"],
            ["fn_mailbox_accounts.organization_id", "fn_mailbox_accounts.id"],
            name="fk_fn_mailbox_labels_account_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_mailbox_labels_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id", "gmail_label_id", name="uq_fn_mailbox_labels_gmail"
        ),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fn_mailbox_labels_org_id"
        ),
        if_not_exists=True,
    )
    op.create_table(
        "fn_mailbox_retry",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("ref", sa.String(), nullable=False),
        sa.Column(
            "attempts", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parked_at", sa.DateTime(timezone=True), nullable=True),
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
            ["organization_id", "account_id"],
            ["fn_mailbox_accounts.organization_id", "fn_mailbox_accounts.id"],
            name="fk_fn_mailbox_retry_account_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_mailbox_retry_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id", "kind", "ref", name="uq_fn_mailbox_retry_ref"
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_fn_mailbox_retry_org_id"),
        if_not_exists=True,
    )
    op.create_table(
        "fn_mailbox_threads",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("gmail_thread_id", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_from_email", sa.String(), nullable=True),
        sa.Column("last_from_name", sa.String(), nullable=True),
        sa.Column(
            "message_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "unread_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("label_ids", sa.JSON(), nullable=False),
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
            ["organization_id", "account_id"],
            ["fn_mailbox_accounts.organization_id", "fn_mailbox_accounts.id"],
            name="fk_fn_mailbox_threads_account_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_mailbox_threads_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id", "gmail_thread_id", name="uq_fn_mailbox_threads_gmail"
        ),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fn_mailbox_threads_org_id"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_mailbox_threads_recent",
        "fn_mailbox_threads",
        ["organization_id", "account_id", "last_message_at"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_min_spends",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("enquiry_id", sa.String(), nullable=True),
        sa.Column("event_id", sa.String(), nullable=True),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("area_id", sa.String(), nullable=True),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column(
            "ends_next_day",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_by_user_id", sa.String(), nullable=True),
        sa.Column("withdraw_reason", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("created_by_label", sa.String(), nullable=True),
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
        sa.CheckConstraint("amount_cents >= 0", name="ck_fn_min_spends_amount"),
        sa.CheckConstraint(
            "enquiry_id IS NOT NULL OR event_id IS NOT NULL",
            name="ck_fn_min_spends_parent",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_fn_min_spends_created_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "enquiry_id"],
            ["fn_enquiries.organization_id", "fn_enquiries.id"],
            name="fk_fn_min_spends_enquiry_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "event_id"],
            ["fn_events.organization_id", "fn_events.id"],
            name="fk_fn_min_spends_event_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id", "area_id"],
            [
                "fn_venue_areas.organization_id",
                "fn_venue_areas.venue_id",
                "fn_venue_areas.id",
            ],
            name="fk_fn_min_spends_area_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_fn_min_spends_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_min_spends_organization_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["withdrawn_by_user_id"],
            ["users.id"],
            name="fk_fn_min_spends_withdrawn_by_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_fn_min_spends_org_id"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_min_spends_area_id",
        "fn_min_spends",
        ["organization_id", "venue_id", "area_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_min_spends_enquiry_id",
        "fn_min_spends",
        ["organization_id", "enquiry_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_min_spends_venue_id",
        "fn_min_spends",
        ["organization_id", "venue_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "uq_fn_min_spends_event",
        "fn_min_spends",
        ["organization_id", "event_id"],
        unique=True,
        postgresql_where=sa.text("event_id IS NOT NULL"),
        if_not_exists=True,
    )
    op.create_table(
        "fn_portal_access",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
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
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invited_by_user_id", sa.String(), nullable=True),
        sa.Column("invited_by_email", sa.String(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
            ["invited_by_user_id"],
            ["users.id"],
            name="fk_fn_portal_access_invited_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "event_id"],
            ["fn_events.organization_id", "fn_events.id"],
            name="fk_fn_portal_access_event_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_portal_access_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_fn_portal_access_org_id"),
        if_not_exists=True,
    )
    op.create_index(
        "uq_fn_portal_access_email",
        "fn_portal_access",
        ["organization_id", "event_id", sa.literal_column("lower(email)")],
        unique=True,
        if_not_exists=True,
    )
    op.create_table(
        "fn_portal_sessions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("session_hash", sa.String(length=64), nullable=False),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
            ["organization_id", "event_id"],
            ["fn_events.organization_id", "fn_events.id"],
            name="fk_fn_portal_sessions_event_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_portal_sessions_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fn_portal_sessions_org_id"
        ),
        sa.UniqueConstraint("session_hash", name="uq_fn_portal_sessions_hash"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_portal_sessions_event_id",
        "fn_portal_sessions",
        ["organization_id", "event_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_preorder_menus",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("enquiry_id", sa.String(), nullable=True),
        sa.Column("event_id", sa.String(), nullable=True),
        sa.Column("venue_id", sa.String(), nullable=False),
        sa.Column("template_id", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "is_set_menu", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("price_per_person_cents", sa.Integer(), nullable=True),
        sa.Column(
            "guest_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False
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
            "enquiry_id IS NOT NULL OR event_id IS NOT NULL",
            name="ck_fn_preorder_menus_parent",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "enquiry_id"],
            ["fn_enquiries.organization_id", "fn_enquiries.id"],
            name="fk_fn_preorder_menus_enquiry_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "event_id"],
            ["fn_events.organization_id", "fn_events.id"],
            name="fk_fn_preorder_menus_event_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "template_id"],
            ["fn_preorder_templates.organization_id", "fn_preorder_templates.id"],
            name="fk_fn_preorder_menus_template_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "venue_id"],
            ["venues.organization_id", "venues.id"],
            name="fk_fn_preorder_menus_venue_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_preorder_menus_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fn_preorder_menus_org_id"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_preorder_menus_enquiry_id",
        "fn_preorder_menus",
        ["organization_id", "enquiry_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_preorder_menus_event_id",
        "fn_preorder_menus",
        ["organization_id", "event_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_preorder_menus_template_id",
        "fn_preorder_menus",
        ["organization_id", "template_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_preorder_menus_venue_id",
        "fn_preorder_menus",
        ["organization_id", "venue_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_proposal_versions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("proposal_id", sa.String(), nullable=False),
        sa.Column("enquiry_id", sa.String(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("html", sa.Text(), nullable=False),
        sa.Column("css", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_by_user_id", sa.String(), nullable=True),
        sa.Column("sent_by_label", sa.String(), nullable=True),
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
            ["organization_id", "enquiry_id"],
            ["fn_enquiries.organization_id", "fn_enquiries.id"],
            name="fk_fn_proposal_versions_enquiry_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "proposal_id"],
            ["fn_proposals.organization_id", "fn_proposals.id"],
            name="fk_fn_proposal_versions_proposal_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_proposal_versions_organization_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sent_by_user_id"],
            ["users.id"],
            name="fk_fn_proposal_versions_sent_by_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fn_proposal_versions_org_id"
        ),
        sa.UniqueConstraint(
            "proposal_id", "version_number", name="uq_fn_proposal_versions_number"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_proposal_versions_enquiry_id",
        "fn_proposal_versions",
        ["organization_id", "enquiry_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_proposal_versions_proposal_id",
        "fn_proposal_versions",
        ["organization_id", "proposal_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_thread_folders",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("gmail_thread_id", sa.String(), nullable=False),
        sa.Column("folder", sa.String(), nullable=False),
        sa.Column("set_by_user_id", sa.String(), nullable=True),
        sa.Column("set_by_label", sa.String(), nullable=True),
        sa.Column(
            "set_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("gmail_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gmail_sync_error", sa.Text(), nullable=True),
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
            "folder IN ('enquiry', 'reservation', 'other')",
            name="ck_fn_thread_folders_folder",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "account_id"],
            ["fn_mailbox_accounts.organization_id", "fn_mailbox_accounts.id"],
            name="fk_fn_thread_folders_account_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_thread_folders_organization_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["set_by_user_id"],
            ["users.id"],
            name="fk_fn_thread_folders_set_by_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "account_id",
            "gmail_thread_id",
            name="uq_fn_thread_folders_thread",
        ),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fn_thread_folders_org_id"
        ),
        if_not_exists=True,
    )
    op.create_table(
        "fn_thread_links",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("gmail_thread_id", sa.String(), nullable=False),
        sa.Column("enquiry_id", sa.String(), nullable=True),
        sa.Column("event_id", sa.String(), nullable=True),
        sa.Column("linked_by_user_id", sa.String(), nullable=True),
        sa.Column("linked_by_label", sa.String(), nullable=True),
        sa.Column(
            "linked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("unlinked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unlinked_by_user_id", sa.String(), nullable=True),
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
            "num_nonnulls(enquiry_id, event_id) = 1", name="ck_fn_thread_links_target"
        ),
        sa.ForeignKeyConstraint(
            ["linked_by_user_id"],
            ["users.id"],
            name="fk_fn_thread_links_linked_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "account_id"],
            ["fn_mailbox_accounts.organization_id", "fn_mailbox_accounts.id"],
            name="fk_fn_thread_links_account_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "enquiry_id"],
            ["fn_enquiries.organization_id", "fn_enquiries.id"],
            name="fk_fn_thread_links_enquiry_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "event_id"],
            ["fn_events.organization_id", "fn_events.id"],
            name="fk_fn_thread_links_event_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_thread_links_organization_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["unlinked_by_user_id"],
            ["users.id"],
            name="fk_fn_thread_links_unlinked_by_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_fn_thread_links_org_id"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_thread_links_enquiry_id",
        "fn_thread_links",
        ["organization_id", "enquiry_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_thread_links_event_id",
        "fn_thread_links",
        ["organization_id", "event_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_thread_links_thread",
        "fn_thread_links",
        ["organization_id", "account_id", "gmail_thread_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "uq_fn_thread_links_live_enquiry",
        "fn_thread_links",
        ["organization_id", "account_id", "gmail_thread_id", "enquiry_id"],
        unique=True,
        postgresql_where=sa.text("unlinked_at IS NULL AND enquiry_id IS NOT NULL"),
        if_not_exists=True,
    )
    op.create_index(
        "uq_fn_thread_links_live_event",
        "fn_thread_links",
        ["organization_id", "account_id", "gmail_thread_id", "event_id"],
        unique=True,
        postgresql_where=sa.text("unlinked_at IS NULL AND event_id IS NOT NULL"),
        if_not_exists=True,
    )
    op.create_table(
        "fn_mailbox_messages",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("thread_id", sa.String(), nullable=False),
        sa.Column("gmail_message_id", sa.String(), nullable=False),
        sa.Column("gmail_thread_id", sa.String(), nullable=False),
        sa.Column("rfc822_message_id", sa.String(), nullable=True),
        sa.Column("internal_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("from_email", sa.String(), nullable=True),
        sa.Column("from_name", sa.String(), nullable=True),
        sa.Column("to_emails", sa.JSON(), nullable=False),
        sa.Column("cc_emails", sa.JSON(), nullable=False),
        sa.Column("reply_to_email", sa.String(), nullable=True),
        sa.Column("subject", sa.String(), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("label_ids", sa.JSON(), nullable=False),
        sa.Column("is_inbound", sa.Boolean(), nullable=False),
        sa.Column(
            "is_internal", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "is_auto_reply",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "unread", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "has_attachments",
            sa.Boolean(),
            server_default=sa.text("false"),
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
        sa.ForeignKeyConstraint(
            ["organization_id", "account_id"],
            ["fn_mailbox_accounts.organization_id", "fn_mailbox_accounts.id"],
            name="fk_fn_mailbox_messages_account_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "thread_id"],
            ["fn_mailbox_threads.organization_id", "fn_mailbox_threads.id"],
            name="fk_fn_mailbox_messages_thread_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_mailbox_messages_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id", "gmail_message_id", name="uq_fn_mailbox_messages_gmail"
        ),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fn_mailbox_messages_org_id"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_mailbox_messages_reply_facts",
        "fn_mailbox_messages",
        [
            "organization_id",
            "account_id",
            "gmail_thread_id",
            "is_inbound",
            "internal_date",
        ],
        unique=False,
        postgresql_where=sa.text("is_internal IS false AND is_auto_reply IS false"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_mailbox_messages_rfc822",
        "fn_mailbox_messages",
        ["rfc822_message_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_mailbox_messages_thread_date",
        "fn_mailbox_messages",
        ["account_id", "gmail_thread_id", "internal_date"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_mailbox_messages_thread_id",
        "fn_mailbox_messages",
        ["organization_id", "thread_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_min_spend_approvals",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("min_spend_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("actor_user_id", sa.String(), nullable=True),
        sa.Column("actor_label", sa.String(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=True),
        sa.Column("terms_key", sa.String(), nullable=True),
        sa.Column(
            "automatic", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "occurred_at",
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
            "kind IN ('approve', 'override', 'revoke')",
            name="ck_fn_min_spend_approvals_kind",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_fn_min_spend_approvals_actor_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "min_spend_id"],
            ["fn_min_spends.organization_id", "fn_min_spends.id"],
            name="fk_fn_min_spend_approvals_min_spend_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_min_spend_approvals_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fn_min_spend_approvals_org_id"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_min_spend_approvals_min_spend_id",
        "fn_min_spend_approvals",
        ["organization_id", "min_spend_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_outbox",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("rfc822_message_id", sa.String(), nullable=False),
        sa.Column("account_id", sa.String(), nullable=False),
        sa.Column("gmail_thread_id", sa.String(), nullable=True),
        sa.Column("in_reply_to", sa.String(), nullable=True),
        sa.Column("references_header", sa.Text(), nullable=True),
        sa.Column("to_emails", sa.JSON(), nullable=False),
        sa.Column("cc_emails", sa.JSON(), nullable=False),
        sa.Column("bcc_emails", sa.JSON(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("gmail_message_id", sa.String(), nullable=True),
        sa.Column(
            "attempts", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("price_override_reason", sa.Text(), nullable=True),
        sa.Column("enquiry_id", sa.String(), nullable=True),
        sa.Column("event_id", sa.String(), nullable=True),
        sa.Column("proposal_version_id", sa.String(), nullable=True),
        sa.Column("email_draft_id", sa.String(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
        sa.Column("created_by_label", sa.String(), nullable=True),
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
            "purpose IN ('reply', 'compose', 'forward', 'proposal', 'portal_link')",
            name="ck_fn_outbox_purpose",
        ),
        sa.CheckConstraint(
            "status IN ('sending', 'sent', 'failed')", name="ck_fn_outbox_status"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_fn_outbox_created_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "account_id"],
            ["fn_mailbox_accounts.organization_id", "fn_mailbox_accounts.id"],
            name="fk_fn_outbox_account_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "email_draft_id"],
            ["fn_email_drafts.organization_id", "fn_email_drafts.id"],
            name="fk_fn_outbox_email_draft_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "enquiry_id"],
            ["fn_enquiries.organization_id", "fn_enquiries.id"],
            name="fk_fn_outbox_enquiry_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "event_id"],
            ["fn_events.organization_id", "fn_events.id"],
            name="fk_fn_outbox_event_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "proposal_version_id"],
            ["fn_proposal_versions.organization_id", "fn_proposal_versions.id"],
            name="fk_fn_outbox_proposal_version_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_outbox_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_fn_outbox_org_id"),
        sa.UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_fn_outbox_idempotency"
        ),
        sa.UniqueConstraint("rfc822_message_id", name="uq_fn_outbox_rfc822"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_outbox_account_id",
        "fn_outbox",
        ["organization_id", "account_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_outbox_email_draft_id",
        "fn_outbox",
        ["organization_id", "email_draft_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_outbox_enquiry_id",
        "fn_outbox",
        ["organization_id", "enquiry_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_outbox_event_id",
        "fn_outbox",
        ["organization_id", "event_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_outbox_proposal_version_id",
        "fn_outbox",
        ["organization_id", "proposal_version_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_outbox_status",
        "fn_outbox",
        ["status", "created_at"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_preorder_menu_items",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("menu_id", sa.String(), nullable=False),
        sa.Column("template_item_id", sa.String(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "quantity", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("unit_price_cents", sa.Integer(), nullable=True),
        sa.Column("default_percent", sa.Integer(), nullable=True),
        sa.Column("dietaries", sa.JSON(), nullable=False),
        sa.Column("loaded_recipe_id", sa.String(), nullable=True),
        sa.Column("loaded_stock_item_id", sa.String(), nullable=True),
        sa.Column(
            "sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False
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
            "kind IN ('header', 'item')", name="ck_fn_preorder_menu_items_kind"
        ),
        sa.CheckConstraint("quantity >= 0", name="ck_fn_preorder_menu_items_quantity"),
        sa.ForeignKeyConstraint(
            ["organization_id", "menu_id"],
            ["fn_preorder_menus.organization_id", "fn_preorder_menus.id"],
            name="fk_fn_preorder_menu_items_menu_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_preorder_menu_items_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fn_preorder_menu_items_org_id"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_preorder_menu_items_menu_id",
        "fn_preorder_menu_items",
        ["organization_id", "menu_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_public_tokens",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("enquiry_id", sa.String(), nullable=True),
        sa.Column("event_id", sa.String(), nullable=True),
        sa.Column("proposal_version_id", sa.String(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=True),
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
            "(kind = 'quote' AND enquiry_id IS NOT NULL) OR (kind = 'portal' AND event_id IS NOT NULL)",
            name="ck_fn_public_tokens_target",
        ),
        sa.CheckConstraint(
            "kind IN ('quote', 'portal')", name="ck_fn_public_tokens_kind"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_fn_public_tokens_created_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "enquiry_id"],
            ["fn_enquiries.organization_id", "fn_enquiries.id"],
            name="fk_fn_public_tokens_enquiry_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "event_id"],
            ["fn_events.organization_id", "fn_events.id"],
            name="fk_fn_public_tokens_event_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "proposal_version_id"],
            ["fn_proposal_versions.organization_id", "fn_proposal_versions.id"],
            name="fk_fn_public_tokens_proposal_version_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_public_tokens_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_fn_public_tokens_org_id"),
        sa.UniqueConstraint("token_hash", name="uq_fn_public_tokens_hash"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_public_tokens_enquiry_id",
        "fn_public_tokens",
        ["organization_id", "enquiry_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_public_tokens_event_id",
        "fn_public_tokens",
        ["organization_id", "event_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_public_tokens_proposal_version_id",
        "fn_public_tokens",
        ["organization_id", "proposal_version_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_min_spend_responses",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("min_spend_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("recorded_by_user_id", sa.String(), nullable=True),
        sa.Column("customer_email", sa.String(), nullable=True),
        sa.Column("public_token_id", sa.String(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "occurred_at",
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
            "channel IN ('quote_page', 'email', 'phone', 'in_person', 'import')",
            name="ck_fn_min_spend_responses_channel",
        ),
        sa.CheckConstraint(
            "kind IN ('confirm', 'decline')", name="ck_fn_min_spend_responses_kind"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "min_spend_id"],
            ["fn_min_spends.organization_id", "fn_min_spends.id"],
            name="fk_fn_min_spend_responses_min_spend_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "public_token_id"],
            ["fn_public_tokens.organization_id", "fn_public_tokens.id"],
            name="fk_fn_min_spend_responses_public_token_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_min_spend_responses_organization_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_user_id"],
            ["users.id"],
            name="fk_fn_min_spend_responses_recorded_by_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fn_min_spend_responses_org_id"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_min_spend_responses_min_spend_id",
        "fn_min_spend_responses",
        ["organization_id", "min_spend_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_min_spend_responses_public_token_id",
        "fn_min_spend_responses",
        ["organization_id", "public_token_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_table(
        "fn_proposal_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("enquiry_id", sa.String(), nullable=False),
        sa.Column("proposal_version_id", sa.String(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("outbox_id", sa.String(), nullable=True),
        sa.Column("account_id", sa.String(), nullable=True),
        sa.Column("gmail_message_id", sa.String(), nullable=True),
        sa.Column("actor_user_id", sa.String(), nullable=True),
        sa.Column("actor_label", sa.String(), nullable=True),
        sa.Column(
            "occurred_at",
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
            "kind IN ('sent', 'viewed')", name="ck_fn_proposal_events_kind"
        ),
        sa.CheckConstraint(
            "source IN ('outbox', 'gmail', 'quote_page', 'import')",
            name="ck_fn_proposal_events_source",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_fn_proposal_events_actor_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "enquiry_id"],
            ["fn_enquiries.organization_id", "fn_enquiries.id"],
            name="fk_fn_proposal_events_enquiry_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "outbox_id"],
            ["fn_outbox.organization_id", "fn_outbox.id"],
            name="fk_fn_proposal_events_outbox_id",
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "proposal_version_id"],
            ["fn_proposal_versions.organization_id", "fn_proposal_versions.id"],
            name="fk_fn_proposal_events_proposal_version_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_fn_proposal_events_organization_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_fn_proposal_events_org_id"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_proposal_events_enquiry_kind",
        "fn_proposal_events",
        ["organization_id", "enquiry_id", "kind"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_proposal_events_outbox_id",
        "fn_proposal_events",
        ["organization_id", "outbox_id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_fn_proposal_events_proposal_version_id",
        "fn_proposal_events",
        ["organization_id", "proposal_version_id"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    for table in reversed(FN_TABLES):
        op.drop_table(table, if_exists=True)
    op.execute("ALTER TABLE venues DROP CONSTRAINT IF EXISTS uq_venues_org_id")
    op.execute(
        "ALTER TABLE organizations "
        "DROP COLUMN IF EXISTS functions_mode, "
        "DROP COLUMN IF EXISTS tax_label, "
        "DROP COLUMN IF EXISTS default_timezone, "
        "DROP COLUMN IF EXISTS currency"
    )
