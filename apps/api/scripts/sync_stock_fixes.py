"""Stage 5 of the 1 Oct 2026 consolidator review: stock (not get_stocktakes —
the user does not want stocktake pairing).

- loadedhub.calculate_template_stock_requirements — returns
  {items, warnings, ...}: its warnings (par levels off, deliveries left out,
  received invoices unavailable, trading-day window unavailable) went to log()
  only, so a degraded forecast read like a clean one. A NEGATIVE stock count
  no longer adds to the order (HAZELNUTS −398.4 ordered 494.5 on a 96.2
  forecast) — it counts as 0 there and the line is flagged. A failed or zero
  budget is refused instead of quietly ordering par levels only. Deleted items
  are skipped; deliveries of items off the template no longer count as "no
  usable unit ratio". Dead HTTP keys on the row go.
- loadedhub.get_recipes — short ids (8-character pastes) match as a prefix or
  are refused; a failed cost lookup is reported (cost_error); one hit no longer
  fetches the cost twice; include_cost "false" is false; limit=1 no longer
  opens an arbitrary match.
- loadedhub.get_stock — a failed templates / groups / item read is a LoadedHub
  failure, not "not found" (the class of defect 0849d6e fixed in the bulk
  read); include_deleted "false" is false; limit=1 no longer opens an
  arbitrary match; the row gains a field_schema (it had none, so item_ids and
  groups reached the model as strings and a list sent as text was dropped).
- The stock_requirements playbook reads the new result: warnings first, and
  the order period passed as the user's phrase.

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_stock_fixes.py --dry-run
    uv run python scripts/sync_stock_fixes.py
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

REQUIREMENTS_DESCRIPTION = (
    "How much of a stocktake template's stock to order until a date: each "
    "item's usage over the last 4 weeks (opening + received − closing) per "
    "$1,000 of sales, scaled by the budget for the order period, and topped up "
    "to its par level. Returns `items` (orderQty per item and what drove it — "
    "usage or minimum) and `warnings`. Tell the user EVERY warning: they say "
    "what the numbers could not account for (par levels not enforced, "
    "deliveries left out of usage, negative stock counts). Refuses — rather "
    "than guess — when the period has no budget or the last 4 weeks no sales."
)

#: Leftovers of the row's HTTP-template days; nothing reads them on a
#: consolidator.
DEAD_KEYS = (
    "path_template",
    "headers",
    "field_mapping",
    "request_body_template",
    "success_status_codes",
    "response_ref_path",
    "timeout_seconds",
    "display_component",
    "display_props",
    "working_document",
    "response_transform",
)

PLAYBOOK_SLUG = "stock_requirements"
PLAYBOOK_INSTRUCTIONS = """Follow these steps exactly:

1. **Get templates**: Call `get_stock` with view 'reference' and kind 'templates' for the user's venue to see available stock areas.

2. **Ask the user**: Present the list of templates and ask the user which stock area they want to check. Wait for their response before proceeding.

3. **Get the order period**: If the user hasn't specified how far ahead they want to forecast (e.g., "next 2 weeks", "until end of month"), ask them.

4. **Calculate requirements**: Call `calculate_template_stock_requirements` with:
   - template_id: the ID of the template the user chose
   - order_until: the user's phrase for the period, passed straight through ("next Friday", "end of next week") — the tool resolves it against the venue's calendar. Use order_until_date only if the user gave an exact date.
   If it returns an error (no budget for the period, no sales, Loaded unavailable), tell the user exactly what it says and stop — never estimate the order yourself.

5. **Warnings first**: If the result's `warnings` list is not empty, state every warning above the table, in plain words. They say what the numbers could not account for — par levels not enforced, deliveries left out of usage, items with negative stock counts. Never drop one.

6. **Present results**: Show the result's `items` in a single consolidated table. Use a single markdown table with 5 columns:
   - Item Name
   - Unit
   - On Hand
   - Forecast
   - To Order
   - Category Integration:
      - Insert category headers (e.g., "Keg", "White Wine", "Bottled Beer") directly into the Item Name column as bold text
      - Leave the other columns empty for category header rows
      - List all items under each category immediately following the category header
      - This creates visual groupings while maintaining a single table structure
   An item with a `note` (e.g. a negative stock count) gets that note after its name.

7. **Offer next steps**: Ask if the user wants to create an order for the items."""


def updates() -> list[RowUpdate]:
    recipes = load_script("sync_recipe_consolidator").READ_TOOL
    stock = load_script("sync_stock_config").build_tool()
    keys = ("description", "optional_fields", "field_descriptions", "field_schema")
    return [
        RowUpdate(
            "loadedhub",
            "get_stock",
            {k: stock[k] for k in keys if k in stock},
            code=stock["consolidator_config"]["function_code"],
            shapes=stock["consolidator_config"]["shapes"],
        ),
        RowUpdate(
            "loadedhub",
            "calculate_template_stock_requirements",
            {"description": REQUIREMENTS_DESCRIPTION},
            code=code_of("calculate_template_stock_requirements.py"),
            drop=DEAD_KEYS,
        ),
        RowUpdate(
            "loadedhub",
            "get_recipes",
            {k: recipes[k] for k in keys if k in recipes},
            code=code_of("get_recipes.py"),
        ),
    ]


def also(db, dry_run: bool) -> list[str]:
    from app.db.config_models import Playbook

    playbook = db.query(Playbook).filter_by(slug=PLAYBOOK_SLUG).first()
    if playbook is None or playbook.instructions == PLAYBOOK_INSTRUCTIONS:
        return []
    if not dry_run:
        playbook.instructions = PLAYBOOK_INSTRUCTIONS
    return [f"playbook {PLAYBOOK_SLUG}: read items + warnings; order_until phrase"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    apply_row_updates(updates(), dry_run=args.dry_run, label="stock-fixes", also=also)


if __name__ == "__main__":
    main()
