"""Add document_extractions cache table

Revision ID: v3c4d5e6f7a8
Revises: u2b3c4d5e6f7
Create Date: 2026-07-19 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "v3c4d5e6f7a8"
down_revision = "u2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_extractions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("cache_key", sa.String(), nullable=False),
        sa.Column("connector", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_document_extractions_cache_key",
        "document_extractions",
        ["cache_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_document_extractions_cache_key", table_name="document_extractions")
    op.drop_table("document_extractions")
