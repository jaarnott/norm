"""Install `loadedhub.get_sales` — THE sales domain tool.

Phase 1 of the domain-tools arc (24 Aug 2026). One tool answers every
read-side sales question; it absorbs eight retiring tools (the five sales
wrappers and norm_reports' three periodic tools) plus the budget/last-year
joins the model used to do by hand. Born from prod thread b9bda2c1, where
a group budget-vs-actual took ~40 tool calls, quoted two different
last-year baselines in one conversation, and silently answered
venue='all' with a single venue.

ORDER: run AFTER deploying the API — the norm.list_venues internal tool
(fan-out backbone) and the venue='all' refusal ship in the same push.
The retiring tools are removed by sync_sales_domain_rollout.py, which
also swaps every binding/playbook/prompt/MCP reference; run this first so
the replacement exists before anything is taken away.

1 Oct 2026: `measure` (sales | orders) absorbs get_pos_orders_for_period —
orders read Loaded's orders feed (placed, valued before discounts, paid or
not), sales its sales feed (paid, after discounts); `tax` (include | exclude) lets the model ask for ex-tax figures, and a
sub-day `interval` gives clock-time buckets. The row now carries its
`shapes` from config/consolidators/shapes.json — this script used to
replace the row without them, which would have dropped the shaping the
Sep-2026 transform move put there.

Usage:
    uv run python scripts/sync_sales_config.py [--dry-run]
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

FUNCTION_CODE_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "get_sales.py"
)
SHAPES_PATH = FUNCTION_CODE_PATH.parent / "shapes.json"

# The company's sales-tax rates — tax 'exclude' divides item, staff and
# discount amounts by 1 + the GST rate. Verified live 1 Oct 2026 (La Zeppa):
# [{"sortOrder": 0, "label": "Exempt", "rate": 0.0},
#  {"sortOrder": 1, "label": "GST", "rate": 0.15}]. The 1.0 API host refuses
# it (403); Mercury's host serves it.
SALES_TAX_ENDPOINT = {
    "action": "get_sales_tax_rates",
    "method": "GET",
    "description": (
        "The company's sales-tax rates, e.g. Exempt 0% and GST 15%. Read by "
        "get_sales to take tax off item, staff and discount amounts."
    ),
    "path_template": "//loadedhub.com/api/sales-tax",
    "headers": {},
    "required_fields": [],
    "optional_fields": [],
    "field_descriptions": {},
    "field_mapping": {},
    "success_status_codes": [200],
    "timeout_seconds": 30,
    "read_only": True,
}

TOOL = {
    "action": "get_sales",
    "method": "GET",  # read-only consolidator: auto-executes, nestable
    # Tightened 7 Oct 2026: each rule stated once (the tool list is ~65% of
    # every prompt).
    "description": (
        "Sales for a period in plain English, on each venue's trading day — THE sales "
        "tool. One venue by default; venues takes a list or 'all'. measure 'sales' "
        "(default) is the MONEY taken: bills as PAID, after discounts. 'orders' is "
        "what was ORDERED: orders as PLACED, paid or not, before discounts — for order "
        "counts, when orders came in, average order. Figures include GST; tax "
        "'exclude' only when the user asks for ex-GST. breakdown: 'total' (default) | "
        "'daily' (interval: per day, week, hourly, half-hourly) | 'items' (product "
        "mix; top N by revenue unless asked by quantity) | 'staff' (staff_name drills "
        "in) | 'discounts'. compare adds 'budget' and/or 'last_year' (the trading week "
        "52 weeks back) to total/daily. time_windows cuts by clock time. Items/staff "
        "show top rows plus '(others)'; item sales are pre-discount, so they sum to "
        "more than total sales. Ask ONCE for the whole period per venue, not month by "
        "month, unless asked per month."
    ),
    "required_fields": [],
    "optional_fields": [
        "period",
        "start",
        "end",
        "confirmed_by_user",
        "venues",
        "measure",
        "tax",
        "breakdown",
        "compare",
        "time_windows",
        "group_by",
        "day_of_week",
        "top",
        "category",
        "group",
        "sort_by",
        "staff_name",
        "interval",
    ],
    "field_descriptions": {
        "period": (
            "Plain English — 'yesterday', 'last week', 'this month'. Prefer it to "
            "start/end; never work out dates yourself."
        ),
        "start": "Only for exact clock times the user asked for: ISO 8601 with offset.",
        "end": "Window end; same rule as start.",
        "confirmed_by_user": (
            "Only when the user really asked for a start/end that isn't a trading day."
        ),
        "venues": "'all', a list of venue names, or omit for the venue in `venue`.",
        "measure": (
            "'sales' (default; rows carry `actual`) or 'orders' (rows carry `orders`, "
            "`order_value` before discounts, `average_order`). An order counts when "
            "placed — a tab holds several — so hourly orders show when orders came in. "
            "With total, daily and time_windows; compare 'budget' needs sales."
        ),
        "tax": (
            "'include' (default — as rung up and as Loaded shows) or 'exclude' (only "
            "when asked; tax removed as Loaded's reports do). The result's `tax` says "
            "which applied."
        ),
        "breakdown": "'total' (default) | 'daily' | 'items' | 'staff' | 'discounts'.",
        "compare": (
            "'budget', 'last_year', or both — total/daily only. Computed here, never "
            "by you."
        ),
        "time_windows": (
            'Clock cuts, e.g. [{"start_hour": 17, "end_hour": 22, "label": "dinner"}]. '
            "Hours before day start belong to the previous trading day. With "
            "total/daily and items."
        ),
        "group_by": "time_windows rows: 'each' (day) | 'week' | 'month' | 'total'.",
        "day_of_week": (
            "time_windows filter: a day, a comma list, 'weekday' or 'weekend'."
        ),
        "top": "items/staff rows before '(others)' (default 25 items).",
        "category": "items: keep items whose category contains this.",
        "group": "items: keep items whose group contains this.",
        "sort_by": "items: 'sales' (default) or 'quantity'.",
        "staff_name": "staff: one person's product mix (part of their POS name).",
        "interval": (
            "daily bucket d.hh:mm:ss — '1.00:00:00' day (default), '7.00:00:00' week, "
            "'01:00:00' hourly, '00:30:00' half-hourly. Hourly rows carry trading date "
            "and clock time; budget needs a day or longer. staff (one venue): per-slot "
            "winners."
        ),
    },
    "field_schema": {
        "venues": {"description": "'all' or a list of venue names"},
        "measure": {"type": "string", "enum": ["sales", "orders"]},
        "tax": {"type": "string", "enum": ["include", "exclude"]},
        "compare": {"description": "budget and/or last_year"},
        "time_windows": {
            "description": "list of {start_hour, end_hour, label} clock cuts"
        },
    },
    "max_result_chars": 60_000,
    "read_only": True,
    "consolidator_config": {
        # function_code injected at sync time. Budget: resolve(1) +
        # list_venues(1) + per venue up to 3 calls for total/daily compare,
        # or up to ~8 for the item/hourly window strategies.
        "max_api_calls": 40,
        "allowed_write_actions": [],
    },
}


def build_tool() -> dict:
    """The row as installed: TOOL plus its code and its shapes."""
    tool = dict(TOOL)
    tool["consolidator_config"] = {
        **TOOL["consolidator_config"],
        "function_code": FUNCTION_CODE_PATH.read_text(encoding="utf-8"),
        "shapes": json.loads(SHAPES_PATH.read_text(encoding="utf-8"))[
            "loadedhub.get_sales"
        ],
    }
    return tool


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.connectors import spec_rows
    from app.db.config_models import ConnectionSpec
    from app.db.engine import _ConfigSessionLocal

    tool = build_tool()

    db = _ConfigSessionLocal()
    try:
        spec = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == "loadedhub")
            .with_for_update()
            .first()
        )
        if not spec:
            raise SystemExit("loadedhub ConnectionSpec not found")
        if not spec_rows.is_split(spec):
            raise SystemExit("loadedhub is not split into tools/endpoints")
        changes = []

        def _bare(row):
            return {k: v for k, v in (row or {}).items() if k != "added_at"}

        ep = spec_rows.find_endpoint(spec, SALES_TAX_ENDPOINT["action"])
        if _bare(ep) != SALES_TAX_ENDPOINT:
            changes.append(
                ("added" if ep is None else "updated") + " endpoint get_sales_tax_rates"
            )
        live = spec_rows.find_tool(spec, tool["action"])
        if _bare(live) != tool:
            changes.append(("added" if live is None else "updated") + " tool get_sales")
        for c in changes or ["get_sales: already up to date"]:
            print(("DRY RUN — would have " if dry_run else "") + c)
        if dry_run or not changes:
            return

        spec.endpoints = spec_rows.upsert(spec.endpoints, [SALES_TAX_ENDPOINT])
        if live is not None and live.get("added_at"):
            tool["added_at"] = live["added_at"]
        spec.tools = spec_rows.upsert(spec.tools, [tool])
        flag_modified(spec, "endpoints")
        flag_modified(spec, "tools")
        spec.version = (spec.version or 0) + 1
        db.commit()
        print(f"spec version -> {spec.version}")
    finally:
        db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
