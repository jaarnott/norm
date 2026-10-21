"""Dashboards belong to an organisation, and keep their history.

Until Oct 2026 no dashboard had an organisation (the column existed; nothing
filled it in), so a person in two organisations saw the same dashboards in
both and "shared" reached every organisation. This assigns each one, and adds
``report_versions``: every version of a dashboard is kept, as an App's are.

Which organisation a dashboard goes to (docs/plans/norm-builds-dashboards.md):
the owner's only organisation; with several, the ones they belonged to when
they made it (all of them if none yet), and among those the one where their
role is highest, then the oldest. On production that sends Rich's dashboards
to Lott Group (owner there, team member at Cook Brothers) and the April ones
of a Cook Brothers account to Cook Brothers, as the owner decided.

Revision ID: yf1h3j5l7n9q
Revises: xe0g2i4k6m8p
"""

import sqlalchemy as sa
from alembic import op

revision = "yf1h3j5l7n9q"
down_revision = "xe0g2i4k6m8p"
branch_labels = None
depends_on = None

_RANK = {"owner": 0, "manager": 1, "payroll_admin": 2, "team_member": 3}


def upgrade() -> None:
    op.create_table(
        "report_versions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "report_id",
            sa.String(),
            sa.ForeignKey("reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("report_id", "version", name="uq_report_version"),
    )
    op.create_index("ix_report_versions_report_id", "report_versions", ["report_id"])

    conn = op.get_bind()
    reports = conn.execute(
        sa.text(
            "SELECT id, user_id, created_at FROM reports "
            "WHERE organization_id IS NULL AND user_id IS NOT NULL"
        )
    ).fetchall()
    for report_id, user_id, made_at in reports:
        rows = conn.execute(
            sa.text(
                "SELECT m.organization_id, m.created_at, r.name "
                "FROM organization_memberships m "
                "LEFT JOIN roles r ON r.id = m.role_id "
                "WHERE m.user_id = :u"
            ),
            {"u": user_id},
        ).fetchall()
        if not rows:
            continue  # nobody's organisation: stays the owner's alone
        then = [
            r for r in rows if r[1] is None or made_at is None or r[1] <= made_at
        ] or rows
        best = min(
            then,
            key=lambda r: (
                _RANK.get(r[2] or "", 9),
                r[1] is not None,  # a membership from before dates were kept is oldest
                r[1] or made_at,
            ),
        )
        conn.execute(
            sa.text("UPDATE reports SET organization_id = :o WHERE id = :id"),
            {"o": best[0], "id": report_id},
        )


def downgrade() -> None:
    op.drop_index("ix_report_versions_report_id", table_name="report_versions")
    op.drop_table("report_versions")
