"""Add component_api_configs table

Revision ID: e5f6g7h8i9j0
Revises: d4e5f6g7h8i9
Create Date: 2026-03-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "e5f6g7h8i9j0"
down_revision = ("d4e5f6g7h8i9", "n1a2b3c4d5e6")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "component_api_configs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("component_key", sa.String(), nullable=False),
        sa.Column("connector_name", sa.String(), nullable=False),
        sa.Column("action_name", sa.String(), nullable=False),
        sa.Column("display_label", sa.String(), nullable=True),
        sa.Column("method", sa.String(), nullable=False, server_default="GET"),
        sa.Column("path_template", sa.String(), nullable=False),
        sa.Column("request_body_template", sa.Text(), nullable=True),
        sa.Column("headers", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("required_fields", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("field_descriptions", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "component_key",
            "connector_name",
            "action_name",
            name="uq_component_connector_action",
        ),
    )


def downgrade() -> None:
    op.drop_table("component_api_configs")
