"""Saved charts show the supplier price change as a percent.

``get_received_items_for_period`` reported ``unit_cost_change_pct`` as a
fraction (0.25 for +25%), which an agent read as "up 0.3%". From Oct 2026 it
is a percent (25.0). The Cost of Goods dashboard's price-movers table
formatted the old fraction with ``{"type": "percent"}`` (×100); charts saved
from that template now show the number as it comes, with a "%" suffix.

Revision ID: u4w6y8a1c3e5
Revises: s7g9h2k4m1p3
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "u4w6y8a1c3e5"
down_revision = "s7g9h2k4m1p3"
branch_labels = None
depends_on = None

FIELD = "unit_cost_change_pct"
OLD = {"type": "percent"}
NEW = {"type": "number", "decimals": 1, "suffix": "%"}


def _swap(src: dict, dst: dict) -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, chart_spec FROM report_charts")).fetchall()
    for chart_id, spec in rows:
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except ValueError:
                continue
        formats = (spec or {}).get("field_formats") if isinstance(spec, dict) else None
        if not isinstance(formats, dict) or formats.get(FIELD) != src:
            continue
        spec = {**spec, "field_formats": {**formats, FIELD: dst}}
        conn.execute(
            sa.text("UPDATE report_charts SET chart_spec = :spec WHERE id = :id"),
            {"spec": json.dumps(spec), "id": chart_id},
        )


def upgrade() -> None:
    _swap(OLD, NEW)


def downgrade() -> None:
    _swap(NEW, OLD)
