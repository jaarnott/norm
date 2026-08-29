"""Drop the three Organization.*_agent_enabled booleans.

Marketplace entitlements (`org_app_entitlements` + the catalog's agent-bundle
apps hr-agent/procurement-agent/reports-agent) replaced them as the single
switch for BOTH agent access and the charge, so the booleans can only drift.

No data copy is performed, deliberately. The booleans were billing-display
only — nothing at runtime ever read them, so a False never blocked an agent
(local ran the procurement agent daily with its boolean False). The agent
apps are `bundled` in the catalog, which keeps every agent on for every org,
matching actual pre-migration behaviour. Production's org had all three True
(verified 29 Aug 2026), so its billing display is unchanged too. An org that
wants a paid agent off now disables the app in the marketplace, which both
blocks it and stops the charge.

Revision ID: q2r3s4t5u6v7
Revises: p1q2r3s4t5u6
"""

import sqlalchemy as sa
from alembic import op

revision = "q2r3s4t5u6v7"
down_revision = "p1q2r3s4t5u6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("organizations", "hr_agent_enabled")
    op.drop_column("organizations", "procurement_agent_enabled")
    op.drop_column("organizations", "reports_agent_enabled")


def downgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "hr_agent_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "organizations",
        sa.Column(
            "procurement_agent_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "organizations",
        sa.Column(
            "reports_agent_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
