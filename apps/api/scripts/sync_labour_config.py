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

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

FUNCTION_CODE_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "get_labour.py"
)

TOOL = {
    "action": "get_labour",
    "method": "GET",  # read-only consolidator: auto-executes, nestable
    "description": (
        "Labour for a period given in plain English, resolved against the "
        "venue's trading day. THE labour tool — view picks the cut: "
        "'attendance' (default; rostered vs actual hours and cost with "
        "booked leave split out — leave is never counted as worked time; "
        "group_by staff/day/detail, venues accepts a list or 'all' for "
        "per-venue totals), 'roster' (the full roster, drawn as the "
        "interactive grid), 'vs_actual' (rostered vs actual per day), "
        "'timeclock' (clock-in entries), 'staff' (the staff reference "
        "list — names, roles, rates; no period needed). staff_name "
        "filters any view to one person."
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
            "The period in plain English — 'yesterday', 'last week', 'this "
            "week'. Norm resolves it against the venue's trading day. "
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
        "view": (
            "'attendance' (default) | 'roster' | 'vs_actual' | 'timeclock' | 'staff'."
        ),
        "venues": (
            "attendance view only: 'all' (every connected venue) or a list "
            "of venue names — one row of totals per venue, computed here."
        ),
        "staff_name": (
            "Filter to one person — case-insensitive substring of their "
            "first or last name."
        ),
        "group_by": (
            "attendance view: 'staff' (default, per-person totals) | 'day' "
            "(per-day totals with unrostered count) | 'detail' "
            "(shift-by-shift lists: rostered, clockins, leave)."
        ),
        "interval": "vs_actual view: bucket size d.hh:mm:ss (default daily).",
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
        "max_api_calls": 12,
        "allowed_write_actions": [],
    },
}


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectorSpec
    from app.db.engine import _ConfigSessionLocal

    tool = dict(TOOL)
    tool["consolidator_config"] = {
        **TOOL["consolidator_config"],
        "function_code": FUNCTION_CODE_PATH.read_text(encoding="utf-8"),
    }

    db = _ConfigSessionLocal()
    try:
        spec = (
            db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == "loadedhub")
            .first()
        )
        if not spec:
            raise SystemExit("loadedhub ConnectorSpec not found")
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
