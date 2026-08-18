"""app_records — the app platform's storage primitive

Gives an app somewhere to put rows it owns. Before this, an app could only be a
view over connector actions, so anything an app created (a training sign-off, a
candidate note) had to live in someone else's system of record.

Rows are keyed by NAMESPACE rather than by app, so two apps in one domain can
share data by explicit declaration instead of by reaching into each other's
tables. See the AppRecord docstring for the full rule.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-08-17
"""

from alembic import op
import sqlalchemy as sa

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_records",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("organization_id", sa.String(), nullable=False),
        # NULL = the row belongs to the whole organization, not one venue.
        sa.Column("venue_id", sa.String(), nullable=True),
        sa.Column("collection", sa.String(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["venue_id"], ["venues.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_app_records_namespace", "app_records", ["namespace"])
    op.create_index(
        "ix_app_records_organization_id", "app_records", ["organization_id"]
    )
    op.create_index("ix_app_records_venue_id", "app_records", ["venue_id"])
    # The two shapes every query takes: "this collection for the org" and
    # "this collection for a venue".
    op.create_index(
        "ix_app_records_org_collection",
        "app_records",
        ["namespace", "collection", "organization_id"],
    )
    op.create_index(
        "ix_app_records_venue_collection",
        "app_records",
        ["namespace", "collection", "venue_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_app_records_venue_collection", table_name="app_records")
    op.drop_index("ix_app_records_org_collection", table_name="app_records")
    op.drop_index("ix_app_records_venue_id", table_name="app_records")
    op.drop_index("ix_app_records_organization_id", table_name="app_records")
    op.drop_index("ix_app_records_namespace", table_name="app_records")
    op.drop_table("app_records")
