"""Add uploaded_documents

A Norm-wide store for user-uploaded documents — the bytes live in the DB
(LargeBinary), matching the SupplierSpecSample precedent. A generic upload
endpoint stores a file here with an ``extraction_target`` (e.g. "recipe") so the
right extractor can pull it back out.

Revision ID: c1d2e3f4a5b6
Revises: b9c0d1e2f3a4
Create Date: 2026-08-12 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "c1d2e3f4a5b6"
down_revision = "b9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "uploaded_documents",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("venue_id", sa.String(), sa.ForeignKey("venues.id"), nullable=True),
        sa.Column("filename", sa.String(), nullable=True),
        sa.Column("content_type", sa.String(), nullable=True),
        sa.Column("size", sa.Integer(), nullable=True),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("extraction_target", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("uploaded_documents")
