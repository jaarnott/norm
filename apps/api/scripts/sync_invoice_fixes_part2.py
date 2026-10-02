"""Stage 4b of the 1 Oct 2026 consolidator review: invoices, part 2 (config).

- reconcile_received_invoices — supplier names stay EXACT (never partial:
  "Bidfood" must not mean both Bidfood businesses); a name that matches
  nothing comes back in report.unmatched_suppliers with what it could have
  meant; a credit is no longer also a false "Total mismatch"; statements asked
  for under approve_all say none were made; the period no longer runs a day
  long; outage wording covers Norm and Loaded too.
- review_and_receive_invoices — reports the mode the service APPLIED (the
  venue's), not "unset"; no "$None" totals; the period no longer runs a day
  long; its text and playbook say receiving is the venue's setting.
- get_received_items_for_period — the same exact-name suppliers rule with
  unmatched_suppliers.
- get_invoices — 'received' says it is by invoice date; a full page of 200
  outstanding invoices says there may be more; periods end on their last day.
- get_purchase_orders — a slim list shape (raw 24-field rows were 34k chars
  for 49 orders); says it lists OPEN orders; query finds order numbers;
  deleted rows dropped.

The code half (mode cap, set_workflow_mode, the applied mode and unfetched
invoices from the review service, outages flagged transient) ships by
deploy; the tools tolerate either order.

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_invoice_fixes_part2.py --dry-run
    uv run python scripts/sync_invoice_fixes_part2.py
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
    shapes_of,
)

KEYS = ("description", "optional_fields", "field_descriptions", "field_schema")


def _fields(definition: dict) -> dict:
    return {k: definition[k] for k in KEYS if k in definition}


def updates() -> list[RowUpdate]:
    inv = load_script("sync_invoice_receiving_config")
    received = load_script("sync_received_items_config").build_tool()
    return [
        RowUpdate(
            "loadedhub",
            "review_and_receive_invoices",
            _fields(inv.CONSOLIDATOR_TOOL),
            code=code_of("review_and_receive_invoices.py"),
        ),
        RowUpdate(
            "loadedhub",
            "reconcile_received_invoices",
            _fields(inv.RECONCILE_CONSOLIDATOR_TOOL),
            code=code_of("reconcile_received_invoices.py"),
        ),
        RowUpdate(
            "loadedhub",
            "get_received_items_for_period",
            _fields(received),
            code=code_of("received_items_for_period.py"),
        ),
        RowUpdate(
            "loadedhub",
            "get_invoices",
            _fields(inv.GET_INVOICES_TOOL),
            code=code_of("get_invoices.py"),
        ),
        RowUpdate(
            "loadedhub",
            "get_purchase_orders",
            _fields(inv.GET_PURCHASE_ORDERS_TOOL),
            code=code_of("get_purchase_orders.py"),
            shapes=shapes_of("loadedhub.get_purchase_orders"),
        ),
    ]


def also(db, dry_run: bool) -> list[str]:
    """The receive and reconcile playbooks' instructions."""
    from app.db.config_models import Playbook

    inv = load_script("sync_invoice_receiving_config")
    lines = []
    for want in (inv.PLAYBOOK, inv.RECONCILE_PLAYBOOK):
        playbook = db.query(Playbook).filter_by(slug=want["slug"]).first()
        if playbook is not None and playbook.instructions != want["instructions"]:
            lines.append(f"playbook {want['slug']}: instructions")
            if not dry_run:
                playbook.instructions = want["instructions"]
    return lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    apply_row_updates(
        updates(), dry_run=args.dry_run, label="invoice-fixes-part2", also=also
    )


if __name__ == "__main__":
    main()
