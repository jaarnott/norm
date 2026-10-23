"""Connections belong to an organisation.

A connection with no venue was used by every organisation's calls: the
executor fell back to "any venue-less row" for a venue with none of its own.
Production's BambooHR connection is exactly that row, and it is Cook Brothers',
so another organisation's Norm could read its employees and applicants
(23 Oct 2026; nobody did). Each row now records its organisation: a venue's row
takes its venue's, and a venue-less row is used only by the organisation it
names (services/connection_scope.py). Venue-less rows can't be attributed here
— scripts/attribute_org_wide_connections.py names their organisation.

Revision ID: bk2m4o6q8s0u
Revises: ah3j5l7n9p1s
"""

import sqlalchemy as sa
from alembic import op

revision = "bk2m4o6q8s0u"
down_revision = "ah3j5l7n9p1s"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "connector_configs",
        sa.Column(
            "organization_id",
            sa.String(),
            sa.ForeignKey("organizations.id"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_connector_configs_organization_id",
        "connector_configs",
        ["organization_id"],
    )
    op.execute(
        "UPDATE connector_configs AS c SET organization_id = v.organization_id "
        "FROM venues AS v WHERE c.venue_id = v.id AND c.organization_id IS NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_connector_configs_organization_id", "connector_configs")
    op.drop_column("connector_configs", "organization_id")
