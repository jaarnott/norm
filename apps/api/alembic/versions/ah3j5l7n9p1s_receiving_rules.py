"""Receiving rules: an organisation's own rules for invoices it receives.

How Norm fills in what an invoice leaves open — the due date first (The Lott:
"if there's no due date I always select Sunday", 22 Oct 2026). Set for the
whole organisation or overridden for one venue, by an owner or a manager
(``receiving:rules``). Adds the table and gives the new scope to the owner and
manager system roles, which were seeded before it existed (changing
STANDARD_ROLES alone never reaches a database — vc8e0g2i4k6m). Idempotent.

Revision ID: ah3j5l7n9p1s
Revises: d4shk1nd2110
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "ah3j5l7n9p1s"
down_revision = "d4shk1nd2110"
branch_labels = None
depends_on = None

#: role name -> scopes it must hold (mirrors STANDARD_ROLES; a test keeps them equal)
GRANTS = {
    "owner": ("receiving:rules",),
    "manager": ("receiving:rules",),
}


def _as_list(value) -> list[str]:
    if isinstance(value, str):
        value = json.loads(value)
    return list(value or [])


def _system_rows(conn):
    return conn.execute(
        sa.text(
            "SELECT id, name, permissions FROM roles "
            "WHERE is_system = TRUE AND organization_id IS NULL AND name IN :names"
        ).bindparams(sa.bindparam("names", expanding=True)),
        {"names": list(GRANTS)},
    ).fetchall()


def _write(conn, role_id: str, permissions: list[str]) -> None:
    conn.execute(
        sa.text(
            "UPDATE roles SET permissions = CAST(:permissions AS JSON), "
            "updated_at = now() WHERE id = :id"
        ),
        {"id": role_id, "permissions": json.dumps(permissions)},
    )


def upgrade() -> None:
    op.create_table(
        "receiving_rules",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("venue_id", sa.String(), sa.ForeignKey("venues.id"), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("updated_by", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_receiving_rules_organization_id", "receiving_rules", ["organization_id"]
    )
    op.create_index("ix_receiving_rules_venue_id", "receiving_rules", ["venue_id"])
    op.create_index(
        "uq_receiving_rule_org",
        "receiving_rules",
        ["organization_id", "kind"],
        unique=True,
        postgresql_where=sa.text("venue_id IS NULL"),
    )
    op.create_index(
        "uq_receiving_rule_venue",
        "receiving_rules",
        ["venue_id", "kind"],
        unique=True,
        postgresql_where=sa.text("venue_id IS NOT NULL"),
    )
    conn = op.get_bind()
    for role_id, name, permissions in _system_rows(conn):
        current = _as_list(permissions)
        missing = [s for s in GRANTS[name] if s not in current]
        if missing:
            _write(conn, role_id, current + missing)


def downgrade() -> None:
    conn = op.get_bind()
    for role_id, name, permissions in _system_rows(conn):
        current = _as_list(permissions)
        kept = [p for p in current if p not in GRANTS[name]]
        if len(kept) != len(current):
            _write(conn, role_id, kept)
    op.drop_table("receiving_rules")
