"""drop operation_mappings from connector_specs

Revision ID: o1a2b3c4d5e6
Revises: n1a2b3c4d5e6
Create Date: 2026-03-31 12:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "o1a2b3c4d5e6"
down_revision = "n1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("connector_specs", "operation_mappings")


def downgrade() -> None:
    op.add_column(
        "connector_specs",
        sa.Column("operation_mappings", sa.JSON, nullable=True),
    )
