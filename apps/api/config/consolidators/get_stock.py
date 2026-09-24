# ruff: noqa: F821 — sandbox-injected names; not imports.
#
# Canonical function_code for `loadedhub.get_stock` — THE stock read tool
# (installed by scripts/sync_stock_config.py; stock consolidation arc,
# Phase 2, Sep 2026).
#
# One tool, four views, each inlined here and never nested — the sandbox
# allows one level of consolidator nesting, and
# calculate_template_stock_requirements already spends it on get_budgets:
#
#   items     (default)  get_stock_items, verbatim: query -> slim {id, name}
#                        matches; item_id -> ONE item at detail summary|full.
#   on_hand              stock on hand and value: by item_id (item -> group ->
#                        whole-category template, matched by ID) or for a
#                        whole template (top rows by value + an "(others)"
#                        rollup so totals stay honest).
#   reference            kind = units | suppliers | groups | templates, as
#                        slim rows with a name filter.
#   minimums             par levels, converted into counting units.
#
# It absorbs, on the agent surface, get_stock_items, get_stock_on_hand_for_item,
# get_stock_on_hand, get_stock_units, get_suppliers, get_stock_item_groups,
# get_stocktake_templates and get_stock_item_minimums. Those raws stay as
# engine-only backends; this file calls them by name.
#
# Doctrine carried over from the tools it replaces:
#   - summaries carry NAMES, never UUIDs (a unit id is ten tokens of noise the
#     model can't reason about); the only ids kept are the handles later calls
#     need — the item's id and each variant_id.
#   - stock on hand is slow (~13 s in Loaded's own UI) and 500s under load:
#     one serial retry, then a Loaded failure is reported AS a failure, never
#     as "not counted" or an empty snapshot.
#   - the whole-category template is found by ID: Loaded's Beverage / Food /
#     Other Stock categories share their ids with the whole-category
#     templates (verified on five production venues). Never by title
#     substring — "BEVERAGE - CELLAR" is an area, not the category.
#
# Requires consolidator_config: {"max_api_calls": 8}

_VIEWS = ("items", "on_hand", "reference", "minimums")
_KINDS = ("units", "suppliers", "groups", "templates")

#: Category words a caller may use for template= ; the value is Loaded's
#: category name, matched against the groups list to find the category id.
_CATEGORY_WORDS = {
    "beverage": "Beverage",
    "beverages": "Beverage",
    "bev": "Beverage",
    "drinks": "Beverage",
    "food": "Food",
    "other": "Other Stock",
    "other stock": "Other Stock",
}


def _api_error(result):
    """call_api hands back {"error": ...} on failure.

    A successful stock-on-hand payload is a dict too, carrying "lines" — so
    require the key's absence before treating the response as an error.
    """
    if isinstance(result, dict) and "error" in result and "lines" not in result:
        return str(result["error"])
    return None


def _as_list(v, key=None):
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        if key and isinstance(v.get(key), list):
            return v[key]
        for k in ("lines", "items", "data", "results"):
            if isinstance(v.get(k), list):
                return v[k]
    return []


def _lower(s):
    return str(s or "").strip().lower()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _unit_name(u):
    # Loaded embeds the WHOLE unit object under minimumStockOnHandUnit
    # ({id, stockUnitType, ratio, name, datestampDeleted, masterId}); the
    # model needs its name. get_stock_items leaked the object.
    if isinstance(u, dict):
        return u.get("name")
    return u


def _find(rows, key, value):
    for r in _as_list(rows):
        if isinstance(r, dict) and str(r.get(key)) == str(value):
            return r
    return None


def _fetch(call_api, call_api_parallel, calls):
    if call_api_parallel is not None and len(calls) > 1:
        return call_api_parallel(calls)
    return [call_api(c, a, p) for c, a, p in calls]


# ---------------------------------------------------------------- items ----


def _summarize(item, venue, call_api):
    # The working subset, names over ids: what ordering, pricing and recipe
    # questions actually use — never the whole Loaded object unless asked.
    # Variant unit/supplier names need the two lookup lists (item-level
    # names ride free on the payload).
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
            "supplier": supplier_names.get(v.get("supplierId")) or v.get("supplierId"),
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
        "minimum_stock_unit": _unit_name(item.get("minimumStockOnHandUnit")),
        "variants": variants,
    }


def _items(params, venue, call_api):
    item_id = params.get("item_id")
    query = _lower(params.get("query"))
    detail = _lower(params.get("detail")) or "summary"
    limit = int(params.get("limit") or 25)

    if item_id:
        item = call_api(
            "loadedhub", "get_stock_item_full", {"venue": venue, "item_id": item_id}
        )
        if not isinstance(item, dict) or item.get("error"):
            err = item.get("error") if isinstance(item, dict) else None
            return {"error": err or f"stock item {item_id} not found"}
        if detail == "full":
            return {"item": item, "detail": "full"}
        return {"item": _summarize(item, venue, call_api), "detail": "summary"}

    rows = call_api("loadedhub", "get_stock_items_raw", {"venue": venue})
    if not isinstance(rows, list):
        err = rows.get("error") if isinstance(rows, dict) else None
        return {"error": err or "stock item list unavailable"}
    if query:
        rows = [r for r in rows if query in _lower(r.get("name"))]
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
            out["item"] = (
                item if detail == "full" else _summarize(item, venue, call_api)
            )
            out["detail"] = "full" if detail == "full" else "summary"
    if not query:
        out["note"] = (
            "this is the full item list — pass query (name substring) or "
            "item_id instead of scanning it, especially before an update"
        )
    return out


# -------------------------------------------------------------- on_hand ----


def _as_of(params):
    """(report_datetime for Loaded, the date to echo back).

    The engine injects today_iso as midnight at the venue's own offset
    (URL-encoded, e.g. 2026-09-24T00:00:00%2B12:00). A bare as_of date keeps
    that offset, so a snapshot "as of the 1st" is the venue's 1st.
    """
    today_iso = str(params.get("today_iso") or "")
    as_of = str(params.get("as_of") or "").strip()
    if not as_of:
        return today_iso, (params.get("today") or today_iso[:10])
    if "T" in as_of:
        return as_of.replace("+", "%2B"), as_of[:10]
    return as_of[:10] + today_iso[10:], as_of[:10]


def _template_for_category(templates, category_id, category_name):
    # By id first — the category IS the whole-category template. Exact
    # title as the fallback. Never a substring.
    t = _find(templates, "id", category_id) if category_id else None
    if t:
        return t
    for cand in _as_list(templates, "templates"):
        if isinstance(cand, dict) and category_name:
            if _lower(cand.get("title")) == _lower(category_name):
                return cand
    return None


def _stock_on_hand(venue, template, as_of_iso, call_api, log):
    args = {
        "venue": venue,
        "template_id": template.get("id"),
        "report_datetime": as_of_iso,
    }
    rows = call_api("loadedhub", "get_stock_on_hand", args)
    if _api_error(rows):
        log(f"stock on hand failed ({_api_error(rows)}); retrying once, serially")
        rows = call_api("loadedhub", "get_stock_on_hand", args)
    err = _api_error(rows)
    if err:
        return None, (
            "LoadedHub could not return stock on hand for template "
            f"'{template.get('title')}' — its stock-on-hand report is slow and "
            "times out under load. This is a LoadedHub failure, not an empty "
            f"count; try again shortly. LoadedHub said: {err}"
        )
    return _as_list(rows, "lines"), None


def _resolve_template(params, venue, call_api):
    template_id = params.get("template_id")
    wanted = _lower(params.get("template"))
    if not template_id and not wanted:
        return None, (
            "Pass item_id for one item's stock on hand, or template — a "
            "stocktake template title, or 'Food' / 'Beverage' / 'Other Stock' "
            "for a whole category. view='reference', kind='templates' lists "
            "this venue's templates."
        )
    templates = _as_list(
        call_api("loadedhub", "get_stocktake_templates", {"venue": venue}),
        "templates",
    )
    if template_id:
        t = _find(templates, "id", template_id)
        if t:
            return t, None
        return None, f"No stocktake template with id {template_id} at {venue}."
    for cand in templates:
        if isinstance(cand, dict) and _lower(cand.get("title")) == wanted:
            return cand, None
    category = _CATEGORY_WORDS.get(wanted)
    if category:
        groups = _as_list(
            call_api("loadedhub", "get_stock_item_groups", {"venue": venue}),
            "groups",
        )
        category_id = None
        for g in groups:
            if isinstance(g, dict) and _lower(g.get("categoryName")) == _lower(
                category
            ):
                category_id = g.get("categoryId")
                break
        t = _template_for_category(templates, category_id, category)
        if t:
            return t, None
    titles = sorted(str(x.get("title")) for x in templates if isinstance(x, dict))
    return None, (
        f"No stocktake template titled '{params.get('template')}' at {venue}. "
        f"Templates: {', '.join(titles)}"
    )


def _snapshot(lines, template, as_of_date, params):
    query = _lower(params.get("query"))
    top = int(params.get("top") or 50)
    sort_by = _lower(params.get("sort_by")) or "value"
    rows = []
    deleted = 0
    for r in lines:
        if not isinstance(r, dict):
            continue
        if r.get("isItemDeleted"):
            deleted += 1
            continue
        name = str(r.get("itemName") or "")
        if query and query not in name.lower():
            continue
        rows.append(
            {
                "item_id": r.get("stockItemID") or r.get("id"),
                "name": name,
                "category": r.get("Category") or r.get("category"),
                "quantity_on_hand": _num(r.get("quantityOnHand")),
                "counting_unit": r.get("countingUnitName"),
                "value_on_hand": _num(r.get("valueOnHand")),
            }
        )
    if sort_by == "quantity":
        rows.sort(key=lambda x: -(x["quantity_on_hand"] or 0))
    elif sort_by == "name":
        rows.sort(key=lambda x: x["name"].lower())
    else:
        rows.sort(key=lambda x: -(x["value_on_hand"] or 0))
    shown = rows[:top]
    out = {
        "template": template.get("title"),
        "template_id": template.get("id"),
        "as_of": as_of_date,
        "lines": len(rows),
        "zero_on_hand": sum(1 for x in rows if not x["quantity_on_hand"]),
        "total_value_on_hand": round(sum(x["value_on_hand"] or 0 for x in rows), 2),
        "shown": len(shown),
        "rows": shown,
    }
    if deleted:
        out["deleted_items_excluded"] = deleted
    if len(rows) > len(shown):
        rest = rows[len(shown) :]
        out["others"] = {
            "count": len(rest),
            "value_on_hand": round(sum(x["value_on_hand"] or 0 for x in rest), 2),
        }
    return out


def _on_hand(params, venue, call_api, call_api_parallel, log):
    item_id = params.get("item_id")
    as_of_iso, as_of_date = _as_of(params)

    if item_id:
        # The item's own record is needed for its group, but the two lookup
        # lists don't depend on it — fetch all three at once.
        item, groups, templates = _fetch(
            call_api,
            call_api_parallel,
            [
                (
                    "loadedhub",
                    "get_stock_item_full",
                    {"venue": venue, "item_id": item_id},
                ),
                ("loadedhub", "get_stock_item_groups", {"venue": venue}),
                ("loadedhub", "get_stocktake_templates", {"venue": venue}),
            ],
        )
        if isinstance(item, list):
            item = item[0] if item else {}
        if not isinstance(item, dict) or not item or item.get("error"):
            return {"error": f"Stock item {item_id} not found at {venue}."}
        name = item.get("name") or item.get("itemName") or item_id
        group_id = item.get("groupId")
        group = _find(_as_list(groups, "groups"), "id", group_id)
        if not group:
            return {"error": f"No stock group found for '{name}' (groupId {group_id})."}
        category = group.get("categoryName")
        template = _template_for_category(templates, group.get("categoryId"), category)
        if not template:
            return {
                "error": (
                    f"No whole-category stocktake template for '{name}' (category "
                    f"{category or 'unknown'}), so its stock on hand can't be read."
                )
            }
        log(f"'{name}' -> {category} -> template '{template.get('title')}'")
        lines, err = _stock_on_hand(venue, template, as_of_iso, call_api, log)
        if err:
            return {"error": err}
        row = None
        for r in lines:
            if not isinstance(r, dict):
                continue
            if str(r.get("stockItemID")) == str(item_id) or str(r.get("id")) == str(
                item_id
            ):
                row = r
                break
        if row is None:
            return {
                "error": (
                    f"'{name}' isn't counted on stocktake template "
                    f"'{template.get('title')}', so it has no stock on hand."
                ),
                "template": template.get("title"),
                "as_of": as_of_date,
            }
        return {
            "item_id": item_id,
            "name": row.get("itemName") or name,
            "category": row.get("Category") or category,
            "template": template.get("title"),
            "as_of": as_of_date,
            "quantity_on_hand": _num(row.get("quantityOnHand")),
            "counting_unit": row.get("countingUnitName"),
            "value_on_hand": _num(row.get("valueOnHand")),
        }

    template, err = _resolve_template(params, venue, call_api)
    if err:
        return {"error": err}
    lines, err = _stock_on_hand(venue, template, as_of_iso, call_api, log)
    if err:
        return {"error": err}
    return _snapshot(lines, template, as_of_date, params)


# ------------------------------------------------------------ reference ----


def _page(kind, rows, limit, extra=None):
    out = {"kind": kind, "total": len(rows), "shown": min(len(rows), limit)}
    if extra:
        out.update(extra)
    out["rows"] = rows[:limit]
    if len(rows) > limit:
        out["note"] = (
            f"{len(rows) - limit} more — narrow with query (name substring) "
            "rather than raising limit"
        )
    return out


def _primary_email(supplier):
    emails = supplier.get("emails") or []
    for e in emails:
        if isinstance(e, dict) and e.get("isPrimary"):
            return e.get("email")
    for e in emails:
        if isinstance(e, dict) and e.get("email"):
            return e.get("email")
    return None


def _reference(params, venue, call_api, call_api_parallel):
    kind = _lower(params.get("kind"))
    if kind not in _KINDS:
        return {"error": f"kind must be one of {', '.join(_KINDS)}."}
    query = _lower(params.get("query"))
    include_deleted = bool(params.get("include_deleted"))
    limit = int(params.get("limit") or (100 if kind == "units" else 500))

    if kind == "units":
        raw = call_api("loadedhub", "get_stock_units", {"venue": venue})
        if _api_error(raw):
            return {"error": _api_error(raw)}
        rows = []
        for u in _as_list(raw):
            if not isinstance(u, dict):
                continue
            if u.get("datestampDeleted") and not include_deleted:
                continue
            if query and query not in _lower(u.get("name")):
                continue
            rows.append(
                {
                    "id": u.get("id"),
                    "name": u.get("name"),
                    "type": u.get("stockUnitType"),
                    "ratio": u.get("ratio"),
                }
            )
        rows.sort(
            key=lambda x: (_lower(x["type"]), _num(x["ratio"]) or 0, _lower(x["name"]))
        )
        return _page("units", rows, limit)

    if kind == "suppliers":
        raw = call_api("loadedhub", "get_suppliers", {"venue": venue})
        if _api_error(raw):
            return {"error": _api_error(raw)}
        rows = []
        for s in _as_list(raw):
            if not isinstance(s, dict):
                continue
            if s.get("removedAt") and not include_deleted:
                continue
            if query and query not in _lower(s.get("name")):
                continue
            rows.append(
                {
                    "id": s.get("id"),
                    "name": s.get("name"),
                    "contact": s.get("contactName"),
                    "email": _primary_email(s),
                    "customer_number": s.get("customerNumber"),
                    "last_order_at": s.get("lastOrderAt"),
                }
            )
        rows.sort(key=lambda x: _lower(x["name"]))
        return _page("suppliers", rows, limit)

    if kind == "groups":
        raw = call_api("loadedhub", "get_stock_item_groups", {"venue": venue})
        if _api_error(raw):
            return {"error": _api_error(raw)}
        rows = []
        categories = {}
        for g in _as_list(raw, "groups"):
            if not isinstance(g, dict):
                continue
            if g.get("categoryName") and g.get("categoryId"):
                categories[g["categoryName"]] = g["categoryId"]
            if query and query not in _lower(g.get("name")):
                continue
            rows.append(
                {
                    "id": g.get("id"),
                    "name": g.get("name"),
                    "category": g.get("categoryName"),
                    "category_id": g.get("categoryId"),
                }
            )
        rows.sort(key=lambda x: (_lower(x["category"]), _lower(x["name"])))
        return _page("groups", rows, limit, {"categories": categories})

    # templates — flag the whole-category ones (id == a category id).
    templates, groups = _fetch(
        call_api,
        call_api_parallel,
        [
            ("loadedhub", "get_stocktake_templates", {"venue": venue}),
            ("loadedhub", "get_stock_item_groups", {"venue": venue}),
        ],
    )
    if _api_error(templates):
        return {"error": _api_error(templates)}
    category_of = {}
    for g in _as_list(groups, "groups"):
        if isinstance(g, dict) and g.get("categoryId"):
            category_of[str(g["categoryId"])] = g.get("categoryName")
    rows = []
    for t in _as_list(templates, "templates"):
        if not isinstance(t, dict):
            continue
        if query and query not in _lower(t.get("title")):
            continue
        row = {"id": t.get("id"), "title": t.get("title")}
        cat = category_of.get(str(t.get("id")))
        if cat:
            row["category"] = cat
        rows.append(row)
    rows.sort(key=lambda x: (0 if x.get("category") else 1, _lower(x["title"])))
    return _page(
        "templates",
        rows,
        limit,
        {
            "note": (
                "a row with category is that category's whole-stock template "
                "(every item in it); the rest are areas and custom counts"
            )
        },
    )


# ------------------------------------------------------------- minimums ----


def _minimums(params, venue, call_api):
    raw = call_api("loadedhub", "get_stock_item_minimums", {"venue": venue})
    if _api_error(raw) or not isinstance(raw, list):
        return {"error": _api_error(raw) or "stock item minimums unavailable"}
    item_id = params.get("item_id")
    query = _lower(params.get("query"))
    limit = int(params.get("limit") or 200)
    rows = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        if item_id and str(it.get("id")) != str(item_id):
            continue
        name = str(it.get("itemName") or it.get("name") or "")
        if query and query not in name.lower():
            continue
        min_qty = _num(it.get("minQty")) or 0.0
        if min_qty <= 0 and not item_id:
            continue
        # Each item stores its minimum in its own unit; stock on hand reports
        # in the counting unit. min_in_counting = minQty * minUnitRatio /
        # countingUnitRatio (~1 in 5 items differ: a beer counted in Each
        # with a 24 Pack minimum is 24x understated without this).
        min_ratio = _num(it.get("minUnitRatio")) or 0.0
        count_ratio = _num(it.get("countingUnitRatio")) or 0.0
        if min_ratio > 0 and count_ratio > 0:
            in_counting = min_qty * min_ratio / count_ratio
        else:
            in_counting = min_qty
        rows.append(
            {
                "item_id": it.get("id"),
                "name": name,
                "minimum_in_counting_units": round(in_counting, 3),
                "minimum_as_set": min_qty,
                "min_unit_ratio": min_ratio or None,
                "counting_unit_ratio": count_ratio or None,
            }
        )
    rows.sort(key=lambda x: _lower(x["name"]))
    return _page(
        "minimums",
        rows,
        limit,
        {
            "note": (
                "minimum_in_counting_units is the par level in the unit the "
                "item is counted in (Loaded stores each minimum in its own unit)"
            )
        },
    )


# ------------------------------------------------------------------ run ----


def run(params, call_api, log, call_api_parallel=None):
    view = _lower(params.get("view")) or "items"
    venue = params.get("venue")
    if view not in _VIEWS:
        return {
            "error": f"Unknown view '{params.get('view')}' — one of {', '.join(_VIEWS)}."
        }
    if view == "items":
        return _items(params, venue, call_api)
    if view == "on_hand":
        return _on_hand(params, venue, call_api, call_api_parallel, log)
    if view == "reference":
        return _reference(params, venue, call_api, call_api_parallel)
    return _minimums(params, venue, call_api)
