"""add_deployments_table

Revision ID: 985079fbbd57
Revises: 9ea472743159
Create Date: 2026-03-23 01:22:45.410366

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '985079fbbd57'
down_revision: Union[str, Sequence[str], None] = '9ea472743159'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('deployments',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('environment', sa.String(), nullable=False),
        sa.Column('image_tag', sa.String(), nullable=False),
        sa.Column('git_sha', sa.String(), nullable=False),
        sa.Column('commit_message', sa.Text(), nullable=True),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('logs_url', sa.String(), nullable=True),
        sa.Column('triggered_by', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_deployments_environment', 'deployments', ['environment'])


def downgrade() -> None:
    op.drop_index('ix_deployments_environment', 'deployments')
    op.drop_table('deployments')
