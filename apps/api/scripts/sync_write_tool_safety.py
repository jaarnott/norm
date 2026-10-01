"""Stage 1 of the 1 Oct 2026 consolidator review: the three write tools.

None had been called in production yet; each would have done damage on first
use (details in each file's header):

- loadedhub.manage_stock_item — creates now default to GST (Loaded filed the
  Aug 2026 creates as Exempt); unknown variant fields are skipped, not counted
  as written; unit/ratio and one-default-per-supplier rules are enforced; an
  update is read back.
- loadedhub.manage_menu — `section` really disambiguates a dish; a bad price
  is refused, never saved as $0; delete needs the menu's name.
- cook_brothers_app.record_recipe — Orbit's input schema on the row; lines
  checked first; an ingredient edit must say merge or replace (Orbit's default
  replace deleted every line not sent).

Only these keys of each live row change: consolidator_config.function_code,
description, optional_fields, field_descriptions, field_schema — taken from
each tool's own installer definition and its config/consolidators file.
Everything else on the row (method, approval gate, budgets, added_at) is
kept. The full installers do much more (clone endpoints, rebind agents) and
are not re-run for this. A JSON backup of the three rows is written first.

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_write_tool_safety.py --dry-run
    uv run python scripts/sync_write_tool_safety.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import pathlib
import sys

API = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API))

BACKUP_DIR = pathlib.Path.home() / "norm-split-backups"
KEYS = ("description", "optional_fields", "field_descriptions", "field_schema")


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, API / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def wanted(specs: dict) -> list[tuple[str, str, dict]]:
    """(connector, action, definition-with-function_code) for each tool."""
    from app.connectors import spec_rows

    code = API / "config" / "consolidators"
    stock = dict(_script("sync_manage_stock_item_config").TOOL)
    stock["consolidator_config"] = {
        "function_code": (code / "manage_stock_item.py").read_text()
    }
    menu = dict(_script("sync_menus_config").WRITE_TOOL)
    menu["consolidator_config"] = {
        "function_code": (code / "manage_menu.py").read_text()
    }
    orbit = _script("sync_orbit_recipe_wrapper")
    endpoint = spec_rows.find_endpoint(specs["cook_brothers_app"], orbit.ENDPOINT)
    if endpoint is None:
        raise SystemExit("cook_brothers_app.kitchen_record_recipe endpoint not found")
    recipe = orbit.tool_row(endpoint)
    return [
        ("loadedhub", "manage_stock_item", stock),
        ("loadedhub", "manage_menu", menu),
        ("cook_brothers_app", "record_recipe", recipe),
    ]


def merged(live: dict, want: dict) -> dict:
    row = dict(live)
    for key in KEYS:
        if key in want:
            row[key] = want[key]
    row["consolidator_config"] = {
        **(live.get("consolidator_config") or {}),
        "function_code": want["consolidator_config"]["function_code"],
    }
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from sqlalchemy.orm.attributes import flag_modified

    from app.connectors import spec_rows
    from app.db.config_models import ConnectionSpec
    from app.db.engine import _ConfigSessionLocal
    from app.services.config_validator import validate_config

    import app.agents.internal_tools  # noqa: F401 — registers built-ins

    db = _ConfigSessionLocal()
    try:
        specs = {
            s.connector_name: s
            for s in db.query(ConnectionSpec)
            .filter(
                ConnectionSpec.connector_name.in_(["loadedhub", "cook_brothers_app"])
            )
            .with_for_update()
            .all()
        }
        plan, backup = [], {}
        for connector, action, want in wanted(specs):
            spec = specs[connector]
            live = spec_rows.find_tool(spec, action)
            if live is None:
                raise SystemExit(f"{connector}.{action} is not a tool row")
            row = merged(live, want)
            diff = [
                k for k in (*KEYS, "consolidator_config") if row.get(k) != live.get(k)
            ]
            if diff:
                plan.append((spec, action, row))
                backup[f"{connector}.{action}"] = live
            print(
                ("[dry-run] " if args.dry_run else "")
                + f"{connector}.{action}: "
                + (("update " + ", ".join(diff)) if diff else "up to date")
            )
        if args.dry_run or not plan:
            db.rollback()
            return

        BACKUP_DIR.mkdir(exist_ok=True)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = BACKUP_DIR / f"write-tool-safety-{stamp}.json"
        path.write_text(json.dumps(backup, indent=1, default=str))
        print(f"backup {path}")
        for spec, action, row in plan:
            spec.tools = [row if t.get("action") == action else t for t in spec.tools]
            flag_modified(spec, "tools")
        for spec in {id(s): s for s, _, _ in plan}.values():
            spec.version = (spec.version or 0) + 1
        db.commit()
        summary = validate_config(config_db=db)
        print(
            f"validate_config: {'ok' if summary.get('ok') else summary.get('issue_count')}"
        )
        for i in (summary.get("issues") or [])[:10]:
            print(f"   {i.get('severity')}: {i.get('where')} — {i.get('problem')}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
