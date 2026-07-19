"""Stop shift writes destroying breaks, rules and pay type.

core-api's shift endpoints are read-modify-write: you PUT the whole shift, so
any field you don't send is cleared. Loaded's own client does this correctly —
it fetches the current shift, mutates a copy, and writes the lot back
(`toCoreShiftRequest`, rostering/packages/api/src/functions/roster/mappers.ts).

Norm instead rebuilt the payload from scratch with blanks hardcoded:

    "breaks":[]  "rules":[]  "remunerationType":"HourlyRate"

so every drag, resize or edit **wiped that shift's breaks**, blanked `rules`
(which is where the rostered times live — see the roster app's own notes), and
rewrote salaried staff as hourly. This makes those fields pass-through, keeping
the previous behaviour only as the default when the caller sends nothing.

`datestampDeleted` is added for the same reason Loaded uses it: deletion is a
PUT that stamps it, not a DELETE.

Idempotent. Verify the rendered body with /dry-run before applying — this is a
live write path.

Usage:
    .venv/bin/python scripts/sync_roster_write_fix.py --dry-run
    .venv/bin/python scripts/sync_roster_write_fix.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

CONNECTOR = "loadedhub"

_HEADERS = {
    "Content-Type": "application/json",
    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
}

# Pass-through fields. `| tojson` renders proper JSON for arrays/objects/null,
# so an absent value becomes [] or null rather than breaking the body.
UPDATE_BODY = (
    "{"
    '"rosterId":"{{ roster_id }}",'
    '"staffMemberShowInRoster":true,'
    '"staffMemberDatestampDeleted":null,'
    '"hourlyRate":{{ hourly_rate | default(0) }},'
    '"adjustedHourlyRate":{{ hourly_rate | default(0) }},'
    '"adjustedHourlyRatePrecise":{{ hourly_rate | default(0) }},'
    '"jobs":null,'
    '"clockinTime":"{{ clockin_time }}",'
    '"clockoutTime":"{{ clockout_time }}",'
    '"datestampModified":null,'
    # --- the three that used to be blanked ---
    '"rules":{{ rules | default([], true) | tojson }},'
    '"breaks":{{ breaks | default([], true) | tojson }},'
    '"remunerationType":"{{ remuneration_type | default(\'HourlyRate\', true) }}",'
    # --- deletion is a PUT that stamps this, not a DELETE ---
    '"datestampDeleted":{{ datestamp_deleted | default(none, true) | tojson }},'
    '"type":"Roster",'
    '"posIdentifier":null,'
    # staffMemberId may be null: that is how core-api represents an OPEN shift.
    '"staffMemberId":{{ staff_member_id | default(none, true) | tojson }},'
    '"roleId":"{{ role_id }}",'
    '"isFinalised":false,'
    '"showOnFinancialReports":true,'
    '"venueId":"{{ venue_id }}",'
    '"isFromOtherCompany":false,'
    '"datestampLocked":null,'
    '"datestampPublished":null,'
    '"saving":true'
    "}"
)

CREATE_BODY = (
    "{"
    # An empty Guid tells core-api "no roster yet — resolve or create the week
    # roster from clockinTime", which is how a first shift on an empty week works.
    '"rosterId":"{{ roster_id | default(\'00000000-0000-0000-0000-000000000000\', true) }}",'
    '"roleId":"{{ role_id }}",'
    '"roleName":"{{ role_name | default(\'\', true) }}",'
    '"staffMemberId":{{ staff_member_id | default(none, true) | tojson }},'
    '"clockinTime":"{{ clockin_time }}",'
    '"clockoutTime":"{{ clockout_time }}",'
    '"breaks":{{ breaks | default([], true) | tojson }},'
    '"remunerationType":"{{ remuneration_type | default(\'HourlyRate\', true) }}",'
    '"type":"Roster",'
    '"saving":true'
    "}"
)

# New optional params, added to field_mapping/field_descriptions so callers can
# send them. They stay optional — required_fields is left alone.
_NEW_FIELDS = {
    "breaks": "The shift's existing breaks, sent back unchanged so they survive "
    "the write. Each: {id, breakStart, breakEnd, paid, deletedAt}.",
    "rules": "The shift's existing pay rules, sent back unchanged. This is where "
    "the rostered times live — omitting it blanks them.",
    "remuneration_type": "HourlyRate or Salary. Defaults to HourlyRate.",
}

# The working-document sync path builds its connector call from the
# ComponentApiConfig row's `action_name` (document_sync._get_mapping sets
# target_action = action_name). Those rows are named add_shift / delete_shift,
# but the spec's tools are create_rostered_shift / delete_rostered_shift — so
# tool_loop raised "Tool not found" and every create and delete failed at sync.
# Add aliases under the names the ops actually use.
ALIASES = [
    {
        "action": "add_shift",
        "description": (
            "Create a rostered shift. Alias of create_rostered_shift under the "
            "name the roster_editor working-document op uses — see "
            "document_sync._get_mapping."
        ),
        "method": "POST",
        "path_template": "//loadedhub.com/api/time/rostered-shifts",
        "headers": dict(_HEADERS),
        "required_fields": ["role_id", "clockin_time", "clockout_time"],
        "field_mapping": {
            k: k for k in (
                "roster_id", "role_id", "role_name", "staff_member_id",
                "clockin_time", "clockout_time", "breaks", "remuneration_type",
            )
        },
        "field_descriptions": {
            "staff_member_id": "Omit or send null to create an OPEN (unassigned) shift.",
            "roster_id": "Omit to let core-api create the week's roster.",
        },
        "request_body_template": CREATE_BODY,
        "success_status_codes": [200, 201],
        "response_ref_path": "",
        "timeout_seconds": 30,
        "response_transform": None,
    },
    {
        "action": "delete_shift",
        "description": (
            "Soft-delete a shift by stamping datestampDeleted. core-api has no "
            "DELETE for shifts — Loaded's own client does exactly this PUT."
        ),
        "method": "PUT",
        "path_template": "//loadedhub.com/api/time/rostered-shifts/{{ shift_id }}",
        "headers": dict(_HEADERS),
        "required_fields": ["shift_id", "datestamp_deleted"],
        "field_mapping": {
            k: k for k in (
                "shift_id", "roster_id", "role_id", "staff_member_id", "venue_id",
                "clockin_time", "clockout_time", "hourly_rate", "breaks", "rules",
                "remuneration_type", "datestamp_deleted",
            )
        },
        "field_descriptions": {
            "datestamp_deleted": "ISO timestamp marking the shift deleted. "
            "Required — the working document stamps it before syncing.",
        },
        "request_body_template": UPDATE_BODY,
        "success_status_codes": [200],
        "response_ref_path": "",
        "timeout_seconds": 30,
        "response_transform": None,
    },
]

PATCHES = {
    "update_shift": {
        "request_body_template": UPDATE_BODY,
        "_add_fields": {
            **_NEW_FIELDS,
            "datestamp_deleted": "Set to an ISO timestamp to soft-delete the "
            "shift; core-api has no DELETE for shifts.",
            "role_name": "Role display name, kept in step with role_id.",
        },
    },
    "create_rostered_shift": {
        "request_body_template": CREATE_BODY,
        "_add_fields": {
            **_NEW_FIELDS,
            "roster_id": "The week's roster id. Omit (or send the empty Guid) to "
            "let core-api create the roster — needed to start an empty week.",
        },
    },
}


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
        changed = []

        for i, tool in enumerate(tools):
            patch = PATCHES.get(tool.get("action"))
            if not patch:
                continue
            new = dict(tool)
            new["request_body_template"] = patch["request_body_template"]
            fm = dict(new.get("field_mapping") or {})
            fd = dict(new.get("field_descriptions") or {})
            for key, desc in patch["_add_fields"].items():
                fm.setdefault(key, key)
                fd.setdefault(key, desc)
            new["field_mapping"] = fm
            new["field_descriptions"] = fd
            if new != tool:
                tools[i] = new
                changed.append(tool["action"])

        by_action = {t.get("action"): i for i, t in enumerate(tools)}
        for alias in ALIASES:
            idx = by_action.get(alias["action"])
            if idx is not None and tools[idx] == alias:
                continue
            if idx is not None:
                tools[idx] = alias
            else:
                tools.append(alias)
            changed.append(alias["action"])

        if not changed:
            print("nothing to do")
            return
        if args.dry_run:
            print(f"WOULD update: {', '.join(changed)}")
            for a in changed:
                print(f"\n--- {a} body ---\n{PATCHES[a]['request_body_template']}")
            return

        spec.tools = tools
        spec.version = (spec.version or 0) + 1
        db.commit()
        print(f"updated {', '.join(changed)}; spec version -> {spec.version}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
