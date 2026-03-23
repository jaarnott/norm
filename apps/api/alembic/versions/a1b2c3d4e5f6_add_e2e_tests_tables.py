"""add_e2e_tests_tables

Revision ID: a1b2c3d4e5f6
Revises: 985079fbbd57
Create Date: 2026-03-23 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '985079fbbd57'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('e2e_tests',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('playwright_script', sa.Text(), nullable=False),
        sa.Column('steps_json', sa.JSON(), nullable=True),
        sa.Column('created_by', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_run_status', sa.String(), nullable=True),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table('e2e_test_runs',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('test_id', sa.String(), nullable=True),
        sa.Column('environment', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('screenshots_json', sa.JSON(), nullable=True),
        sa.Column('video_url', sa.String(), nullable=True),
        sa.Column('triggered_by', sa.String(), nullable=True),
        sa.Column('git_sha', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['test_id'], ['e2e_tests.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_e2e_test_runs_test_id', 'e2e_test_runs', ['test_id'])
    op.create_index('ix_e2e_test_runs_environment', 'e2e_test_runs', ['environment'])


def downgrade() -> None:
    op.drop_index('ix_e2e_test_runs_environment', 'e2e_test_runs')
    op.drop_index('ix_e2e_test_runs_test_id', 'e2e_test_runs')
    op.drop_table('e2e_test_runs')
    op.drop_table('e2e_tests')
