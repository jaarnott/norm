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

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

FUNCTION_CODE_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "get_stock.py"
)
SHAPES_PATH = FUNCTION_CODE_PATH.parent / "shapes.json"

#: Engine-only endpoints the tool reads (merged into the spec by action).
ENDPOINTS = [
    {
        # The Catalogue page's item list (view 'catalogue', 23 Oct 2026): the
        # stock API's whole list, each item with its supplier variants, raw.
        # get_stock_item_minimums is the same URL but shaped down to par levels.
        "action": "get_stock_items_detail_raw",
        "description": (
            "Every live stock item with its units and supplier variants "
            "(suppliers[]). Loaded leaves retired items out whatever the query."
        ),
        "method": "GET",
        "path_template": "//api.loadedhub.com/1.0/stock/internal/items",
        "headers": {
            "Content-Type": "application/json",
            "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
        },
        "required_fields": [],
        "field_mapping": {},
        "field_descriptions": {},
        "success_status_codes": [200],
        "timeout_seconds": 60,
        "read_only": True,
        "engine_only": True,
    },
]

TOOL = {
    "action": "get_stock",
    "method": "GET",  # read-only consolidator: auto-executes
    # Tightened 7 Oct 2026: each rule stated once (the tool list is ~65% of
    # every prompt).
    "description": (
        "THE stock read tool. view 'items' (default): query (part of a name) returns "
        "slim {id, name} matches; item_id returns ONE item ('summary': units, minimum, "
        "variants with codes and costs; or 'full'). For one edit, look up that one "
        "item. For a BULK job (a tender, a price list): call once with no query and "
        "limit 1000, match the printed names yourself, then read the matched items "
        "with item_ids (5 per call). Never search name by name — a missed search looks "
        "exactly like a missing item. 'on_hand': stock and value for an item_id or a "
        "whole template ('Food' / 'Beverage' / 'Other Stock' or a title; top rows plus "
        "'(others)'). 'reference': units | suppliers | groups | templates. 'minimums': "
        "par levels in counting units. 'catalogue' is the Catalogue page's whole "
        "list — too big for a chat answer, never use it here. Before manage_stock_item "
        "(op 'update'), fetch the item here and change only what's needed. Never "
        "guess an item id."
    ),
    "required_fields": [],
    "optional_fields": [
        "view",
        "item_id",
        "item_ids",
        "query",
        "groups",
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
        "view": (
            "'items' (default) | 'on_hand' | 'reference' | 'minimums' | 'catalogue' "
            "(the Catalogue page only)."
        ),
        "item_id": (
            "One stock item — items: that item; on_hand: its count; minimums: its par."
        ),
        "item_ids": (
            "items: a list of ids, 5 per call; the rest come back as `remaining` — "
            "fetch those together, not one by one."
        ),
        "query": "Part of a name, any case — items, on_hand rows, reference, minimums.",
        "groups": (
            "items: group names to narrow the catalogue (from reference 'groups'), "
            "when a full list comes back truncated."
        ),
        "detail": "items: 'summary' (default) or 'full'.",
        "limit": (
            "Max rows (items 25, units 100); raise only when the task needs the whole "
            "list."
        ),
        "template": (
            "on_hand: a template title, or 'Food' / 'Beverage' / 'Other Stock'."
        ),
        "template_id": "on_hand: a template id you already hold.",
        "as_of": "on_hand: date YYYY-MM-DD (default today, at the venue's day start).",
        "top": "on_hand template: rows before '(others)' (default 50).",
        "sort_by": "on_hand template: 'value' (default) | 'quantity' | 'name'.",
        "kind": "reference: 'units' | 'suppliers' | 'groups' | 'templates'.",
        "include_deleted": (
            "reference / catalogue: include deactivated units, suppliers (and, in "
            "catalogue, items)."
        ),
    },
    # A full-catalogue listing (~1,000 slim {id, name} rows) must survive the
    # tool-result slimmer when explicitly requested via limit.
    # Typed, so the model sends true/false rather than text: bool("false")
    # is True, and include_deleted used to read every "false" as yes.
    "field_schema": {
        "include_deleted": {"type": "boolean"},
        "limit": {"type": "integer"},
        "item_ids": {"type": "array", "items": {"type": "string"}},
        "groups": {"type": "array", "items": {"type": "string"}},
    },
    "max_result_chars": 80_000,
    "read_only": True,
    "effect": "read",
    "consolidator_config": {
        # function_code injected at sync time. Budget: the heaviest path is a
        # bulk read at detail 'summary' — 2 lookup lists + one call per item,
        # which _BULK_PAGE (5) sizes to, with one call spare. on_hand by item
        # is 5 (item + groups + templates, the report, one serial retry).
        #
        # DELIBERATELY STILL 8. This is a runaway guard on consolidator code,
        # NOT a connection control: concurrent DB use is bounded by
        # db_call_semaphore (DB_CALL_LIMIT = 12 per process) and each fan-out
        # worker returns its connection before the slow HTTP call
        # (release_db_after_render), so raising this would not protect the
        # database — and it could not bound fleet concurrency even in
        # principle, being per invocation. It was briefly raised to 56 and then
        # 30 for a bulk read on 1 Oct 2026; that was the wrong lever, and the
        # bulk read now pages within 8 instead.
        "max_api_calls": 8,
        "allowed_write_actions": [],
    },
}


def build_tool() -> dict:
    """The row as installed: TOOL plus its code and its shapes (the response
    shaping that moved into the tool in Sep 2026 — replacing the row without
    them would quietly undo that)."""
    tool = dict(TOOL)
    tool["consolidator_config"] = {
        **TOOL["consolidator_config"],
        "function_code": FUNCTION_CODE_PATH.read_text(encoding="utf-8"),
        "shapes": json.loads(SHAPES_PATH.read_text(encoding="utf-8"))[
            "loadedhub.get_stock"
        ],
    }
    return tool


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import ConnectionSpec
    from app.db.engine import _ConfigSessionLocal

    tool = build_tool()

    db = _ConfigSessionLocal()
    try:
        spec = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == "loadedhub")
            .first()
        )
        if not spec:
            raise SystemExit("loadedhub ConnectionSpec not found")
        endpoints = [dict(e) for e in (spec.endpoints or [])]
        have = {e.get("action"): i for i, e in enumerate(endpoints)}
        endpoint_changes = []
        for ep in ENDPOINTS:
            i = have.get(ep["action"])
            if i is None:
                endpoints.append(dict(ep))
                endpoint_changes.append(f"added {ep['action']}")
            elif {**endpoints[i], **ep} != endpoints[i]:
                endpoints[i] = {**endpoints[i], **ep}
                endpoint_changes.append(f"updated {ep['action']}")
        tools = [dict(t) for t in (spec.tools or [])]
        idx = next(
            (i for i, t in enumerate(tools) if t.get("action") == tool["action"]),
            None,
        )
        if idx is not None:
            # Keep what other installers own on the live row (approval labels,
            # added_at …): replacing the whole row used to drop them.
            for key, value in tools[idx].items():
                tool.setdefault(key, value)
        if idx is not None and tools[idx] == tool and not endpoint_changes:
            print("get_stock: already up to date")
            return
        if idx is None:
            tools.append(tool)
            what = "added"
        else:
            tools[idx] = tool
            what = "updated"
        if endpoint_changes:
            what += " (endpoints: " + ", ".join(endpoint_changes) + ")"
        if dry_run:
            print(f"DRY RUN — would have {what} get_stock")
            return
        spec.endpoints = endpoints
        flag_modified(spec, "endpoints")
        spec.tools = tools
        flag_modified(spec, "tools")
        spec.version = (spec.version or 0) + 1
        db.commit()
        print(f"get_stock {what}, spec version -> {spec.version}")
    finally:
        db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
