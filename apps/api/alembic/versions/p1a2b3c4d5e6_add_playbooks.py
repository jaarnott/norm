"""add playbooks table and playbook_id to threads

Revision ID: p1a2b3c4d5e6
Revises: o1a2b3c4d5e6
Create Date: 2026-04-01 00:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = "p1a2b3c4d5e6"
down_revision = "o1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # playbooks table may already exist in the shared config DB
    from sqlalchemy import inspect

    bind = op.get_bind()
    inspector = inspect(bind)
    if "playbooks" not in inspector.get_table_names():
        op.create_table(
            "playbooks",
            sa.Column("id", sa.String, primary_key=True),
            sa.Column("slug", sa.String, unique=True, nullable=False),
            sa.Column("agent_slug", sa.String, nullable=False),
            sa.Column("display_name", sa.String, nullable=False),
            sa.Column("description", sa.Text, nullable=False),
            sa.Column("instructions", sa.Text, nullable=False),
            sa.Column("tool_filter", sa.JSON, nullable=True),
            sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime(timezone=True)),
            sa.Column("updated_at", sa.DateTime(timezone=True)),
        )
    op.add_column("threads", sa.Column("playbook_id", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("threads", "playbook_id")
    op.drop_table("playbooks")
