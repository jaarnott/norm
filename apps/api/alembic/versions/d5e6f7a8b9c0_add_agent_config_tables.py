"""add agent_config tables

Revision ID: d5e6f7a8b9c0
Revises: c4a1b2d3e5f7
Create Date: 2026-03-14 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, None] = 'c4a1b2d3e5f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'agent_configs',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('agent_slug', sa.String(), nullable=False),
        sa.Column('display_name', sa.String(), nullable=False),
        sa.Column('system_prompt', sa.Text(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('agent_slug'),
    )

    op.create_table(
        'agent_connector_bindings',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('agent_slug', sa.String(), nullable=False),
        sa.Column('connector_name', sa.String(), nullable=False),
        sa.Column('capabilities', sa.JSON(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('agent_slug', 'connector_name', name='uq_agent_connector'),
    )


def downgrade() -> None:
    op.drop_table('agent_connector_bindings')
    op.drop_table('agent_configs')
