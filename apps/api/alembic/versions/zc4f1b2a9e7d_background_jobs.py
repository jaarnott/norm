"""Background jobs: long work that runs outside the chat request.

A chat request is cut off after 25 minutes; a 200-invoice receive lost
everything it had done twice on 14 Oct 2026. Jobs are claimed by a worker,
heartbeat, save progress as they go and resume after a restart
(services/background_jobs).

Revision ID: zc4f1b2a9e7d
Revises: zb2c3d4e5f6a
"""

import sqlalchemy as sa
from alembic import op

revision = "zc4f1b2a9e7d"
down_revision = "zb2c3d4e5f6a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "background_jobs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False, server_default=""),
        sa.Column(
            "organization_id",
            sa.String(),
            sa.ForeignKey("organizations.id"),
            nullable=True,
        ),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("venue_id", sa.String(), sa.ForeignKey("venues.id"), nullable=True),
        sa.Column("thread_id", sa.String(), sa.ForeignKey("threads.id"), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("params", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("progress", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("claimed_by", sa.String(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "cancel_requested", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_background_jobs_status", "background_jobs", ["status"])
    op.create_index("ix_background_jobs_thread", "background_jobs", ["thread_id"])


def downgrade() -> None:
    op.drop_index("ix_background_jobs_thread", table_name="background_jobs")
    op.drop_index("ix_background_jobs_status", table_name="background_jobs")
    op.drop_table("background_jobs")
