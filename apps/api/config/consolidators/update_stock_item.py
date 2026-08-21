# ruff: noqa: F821 — sandbox-injected names; not imports.
#
# Canonical function_code for the `loadedhub.update_stock_item` consolidator
# (synced by scripts/sync_stock_item_consolidators.py). Loaded updates a
# stock item by PUTting the WHOLE object back, so the old flow made the LLM
# fetch the complete item (get_stock_item_full), carry every field through
# its context, and echo the lot back with one edit — expensive, and one
# hallucinated field away from corrupting the item. The read-modify-write now
# happens HERE: the model sends the item_id and ONLY the deltas.
#
# - `changes`: top-level fields to set, Loaded's own field names
#   (e.g. {"name": "...", "minimumStockOnHand": 6}). Keys the item does not
#   already carry are refused — a typo must not invent a field.
# - `variant_changes`: edits to existing suppliers[] entries, matched by
#   variant_id, or by supplier_id + stock_code.
# - `add_suppliers`: new suppliers[] entries appended verbatim (Loaded has no
#   variant-create endpoint; appending to the whole-item PUT is the verified
#   path).
#
# The tool's method stays PUT so the agent loop's human-approval gate holds;
# the PUT itself goes through update_stock_item_raw (declared in
# allowed_write_actions).
#
# Requires consolidator_config:
#   {"max_api_calls": 3, "allowed_write_actions": ["update_stock_item_raw"]}


def run(params, call_api, log):
    item_id = params.get("item_id")
    if not item_id:
        return {"error": "item_id is required — look the item up with get_stock_items"}
    venue = params.get("venue")
    changes = params.get("changes") or {}
    variant_changes = params.get("variant_changes") or []
    add_suppliers = params.get("add_suppliers") or []
    if not (changes or variant_changes or add_suppliers):
        return {
            "error": "nothing to change — pass changes, variant_changes or add_suppliers"
        }

    item = call_api(
        "loadedhub", "get_stock_item_full", {"venue": venue, "item_id": item_id}
    )
    if not isinstance(item, dict) or item.get("error"):
        return {"error": (item or {}).get("error") or f"stock item {item_id} not found"}

    changed = {}
    skipped = []
    for key, value in changes.items() if isinstance(changes, dict) else []:
        if key in ("id", "suppliers"):
            skipped.append(key)
            continue
        if key not in item:
            # A field Loaded doesn't store on this item — refusing beats
            # silently inventing schema.
            skipped.append(key)
            continue
        if item.get(key) != value:
            changed[key] = {"from": item.get(key), "to": value}
            item[key] = value

    variants = item.get("suppliers") or []
    variants_changed = 0
    for vc in variant_changes if isinstance(variant_changes, list) else []:
        if not isinstance(vc, dict):
            continue
        target = None
        for v in variants:
            if vc.get("variant_id") and v.get("id") == vc["variant_id"]:
                target = v
                break
            if (
                vc.get("supplier_id")
                and vc.get("stock_code")
                and v.get("supplierId") == vc["supplier_id"]
                and str(v.get("stockCode")) == str(vc["stock_code"])
            ):
                target = v
                break
        if target is None:
            skipped.append(
                f"variant {vc.get('variant_id') or vc.get('stock_code')} (no match)"
            )
            continue
        for key, value in vc.items():
            if key in ("variant_id", "supplier_id", "stock_code", "id"):
                continue
            if target.get(key) != value:
                target[key] = value
                variants_changed += 1
    for entry in add_suppliers if isinstance(add_suppliers, list) else []:
        if isinstance(entry, dict):
            variants.append(entry)
            variants_changed += 1
    item["suppliers"] = variants

    if not changed and not variants_changed:
        return {
            "item_id": item_id,
            "name": item.get("name"),
            "result": "no differences — nothing written",
            "skipped": skipped,
        }

    log(
        f"updating {item.get('name')}: {sorted(changed)} "
        f"+ {variants_changed} variant change(s)"
    )
    out = call_api(
        "loadedhub",
        "update_stock_item_raw",
        {"venue": venue, "item_id": item_id, "item": item},
    )
    if isinstance(out, dict) and out.get("error"):
        return {"error": out["error"], "attempted": sorted(changed)}
    return {
        "item_id": item_id,
        "name": item.get("name"),
        "changed": changed,
        "variants_changed": variants_changed,
        "skipped": skipped,
        "result": "updated",
    }
