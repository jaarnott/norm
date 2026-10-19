"""Threads remember the organisation they were started in.

A person can belong to several organisations (Oct 2026), so a conversation can
no longer be tied to an organisation through its user's membership. The usage
it causes is billed to this organisation, and background work on it (an
approval, a scheduled run) acts as it.

Backfill: the venue's organisation where the thread has a venue, else the
organisation of a user who belongs to exactly one.

Revision ID: v5x7z9b2d4f6
Revises: uc7k2m4p9r1t
"""

import sqlalchemy as sa
from alembic import op

revision = "v5x7z9b2d4f6"
down_revision = "uc7k2m4p9r1t"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("threads", sa.Column("organization_id", sa.String(), nullable=True))
    op.create_index("ix_threads_organization_id", "threads", ["organization_id"])
    op.execute(
        """
        UPDATE threads t SET organization_id = v.organization_id
        FROM venues v
        WHERE t.venue_id = v.id AND v.organization_id IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE threads t SET organization_id = m.organization_id
        FROM (
            SELECT user_id, MIN(organization_id) AS organization_id
            FROM organization_memberships
            GROUP BY user_id HAVING COUNT(*) = 1
        ) m
        WHERE t.organization_id IS NULL AND t.user_id = m.user_id
        """
    )


def downgrade() -> None:
    op.drop_index("ix_threads_organization_id", table_name="threads")
    op.drop_column("threads", "organization_id")
