"""Finish the invoice-surface consolidation: demotions outside the installer.

Companion to scripts/sync_invoice_receiving_config.py (which now installs the
``get_invoices`` / ``get_purchase_orders`` consolidators, flags every raw it
owns engine_only, and prunes the three deleted duplicates). This script covers
the rest:

- Demote the CONFIG-ONLY rows no sync script owns: ``get_received_invoices``
  (the transform twin the received-items aggregation reads),
  ``get_purchase_orders_summary`` and ``get_purchase_order_detail`` (the PO
  consolidator's backends).
- Disable binding capability entries for every demoted/deleted action across
  ALL loadedhub bindings (reports and app_builder carry entries the
  procurement-focused installer never touches), enabling the consolidator
  replacements where a flip happened.
- Flip MCP capability rows: the demoted/deleted actions disappear from
  claude.ai; ``get_invoices`` and ``get_purchase_orders`` appear (same
  mcp:orders:read scope). ``get_received_items_for_period`` stays.
- Patch ``AutomatedTask.tool_filter`` rows in the APP DB this script is run
  against (tool filters are frozen snapshots of past conversations — a filter
  naming a demoted action silently loses it, so the consolidator is appended
  and dead names dropped). Run once locally and once against production
  (point DATABASE_URL at prod via the usual audit connection).

ORDER: run AFTER deploying the API (show_orders' widened replay match ships
with it) and AFTER sync_invoice_receiving_config.py.

Usage:
    uv run python scripts/sync_invoice_consolidators.py [--dry-run] [--tasks-only]
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

#: Config-only rows this script demotes (action → what supersedes it).
DEMOTE_CONFIG_ONLY = {
    "get_received_invoices": "get_invoices (kind='received') / get_received_items_for_period",
    "get_purchase_orders_summary": "get_purchase_orders",
    "get_purchase_order_detail": "get_purchase_orders (order_id)",
}

#: Every action leaving the agent/MCP surface in this arc (demoted or deleted).
RETIRED_SURFACE = [
    "list_stock_invoices",
    "get_invoice_detail",
    "get_stock_purchase_order",
    "list_purchase_orders",
    "list_supplier_statements",
    "list_received_invoices",
    "get_received_invoices",
    "get_outstanding_invoices",
    "get_received_invoices_for_period",
    "get_purchase_orders_summary",
    "get_purchase_order_detail",
    "download_invoice_file",
    "update_supplier_statement",
    "create_supplier_statement",
]

#: What replaces them on the surface.
REPLACEMENTS = ["get_invoices", "get_purchase_orders"]

#: Which replacement each retired action maps to — bindings and task filters
#: gain only the consolidators for surfaces they actually had (reports
#: carried PO reads only, so it gets get_purchase_orders, not get_invoices).
REPLACEMENT_FOR = {
    "list_stock_invoices": "get_invoices",
    "get_invoice_detail": "get_invoices",
    "list_supplier_statements": "get_invoices",
    "list_received_invoices": "get_invoices",
    "get_received_invoices": "get_invoices",
    "get_outstanding_invoices": "get_invoices",
    "get_received_invoices_for_period": "get_invoices",
    "get_stock_purchase_order": "get_purchase_orders",
    "list_purchase_orders": "get_purchase_orders",
    "get_purchase_orders_summary": "get_purchase_orders",
    "get_purchase_order_detail": "get_purchase_orders",
}

MCP_SCOPES = ["mcp:orders:read"]


def _patch_task_filters(dry_run: bool) -> list[str]:
    from app.db.engine import SessionLocal
    from app.db.models import AutomatedTask

    changed: list[str] = []
    db = SessionLocal()
    try:
        for task in db.query(AutomatedTask).all():
            tf = list(task.tool_filter or [])
            if not tf:
                continue
            dead = [a for a in tf if a in RETIRED_SURFACE]
            if not dead:
                continue
            new_tf = [a for a in tf if a not in RETIRED_SURFACE]
            needed = sorted({REPLACEMENT_FOR[a] for a in dead if a in REPLACEMENT_FOR})
            for repl in needed:
                if repl not in new_tf:
                    new_tf.append(repl)
            changed.append(
                f"task {task.id} ({getattr(task, 'name', '')!r}): "
                f"dropped {dead}, ensured {needed}"
            )
            if not dry_run:
                task.tool_filter = new_tf
        if not dry_run:
            db.commit()
    finally:
        db.close()
    return changed


def main(dry_run: bool = False, tasks_only: bool = False) -> None:
    changed: list[str] = []

    if not tasks_only:
        from sqlalchemy.orm.attributes import flag_modified

        from app.db.config_models import (
            AgentConnectorBinding,
            ConnectorSpec,
            McpCapability,
        )
        from app.db.engine import _ConfigSessionLocal

        db = _ConfigSessionLocal()
        try:
            spec = (
                db.query(ConnectorSpec)
                .filter(ConnectorSpec.connector_name == "loadedhub")
                .first()
            )
            if not spec:
                raise SystemExit("loadedhub ConnectorSpec not found")
            tools = [dict(t) for t in (spec.tools or [])]

            # 0. Widen get_stock_items_raw's transform ADDITIVELY to carry
            #    each item's group (existing callers read only id/name) — the
            #    received-items group rollups resolve item → group from it.
            for t in tools:
                if t.get("action") != "get_stock_items_raw":
                    continue
                rt = dict(t.get("response_transform") or {})
                fields = dict(rt.get("fields") or {})
                if fields.get("groupId") != "groupId" or (
                    fields.get("groupName") != "groupName"
                ):
                    fields["groupId"] = "groupId"
                    fields["groupName"] = "groupName"
                    rt["fields"] = fields
                    t["response_transform"] = rt
                    changed.append(
                        "get_stock_items_raw transform now carries groupId/groupName"
                    )

            # 1. Demote the config-only rows.
            for action, use in DEMOTE_CONFIG_ONLY.items():
                for t in tools:
                    if t.get("action") == action and not t.get("engine_only"):
                        t["engine_only"] = True
                        desc = str(t.get("description") or "")
                        if not desc.startswith("[consolidator-only]"):
                            t["description"] = (
                                f"[consolidator-only] Superseded by {use}. " + desc
                            )
                        changed.append(f"demoted {action}")
            if not dry_run:
                spec.tools = tools
                flag_modified(spec, "tools")

            # 2. Bindings: retired actions off; replacements on where a flip
            #    happened (empty-caps bindings inherit the new tools and lose
            #    the raws via engine_only, so they need no touch).
            for b in (
                db.query(AgentConnectorBinding)
                .filter(
                    AgentConnectorBinding.connector_name == "loadedhub",
                    AgentConnectorBinding.enabled == True,  # noqa: E712
                )
                .all()
            ):
                caps = [dict(c) for c in (b.capabilities or [])]
                touched = False
                needed: set[str] = set()
                for cap in caps:
                    if cap.get("action") in RETIRED_SURFACE and cap.get(
                        "enabled", True
                    ):
                        cap["enabled"] = False
                        touched = True
                        repl = REPLACEMENT_FOR.get(cap.get("action"))
                        if repl:
                            needed.add(repl)
                        changed.append(
                            f"binding {b.agent_slug}: {cap.get('action')} disabled"
                        )
                for repl in sorted(needed):
                    if not any(c.get("action") == repl for c in caps):
                        caps.append({"action": repl, "enabled": True})
                        touched = True
                        changed.append(f"binding {b.agent_slug}: {repl} enabled")
                if touched and not dry_run:
                    b.capabilities = caps
                    flag_modified(b, "capabilities")

            # 3. MCP capability rows.
            for action in RETIRED_SURFACE:
                cap = (
                    db.query(McpCapability)
                    .filter(
                        McpCapability.kind == "connector",
                        McpCapability.target == "loadedhub",
                        McpCapability.action == action,
                    )
                    .first()
                )
                if cap and cap.enabled:
                    if not dry_run:
                        cap.enabled = False
                    changed.append(f"mcp capability {action} disabled")
            for repl in REPLACEMENTS:
                cap = (
                    db.query(McpCapability)
                    .filter(
                        McpCapability.kind == "connector",
                        McpCapability.target == "loadedhub",
                        McpCapability.action == repl,
                    )
                    .first()
                )
                if not cap:
                    if not dry_run:
                        db.add(
                            McpCapability(
                                kind="connector",
                                target="loadedhub",
                                action=repl,
                                scopes=list(MCP_SCOPES),
                                enabled=True,
                            )
                        )
                    changed.append(f"mcp capability {repl} enabled")
                elif not cap.enabled:
                    if not dry_run:
                        cap.enabled = True
                    changed.append(f"mcp capability {repl} re-enabled")

            if not dry_run:
                spec.version = (spec.version or 0) + 1
                db.commit()
        finally:
            db.close()

    # 4. Automated-task tool filters (app DB — local, or prod when run with
    #    the prod DATABASE_URL).
    changed += _patch_task_filters(dry_run)

    print("DRY RUN — would apply:" if dry_run else "Applied:")
    for line in changed or ["  (nothing to do)"]:
        print(f"  {line}")


if __name__ == "__main__":
    main(
        dry_run="--dry-run" in sys.argv,
        tasks_only="--tasks-only" in sys.argv,
    )
