"""add connector_specs table and integration_runs columns

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-03-15 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "e6f7a8b9c0d1"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connector_specs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("connector_name", sa.String(), unique=True, nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("execution_mode", sa.String(), nullable=False, server_default="template"),
        sa.Column("auth_type", sa.String(), nullable=False),
        sa.Column("auth_config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("base_url_template", sa.String(), nullable=True),
        sa.Column("operations", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("api_documentation", sa.Text(), nullable=True),
        sa.Column("example_requests", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("credential_fields", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.add_column("integration_runs", sa.Column("execution_mode", sa.String(), nullable=True))
    op.add_column("integration_runs", sa.Column("rendered_request", sa.JSON(), nullable=True))
    op.add_column("integration_runs", sa.Column("spec_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("integration_runs", "spec_version")
    op.drop_column("integration_runs", "rendered_request")
    op.drop_column("integration_runs", "execution_mode")
    op.drop_table("connector_specs")
