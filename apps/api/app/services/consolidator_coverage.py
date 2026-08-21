"""The consolidator-migration dashboard — derived, never maintained.

The strategy (Aug 2026): move agent-facing tools to fewer, higher-value
consolidators. This module derives the whole state of that migration from
what the system already knows, so there is no checklist to drift:

- the config DB's connector specs say what each action IS
  (consolidator / demoted backend / raw),
- the agent bindings and MCP capability rows say what is actually EXPOSED,
- ``tool_calls`` says what actually gets USED (ranking the backlog by value),
- and ``config/consolidators/*.py`` says what the canonical source is
  (drift check — a hand-edited config row must not silently diverge from
  the reviewed, tested file).

A tool's lifecycle: raw → consolidator exists (its raw twin is now a LEAK if
still exposed) → raw twin demoted (``engine_only: true`` + out of every
binding) → done. "Done" is a fact about the config DB, never a checkbox.
"""

from __future__ import annotations

import logging
import pathlib
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_CONSOLIDATORS_DIR = (
    pathlib.Path(__file__).resolve().parent.parent.parent / "config" / "consolidators"
)

#: Raw action → the consolidator that supersedes it, where the name alone
#: doesn't say so. The ``<action>_for_period`` convention is checked
#: automatically; this map carries the exceptions.
_SUPERSEDES: dict[str, dict[str, str]] = {
    "loadedhub": {
        "get_sales_data": "get_sales_for_period",
        "get_pos_orders": "get_pos_orders_for_period",
        "get_received_invoices": "get_received_items_for_period",
        "get_roster": "get_roster_for_period",
        "get_roster_vs_actual": "get_roster_vs_actual_for_period",
        "get_timeclock_entries": "get_timeclock_entries_for_period",
        "get_cogs_detail": "get_cogs_detail_for_period",
        "get_completed_stocktakes": "get_completed_stocktakes_for_period",
        "get_pos_item_sales": "get_pos_item_sales_for_period",
        "get_staff_orders": "get_staff_orders_for_period",
        "get_staff_item_orders": "get_staff_item_orders_for_period",
        "get_pos_discounts": "get_pos_discounts_for_period",
        "get_all_recipes": "get_recipes",
        "get_recipe_details": "get_recipes",
    },
}

_BACKEND_MARKERS = ("[consolidator-only]", "[engine-only]")


def _classify(tool: dict) -> str:
    cfg = tool.get("consolidator_config")
    if isinstance(cfg, dict) and cfg.get("function_code"):
        return "consolidator"
    desc = str(tool.get("description") or "")
    if tool.get("engine_only") or desc.startswith(_BACKEND_MARKERS):
        return "backend"
    return "raw"


def _canonical_files() -> dict[str, str]:
    """action-guess → file source, from config/consolidators/*.py.

    A file matches an action by stem (``get_budgets.py`` → ``get_budgets``)
    or with a ``get_`` prefix (``staff_attendance.py`` →
    ``get_staff_attendance``).
    """
    out: dict[str, str] = {}
    try:
        for f in sorted(_CONSOLIDATORS_DIR.glob("*.py")):
            src = f.read_text(encoding="utf-8")
            out[f.stem] = src
            out.setdefault(f"get_{f.stem}", src)
    except OSError as exc:  # pragma: no cover — image without the dir
        logger.info("canonical consolidator dir unreadable: %s", exc)
    return out


def _exposure(config_db: Session, db: Session) -> dict[tuple[str, str], dict]:
    """(connector, action) → {"agents": [slugs], "mcp": bool}.

    A binding with an EMPTY capabilities list exposes every action on its
    connector — recorded as agent slug ``<slug>*`` so the report shows that
    the exposure is implicit.
    """
    from app.db.config_models import AgentConnectorBinding, McpCapability

    out: dict[tuple[str, str], dict] = {}

    def _slot(connector: str, action: str) -> dict:
        return out.setdefault((connector, str(action)), {"agents": [], "mcp": False})

    all_of: dict[str, list[str]] = {}  # connector → slugs with empty caps
    for b in (
        config_db.query(AgentConnectorBinding)
        .filter(AgentConnectorBinding.enabled == True)  # noqa: E712
        .all()
    ):
        caps = b.capabilities or []
        if not caps:
            all_of.setdefault(b.connector_name, []).append(f"{b.agent_slug}*")
            continue
        for cap in caps:
            if isinstance(cap, dict) and cap.get("enabled", True):
                _slot(b.connector_name, cap.get("action")).setdefault("agents", [])
                _slot(b.connector_name, cap.get("action"))["agents"].append(
                    b.agent_slug
                )
    try:
        for row in (
            config_db.query(McpCapability)
            .filter(McpCapability.enabled == True)  # noqa: E712
            .all()
        ):
            if row.kind == "connector" and row.target and row.action:
                _slot(row.target, row.action)["mcp"] = True
    except Exception as exc:  # noqa: BLE001 — MCP rows are env-local, optional
        logger.info("mcp capability read failed: %s", exc)
    out["__all__"] = all_of  # type: ignore[assignment]
    return out


def _usage(db: Session, days: int) -> dict[str, int]:
    """tool_name ("connector__action") → call count over the window."""
    from sqlalchemy import func

    from app.db.models import ToolCall

    since = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        rows = (
            db.query(ToolCall.tool_name, func.count(ToolCall.id))
            .filter(ToolCall.created_at >= since)
            .group_by(ToolCall.tool_name)
            .all()
        )
        return {str(name): int(n) for name, n in rows}
    except Exception as exc:  # noqa: BLE001 — usage is enrichment
        logger.info("tool usage read failed: %s", exc)
        return {}


def coverage_report(db: Session, config_db: Session, *, days: int = 30) -> dict:
    """The migration dashboard payload. Read-only; never raises."""
    from app.db.config_models import ConnectorSpec

    canonical = _canonical_files()
    exposure = _exposure(config_db, db)
    all_of: dict[str, list[str]] = exposure.pop("__all__", {})  # type: ignore[arg-type]
    usage = _usage(db, days)

    connectors: list[dict] = []
    for spec in config_db.query(ConnectorSpec).order_by(ConnectorSpec.connector_name):
        tools = [t for t in (spec.tools or []) if isinstance(t, dict)]
        if not tools:
            continue
        actions = {str(t.get("action")) for t in tools}
        consolidator_actions = {
            str(t.get("action")) for t in tools if _classify(t) == "consolidator"
        }
        rows: list[dict] = []
        drift: list[dict] = []
        for t in tools:
            action = str(t.get("action"))
            status = _classify(t)
            exp = exposure.get((spec.connector_name, action), {})
            agents = list(exp.get("agents") or []) + all_of.get(spec.connector_name, [])
            calls = usage.get(f"{spec.connector_name}__{action}", 0)
            superseded_by = _SUPERSEDES.get(spec.connector_name, {}).get(action)
            if not superseded_by and f"{action}_for_period" in consolidator_actions:
                superseded_by = f"{action}_for_period"
            if superseded_by not in actions:
                superseded_by = None
            rows.append(
                {
                    "action": action,
                    "status": status,
                    "added_at": t.get("added_at"),
                    "calls_30d": calls,
                    "agents": sorted(set(agents)),
                    "mcp": bool(exp.get("mcp")),
                    "superseded_by": superseded_by,
                    "leak": bool(
                        status == "raw" and superseded_by and (agents or exp.get("mcp"))
                    ),
                }
            )
            if status == "consolidator":
                code = (t.get("consolidator_config") or {}).get("function_code") or ""
                src = canonical.get(action)
                if src is None:
                    drift.append({"action": action, "state": "no_canonical_file"})
                elif src != code:
                    drift.append({"action": action, "state": "differs_from_file"})
        rows.sort(key=lambda r: (-r["calls_30d"], r["action"]))
        counts = {"consolidator": 0, "backend": 0, "raw": 0}
        for r in rows:
            counts[r["status"]] += 1
        connectors.append(
            {
                "connector": spec.connector_name,
                "counts": counts,
                "leaks": [r for r in rows if r["leak"]],
                "backlog": [r for r in rows if r["status"] == "raw" and not r["leak"]],
                "drift": drift,
                "tools": rows,
            }
        )
    connectors.sort(key=lambda c: -sum(c["counts"].values()))
    totals = {"consolidator": 0, "backend": 0, "raw": 0, "leaks": 0}
    for c in connectors:
        for k in ("consolidator", "backend", "raw"):
            totals[k] += c["counts"][k]
        totals["leaks"] += len(c["leaks"])
    return {"window_days": days, "totals": totals, "connectors": connectors}
