"""Add operation_mappings to connector_specs

Revision ID: d4e5f6g7h8i9
Revises: c3d4e5f6g7h8
Create Date: 2026-03-28 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "d4e5f6g7h8i9"
down_revision = "c3d4e5f6g7h8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "connector_specs",
        sa.Column("operation_mappings", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("connector_specs", "operation_mappings")
