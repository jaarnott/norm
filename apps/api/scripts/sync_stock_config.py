"""Install `loadedhub.get_stock` — THE stock read tool.

Phase 2 of the stock consolidation arc (Sep 2026). One tool answers every
read-side stock question through four views — items, on_hand, reference,
minimums — and absorbs eight tools from the agent surface: get_stock_items,
get_stock_on_hand_for_item, get_stock_on_hand, get_stock_units,
get_suppliers, get_stock_item_groups, get_stocktake_templates and
get_stock_item_minimums. Those were ~2,100 tokens on EVERY conversation's
menu (the menu is the whole entitled union since 1f8ab7a), and two of them
were broken for every call they ever received (bugs C and D of the arc).

This script installs the spec row only. Nothing can reach it until
scripts/sync_stock_domain_rollout.py binds it and flips the MCP rows — and
no loadedhub binding carries an empty (wildcard) capability list, so
installing the row exposes it to nobody. Run this first so the replacement
exists before anything is taken away.

Usage:
    uv run python scripts/sync_stock_config.py [--dry-run]
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

FUNCTION_CODE_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "get_stock.py"
)

TOOL = {
    "action": "get_stock",
    "method": "GET",  # read-only consolidator: auto-executes
    "description": (
        "THE stock read tool. view picks the cut — 'items' (default): query "
        "(name substring) returns slim {id, name} matches, or item_id returns "
        "ONE item at detail 'summary' (units, minimum, variants with codes and "
        "costs) or 'full'; always look up the ONE item you need, never scan "
        "the list. 'on_hand': stock on hand and value for one item_id (Norm "
        "finds its stocktake template) or for a whole template (template = a "
        "title, or 'Food' / 'Beverage' / 'Other Stock'; top rows by value plus "
        "an '(others)' rollup). 'reference': kind = units | suppliers | groups "
        "| templates, slim rows, filter with query. 'minimums': par levels in "
        "counting units. Before an update, fetch just the item here, then call "
        "update_stock_item with only the fields to change."
    ),
    "required_fields": [],
    "optional_fields": [
        "view",
        "item_id",
        "query",
        "detail",
        "limit",
        "template",
        "template_id",
        "as_of",
        "top",
        "sort_by",
        "kind",
        "include_deleted",
    ],
    "field_descriptions": {
        "view": "'items' (default) | 'on_hand' | 'reference' | 'minimums'.",
        "item_id": (
            "Loaded stock item id — items: exactly this item; on_hand: this "
            "item's count; minimums: this item's par level."
        ),
        "query": "Case-insensitive name substring — items, on_hand rows, reference, minimums.",
        "detail": "items: 'summary' (default) or 'full' (the complete Loaded object).",
        "limit": (
            "Max rows (items default 25, units 100). Raise it only when a task "
            "genuinely needs the whole list."
        ),
        "template": (
            "on_hand: a stocktake template title, or 'Food' / 'Beverage' / "
            "'Other Stock' for that whole category."
        ),
        "template_id": "on_hand: a template id, when you already hold one.",
        "as_of": (
            "on_hand: the snapshot date YYYY-MM-DD (default today, at the "
            "venue's own day start)."
        ),
        "top": "on_hand for a template: rows shown before the '(others)' rollup (default 50).",
        "sort_by": "on_hand for a template: 'value' (default) | 'quantity' | 'name'.",
        "kind": "reference: 'units' | 'suppliers' | 'groups' | 'templates'.",
        "include_deleted": "reference: also return deleted units / removed suppliers.",
    },
    # A full-catalogue listing (~1,000 slim {id, name} rows) must survive the
    # tool-result slimmer when explicitly requested via limit.
    "max_result_chars": 80_000,
    "read_only": True,
    "consolidator_config": {
        # function_code injected at sync time. Budget: the heaviest path is
        # on_hand by item — item + groups + templates (3), the stock-on-hand
        # report (1) and its one serial retry (1). items' name query is 4.
        "max_api_calls": 8,
        "allowed_write_actions": [],
    },
}


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectionSpec
    from app.db.engine import _ConfigSessionLocal

    tool = dict(TOOL)
    tool["consolidator_config"] = {
        **TOOL["consolidator_config"],
        "function_code": FUNCTION_CODE_PATH.read_text(encoding="utf-8"),
    }

    db = _ConfigSessionLocal()
    try:
        spec = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == "loadedhub")
            .first()
        )
        if not spec:
            raise SystemExit("loadedhub ConnectionSpec not found")
        tools = [dict(t) for t in (spec.tools or [])]
        idx = next(
            (i for i, t in enumerate(tools) if t.get("action") == tool["action"]),
            None,
        )
        if idx is not None and tools[idx] == tool:
            print("get_stock: already up to date")
            return
        if idx is None:
            tools.append(tool)
            what = "added"
        else:
            keep = tools[idx].get("added_at")
            if keep:
                tool["added_at"] = keep
            tools[idx] = tool
            what = "updated"
        if dry_run:
            print(f"DRY RUN — would have {what} get_stock")
            return
        spec.tools = tools
        flag_modified(spec, "tools")
        spec.version = (spec.version or 0) + 1
        db.commit()
        print(f"get_stock {what}, spec version -> {spec.version}")
    finally:
        db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
