"""Finance Phase 0: payroll and finance scopes, the module switches, trading venues.

Three things, in the one deploy as the code that reads them (plan
docs/plans/finance-migration.md §5.2 item 4, §6):

* ``organizations`` gains ``payroll_mode``, ``entertainers_mode`` and
  ``finance_mode`` (off | shadow | live, beside ``functions_mode``),
  ``fiscal_year_start_month`` (1–12, April by default) and
  ``payroll_contact_email`` (where shadow-mode emails go).
* ``venues`` gains ``is_trading`` — false for a head office with its own
  Loaded and Xero logins (CBI), so its $0 days stay out of group figures.
* The four new scopes — ``payroll:read``, ``payroll:write``, ``finance:read``,
  ``budgets:write`` — are written onto every organisation's SYSTEM roles. The
  roles are rows seeded once, so STANDARD_ROLES alone never reaches an
  existing database: without this every real owner would 403 on the first
  Finance page while tests signed in as a platform admin passed. Owner and
  payroll administrator get all four; manager gets ``finance:read`` and
  ``budgets:write`` only (never pay data — owner decision D8); team member
  nothing. Custom roles gain nothing until an owner edits them.

Idempotent: a column or constraint that exists is passed over (the test suite
builds tables with create_all on the local database), and a role that already
holds a scope is left alone. Downgrade drops the columns and strips the four
scopes from EVERY role, custom ones included — once the code no longer knows
them, a role still carrying one could not be saved.

Revision ID: kb6e8g0i2k4m
Revises: bk2m4o6q8s0u
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "kb6e8g0i2k4m"
down_revision = "bk2m4o6q8s0u"
branch_labels = None
depends_on = None

_MODES = ("payroll_mode", "entertainers_mode", "finance_mode")
_SCOPES = ("payroll:read", "payroll:write", "finance:read", "budgets:write")
#: What each system role gains (plan §9 "Security", app/auth/permissions.py).
_GRANTS = {
    "owner": _SCOPES,
    "payroll_admin": _SCOPES,
    "manager": ("finance:read", "budgets:write"),
}


def _as_list(value) -> list[str]:
    """roles.permissions is a JSON column; tolerate a driver handing back text."""
    if isinstance(value, str):
        value = json.loads(value)
    return list(value or [])


def _write(conn, role_id: str, permissions: list[str]) -> None:
    conn.execute(
        sa.text(
            "UPDATE roles SET permissions = CAST(:permissions AS JSON), "
            "updated_at = now() WHERE id = :id"
        ),
        {"id": role_id, "permissions": json.dumps(permissions)},
    )


def upgrade() -> None:
    mode_columns = ", ".join(
        f"ADD COLUMN IF NOT EXISTS {col} VARCHAR NOT NULL DEFAULT 'off' "
        f"CONSTRAINT ck_organizations_{col} CHECK ({col} IN ('off', 'shadow', 'live'))"
        for col in _MODES
    )
    op.execute(
        "ALTER TABLE organizations "
        f"{mode_columns}, "
        "ADD COLUMN IF NOT EXISTS fiscal_year_start_month INTEGER NOT NULL DEFAULT 4 "
        "CONSTRAINT ck_organizations_fiscal_year_start_month "
        "CHECK (fiscal_year_start_month BETWEEN 1 AND 12), "
        "ADD COLUMN IF NOT EXISTS payroll_contact_email VARCHAR"
    )
    op.execute(
        "ALTER TABLE venues "
        "ADD COLUMN IF NOT EXISTS is_trading BOOLEAN NOT NULL DEFAULT TRUE"
    )

    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, name, permissions FROM roles "
            "WHERE is_system = TRUE AND name IN :names"
        ).bindparams(sa.bindparam("names", expanding=True)),
        {"names": list(_GRANTS)},
    ).fetchall()
    for role_id, name, permissions in rows:
        current = _as_list(permissions)
        missing = [s for s in _GRANTS[name] if s not in current]
        if missing:
            _write(conn, role_id, current + missing)


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, permissions FROM roles")).fetchall()
    for role_id, permissions in rows:
        current = _as_list(permissions)
        kept = [p for p in current if p not in _SCOPES]
        if len(kept) != len(current):
            _write(conn, role_id, kept)

    op.execute("ALTER TABLE venues DROP COLUMN IF EXISTS is_trading")
    op.execute(
        "ALTER TABLE organizations "
        + ", ".join(f"DROP COLUMN IF EXISTS {col}" for col in _MODES)
        + ", DROP COLUMN IF EXISTS fiscal_year_start_month"
        ", DROP COLUMN IF EXISTS payroll_contact_email"
    )
