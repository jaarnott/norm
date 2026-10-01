"""Delete `loadedhub.get_pos_orders_for_period` — folded into get_sales (1 Oct 2026).

get_sales took a `measure` ('sales' | 'orders') and sub-day intervals, so the
order count, items and average order come from the same sales feed as the
money — one tool, with the difference spelled out in its description. The
wrapper had no App claim, no agent or MCP call in 90 days, and a disabled
capability entry on the procurement binding; seven production dashboard
charts and two template charts read it.

ORDER (each step reversible):
  1. scripts/sync_sales_config.py           — get_sales with measure/tax + the
                                               get_sales_tax_rates endpoint
  2. scripts/migrate_charts_to_tools.py     — charts per environment
     (--main-url …) and --templates          (dry-run first, --apply backs up)
  3. this script                            — refuses while get_sales lacks
                                               `measure` or a template still
                                               names the wrapper

It removes the tool row (a JSON backup first), its McpCapability row if any,
its binding capability entries, and rewrites the get_pos_orders endpoint
description that still pointed at it. The endpoint stays: Loaded's orders feed
(order value BEFORE discounts, timed by when each order was opened) is a
building block a future tool may want.

Usage (from apps/api, .env loaded):
    uv run python scripts/sync_fold_pos_orders_into_sales.py --dry-run
    uv run python scripts/sync_fold_pos_orders_into_sales.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

BACKUP_DIR = pathlib.Path.home() / "norm-split-backups"
ACTION = "get_pos_orders_for_period"

GET_POS_ORDERS_DESCRIPTION = (
    "Loaded's POS orders by interval: `count` and `amount` — the order value "
    "BEFORE discounts — timed by when each order was opened (the sales feed "
    "times a bill by when it was paid). No tool reads it since "
    "get_pos_orders_for_period folded into get_sales (measure 'orders', 1 Oct "
    "2026), which counts orders from the sales feed instead."
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from sqlalchemy.orm.attributes import flag_modified

    from app.connectors import spec_rows
    from app.db.config_models import (
        AgentConnectionBinding,
        ConnectionSpec,
        DashboardTemplate,
        McpCapability,
    )
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
            raise SystemExit("loadedhub is not split — expects tools/endpoints")
        sales = spec_rows.find_tool(spec, "get_sales") or {}
        if "measure" not in (sales.get("optional_fields") or []):
            raise SystemExit(
                "get_sales has no `measure` yet — run sync_sales_config.py first"
            )
        still = [
            f"{t.slug}: {c.get('title')}"
            for t in db.query(DashboardTemplate).all()
            for c in t.charts or []
            if (c.get("script") or {}).get("action") == ACTION
        ]
        if still:
            raise SystemExit(
                "templates still use it — run migrate_charts_to_tools.py "
                "--templates --apply first: " + ", ".join(still)
            )

        changes = []
        row = spec_rows.find_tool(spec, ACTION)
        if row is not None:
            changes.append(f"delete tool loadedhub.{ACTION}")
        ep = spec_rows.find_endpoint(spec, "get_pos_orders")
        if ep is not None and ep.get("description") != GET_POS_ORDERS_DESCRIPTION:
            changes.append("rewrite description of endpoint loadedhub.get_pos_orders")
        cap = (
            db.query(McpCapability)
            .filter(
                McpCapability.kind == "connector",
                McpCapability.target == "loadedhub",
                McpCapability.action == ACTION,
            )
            .first()
        )
        if cap is not None:
            changes.append("delete its McpCapability row")
        bindings = [
            b
            for b in db.query(AgentConnectionBinding)
            .filter(AgentConnectionBinding.connector_name == "loadedhub")
            .all()
            if any(c.get("action") == ACTION for c in b.capabilities or [])
        ]
        for b in bindings:
            changes.append(f"drop it from the {b.agent_slug} binding's capabilities")
        for c in changes or ["nothing to do"]:
            print(("[dry-run] " if args.dry_run else "") + c)
        if args.dry_run or not changes:
            db.rollback()
            return

        BACKUP_DIR.mkdir(exist_ok=True)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = BACKUP_DIR / f"{ACTION}-{stamp}.json"
        backup.write_text(
            json.dumps(
                {
                    "tool": row,
                    "get_pos_orders_description": (ep or {}).get("description"),
                    "bindings": {
                        b.agent_slug: [
                            c for c in b.capabilities if c.get("action") == ACTION
                        ]
                        for b in bindings
                    },
                    "mcp_capability": (
                        {
                            k: getattr(cap, k)
                            for k in ("kind", "target", "action", "enabled")
                            if hasattr(cap, k)
                        }
                        if cap is not None
                        else None
                    ),
                },
                indent=1,
                default=str,
            )
        )
        print(f"backup {backup}")

        if row is not None:
            spec.tools = [t for t in spec.tools if t.get("action") != ACTION]
            flag_modified(spec, "tools")
        spec.endpoints = [
            {**e, "description": GET_POS_ORDERS_DESCRIPTION}
            if e.get("action") == "get_pos_orders"
            else e
            for e in spec.endpoints
        ]
        flag_modified(spec, "endpoints")
        spec.version = (spec.version or 0) + 1
        if cap is not None:
            db.delete(cap)
        for b in bindings:
            b.capabilities = [c for c in b.capabilities if c.get("action") != ACTION]
            flag_modified(b, "capabilities")
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
