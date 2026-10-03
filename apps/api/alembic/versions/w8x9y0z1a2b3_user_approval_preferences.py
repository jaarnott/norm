"""Each person decides what Norm may do without asking them.

Approvals stage 4 (Oct 2026). One store, ``users.approval_preferences``, keyed
by write tool: "always allow" for an ordinary write, a level (and switches)
for receiving and reconciling invoices. It replaces two settings with two
homes — a personal run mode (``users.workflow_modes``) and, for receiving
only, a per-venue ladder (``venues.invoice_autopilot``).

Filled from both, so nobody's Norm does more than it did:

- Receiving: the LOWEST rung among the venues the person can open, and lower
  still if they had chosen a lower personal mode (stored but never read while
  receiving was the venue's). A switch stays on only if every one of their
  venues had it on.
- Reconciling: their personal mode, as it was.
- Report emails: "always allow" for anyone who owns an active scheduled task.
  The report email asked nobody until the tool labels made it a write that
  asks; without this, a daily report would stop at a card no one is watching.

Neither old column is dropped here; a later release removes them.

Also: ``approvals.thread_id`` becomes optional, so a preference changed in
Settings — outside any conversation — is recorded like one changed on a card.

Revision ID: w8x9y0z1a2b3
Revises: p7v1e2w3t4c5
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "w8x9y0z1a2b3"
down_revision = "p7v1e2w3t4c5"  # tool_call_preview
branch_labels = None
depends_on = None

LEVELS = ("approve_all", "approve_fixes", "autopilot")
RECEIVE = "loadedhub.review_and_receive_invoices"
RECONCILE = "loadedhub.reconcile_received_invoices"
REPORT_EMAIL = "norm_email.send_report_email"


def _json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def _lowest(levels):
    known = [lv for lv in levels if lv in LEVELS]
    return min(known, key=LEVELS.index) if known else None


def preferences_for(
    personal: dict, venue_settings: list[dict], owns_task: bool
) -> dict:
    """One person's starting preferences (pure, so it is tested)."""
    personal = personal if isinstance(personal, dict) else {}
    prefs: dict = {}

    receive_levels = [
        (s or {}).get("mode") or "approve_all" for s in venue_settings
    ] + (
        [personal["review_and_receive_invoices"]]
        if personal.get("review_and_receive_invoices") in LEVELS
        else []
    )
    level = _lowest(receive_levels)
    if level and level != "approve_all":
        switches = sorted(
            {k for s in venue_settings for k, v in (s or {}).items() if v is True}
        )
        options = {
            k: True
            for k in switches
            if venue_settings and all((s or {}).get(k) is True for s in venue_settings)
        }
        prefs[RECEIVE] = {"level": level, "options": options}

    reconcile = personal.get("reconcile_received_invoices")
    if reconcile in LEVELS and reconcile != "approve_all":
        prefs[RECONCILE] = {"level": reconcile}

    if owns_task:
        prefs[REPORT_EMAIL] = {"always": True}
    return prefs


def upgrade() -> None:
    op.add_column("users", sa.Column("approval_preferences", sa.JSON(), nullable=True))
    op.alter_column("approvals", "thread_id", existing_type=sa.String(), nullable=True)
    bind = op.get_bind()
    venues = {
        row.id: _json(row.invoice_autopilot) or {}
        for row in bind.execute(sa.text("SELECT id, invoice_autopilot FROM venues"))
    }
    access: dict[str, list[str]] = {}
    for row in bind.execute(sa.text("SELECT user_id, venue_id FROM user_venue_access")):
        access.setdefault(row.user_id, []).append(row.venue_id)
    owners = {
        row.created_by
        for row in bind.execute(
            sa.text(
                "SELECT DISTINCT created_by FROM automated_tasks "
                "WHERE status = 'active' AND created_by IS NOT NULL"
            )
        )
    }
    for row in bind.execute(sa.text("SELECT id, workflow_modes FROM users")):
        prefs = preferences_for(
            _json(row.workflow_modes) or {},
            [venues[v] for v in access.get(row.id, []) if v in venues],
            row.id in owners,
        )
        if prefs:
            bind.execute(
                sa.text("UPDATE users SET approval_preferences = CAST(:p AS JSON) WHERE id = :id"),
                {"p": json.dumps(prefs), "id": row.id},
            )


def downgrade() -> None:
    op.execute("DELETE FROM approvals WHERE thread_id IS NULL")
    op.alter_column("approvals", "thread_id", existing_type=sa.String(), nullable=False)
    op.drop_column("users", "approval_preferences")
