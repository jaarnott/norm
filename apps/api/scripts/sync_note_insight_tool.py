"""Install ``norm.note_insight`` in config: the tool row and Norm Core's claim.

Home's insight suggestions (Oct 2026, docs/plans/home-suggested-prompts.md):
when a conversation began from an insight and the person explains it ("First
Table is our promo until the end of November") or decides to act ("we'll cut
the Tuesday roster"), Norm records it with this tool, and the daily insights
take it into account. It writes only Norm's own data, so — like
``norm.remember`` — it runs without asking (sync_approval_labels.WRITES).

Two config edits, in one transaction, backed up first:
- the ``note_insight`` tool row on the ``norm`` spec;
- Norm Core claims ``norm.note_insight`` (exposure is the App Map: a tool
  reaches the agent only when an App claims it). sync_marketplace_catalog.py's
  seed lists it too, so a full catalog sync keeps it.

**Run AFTER the code that has the handler is deployed** — a claimed tool with
no handler fails when called. The config DB is shared by every environment.

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_note_insight_tool.py --dry-run
    uv run python scripts/sync_note_insight_tool.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts.sync_approval_labels import WRITES  # noqa: E402
from scripts.tool_row_updates import BACKUP_DIR, apply_row_updates  # noqa: E402

ACTION = "note_insight"
KEY = f"norm.{ACTION}"
APP = "norm-core"

ROW = {
    "action": ACTION,
    "method": "POST",
    "read_only": False,
    "description": (
        "Record what the person said about the insight this conversation "
        "began from, so Norm's daily insights take it into account. Use it "
        "when they EXPLAIN the insight — it is planned or known (a promotion, "
        "a minimum crew on quiet days, a one-off event, a supplier deal) — "
        "with verdict 'expected'; when they DECIDE TO ACT on it (cut a "
        "roster, switch supplier) with verdict 'acting'; or when they say it "
        "isn't useful to them with verdict 'not_useful'. Only what they "
        "actually said, once per thing they said; tell them in one line what "
        "Norm will do with it."
    ),
    "required_fields": ["verdict"],
    "optional_fields": ["reason", "until", "insight_id"],
    "field_descriptions": {
        "verdict": "expected | acting | not_useful",
        "reason": "Their words, short: 'First Table promo', 'minimum crew of 3 on Tuesdays'.",
        "until": "When it stops being expected (a promo's end date), YYYY-MM-DD. Leave out if they didn't say.",
        "insight_id": "Only when noting a different insight than the one this conversation began from.",
    },
    "field_schema": {
        "verdict": {"type": "string", "enum": ["expected", "acting", "not_useful"]},
        "reason": {"type": "string"},
        "until": {"type": "string", "description": "YYYY-MM-DD"},
        "insight_id": {"type": "string"},
    },
    "effect": "write",
    "approval": WRITES[KEY],
}


def also(db, dry_run: bool) -> list[str]:
    import datetime as dt
    import json

    from sqlalchemy.orm.attributes import flag_modified

    from app.connectors import spec_rows
    from app.db.config_models import ConnectionSpec, MarketplaceApp

    lines = []
    norm = (
        db.query(ConnectionSpec)
        .filter(ConnectionSpec.connector_name == "norm")
        .with_for_update()
        .one()
    )
    app = (
        db.query(MarketplaceApp)
        .filter(MarketplaceApp.slug == APP)
        .with_for_update()
        .one()
    )
    backup = {"norm_tools": norm.tools, "app": app.composition}
    if spec_rows.find_tool(norm, ACTION) is None:
        lines.append(f"{KEY}: add tool row")
        if not dry_run:
            norm.tools = [*(norm.tools or []), ROW]
            flag_modified(norm, "tools")
            norm.version = (norm.version or 0) + 1
    comp = dict(app.composition or {})
    tools = list(comp.get("tools") or [])
    if KEY not in tools:
        lines.append(f"app {APP}: claim {KEY}")
        if not dry_run:
            comp["tools"] = [*tools, KEY]
            app.composition = comp
            flag_modified(app, "composition")
    if lines and not dry_run:
        BACKUP_DIR.mkdir(exist_ok=True)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = BACKUP_DIR / f"note-insight-config-{stamp}.json"
        path.write_text(json.dumps(backup, indent=1, default=str))
        print(f"backup {path}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    apply_row_updates([], dry_run=args.dry_run, label="note-insight", also=also)


if __name__ == "__main__":
    main()
