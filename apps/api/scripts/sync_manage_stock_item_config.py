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
7. (18 Oct 2026) Installs the endpoint `update_invoice_raw` — a whole-invoice
   PUT — for op 'fix_received_unit', which rewrites an item's lines on
   RECEIVED invoices to the right unit (thread bf840172: a Count item
   received in a Weight unit priced a bun at $11.25). The same run raises
   max_api_calls and declares the new write.
8. (23 Oct 2026) The Catalogue page's operations — retire, restore, merge,
   and the unit operations (docs/plans/stock-catalogue-page.md): four more
   stock-API endpoints (ENDPOINTS) and the Loaded Legacy writes they need
   (those endpoints are Loaded Legacy's own, installed by
   sync_loaded_legacy_spec.py — run that first).

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


def _now() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def demoted_prefix(action: str) -> str:
    return f"[consolidator-only] Superseded by {NEW} — {RETIRING[action][1]}. "


TOOL = {
    "action": NEW,
    # PUT keeps the agent loop's human-approval gate; the consolidator's
    # deterministic merge decides what is actually written.
    "method": "PUT",
    "read_only": False,
    # Owned by the approvals installers (sync_approval_labels.py,
    # sync_approval_preferences.py) and carried here too: a re-run of this
    # script on 18 Oct 2026 replaced the row and dropped both, which failed
    # validation (an App-claimed tool must declare its effect) and lost the
    # per-person "always allow" policy until the next run put them back.
    "effect": "write",
    "approval": {
        "default": "ask",
        "allow_auto": True,
        "label": "change stock items and units",
    },
    # Tightened 7 Oct 2026: each rule stated once (the tool list is ~65% of
    # every prompt).
    "description": (
        "Create or change a Loaded stock item — a write, human-approved. op 'create': "
        "the full `item` (name, groupId, unitType 0 Weight / 1 Volume / 2 Count, "
        "countingUnitId + countingUnitRatio, orderingUnitId + orderingUnitRatio, "
        "defaultSupplierId, suppliers [{supplierId, stockCode, unitId, unitCost, "
        "defaultForSupplier}]); GST-rated unless you pass globalSalesTaxSortOrder 0. "
        "op 'update': item_id plus ONLY the deltas — `changes`, `variant_changes`, "
        "`add_suppliers`; Norm merges, writes the whole item and reports anything "
        "Loaded didn't keep — never resend the item. op 'set_variant_unit': variant_id "
        "+ unit_id. op 'fix_received_unit': item_id + unit_id (+ period or invoice_ids) "
        "rewrites the item's lines on RECEIVED invoices to that unit — quantity and "
        "cost unchanged, so the cost per base unit changes — and moves its supplier "
        "variants out of the old unit; one approval covers every invoice. It is the "
        "only way to change a received invoice. A changed unit needs its ratio, and "
        "each supplier keeps exactly one defaultForSupplier — both checked before "
        "writing. op 'retire' / 'restore': item_id. op 'merge': item_id INTO "
        "into_item_id — its invoices, variants, open orders, recipes and POS links "
        "move, then it is retired (same unit type only). Units: 'update_unit' "
        "(unit_id + name / ratio), 'delete_unit' (refused while in use), "
        "'restore_unit', 'merge_units' (unit_id INTO into_unit_id, same type and "
        "size). A merge that says 'in progress' is run again to finish. Ids and "
        "units come from get_stock."
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
        "from_unit",
        "period",
        "invoice_ids",
        "include_reconciled",
        "also_variants",
        "remove_variants",
        "into_item_id",
        "into_unit_id",
        "name",
        "ratio",
    ],
    "field_descriptions": {
        "op": (
            "create | update | set_variant_unit | fix_received_unit | retire | "
            "restore | merge | update_unit | delete_unit | restore_unit | "
            "merge_units. No default."
        ),
        "item": "create: the full stock item.",
        "item_id": (
            "update / fix_received_unit / retire / restore: the stock item id "
            "(from get_stock). merge: the item merged AWAY."
        ),
        "into_item_id": "merge: the item kept.",
        "into_unit_id": "merge_units: the unit kept.",
        "remove_variants": "update: suppliers[] entry ids (variant_id) to remove.",
        "name": "update_unit: the unit's new name.",
        "ratio": (
            "update_unit: the unit's new size in its base unit (kg, L or each) — "
            "everything already recorded in the unit is read at the new size."
        ),
        "changes": (
            "update: top-level fields to set, in Loaded's names (e.g. "
            "{'minimumStockOnHandQuantity': 6}); deltas only."
        ),
        "variant_changes": (
            "update: edits to existing suppliers[] entries — each by variant_id, or "
            "supplier_id + stock_code, with the fields in Loaded's names (unitCost, "
            "unitId, brandId, defaultForSupplier, description). A field a variant "
            "doesn't have is skipped."
        ),
        "add_suppliers": (
            "update: new suppliers[] entries (Loaded has no variant-create) — each "
            "needs supplierId and unitId, plus stockCode, unitCost, "
            "defaultForSupplier."
        ),
        "variant_id": "set_variant_unit: the suppliers[] entry id (36 characters).",
        "unit_id": (
            "set_variant_unit / fix_received_unit / the unit ops: the unit id (36 "
            "characters; get_stock view 'reference', kind 'units'). merge_units: "
            "the unit merged AWAY."
        ),
        "from_unit": (
            "fix_received_unit: only lines and variants in this unit (id or name); "
            "default: every unit other than unit_id."
        ),
        "period": (
            "fix_received_unit: which received invoices to look through, in plain "
            "English ('last 12 months' by default) — or pass invoice_ids instead."
        ),
        "invoice_ids": (
            "fix_received_unit: the Loaded invoice ids to fix (from get_invoices), "
            "instead of a period."
        ),
        "include_reconciled": (
            "fix_received_unit: also change invoices reconciled against a statement "
            "(default false — they are skipped and listed)."
        ),
        "also_variants": (
            "fix_received_unit: also move the item's supplier variants out of the old "
            "unit so future receives are right (default true)."
        ),
    },
    # Real JSON types: shown as strings, the model wrapped them in quotes and
    # the old consolidator silently ignored them (27 Aug 2026).
    "field_schema": {
        "op": {
            "type": "string",
            "enum": [
                "create",
                "update",
                "set_variant_unit",
                "fix_received_unit",
                "retire",
                "restore",
                "merge",
                "update_unit",
                "delete_unit",
                "restore_unit",
                "merge_units",
            ],
        },
        "item": {"type": "object"},
        "changes": {"type": "object"},
        "variant_changes": {"type": "array", "items": {"type": "object"}},
        "add_suppliers": {"type": "array", "items": {"type": "object"}},
        "invoice_ids": {"type": "array", "items": {"type": "string"}},
        "include_reconciled": {"type": "boolean"},
        "also_variants": {"type": "boolean"},
        "remove_variants": {"type": "array", "items": {"type": "string"}},
        "ratio": {"type": "number"},
    },
    "consolidator_config": {
        # function_code injected at sync time. create 1, update 2 (read +
        # PUT), set_variant_unit 1; fix_received_unit reads units, the item,
        # the period and the feed, then per invoice a detail + PUT + read-back,
        # plus one PATCH per variant — about 4 + 3n + v (6 invoices ≈ 26).
        # A merge (23 Oct 2026) can need hundreds: it stops itself at _BUDGET
        # (180) and is run again, so this is the sandbox's own cap.
        "max_api_calls": 200,
        "allowed_write_actions": [
            "create_stock_item_raw",
            "update_stock_item_raw",
            "update_variant_unit_raw",
            "update_invoice_raw",
            "move_variant_raw",
            "update_purchase_order_raw",
            "loaded_legacy.patch_stock_item",
            "loaded_legacy.update_pos_item_link",
            "loaded_legacy.update_stock_unit",
            "loaded_legacy.save_recipe_version",
        ],
    },
}

_HEADERS = {
    "Content-Type": "application/json",
    "x-loaded-company-id": "{{ creds.x_loaded_company_id }}",
}
_STOCK = "//api.loadedhub.com/1.0/stock/internal"


def _endpoint(action, description, method, path, fields, body=None, ok=(200,)):
    row = {
        "action": action,
        "engine_only": True,
        "read_only": method == "GET",
        "description": "[consolidator-only] " + description + " Called by "
        "manage_stock_item; never bind this to an agent.",
        "method": method,
        "path_template": path,
        "headers": dict(_HEADERS),
        "required_fields": list(fields),
        "field_mapping": {k: k for k in fields},
        "field_descriptions": fields,
        "success_status_codes": list(ok),
        "response_ref_path": "",
        "timeout_seconds": 60,
        "response_transform": None,
    }
    if body:
        row["request_body_template"] = body
    return row


#: The whole-invoice PUT behind op 'fix_received_unit'. Loaded has no line
#: edit: a received invoice is changed by sending the complete detail back
#: (it stays received — the 16 Oct 2026 GST repair did 64 this way).
INVOICE_PUT = _endpoint(
    "update_invoice_raw",
    "Re-PUT a whole Loaded invoice (the complete detail from get_invoice_detail, "
    "edited).",
    "PUT",
    _STOCK + "/invoices/{{ invoice_id }}",
    {
        "invoice_id": "The Loaded invoice id.",
        "invoice": "The complete invoice detail to store.",
    },
    body="{{ invoice | tojson }}",
    ok=(200, 204),
)
INVOICE_PUT["timeout_seconds"] = 30

#: Engine-only endpoints this tool calls, written into spec.endpoints. The
#: Catalogue page's four (23 Oct 2026) were each tried on Loaded's test
#: company before use (docs/plans/stock-catalogue-page.md).
ENDPOINTS = [
    INVOICE_PUT,
    _endpoint(
        "get_stock_item_any_raw",
        "One stock item, retired or not, with its supplier variants "
        "(get_stock_item_full 404s on a retired item).",
        "GET",
        _STOCK + "/items/{{ item_id }}?includeDeleted=true",
        {"item_id": "The stock item's id."},
    ),
    _endpoint(
        "move_variant_raw",
        "Move a supplier variant to another stock item (it keeps its id; Loaded "
        "sorts out both items' default flags).",
        "PATCH",
        _STOCK + "/item-supplier-variant/{{ variant_id }}",
        {
            "variant_id": "The variant's id.",
            "item_id": "The stock item it moves to.",
        },
        body='{"stockItemId": {{ item_id | tojson }}}',
    ),
    _endpoint(
        "get_received_for_item_raw",
        "Every received invoice holding a stock item, between two datetimes.",
        "GET",
        _STOCK + "/stock-received?from={{ from }}&to={{ to }}&property=Received"
        "&itemId={{ item_id }}",
        {
            "item_id": "The stock item's id.",
            "from": "ISO datetime.",
            "to": "ISO datetime.",
        },
    ),
    _endpoint(
        "update_purchase_order_raw",
        "Re-PUT a whole purchase order (the complete detail from "
        "get_purchase_order_detail, edited; a line left out is deleted).",
        "PUT",
        _STOCK + "/purchase-orders/{{ order_id }}",
        {
            "order_id": "The purchase order's id.",
            "order": "The complete purchase order to store.",
        },
        body="{{ order | tojson }}",
        ok=(200, 204),
    ),
]


def build_tool() -> dict:
    """TOOL with its function_code, as installed."""
    tool = dict(TOOL)
    tool["consolidator_config"] = {
        **TOOL["consolidator_config"],
        "function_code": (_DIR / "manage_stock_item.py").read_text(encoding="utf-8"),
    }
    return tool


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

    from app.connectors import spec_rows
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
        # Both lists: since Sep 2026 these endpoints live in spec.endpoints.
        by_action = {t.get("action"): t for t in spec_rows.rows(spec)}
        for needed in (
            "update_stock_item_raw",
            "get_stock_item_full",
            "get_stock_items_raw",
            "get_stock_units",
            "get_suppliers",
            "get_invoice_detail",
            "get_received_invoices",
            # The catalogue ops' reads: get_stock_items_detail_raw is installed by
            # sync_stock_config.py, get_all_recipes and the purchase-order reads
            # were there before.
            "get_stock_items_detail_raw",
            "get_all_recipes",
            "get_purchase_order_detail",
            "list_purchase_orders",
        ):
            if needed not in by_action:
                raise SystemExit(
                    f"{needed} missing — the stock-item endpoints were installed by "
                    "sync_stock_item_consolidators.py (retired; see git history)"
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

        # ── 1b. The endpoints (never shown to a model) ───────────────────
        endpoints = [dict(e) for e in (spec.endpoints or [])]
        for ep in ENDPOINTS:
            have = next((e for e in endpoints if e.get("action") == ep["action"]), None)
            want = dict(ep)
            if have and have.get("added_at"):
                want["added_at"] = have["added_at"]
            if have is None:
                want.setdefault("added_at", _now())
                endpoints.append(want)
                changes.append(f"spec endpoints: added {ep['action']}")
            elif have != want:
                endpoints[endpoints.index(have)] = want
                changes.append(f"spec endpoints: updated {ep['action']}")
            by_action[ep["action"]] = want
        if not dry_run and endpoints != [dict(e) for e in (spec.endpoints or [])]:
            spec.endpoints = endpoints
            flag_modified(spec, "endpoints")

        # ── 2. The consolidator ───────────────────────────────────────────
        tool = build_tool()
        if NEW in by_action:
            live = by_action[NEW]
            keep = live.get("added_at")
            if keep:
                tool["added_at"] = keep
            # Keys another installer owns (approval policy, levels, …) are kept
            # as they are, never silently dropped by this one.
            for k, v in live.items():
                if k not in tool:
                    tool[k] = v
            if live != tool:
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
