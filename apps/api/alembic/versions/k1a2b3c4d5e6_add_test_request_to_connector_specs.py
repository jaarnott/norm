"""add test_request column to connector_specs

Revision ID: k1a2b3c4d5e6
Revises: j1a2b3c4d5e6
Create Date: 2026-03-16 14:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "k1a2b3c4d5e6"
down_revision = "j1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("connector_specs", sa.Column("test_request", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("connector_specs", "test_request")
