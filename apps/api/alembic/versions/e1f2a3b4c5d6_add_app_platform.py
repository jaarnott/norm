"""Add the app platform: apps, versions, shares, calls

Revision ID: e1f2a3b4c5d6
Revises: c1d2e3f4a5b6
Create Date: 2026-08-15

User-built apps live in the MAIN db (org-scoped), not the shared config db.
Reach is declared per VERSION, so editing an app can never widen what an
already-shared copy may touch.
"""

import sqlalchemy as sa
from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "apps",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "created_by",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("icon", sa.String(), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column(
            "visibility", sa.String(), nullable=False, server_default="private"
        ),
        sa.Column("current_version_id", sa.String(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), index=True),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("organization_id", "slug", name="uq_app_org_slug"),
    )
    op.create_table(
        "app_versions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "app_id",
            sa.String(),
            sa.ForeignKey("apps.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("ui_source", sa.Text(), nullable=True),
        sa.Column("logic_source", sa.Text(), nullable=True),
        sa.Column("changelog", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), index=True),
        sa.UniqueConstraint("app_id", "version", name="uq_app_version"),
    )
    op.create_table(
        "app_shares",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "app_id",
            sa.String(),
            sa.ForeignKey("apps.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("principal_type", sa.String(), nullable=False),
        sa.Column("principal_id", sa.String(), nullable=False, index=True),
        sa.Column("access", sa.String(), nullable=False, server_default="view"),
        sa.Column(
            "write_actions_approved",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "granted_by",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "app_id", "principal_type", "principal_id", name="uq_app_share_principal"
        ),
    )
    op.create_table(
        "app_calls",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "app_id",
            sa.String(),
            sa.ForeignKey("apps.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("app_version_id", sa.String(), nullable=True),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "venue_id",
            sa.String(),
            sa.ForeignKey("venues.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("connector", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("method", sa.String(), nullable=False, server_default="GET"),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), index=True),
    )


def downgrade() -> None:
    op.drop_table("app_calls")
    op.drop_table("app_shares")
    op.drop_table("app_versions")
    op.drop_table("apps")
