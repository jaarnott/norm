# ruff: noqa: F821 — sandbox-injected names; not imports.
#
# Canonical function_code for `loadedhub.get_stock_on_hand_for_item`
# (synced by scripts/sync_stock_item_consolidators.py). Recovered from the
# config DB 22 Aug 2026 — it previously existed ONLY there, invisible to
# review and CI (a drift-report finding).
def run(params, call_api, log, call_api_parallel):
    """Stock on hand for one item, resolving its stocktake template automatically.

    Ported from the legacy `steps` consolidator format, whose executor was
    removed (see the 2026-04-06 "Remove legacy consolidator code" commit). The
    original pipeline was:
        item_details        -> get_stock_item
        stock_groups        -> get_stock_item_groups        (parallel)
        stocktake_templates -> get_stocktake_templates      (parallel)
        group_lookup        -> stock_groups   where id    contains item.groupId
        template_lookup     -> templates      where title contains group.superGroupName
                               (now: id == group.categoryId — see template_lookup)
        stock_on_hand       -> get_stock_on_hand(template_id=template_lookup.id)
        search              -> filter stock_on_hand rows by item_id
    """
    venue = params["venue"]
    item_id = params["item_id"]

    # The item's own details are needed before the group lookup, but the two
    # lookup tables don't depend on it — fetch all three at once (the legacy
    # config marked groups/templates as parallel:"fetch" for the same reason).
    log(f"Fetching item {item_id} details, stock groups and templates...")
    item_details, stock_groups, templates = call_api_parallel(
        [
            # The one raw single-item read (get_stock_item was a duplicate of the
            # same endpoint and is gone; the ?includeDeleted param defaulted false).
            ("loadedhub", "get_stock_item_full", {"venue": venue, "item_id": item_id}),
            ("loadedhub", "get_stock_item_groups", {"venue": venue}),
            ("loadedhub", "get_stocktake_templates", {"venue": venue}),
        ]
    )

    def as_list(v, key=None):
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            if key and isinstance(v.get(key), list):
                return v[key]
            for k in ("lines", "items", "data", "results"):
                if isinstance(v.get(k), list):
                    return v[k]
        return []

    if isinstance(item_details, list):
        item_details = item_details[0] if item_details else {}
    if not isinstance(item_details, dict) or not item_details:
        return {"error": f"Stock item {item_id} not found at {venue}."}

    group_id = item_details.get("groupId")
    item_name = item_details.get("name") or item_details.get("itemName") or item_id
    if not group_id:
        return {
            "error": f"Stock item '{item_name}' has no groupId, so its stocktake template can't be resolved."
        }

    # group_lookup: the group whose id matches the item's groupId
    group = None
    for g in as_list(stock_groups, "groups"):
        if isinstance(g, dict) and str(g.get("id")) == str(group_id):
            group = g
            break
    if not group:
        return {
            "error": f"No stock group found for '{item_name}' (groupId {group_id})."
        }

    # template_lookup. This read `superGroupName`, a field the groups
    # transform has never emitted (it keeps categoryId / categoryName), so the
    # tool failed on EVERY call and never once succeeded in production — found
    # 23 Sep 2026 by reading the live transform.
    #
    # The category IS the super group, and Loaded's three categories (Beverage
    # / Food / Other Stock) share their ids with the whole-category stocktake
    # templates of the same name — confirmed on production payloads from three
    # venues. So the template is found by id, exactly. The old substring match
    # on the title was also dangerous in its own right: venues keep area
    # templates like "BEVERAGE - CELLAR" and "BEVERAGE - BESSIE BAR", and
    # "contains Beverage" took whichever came first — a partial area the item
    # may not even be counted on.
    category_id = group.get("categoryId")
    category = group.get("categoryName") or group.get("superGroupName")
    template_list = [t for t in as_list(templates, "templates") if isinstance(t, dict)]
    template = None
    if category_id:
        for t in template_list:
            if str(t.get("id")) == str(category_id):
                template = t
                break
    if not template and category:
        # Fallback: a template titled exactly as the category. Never a
        # substring — see above.
        for t in template_list:
            if str(t.get("title", "")).strip().lower() == category.strip().lower():
                template = t
                break
    if not template:
        return {
            "error": (
                f"No whole-category stocktake template for '{item_name}' "
                f"(category {category or 'unknown'}), so its stock on hand can't "
                "be read."
            )
        }

    log(
        f"Item '{item_name}' -> category '{category}' -> template '{template.get('title')}'"
    )

    # Stock on hand is slow (~13 s in Loaded's own UI) and 500s past Loaded's
    # ~30 s limit under load. One serial retry, then say plainly that Loaded
    # failed — never report a failure as "not counted" (the stock-requirements
    # doctrine: an upstream outage must not read as a data problem).
    soh_args = {
        "venue": venue,
        "template_id": template.get("id"),
        "report_datetime": params["today_iso"],
    }
    rows = call_api("loadedhub", "get_stock_on_hand", soh_args)
    if isinstance(rows, dict) and rows.get("error") and "lines" not in rows:
        log(f"stock on hand failed ({rows.get('error')}); retrying once")
        rows = call_api("loadedhub", "get_stock_on_hand", soh_args)
    if isinstance(rows, dict) and rows.get("error") and "lines" not in rows:
        return {
            "error": (
                "LoadedHub could not return stock on hand for template "
                f"'{template.get('title')}' — its stock-on-hand report is slow and "
                "times out under load. This is a LoadedHub failure, not a missing "
                f"item; try again shortly. LoadedHub said: {rows.get('error')}"
            )
        }

    # search: keep only the row for this item
    match = None
    for r in as_list(rows, "lines"):
        if not isinstance(r, dict):
            continue
        if str(r.get("stockItemID")) == str(item_id) or str(r.get("id")) == str(
            item_id
        ):
            match = r
            break

    if not match:
        return {
            "error": f"'{item_name}' isn't counted on stocktake template '{template.get('title')}', so it has no stock on hand."
        }

    # output_fields from the legacy config
    return {
        "Category": match.get("Category") or match.get("category"),
        "stockItemID": match.get("stockItemID") or item_id,
        "itemName": match.get("itemName") or item_name,
        "countingUnitName": match.get("countingUnitName"),
        "quantityOnHand": match.get("quantityOnHand"),
        "valueOnHand": match.get("valueOnHand"),
    }
