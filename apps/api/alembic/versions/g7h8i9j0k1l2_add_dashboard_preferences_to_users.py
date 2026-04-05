"""Add dashboard_preferences to users

Revision ID: g7h8i9j0k1l2
Revises: f6g7h8i9j0k1
Create Date: 2026-04-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "g7h8i9j0k1l2"
down_revision = "f6g7h8i9j0k1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("dashboard_preferences", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "dashboard_preferences")
