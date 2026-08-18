"""apps.agent — which agent's menu an app's pages join

An app used to appear only under the App Builder. This lets it declare that it
belongs to HR (or Procurement, …) so its pages sit beside that agent's own —
which is how a suite of apps becomes part of an area of the product rather than
a separate destination.

NULL means the App Builder, i.e. exactly today's behaviour for every existing
app, so no backfill is needed.

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-08-17
"""

from alembic import op
import sqlalchemy as sa

revision = "a3b4c5d6e7f8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("apps", sa.Column("agent", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("apps", "agent")
