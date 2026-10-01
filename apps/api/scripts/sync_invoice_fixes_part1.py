"""Stage 4a of the 1 Oct 2026 consolidator review: invoices, part 1.

1. loadedhub.get_received_items_for_period
   - consumes `venue_id`: it rode into every per-venue invoices call, and the
     engine reads credentials from venue_id before venue — a venues='all' run
     from MCP, a chart or an app read every venue with the caller's login;
   - a failed catalogue read is retried once, and a query/group answer flags
     a venue whose item names stayed unavailable instead of dropping it;
   - per-venue totals follow the filters; call budget 30 → 50.
2. loadedhub.receive_loadedhub_invoice folds into review_and_receive_invoices,
   which already ran its single-invoice mode on `invoice_id`. Never called
   (0 calls since logging began 17 Jul 2026). The batch tool declares
   invoice_id; the "Receive a Supplier Invoice" playbook calls it; the old
   row, its loaded-stock App claim and its procurement binding entry go. Its
   card opened full-size; the batch card opens compact.

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_invoice_fixes_part1.py --dry-run
    uv run python scripts/sync_invoice_fixes_part1.py
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

OLD = "receive_loadedhub_invoice"
KEYS = ("description", "optional_fields", "field_descriptions", "field_schema")


def updates() -> list[RowUpdate]:
    received = load_script("sync_received_items_config").build_tool()
    invoicing = load_script("sync_invoice_receiving_config")
    batch = invoicing.CONSOLIDATOR_TOOL
    return [
        RowUpdate(
            "loadedhub",
            "get_received_items_for_period",
            {k: received[k] for k in KEYS if k in received},
            code=code_of("received_items_for_period.py"),
            config={"max_api_calls": received["consolidator_config"]["max_api_calls"]},
        ),
        RowUpdate(
            "loadedhub",
            "review_and_receive_invoices",
            {k: batch[k] for k in KEYS if k in batch},
            code=code_of("review_and_receive_invoices.py"),
        ),
    ]


def also(db, dry_run: bool) -> list[str]:
    """The old tool's claim, binding entry and playbook text."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import (
        AgentConnectionBinding,
        MarketplaceApp,
        McpCapability,
        Playbook,
    )

    lines = []
    key = f"loadedhub.{OLD}"
    for app_row in db.query(MarketplaceApp).all():
        comp = dict(app_row.composition or {})
        claimed = list(comp.get("tools") or [])
        if key in claimed:
            lines.append(f"App {app_row.slug}: drop claim {key}")
            if not dry_run:
                comp["tools"] = [k for k in claimed if k != key]
                app_row.composition = comp
                flag_modified(app_row, "composition")
    for b in db.query(AgentConnectionBinding).filter_by(connector_name="loadedhub"):
        caps = list(b.capabilities or [])
        kept = [
            c for c in caps if (c.get("action") if isinstance(c, dict) else c) != OLD
        ]
        if len(kept) != len(caps):
            lines.append(f"binding {b.agent_slug}: drop capability {OLD}")
            if not dry_run:
                b.capabilities = kept
                flag_modified(b, "capabilities")
    cap = (
        db.query(McpCapability)
        .filter_by(kind="connector", target="loadedhub", action=OLD)
        .first()
    )
    if cap is not None:
        lines.append(f"McpCapability loadedhub.{OLD}: delete")
        if not dry_run:
            db.delete(cap)
    want = load_script("sync_invoice_receiving_config").RECEIVE_ONE_PLAYBOOK
    playbook = db.query(Playbook).filter_by(slug=want["slug"]).first()
    if playbook is not None and playbook.instructions != want["instructions"]:
        lines.append(f"playbook {want['slug']}: call review_and_receive_invoices")
        if not dry_run:
            playbook.instructions = want["instructions"]
    return lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    apply_row_updates(
        updates(),
        dry_run=args.dry_run,
        label="invoice-fixes-part1",
        delete=(("loadedhub", OLD),),
        also=also,
    )


if __name__ == "__main__":
    main()
