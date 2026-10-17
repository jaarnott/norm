"""Insights and suggested prompts on Home.

Norm looks at each venue's sales, cost of goods and wages once a day and keeps
what is worth a person's attention as insights (one row per subject). Home
shows a few suggestions per person, learning from what they click, dismiss
and tell Norm about an insight (docs/plans/home-suggested-prompts.md,
docs/plans/insights-research.md).

Revision ID: s7g9h2k4m1p3
Revises: 319934fc4bdd
"""

import sqlalchemy as sa
from alembic import op

revision = "s7g9h2k4m1p3"
down_revision = "319934fc4bdd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("threads", sa.Column("insight_id", sa.String(), nullable=True))

    op.create_table(
        "insights",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "venue_id",
            sa.String(),
            sa.ForeignKey("venues.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("detector", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("subject_key", sa.String(), nullable=False),
        sa.Column("area", sa.String(), nullable=False),
        sa.Column("audience", sa.String(), nullable=False),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("as_of", sa.String(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("subject_key", name="uq_insight_subject"),
    )
    op.create_index("ix_insights_organization_id", "insights", ["organization_id"])

    op.create_table(
        "insight_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "venue_id",
            sa.String(),
            sa.ForeignKey("venues.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slot", sa.String(), nullable=False),
        sa.Column("cadences", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.UniqueConstraint("venue_id", "slot", name="uq_insight_run_slot"),
    )

    op.create_table(
        "insight_notes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "venue_id",
            sa.String(),
            sa.ForeignKey("venues.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("subject_key", sa.String(), nullable=False),
        sa.Column("detector", sa.String(), nullable=False),
        sa.Column("verdict", sa.String(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("until", sa.String(), nullable=True),
        sa.Column("baseline", sa.Numeric(12, 2), nullable=True),
        sa.Column(
            "created_by",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("thread_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_insight_notes_organization_id", "insight_notes", ["organization_id"]
    )
    op.create_index("ix_insight_notes_subject_key", "insight_notes", ["subject_key"])

    op.create_table(
        "suggestion_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.String(), nullable=True),
        sa.Column("suggestion_id", sa.String(), nullable=False),
        sa.Column("subject_key", sa.String(), nullable=True),
        sa.Column("event", sa.String(), nullable=False),
        sa.Column("venue_id", sa.String(), nullable=True),
        sa.Column("value", sa.Numeric(12, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_suggestion_events_user_time", "suggestion_events", ["user_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_suggestion_events_user_time", table_name="suggestion_events")
    op.drop_table("suggestion_events")
    op.drop_index("ix_insight_notes_subject_key", table_name="insight_notes")
    op.drop_index("ix_insight_notes_organization_id", table_name="insight_notes")
    op.drop_table("insight_notes")
    op.drop_table("insight_runs")
    op.drop_index("ix_insights_organization_id", table_name="insights")
    op.drop_table("insights")
    op.drop_column("threads", "insight_id")
