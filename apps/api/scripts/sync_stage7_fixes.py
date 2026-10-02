"""Stage 7 of the 1 Oct 2026 consolidator review: the roster card saves, and
two dead endpoints go.

1. The chat roster card becomes editable (7d). The card now loads its week as
   a working document — the Roster page's path, with every shift field — and
   saves through the document sync. That path had never written in production
   (no shift write in tool_calls, ever), and on a real La Zeppa week it would
   have failed or done damage:
   - a delete is sent as the shift id alone. document_sync now fills the op
     from the shift as the document holds it, and the roster_editor
     delete_shift mapping here carries every field Loaded's soft-delete PUT
     needs (it mapped only breaks/rules/pay type, so the PUT would have blanked
     the shift's times and roster);
   - an added shift dropped its roster id (the add_shift mapping had no
     rosterId), so Loaded was told to start a new roster for the week;
   - update_shift refused an open shift (no staff member) and a salaried one
     (hourly rate 0) as "missing required fields", though its body already
     writes both correctly; and a null rate rendered as the text None.
   Verified by dry-run on a real week (update, delete, add) — never sent.
2. get_labour's timeclock shape keeps a break's deletedAt, so a break deleted
   in Loaded no longer shows as taken (_fmt_breaks skips deleted ones).
3. Two duplicate Loaded endpoints go: create_stock_item and
   update_variant_unit, twins of the *_raw rows manage_stock_item calls.
   Nothing calls them — no consolidator, chart, App, binding, MCP row, skill
   or prompt; last production call 27 Aug 2026, through the retired tools.

Endpoint and component rows are backed up to ~/norm-split-backups first.

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_stage7_fixes.py --dry-run
    uv run python scripts/sync_stage7_fixes.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts.tool_row_updates import (  # noqa: E402 — path set above
    BACKUP_DIR,
    RowUpdate,
    apply_row_updates,
    shapes_of,
)

DEAD_ENDPOINTS = ("create_stock_item", "update_variant_unit")

#: The shift fields a soft-delete PUT writes back — the same mapping
#: update_shift has, so a delete sends the whole shift with its stamp.
SHIFT_FIELDS = {
    "rosterId": "roster_id",
    "staffMemberId": "staff_member_id",
    "roleId": "role_id",
    "clockinTime": "clockin_time",
    "clockoutTime": "clockout_time",
    "venueId": "venue_id",
    "hourlyRate": "hourly_rate",
    "breaks": "breaks",
    "rules": "rules",
    "remunerationType": "remuneration_type",
    "datestampDeleted": "datestamp_deleted",
}

#: update_shift without staff_member_id (an open shift) and hourly_rate (0 for
#: salaried staff): the body writes a null staff member and defaults the rate.
UPDATE_SHIFT_REQUIRED = [
    "shift_id",
    "roster_id",
    "role_id",
    "venue_id",
    "clockin_time",
    "clockout_time",
]


def updates() -> list[RowUpdate]:
    return [
        RowUpdate("loadedhub", "get_labour", shapes=shapes_of("loadedhub.get_labour"))
    ]


def _component_changes(rows: dict) -> dict:
    """{action: {column: new value}} for the roster_editor mapping rows."""
    want = {}
    delete = rows.get("delete_shift")
    if delete is not None:
        cols = {
            "field_mapping": dict(SHIFT_FIELDS),
            "method": "PUT",
            "path_template": "//loadedhub.com/api/time/rostered-shifts/{{ shift_id }}",
        }
        want["delete_shift"] = {
            k: v for k, v in cols.items() if getattr(delete, k) != v
        }
    add = rows.get("add_shift")
    if add is not None:
        fm = dict(add.field_mapping or {})
        if fm.get("rosterId") != "roster_id":
            want["add_shift"] = {"field_mapping": {**fm, "rosterId": "roster_id"}}
    return {a: c for a, c in want.items() if c}


def also(db, dry_run: bool) -> list[str]:
    from sqlalchemy.orm.attributes import flag_modified

    from app.connectors import spec_rows
    from app.db.config_models import ComponentApiConfig, ConnectionSpec

    spec = db.query(ConnectionSpec).filter_by(connector_name="loadedhub").one()
    if not spec_rows.is_split(spec):
        raise SystemExit("loadedhub is not split — expects tools/endpoints")
    lines, backup = [], {}

    # -- endpoints: the dead twins, and the shift writes ---------------------
    endpoints = [dict(e) for e in spec.endpoints]
    kept = []
    for e in endpoints:
        if e.get("action") in DEAD_ENDPOINTS:
            lines.append(f"loadedhub endpoint {e['action']}: delete")
            backup[f"endpoint.{e['action']}"] = e
            continue
        new = dict(e)
        if (
            e.get("action") == "update_shift"
            and e.get("required_fields") != UPDATE_SHIFT_REQUIRED
        ):
            new["required_fields"] = list(UPDATE_SHIFT_REQUIRED)
        if e.get("action") in ("update_shift", "delete_shift"):
            body = new.get("request_body_template") or ""
            new["request_body_template"] = body.replace(
                "{{ hourly_rate | default(0) }}", "{{ hourly_rate | default(0, true) }}"
            )
        if new != e:
            diff = [k for k in new if new.get(k) != e.get(k)]
            lines.append(f"loadedhub endpoint {e['action']}: update {', '.join(diff)}")
            backup[f"endpoint.{e['action']}"] = e
        kept.append(new)

    # -- the roster card's sync mappings -------------------------------------
    rows = {
        r.action_name: r
        for r in db.query(ComponentApiConfig).filter_by(
            component_key="roster_editor", connector_name="loadedhub"
        )
    }
    changes = _component_changes(rows)
    for action, cols in changes.items():
        lines.append(f"roster_editor mapping {action}: update {', '.join(cols)}")
        backup[f"component.roster_editor.{action}"] = {
            k: getattr(rows[action], k) for k in cols
        }

    if dry_run or not lines:
        return lines
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = BACKUP_DIR / f"stage7-rows-{stamp}.json"
    path.write_text(json.dumps(backup, indent=1, default=str))
    print(f"backup {path}")
    if kept != endpoints:
        spec.endpoints = kept
        flag_modified(spec, "endpoints")
        spec.version = (spec.version or 0) + 1
    for action, cols in changes.items():
        for k, v in cols.items():
            setattr(rows[action], k, v)
            if k == "field_mapping":
                flag_modified(rows[action], "field_mapping")
    return lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    apply_row_updates(updates(), dry_run=args.dry_run, label="stage7-fixes", also=also)


if __name__ == "__main__":
    main()
