"""Add connector connection-health columns

Persist whether a connector's OAuth needs reconnecting, so a dead refresh token
is visible before someone tries to fetch. bool(access_token) stays true after a
refresh token dies — which is how a LoadedHub outage read as "Connected" while
every fetch failed.

Revision ID: z7a8b9c0d1e2
Revises: y6f7a8b9c0d1
Create Date: 2026-07-30 21:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

revision = "z7a8b9c0d1e2"
down_revision = "y6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "connector_configs",
        sa.Column(
            "needs_reconnect",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "connector_configs",
        sa.Column("last_auth_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "connector_configs",
        sa.Column("last_auth_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("connector_configs", "last_auth_checked_at")
    op.drop_column("connector_configs", "last_auth_error")
    op.drop_column("connector_configs", "needs_reconnect")
