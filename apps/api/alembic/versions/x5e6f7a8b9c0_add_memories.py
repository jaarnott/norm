"""Add memories

Learned facts about a user or an organisation. Deliberately in the main
per-environment DB rather than the config DB: the config DB has no
organization_id and is shared across every environment and every organisation,
so an org-scoped row there would be readable by other tenants.

Revision ID: x5e6f7a8b9c0
Revises: w4d5e6f7a8b9
Create Date: 2026-07-20 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "x5e6f7a8b9c0"
down_revision = "w4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memories",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "organization_id",
            sa.String(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("venue_id", sa.String(), sa.ForeignKey("venues.id"), nullable=True),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("why", sa.Text(), nullable=True),
        sa.Column("how_to_apply", sa.Text(), nullable=True),
        sa.Column(
            "thread_id",
            sa.String(),
            sa.ForeignKey("threads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_by", sa.String(), nullable=False, server_default="agent"),
        sa.Column("trigger", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
        sa.Column(
            "superseded_by",
            sa.String(),
            sa.ForeignKey("memories.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("review_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_memories_user_id", "memories", ["user_id"])
    op.create_index("ix_memories_organization_id", "memories", ["organization_id"])
    # The index is loaded for every turn, filtered to what is live and in
    # scope — this is the query that has to stay cheap.
    op.create_index(
        "ix_memories_recall", "memories", ["organization_id", "status", "scope"]
    )


def downgrade() -> None:
    op.drop_index("ix_memories_recall", table_name="memories")
    op.drop_index("ix_memories_organization_id", table_name="memories")
    op.drop_index("ix_memories_user_id", table_name="memories")
    op.drop_table("memories")
