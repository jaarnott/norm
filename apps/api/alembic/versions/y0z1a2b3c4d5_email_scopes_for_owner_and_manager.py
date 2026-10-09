"""Owners and managers hold email:read and email:manage.

The email router has always gated on ``email:read`` / ``email:manage``, but
neither scope was in PERMISSION_SCOPES, so no role could hold them: every
non-admin got 403 on Settings → Email, the owner included. The scopes now
exist (app/auth/permissions.py) and the Owner and Manager system roles grant
them — Manager by the existing rule (everything but billing:manage and
org:roles). Team Member and Payroll Administrator do not.

The system roles live in the database, seeded once by 6d95321ad9e6, so
changing STANDARD_ROLES alone never reaches an existing database. This adds
the two scopes to the system ``owner`` and ``manager`` rows. Idempotent: a
role that already holds a scope is left alone, so a re-run changes nothing.

Downgrade takes them off EVERY role, custom ones included: once the code no
longer knows the scopes, a custom role still carrying one could not be saved
(the roles router rejects unknown scopes).

Revision ID: y0z1a2b3c4d5
Revises: x9y0z1a2b3c4
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "y0z1a2b3c4d5"
down_revision = "x9y0z1a2b3c4"
branch_labels = None
depends_on = None

_SCOPES = ("email:read", "email:manage")
_SYSTEM_ROLES = ("owner", "manager")


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
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, permissions FROM roles "
            "WHERE is_system = TRUE AND name IN :names"
        ).bindparams(sa.bindparam("names", expanding=True)),
        {"names": list(_SYSTEM_ROLES)},
    ).fetchall()
    for role_id, permissions in rows:
        current = _as_list(permissions)
        missing = [s for s in _SCOPES if s not in current]
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
