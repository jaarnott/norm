"""Sync the get_staff_attendance consolidator into the config DB.

The function_code lives in the config DB, where no test or review can see it —
the "config blind spot". The canonical source is
config/consolidators/staff_attendance.py, which CI execs under the real sandbox
namespace; this script copies it into the row verbatim.

Why this exists: Loaded's /api/time-clockins returns booked LEAVE as pseudo
clock-ins (type "Leave", 7:00 AM-3:00 PM, 8h at pay rate). The original
function_code counted those as worked hours, inventing "ghost shifts" (Bessie,
Sat 15 Aug 2026: two leave entries reported as two unrostered 8h shifts). The
canonical source splits leave into its own list/totals in every view. The tool
description is updated to match so the agent narrates leave correctly.

Idempotent — safe to re-run; reports whether anything changed. The config DB is
shared across every environment, so committing reaches production. Dry-run first.

Usage:
    .venv/bin/python scripts/sync_staff_attendance_config.py --dry-run
    .venv/bin/python scripts/sync_staff_attendance_config.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

FUNCTION_CODE_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "staff_attendance.py"
)

CONNECTOR = "loadedhub"
ACTION = "get_staff_attendance"

DESCRIPTION = (
    "Rostered vs actual (worked) hours and cost per staff member, per day, or as "
    "shift-by-shift detail. group_by: 'staff' (default), 'day', or 'detail'. "
    "Booked LEAVE is reported separately (a 'leave' list in detail view; "
    "leave_hours/leave_cost fields elsewhere) and is NEVER part of actual worked "
    "hours or the unrostered count — a leave day shows in Loaded's leave "
    "calendar, not on the timesheet, so treat it as absence, not a shift. "
    "Optional staff_name filters to one person. Takes a period in plain "
    "English ('last week') — Norm resolves it against the venue's trading "
    "day; only pass start_datetime/end_datetime when the user asked for "
    "exact clock times (a non-trading window asks for confirmation first)."
)

FIELD_SURFACE = {
    "required_fields": [],
    "optional_fields": [
        "period",
        "start_datetime",
        "end_datetime",
        "confirmed_by_user",
        "staff_name",
        "group_by",
    ],
    "field_descriptions": {
        "period": (
            "The period in plain English — 'last week', 'yesterday'. Norm "
            "resolves it against this venue's trading day. Prefer this over "
            "start/end; never work out dates yourself."
        ),
        "start_datetime": (
            "Only when the user asked for exact clock times. ISO 8601 with "
            "offset. Honoured verbatim after confirmation."
        ),
        "end_datetime": "Window end, same rule as start_datetime.",
        "confirmed_by_user": (
            "Only for an explicit start/end that is not a trading day, and "
            "only when the user really did ask for those clock times."
        ),
        "staff_name": "Filter to one staff member (name substring)",
        "group_by": "'staff' (default), 'day', or 'detail'",
    },
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectorSpec
    from app.db.engine import _ConfigSessionLocal

    code = FUNCTION_CODE_PATH.read_text(encoding="utf-8")
    db = _ConfigSessionLocal()
    try:
        spec = (
            db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == CONNECTOR)
            .first()
        )
        if not spec:
            sys.exit(f"No connector spec named {CONNECTOR}")

        tools = list(spec.tools or [])
        for i, tool in enumerate(tools):
            if tool.get("action") != ACTION:
                continue

            cfg = dict(tool.get("consolidator_config") or {})
            code_changed = cfg.get("function_code") != code
            desc_changed = tool.get("description") != DESCRIPTION
            fields_changed = any(tool.get(k) != v for k, v in FIELD_SURFACE.items())
            if not code_changed and not desc_changed and not fields_changed:
                print(f"{ACTION}: already up to date ({len(code)} chars)")
                return

            cfg["function_code"] = code
            # +1 for the resolve_dates call in front of the parallel fetch.
            cfg["max_api_calls"] = max(int(cfg.get("max_api_calls") or 0), 6)
            tool = dict(tool)
            tool["consolidator_config"] = cfg
            tool["description"] = DESCRIPTION
            tool.update(FIELD_SURFACE)
            tools[i] = tool

            what = " + ".join(
                w
                for w, c in (
                    ("function_code", code_changed),
                    ("description", desc_changed),
                    ("field surface", fields_changed),
                )
                if c
            )
            if args.dry_run:
                print(f"{ACTION}: WOULD update {what} ({len(code)} chars)")
                return

            spec.tools = tools
            spec.version = (spec.version or 0) + 1
            flag_modified(spec, "tools")
            db.commit()
            print(
                f"{ACTION}: {what} updated ({len(code)} chars), "
                f"spec version -> {spec.version}"
            )
            return

        sys.exit(f"No tool named {ACTION} on the {CONNECTOR} spec")
    finally:
        db.close()


if __name__ == "__main__":
    main()
