"""Add dashboard fields to reports

Revision ID: f6g7h8i9j0k1
Revises: r1a2b3c4d5e6
Create Date: 2026-04-02 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "f6g7h8i9j0k1"
down_revision = "r1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("is_dashboard", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("reports", sa.Column("agent_slug", sa.String(), nullable=True))
    op.add_column("reports", sa.Column("is_published", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("reports", sa.Column("is_template", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("reports", sa.Column("refresh_interval_seconds", sa.Integer(), nullable=True))
    op.add_column("reports", sa.Column("global_filters", sa.JSON(), nullable=True))
    op.add_column("reports", sa.Column("organization_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("reports", "organization_id")
    op.drop_column("reports", "global_filters")
    op.drop_column("reports", "refresh_interval_seconds")
    op.drop_column("reports", "is_template")
    op.drop_column("reports", "is_published")
    op.drop_column("reports", "agent_slug")
    op.drop_column("reports", "is_dashboard")
