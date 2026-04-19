"""add stdout to e2e_test_runs

Revision ID: s1a2b3c4d5e6
Revises: r1a2b3c4d5e6, g7h8i9j0k1l2
Create Date: 2026-04-19 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "s1a2b3c4d5e6"
down_revision = ("r1a2b3c4d5e6", "g7h8i9j0k1l2")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("e2e_test_runs", sa.Column("stdout", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("e2e_test_runs", "stdout")
