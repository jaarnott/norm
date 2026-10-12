"""Remove four dead `norm.*` engine tools from config (Oct 2026).

`match_stock_items`, `match_supplier`, `get_supplier_invoice_specs` and
`sensei_train_supplier` existed so the invoice review, when it ran inside the
consolidator sandbox, could reach these services through `call_api`. The
review has run server-side since the replica-primary refactor (Aug 2026) and
calls the services directly; nothing has called these tools since (the
match_stock_items cache was last written on 9 Aug 2026). Their handlers are
gone from app/agents/internal_tools.py — the sensei's moved to
`spec_dojo.train_supplier` — so the rows, engine_only and unclaimed by any
App, go too, with the binding capability entries that still named them.

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_remove_dead_invoice_tools.py --dry-run
    uv run python scripts/sync_remove_dead_invoice_tools.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts.tool_row_updates import apply_row_updates  # noqa: E402

DEAD = (
    "match_stock_items",
    "match_supplier",
    "get_supplier_invoice_specs",
    "sensei_train_supplier",
)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    apply_row_updates(
        [],
        dry_run=args.dry_run,
        label="remove-dead-invoice-tools",
        delete=tuple(("norm", a) for a in DEAD),
        also=_unclaim,
    )


def _unclaim(db, dry_run: bool):
    """Drop the names wherever config still claims them: agent bindings'
    capability lists and the marketplace apps' composition.tools. Same
    transaction as the row deletions; writes only when not a dry run."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import AgentConnectorBinding, MarketplaceApp

    dead = set(DEAD)
    lines = []
    for b in db.query(AgentConnectorBinding).filter(
        AgentConnectorBinding.connector_name == "norm"
    ):
        caps = b.capabilities or []
        kept = [
            c
            for c in caps
            if (c.get("action") if isinstance(c, dict) else c) not in dead
        ]
        if len(kept) != len(caps):
            gone = sorted(
                {(c.get("action") if isinstance(c, dict) else c) for c in caps} & dead
            )
            lines.append(f"binding {b.agent_slug}/norm: drop {', '.join(gone)}")
            if not dry_run:
                b.capabilities = kept
                flag_modified(b, "capabilities")
    names = {f"norm.{a}" for a in DEAD}
    for app in db.query(MarketplaceApp):
        comp = app.composition or {}
        tools = comp.get("tools") if isinstance(comp, dict) else None
        if isinstance(tools, list) and names & set(tools):
            lines.append(
                f"marketplace app {app.slug}: drop {sorted(names & set(tools))}"
            )
            if not dry_run:
                app.composition = {
                    **comp,
                    "tools": [x for x in tools if x not in names],
                }
                flag_modified(app, "composition")
    return lines


if __name__ == "__main__":
    main()
