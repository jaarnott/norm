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

Usage:
    uv run python scripts/sync_sales_config.py [--dry-run]
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

FUNCTION_CODE_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "get_sales.py"
)

TOOL = {
    "action": "get_sales",
    "method": "GET",  # read-only consolidator: auto-executes, nestable
    "description": (
        "Sales for a period given in plain English, resolved against each "
        "venue's trading day. THE sales tool: one venue by default, venues "
        "accepts a list or 'all' for the whole group. breakdown picks the "
        "cut — 'total' (default), 'daily', 'items' (product mix), 'staff' "
        "(sales by staff member; staff_name drills into one person's "
        "items), or 'discounts'. compare adds engine-computed columns to "
        "total/daily: 'budget' and/or 'last_year' (the aligned trading "
        "week exactly 52 weeks back — the same baseline for every venue). "
        "time_windows cuts by clock time (e.g. dinner 17:00-22:00) with "
        "trading-day attribution. Items/staff return the top rows plus an "
        "'(others)' rollup so totals stay honest."
    ),
    "required_fields": [],
    "optional_fields": [
        "period",
        "start",
        "end",
        "confirmed_by_user",
        "venues",
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
            "Daily breakdown: custom bucket d.hh:mm:ss (default daily). "
            "Staff breakdown (one venue): adds per-slot winners."
        ),
    },
    "field_schema": {
        "venues": {"description": "'all' or a list of venue names"},
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


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectionSpec
    from app.db.engine import _ConfigSessionLocal

    tool = dict(TOOL)
    tool["consolidator_config"] = {
        **TOOL["consolidator_config"],
        "function_code": FUNCTION_CODE_PATH.read_text(encoding="utf-8"),
    }

    db = _ConfigSessionLocal()
    try:
        spec = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == "loadedhub")
            .first()
        )
        if not spec:
            raise SystemExit("loadedhub ConnectionSpec not found")
        tools = [dict(t) for t in (spec.tools or [])]
        idx = next(
            (i for i, t in enumerate(tools) if t.get("action") == tool["action"]),
            None,
        )
        if idx is not None and tools[idx] == tool:
            print("get_sales: already up to date")
            return
        if idx is None:
            tools.append(tool)
            what = "added"
        else:
            keep = tools[idx].get("added_at")
            if keep:
                tool["added_at"] = keep
            tools[idx] = tool
            what = "updated"
        if dry_run:
            print(f"DRY RUN — would have {what} get_sales")
            return
        spec.tools = tools
        flag_modified(spec, "tools")
        spec.version = (spec.version or 0) + 1
        db.commit()
        print(f"get_sales {what}, spec version -> {spec.version}")
    finally:
        db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
