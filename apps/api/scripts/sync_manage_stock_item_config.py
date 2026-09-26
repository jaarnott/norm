"""Install `loadedhub.manage_stock_item`; the three writes it absorbs step behind it.

Stock writes arc (Sep 2026). create_stock_item, update_stock_item and
update_variant_unit were three schemas for one object; 13 calls in 90 days,
two of which wrote nothing while reporting success (see the consolidator
header). They merge into `manage_stock_item(op=create|update|set_variant_unit)`,
mirroring manage_task.

What this does, each step idempotent:

1. Clones the two raw HTTP writes the agent used to hold into engine-only
   backends — create_stock_item_raw, update_variant_unit_raw — next to the
   existing update_stock_item_raw, so the consolidator can call all three
   through allowed_write_actions.
2. Installs manage_stock_item (method PUT: the agent loop's human-approval
   gate keys on the method). Its object/array fields carry a field_schema,
   so the model emits JSON, not JSON-in-a-string.
3. Demotes the three agent-facing rows to engine_only with a superseded
   marker. Demote, not delete: nothing else calls them by name (no saved
   task, app or chart does — scanned 26 Sep 2026), but a demoted row is
   reversible with one flag.
4. Rebinds executive_chef: the three out, manage_stock_item in; rewrites the
   two prompt sentences that named them.
5. Installs get_stock_items_with_codes, an engine-only list carrying each
   item's suppliers[] (stock codes), for create_purchase_order's stock-code
   path — which scanned get_stock_items_raw, whose transform has no
   suppliers, so a code could never match.
6. validate_config.

Replays kept honest in the same commit: sync_stock_item_write_actions.py and
sync_stock_item_consolidators.py preserve the demotion; the executive_chef
seed lists manage_stock_item; consolidator_coverage maps the three to it.

Usage:
    uv run python scripts/sync_manage_stock_item_config.py [--dry-run]
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

_DIR = pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"

NEW = "manage_stock_item"
AGENT = "executive_chef"

#: Retiring agent-facing row -> its raw backend, and how to ask the new tool.
RETIRING = {
    "create_stock_item": ("create_stock_item_raw", "op 'create'"),
    "update_stock_item": (None, "op 'update'"),  # already a consolidator
    "update_variant_unit": ("update_variant_unit_raw", "op 'set_variant_unit'"),
}


def demoted_prefix(action: str) -> str:
    return f"[consolidator-only] Superseded by {NEW} — {RETIRING[action][1]}. "


TOOL = {
    "action": NEW,
    # PUT keeps the agent loop's human-approval gate; the consolidator's
    # deterministic merge decides what is actually written.
    "method": "PUT",
    "read_only": False,
    "description": (
        "Create or change a stock item in Loaded. op picks the action: "
        "'create' — pass the full `item` (name, groupId, unitType 0=Weight "
        "1=Volume 2=Count, countingUnitId+countingUnitRatio, "
        "orderingUnitId+orderingUnitRatio, defaultSupplierId, suppliers:[{"
        "supplierId, stockCode, unitId, unitCost, defaultForSupplier}]). "
        "'update' — item_id plus ONLY the deltas: `changes` (Loaded's field "
        "names, e.g. {'minimumStockOnHandQuantity': 6}), `variant_changes` "
        "(suppliers[] edits matched by variant_id or supplier_id+stock_code), "
        "`add_suppliers` (new variants — Loaded has no variant-create endpoint); "
        "the server fetches, merges and writes the whole item, never resend it. "
        "'set_variant_unit' — variant_id + unit_id. Ids and units come from "
        "get_stock. This is a write — human-approved."
    ),
    "required_fields": ["op"],
    "optional_fields": [
        "item",
        "item_id",
        "changes",
        "variant_changes",
        "add_suppliers",
        "variant_id",
        "unit_id",
    ],
    "field_descriptions": {
        "op": "create | update | set_variant_unit. No default.",
        "item": "create: the full stock item object.",
        "item_id": "update: Loaded stock item id (from get_stock).",
        "changes": "update: top-level fields to set, Loaded's field names, deltas only.",
        "variant_changes": (
            "update: list of edits to existing suppliers[] entries; each needs "
            "variant_id OR supplier_id + stock_code, plus the fields to set."
        ),
        "add_suppliers": "update: new suppliers[] entries to append (full entry dicts).",
        "variant_id": "set_variant_unit: the suppliers[] entry id (36 characters).",
        "unit_id": "set_variant_unit: the new unit id (36 characters).",
    },
    # Real JSON types: shown as strings, the model wrapped them in quotes and
    # the old consolidator silently ignored them (27 Aug 2026).
    "field_schema": {
        "op": {"type": "string", "enum": ["create", "update", "set_variant_unit"]},
        "item": {"type": "object"},
        "changes": {"type": "object"},
        "variant_changes": {"type": "array", "items": {"type": "object"}},
        "add_suppliers": {"type": "array", "items": {"type": "object"}},
    },
    "consolidator_config": {
        # function_code injected at sync time. create 1, update 2 (read +
        # PUT), set_variant_unit 1.
        "max_api_calls": 3,
        "allowed_write_actions": [
            "create_stock_item_raw",
            "update_stock_item_raw",
            "update_variant_unit_raw",
        ],
    },
}

#: The purchase-order resolver's stock-code path needs the codes.
CODES_TOOL = {
    "action": "get_stock_items_with_codes",
    "engine_only": True,
    "description": (
        "[consolidator-only] The all-items list with each item's suppliers[] "
        "(stock codes), deleted items filtered. Backend of the purchase-order "
        "resolver's stock-code match; never bind this to an agent — get_stock "
        "answers item questions."
    ),
    "response_transform": {
        "enabled": True,
        "fields": {"id": "id", "name": "name", "suppliers": "suppliers"},
        "flatten": [],
        "filters": [{"field": "datestampRemoved", "operator": "is_empty", "value": ""}],
    },
}

PROMPT_PATCHES = [
    (
        "For an update, call update_stock_item with the\n"
        "item_id and ONLY the fields to change",
        "For any write call manage_stock_item — op 'create' with the full item, "
        "or op 'update' with the\nitem_id and ONLY the fields to change",
    ),
    (
        "For a single variant-unit change,\nprefer update_variant_unit.",
        "For a single variant-unit change,\nuse op 'set_variant_unit'.",
    ),
]


def main(dry_run: bool = False) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.config_models import AgentConfig, AgentConnectionBinding, ConnectionSpec
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    changes: list[str] = []
    try:
        spec = (
            db.query(ConnectionSpec)
            .filter(ConnectionSpec.connector_name == "loadedhub")
            .first()
        )
        if not spec:
            raise SystemExit("loadedhub ConnectionSpec not found")
        tools = [dict(t) for t in (spec.tools or [])]
        by_action = {t.get("action"): t for t in tools}
        for needed in (
            "update_stock_item_raw",
            "get_stock_item_full",
            "get_stock_items_raw",
        ):
            if needed not in by_action:
                raise SystemExit(
                    f"{needed} missing — run sync_stock_item_consolidators.py first"
                )

        # ── 1. Raw backends cloned from the agent-facing HTTP rows ────────
        for action, (raw_name, _how) in RETIRING.items():
            if not raw_name or raw_name in by_action:
                continue
            src = by_action.get(action)
            if not src or src.get("consolidator_config"):
                raise SystemExit(f"expected the raw HTTP row {action} to clone")
            raw = {
                k: v
                for k, v in src.items()
                if k not in ("added_at", "engine_only", "description")
            }
            raw["action"] = raw_name
            raw["engine_only"] = True
            raw["description"] = (
                f"[consolidator-only] {src.get('description', '')} Called by "
                f"{NEW}; never bind this to an agent."
            )
            tools.append(raw)
            by_action[raw_name] = raw
            changes.append(f"spec: added {raw_name}")

        # ── 2. The consolidator ───────────────────────────────────────────
        tool = dict(TOOL)
        tool["consolidator_config"] = {
            **TOOL["consolidator_config"],
            "function_code": (_DIR / "manage_stock_item.py").read_text(
                encoding="utf-8"
            ),
        }
        if NEW in by_action:
            keep = by_action[NEW].get("added_at")
            if keep:
                tool["added_at"] = keep
            if by_action[NEW] != tool:
                tools[[t.get("action") for t in tools].index(NEW)] = tool
                changes.append(f"spec: updated {NEW}")
        else:
            tools.append(tool)
            changes.append(f"spec: added {NEW}")

        # ── 3. Demote the three ───────────────────────────────────────────
        for t in tools:
            action = t.get("action")
            if action in RETIRING:
                if not t.get("engine_only"):
                    t["engine_only"] = True
                    changes.append(f"spec {action}: engine_only")
                desc = str(t.get("description") or "")
                if not desc.startswith("[consolidator-only]"):
                    t["description"] = demoted_prefix(action) + desc
                    changes.append(f"spec {action}: description marked superseded")

        # ── 5. Codes list for the purchase-order resolver ─────────────────
        if CODES_TOOL["action"] not in by_action:
            src = by_action["get_stock_items_raw"]
            codes = {k: v for k, v in src.items() if k not in ("added_at",)}
            codes.update(CODES_TOOL)
            tools.append(codes)
            changes.append(f"spec: added {CODES_TOOL['action']}")

        if changes and not dry_run:
            spec.tools = tools
            flag_modified(spec, "tools")
            spec.version = (spec.version or 0) + 1

        # ── 4. Binding + prompt ───────────────────────────────────────────
        b = (
            db.query(AgentConnectionBinding)
            .filter(
                AgentConnectionBinding.agent_slug == AGENT,
                AgentConnectionBinding.connector_name == "loadedhub",
            )
            .first()
        )
        if b:
            caps = [dict(c) for c in (b.capabilities or [])]
            dropped = [c for c in caps if c.get("action") in RETIRING]
            kept = [c for c in caps if c.get("action") not in RETIRING]
            if dropped and not any(c.get("action") == NEW for c in kept):
                kept.append({"action": NEW, "enabled": True})
            if kept != caps:
                changes.append(
                    f"binding {AGENT}: dropped {sorted(c.get('action') for c in dropped)}; "
                    f"{NEW} enabled"
                )
                if not dry_run:
                    b.capabilities = kept
                    flag_modified(b, "capabilities")
        a = db.query(AgentConfig).filter(AgentConfig.agent_slug == AGENT).first()
        if a and a.system_prompt:
            text = a.system_prompt
            for old, new in PROMPT_PATCHES:
                if old in text:
                    text = text.replace(old, new)
                    changes.append(f"prompt {AGENT}: needle swapped")
            if text != a.system_prompt and not dry_run:
                a.system_prompt = text

        if dry_run:
            db.rollback()
            print("DRY RUN — would apply:")
        else:
            db.commit()
            print("Applied:")
        for line in changes or ["  (nothing to do)"]:
            print(f"  {line}")

        if not dry_run:
            from app.services.config_validator import validate_config

            summary = validate_config(config_db=db)
            errors = [i for i in summary["issues"] if i.get("severity") == "error"]
            if errors:
                print("\nVALIDATION ERRORS (fix before walking away):")
                for i in errors:
                    print(f"  {i['where']}: {i['problem']}")
                sys.exit(1)
            print(
                f"\nconfig validation: clean ({summary['issue_count']} non-error notes)"
            )
    finally:
        db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
