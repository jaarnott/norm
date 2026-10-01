"""Delete `loadedhub.update_stock_item` — superseded by manage_stock_item (1 Oct 2026).

manage_stock_item (op 'update') replaced it on the agent surface on 26 Sep 2026
(f42e4c1) and fixed the two production defects it had: its description told
the model to send `minimumStockOnHand` (Loaded's field is
`minimumStockOnHandQuantity`), and `changes` sent as a JSON string was ignored
— both 27 Aug updates to CHILLI POWDER MILD reported "nothing written". Since
then it had no App claim, no caller (no consolidator, chart, app, task,
binding or MCP capability names it) and no production calls.

This removes the tool row (a JSON backup of it is written first) and rewrites
the two endpoint descriptions that still named it.

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_delete_update_stock_item.py --dry-run
    uv run python scripts/sync_delete_update_stock_item.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

BACKUP_DIR = pathlib.Path.home() / "norm-split-backups"
ACTION = "update_stock_item"

DESCRIPTIONS = {
    "get_stock_item_full": (
        "The COMPLETE stock item as Loaded stores it — every field plus its "
        "suppliers[] variants (each variant's id, supplierId, stockCode, unitId, "
        "unitCost, brandId, defaultForSupplier, description). Read by get_stock "
        "(detail 'full') and by manage_stock_item's update, which merges the "
        "changes into it and writes the whole item back via "
        "update_stock_item_raw. Read-only."
    ),
    "update_stock_item_raw": (
        "PUT the WHOLE item object back to Loaded. Called by manage_stock_item's "
        "server-side merge (op 'update'); never exposed to an LLM."
    ),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from sqlalchemy.orm.attributes import flag_modified

    from app.connectors import spec_rows
    from app.db.config_models import ConnectionSpec
    from app.db.engine import _ConfigSessionLocal
    from app.services.config_validator import validate_config

    import app.agents.internal_tools  # noqa: F401 — registers built-ins

    db = _ConfigSessionLocal()
    try:
        spec = (
            db.query(ConnectionSpec)
            .filter_by(connector_name="loadedhub")
            .with_for_update()
            .one()
        )
        if not spec_rows.is_split(spec):
            raise SystemExit(
                "loadedhub is not split — this script expects tools/endpoints"
            )
        row = spec_rows.find_tool(spec, ACTION)
        changes = []
        if row is not None:
            changes.append(f"delete tool loadedhub.{ACTION}")
        for action, text in DESCRIPTIONS.items():
            ep = spec_rows.find_endpoint(spec, action)
            if ep is not None and ep.get("description") != text:
                changes.append(f"rewrite description of endpoint loadedhub.{action}")
        for c in changes or ["nothing to do"]:
            print(("[dry-run] " if args.dry_run else "") + c)
        if args.dry_run or not changes:
            db.rollback()
            return

        if row is not None:
            BACKUP_DIR.mkdir(exist_ok=True)
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = BACKUP_DIR / f"{ACTION}-{stamp}.json"
            backup.write_text(json.dumps(row, indent=1, default=str))
            spec.tools = [t for t in spec.tools if t.get("action") != ACTION]
            flag_modified(spec, "tools")
            print(f"backup {backup}")
        spec.endpoints = [
            {**e, "description": DESCRIPTIONS[e.get("action")]}
            if e.get("action") in DESCRIPTIONS
            else e
            for e in spec.endpoints
        ]
        flag_modified(spec, "endpoints")
        spec.version = (spec.version or 0) + 1
        db.commit()
        summary = validate_config(config_db=db)
        print(
            f"validate_config: {'ok' if summary.get('ok') else summary.get('issue_count')}"
        )
        for i in (summary.get("issues") or [])[:10]:
            print(f"   {i.get('severity')}: {i.get('where')} — {i.get('problem')}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
