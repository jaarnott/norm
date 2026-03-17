"""add working_documents table

Revision ID: l1a2b3c4d5e6
Revises: k1a2b3c4d5e6
Create Date: 2026-03-17 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "l1a2b3c4d5e6"
down_revision = "k1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "working_documents",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("task_id", sa.String(), sa.ForeignKey("tasks.id"), nullable=False, index=True),
        sa.Column("doc_type", sa.String(), nullable=False),
        sa.Column("connector_name", sa.String(), nullable=False),
        sa.Column("sync_mode", sa.String(), nullable=False, server_default="auto"),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("external_ref", sa.JSON(), nullable=True),
        sa.Column("sync_status", sa.String(), nullable=False, server_default="synced"),
        sa.Column("sync_error", sa.Text(), nullable=True),
        sa.Column("pending_ops", sa.JSON(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("working_documents")
