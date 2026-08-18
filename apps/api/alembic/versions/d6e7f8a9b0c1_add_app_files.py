"""app_files — bytes an app owns

Apps could store rows but not files, so evidence and CVs had to live in someone
else's bucket. This is what lets Orbit's Supabase storage retire.

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-08-17
"""

from alembic import op
import sqlalchemy as sa

revision = "d6e7f8a9b0c1"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_files",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=True),
        sa.Column("collection", sa.String(), nullable=True),
        sa.Column("record_id", sa.String(), nullable=True),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("source_ref", sa.String(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["venue_id"], ["venues.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_app_files_namespace", "app_files", ["namespace"])
    op.create_index("ix_app_files_organization_id", "app_files", ["organization_id"])
    op.create_index("ix_app_files_venue_id", "app_files", ["venue_id"])
    op.create_index("ix_app_files_source_ref", "app_files", ["source_ref"])
    op.create_index(
        "ix_app_files_owner", "app_files", ["namespace", "collection", "record_id"]
    )


def downgrade() -> None:
    for name in (
        "ix_app_files_owner",
        "ix_app_files_source_ref",
        "ix_app_files_venue_id",
        "ix_app_files_organization_id",
        "ix_app_files_namespace",
    ):
        op.drop_index(name, table_name="app_files")
    op.drop_table("app_files")
