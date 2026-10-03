"""Install the three write tools that now preview themselves.

Approvals stage 3 (Oct 2026, plan: ~/.claude/plans/structured-launching-cook.md).
``manage_stock_item``, ``manage_menu`` and ``record_recipe`` call
``preview(...)`` just before they write, so the approval card shows what
changes, before → after, instead of raw JSON (app/services/previews.py).

Sets each tool's ``consolidator_config.function_code`` from
config/consolidators/, and:

- ``record_recipe``: ``max_api_calls`` 1 → 2 — an edit now reads the recipe
  (``kitchen_get_recipe``) before it writes.
- ``cook_brothers_app.kitchen_get_recipe`` (endpoint): method GET and
  ``read_only`` — it is a read, and was filed as a POST write because Orbit
  discovery once classed every tool not starting "get_" as a write
  (mcp_executor.is_read_tool has since been fixed). As a write, a preview run
  would record it instead of reading it. (Not in the backup file: the old
  value is method POST, no read_only.)

**Run AFTER the stage-3 code is deployed.** The config DB is shared with
production, and code that predates ``preview`` has no such name in its
sandbox: these tools would fail there with "name 'preview' is not defined".

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_write_previews.py --dry-run
    uv run python scripts/sync_write_previews.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts.tool_row_updates import RowUpdate, apply_row_updates, code_of  # noqa: E402

READ_ENDPOINTS = (("cook_brothers_app", "kitchen_get_recipe"),)


def updates() -> list[RowUpdate]:
    return [
        RowUpdate(
            "loadedhub", "manage_stock_item", code=code_of("manage_stock_item.py")
        ),
        RowUpdate("loadedhub", "manage_menu", code=code_of("manage_menu.py")),
        RowUpdate(
            "cook_brothers_app",
            "record_recipe",
            code=code_of("record_recipe.py"),
            config={"max_api_calls": 2},
        ),
    ]


def mark_reads(db, dry_run: bool) -> list[str]:
    """Mark the endpoints the previews read as reads."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectionSpec

    lines = []
    for connector, action in READ_ENDPOINTS:
        spec = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == connector)
            .with_for_update()
            .one()
        )
        column = "endpoints" if isinstance(spec.endpoints, list) else "tools"
        rows = list(getattr(spec, column) or [])
        for i, row in enumerate(rows):
            if row.get("action") != action:
                continue
            if row.get("method") == "GET" and row.get("read_only") is True:
                print(f"{connector}.{action}: already a read")
                break
            lines.append(
                f"{connector}.{action}: method {row.get('method')} → GET, read_only"
            )
            if not dry_run:
                rows[i] = {**row, "method": "GET", "read_only": True}
                setattr(spec, column, rows)
                flag_modified(spec, column)
            break
        else:
            raise SystemExit(f"{connector}.{action} not found")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    apply_row_updates(
        updates(), dry_run=args.dry_run, label="write-previews", also=mark_reads
    )


if __name__ == "__main__":
    main()
