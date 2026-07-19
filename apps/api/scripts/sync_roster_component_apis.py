"""Give the roster editor a real staff list and role list.

Until now `staffOptions`/`roleOptions` in RosterEditor were derived by scanning
the shifts already loaded for the week, so the roster could only ever be a
permutation of itself: a new hire, or anyone who was off last week, simply was
not in the dropdown. Same for a role nobody happened to be working.

Both lists exist upstream — `/staff-members` (the venue's whole roll, with each
person's roles and pay rate) and `/staff-roles`. The web reaches them through
`callComponentApi(component_key, action_name)`, which resolves a
ComponentApiConfig row, so they need rows of their own.

Read-only GETs; adding them changes nothing until the UI calls them.

Usage:
    .venv/bin/python scripts/sync_roster_component_apis.py --dry-run
    .venv/bin/python scripts/sync_roster_component_apis.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

COMPONENT = "roster_editor"
CONNECTOR = "loadedhub"

HEADERS = {
    "Content-Type": "application/json",
    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
}

ROWS = [
    {
        "action_name": "staff_list",
        "display_label": "Staff list",
        "method": "GET",
        # activeAsOfDate keeps leavers out without hiding anyone currently employed.
        "path_template": (
            "//loadedhub.com/api/staff-members"
            "?includeDeleted=false&includeLastClocks=false"
        ),
        "request_body_template": "",
        "headers": dict(HEADERS),
        "required_fields": [],
        "field_mapping": {},
        "field_descriptions": {},
        "ref_fields": {},
        "id_field": None,
        # Verified live against La Zeppa: 33 people, each with memberRoles[] and
        # a rate. `name` is already a single display name — there is no separate
        # first/last on this endpoint.
        "response_field_mapping": {
            "id": "id",
            "name": "name",
            "email": "email",
            "remunerationType": "remunerationType",
            "defaultMemberRoleRoleId": "defaultRoleId",
            "defaultMemberRoleRoleName": "defaultRoleName",
            "defaultMemberRoleHourlyRate": "defaultHourlyRate",
            "memberRoles": "memberRoles",
        },
        "enabled": True,
    },
    {
        "action_name": "leave_list",
        "display_label": "Leave requests",
        "method": "GET",
        "path_template": (
            "//loadedhub.com/api/time/leave-requests"
            "?from={{ from_date }}&to={{ to_date }}"
        ),
        "request_body_template": "",
        "headers": dict(HEADERS),
        "required_fields": ["from_date", "to_date"],
        "field_mapping": {"from_date": "from_date", "to_date": "to_date"},
        "field_descriptions": {
            "from_date": "YYYY-MM-DD", "to_date": "YYYY-MM-DD",
        },
        "ref_fields": {},
        "id_field": None,
        # Verified live: no leaveTypeName on the row (resolve via leave types),
        # and datetimes arrive WITHOUT an offset — they are venue-local.
        "response_field_mapping": {
            "id": "id",
            "staffMemberId": "staffMemberId",
            "leaveTypeId": "leaveTypeId",
            "startDateTime": "startDateTime",
            "endDateTime": "endDateTime",
            "status": "status",
            "reason": "reason",
        },
        "enabled": True,
    },
    {
        "action_name": "unavailability_list",
        "display_label": "Unavailability",
        "method": "GET",
        "path_template": (
            "//loadedhub.com/api/time/unavailability"
            "?from={{ from_date }}&to={{ to_date }}"
        ),
        "request_body_template": "",
        "headers": dict(HEADERS),
        "required_fields": ["from_date", "to_date"],
        "field_mapping": {"from_date": "from_date", "to_date": "to_date"},
        "field_descriptions": {
            "from_date": "YYYY-MM-DD", "to_date": "YYYY-MM-DD",
        },
        "ref_fields": {},
        "id_field": None,
        # `note` (not `reason`); `to` may be null for open-ended.
        "response_field_mapping": {
            "id": "id",
            "staffMemberId": "staffMemberId",
            "type": "type",
            "from": "from",
            "to": "to",
            "note": "note",
            "times": "times",
            "status": "status",
        },
        "enabled": True,
    },
    {
        "action_name": "roles_list",
        "display_label": "Role list",
        "method": "GET",
        "path_template": "//loadedhub.com/api/staff-roles?includeDeleted=false",
        "request_body_template": "",
        "headers": dict(HEADERS),
        "required_fields": [],
        "field_mapping": {},
        "field_descriptions": {},
        "ref_fields": {},
        "id_field": None,
        "response_field_mapping": {
            "id": "id",
            "name": "name",
            "costGroup": "costGroup",
        },
        "enabled": True,
    },
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from app.db.config_models import ComponentApiConfig
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        changed = []
        for row in ROWS:
            existing = (
                db.query(ComponentApiConfig)
                .filter(
                    ComponentApiConfig.component_key == COMPONENT,
                    ComponentApiConfig.connector_name == CONNECTOR,
                    ComponentApiConfig.action_name == row["action_name"],
                )
                .first()
            )
            if existing:
                dirty = [
                    k for k, v in row.items() if getattr(existing, k, None) != v
                ]
                if not dirty:
                    print(f"  = {row['action_name']}: already up to date")
                    continue
                changed.append(f"update {row['action_name']} ({', '.join(dirty)})")
                if not args.dry_run:
                    for k, v in row.items():
                        setattr(existing, k, v)
            else:
                changed.append(f"add {row['action_name']}")
                if not args.dry_run:
                    db.add(
                        ComponentApiConfig(
                            component_key=COMPONENT,
                            connector_name=CONNECTOR,
                            **row,
                        )
                    )

        if not changed:
            print("nothing to do")
            return
        if args.dry_run:
            print("WOULD: " + "; ".join(changed))
            return
        db.commit()
        print("applied: " + "; ".join(changed))
    finally:
        db.close()


if __name__ == "__main__":
    main()
