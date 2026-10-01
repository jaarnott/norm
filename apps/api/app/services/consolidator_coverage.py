"""The tools-and-endpoints dashboard — derived, never maintained.

Sep 2026 vocabulary (docs/tool-architecture-strategy.md): every connector row
is a TOOL — a consolidator (config Python over endpoints) or a built-in (Norm
code) — or an API ENDPOINT, a building block no LLM should see. This module
derives the state of that rule from what the system already knows, so there is
no checklist to drift:

- ``app/connectors/spec_rows.py`` says what each row IS,
- the App Map and the MCP capability rows say what is actually EXPOSED,
- the consolidators' own code says which endpoints each tool is built on,
- ``tool_calls`` says what actually gets USED,
- and ``config/consolidators/*.py`` says what the canonical source is (drift
  check — a hand-edited config row must not silently diverge from the
  reviewed, tested file).

A LEAK is an endpoint an LLM can still reach. "Done" is zero leaks — a fact
about the config DB, never a checkbox.
"""

from __future__ import annotations

import logging
import pathlib
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session
from app.connectors import spec_rows

logger = logging.getLogger(__name__)

_CONSOLIDATORS_DIR = (
    pathlib.Path(__file__).resolve().parent.parent.parent / "config" / "consolidators"
)

#: Raw action → the consolidator that supersedes it, where the name alone
#: doesn't say so. The ``<action>_for_period`` convention is checked
#: automatically; this map carries the exceptions.
_SUPERSEDES: dict[str, dict[str, str]] = {
    "loadedhub": {
        "get_sales_data": "get_sales",
        "get_pos_orders": "get_sales",
        "get_received_invoices": "get_received_items_for_period",
        "get_roster": "get_labour",
        "get_roster_vs_actual": "get_labour",
        "get_timeclock_entries": "get_labour",
        "get_staff_members": "get_labour",
        "get_cogs_detail": "get_cogs_detail_for_period",
        "get_pos_item_sales": "get_sales",
        "get_staff_orders": "get_sales",
        "get_staff_item_orders": "get_sales",
        "get_pos_discounts": "get_sales",
        "get_all_recipes": "get_recipes",
        "get_recipe_details": "get_recipes",
        "get_stock_item_full": "get_stock",
        "create_stock_item": "manage_stock_item",
        "list_menus": "get_menus",
        "generate_stocktake_report": "get_stocktakes",
        "get_menu": "get_menus",
        "create_menu": "manage_menu",
        "update_menu": "manage_menu",
        "delete_menu": "manage_menu",
        "update_variant_unit": "manage_stock_item",
        "get_stock_on_hand": "get_stock",
        "get_stock_units": "get_stock",
        "get_suppliers": "get_stock",
        "get_stock_item_groups": "get_stock",
        "get_stocktake_templates": "get_stock",
        "get_stock_item_minimums": "get_stock",
        "list_stock_invoices": "get_invoices",
        "get_invoice_detail": "get_invoices",
        "list_received_invoices": "get_invoices",
        "list_supplier_statements": "get_invoices",
        "get_purchase_orders_summary": "get_purchase_orders",
        "get_purchase_order_detail": "get_purchase_orders",
        "list_purchase_orders": "get_purchase_orders",
    },
    "bamboohr": {
        "get_jobs": "get_hr",
        "get_applications": "get_hr",
        "get_application_details": "get_hr",
        "get_applicant_statuses": "get_hr",
        "list_employees": "get_hr",
        "get_employee": "get_hr",
    },
}


#: Consolidators whose canonical source is a file named for a SIBLING tool
#: (many-to-one, beyond the `wraps` marker that maps every *_for_period
#: wrapper to for_period.py).
_SHARED_CANONICAL = {
    "receive_loadedhub_invoice": "review_and_receive_invoices",
}


def _canonical_files() -> dict[str, str]:
    """action-guess → file source, from config/consolidators/*.py.

    A file matches an action by stem (``get_budgets.py`` → ``get_budgets``)
    or with a ``get_`` prefix (a ``staff_attendance.py`` would match
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


def _exposed(config_db: Session) -> tuple[dict[str, str], set[tuple[str, str]]]:
    """What puts a row in front of an LLM: App Map claims (``connector.action``
    -> App slug) and enabled MCP capabilities. Legacy agent bindings no longer
    decide the menu once the App Map is armed, so they are not exposure."""
    from app.db.config_models import McpCapability
    from app.services.entitlements import tool_owners

    try:
        owners = tool_owners(config_db)
    except Exception as exc:  # noqa: BLE001 — enrichment only
        logger.info("App Map read failed: %s", exc)
        owners = {}
    mcp: set[tuple[str, str]] = set()
    try:
        for row in (
            config_db.query(McpCapability)
            .filter(McpCapability.enabled == True)  # noqa: E712
            .all()
        ):
            if row.kind == "connector" and row.target and row.action:
                mcp.add((row.target, row.action))
    except Exception as exc:  # noqa: BLE001 — MCP rows are optional
        logger.info("mcp capability read failed: %s", exc)
    return owners, mcp


def coverage_report(db: Session, config_db: Session, *, days: int = 30) -> dict:
    """The tools-and-endpoints dashboard payload. Read-only; never raises.

    Sep 2026 vocabulary (docs/tool-architecture-strategy.md): every row is a
    tool — a consolidator or a built-in — or an API endpoint. A LEAK is an
    endpoint an LLM can still reach: claimed by an App, or enabled as an MCP
    capability. An endpoint no tool calls is listed as unused.
    """
    from app.db.config_models import ConnectionSpec
    from app.services.spec_inventory import dependency_map

    canonical = _canonical_files()
    owners, mcp = _exposed(config_db)
    usage = _usage(db, days)
    specs = (
        config_db.query(ConnectionSpec).order_by(ConnectionSpec.connector_name).all()
    )
    _uses, used_by = dependency_map(specs)

    connectors: list[dict] = []
    for spec in specs:
        tools = spec_rows.rows(spec)
        if not tools:
            continue
        name = spec.connector_name
        actions = {str(t.get("action")) for t in tools}
        consolidator_actions = {
            str(t.get("action")) for t in tools if spec_rows.is_consolidator(t)
        }
        rows: list[dict] = []
        drift: list[dict] = []
        for t in tools:
            action = str(t.get("action"))
            key = f"{name}.{action}"
            build = spec_rows.build_of(name, t, execution_mode=spec.execution_mode)
            app = owners.get(key)
            in_mcp = (name, action) in mcp
            superseded_by = _SUPERSEDES.get(name, {}).get(action)
            if not superseded_by and f"{action}_for_period" in consolidator_actions:
                superseded_by = f"{action}_for_period"
            if superseded_by not in actions:
                superseded_by = None
            rows.append(
                {
                    "action": action,
                    "status": build,
                    "kind": "endpoint" if build == "endpoint" else "tool",
                    "added_at": t.get("added_at"),
                    "calls_30d": usage.get(f"{name}__{action}", 0),
                    "app": app,
                    "mcp": in_mcp,
                    "used_by": used_by.get(key, []),
                    "superseded_by": superseded_by,
                    "leak": build == "endpoint" and bool(app or in_mcp),
                }
            )
            if build == "consolidator":
                cc = t.get("consolidator_config") or {}
                code = cc.get("function_code") or ""
                # Many-to-one canonical sources: every *_for_period wrapper
                # (marked by `wraps` in its config) shares for_period.py, and
                # receive_loadedhub_invoice is the single-invoice mode of the
                # batch review's file.
                if cc.get("wraps"):
                    ckey = "for_period"
                elif action in _SHARED_CANONICAL:
                    ckey = _SHARED_CANONICAL[action]
                else:
                    ckey = action
                src = canonical.get(ckey)
                if src is None:
                    drift.append({"action": action, "state": "no_canonical_file"})
                elif src != code:
                    drift.append({"action": action, "state": "differs_from_file"})
        rows.sort(key=lambda r: (-r["calls_30d"], r["action"]))
        counts = {"consolidator": 0, "built-in": 0, "endpoint": 0}
        for r in rows:
            counts[r["status"]] += 1
        connectors.append(
            {
                "connector": name,
                "split": spec_rows.is_split(spec),
                "counts": counts,
                "leaks": [r for r in rows if r["leak"]],
                "unused": [
                    r
                    for r in rows
                    if r["kind"] == "endpoint" and not r["used_by"] and not r["leak"]
                ],
                "drift": drift,
                "tools": rows,
            }
        )
    connectors.sort(key=lambda c: -sum(c["counts"].values()))
    totals = {"consolidator": 0, "built-in": 0, "endpoint": 0, "leaks": 0}
    for c in connectors:
        for k in ("consolidator", "built-in", "endpoint"):
            totals[k] += c["counts"][k]
        totals["leaks"] += len(c["leaks"])
    return {"window_days": days, "totals": totals, "connectors": connectors}
