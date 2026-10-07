"""Install `loadedhub.get_labour` — THE labour domain tool.

Phase 2 of the domain-tools arc (25 Aug 2026). One tool answers every
read-side labour question; it absorbs five retiring tools
(get_roster_for_period, get_roster_vs_actual_for_period,
get_timeclock_entries_for_period, get_staff_attendance — whose leave-split
engine lives on as the default view — and get_staff_members, demoted to
the engine backend of the staff view).

ORDER: run AFTER deploying the API — the view-gated roster-card mapping
(ui_apps.TOOL_COMPONENT_VIEW), the show_roster replay acceptance, and the
rebuilt roster artifact bundle ship in the same push. The retiring tools
are removed by sync_labour_domain_rollout.py; run this first so the
replacement exists before anything is taken away.

Usage:
    uv run python scripts/sync_labour_config.py [--dry-run]
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
    / "get_labour.py"
)
SHAPES_PATH = FUNCTION_CODE_PATH.parent / "shapes.json"

TOOL = {
    "action": "get_labour",
    "method": "GET",  # read-only consolidator: auto-executes, nestable
    # Tightened 7 Oct 2026: each rule stated once (the tool list is ~65% of
    # every prompt).
    "description": (
        "Labour for a period in plain English, on the venue's trading day — THE labour "
        "tool. view: 'attendance' (default; rostered vs actual hours and cost, booked "
        "leave split out and never counted as worked; group_by staff/day/detail; "
        "venues takes a list or 'all') | 'roster' (the full roster, drawn as the grid) "
        "| 'vs_actual' (rostered vs actual per day, venue totals) | 'timeclock' "
        "(clock-ins; worked and leave hours summed separately) | 'staff' (names, "
        "roles, rates; no period). staff_name narrows every view but vs_actual. Ask "
        "ONCE for the whole period per venue. On attendance, flag no-shows (rostered, "
        "never clocked in), unrostered clock-ins, and hours more than 20% over roster."
    ),
    "required_fields": [],
    "optional_fields": [
        "period",
        "start",
        "end",
        "confirmed_by_user",
        "view",
        "venues",
        "staff_name",
        "group_by",
        "interval",
    ],
    "field_descriptions": {
        "period": (
            "Plain English — 'yesterday', 'last week', 'this week'. Prefer it to "
            "start/end; never work out dates yourself."
        ),
        "start": "Only for exact clock times the user asked for: ISO 8601 with offset.",
        "end": "Window end; same rule as start.",
        "confirmed_by_user": (
            "Only when the user really asked for a start/end that isn't a trading day."
        ),
        "view": (
            "'attendance' (default) | 'roster' | 'vs_actual' | 'timeclock' | 'staff'."
        ),
        "venues": (
            "attendance only: 'all' or a list of venue names — one row of totals per "
            "venue."
        ),
        "staff_name": (
            "One person — part of their first or last name, any case. Not for "
            "vs_actual."
        ),
        "group_by": (
            "attendance: 'staff' (default) | 'day' (with unrostered count) | 'detail' "
            "(shift by shift: rostered, clock-ins, leave)."
        ),
        "interval": "vs_actual: bucket d.hh:mm:ss (default daily).",
    },
    "field_schema": {
        "venues": {"description": "'all' or a list of venue names"},
    },
    "max_result_chars": 60_000,
    "read_only": True,
    "consolidator_config": {
        # function_code injected at sync time. Budget: resolve(1) +
        # list_venues(1) + 2 calls per venue (roster + timeclock) — four
        # venues fit with headroom.
        "max_api_calls": 20,  # 2 per venue for venues='all' (see get_labour.py)
        "allowed_write_actions": [],
    },
}


def build_tool() -> dict:
    """The row as installed: TOOL plus its code and its shapes (the response
    shaping that moved into the tool in Sep 2026 — replacing the row without
    them would quietly undo that)."""
    tool = dict(TOOL)
    tool["consolidator_config"] = {
        **TOOL["consolidator_config"],
        "function_code": FUNCTION_CODE_PATH.read_text(encoding="utf-8"),
        "shapes": json.loads(SHAPES_PATH.read_text(encoding="utf-8"))[
            "loadedhub.get_labour"
        ],
    }
    return tool


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectionSpec
    from app.db.engine import _ConfigSessionLocal

    tool = build_tool()

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
            print("get_labour: already up to date")
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
            print(f"DRY RUN — would have {what} get_labour")
            return
        spec.tools = tools
        flag_modified(spec, "tools")
        spec.version = (spec.version or 0) + 1
        db.commit()
        print(f"get_labour {what}, spec version -> {spec.version}")
    finally:
        db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
