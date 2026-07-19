"""Add workflow_modes to users

Revision ID: u2b3c4d5e6f7
Revises: 379f84a5e8af
Create Date: 2026-07-19 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "u2b3c4d5e6f7"
down_revision = "379f84a5e8af"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("workflow_modes", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "workflow_modes")
