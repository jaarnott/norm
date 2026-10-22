"""apps.kind + app_viewer_settings — dashboards become a kind of app

Norm builds one thing in two kinds (docs/plans/apps-and-dashboards-one-system.md):
an App lives in its AI agent's menu and can be published; a Dashboard lives on
its AI agent's Dashboard page and is shared the way dashboards are (only its
owner changes it, anyone else uses it or makes a copy). Every existing app is
an App, so the default needs no backfill.

app_viewer_settings is each person's own choices on a page (colours, % or $,
the last period): one row per app, organisation and person, so one person's
choice never changes anyone else's screen. ``app_key`` is the app's id, or
``template:<slug>`` / ``builtin:<slug>`` for a page that has no apps row.

Revision ID: d4shk1nd2110
Revises: zg2i4k6m8o0r
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d4shk1nd2110"
down_revision = "zg2i4k6m8o0r"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "apps",
        sa.Column("kind", sa.String(), nullable=False, server_default="app"),
    )
    op.create_table(
        "app_viewer_settings",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("app_key", sa.String(), nullable=False),
        sa.Column(
            "organization_id",
            sa.String(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "app_key", "organization_id", "user_id", name="uq_app_viewer_setting"
        ),
    )


def downgrade() -> None:
    op.drop_table("app_viewer_settings")
    op.drop_column("apps", "kind")
