"""Register the `*_for_period` date-safe consolidators on the loadedhub spec.

Each fronts one existing action, taking a period in plain English and resolving
it through Norm's venue-aware calendar instead of making the caller work out ISO
timestamps. One reviewed function_code (config/consolidators/for_period.py)
serves them all; the tools differ only in the WRAPPED entry below.

The raw actions are left untouched, so dashboards, saved reports and the in-app
agents keep working exactly as they do. Each new tool is invisible until
deliberately switched on — for an agent via its AgentConnectorBinding
capabilities, for Claude via Settings → MCP (McpCapability rows are fail-closed).

WHY THESE AND NOT THE OTHERS
---------------------------
Of the 46 loadedhub tools, 20 take a date parameter. Only these front actions
where a *trading-day window* is both meaningful and expressible:

Excluded deliberately —
  get_budgets, list_received_invoices, list_stock_invoices
      Their API takes YYYY-MM-DD only, so a 07:00 boundary CANNOT be expressed.
      Wrapping them would imply a precision the upstream cannot honour. Invoice
      and budget dates are calendar dates anyway — the trading day does not
      apply to when an invoice is dated.
  get_stock_on_hand
      A single point-in-time snapshot (report_datetime), not a window.
  create_rostered_shift, update_shift
      Writes, and their times are the shift's own values, not a query window.
      This pattern must never wrap a write: consolidators are declared GET and
      so bypass the human approval gate.
  generate_stocktake_report
      start_/end_stocktake_id are IDs, not dates.

    uv run python scripts/sync_for_period_config.py --dry-run
    uv run python scripts/sync_for_period_config.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

FUNCTION_CODE_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "for_period.py"
)

# (new action, wrapped action, start param, end param, what it returns)
WRAPPED = [
    (
        "get_sales_for_period",
        "get_sales_data",
        "start_datetime",
        "end_datetime",
        "Sales totals broken down by interval",
    ),
    (
        "get_pos_orders_for_period",
        "get_pos_orders",
        "start",
        "end",
        "POS order totals broken down by interval",
    ),
    (
        "get_pos_item_sales_for_period",
        "get_pos_item_sales",
        "start_time",
        "end_time",
        "Product sales with group and category",
    ),
    (
        "get_staff_orders_for_period",
        "get_staff_orders",
        "start",
        "end",
        "Orders by staff member",
    ),
    (
        "get_staff_item_orders_for_period",
        "get_staff_item_orders",
        "start",
        "end",
        "Product orders for one staff member",
    ),
    (
        "get_pos_discounts_for_period",
        "get_pos_discounts",
        "start",
        "end",
        "Discounts by staff member",
    ),
    (
        "get_roster_for_period",
        "get_roster",
        "start_datetime",
        "end_datetime",
        "The roster including all shifts",
    ),
    (
        "get_roster_vs_actual_for_period",
        "get_roster_vs_actual",
        "start",
        "end",
        "Rostered versus actual hours and cost",
    ),
    (
        "get_timeclock_entries_for_period",
        "get_timeclock_entries",
        "start_time",
        "end_time",
        "Timeclock entries",
    ),
    (
        "get_cogs_detail_for_period",
        "get_cogs_detail",
        "start",
        "end",
        "Cost of goods detail",
    ),
    (
        "get_completed_stocktakes_for_period",
        "get_completed_stocktakes",
        "start_date",
        "end_date",
        "Completed stocktakes",
    ),
    (
        "get_received_invoices_for_period",
        "get_received_invoices",
        "from",
        "to",
        "Received supplier invoices",
    ),
    (
        "list_supplier_statements_for_period",
        "list_supplier_statements",
        "from_iso",
        "to_iso",
        "Supplier statements",
    ),
]

PERIOD_DESC = (
    "The period in plain English — 'yesterday', 'last week', 'this month'. "
    "Norm resolves it against this venue's trading day. Prefer this over "
    "start/end; do not work out dates yourself."
)
START_DESC = (
    "Only when the user asked for exact clock times (e.g. reconciling against a "
    "bank statement). ISO 8601 with offset. Honoured verbatim."
)


def tool_for(action, wraps, start_param, end_param, returns, function_code):
    return {
        "action": action,
        # Deliberate, and the same choice the other consolidators make:
        # consolidator dispatch auto-executes GET tools. Safe here because these
        # read only — allowed_write_actions is empty, so the sandbox refuses any
        # write.
        "method": "GET",
        "description": (
            f"{returns}, for a period given in plain English. Norm resolves the "
            "period using this venue's trading day — which is NOT midnight to "
            "midnight: a hospitality day runs from the venue's start time "
            "(typically 7:00am) to one second before it the next day, so "
            "late-night trade after midnight belongs to the evening that started "
            "it. Do not calculate timestamps yourself. Only pass start and end if "
            "the user explicitly asked for specific clock times; the result "
            "always states which window was used — report that window to the "
            "user alongside the numbers, so the basis of the figures is visible. "
            "For a group-wide question pass venue='all' to cover every venue in "
            "one call, each measured over its own trading day."
        ),
        "required_fields": [],
        "optional_fields": ["period", "start", "end", "confirmed_by_user"],
        "field_descriptions": {
            "period": PERIOD_DESC,
            "start": START_DESC,
            "end": "Only with start. ISO 8601 with offset. Honoured verbatim.",
        },
        "field_schema": {
            "confirmed_by_user": {
                "type": "boolean",
                "description": (
                    "Set true only after confirming the user really asked for a "
                    "start/end that is not this venue's trading day."
                ),
            },
        },
        "consolidator_config": {
            "max_api_calls": 6,
            "allowed_write_actions": [],
            "wraps": wraps,
            "start_param": start_param,
            "end_param": end_param,
            "function_code": function_code,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from app.db.config_models import ConnectorSpec
    from app.db.engine import _ConfigSessionLocal

    function_code = FUNCTION_CODE_PATH.read_text(encoding="utf-8")
    desired = {
        action: tool_for(action, wraps, sp, ep, returns, function_code)
        for action, wraps, sp, ep, returns in WRAPPED
    }

    db = _ConfigSessionLocal()
    changes: list[str] = []
    try:
        spec = (
            db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == "loadedhub")
            .first()
        )
        if not spec:
            raise SystemExit("loadedhub ConnectorSpec not found in config DB")

        tools = list(spec.tools or [])
        by_action = {t.get("action"): i for i, t in enumerate(tools)}

        # Every wrapped action must exist, or the new tool would fail at runtime
        # in a way no test would catch — the config blind spot.
        missing = [w for _, w, _, _, _ in WRAPPED if w not in by_action]
        if missing:
            raise SystemExit(f"wrapped actions missing from the spec: {missing}")

        for action, tool in desired.items():
            if action in by_action:
                if tools[by_action[action]] != tool:
                    tools[by_action[action]] = tool
                    changes.append(f"updated: {action}")
            else:
                tools.append(tool)
                changes.append(
                    f"added:   {action}  (wraps {tool['consolidator_config']['wraps']})"
                )
        spec.tools = tools

        if not changes:
            print("Config already in sync — nothing to do.")
            return
        for line in changes:
            print(("DRY RUN: " if args.dry_run else "") + line)
        if args.dry_run:
            db.rollback()
        else:
            from sqlalchemy.orm.attributes import flag_modified

            flag_modified(spec, "tools")
            db.commit()
            print(f"\nApplied {len(changes)} change(s).")
            print(
                "Not reachable by anyone yet — enable deliberately:\n"
                "  agents: add to the AgentConnectorBinding capabilities\n"
                "  Claude: Settings → MCP, enable the loadedhub__*_for_period tools"
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
