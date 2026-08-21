# ruff: noqa: F821 — sandbox-injected names; not imports.
#
# Canonical function_code for the `loadedhub.get_stock_items` consolidator —
# THE stock item lookup (synced by scripts/sync_stock_item_consolidators.py).
# Replaces three raw tools on the agent surface: the all-items list, the
# per-item detail (get_stock_item) and the complete-item read
# (get_stock_item_full, which existed only as the read-before-write for
# whole-item PUTs — the update_stock_item consolidator does that merge
# server-side now, so no agent ever needs to carry the full object again).
#
# The LLM chooses what data it wants instead of a fixed transform choosing
# for it: a name query returns slim {id, name} matches; an item_id returns
# ONE item at `detail` "summary" (the working fields) or "full" (everything,
# suppliers[] included — what ordering needs to pick the default variant).
#
# Token doctrine: summaries carry NAMES, not UUIDs. A unit id is ~10 tokens
# of noise the model can't reason about without another lookup; "20 L" is one
# token it can. The only ids kept are the handles later calls need: the
# item's id (detail / update) and each variant_id (update's precise match).
# Item-level names ride free on Loaded's payload; variant unit and supplier
# names are resolved here via the units and suppliers lists so the model
# never spends a round trip interpreting ids.
#
# Requires consolidator_config: {"max_api_calls": 5}


def run(params, call_api, log):
    item_id = params.get("item_id")
    query = str(params.get("query") or "").strip().lower()
    detail = str(params.get("detail") or "summary").strip().lower()
    limit = int(params.get("limit") or 25)
    venue = params.get("venue")

    def summarize(item):
        # The working subset, names over ids: what ordering, pricing and
        # recipe questions actually use — never the whole Loaded object
        # unless asked. Variant unit/supplier names need the two lookup
        # lists (item-level names ride free on the payload).
        raw_variants = item.get("suppliers") or []
        unit_names = {}
        supplier_names = {}
        if raw_variants:
            units = call_api("loadedhub", "get_stock_units", {"venue": venue})
            for u in units if isinstance(units, list) else []:
                unit_names[u.get("id")] = u.get("name")
            sups = call_api("loadedhub", "get_suppliers", {"venue": venue})
            for s in sups if isinstance(sups, list) else []:
                supplier_names[s.get("id")] = s.get("name")
        variants = [
            {
                "variant_id": v.get("id"),
                "supplier": supplier_names.get(v.get("supplierId"))
                or v.get("supplierId"),
                "stock_code": v.get("stockCode"),
                "unit": unit_names.get(v.get("unitId")) or v.get("unitId"),
                "unit_cost": v.get("unitCost"),
                "default": v.get("defaultForSupplier"),
            }
            for v in raw_variants
        ]
        return {
            "id": item.get("id"),
            "name": item.get("name"),
            "group": item.get("groupName"),
            "counting_unit": item.get("countingUnitName"),
            "counting_unit_ratio": item.get("countingUnitRatio"),
            "ordering_unit": item.get("orderingUnitName"),
            "ordering_unit_ratio": item.get("orderingUnitRatio"),
            "minimum_stock_on_hand": item.get("minimumStockOnHandQuantity"),
            "minimum_stock_unit": item.get("minimumStockOnHandUnit"),
            "variants": variants,
        }

    if item_id:
        item = call_api(
            "loadedhub", "get_stock_item_full", {"venue": venue, "item_id": item_id}
        )
        if not isinstance(item, dict) or item.get("error"):
            return {
                "error": (item or {}).get("error") or f"stock item {item_id} not found"
            }
        if detail == "full":
            return {"item": item, "detail": "full"}
        return {"item": summarize(item), "detail": "summary"}

    rows = call_api("loadedhub", "get_stock_items_raw", {"venue": venue})
    if not isinstance(rows, list):
        return {"error": (rows or {}).get("error") or "stock item list unavailable"}
    if query:
        rows = [r for r in rows if query in str(r.get("name") or "").lower()]
    total = len(rows)
    rows = rows[:limit]
    out = {
        "matches": rows,  # slim {id, name} — ask again with item_id for detail
        "total_matches": total,
        "shown": len(rows),
    }
    if len(rows) == 1 and query:
        # An unambiguous name hit: save the model a round trip.
        item = call_api(
            "loadedhub",
            "get_stock_item_full",
            {"venue": venue, "item_id": rows[0].get("id")},
        )
        if isinstance(item, dict) and not item.get("error"):
            out["item"] = item if detail == "full" else summarize(item)
            out["detail"] = "full" if detail == "full" else "summary"
    if not query:
        out["note"] = (
            "this is the full item list — pass query (name substring) or "
            "item_id instead of scanning it, especially before an update"
        )
    return out
