"""Install the tightened text of the six biggest tools (7 Oct 2026).

The tool list is about two thirds of every agent prompt, and these six
(get_sales, get_stock, get_labour, get_received_items_for_period,
manage_stock_item, manage_menu) carried the most text — the same rule often
stated in the description and again in a field, with the reasoning behind it.
Each now states every rule once; nothing the model needs was dropped.

Sets only ``description`` and ``field_descriptions``, from each tool's own
sync script (the canonical source). Code, shapes, schemas, approval gates and
everything else on the live row are kept. Text only, so it needs no deploy.

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_tool_text_tightening.py --dry-run
    uv run python scripts/sync_tool_text_tightening.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts.tool_row_updates import RowUpdate, apply_row_updates, load_script  # noqa: E402

#: (sync script, its tool definition)
SOURCES = (
    ("sync_sales_config", "TOOL"),
    ("sync_stock_config", "TOOL"),
    ("sync_labour_config", "TOOL"),
    ("sync_received_items_config", "TOOL"),
    ("sync_manage_stock_item_config", "TOOL"),
    ("sync_menus_config", "WRITE_TOOL"),
)


def updates() -> list[RowUpdate]:
    out = []
    for script, name in SOURCES:
        tool = getattr(load_script(script), name)
        out.append(
            RowUpdate(
                "loadedhub",
                tool["action"],
                fields={
                    "description": tool["description"],
                    "field_descriptions": tool["field_descriptions"],
                },
            )
        )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    apply_row_updates(updates(), dry_run=args.dry_run, label="tool-text-tightening")


if __name__ == "__main__":
    main()
