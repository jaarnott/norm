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
# And, from the consolidator review (1 Oct 2026):
#
#   - create sent no tax setting, so Loaded filed every new item under sales
#     tax sort order 0 — EXEMPT. All four items the agent created at Mr
#     Murdochs in Aug 2026 came out GST-exempt. It now defaults to 1 (GST),
#     exactly as the receive screen's own create does (invoice_fixes.py).
#   - variant_changes wrote any key it was given and counted it as a change,
#     so a field Loaded doesn't have ("unit_cost") reported "updated" while
#     nothing changed — the same failure this tool was built to fix for
#     top-level fields. Unknown keys are now skipped with a reason, and the
#     snake_case spellings are aliases.
#   - the two rules the description states are enforced, not just stated: a
#     changed counting/ordering unit needs its ratio in the same call, and a
#     supplier keeps exactly one default variant.
#   - an update is read back after the PUT, so a field or variant Loaded
#     didn't keep is reported, never assumed (the manage_menu pattern).
#
# The tool's method stays PUT so the agent loop's human-approval gate holds;
# the raw writes are declared in allowed_write_actions.
#
# Approval preview (Oct 2026, services/previews.py): just before each write
# the tool calls preview(...) with what it is about to change — field by
# field, before → after, supplier and unit ids shown as names. In a preview
# run that is where it stops; on the approved run it is where it checks the
# item still holds the values the person saw. The names cost two reads, made
# only while previewing.
#
# Catalogue page ops (23 Oct 2026): retire, restore, merge (items) and
# update_unit, delete_unit, restore_unit, merge_units — see "catalogue ops".
#
# Requires consolidator_config: max_api_calls 200 (a merge stops itself at
# _BUDGET) and allowed_write_actions naming every write below — the list is in
# scripts/sync_manage_stock_item_config.py.

_OPS = (
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
)

#: Loaded's own names for the fields a model tends to abbreviate.
_ALIASES = {
    "minimumStockOnHand": "minimumStockOnHandQuantity",
    "minimum_stock_on_hand": "minimumStockOnHandQuantity",
    "minimumStock": "minimumStockOnHandQuantity",
}

#: Loaded's names for the variant fields a model writes in snake_case.
_VARIANT_ALIASES = {
    "unit_id": "unitId",
    "unit_cost": "unitCost",
    "brand_id": "brandId",
    "default_for_supplier": "defaultForSupplier",
    "is_default": "defaultForSupplier",
}

#: A unit and the ratio that gives it meaning; one without the other leaves
#: the item counting in the new unit at the old unit's size.
_UNIT_PAIRS = (
    ("countingUnitId", "countingUnitRatio"),
    ("orderingUnitId", "orderingUnitRatio"),
)

#: GST in Loaded's sales-tax list ([Exempt 0, GST 1]); 0 makes an item exempt.
_GST_SORT_ORDER = 1

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

#: How the approval card names Loaded's fields.
_LABELS = {
    "name": "Name",
    "minimumStockOnHandQuantity": "Minimum on hand",
    "minimumStockOnHandUnitId": "Minimum on hand unit",
    "countingUnitId": "Counting unit",
    "countingUnitRatio": "Counting unit size",
    "orderingUnitId": "Ordering unit",
    "orderingUnitRatio": "Ordering unit size",
    "defaultSupplierId": "Default supplier",
    "globalSalesTaxSortOrder": "Sales tax",
    "supplierId": "Supplier",
    "stockCode": "Stock code",
    "unitId": "Unit",
    "unitCost": "Unit cost",
    "defaultForSupplier": "Default for supplier",
}
_UNIT_FIELDS = (
    "countingUnitId",
    "orderingUnitId",
    "minimumStockOnHandUnitId",
    "unitId",
)
_SUPPLIER_FIELDS = ("defaultSupplierId", "supplierId")


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


# -------------------------------------------------------------- preview ----


def _names(venue, call_api):
    """{unit id: name}, {supplier id: name} — only while previewing; the card
    is the only thing that needs them."""
    if not previewing():
        return {}, {}
    units, suppliers = {}, {}
    for u in _lst(call_api("loadedhub", "get_stock_units", {"venue": venue})) or []:
        if isinstance(u, dict):
            units[u.get("id")] = u.get("name")
    for x in _lst(call_api("loadedhub", "get_suppliers", {"venue": venue})) or []:
        if isinstance(x, dict):
            suppliers[x.get("id")] = x.get("name")
    return units, suppliers


def _label(key):
    if key in _LABELS:
        return _LABELS[key]
    words = ""
    for ch in str(key):
        words += (" " + ch.lower()) if ch.isupper() else ch
    words = words.replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def _shown(field, value, names):
    units, suppliers = names
    if field in _UNIT_FIELDS:
        return units.get(value) or value
    if field in _SUPPLIER_FIELDS:
        return suppliers.get(value) or value
    if (
        field == "unitCost"
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    ):
        return "$" + format(value, ".2f")
    if field == "globalSalesTaxSortOrder":
        return (
            "GST" if value == _GST_SORT_ORDER else ("Exempt" if value == 0 else value)
        )
    return value


def _variant_name(v, names):
    supplier = names[1].get(v.get("supplierId")) or "Supplier"
    code = v.get("stockCode")
    return f"{supplier} {code}" if code else supplier


def _bad_variant(entry):
    """Why a new suppliers[] entry can't be written, or None."""
    if not isinstance(entry, dict):
        return "is not an object"
    missing = [k for k in ("supplierId", "unitId") if not entry.get(k)]
    if missing:
        return "needs " + " and ".join(missing)
    cost = entry.get("unitCost")
    if cost is not None and (
        isinstance(cost, bool) or not isinstance(cost, (int, float))
    ):
        return f"unitCost {cost!r} is not a number"
    return None


def _defaults_by_supplier(variants):
    counts = {}
    for v in variants:
        if isinstance(v, dict) and v.get("supplierId"):
            counts.setdefault(v["supplierId"], 0)
            if v.get("defaultForSupplier") is True:
                counts[v["supplierId"]] += 1
    return counts


def _default_problems(before, after):
    """A supplier left with two default variants, or with none where it had one."""
    problems = []
    for supplier, n in sorted(after.items()):
        if n > 1:
            problems.append(
                f"supplier {supplier} would have {n} default variants — set "
                "defaultForSupplier false on the others in the same call"
            )
        elif n == 0 and before.get(supplier, 0) >= 1:
            problems.append(
                f"supplier {supplier} would be left with no default variant — "
                "set defaultForSupplier true on one of its variants"
            )
    return problems


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
    # GST unless the caller says otherwise. Left out, Loaded files the item
    # as Exempt (sort order 0) — what happened to all four Aug 2026 creates.
    item.setdefault("globalSalesTaxSortOrder", _GST_SORT_ORDER)
    suppliers = item.get("suppliers") or []
    problems = [
        f"suppliers[{i}] {why}"
        for i, why in ((i, _bad_variant(e)) for i, e in enumerate(suppliers))
        if why
    ] + _default_problems({}, _defaults_by_supplier(suppliers))
    if problems:
        return {"error": "nothing written — " + "; ".join(problems)}
    # The basis is the raw item: names on the card are looked up only while
    # previewing, so fingerprinting the card itself could never match on the
    # approved run (set_variant_unit failed 4 of 4 that way, 18 Oct 2026).
    preview(lambda: _create_card(item, venue, call_api), basis={"create": item})
    log(f"creating stock item '{item.get('name')}'")
    out = call_api("loadedhub", "create_stock_item_raw", {"venue": venue, "item": item})
    if _err(out):
        return {"error": _err(out), "attempted": item.get("name")}
    new_id = out.get("id") if isinstance(out, dict) else None
    return {
        "result": "created",
        "name": item.get("name"),
        "item_id": new_id,
        "sales_tax": (
            "GST"
            if item.get("globalSalesTaxSortOrder") == _GST_SORT_ORDER
            else "sort order " + str(item.get("globalSalesTaxSortOrder"))
        ),
    }


# --------------------------------------------------------------- update ----


def _update(params, venue, call_api, log):
    item_id = params.get("item_id")
    if not item_id:
        return {"error": "op 'update' needs item_id — look the item up with get_stock"}
    raw_changes = params.get("changes")
    raw_variants = params.get("variant_changes")
    raw_add = params.get("add_suppliers")
    raw_remove = params.get("remove_variants")
    changes = _obj(raw_changes) if raw_changes not in (None, "") else {}
    variant_changes = _lst(raw_variants) if raw_variants not in (None, "") else []
    add_suppliers = _lst(raw_add) if raw_add not in (None, "") else []
    remove_ids = [
        str(x).strip()
        for x in (_lst(raw_remove) if raw_remove not in (None, "") else []) or []
        if str(x).strip()
    ]
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
    if not (changes or variant_changes or add_suppliers or remove_ids):
        return {
            "error": "nothing to change — pass changes, variant_changes, add_suppliers "
            "or remove_variants"
        }

    item = call_api(
        "loadedhub", "get_stock_item_full", {"venue": venue, "item_id": item_id}
    )
    if not isinstance(item, dict) or item.get("error"):
        return {"error": _err(item) or f"stock item {item_id} not found"}

    unpaired = [
        f"{unit} needs {ratio} in the same changes"
        for unit, ratio in _UNIT_PAIRS
        if any(_ALIASES.get(k, k) == unit for k in changes)
        and not any(_ALIASES.get(k, k) == ratio for k in changes)
    ]
    bad_new = [
        f"add_suppliers[{i}] {why}"
        for i, why in ((i, _bad_variant(e)) for i, e in enumerate(add_suppliers))
        if why
    ]
    if unpaired or bad_new:
        return {
            "error": "nothing written — " + "; ".join(unpaired + bad_new),
            "item_id": item_id,
        }

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
    defaults_before = _defaults_by_supplier(variants)
    variants_changed = 0
    variant_edits = []  # (variant, field, value) to check after the write
    variant_before = []  # (variant as it was, field, old value) for the card
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
            field = _VARIANT_ALIASES.get(key, key)
            if field not in target:
                skipped[f"variant {target.get('id')}.{key}"] = (
                    "Loaded has no such field on a variant"
                )
                continue
            if target.get(field) != value:
                variant_before.append((dict(target), field, target.get(field)))
                target[field] = value
                variants_changed += 1
                variant_edits.append((target.get("id"), field, value))
    for entry in add_suppliers:
        variants.append(entry)
        variants_changed += 1
    # Removing a variant: Loaded's item PUT drops any variant not sent.
    removed = [v for v in variants if isinstance(v, dict) and v.get("id") in remove_ids]
    for vid in remove_ids:
        if not any(v.get("id") == vid for v in removed):
            skipped[f"remove variant {vid}"] = "no such variant"
    if removed:
        variants = [v for v in variants if v not in removed]
        variants_changed += len(removed)
    item["suppliers"] = variants
    problems = _default_problems(defaults_before, _defaults_by_supplier(variants))
    if problems:
        return {"error": "nothing written — " + "; ".join(problems), "item_id": item_id}

    if not changed and not variants_changed:
        return {
            "item_id": item_id,
            "name": item.get("name"),
            "result": "no differences — nothing written",
            "skipped": skipped,
        }

    preview(
        lambda: _update_card(
            item,
            changed,
            variant_before,
            variant_edits,
            add_suppliers,
            skipped,
            venue,
            call_api,
            removed,
        ),
        basis={
            "item_id": item_id,
            "changed": changed,
            "variants": [[vid, f, v] for vid, f, v in variant_edits],
            "variants_before": [[b.get("id"), f, old] for b, f, old in variant_before],
            "add": add_suppliers,
            "remove": sorted(v.get("id") for v in removed),
        },
    )
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
    result = {
        "item_id": item_id,
        "name": item.get("name"),
        "changed": changed,
        "variants_changed": variants_changed,
        "skipped": skipped,
        "result": "updated",
    }

    # Read it back: a 200 is not proof Loaded kept every field or variant.
    after = call_api(
        "loadedhub", "get_stock_item_full", {"venue": venue, "item_id": item_id}
    )
    if not isinstance(after, dict) or _err(after):
        result["result"] = "updated — could not read the item back to confirm"
        return result
    lost = [f for f, ch in changed.items() if after.get(f) != ch["to"]]
    after_variants = {
        v.get("id"): v for v in after.get("suppliers") or [] if isinstance(v, dict)
    }
    for vid, field, value in variant_edits:
        if (after_variants.get(vid) or {}).get(field) != value:
            lost.append(f"variant {vid}.{field}")
    for v in removed:
        if v.get("id") in after_variants:
            lost.append(f"removing variant {v.get('stockCode') or v.get('id')}")
    for entry in add_suppliers:
        kept = any(
            v.get("supplierId") == entry.get("supplierId")
            and str(v.get("stockCode")) == str(entry.get("stockCode"))
            and v.get("unitId") == entry.get("unitId")
            for v in after_variants.values()
        )
        if not kept:
            lost.append(
                f"new variant {entry.get('supplierId')}/{entry.get('stockCode')}"
            )
    if lost:
        result["not_kept_by_loaded"] = lost
        result["result"] = "updated — but Loaded did not keep: " + ", ".join(lost)
    return result


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
    preview(
        lambda: _variant_unit_card(variant_id, unit_id, venue, call_api),
        basis={"variant_id": variant_id, "unit_id": unit_id},
    )
    log(f"variant {variant_id} -> unit {unit_id}")
    out = call_api(
        "loadedhub",
        "update_variant_unit_raw",
        {"venue": venue, "variant_id": variant_id, "unit_id": unit_id},
    )
    if _err(out):
        return {"error": _err(out)}
    return {"result": "updated", "variant_id": variant_id, "unit_id": unit_id}


# ---------------------------------------------------- fix_received_unit ----
#
# The unit on an item's RECEIVED invoice lines, and on its supplier variants,
# made right in one approved write. Why it exists (thread bf840172, 18 Oct
# 2026): a Count item (80g Potato Bun, base unit Each) had both M H Luxe
# variants in the Weight unit "80 Grams" (ratio 0.08); six received invoices
# booked 20 × $0.90 in that unit, Loaded priced the bun at $0.90 / 0.08 =
# $11.25 each, and a bacon-and-egg roll costed $13.86. Nothing could fix the
# received lines but a person in Loaded, invoice by invoice.
#
# Loaded has no line edit: a received invoice is changed by re-PUTting the
# whole detail (it stays received — the GST repair of 16 Oct did 64 this way).
# Only the unit fields move: the line's `unit` (name), `linkedUnitId` and
# `linkedUnitRatio`. Quantity, unit cost and totals are untouched, so the
# money on the invoice does not change — the price per base unit does.


def _unit_rows(venue, call_api):
    out = {}
    for u in _lst(call_api("loadedhub", "get_stock_units", {"venue": venue})) or []:
        if isinstance(u, dict) and u.get("id"):
            out[u["id"]] = u
    return out


def _find_unit(units, text):
    """A unit by id or (case-insensitive) name; None when unknown."""
    if not text:
        return None
    if text in units:
        return units[text]
    want = str(text).strip().lower()
    for u in units.values():
        if str(u.get("name") or "").strip().lower() == want:
            return u
    return None


def _iso_date(text):
    return str(text or "")[:10]


def _invoice_date(detail):
    return _iso_date(detail.get("issuedAt") or detail.get("receivedAt"))


def _fix_received_unit(params, venue, call_api, log):
    item_id = params.get("item_id")
    unit_id = params.get("unit_id")
    if not _looks_like_id(item_id) or not _looks_like_id(unit_id):
        return {
            "error": (
                "op 'fix_received_unit' needs item_id and unit_id — the full "
                "36-character ids from get_stock (units under view 'reference')"
            )
        }
    include_reconciled = bool(params.get("include_reconciled"))
    also_variants = params.get("also_variants") is not False

    units = _unit_rows(venue, call_api)
    target = units.get(unit_id)
    if not target:
        return {"error": f"unit {unit_id} is not one of this venue's units"}
    from_unit = (
        _find_unit(units, params.get("from_unit")) if params.get("from_unit") else None
    )
    if params.get("from_unit") and not from_unit:
        return {
            "error": f"from_unit {params.get('from_unit')!r} is not one of this venue's units"
        }

    item = call_api(
        "loadedhub", "get_stock_item_full", {"venue": venue, "item_id": item_id}
    )
    if not isinstance(item, dict) or _err(item):
        return {"error": _err(item) or f"stock item {item_id} not found"}
    item_name = item.get("name") or item_id

    # --- which invoices: an explicit list, or the received feed for a period
    invoice_ids = params.get("invoice_ids")
    if isinstance(invoice_ids, str):
        invoice_ids = [x.strip() for x in invoice_ids.split(",") if x.strip()]
    if not invoice_ids:
        period = str(params.get("period") or "last 12 months").strip()
        resolve_args = {"query": period}
        if params.get("venue_id"):
            resolve_args["venue_id"] = params["venue_id"]
        resolved = call_api("norm", "resolve_dates", resolve_args)
        data = (
            resolved.get("data")
            if isinstance(resolved, dict) and "data" in resolved
            else resolved
        )
        window = data.get("window") if isinstance(data, dict) else None
        if not isinstance(window, dict) or not window.get("start"):
            return {"error": "Could not resolve the period " + repr(period)}
        feed = call_api(
            "loadedhub",
            "get_received_invoices",
            {"venue": venue, "from": str(window["start"]), "to": str(window["end"])},
        )
        if _err(feed):
            return {"error": "received invoices: " + _err(feed)}
        invoice_ids = []
        for inv in _lst(feed) or []:
            if not isinstance(inv, dict):
                continue
            for ln in inv.get("lines") or []:
                if not isinstance(ln, dict):
                    continue
                if (ln.get("StockItemId") or ln.get("itemId")) != item_id:
                    continue
                name = str(ln.get("unitName") or "")
                if (
                    from_unit
                    and name.strip().lower()
                    != str(from_unit.get("name") or "").strip().lower()
                ):
                    continue
                if (
                    not from_unit
                    and name.strip().lower()
                    == str(target.get("name") or "").strip().lower()
                ):
                    continue
                if inv.get("id") and inv["id"] not in invoice_ids:
                    invoice_ids.append(inv["id"])
                break
        if not invoice_ids:
            return {
                "result": "nothing to fix",
                "item": item_name,
                "note": f"no received invoice in the period has {item_name} in another unit",
            }

    # --- each invoice's detail: the lines that will change
    plans = []  # (detail, [line dicts to change], number, date)
    skipped = []
    for iid in invoice_ids:
        detail = call_api(
            "loadedhub", "get_invoice_detail", {"venue": venue, "invoice_id": iid}
        )
        if not isinstance(detail, dict) or _err(detail):
            skipped.append({"invoice_id": iid, "why": _err(detail) or "not found"})
            continue
        number = detail.get("referenceNumber") or iid[:8]
        if not detail.get("isReceived"):
            skipped.append(
                {
                    "invoice": number,
                    "why": "not received — open it with the receive tool instead",
                }
            )
            continue
        if detail.get("reconciled") and not include_reconciled:
            skipped.append(
                {
                    "invoice": number,
                    "why": "reconciled against a statement — pass include_reconciled to change it",
                }
            )
            continue
        lines = []
        for ln in detail.get("lines") or []:
            if not isinstance(ln, dict) or ln.get("deletedAt"):
                continue
            if ln.get("linkedItemId") != item_id or ln.get("linkedUnitId") == unit_id:
                continue
            if from_unit and ln.get("linkedUnitId") != from_unit.get("id"):
                continue
            lines.append(ln)
        if not lines:
            skipped.append(
                {"invoice": number, "why": "no line of this item in another unit"}
            )
            continue
        plans.append((detail, lines, number, _invoice_date(detail)))

    # --- the variants in the old unit(s)
    old_unit_ids = {
        ln.get("linkedUnitId") for _d, lines, _n, _dt in plans for ln in lines
    }
    if from_unit:
        old_unit_ids.add(from_unit.get("id"))
    variants = []
    if also_variants:
        for v in item.get("suppliers") or []:
            if (
                isinstance(v, dict)
                and v.get("id")
                and v.get("unitId") in old_unit_ids
                and v.get("unitId") != unit_id
            ):
                variants.append(v)

    if not plans and not variants:
        return {"result": "nothing to fix", "item": item_name, "skipped": skipped}

    # --- the card the person approves, and the basis that must still hold.
    # Unit names come from the units already read; supplier names are looked
    # up only while previewing (the card is the only thing that needs them).
    suppliers = {}
    if previewing():
        for x in _lst(call_api("loadedhub", "get_suppliers", {"venue": venue})) or []:
            if isinstance(x, dict):
                suppliers[x.get("id")] = x.get("name")
    names = ({uid: u.get("name") for uid, u in units.items()}, suppliers)
    unit_name = target.get("name") or unit_id

    def unit_label(uid):
        return (units.get(uid) or {}).get("name") or str(uid)[:8]

    changes = []
    for _detail, lines, number, date in plans:
        for ln in lines:
            qty = ln.get("quantityReceived")
            cost = ln.get("unitCostExclTax")
            cost_text = (
                ("$" + format(cost, ".2f"))
                if isinstance(cost, (int, float))
                else str(cost)
            )
            changes.append(
                {
                    "field": f"Invoice {number} · {date}",
                    "before": f"{qty} × {unit_label(ln.get('linkedUnitId'))} @ {cost_text}",
                    "after": f"{qty} × {unit_name} @ {cost_text}",
                }
            )
    for v in variants:
        changes.append(
            {
                "field": "Supplier variant · " + _variant_name(v, names),
                "before": unit_label(v.get("unitId")),
                "after": unit_name,
            }
        )
    warnings = [
        f"{sk.get('invoice') or sk.get('invoice_id')}: {sk['why']}" for sk in skipped
    ]
    preview(
        {
            "title": "Fix the unit on received invoice lines",
            "target": item_name,
            "venue": venue,
            "changes": changes,
            "warnings": warnings,
        },
        basis={
            "item_id": item_id,
            "unit_id": unit_id,
            "lines": [
                [
                    d.get("id"),
                    ln.get("id"),
                    ln.get("linkedUnitId"),
                    ln.get("linkedUnitRatio"),
                    ln.get("quantityReceived"),
                    ln.get("unitCostExclTax"),
                ]
                for d, lines, _n, _dt in plans
                for ln in lines
            ],
            "variants": [[v.get("id"), v.get("unitId")] for v in variants],
        },
    )

    # --- the writes: one PUT per invoice, read back; then the variants
    results = []
    for detail, lines, number, date in plans:
        for ln in lines:
            ln["unit"] = unit_name
            ln["linkedUnitId"] = unit_id
            ln["linkedUnitRatio"] = target.get("ratio")
        log(f"re-PUT received invoice {number}: {len(lines)} line(s) -> {unit_name}")
        out = call_api(
            "loadedhub",
            "update_invoice_raw",
            {"venue": venue, "invoice_id": detail.get("id"), "invoice": detail},
        )
        entry = {"invoice": number, "date": date, "lines_changed": len(lines)}
        if _err(out):
            entry["error"] = _err(out)
            results.append(entry)
            continue
        after = call_api(
            "loadedhub",
            "get_invoice_detail",
            {"venue": venue, "invoice_id": detail.get("id")},
        )
        if isinstance(after, dict) and not _err(after):
            still_received = bool(after.get("isReceived"))
            adds_up = True
            try:
                adds_up = (
                    abs(
                        float(after.get("subtotal") or 0)
                        + float(after.get("taxAmount") or 0)
                        - float(after.get("total") or 0)
                    )
                    <= 0.05
                )
            except (TypeError, ValueError):
                adds_up = True
            kept = sum(
                1
                for l2 in after.get("lines") or []
                if isinstance(l2, dict)
                and l2.get("linkedItemId") == item_id
                and l2.get("linkedUnitId") == unit_id
            )
            entry.update(
                {
                    "still_received": still_received,
                    "adds_up": adds_up,
                    "lines_now_in_unit": kept,
                }
            )
            if not still_received or not adds_up or kept < len(lines):
                entry["warning"] = (
                    "Loaded kept the invoice differently from what was sent — check it"
                )
        results.append(entry)

    variant_results = []
    for v in variants:
        out = call_api(
            "loadedhub",
            "update_variant_unit_raw",
            {"venue": venue, "variant_id": v.get("id"), "unit_id": unit_id},
        )
        variant_results.append(
            {
                "variant_id": v.get("id"),
                "supplier": _variant_name(v, names),
                **({"error": _err(out)} if _err(out) else {"result": "updated"}),
            }
        )

    return {
        "result": "updated",
        "item": item_name,
        "unit": unit_name,
        "invoices": results,
        "variants": variant_results,
        "skipped": skipped,
        "note": (
            "quantities and costs are unchanged; Loaded's Live price for the item "
            "follows the latest received invoice, so recipe costs update from here"
        ),
    }


# ---------------------------------------------------- catalogue ops ----
#
# The Catalogue page's operations (Loaded Stock, 23 Oct 2026;
# docs/plans/stock-catalogue-page.md). Every Loaded call was read in Loaded's
# own source and tried on Loaded's test company before it was used here:
#
#   retire / restore  PATCH /wapi/r/StockItems/{id} DatestampRemoved — what
#                     Loaded's item screen sends. (The stock API's PUT also
#                     retires, but answers 500 and drops every variant whose
#                     supplier is retired.)
#   merge             item_id INTO into_item_id: its received invoice lines,
#                     supplier variants, open order lines, recipe lines and
#                     POS links move to the kept item; then it is retired.
#                     Loaded has no item merge of its own. (Owner, 23 Oct:
#                     past invoices move too, so cost history joins up.)
#   update_unit       PUT /wapi/r/StockUnits/{id}: name and size.
#   delete_unit /     the same PUT, datestampDeleted set / null. Loaded has no
#   restore_unit      in-use check, so Norm refuses to delete a unit anything
#                     still uses — merge it instead.
#   merge_units       unit_id INTO into_unit_id, the same type AND size only
#                     (different sizes would change what every quantity
#                     means): variants, items' counting / ordering / minimum
#                     units, open order lines, recipe lines and yields, and POS
#                     links move; then it is deleted. Past invoices keep it —
#                     the same size, so no cost changes.
#
# The older web API (/wapi) is reached with the venue's Loaded Legacy login.
#
# A merge can need more Loaded calls than one run may make: a staple sits on
# hundreds of received invoices. It stops at _BUDGET and says what is left;
# the same merge run again carries on, because each step re-reads what still
# points at the old record. The old record is retired / deleted last, so a
# stopped merge leaves nothing pointing at a retired record.

_LL = "loaded_legacy"
_EMPTY_ID = "00000000-0000-0000-0000-000000000000"
#: Loaded calls one run spends before it stops with what is left (cap 200).
_BUDGET = 180
#: Received invoices are searched back to here.
_HISTORY_FROM = "2000-01-01T00:00:00Z"
#: Rows a card lists one by one before it sums up the rest.
_CARD_ROWS = 25
#: Norm marks non-measurement units deleted on the way in (invoice_units);
#: in Loaded they are live.
_HIDDEN_UNIT_MARK = "1970-01-01T00:00:00+00:00"


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _tomorrow_iso():
    t = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)
    return t.isoformat()[:19] + "Z"


def _counted(call_api):
    """call_api, counted: a merge stops itself before the sandbox's cap."""
    used = [0]

    def call(connector, action, args):
        used[0] += 1
        return call_api(connector, action, args)

    return call, used


def _rows(value):
    """A list payload, or the list inside {data | items | results | value}."""
    if isinstance(value, dict):
        for key in ("data", "items", "results", "value"):
            if isinstance(value.get(key), list):
                return value[key]
    return value if isinstance(value, list) else []


def _same_text(a, b):
    return " ".join(str(a or "").lower().split()) == " ".join(
        str(b or "").lower().split()
    )


def _item_any(venue, call, item_id):
    """One item, retired or not, with its variants; None when not found."""
    item = call(
        "loadedhub", "get_stock_item_any_raw", {"venue": venue, "item_id": item_id}
    )
    if isinstance(item, dict) and item.get("id") and not _err(item):
        return item
    return None


def _patch_item(venue, call, item_id, fields):
    """Set fields on one item through the older web API (Loaded's own names);
    nothing else on the item is touched."""
    return call(
        _LL,
        "patch_stock_item",
        {
            "venue": venue,
            "item_id": item_id,
            "patch": [{"op": "replace", "path": k, "value": v} for k, v in fields],
        },
    )


def _shared(item):
    shared = item.get("sharedItemId")
    return bool(item.get("isShared")) or bool(shared and shared != _EMPTY_ID)


def _type_name(unit_type):
    return {0: "weight", 1: "volume", 2: "count"}.get(unit_type, str(unit_type))


def _recipe_uses(venue, call, line_match, version_match=None):
    """[(recipe, [versions])] of the recipes Loaded keeps whose versions match
    — a line (line_match) or the version itself (version_match)."""
    raw = call("loadedhub", "get_all_recipes", {"venue": venue})
    if _err(raw) or not isinstance(_rows(raw), list):
        return None, "recipes: " + (_err(raw) or "no list came back")
    out = []
    for r in _rows(raw):
        if not isinstance(r, dict) or not r.get("id") or r.get("deletedAt"):
            continue
        hits = []
        for v in r.get("versions") or []:
            if not isinstance(v, dict) or not v.get("id"):
                continue
            lines = [ln for ln in v.get("lines") or [] if isinstance(ln, dict)]
            if any(not ln.get("deletedAt") and line_match(ln) for ln in lines) or (
                version_match and version_match(v)
            ):
                hits.append(v)
        if hits:
            out.append((r, hits))
    out.sort(key=lambda rv: str(rv[0].get("name") or "").lower())
    return out, None


def _link_uses(venue, call, match):
    raw = call(_LL, "list_pos_item_links", {"venue": venue})
    if _err(raw):
        return None, "POS links: " + _err(raw)
    return [
        ln for ln in _rows(raw) if isinstance(ln, dict) and ln.get("id") and match(ln)
    ], None


def _open_orders_for_item(venue, call, item_id):
    """[{id, number}] of the open (not received, not deleted) orders with a
    line of this item."""
    raw = call(
        _LL,
        "find_purchase_order_lines",
        {"venue": venue, "filter": f"StockItemId eq {item_id}"},
    )
    if _err(raw):
        return None, "orders: " + _err(raw)
    out = []
    seen = set()
    for ln in _rows(raw):
        if not isinstance(ln, dict):
            continue
        if ln.get("purchaseOrderDatestampReceived") or ln.get(
            "purchaseOrderDatestampDeleted"
        ):
            continue
        po = ln.get("purchaseOrderId")
        if po and po not in seen:
            seen.add(po)
            out.append(
                # Loaded sends order numbers as numbers (production, 23 Oct).
                {"id": po, "number": str(ln.get("purchaseOrderOrderNumber") or po[:8])}
            )
    return out, None


def _invoices_for_item(venue, call, item_id):
    """[{id, number, date, lines, supplier}] — every received invoice with a
    line of this item."""
    raw = call(
        "loadedhub",
        "get_received_for_item_raw",
        {
            "venue": venue,
            "item_id": item_id,
            "from": _HISTORY_FROM,
            "to": _tomorrow_iso(),
        },
    )
    if _err(raw):
        return None, "received invoices: " + _err(raw)
    out = []
    for inv in _rows(raw):
        if not isinstance(inv, dict) or not inv.get("id"):
            continue
        n = sum(
            1
            for ln in inv.get("lines") or []
            if isinstance(ln, dict) and ln.get("itemId") == item_id
        )
        if n:
            out.append(
                {
                    "id": inv["id"],
                    "number": str(inv.get("invoiceNumber") or inv["id"][:8]),
                    "date": str(inv.get("receivedAt") or "")[:10],
                    "lines": n,
                    "supplier": inv.get("supplierName"),
                }
            )
    out.sort(key=lambda i: (i["date"], i["number"]))
    return out, None


def _strip_img_src(html):
    """<img ... src="..."> → <img ...> (the sandbox has no re). As
    record_recipe sends a recipe's notes."""
    out, i = [], 0
    while True:
        j = html.find("<img", i)
        if j < 0:
            out.append(html[i:])
            return "".join(out)
        end = html.find(">", j)
        if end < 0:
            out.append(html[i:])
            return "".join(out)
        out.append(html[i:j])
        tag = html[j : end + 1]
        s = tag.find(' src="')
        if s >= 0:
            e = tag.find('"', s + 6)
            if e >= 0:
                tag = tag[:s] + tag[e + 1 :]
        out.append(tag)
        i = end + 1


def _web_recipe(rec):
    """The recipe as Loaded's web app nests it in a version save (the same as
    record_recipe): no versions, images as JSON text without their src."""
    w = {k: v for k, v in rec.items() if k not in ("versions", "currentVersion")}
    images = rec.get("imagesJson")
    if isinstance(images, str):
        try:
            images = json.loads(images) if images else []
        except ValueError:
            images = []
    clean = []
    for im in images if isinstance(images, list) else []:
        if isinstance(im, dict):
            im = {k: v for k, v in im.items() if k != "src"}
        clean.append(im)
    w["imagesJson"] = json.dumps(clean)
    main = rec.get("mainImageJson")
    if isinstance(main, str) and main:
        try:
            main = json.loads(main)
        except ValueError:
            main = None
    if isinstance(main, dict):
        w["mainImageJson"] = json.dumps({k: v for k, v in main.items() if k != "src"})
    if isinstance(w.get("notes"), str):
        w["notes"] = _strip_img_src(w["notes"])
    return w


def _save_recipe_versions(
    venue, call, used, recipe, versions, edit_lines, edit_version=None
):
    """Re-save each version with edit_lines(lines) -> (new lines, changed?);
    returns (saved, failed, stopped)."""
    saved, failed = 0, []
    if recipe.get("isShared"):
        return (
            0,
            [
                {
                    "recipe": recipe.get("name"),
                    "error": "shared with other companies — Loaded's older API won't "
                    "save it; change it in Loaded",
                }
            ],
            False,
        )
    if used[0] + 1 + 2 * len(versions) > _BUDGET:
        return 0, [], True
    rec = call(_LL, "get_recipe", {"venue": venue, "recipe_id": recipe.get("id")})
    if _err(rec) or not isinstance(rec, dict) or not rec.get("id"):
        return (
            0,
            [{"recipe": recipe.get("name"), "error": _err(rec) or "not found"}],
            False,
        )
    for v in versions:
        ver = call(
            _LL, "get_recipe_version", {"venue": venue, "version_id": v.get("id")}
        )
        if _err(ver) or not isinstance(ver, dict):
            failed.append(
                {"recipe": recipe.get("name"), "error": _err(ver) or "no version"}
            )
            continue
        lines, changed = edit_lines(
            [ln for ln in ver.get("lines") or [] if isinstance(ln, dict)]
        )
        sent = dict(ver)
        sent["lines"] = lines
        if edit_version and edit_version(sent):
            changed = True
        if not changed:
            continue
        sent["recipe"] = _web_recipe(rec)
        out = call(
            _LL,
            "save_recipe_version",
            {
                "venue": venue,
                "version_id": ver.get("id"),
                "valid_until": "",
                "body": sent,
            },
        )
        if _err(out):
            failed.append({"recipe": recipe.get("name"), "error": _err(out)})
        else:
            saved += 1
    return saved, failed, False


def _more(rows, what):
    """Card rows, at most _CARD_ROWS of them, then one that sums up the rest."""
    if len(rows) <= _CARD_ROWS:
        return rows
    rest = len(rows) - _CARD_ROWS + 1
    return rows[: _CARD_ROWS - 1] + [
        {"field": f"… and {rest} more {what}", "after": ""}
    ]


# ------------------------------------------------------ retire / restore ----


def _retire_restore(params, venue, call_api, log, retire):
    call, _used = _counted(call_api)
    item_id = params.get("item_id")
    if not _looks_like_id(item_id):
        return {"error": "say which stock item — item_id, the full id from get_stock"}
    item = _item_any(venue, call, item_id)
    if not item:
        return {"error": f"stock item {item_id} not found at this venue"}
    name = item.get("name")
    gone = bool(item.get("datestampRemoved"))
    if retire and gone:
        return {"result": "already retired", "item": name}
    if not retire and not gone:
        return {"result": "already active", "item": name}
    if _shared(item) and retire:
        return {
            "error": f"{name} is shared with other companies — retiring it here would "
            "remove their copies too; retire it in Loaded"
        }

    warnings = []
    if retire:
        recipes, _e1 = _recipe_uses(venue, call, lambda ln: ln.get("itemId") == item_id)
        links, _e2 = _link_uses(
            venue, call, lambda ln: ln.get("stockItemId") == item_id
        )
        orders, _e3 = _open_orders_for_item(venue, call, item_id)
        if recipes:
            names = ", ".join(str(r.get("name")) for r, _v in recipes[:8])
            more = f" and {len(recipes) - 8} more" if len(recipes) > 8 else ""
            warnings.append(
                f"{len(recipes)} recipe(s) use it ({names}{more}). They keep it, at "
                "its last cost — merge it into another item instead to move them."
            )
        if links:
            warnings.append(
                f"{len(links)} POS item(s) take it off stock when they sell — merge it "
                "into another item instead to move them."
            )
        if orders:
            warnings.append(
                f"{len(orders)} open order(s) include it: "
                + ", ".join(o["number"] for o in orders[:8])
            )
    preview(
        lambda: {
            "title": "Retire a stock item" if retire else "Restore a stock item",
            "target": name,
            "venue": venue,
            "changes": [
                {
                    "field": "Status",
                    "before": "active" if retire else "retired",
                    "after": "retired" if retire else "active",
                }
            ],
            "warnings": warnings,
        },
        basis={"item_id": item_id, "retired": gone},
    )
    log(("retiring " if retire else "restoring ") + str(name))
    out = _patch_item(
        venue, call, item_id, [("DatestampRemoved", _now_iso() if retire else None)]
    )
    if _err(out):
        return {"error": f"Loaded refused: {_err(out)}", "item": name}
    after = _item_any(venue, call, item_id)
    now_gone = bool(after and after.get("datestampRemoved"))
    result = {
        "result": "retired" if retire else "restored",
        "item": name,
        "item_id": item_id,
    }
    if after is None or now_gone != retire:
        result["warning"] = "Loaded answered, but reading the item back doesn't show it"
    return result


# --------------------------------------------------------------- merge ----


def _swap_item_lines(src_id, dst):
    """A recipe version's lines with each line of src replaced by a new line of
    dst in its place — Loaded never changes an existing line's item."""

    def edit(lines):
        out, changed = [], False
        for ln in lines:
            if (
                not ln.get("deletedAt")
                and (ln.get("stockItemId") or ln.get("itemId")) == src_id
            ):
                new = {
                    k: v
                    for k, v in ln.items()
                    if k not in ("id", "currentPrice", "stockDeletedAt")
                }
                new.update(
                    {
                        "id": _EMPTY_ID,
                        "stockItemId": dst.get("id"),
                        "stockItemName": dst.get("name"),
                    }
                )
                out.append(new)
                changed = True
            else:
                out.append(ln)
        return out, changed

    return edit


def _merge(params, venue, call_api, log):
    call, used = _counted(call_api)
    src_id = params.get("item_id")
    dst_id = params.get("into_item_id")
    if not _looks_like_id(src_id) or not _looks_like_id(dst_id):
        return {
            "error": "op 'merge' needs item_id (the item merged away) and "
            "into_item_id (the item kept) — full ids from get_stock"
        }
    if src_id == dst_id:
        return {"error": "an item can't be merged into itself"}
    dst = _item_any(venue, call, dst_id)
    if not dst or dst.get("datestampRemoved"):
        return {"error": "the item to keep isn't a live stock item at this venue"}
    src = _item_any(venue, call, src_id)
    if not src:
        return {"error": f"stock item {src_id} not found at this venue"}
    sname, dname = src.get("name"), dst.get("name")
    if src.get("datestampRemoved"):
        return {"result": "already merged", "item": sname, "into": dname}
    problems = []
    if src.get("unitType") != dst.get("unitType"):
        problems.append(
            f"{sname} is counted by {_type_name(src.get('unitType'))} and {dname} by "
            f"{_type_name(dst.get('unitType'))} — the quantities on its invoices and "
            "recipes would mean something else"
        )
    if src.get("systemItem") or dst.get("systemItem"):
        problems.append("Loaded's own system items can't be merged")
    if _shared(src):
        # Merging it away retires it, and retiring a shared item removes its
        # copies in the other companies (Loaded's StockItemsController). An
        # item merged INTO may be shared: it is only added to.
        problems.append(
            f"{sname} is shared with other companies — merge the other way round, "
            "or retire it in Loaded"
        )
    if problems:
        return {"error": "nothing changed — " + "; ".join(problems)}

    invoices, e1 = _invoices_for_item(venue, call, src_id)
    orders, e2 = _open_orders_for_item(venue, call, src_id)
    recipes, e3 = _recipe_uses(venue, call, lambda ln: ln.get("itemId") == src_id)
    links, e4 = _link_uses(venue, call, lambda ln: ln.get("stockItemId") == src_id)
    unread = [e for e in (e1, e2, e3, e4) if e]
    if unread:
        return {
            "error": "couldn't read everything that uses the item, so nothing changed — "
            + "; ".join(unread)
        }
    variants = [
        v for v in src.get("suppliers") or [] if isinstance(v, dict) and v.get("id")
    ]

    def card():
        names = _names(venue, call_api)
        changes = []
        for v in variants:
            changes.append(
                {
                    "field": "Supplier variant · " + _variant_name(v, names),
                    "before": sname,
                    "after": dname,
                }
            )
        if invoices:
            n = sum(i["lines"] for i in invoices)
            changes.append(
                {
                    "field": "Received invoices",
                    "before": f"{n} line(s) on {len(invoices)} invoice(s), "
                    f"{invoices[0]['date']} to {invoices[-1]['date']}",
                    "after": f"booked as {dname}",
                }
            )
        changes += _more(
            [
                {"field": f"Open order {o['number']}", "before": sname, "after": dname}
                for o in orders
            ],
            "orders",
        )
        changes += _more(
            [
                {
                    "field": f"Recipe · {r.get('name')}",
                    "before": sname,
                    "after": dname
                    + (f" (all {len(vs)} versions)" if len(vs) > 1 else ""),
                }
                for r, vs in recipes
            ],
            "recipes",
        )
        if links:
            changes.append(
                {
                    "field": "POS links",
                    "before": f"{len(links)} POS item(s) use {sname}",
                    "after": f"they use {dname}",
                }
            )
        changes.append({"field": sname, "before": "active", "after": "retired"})
        warnings = []
        if invoices:
            warnings.append(
                f"Past purchases move with the invoices: costs and cost of goods count "
                f"them as {dname} from now on."
            )
        shared = [str(r.get("name")) for r, _vs in recipes if r.get("isShared")]
        if shared:
            warnings.append(
                "Loaded won't save recipes shared with other companies here: "
                + ", ".join(shared)
                + " — change those in Loaded."
            )
        warnings.append(
            "Past stocktakes, stocktake templates, order templates and tenders keep "
            f"{sname}."
        )
        return {
            "title": "Merge stock items",
            "target": f"{sname} into {dname}",
            "venue": venue,
            "changes": changes,
            "warnings": warnings,
        }

    preview(
        card,
        basis={
            "item": src_id,
            "into": dst_id,
            "invoices": sorted(i["id"] for i in invoices),
            "orders": sorted(o["id"] for o in orders),
            "recipes": sorted(v.get("id") for _r, vs in recipes for v in vs),
            "links": sorted(ln.get("id") for ln in links),
            "variants": sorted(v.get("id") for v in variants),
        },
    )
    log(f"merging {sname} into {dname}")
    moved = {
        "invoice_lines": 0,
        "invoices": 0,
        "variants": 0,
        "orders": 0,
        "recipe_versions": 0,
        "pos_links": 0,
    }
    failed = []

    def stopped(left):
        return {
            "result": "in progress",
            "item": sname,
            "into": dname,
            "moved": moved,
            "remaining": left,
            "failed": failed,
            "continue": True,
            "note": f"Loaded allows so many calls per run. Run the same merge again to "
            f"carry on — it picks up what still points at {sname}, and retires it last.",
        }

    # 1. Received invoices: the whole invoice back with the lines re-pointed.
    for k, inv in enumerate(invoices):
        if used[0] + 2 > _BUDGET:
            return stopped({"invoices": len(invoices) - k})
        detail = call(
            "loadedhub", "get_invoice_detail", {"venue": venue, "invoice_id": inv["id"]}
        )
        if not isinstance(detail, dict) or _err(detail):
            failed.append(
                {"invoice": inv["number"], "error": _err(detail) or "not found"}
            )
            continue
        n = 0
        for ln in detail.get("lines") or []:
            if (
                isinstance(ln, dict)
                and not ln.get("deletedAt")
                and ln.get("linkedItemId") == src_id
            ):
                ln["linkedItemId"] = dst_id
                n += 1
        if not n:
            continue
        out = call(
            "loadedhub",
            "update_invoice_raw",
            {"venue": venue, "invoice_id": detail.get("id"), "invoice": detail},
        )
        if _err(out):
            failed.append({"invoice": inv["number"], "error": _err(out)})
        else:
            moved["invoices"] += 1
            moved["invoice_lines"] += n

    # 2. Supplier variants: each moved whole, keeping its id.
    for k, v in enumerate(variants):
        if used[0] + 1 > _BUDGET:
            return stopped({"variants": len(variants) - k})
        out = call(
            "loadedhub",
            "move_variant_raw",
            {"venue": venue, "variant_id": v.get("id"), "item_id": dst_id},
        )
        if _err(out):
            failed.append(
                {"variant": v.get("stockCode") or v.get("id"), "error": _err(out)}
            )
        else:
            moved["variants"] += 1

    # 3. Open orders: the whole order back with the lines re-pointed.
    for k, o in enumerate(orders):
        if used[0] + 2 > _BUDGET:
            return stopped({"orders": len(orders) - k})
        po = call(
            "loadedhub",
            "get_purchase_order_detail",
            {"venue": venue, "order_id": o["id"]},
        )
        if not isinstance(po, dict) or _err(po):
            failed.append({"order": o["number"], "error": _err(po) or "not found"})
            continue
        n = 0
        for ln in po.get("lines") or []:
            if isinstance(ln, dict) and ln.get("itemId") == src_id:
                ln["itemId"] = dst_id
                ln["itemName"] = dname
                n += 1
        if not n:
            continue
        out = call(
            "loadedhub",
            "update_purchase_order_raw",
            {"venue": venue, "order_id": o["id"], "order": po},
        )
        if _err(out):
            failed.append({"order": o["number"], "error": _err(out)})
        else:
            moved["orders"] += 1

    # 4. Recipes: every version that uses it.
    for k, (r, vs) in enumerate(recipes):
        saved, bad, stop = _save_recipe_versions(
            venue, call, used, r, vs, _swap_item_lines(src_id, dst)
        )
        if stop:
            return stopped({"recipes": len(recipes) - k})
        moved["recipe_versions"] += saved
        failed.extend(bad)

    # 5. POS links, edited in place.
    for k, ln in enumerate(links):
        if used[0] + 1 > _BUDGET:
            return stopped({"pos_links": len(links) - k})
        body = dict(ln)
        body["stockItemId"] = dst_id
        out = call(
            _LL,
            "update_pos_item_link",
            {"venue": venue, "link_id": ln.get("id"), "link": body},
        )
        if _err(out):
            failed.append({"pos_link": ln.get("posIdentifier"), "error": _err(out)})
        else:
            moved["pos_links"] += 1

    # 6. Retire it — last, and only when everything moved.
    if failed:
        return {
            "result": "partly merged",
            "item": sname,
            "into": dname,
            "moved": moved,
            "failed": failed,
            "error": f"{len(failed)} change(s) failed, so {sname} was NOT retired — "
            "fix those (or run the merge again) and it will be.",
        }
    if used[0] + 2 > _BUDGET:
        return stopped({"retire": 1})
    out = _patch_item(venue, call, src_id, [("DatestampRemoved", _now_iso())])
    if _err(out):
        return {
            "result": "partly merged",
            "item": sname,
            "into": dname,
            "moved": moved,
            "error": f"everything moved, but Loaded refused to retire {sname}: {_err(out)}",
        }
    after = _item_any(venue, call, src_id)
    result = {"result": "merged", "item": sname, "into": dname, "moved": moved}
    if not (after and after.get("datestampRemoved")):
        result["warning"] = f"Loaded answered, but {sname} doesn't read back as retired"
    return result


# --------------------------------------------------------------- units ----


def _unit_gone(u):
    at = u.get("datestampDeleted")
    return bool(at) and str(at) != _HIDDEN_UNIT_MARK


def _unit_record(venue, call, unit_id):
    """The unit as the older web API stores it — what a change sends back."""
    rows = _rows(
        call(_LL, "find_stock_units", {"venue": venue, "filter": f"Id eq {unit_id}"})
    )
    return rows[0] if rows and isinstance(rows[0], dict) else None


def _items_using_unit(venue, call, unit_id):
    """(items whose own units use it [(item, [fields])], [(item, variant)]) —
    live items only."""
    raw = call("loadedhub", "get_stock_items_detail_raw", {"venue": venue})
    if _err(raw) or not isinstance(_rows(raw), list):
        return None, None, "items: " + (_err(raw) or "no list came back")
    own, variants = [], []
    for it in _rows(raw):
        if not isinstance(it, dict) or not it.get("id") or it.get("datestampRemoved"):
            continue
        fields = [
            f
            for f in ("countingUnitId", "orderingUnitId", "minimumStockOnHandUnitId")
            if it.get(f) == unit_id
        ]
        if fields:
            own.append((it, fields))
        for v in it.get("suppliers") or []:
            if isinstance(v, dict) and v.get("unitId") == unit_id:
                variants.append((it, v))
    return own, variants, None


def _unit_args(params):
    unit_id = params.get("unit_id")
    if not _looks_like_id(unit_id):
        return None, {
            "error": "say which unit — unit_id, the full id from get_stock "
            "(view 'reference', kind 'units')"
        }
    return unit_id, None


def _update_unit(params, venue, call_api, log):
    call, _used = _counted(call_api)
    unit_id, problem = _unit_args(params)
    if problem:
        return problem
    units = _unit_rows(venue, call)
    unit = units.get(unit_id)
    rec = _unit_record(venue, call, unit_id)
    if not unit or not rec:
        return {"error": f"unit {unit_id} is not one of this venue's units"}
    if rec.get("masterId"):
        return {
            "error": f"{rec.get('name')} is one of Loaded's standard units — Loaded doesn't let it be changed"
        }
    name = params.get("name")
    ratio = params.get("ratio")
    new_name = str(name).strip() if name is not None else rec.get("name")
    if not new_name:
        return {"error": "a unit needs a name"}
    try:
        new_ratio = float(ratio) if ratio not in (None, "") else float(rec.get("ratio"))
    except (TypeError, ValueError):
        return {"error": f"size {ratio!r} isn't a number"}
    if new_ratio <= 0:
        return {"error": "a unit's size must be more than 0"}
    clash = [
        u.get("name")
        for uid, u in units.items()
        if uid != unit_id and not _unit_gone(u) and _same_text(u.get("name"), new_name)
    ]
    if clash:
        return {
            "error": f"another unit is already called {clash[0]!r} — merge the two "
            "instead (op 'merge_units')"
        }
    changes = []
    if new_name != rec.get("name"):
        changes.append({"field": "Name", "before": rec.get("name"), "after": new_name})
    resized = abs(new_ratio - float(rec.get("ratio") or 0)) > 1e-12
    if resized:
        changes.append(
            {"field": "Size", "before": rec.get("ratio"), "after": new_ratio}
        )
    if not changes:
        return {"result": "no differences — nothing written", "unit": rec.get("name")}
    warnings = []
    if resized:
        own, variants, _e = _items_using_unit(venue, call, unit_id)
        n_items = len(
            {it.get("id") for it, _f in (own or [])}
            | {it.get("id") for it, _v in (variants or [])}
        )
        warnings.append(
            f"{n_items} item(s) use this unit. Everything already recorded in it — "
            "invoices, stocktakes, recipes, orders — will be read at the new size."
        )
    preview(
        lambda: {
            "title": "Change a unit",
            "target": rec.get("name"),
            "venue": venue,
            "changes": changes,
            "warnings": warnings,
        },
        basis={
            "unit": [rec.get("name"), rec.get("ratio"), rec.get("datestampDeleted")]
        },
    )
    sent = dict(rec)
    sent["name"] = new_name
    sent["ratio"] = new_ratio
    out = call(
        _LL, "update_stock_unit", {"venue": venue, "unit_id": unit_id, "unit": sent}
    )
    if _err(out):
        return {"error": f"Loaded refused: {_err(out)}", "unit": rec.get("name")}
    after = _unit_record(venue, call, unit_id) or {}
    result = {"result": "updated", "unit": new_name, "unit_id": unit_id}
    if (
        after.get("name") != new_name
        or abs(float(after.get("ratio") or 0) - new_ratio) > 1e-9
    ):
        result["warning"] = "Loaded answered, but the unit doesn't read back changed"
    return result


def _unit_in_use(venue, call, unit_id):
    """(what still uses the unit, as sentences) or (None, error)."""
    own, variants, e1 = _items_using_unit(venue, call, unit_id)
    recipes, e2 = _recipe_uses(
        venue,
        call,
        lambda ln: ln.get("unitId") == unit_id,
        lambda v: v.get("yieldUnitId") == unit_id,
    )
    links, e3 = _link_uses(venue, call, lambda ln: ln.get("unitId") == unit_id)
    unread = [e for e in (e1, e2, e3) if e]
    if unread:
        return None, "; ".join(unread)
    uses = []
    items = sorted(
        {str(it.get("name")) for it, _f in own}
        | {str(it.get("name")) for it, _v in variants}
    )
    if items:
        uses.append(
            f"{len(items)} item(s): "
            + ", ".join(items[:8])
            + (" …" if len(items) > 8 else "")
        )
    if recipes:
        uses.append(
            f"{len(recipes)} recipe(s): "
            + ", ".join(str(r.get("name")) for r, _v in recipes[:8])
        )
    if links:
        uses.append(f"{len(links)} POS link(s)")
    return uses, None


def _delete_restore_unit(params, venue, call_api, log, delete):
    call, _used = _counted(call_api)
    unit_id, problem = _unit_args(params)
    if problem:
        return problem
    rec = _unit_record(venue, call, unit_id)
    if not rec:
        return {"error": f"unit {unit_id} is not one of this venue's units"}
    name = rec.get("name")
    gone = bool(rec.get("datestampDeleted"))
    if delete and gone:
        return {"result": "already deleted", "unit": name}
    if not delete and not gone:
        return {"result": "already active", "unit": name}
    if rec.get("masterId"):
        return {
            "error": f"{name} is one of Loaded's standard units — Loaded doesn't let it be changed"
        }
    if delete:
        uses, err = _unit_in_use(venue, call, unit_id)
        if err:
            return {
                "error": "couldn't check what uses the unit, so nothing changed — "
                + err
            }
        if uses:
            return {
                "error": f"{name} is still in use — " + "; ".join(uses) + ". Merge it "
                "into another unit of the same size instead (op 'merge_units').",
                "in_use": uses,
            }
    preview(
        lambda: {
            "title": "Delete a unit" if delete else "Restore a unit",
            "target": name,
            "venue": venue,
            "changes": [
                {
                    "field": "Status",
                    "before": "active" if delete else "deleted",
                    "after": "deleted" if delete else "active",
                }
            ],
        },
        basis={"unit": unit_id, "deleted": gone},
    )
    sent = dict(rec)
    sent["datestampDeleted"] = _now_iso() if delete else None
    out = call(
        _LL, "update_stock_unit", {"venue": venue, "unit_id": unit_id, "unit": sent}
    )
    if _err(out):
        return {"error": f"Loaded refused: {_err(out)}", "unit": name}
    return {
        "result": "deleted" if delete else "restored",
        "unit": name,
        "unit_id": unit_id,
    }


def _open_order_lines_in_unit(venue, call, used, unit_id):
    """[(order detail, [lines])] of open orders with a line in the unit; None
    when the budget ran out first."""
    raw = call("loadedhub", "list_purchase_orders", {"venue": venue})
    out = []
    for po in _rows(raw):
        if not isinstance(po, dict) or po.get("isReceived") or not po.get("id"):
            continue
        if used[0] + 1 > _BUDGET - 20:
            return None
        detail = call(
            "loadedhub",
            "get_purchase_order_detail",
            {"venue": venue, "order_id": po["id"]},
        )
        if not isinstance(detail, dict) or _err(detail):
            continue
        lines = [
            ln
            for ln in detail.get("lines") or []
            if isinstance(ln, dict) and ln.get("unitId") == unit_id
        ]
        if lines:
            out.append((detail, lines))
    return out


def _merge_units(params, venue, call_api, log):
    call, used = _counted(call_api)
    src_id, problem = _unit_args(params)
    if problem:
        return problem
    dst_id = params.get("into_unit_id")
    if not _looks_like_id(dst_id):
        return {"error": "op 'merge_units' needs into_unit_id — the unit kept"}
    if src_id == dst_id:
        return {"error": "a unit can't be merged into itself"}
    units = _unit_rows(venue, call)
    src, dst = units.get(src_id), units.get(dst_id)
    if not dst or _unit_gone(dst):
        return {"error": "the unit to keep isn't a live unit at this venue"}
    if not src:
        return {"error": f"unit {src_id} is not one of this venue's units"}
    sname, dname = src.get("name"), dst.get("name")
    if _unit_gone(src):
        return {"result": "already merged", "unit": sname, "into": dname}
    try:
        same_size = abs(
            float(src.get("ratio")) - float(dst.get("ratio"))
        ) <= 1e-9 * max(1.0, abs(float(dst.get("ratio"))))
    except (TypeError, ValueError):
        same_size = False
    if src.get("stockUnitType") != dst.get("stockUnitType") or not same_size:
        return {
            "error": f"only the same kind of unit, the same size, can be merged: {sname} "
            f"is {src.get('stockUnitType')} {src.get('ratio')}, {dname} is "
            f"{dst.get('stockUnitType')} {dst.get('ratio')}. Change the items' units "
            "instead."
        }
    rec = _unit_record(venue, call, src_id)
    if not rec:
        return {"error": f"couldn't read {sname} from Loaded's older API"}
    if rec.get("masterId"):
        keep = _unit_record(venue, call, dst_id) or {}
        if keep.get("masterId"):
            return {
                "error": f"{sname} and {dname} are both Loaded's standard units, which "
                "Loaded won't delete — neither can be merged away"
            }
        return {
            "error": f"{sname} is one of Loaded's standard units, which Loaded won't "
            f"delete — merge {dname} into {sname} instead"
        }

    own, variants, e1 = _items_using_unit(venue, call, src_id)
    recipes, e2 = _recipe_uses(
        venue,
        call,
        lambda ln: ln.get("unitId") == src_id,
        lambda v: v.get("yieldUnitId") == src_id,
    )
    links, e3 = _link_uses(venue, call, lambda ln: ln.get("unitId") == src_id)
    unread = [e for e in (e1, e2, e3) if e]
    if unread:
        return {
            "error": "couldn't read everything that uses the unit, so nothing changed — "
            + "; ".join(unread)
        }
    orders = _open_order_lines_in_unit(venue, call, used, src_id)
    if orders is None:
        return {
            "error": "too many open orders to check in one run — receive or delete old ones first"
        }

    def card():
        changes = []
        changes += _more(
            [
                {
                    "field": f"Item · {it.get('name')}",
                    "before": ", ".join(
                        {
                            "countingUnitId": "counted",
                            "orderingUnitId": "ordered",
                            "minimumStockOnHandUnitId": "minimum",
                        }[f]
                        for f in fields
                    )
                    + f" in {sname}",
                    "after": dname,
                }
                for it, fields in own
            ],
            "items",
        )
        if variants:
            changes.append(
                {
                    "field": "Supplier variants",
                    "before": f"{len(variants)} in {sname}",
                    "after": dname,
                }
            )
        changes += _more(
            [
                {
                    "field": f"Open order {po.get('orderNumber')}",
                    "before": sname,
                    "after": dname,
                }
                for po, _l in orders
            ],
            "orders",
        )
        changes += _more(
            [
                {"field": f"Recipe · {r.get('name')}", "before": sname, "after": dname}
                for r, _v in recipes
            ],
            "recipes",
        )
        if links:
            changes.append(
                {
                    "field": "POS links",
                    "before": f"{len(links)} in {sname}",
                    "after": dname,
                }
            )
        changes.append({"field": sname, "before": "active", "after": "deleted"})
        return {
            "title": "Merge units",
            "target": f"{sname} into {dname}",
            "venue": venue,
            "changes": changes,
            "warnings": [
                f"Past invoices and stocktakes keep {sname}: it is the same size, so "
                "nothing they cost changes."
            ],
        }

    preview(
        card,
        basis={
            "unit": src_id,
            "into": dst_id,
            "items": sorted([it.get("id"), f] for it, fields in own for f in fields),
            "variants": sorted(v.get("id") for _it, v in variants),
            "orders": sorted(po.get("id") for po, _l in orders),
            "recipes": sorted(v.get("id") for _r, vs in recipes for v in vs),
            "links": sorted(ln.get("id") for ln in links),
        },
    )
    log(f"merging unit {sname} into {dname}")
    moved = {
        "items": 0,
        "variants": 0,
        "orders": 0,
        "recipe_versions": 0,
        "pos_links": 0,
    }
    failed = []

    def stopped(left):
        return {
            "result": "in progress",
            "unit": sname,
            "into": dname,
            "moved": moved,
            "remaining": left,
            "failed": failed,
            "continue": True,
            "note": f"Run the same merge again to carry on — it picks up what still "
            f"uses {sname}, and deletes it last.",
        }

    to_loaded = {
        "countingUnitId": "CountingUnitId",
        "orderingUnitId": "OrderingUnitId",
        "minimumStockOnHandUnitId": "MinimumStockOnHandUnitId",
    }
    for k, (it, fields) in enumerate(own):
        if used[0] + 1 > _BUDGET:
            return stopped({"items": len(own) - k})
        out = _patch_item(
            venue, call, it.get("id"), [(to_loaded[f], dst_id) for f in fields]
        )
        if _err(out):
            failed.append({"item": it.get("name"), "error": _err(out)})
        else:
            moved["items"] += 1
    for k, (it, v) in enumerate(variants):
        if used[0] + 1 > _BUDGET:
            return stopped({"variants": len(variants) - k})
        out = call(
            "loadedhub",
            "update_variant_unit_raw",
            {"venue": venue, "variant_id": v.get("id"), "unit_id": dst_id},
        )
        if _err(out):
            failed.append({"item": it.get("name"), "error": _err(out)})
        else:
            moved["variants"] += 1
    for k, (po, lines) in enumerate(orders):
        if used[0] + 1 > _BUDGET:
            return stopped({"orders": len(orders) - k})
        for ln in lines:
            ln["unitId"] = dst_id
            ln["unitName"] = dname
        out = call(
            "loadedhub",
            "update_purchase_order_raw",
            {"venue": venue, "order_id": po.get("id"), "order": po},
        )
        if _err(out):
            failed.append({"order": po.get("orderNumber"), "error": _err(out)})
        else:
            moved["orders"] += 1

    def edit_lines(lines):
        changed = False
        for ln in lines:
            if not ln.get("deletedAt") and ln.get("unitId") == src_id:
                ln["unitId"] = dst_id
                ln["unitName"] = dname
                changed = True
        return lines, changed

    def edit_version(sent):
        if sent.get("yieldUnitId") == src_id:
            sent["yieldUnitId"] = dst_id
            return True
        return False

    for k, (r, vs) in enumerate(recipes):
        saved, bad, stop = _save_recipe_versions(
            venue, call, used, r, vs, edit_lines, edit_version
        )
        if stop:
            return stopped({"recipes": len(recipes) - k})
        moved["recipe_versions"] += saved
        failed.extend(bad)
    for k, ln in enumerate(links):
        if used[0] + 1 > _BUDGET:
            return stopped({"pos_links": len(links) - k})
        body = dict(ln)
        body["unitId"] = dst_id
        body["unitName"] = dname
        out = call(
            _LL,
            "update_pos_item_link",
            {"venue": venue, "link_id": ln.get("id"), "link": body},
        )
        if _err(out):
            failed.append({"pos_link": ln.get("posIdentifier"), "error": _err(out)})
        else:
            moved["pos_links"] += 1

    if failed:
        return {
            "result": "partly merged",
            "unit": sname,
            "into": dname,
            "moved": moved,
            "failed": failed,
            "error": f"{len(failed)} change(s) failed, so {sname} was NOT deleted — fix "
            "those (or run the merge again) and it will be.",
        }
    sent = dict(rec)
    sent["datestampDeleted"] = _now_iso()
    out = call(
        _LL, "update_stock_unit", {"venue": venue, "unit_id": src_id, "unit": sent}
    )
    if _err(out):
        return {
            "result": "partly merged",
            "unit": sname,
            "into": dname,
            "moved": moved,
            "error": f"everything moved, but Loaded refused to delete {sname}: {_err(out)}",
        }
    return {"result": "merged", "unit": sname, "into": dname, "moved": moved}


# ------------------------------------------------------------ the cards ----


def _create_card(item, venue, call_api):
    names = _names(venue, call_api)
    changes = [{"field": "Name", "after": item.get("name")}]
    for field in ("countingUnitId", "orderingUnitId", "globalSalesTaxSortOrder"):
        if item.get(field) is not None:
            changes.append(
                {"field": _label(field), "after": _shown(field, item[field], names)}
            )
    for v in item.get("suppliers") or []:
        if isinstance(v, dict):
            cost = _shown("unitCost", v.get("unitCost"), names)
            unit = _shown("unitId", v.get("unitId"), names)
            changes.append(
                {
                    "field": "Supplier · " + _variant_name(v, names),
                    "after": f"{cost} per {unit}"
                    + (" (default)" if v.get("defaultForSupplier") else ""),
                }
            )
    return {
        "title": "Create a stock item",
        "target": item.get("name"),
        "venue": venue,
        "changes": changes,
    }


def _update_card(
    item,
    changed,
    variant_before,
    variant_edits,
    add_suppliers,
    skipped,
    venue,
    call_api,
    removed=(),
):
    names = _names(venue, call_api)
    changes = [
        {
            "field": _label(field),
            "before": _shown(field, ch["from"], names),
            "after": _shown(field, ch["to"], names),
        }
        for field, ch in changed.items()
    ]
    for (before, field, old), (_vid, _f, new) in zip(variant_before, variant_edits):
        changes.append(
            {
                "field": _variant_name(before, names) + " · " + _label(field),
                "before": _shown(field, old, names),
                "after": _shown(field, new, names),
            }
        )
    for v in add_suppliers:
        changes.append(
            {
                "field": "New supplier variant · " + _variant_name(v, names),
                "after": f"{_shown('unitCost', v.get('unitCost'), names)} per "
                f"{_shown('unitId', v.get('unitId'), names)}"
                + (" (default)" if v.get("defaultForSupplier") else ""),
            }
        )
    for v in removed:
        changes.append(
            {
                "field": "Supplier variant · " + _variant_name(v, names),
                "before": f"{_shown('unitCost', v.get('unitCost'), names)} per "
                f"{_shown('unitId', v.get('unitId'), names)}",
                "after": "removed",
            }
        )
    warnings = [f"Not changed — {k}: {why}" for k, why in sorted(skipped.items())]
    for v in add_suppliers:
        if v.get("unitCost") in (0, 0.0):
            warnings.append(
                f"{_variant_name(v, names)} is added at a unit cost of $0.00"
            )
    return {
        "title": "Update a stock item",
        "target": item.get("name"),
        "venue": venue,
        "changes": changes,
        "warnings": warnings,
    }


def _variant_unit_card(variant_id, unit_id, venue, call_api):
    names = _names(venue, call_api)
    return {
        "title": "Change a supplier variant's unit",
        "target": "variant " + str(variant_id)[:8],
        "venue": venue,
        "changes": [{"field": "Unit", "after": _shown("unitId", unit_id, names)}],
    }


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
    if op == "fix_received_unit":
        return _fix_received_unit(params, venue, call_api, log)
    if op in ("retire", "restore"):
        return _retire_restore(params, venue, call_api, log, op == "retire")
    if op == "merge":
        return _merge(params, venue, call_api, log)
    if op == "update_unit":
        return _update_unit(params, venue, call_api, log)
    if op in ("delete_unit", "restore_unit"):
        return _delete_restore_unit(params, venue, call_api, log, op == "delete_unit")
    if op == "merge_units":
        return _merge_units(params, venue, call_api, log)
    return _set_variant_unit(params, venue, call_api, log)
