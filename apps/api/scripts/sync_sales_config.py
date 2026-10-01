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
    "description": (
        "Sales for a period given in plain English, resolved against each "
        "venue's trading day. THE sales tool: one venue by default, venues "
        "accepts a list or 'all' for the whole group. measure picks one of "
        "two different things — 'sales' (default) is the MONEY taken: bills "
        "as they were PAID, after discounts; 'orders' is what was ORDERED: "
        "orders as they were PLACED on the POS, how many and their value as "
        "rung up, paid yet or not. They differ — open tabs and unpaid orders "
        "count as orders but not yet as sales, and orders are valued before "
        "discounts. Use orders for 'how many orders', 'when did orders come "
        "in', 'average order'; sales for revenue, takings and turnover. "
        "Figures INCLUDE tax (GST) by default — what "
        "the till took and what Loaded shows; pass tax 'exclude' only when "
        "the user asks for figures excluding tax / ex-GST. breakdown picks "
        "the cut — 'total' (default), 'daily' (interval sets the bucket: per "
        "day by default, per week, or hourly / half-hourly through the "
        "day), 'items' (product mix), 'staff' (sales by staff member; "
        "staff_name drills into one person's items), or 'discounts'. compare "
        "adds engine-computed columns to total/daily: 'budget' and/or "
        "'last_year' (the aligned trading week exactly 52 weeks back — the "
        "same baseline for every venue). time_windows cuts by clock time "
        "(e.g. dinner 17:00-22:00) with trading-day attribution. Items/staff "
        "return the top rows plus an '(others)' rollup so totals stay "
        "honest; item sales are before discounts, so they add up to more "
        "than total sales. Ask ONCE for the whole period per venue — not "
        "month by month — unless the user wants a per-month breakdown. For "
        "'top N items' use breakdown 'items', ranked by revenue unless the "
        "user asks for quantity."
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
            "The period in plain English — 'yesterday', 'last week', 'this "
            "month'. Norm resolves it against the venue's trading day. "
            "Prefer this over start/end; do not work out dates yourself."
        ),
        "start": (
            "Only when the user asked for exact clock times. ISO 8601 with "
            "offset. Honoured verbatim after confirmation."
        ),
        "end": "Window end, with the same rule as start.",
        "confirmed_by_user": (
            "Only for an explicit start/end that is not a trading day, and "
            "only when the user really did ask for those clock times."
        ),
        "venues": (
            "'all' (every connected venue), a list of venue names, or omit "
            "for the single venue in `venue`."
        ),
        "measure": (
            "'sales' (default): the money taken — bills when they were paid, "
            "after discounts; rows carry `actual`. 'orders': orders when they "
            "were placed — not bills (a bar tab can hold several rounds, each "
            "its own order), people or items. Rows carry `orders` (how "
            "many), `order_value` (their value as rung up, before discounts) "
            "and `average_order` (order_value ÷ orders). An order counts the "
            "moment it is placed, paid or not, so orders and sales differ: "
            "hourly orders show when orders came in, hourly sales when money "
            "was taken. With breakdown total or daily, and time_windows. "
            "compare 'last_year' works for orders; 'budget' does not (budgets "
            "are money taken)."
        ),
        "tax": (
            "'include' (default): figures include tax (GST), as rung up and as "
            "Loaded shows them — use this unless the user asks otherwise. "
            "'exclude': tax taken off — sales lose the tax Loaded recorded in "
            "them, budgets (set tax-inclusive) are divided by 1 + their tax "
            "rate, and order values, item, staff and discount amounts (which "
            "carry no tax split) are divided by 1 + the GST rate, as Loaded's "
            "own reports do. The result's `tax` says which applied."
        ),
        "breakdown": ("'total' (default) | 'daily' | 'items' | 'staff' | 'discounts'."),
        "compare": (
            "'budget', 'last_year', or both (list or comma-separated) — "
            "with breakdown total or daily. The joins and group totals are "
            "computed here, never by the model."
        ),
        "time_windows": (
            'Clock-time cuts, e.g. [{"start_hour": 17, "end_hour": 22, '
            '"label": "dinner"}]. Hours before the venue\'s day start '
            "belong to the previous trading day. Works with breakdown "
            "total/daily (sales per cut) and items (product mix per cut)."
        ),
        "group_by": (
            "Row grouping for time_windows: 'each' (per day) | 'week' | "
            "'month' | 'total'."
        ),
        "day_of_week": (
            "Filter for time_windows rows: a day name, a comma list, "
            "'weekday' or 'weekend'."
        ),
        "top": (
            "Cap for items/staff rows (default 25 items); the rest roll "
            "into '(others)' so totals stay honest."
        ),
        "category": "Items breakdown: keep items whose category contains this.",
        "group": "Items breakdown: keep items whose group contains this.",
        "sort_by": "Items breakdown: 'sales' (default) or 'quantity'.",
        "staff_name": (
            "Staff breakdown: drill into one person's product mix. "
            "Case-insensitive substring of their POS name."
        ),
        "interval": (
            "Daily breakdown: the bucket, d.hh:mm:ss — '1.00:00:00' per day "
            "(default), '7.00:00:00' per week, '01:00:00' hourly, '00:30:00' "
            "half-hourly. Hourly and finer rows carry their trading date and "
            "clock time (a 1am bucket belongs to the day before), without the "
            "empty hours before opening and after close; compare 'budget' "
            "needs a daily or longer bucket. Staff breakdown (one venue): "
            "adds per-slot winners."
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
