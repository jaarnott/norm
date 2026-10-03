"""Remove the dead `norm.update_thread_summary` tool row (3 Oct 2026).

It wrote automated_task.thread_summary, which nothing reads back into a
prompt — two API responses show it, for display. One call in 90 days, and a
name that invited confusion with the automatic conversation summariser (a
different field). The handler is gone from app/agents/internal_tools.py; a
row with no handler would hand the model an error, so the row goes too.

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_remove_update_thread_summary.py --dry-run
    uv run python scripts/sync_remove_update_thread_summary.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from scripts.tool_row_updates import apply_row_updates  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    apply_row_updates(
        [],
        dry_run=args.dry_run,
        label="remove-update-thread-summary",
        delete=(("norm", "update_thread_summary"),),
        also=_unclaim,
    )


def _unclaim(db, dry_run: bool):
    """Drop the name wherever config still claims it: agent bindings'
    capability lists ({action, label} entries) and the marketplace apps'
    composition.tools ("norm.update_thread_summary"). Same transaction as
    the row deletion; writes only when not a dry run."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import AgentConnectorBinding, MarketplaceApp

    lines = []
    for b in db.query(AgentConnectorBinding).filter(
        AgentConnectorBinding.connector_name == "norm"
    ):
        caps = b.capabilities or []
        kept = [
            c
            for c in caps
            if (c.get("action") if isinstance(c, dict) else c)
            != "update_thread_summary"
        ]
        if len(kept) != len(caps):
            lines.append(
                f"binding {b.agent_slug}/{b.connector_name}: drop update_thread_summary"
            )
            if not dry_run:
                b.capabilities = kept
                flag_modified(b, "capabilities")
    for app in db.query(MarketplaceApp):
        comp = app.composition or {}
        tools = comp.get("tools") if isinstance(comp, dict) else None
        if isinstance(tools, list) and "norm.update_thread_summary" in tools:
            lines.append(f"marketplace app {app.slug}: drop norm.update_thread_summary")
            if not dry_run:
                comp = {
                    **comp,
                    "tools": [x for x in tools if x != "norm.update_thread_summary"],
                }
                app.composition = comp
                flag_modified(app, "composition")
    return lines


if __name__ == "__main__":
    main()
