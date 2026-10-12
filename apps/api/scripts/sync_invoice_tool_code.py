"""Push the invoice consolidators' code from the repo to their live rows.

Code only (``consolidator_config.function_code``), from
config/consolidators/*.py — every other key on the live row is kept
(scripts/tool_row_updates.py). The full installer that used to do this,
scripts/sync_invoice_receiving_config.py, wrote raw endpoints into the
LLM-facing tools list and now refuses to run; this is its safe replacement
for a code change.

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_invoice_tool_code.py --dry-run
    uv run python scripts/sync_invoice_tool_code.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts.tool_row_updates import RowUpdate, apply_row_updates, code_of  # noqa: E402

TOOLS = {
    "review_and_receive_invoices": "review_and_receive_invoices.py",
    "reconcile_received_invoices": "reconcile_received_invoices.py",
    "get_invoices": "get_invoices.py",
    "get_purchase_orders": "get_purchase_orders.py",
}


def updates() -> list[RowUpdate]:
    return [
        RowUpdate("loadedhub", action, code=code_of(filename))
        for action, filename in TOOLS.items()
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    apply_row_updates(updates(), dry_run=args.dry_run, label="invoice-tool-code")


if __name__ == "__main__":
    main()
