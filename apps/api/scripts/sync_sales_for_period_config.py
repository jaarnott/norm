"""Register the `loadedhub.get_sales_for_period` consolidator in the config DB.

Adds a date-safe wrapper around `get_sales_data`. The raw action is left
untouched: dashboards, saved reports and the in-app agents keep working exactly
as they do, and this tool is invisible until deliberately switched on — for an
agent via its AgentConnectorBinding capabilities, for Claude via Settings → MCP
(McpCapability rows are fail-closed).

Why it exists: `get_sales_data` takes raw ISO timestamps, so the caller has to
work out the window. Claude computed midnight-to-midnight for "yesterday", but a
trading day runs from the venue's day_start_time (07:00) to one second before it
the next day — so a late-night venue read $0 for a Saturday and that looked like
a POS outage rather than a bad window. This tool takes a period in plain English
and resolves it through Norm's own venue-aware calendar, which makes the rule a
property of the interface rather than advice a client may ignore.

    uv run python scripts/sync_sales_for_period_config.py --dry-run
    uv run python scripts/sync_sales_for_period_config.py
"""

from __future__ import annotations

import argparse
import pathlib

FUNCTION_CODE_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "sales_for_period.py"
)

CONSOLIDATOR_TOOL = {
    "action": "get_sales_for_period",
    # Deliberate, and the same choice the other consolidators make: consolidator
    # dispatch auto-executes GET tools. Safe here because this one reads only —
    # allowed_write_actions is empty, so the sandbox refuses any write.
    "method": "GET",
    "description": (
        "Sales totals for a period, broken down by interval. Give the period in "
        "plain English ('yesterday', 'last week') and Norm resolves it using this "
        "venue's trading day — which is NOT midnight to midnight: a hospitality "
        "day runs from the venue's start time (typically 7:00am) to one second "
        "before it the next day, so late-night trade after midnight belongs to the "
        "evening that started it. Do not calculate timestamps yourself. Only pass "
        "start and end if the user explicitly asked for specific clock times; the "
        "result always states which window was used."
    ),
    "required_fields": [],
    "optional_fields": [
        "period",
        "start",
        "end",
        "interval",
        "confirmed_by_user",
    ],
    "field_descriptions": {
        "period": (
            "The period in plain English — 'yesterday', 'last week', 'this month'. "
            "Norm resolves it against this venue's trading day. Prefer this over "
            "start/end; do not work out dates yourself."
        ),
        "start": (
            "Only when the user asked for exact clock times (e.g. reconciling "
            "against a bank statement). ISO 8601 with offset. Honoured verbatim."
        ),
        "end": "Only with start. ISO 8601 with offset. Honoured verbatim.",
        "interval": "Bucket size, e.g. '1.00:00:00' for daily. Default daily.",
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
        # Reads only. This pattern must never wrap a write: consolidators are
        # declared GET and so bypass the human approval gate.
        "allowed_write_actions": [],
    },
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from app.db.config_models import ConnectorSpec
    from app.db.engine import _ConfigSessionLocal

    tool = dict(CONSOLIDATOR_TOOL)
    tool["consolidator_config"] = {
        **CONSOLIDATOR_TOOL["consolidator_config"],
        "function_code": FUNCTION_CODE_PATH.read_text(encoding="utf-8"),
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

        if "get_sales_data" not in by_action:
            raise SystemExit(
                "loadedhub.get_sales_data not found — this tool wraps it, so "
                "something is wrong with the spec rather than with this script."
            )

        action = tool["action"]
        if action in by_action:
            if tools[by_action[action]] != tool:
                tools[by_action[action]] = tool
                changes.append(f"spec tool updated: {action}")
        else:
            tools.append(tool)
            changes.append(f"spec tool added: {action}")
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
            print(f"Applied {len(changes)} change(s).")
            print(
                "\nNot yet reachable by anyone — enable it deliberately:\n"
                "  agents: add to the AgentConnectorBinding capabilities\n"
                "  Claude: Settings → MCP, enable loadedhub__get_sales_for_period"
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
