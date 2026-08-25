"""Demote superseded raw tools — the second half of every consolidation.

A conversion isn't done while its raw twin is still offered to agents: the
coverage dashboard showed agents reaching PAST the consolidators to the raw
endpoints (get_sales_data: 66 calls in 30 days vs 28 for
get_sales_for_period). Demotion = the structured ``engine_only: true`` flag
(honored by prompt_builder's tool menu once deployed) + a
``[consolidator-only]`` description + the action switched off in every
agent binding's capabilities (effective immediately, no deploy).

Deliberately NOT demoted here (their agent prompts still name them — update
the prompts first): get_pos_item_sales, get_roster, get_timeclock_entries.

call_api is unaffected: consolidators keep calling raw tools regardless of
demotion — engine_only only removes them from the agent's menu.

Usage:
    uv run python scripts/sync_demote_superseded_tools.py [--dry-run]
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

#: connector → {raw action: superseding consolidator}. Kept at CURRENT
#: doctrine (the sales family's front is get_sales since 24 Aug 2026), so
#: a replay never writes a retired name into a description.
DEMOTIONS: dict[str, dict[str, str]] = {
    "loadedhub": {
        "get_sales_data": "get_sales",
        "get_cogs_detail": "get_cogs_detail_for_period",
        "get_completed_stocktakes": "get_completed_stocktakes_for_period",
        "get_pos_discounts": "get_sales",
        "get_pos_orders": "get_pos_orders_for_period",
        "get_roster_vs_actual": "get_roster_vs_actual_for_period",
        "get_staff_orders": "get_sales",
        "get_staff_item_orders": "get_sales",
    },
}


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import AgentConnectorBinding, ConnectorSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    changed: list[str] = []
    try:
        for connector, actions in DEMOTIONS.items():
            spec = (
                db.query(ConnectorSpec)
                .filter(ConnectorSpec.connector_name == connector)
                .first()
            )
            if not spec:
                print(f"skip: no spec for {connector}")
                continue
            tools = [dict(t) for t in (spec.tools or [])]
            for t in tools:
                action = t.get("action")
                target = actions.get(str(action))
                if not target or t.get("engine_only"):
                    continue
                desc = str(t.get("description") or "")
                if not desc.startswith("[consolidator-only]"):
                    desc = (
                        f"[consolidator-only] Superseded by {target} — use "
                        f"{target}. " + desc
                    )
                t["engine_only"] = True
                t["description"] = desc
                changed.append(f"{connector}.{action} → engine_only")
            if not dry_run:
                spec.tools = tools
                flag_modified(spec, "tools")

            for b in (
                db.query(AgentConnectorBinding)
                .filter(
                    AgentConnectorBinding.connector_name == connector,
                    AgentConnectorBinding.enabled == True,  # noqa: E712
                )
                .all()
            ):
                caps = [dict(c) for c in (b.capabilities or [])]
                touched = False
                for cap in caps:
                    if cap.get("action") in actions and cap.get("enabled", True):
                        cap["enabled"] = False
                        touched = True
                        changed.append(
                            f"binding {b.agent_slug}.{connector}: "
                            f"{cap.get('action')} disabled"
                        )
                if touched and not dry_run:
                    b.capabilities = caps
                    flag_modified(b, "capabilities")
        if not changed:
            print("Already in sync.")
            return
        if dry_run:
            print("Would apply:")
        else:
            db.commit()
            print("Applied:")
        for line in changed:
            print("  " + line)
    finally:
        db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
