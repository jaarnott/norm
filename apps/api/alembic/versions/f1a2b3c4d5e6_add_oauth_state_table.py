"""add oauth_states table and oauth columns to connector_configs

Revision ID: f1a2b3c4d5e6
Revises: e6f7a8b9c0d1
Create Date: 2026-03-15 14:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "f1a2b3c4d5e6"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_states",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("connector_name", sa.String(), nullable=False),
        sa.Column("state", sa.String(), unique=True, nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Add OAuth token fields to connector_configs
    op.add_column("connector_configs", sa.Column("access_token", sa.Text(), nullable=True))
    op.add_column("connector_configs", sa.Column("refresh_token", sa.Text(), nullable=True))
    op.add_column("connector_configs", sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("connector_configs", sa.Column("oauth_metadata", sa.JSON(), nullable=True))

    # Add OAuth provider fields to connector_specs
    op.add_column("connector_specs", sa.Column("oauth_config", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("connector_specs", "oauth_config")
    op.drop_column("connector_configs", "oauth_metadata")
    op.drop_column("connector_configs", "token_expires_at")
    op.drop_column("connector_configs", "refresh_token")
    op.drop_column("connector_configs", "access_token")
    op.drop_table("oauth_states")
