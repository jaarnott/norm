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

Only code, description, optional_fields, field_descriptions and field_schema
change, taken from each tool's own installer definition and its
config/consolidators file (scripts/tool_row_updates.py). A JSON backup of the
rows is written first.

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_write_tool_safety.py --dry-run
    uv run python scripts/sync_write_tool_safety.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts.tool_row_updates import (  # noqa: E402 — path set above
    RowUpdate,
    apply_row_updates,
    code_of,
    load_script,
)

KEYS = ("description", "optional_fields", "field_descriptions", "field_schema")


def _fields(definition: dict) -> dict:
    return {k: definition[k] for k in KEYS if k in definition}


def updates(specs: dict) -> list[RowUpdate]:
    from app.connectors import spec_rows

    stock = load_script("sync_manage_stock_item_config").TOOL
    menu = load_script("sync_menus_config").WRITE_TOOL
    orbit = load_script("sync_orbit_recipe_wrapper")
    endpoint = spec_rows.find_endpoint(specs["cook_brothers_app"], orbit.ENDPOINT)
    if endpoint is None:
        raise SystemExit("cook_brothers_app.kitchen_record_recipe endpoint not found")
    recipe = orbit.tool_row(endpoint)
    return [
        RowUpdate(
            "loadedhub",
            "manage_stock_item",
            _fields(stock),
            code=code_of("manage_stock_item.py"),
        ),
        RowUpdate(
            "loadedhub", "manage_menu", _fields(menu), code=code_of("manage_menu.py")
        ),
        RowUpdate(
            "cook_brothers_app",
            "record_recipe",
            _fields(recipe),
            code=recipe["consolidator_config"]["function_code"],
        ),
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    apply_row_updates(updates, dry_run=args.dry_run, label="write-tool-safety")


if __name__ == "__main__":
    main()
