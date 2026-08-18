"""app_records.data → JSONB, with a GIN index

The column was the generic JSON type, which cannot be indexed and cannot be
queried through: no `astext`, no nested paths, so every filter was a sequential
scan with a per-row parse and `result.signoff_at` was unreachable. JSONB is what
this table always claimed to be in its own docstring.

Revision ID: c5d6e7f8a9b0
Revises: a3b4c5d6e7f8
Create Date: 2026-08-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c5d6e7f8a9b0"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "app_records",
        "data",
        existing_type=sa.JSON(),
        type_=postgresql.JSONB(),
        postgresql_using="data::jsonb",
        existing_nullable=False,
    )
    op.create_index(
        "ix_app_records_data",
        "app_records",
        ["data"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_app_records_data", table_name="app_records")
    op.alter_column(
        "app_records",
        "data",
        existing_type=postgresql.JSONB(),
        type_=sa.JSON(),
        postgresql_using="data::json",
        existing_nullable=False,
    )
