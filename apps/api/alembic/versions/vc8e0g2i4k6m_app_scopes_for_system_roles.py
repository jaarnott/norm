"""The standard roles hold the app permissions the code gives them.

``apps:build`` and ``apps:share`` were added to STANDARD_ROLES after the system
roles were seeded (6d95321ad9e6), and changing STANDARD_ROLES alone never
reaches an existing database — the email scopes hit the same trap
(y0z1a2b3c4d5). So in production no one but a platform admin could save an
app: an owner building an end-of-night report was refused "you do not have the
apps:build permission" (thread 029ca35b, 20 Oct 2026).

Adds, per app/auth/permissions.py: owner and manager get both scopes, team
member gets apps:build. Idempotent: a role already holding a scope is left as
it is. Downgrade takes them off the three system roles again.

Revision ID: vc8e0g2i4k6m
Revises: ja5d7f9h1j3l
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "vc8e0g2i4k6m"
down_revision = "ja5d7f9h1j3l"
branch_labels = None
depends_on = None

#: role name -> scopes it must hold (mirrors STANDARD_ROLES; a test keeps them equal)
GRANTS = {
    "owner": ("apps:build", "apps:share"),
    "manager": ("apps:build", "apps:share"),
    "team_member": ("apps:build",),
}


def _as_list(value) -> list[str]:
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


def _system_rows(conn):
    return conn.execute(
        sa.text(
            "SELECT id, name, permissions FROM roles "
            "WHERE is_system = TRUE AND organization_id IS NULL AND name IN :names"
        ).bindparams(sa.bindparam("names", expanding=True)),
        {"names": list(GRANTS)},
    ).fetchall()


def upgrade() -> None:
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
