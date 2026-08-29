"""Add org_app_entitlements — the org-scoped half of the app marketplace.

The catalog (`marketplace_apps`) lives in the CONFIG db and auto-creates at
startup; this main-db table records which org has which App enabled. Absence of
a row means the app's `bundled` default applies, so this ships empty and
changes nothing (docs/apps-marketplace-plan.md Phase 1).

Revision ID: p1q2r3s4t5u6
Revises: e7f8a9b0c1d2
"""

import sqlalchemy as sa
from alembic import op

revision = "p1q2r3s4t5u6"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "org_app_entitlements",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.String(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("app_slug", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("stripe_subscription_item_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("organization_id", "app_slug", name="uq_org_app"),
    )
    op.create_index(
        "ix_org_app_entitlements_organization_id",
        "org_app_entitlements",
        ["organization_id"],
    )
    op.create_index(
        "ix_org_app_entitlements_app_slug", "org_app_entitlements", ["app_slug"]
    )


def downgrade() -> None:
    op.drop_index("ix_org_app_entitlements_app_slug", "org_app_entitlements")
    op.drop_index("ix_org_app_entitlements_organization_id", "org_app_entitlements")
    op.drop_table("org_app_entitlements")
