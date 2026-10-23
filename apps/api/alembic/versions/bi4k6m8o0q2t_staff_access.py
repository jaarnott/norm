"""Staff access: venue staff sign in, roles say what they reach, kitchen tablets.

docs/plans/staff-access.md, section 11.2. Roles gain ``agents``/``apps`` (NULL
= everything hired / switched on, a list = only those) and ``loaded_roles``
(the Loaded role names that land a new starter there). Organisations gain the
switch and the staff chat cap; users gain ``is_device`` and ``password_set``;
apps gain ``audience``. New tables: the staff directory, the emailed sign-in
codes, venue devices (tablets) and the hourly sync runs.

Data: the system Team Member role becomes the venue-staff role — no AI agent,
no apps until an owner ticks some, and permissions trimmed to tasks, org:read
and apps:build (it read orders, the roster, HR and reports before, which a
chef should not). Mirrors STANDARD_ROLES; a test keeps them equal. Other
system roles stay NULL/NULL.

Revision ID: bi4k6m8o0q2t
Revises: ne9h1j3l5n7p (origin/main's head on 23 Oct 2026; the five migrations between came in with it)
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "bi4k6m8o0q2t"
down_revision = "ne9h1j3l5n7p"
branch_labels = None
depends_on = None

#: What the system team_member role holds after this (mirrors STANDARD_ROLES).
TEAM_MEMBER_PERMISSIONS = ["tasks:read", "tasks:write", "org:read", "apps:build"]
#: What it loses — given back on downgrade.
TEAM_MEMBER_DROPPED = ["orders:read", "roster:read", "hr:read", "reports:read"]


def _as_list(value) -> list[str]:
    if isinstance(value, str):
        value = json.loads(value)
    return list(value or [])


def _team_member_rows(conn):
    """Every system team_member row — one in production, but a test database
    can hold leaked copies, and all of them must agree with the code."""
    return conn.execute(
        sa.text(
            "SELECT id, permissions FROM roles WHERE is_system = TRUE "
            "AND organization_id IS NULL AND name = 'team_member'"
        )
    ).fetchall()


def upgrade() -> None:
    op.add_column("roles", sa.Column("agents", sa.JSON(), nullable=True))
    op.add_column("roles", sa.Column("apps", sa.JSON(), nullable=True))
    op.add_column(
        "roles",
        sa.Column(
            "loaded_roles", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
        ),
    )
    op.add_column(
        "organizations",
        sa.Column(
            "staff_access_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "organizations", sa.Column("staff_chat_cap_cents", sa.Integer(), nullable=True)
    )
    op.add_column(
        "users",
        sa.Column("is_device", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "users",
        sa.Column(
            "password_set", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )
    op.add_column(
        "apps",
        sa.Column("audience", sa.String(), nullable=False, server_default="agent"),
    )

    op.create_table(
        "staff_members",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column(
            "loaded_roles", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column(
            "venue_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column("role_id", sa.String(), sa.ForeignKey("roles.id"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("held_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "granted_membership",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("pin_hash", sa.String(), nullable=True),
        sa.Column("pin_set_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_staff_members_organization_id", "staff_members", ["organization_id"]
    )
    op.create_index("ix_staff_members_user_id", "staff_members", ["user_id"])
    op.create_index(
        "uq_staff_member_external",
        "staff_members",
        ["organization_id", "source", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.create_index(
        "ix_staff_members_org_email", "staff_members", ["organization_id", "email"]
    )
    op.create_index(
        "uq_staff_member_pin",
        "staff_members",
        ["organization_id", "pin_hash"],
        unique=True,
        postgresql_where=sa.text("pin_hash IS NOT NULL"),
    )

    op.create_table(
        "staff_login_codes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("code_hash", sa.String(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sends_in_window", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "venue_devices",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("venue_id", sa.String(), sa.ForeignKey("venues.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "pin_required", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_venue_devices_organization_id", "venue_devices", ["organization_id"]
    )
    op.create_index("ix_venue_devices_venue_id", "venue_devices", ["venue_id"])

    op.create_table(
        "staff_sync_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("slot", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.UniqueConstraint("organization_id", "slot", name="uq_staff_sync_run_slot"),
    )
    op.create_index(
        "ix_staff_sync_runs_organization_id", "staff_sync_runs", ["organization_id"]
    )

    # Data: the system Team Member role is the venue-staff role now.
    conn = op.get_bind()
    for row in _team_member_rows(conn):
        conn.execute(
            sa.text(
                "UPDATE roles SET permissions = CAST(:permissions AS JSON), "
                "agents = CAST('[]' AS JSON), apps = CAST('[]' AS JSON), "
                "updated_at = now() WHERE id = :id"
            ),
            {"id": row.id, "permissions": json.dumps(TEAM_MEMBER_PERMISSIONS)},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for row in _team_member_rows(conn):
        current = _as_list(row.permissions)
        restored = current + [p for p in TEAM_MEMBER_DROPPED if p not in current]
        conn.execute(
            sa.text(
                "UPDATE roles SET permissions = CAST(:permissions AS JSON), "
                "updated_at = now() WHERE id = :id"
            ),
            {"id": row.id, "permissions": json.dumps(restored)},
        )

    op.drop_index("ix_staff_sync_runs_organization_id", table_name="staff_sync_runs")
    op.drop_table("staff_sync_runs")
    op.drop_index("ix_venue_devices_venue_id", table_name="venue_devices")
    op.drop_index("ix_venue_devices_organization_id", table_name="venue_devices")
    op.drop_table("venue_devices")
    op.drop_table("staff_login_codes")
    op.drop_index("uq_staff_member_pin", table_name="staff_members")
    op.drop_index("ix_staff_members_org_email", table_name="staff_members")
    op.drop_index("uq_staff_member_external", table_name="staff_members")
    op.drop_index("ix_staff_members_user_id", table_name="staff_members")
    op.drop_index("ix_staff_members_organization_id", table_name="staff_members")
    op.drop_table("staff_members")

    op.drop_column("apps", "audience")
    op.drop_column("users", "password_set")
    op.drop_column("users", "is_device")
    op.drop_column("organizations", "staff_chat_cap_cents")
    op.drop_column("organizations", "staff_access_enabled")
    op.drop_column("roles", "loaded_roles")
    op.drop_column("roles", "apps")
    op.drop_column("roles", "agents")
