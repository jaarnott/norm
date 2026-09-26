# ruff: noqa: F821 — sandbox-injected names (json) ; not imports.
#
# Canonical function_code for `loadedhub.manage_stock_item` — THE stock item
# write tool (installed by scripts/sync_manage_stock_item_config.py; stock
# writes arc, Sep 2026). One tool, three ops, each reaching the raw write
# that always did the work:
#
#   create            POST the full item          -> create_stock_item_raw
#   update            item_id + deltas, merged    -> update_stock_item_raw
#                     server-side into the whole object Loaded wants back
#   set_variant_unit  variant_id + unit_id        -> update_variant_unit_raw
#
# It absorbs create_stock_item, update_stock_item and update_variant_unit on
# the agent surface, and fixes three things found in production:
#
#   - update_stock_item's own description told the model to send
#     {'minimumStockOnHand': 6}. Loaded's field is minimumStockOnHandQuantity;
#     the consolidator refused the unknown key and the edit was skipped.
#     The old name is now an alias, and the skip is explained.
#   - `changes` / `add_suppliers` / `item` arrive as JSON STRINGS when the
#     model is shown them as strings (the two CHILLI POWDER MILD updates on
#     27 Aug 2026 wrote nothing: "no differences"). Strings are parsed here,
#     and the installer declares them as objects/arrays so the model emits
#     real JSON.
#   - update_variant_unit's only failure was an 8-character id ("5f392d87"):
#     Loaded answered 400. Ids are checked for shape before the call.
#
# The tool's method stays PUT so the agent loop's human-approval gate holds;
# the raw writes are declared in allowed_write_actions.
#
# Requires consolidator_config:
#   {"max_api_calls": 3, "allowed_write_actions": ["create_stock_item_raw",
#    "update_stock_item_raw", "update_variant_unit_raw"]}

_OPS = ("create", "update", "set_variant_unit")

#: Loaded's own names for the fields a model tends to abbreviate.
_ALIASES = {
    "minimumStockOnHand": "minimumStockOnHandQuantity",
    "minimum_stock_on_hand": "minimumStockOnHandQuantity",
    "minimumStock": "minimumStockOnHandQuantity",
}

#: Without these Loaded answers 400 to a create; refusing here is clearer.
_CREATE_REQUIRED = (
    "name",
    "groupId",
    "unitType",
    "countingUnitId",
    "countingUnitRatio",
    "orderingUnitId",
    "orderingUnitRatio",
)


def _parse(value):
    """A dict/list as given; a JSON string decoded; anything else -> None."""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return None
    return value


def _obj(value):
    parsed = _parse(value)
    return parsed if isinstance(parsed, dict) else None


def _lst(value):
    parsed = _parse(value)
    if isinstance(parsed, dict):
        return [parsed]
    return parsed if isinstance(parsed, list) else None


def _looks_like_id(value):
    return isinstance(value, str) and len(value.strip()) == 36


def _err(result):
    if isinstance(result, dict) and result.get("error"):
        return str(result["error"])
    return None


# --------------------------------------------------------------- create ----


def _create(params, venue, call_api, log):
    item = _obj(params.get("item"))
    if item is None:
        return {
            "error": (
                "op 'create' needs `item`: a JSON object (not a string) with "
                + ", ".join(_CREATE_REQUIRED)
                + ", defaultSupplierId and suppliers[]"
            )
        }
    missing = [k for k in _CREATE_REQUIRED if item.get(k) in (None, "")]
    if missing:
        return {
            "error": (
                f"item is missing {', '.join(missing)} — ids and units come from "
                "get_stock (view 'reference')"
            )
        }
    item.setdefault("itemType", "Default")
    log(f"creating stock item '{item.get('name')}'")
    out = call_api("loadedhub", "create_stock_item_raw", {"venue": venue, "item": item})
    if _err(out):
        return {"error": _err(out), "attempted": item.get("name")}
    new_id = out.get("id") if isinstance(out, dict) else None
    return {"result": "created", "name": item.get("name"), "item_id": new_id}


# --------------------------------------------------------------- update ----


def _update(params, venue, call_api, log):
    item_id = params.get("item_id")
    if not item_id:
        return {"error": "op 'update' needs item_id — look the item up with get_stock"}
    raw_changes = params.get("changes")
    raw_variants = params.get("variant_changes")
    raw_add = params.get("add_suppliers")
    changes = _obj(raw_changes) if raw_changes not in (None, "") else {}
    variant_changes = _lst(raw_variants) if raw_variants not in (None, "") else []
    add_suppliers = _lst(raw_add) if raw_add not in (None, "") else []
    bad = [
        name
        for name, given, parsed in (
            ("changes", raw_changes, changes),
            ("variant_changes", raw_variants, variant_changes),
            ("add_suppliers", raw_add, add_suppliers),
        )
        if given not in (None, "") and parsed is None
    ]
    if bad:
        return {
            "error": (
                f"{', '.join(bad)} could not be read as JSON — pass an object "
                "(changes) or a list (variant_changes, add_suppliers), not text"
            )
        }
    if not (changes or variant_changes or add_suppliers):
        return {
            "error": "nothing to change — pass changes, variant_changes or add_suppliers"
        }

    item = call_api(
        "loadedhub", "get_stock_item_full", {"venue": venue, "item_id": item_id}
    )
    if not isinstance(item, dict) or item.get("error"):
        return {"error": _err(item) or f"stock item {item_id} not found"}

    changed = {}
    skipped = {}
    for key, value in changes.items():
        field = _ALIASES.get(key, key)
        if field in ("id", "suppliers"):
            skipped[key] = "not editable here (use variant_changes / add_suppliers)"
            continue
        if field not in item:
            # A field Loaded doesn't store on this item — refusing beats
            # silently inventing schema.
            skipped[key] = "Loaded has no such field on this item"
            continue
        if item.get(field) != value:
            changed[field] = {"from": item.get(field), "to": value}
            item[field] = value

    variants = item.get("suppliers") or []
    variants_changed = 0
    for vc in variant_changes:
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
            skipped[f"variant {vc.get('variant_id') or vc.get('stock_code')}"] = (
                "no such variant"
            )
            continue
        for key, value in vc.items():
            if key in ("variant_id", "supplier_id", "stock_code", "id"):
                continue
            if target.get(key) != value:
                target[key] = value
                variants_changed += 1
    for entry in add_suppliers:
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
        f"updating {item.get('name')}: {sorted(changed)} + {variants_changed} variant change(s)"
    )
    out = call_api(
        "loadedhub",
        "update_stock_item_raw",
        {"venue": venue, "item_id": item_id, "item": item},
    )
    if _err(out):
        return {"error": _err(out), "attempted": sorted(changed)}
    return {
        "item_id": item_id,
        "name": item.get("name"),
        "changed": changed,
        "variants_changed": variants_changed,
        "skipped": skipped,
        "result": "updated",
    }


# ----------------------------------------------------- set_variant_unit ----


def _set_variant_unit(params, venue, call_api, log):
    variant_id = params.get("variant_id")
    unit_id = params.get("unit_id")
    if not variant_id or not unit_id:
        return {"error": "op 'set_variant_unit' needs variant_id and unit_id"}
    short = [
        n
        for n, v in (("variant_id", variant_id), ("unit_id", unit_id))
        if not _looks_like_id(v)
    ]
    if short:
        return {
            "error": (
                f"{', '.join(short)} must be the full 36-character id from get_stock "
                "(the summary lists each variant_id; units under view 'reference')"
            )
        }
    log(f"variant {variant_id} -> unit {unit_id}")
    out = call_api(
        "loadedhub",
        "update_variant_unit_raw",
        {"venue": venue, "variant_id": variant_id, "unit_id": unit_id},
    )
    if _err(out):
        return {"error": _err(out)}
    return {"result": "updated", "variant_id": variant_id, "unit_id": unit_id}


# ------------------------------------------------------------------ run ----


def run(params, call_api, log):
    op = str(params.get("op") or "").strip().lower()
    venue = params.get("venue")
    if op not in _OPS:
        return {
            "error": f"op must be one of {', '.join(_OPS)} (got '{params.get('op')}')"
        }
    if op == "create":
        return _create(params, venue, call_api, log)
    if op == "update":
        return _update(params, venue, call_api, log)
    return _set_variant_unit(params, venue, call_api, log)
