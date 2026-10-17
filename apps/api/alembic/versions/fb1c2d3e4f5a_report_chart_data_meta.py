"""A dashboard chart remembers what its cached rows are for.

``report_charts.data_meta`` — ``{"refreshed_at", "venue_id", "period"}``,
written by every refresh beside ``data``. The page uses it to skip a tile on
open whose cached rows are still younger than that tile's own refresh
interval (``chart_spec.refresh_interval_seconds``) and were fetched for the
same venue and period — so eleven tiles no longer re-read Loaded every time
the dashboard is opened (Oct 2026).

Revision ID: fb1c2d3e4f5a
Revises: fa0b1c2d3e4f
"""

import sqlalchemy as sa
from alembic import op

revision = "fb1c2d3e4f5a"
down_revision = "fa0b1c2d3e4f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("report_charts", sa.Column("data_meta", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("report_charts", "data_meta")
