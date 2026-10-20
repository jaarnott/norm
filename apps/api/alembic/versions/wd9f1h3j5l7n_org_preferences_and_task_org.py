"""Settings per organisation, and tasks that know their organisation.

A person in two organisations had ONE set of approval settings: Autopilot in
one organisation was Autopilot in all of them. Settings are now kept per
organisation (``users.approval_preferences = {"by_org": {org: {...}}}``).
Nothing changes for anyone today: each person's current settings are copied
into every organisation they belong to, to be changed separately from now on.

Automated tasks had no organisation, so an unattended run acted in its
creator's OLDEST organisation — wrong venues and wrong settings for anyone in
several. ``automated_tasks.organization_id`` is filled from, in order: the
task's venue; the conversation that created it (its manage_task call); its own
task conversation; its creator's only organisation.

Revision ID: wd9f1h3j5l7n
Revises: vc8e0g2i4k6m
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "wd9f1h3j5l7n"
down_revision = "vc8e0g2i4k6m"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "automated_tasks", sa.Column("organization_id", sa.String(), nullable=True)
    )
    op.create_index(
        "ix_automated_tasks_organization_id", "automated_tasks", ["organization_id"]
    )
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE automated_tasks t SET organization_id = v.organization_id
            FROM venues v WHERE t.venue_id = v.id AND v.organization_id IS NOT NULL
            """
        )
    )
    # The conversation whose manage_task call created it.
    conn.execute(
        sa.text(
            """
            UPDATE automated_tasks t SET organization_id = th.organization_id
            FROM tool_calls tc JOIN threads th ON th.id = tc.thread_id
            WHERE t.organization_id IS NULL
              AND tc.action IN ('manage_task', 'create_automated_task')
              AND tc.result_payload IS NOT NULL
              AND (tc.result_payload::jsonb ->> 'id') = t.id
              AND th.organization_id IS NOT NULL
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE automated_tasks t SET organization_id = th.organization_id
            FROM threads th
            WHERE t.organization_id IS NULL AND th.id = t.conversation_thread_id
              AND th.organization_id IS NOT NULL
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE automated_tasks t SET organization_id = m.organization_id
            FROM (
                SELECT user_id, MIN(organization_id) AS organization_id
                FROM organization_memberships GROUP BY user_id HAVING COUNT(*) = 1
            ) m
            WHERE t.organization_id IS NULL AND t.created_by = m.user_id
            """
        )
    )

    # Settings: the one-for-everything store becomes the same setting in each
    # organisation the person belongs to.
    members: dict[str, list[str]] = {}
    for user_id, org_id in conn.execute(
        sa.text("SELECT user_id, organization_id FROM organization_memberships")
    ):
        members.setdefault(user_id, []).append(org_id)
    rows = conn.execute(
        sa.text(
            "SELECT id, approval_preferences FROM users "
            "WHERE approval_preferences IS NOT NULL"
        )
    ).fetchall()
    for user_id, prefs in rows:
        if isinstance(prefs, str):
            prefs = json.loads(prefs)
        if not isinstance(prefs, dict) or "by_org" in prefs:
            continue
        orgs = members.get(user_id) or []
        if not orgs:
            continue  # read as-is until they join an organisation
        flat = {k: v for k, v in prefs.items()}
        conn.execute(
            sa.text(
                "UPDATE users SET approval_preferences = CAST(:p AS JSON) WHERE id = :id"
            ),
            {"id": user_id, "p": json.dumps({"by_org": {o: flat for o in orgs}})},
        )


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, approval_preferences FROM users "
            "WHERE approval_preferences IS NOT NULL"
        )
    ).fetchall()
    for user_id, prefs in rows:
        if isinstance(prefs, str):
            prefs = json.loads(prefs)
        if not isinstance(prefs, dict) or "by_org" not in prefs:
            continue
        by_org = prefs.get("by_org") or {}
        # One store again: the first organisation's settings.
        first = next(iter(by_org.values()), {}) if by_org else {}
        conn.execute(
            sa.text(
                "UPDATE users SET approval_preferences = CAST(:p AS JSON) WHERE id = :id"
            ),
            {"id": user_id, "p": json.dumps(first)},
        )
    op.drop_index("ix_automated_tasks_organization_id", table_name="automated_tasks")
    op.drop_column("automated_tasks", "organization_id")
