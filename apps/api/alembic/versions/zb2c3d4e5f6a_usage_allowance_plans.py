"""Plans become usage allowances: Core, Pro, Max; top-ups add usage in dollars.

Oct 2026 pricing: a plan includes a month of usage measured as what Norm's
model calls cost (TokenUsage.cost_usd), not a token count. The token plans
are renamed to their successors (basic → core, standard → pro; max keeps its
name), and a top-up records the usage it adds (``usage_usd``) instead of
tokens. Plan amounts live in code (billing_service.PLANS).

Revision ID: zb2c3d4e5f6a
Revises: za1b2c3d4e5f
"""

import sqlalchemy as sa
from alembic import op

revision = "zb2c3d4e5f6a"
down_revision = "za1b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "token_top_ups", sa.Column("usage_usd", sa.Numeric(10, 2), nullable=True)
    )
    op.execute("UPDATE subscriptions SET token_plan = 'core' WHERE token_plan = 'basic'")
    op.execute("UPDATE subscriptions SET token_plan = 'pro' WHERE token_plan = 'standard'")


def downgrade() -> None:
    op.execute("UPDATE subscriptions SET token_plan = 'standard' WHERE token_plan = 'pro'")
    op.execute("UPDATE subscriptions SET token_plan = 'basic' WHERE token_plan = 'core'")
    op.drop_column("token_top_ups", "usage_usd")
