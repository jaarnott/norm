"""Supplier tenders — via the Cook Brothers App (the loadedhub path is dead).

History: first shipped 29 Aug 2026 as direct loadedhub actions against
``/1.0/stock/internal/tenders``. Loaded's ``cookbrothers`` OAuth client cannot
be granted the ``stock:tenders`` scopes, so — exactly like recipe writes —
tenders go through the **Cook Brothers App**, whose stored Loaded session
carries the Stock permission and which resolves the venue from the
authenticated connection (docs/apps-marketplace-plan.md Phase 2, revised).

This script enforces the end state (the chef-seed lesson — a re-run must never
reinstall yesterday's doctrine):

  1. REMOVE the five loadedhub tender tools from the spec;
  2. REMOVE their capabilities from the procurement/loadedhub binding;
  3. REMOVE the ``supplier_tenders`` component-api rows (the page now reads
     through ``/api/supplier-tenders/*``, which bridges to the CB App —
     the component-api door is HTTP-only and cannot call an MCP connector);
  4. BIND the CB App tender tools to procurement once they are discovered
     (``POST /api/connector-specs/cook_brothers_app/sync-mcp-tools`` after the
     CB App ships them), clearing create-blocking required_fields the same way
     the recipe write binding does.

Expected CB App tools (contract in docs/apps-marketplace-plan.md):
    stock_loadedhub_tender  (action: list | get | update — one consolidated tool)

Idempotent — safe to re-run. The config DB is shared across every environment,
so committing reaches production. Dry-run first.

Usage:
    .venv/bin/python scripts/sync_tender_actions.py --dry-run
    .venv/bin/python scripts/sync_tender_actions.py
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

AGENT = "procurement"
COMPONENT = "supplier_tenders"
DEAD_LOADEDHUB_ACTIONS = {
    "get_tenders",
    "get_tender",
    "get_tender_review",
    "create_tender",
    "update_tender",
}
CB_CONNECTOR = "cook_brothers_app"
# The CB App shipped ONE consolidated tool (its house style): action list|get|
# update. It can write, so it stays a POST/approval tool for the agent — reads
# through it also pause for approval, which is the price of the consolidation;
# the PAGE reads skip that entirely via /api/supplier-tenders/*. The review is
# composed Norm-side (routers/supplier_tenders.py) from the CB tender + Loaded's
# stock-received feed, so no review action is needed CB-side.
CB_TOOL = "stock_loadedhub_tender"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import (
        AgentConnectionBinding,
        ComponentApiConfig,
        ConnectionSpec,
    )
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        changes = []

        # --- 1. strip the dead loadedhub tools -------------------------------
        lh = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == "loadedhub")
            .first()
        )
        if lh:
            tools = [
                t
                for t in (lh.tools or [])
                if t.get("action") not in DEAD_LOADEDHUB_ACTIONS
            ]
            if len(tools) != len(lh.tools or []):
                changes.append(
                    f"loadedhub spec: remove {len(lh.tools or []) - len(tools)} tender tool(s)"
                )
                if not args.dry_run:
                    lh.tools = tools
                    lh.version = (lh.version or 0) + 1
                    flag_modified(lh, "tools")

        # --- 2. strip the dead binding caps ----------------------------------
        b = (
            db.query(AgentConnectionBinding)
            .filter(
                AgentConnectionBinding.agent_slug == AGENT,
                AgentConnectionBinding.connector_name == "loadedhub",
            )
            .first()
        )
        if b:
            caps = [
                c
                for c in (b.capabilities or [])
                if c.get("action") not in DEAD_LOADEDHUB_ACTIONS
            ]
            if len(caps) != len(b.capabilities or []):
                changes.append(f"binding {AGENT}/loadedhub: remove tender caps")
                if not args.dry_run:
                    b.capabilities = caps
                    flag_modified(b, "capabilities")

        # --- 3. drop the component-api rows ----------------------------------
        rows = (
            db.query(ComponentApiConfig)
            .filter(ComponentApiConfig.component_key == COMPONENT)
            .all()
        )
        for r in rows:
            changes.append(f"component-api: delete {COMPONENT}/{r.action_name}")
            if not args.dry_run:
                db.delete(r)

        # --- 4. bind CB tender tools (once discovered) -----------------------
        cb = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == CB_CONNECTOR)
            .first()
        )
        cb_actions = (
            {t.get("action"): i for i, t in enumerate(cb.tools or [])} if cb else {}
        )
        if CB_TOOL not in cb_actions:
            changes.append(
                f"NOTE: CB App tool not discovered yet ({CB_TOOL}) — "
                "run sync-mcp-tools after the CB App ships it, then re-run this."
            )
        elif cb:
            cbb = (
                db.query(AgentConnectionBinding)
                .filter(
                    AgentConnectionBinding.agent_slug == AGENT,
                    AgentConnectionBinding.connector_name == CB_CONNECTOR,
                )
                .first()
            )
            caps = list(cbb.capabilities or []) if cbb else []
            if CB_TOOL not in {c.get("action") for c in caps}:
                caps.append(
                    {
                        "action": CB_TOOL,
                        "label": "Supplier tenders: list, read and update (approve-gated).",
                        "enabled": True,
                    }
                )
                changes.append(f"binding {AGENT}/{CB_CONNECTOR}: + {CB_TOOL}")
                if not args.dry_run:
                    if cbb is None:
                        db.add(
                            AgentConnectionBinding(
                                agent_slug=AGENT,
                                connector_name=CB_CONNECTOR,
                                capabilities=caps,
                                enabled=True,
                            )
                        )
                    else:
                        cbb.capabilities = caps
                        flag_modified(cbb, "capabilities")

        if not changes:
            print("everything up to date")
            return
        for c in changes:
            print(f"  {c}")
        if args.dry_run:
            print("(dry run — nothing written)")
            return
        db.commit()
        print("committed")
    finally:
        db.close()


if __name__ == "__main__":
    main()
