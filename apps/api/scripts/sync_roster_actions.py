"""Add the roster connector actions Norm was missing.

Norm's loadedhub spec could read a roster and write shifts, but had no way to
see who is on leave, who is unavailable, what roles exist, or to publish a
roster. All four are available from core-api under the same
`loadedhub.com/api` base the existing roster tools use — the endpoints and
payload shapes below are taken from Loaded's own rostering BFF
(`loadedreports/rostering`, packages/api/src/functions/roster/), which is the
authoritative consumer of those endpoints.

Read actions are additive and safe: nothing calls them until the UI does.
The publish action is a write, so it is declared PUT and must be listed in a
consolidator's allowed_write_actions to be callable from sandboxed code.

Idempotent — safe to re-run; reports what changed.

Usage:
    .venv/bin/python scripts/sync_roster_actions.py --dry-run
    .venv/bin/python scripts/sync_roster_actions.py
"""

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CONNECTOR = "loadedhub"

_HEADERS = {
    "Content-Type": "application/json",
    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
}

_DATE_HINT = "Date in YYYY-MM-DD form (the API takes dates, not datetimes)."


def _read(action, path, description, params=None, transform=None, timeout=30):
    params = params or {}
    return {
        "action": action,
        "description": description,
        "method": "GET",
        "path_template": path,
        "headers": dict(_HEADERS),
        "required_fields": list(params),
        "field_mapping": {k: k for k in params},
        "field_descriptions": dict(params),
        "request_body_template": "",
        "success_status_codes": [200],
        "response_ref_path": "",
        "timeout_seconds": timeout,
        "response_transform": transform,
    }


TOOLS = [
    _read(
        "get_leave_requests",
        "//loadedhub.com/api/time/leave-requests?from={{ from_date }}&to={{ to_date }}",
        "Approved and pending leave for a date range. Use to check whether a "
        "staff member is on leave before rostering them. Verified live: the row "
        "carries leaveTypeId but NOT a type name — resolve it via "
        "get_leave_types. `status` includes Cancelled, so filter it. Datetimes "
        "come back WITHOUT an offset (2026-06-27T00:00:00) and are venue-local.",
        {"from_date": _DATE_HINT, "to_date": _DATE_HINT},
        transform={
            "enabled": True,
            "fields": {
                "id": "id",
                "staffMemberId": "staffMemberId",
                "leaveTypeId": "leaveTypeId",
                "startDateTime": "startDateTime",
                "endDateTime": "endDateTime",
                "status": "status",
                "reason": "reason",
                "isStartAllDay": "isStartAllDay",
                "isEndAllDay": "isEndAllDay",
            },
            "flatten": [],
            "filters": [],
        },
    ),
    _read(
        "get_leave_types",
        "//loadedhub.com/api/time/leave-types",
        "The venue's leave types (annual, sick, ...). Small reference list; "
        "use it to name a leave request whose leaveTypeName is absent.",
        transform={
            "enabled": True,
            "fields": {"id": "id", "name": "name"},
            "flatten": [],
            "filters": [],
        },
    ),
    _read(
        "get_unavailability",
        "//loadedhub.com/api/time/unavailability?from={{ from_date }}&to={{ to_date }}",
        "Staff unavailability for a date range. `type` is OneOff or Weekly — a "
        "Weekly record recurs on its named weekday within the from/to window, "
        "and its `times` carry clock times rather than full datetimes. Verified "
        "live: the free-text field is `note` (not `reason`), `to` may be null "
        "(open-ended), and `preferredHours` is a {min,max} contracted-hours "
        "range useful for over/under-hours checks.",
        {"from_date": _DATE_HINT, "to_date": _DATE_HINT},
        transform={
            "enabled": True,
            "fields": {
                "id": "id",
                "staffMemberId": "staffMemberId",
                "type": "type",
                "from": "from",
                "to": "to",
                "note": "note",
                "times": "times",
                "status": "status",
                "preferredHours": "preferredHours",
            },
            "flatten": [],
            "filters": [],
        },
    ),
    _read(
        "get_staff_roles",
        "//loadedhub.com/api/staff-roles?includeDeleted=false",
        "The venue's roles (Bar, Kitchen, ...) with their cost group. Use this "
        "rather than inferring roles from whoever happens to be rostered.",
        transform={
            "enabled": True,
            "fields": {
                "id": "id",
                "name": "name",
                "costGroup": "costGroup",
                "deptCode": "deptCode",
                "posIdentifier": "posIdentifier",
            },
            "flatten": [],
            "filters": [
                {"field": "datestampDeleted", "operator": "is_empty", "value": ""}
            ],
        },
    ),
    {
        "action": "publish_roster",
        "description": (
            "Publish a roster so staff can see it. `selected_shifts` is AllShifts "
            "or UpdatedShiftsOnly; `lock_roster` freezes it against further edits. "
            "This is a write — it notifies staff — so it must be human-approved."
        ),
        "method": "PUT",
        "path_template": "//loadedhub.com/api/time/rosters/{{ roster_id }}/publish",
        "headers": dict(_HEADERS),
        "required_fields": ["roster_id"],
        "field_mapping": {
            "roster_id": "roster_id",
            "lock_roster": "lock_roster",
            "publish_to_web": "publish_to_web",
            "additional_note": "additional_note",
            "selected_shifts": "selected_shifts",
        },
        "field_descriptions": {
            "roster_id": "The roster's id (from get_roster).",
            "lock_roster": "true to freeze the roster against further edits.",
            "publish_to_web": "true to publish to the staff-facing web view.",
            "additional_note": "Optional note included with the notification.",
            "selected_shifts": "AllShifts or UpdatedShiftsOnly.",
        },
        "request_body_template": json.dumps(
            {
                "lockRoster": "{{ lock_roster | default(false) }}",
                "publishRosterToWeb": "{{ publish_to_web | default(false) }}",
                "additionalNote": "{{ additional_note | default('') }}",
                "selectedShifts": "{{ selected_shifts | default('AllShifts') }}",
                "staffMemberRosteredShiftsInfo": [],
            }
        ),
        "success_status_codes": [200],
        "response_ref_path": "",
        "timeout_seconds": 60,
        "response_transform": None,
    },
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from app.db.config_models import ConnectorSpec
    from app.db.engine import _ConfigSessionLocal

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
        by_action = {t.get("action"): i for i, t in enumerate(tools)}
        changed = []

        for tool in TOOLS:
            action = tool["action"]
            idx = by_action.get(action)
            if idx is not None and tools[idx] == tool:
                print(f"  = {action}: already up to date")
                continue
            verb = "update" if idx is not None else "add"
            changed.append(f"{verb} {action}")
            if args.dry_run:
                print(f"  ~ {action}: WOULD {verb}")
                continue
            if idx is not None:
                tools[idx] = tool
            else:
                tools.append(tool)

        if not changed:
            print("nothing to do")
            return
        if args.dry_run:
            print(f"\n{len(changed)} change(s): {', '.join(changed)}")
            return

        spec.tools = tools
        spec.version = (spec.version or 0) + 1
        db.commit()
        print(f"\n{len(changed)} change(s) applied; spec version -> {spec.version}")
        for c in changed:
            print(f"  {c}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
