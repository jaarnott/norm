"""The per-answer caps review (3 Oct 2026): four tool rows, one install.

Decided with the user: 40k for every raised cap except get_stock at 80k, and
where an answer was too big the fix is its shape, not a card. The full
installers for these tools do far more than their one row — on the split spec
`sync_invoice_receiving_config` would re-add four raw endpoints as LLM-facing
tools, and `sync_received_items_config` refuses to run — so this is a
RowUpdate per tool: the exact keys to set, the consolidator's code and shapes,
and nothing else on the live row touched.

- loadedhub.get_received_items_for_period — modes (item / history /
  item_supplier / group / super_group) with purpose-fit rows, history
  requiring a filter, description leading with the real uses, limit typed,
  cap 100k → 40k.
- loadedhub.reconcile_received_invoices — a cause's sentence said once per
  group, generic comparisons compacted, cap 100k → 40k.
- loadedhub.get_recipes — cap 80k → 40k.
- loadedhub.get_stock — the whole-catalogue scan degrades to a group
  breakdown when the lean list will not fit; cap stays 80k.

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_answer_shapes.py --dry-run
    uv run python scripts/sync_answer_shapes.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts.tool_row_updates import (  # noqa: E402
    RowUpdate,
    apply_row_updates,
    code_of,
    load_script,
    shapes_of,
)

ROW_KEYS = (
    "description",
    "optional_fields",
    "field_descriptions",
    "field_schema",
    "max_result_chars",
)


def updates() -> list[RowUpdate]:
    received = load_script("sync_received_items_config").TOOL
    recipes = load_script("sync_recipe_consolidator").READ_TOOL
    stock = load_script("sync_stock_config").build_tool()
    return [
        RowUpdate(
            "loadedhub",
            "get_received_items_for_period",
            {k: received[k] for k in ROW_KEYS if k in received},
            code=code_of("received_items_for_period.py"),
            shapes=shapes_of("loadedhub.get_received_items_for_period"),
        ),
        RowUpdate(
            "loadedhub",
            "reconcile_received_invoices",
            {"max_result_chars": 40_000},
            code=code_of("reconcile_received_invoices.py"),
        ),
        RowUpdate(
            "loadedhub",
            "get_recipes",
            {"max_result_chars": recipes["max_result_chars"]},
        ),
        RowUpdate(
            "loadedhub",
            "get_stock",
            {k: stock[k] for k in ROW_KEYS if k in stock},
            code=stock["consolidator_config"]["function_code"],
            shapes=stock["consolidator_config"]["shapes"],
        ),
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    apply_row_updates(updates(), dry_run=args.dry_run, label="answer-shapes")


if __name__ == "__main__":
    main()
