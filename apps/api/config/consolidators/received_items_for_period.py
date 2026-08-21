# ruff: noqa: F821 — `datetime`, `json` and `math` are injected into the
# sandbox namespace by app/connectors/function_executor.py; not imports.
#
# Canonical function_code for `loadedhub.get_received_items_for_period`.
# Synced verbatim into the config DB — see config/consolidators/README.md and
# scripts/sync_received_items_config.py.
#
# Requires consolidator_config:
#   {"max_api_calls": 6, "allowed_write_actions": []}   # reads only, always
#
# WHAT THIS IS FOR
#
# `get_received_invoices_for_period` answers "which invoices landed", but the
# question people actually ask is about ITEMS: how much of a thing did we take
# in, what did it cost, and did the price move. That data is already in the
# feed — one level down, inside each invoice's `lines` — so answering it meant
# handing the model every invoice in the window (117 invoices / 552 lines for
# one venue-fortnight) and asking it to flatten and add up. Arithmetic done by
# eye is a guess that reads like a fact. This does it in code.
#
# THREE THINGS THAT ARE WRONG IF DONE NAIVELY
#
# 1. Quantities cannot be summed as printed. Twelve of a `6x1L` and twelve of a
#    `1L` are not twenty-four of anything. Every line carries `unitRatio`
#    (0.75 for "750 mL"), so quantity is converted to the item's base unit
#    BEFORE summing, and every distinct unit seen is reported so a mixed-pack
#    item stays visible instead of being silently averaged.
#
# 2. Price movement must be per base unit. $4.60 for a 1L against $27.00 for a
#    6x1L is not a 487% rise; it is the same price in a different box. Costs are
#    normalised to the base unit before first/last/min/max, or the headline
#    feature invents a price hike on every pack-size change.
#
# 3. The feed carries NO item name — only `itemId`, sometimes `itemCode` (which
#    is often null), and the supplier's own line text. Loaded's own "Stock Item
#    Description" column shows the CATALOGUE name, and the two genuinely differ:
#    the line "Spianata Piccante 2kg C6" belongs to the item "SPIANATA PICCANTE"
#    (verified live — see app/services/received_invoice.py::attach_item_names).
#    Grouping on supplier text would split one item across every wording its
#    suppliers use, so names are resolved from the catalogue in ONE bulk call.

_GROUPINGS = ("item", "item_supplier", "line", "group", "super_group")
_LINE_CAP = 2000
# Aggregated modes cap at the top rows by spend, with an "(others)" rollup —
# a 3-month "what did we buy" answer is ~25 rows and a total, never 1,000.
_ROW_CAP = 25
# Loaded's three stock dimensions, and what one unit of each means once the
# unit ratio has been applied. A quantity without this label is a bare number.
_BASE_UNIT = {"Weight": "kg", "Volume": "L", "Count": "each"}
_CONSUMED = (
    "period",
    "start",
    "end",
    "confirmed_by_user",
    "group_by",
    "suppliers",
    "item_id",
    "query",
    "group",
    "limit",
)


def run(params, call_api, log, call_api_parallel=None):
    def norm(text):
        return "".join(ch for ch in str(text or "").lower() if ch.isalnum())

    def num(value):
        if value is None or value is True or value is False:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    group_by = (params.get("group_by") or "item").strip().lower()
    if group_by not in _GROUPINGS:
        return {
            "error": (
                "group_by must be one of "
                + ", ".join(_GROUPINGS)
                + " — got "
                + repr(params.get("group_by"))
            )
        }

    period = (params.get("period") or "").strip()
    start = params.get("start")
    end = params.get("end")
    if not period and not (start and end):
        return {
            "error": (
                "Give a period in plain English (e.g. 'last month', 'last week'). "
                "Only pass start and end if the user asked for specific clock times."
            )
        }

    # The venue's calendar lives in Norm, not in config-DB code shared by every
    # organisation — so the window is resolved as a tool call, exactly as
    # config/consolidators/for_period.py does it.
    resolve_args = {}
    if params.get("venue_id"):
        resolve_args["venue_id"] = params["venue_id"]
    if period:
        resolve_args["query"] = period
    else:
        resolve_args["start"] = start
        resolve_args["end"] = end

    resolved = call_api("norm", "resolve_dates", resolve_args)
    if isinstance(resolved, dict) and resolved.get("error"):
        return {"error": "Could not resolve the period: " + str(resolved["error"])}
    window = resolved.get("window") if isinstance(resolved, dict) else None
    if not isinstance(window, dict):
        data = resolved.get("data") if isinstance(resolved, dict) else None
        window = data.get("window") if isinstance(data, dict) else None
    if not isinstance(window, dict):
        return {
            "error": (
                "Could not resolve '" + (period or "that range") + "' to a date "
                "range. Try a simpler period such as 'last week'."
            )
        }

    # Same deviation check as for_period: ask a question answerable from fact
    # ("did the user ask for these times?"), not "is this right?", which invites
    # agreement and would launder a mistake as confirmed.
    if not window.get("trading_aligned") and not params.get("confirmed_by_user"):
        log("explicit window is not a trading day; asking before fetching")
        return {
            "needs_confirmation": True,
            "window": window,
            "question": (
                "These times are not this venue's trading day. "
                + str(window.get("description", ""))
                + " Did the user explicitly ask for these exact clock times? If "
                "yes, call again with confirmed_by_user=true. If they asked for a "
                "named period, pass period instead and no start/end."
            ),
        }

    forwarded = {k: v for k, v in params.items() if k not in _CONSUMED}
    forwarded["from"] = window["start"]
    forwarded["to"] = window["end"]

    invoices = call_api("loadedhub", "get_received_invoices", forwarded)
    # A failed call is a failure, not an empty period — reaching straight for
    # the rows would report an outage as "nothing was received".
    if isinstance(invoices, dict) and invoices.get("error"):
        return {"error": str(invoices["error"]), "window": window}
    if not isinstance(invoices, list):
        invoices = []

    warnings = []

    # Names and unit types are ENHANCEMENTS: if either lookup fails the numbers
    # are still correct, so degrade with a visible warning rather than failing
    # the whole read. Both are one bulk call — a per-item fetch would be ~267
    # calls for a fortnight and blow max_api_calls.
    names = {}
    item_groups = {}
    # The raw {id, name, group…} list (get_stock_items is a consolidator now;
    # this engine-side call wants the bare list, not the lookup surface).
    catalogue = call_api("loadedhub", "get_stock_items_raw", {})
    if isinstance(catalogue, dict) and catalogue.get("error"):
        warnings.append("Item names unavailable: " + str(catalogue["error"]))
    elif isinstance(catalogue, list):
        for item in catalogue:
            if isinstance(item, dict) and item.get("id"):
                names[item["id"]] = item.get("name") or item.get("itemName")
                if item.get("groupName"):
                    item_groups[item["id"]] = (item.get("groupId"), item["groupName"])

    # Group → category (super-group) mapping: Loaded's subcategories list ties
    # each stock group to one of the three categories (Beverage/Food/Other
    # Stock). Fetched only when the rollup needs it; a miss degrades to each
    # line's own category field with a visible warning.
    group_category = {}
    if group_by == "super_group":
        subcats = call_api("loadedhub", "get_stock_item_groups", {})
        for g in subcats if isinstance(subcats, list) else []:
            if isinstance(g, dict) and g.get("id"):
                group_category[g["id"]] = g.get("categoryName")
        if not group_category:
            warnings.append(
                "Group-to-category mapping unavailable; rows fall back to each "
                "line's own category field"
            )

    # The feed's transform drops unitId, so a unit is identified by its (name,
    # ratio) pair. That is what distinguishes the two units both named '6.5 KG'
    # in one venue — same name, ratios 6.5 and 1.0.
    unit_types = {}
    units = call_api("loadedhub", "get_stock_units", {})
    if isinstance(units, dict) and units.get("error"):
        warnings.append("Unit types unavailable: " + str(units["error"]))
    elif isinstance(units, list):
        for unit in units:
            if not isinstance(unit, dict):
                continue
            ratio = num(unit.get("ratio"))
            key = (norm(unit.get("name")), round(ratio, 6) if ratio else None)
            unit_types.setdefault(key, unit.get("stockUnitType"))

    supplier_filter = {norm(s) for s in (params.get("suppliers") or []) if s}

    flat = []
    unnamed = set()
    unratioed = 0
    for inv in invoices:
        if not isinstance(inv, dict):
            continue
        if supplier_filter and norm(inv.get("supplierName")) not in supplier_filter:
            continue
        is_credit = bool(inv.get("creditRequest"))
        when = str(inv.get("invoicedAt") or inv.get("receivedAt") or "")[:10]
        for line in inv.get("lines") or []:
            if not isinstance(line, dict):
                continue
            item_id = line.get("StockItemId") or line.get("itemId")
            qty = num(line.get("quantityReceived"))
            cost = num(line.get("unitCost"))
            ratio = num(line.get("unitRatio"))
            if qty is None:
                continue
            if not ratio or ratio <= 0:
                # Without a ratio the quantity cannot be made comparable. Count
                # it rather than pretending 1.0 and reporting a wrong total.
                unratioed += 1
                ratio = None
            name = names.get(item_id)
            if item_id and not name:
                unnamed.add(item_id)
            unit_name = line.get("unitName")
            utype = unit_types.get(
                (norm(unit_name), round(ratio, 6) if ratio else None)
            )
            flat.append(
                {
                    "item_id": item_id,
                    "item_code": line.get("StockVariantCode") or line.get("itemCode"),
                    # Fall back to the printed unit rather than blanking the row:
                    # a nameless row is unusable, and the unit text is at least
                    # a human clue about what arrived.
                    "item_name": name or ("(unknown item " + str(item_id)[:8] + ")"),
                    "named": bool(name),
                    "category": line.get("Category")
                    or (line.get("itemCategory") or {}).get("name"),
                    "group_id": (item_groups.get(item_id) or (None, None))[0],
                    "group": (item_groups.get(item_id) or (None, None))[1],
                    "supplier_id": inv.get("supplierId"),
                    "supplier_name": inv.get("supplierName"),
                    "invoice_id": inv.get("id"),
                    "invoice_number": inv.get("invoiceNumber"),
                    "date": when,
                    "is_credit": is_credit,
                    "unit_name": unit_name,
                    "unit_ratio": ratio,
                    "base_unit": _BASE_UNIT.get(utype),
                    "quantity": qty,
                    # The two SUMMED fields are kept at full precision and
                    # rounded only where they are emitted. Rounding each line
                    # first and adding the results drifts: over 552 lines it put
                    # the fortnight's spend 4c above the same figure computed by
                    # hand from the feed.
                    "quantity_base": qty * ratio if ratio else None,
                    "unit_cost": round(cost, 4) if cost is not None else None,
                    "cost_per_base_unit": (
                        round(cost / ratio, 4) if cost is not None and ratio else None
                    ),
                    "spend": qty * cost if cost is not None else 0.0,
                }
            )

    if unnamed:
        warnings.append(
            str(len(unnamed)) + " item id(s) were not in the stock catalogue "
            "(deleted items?) and are shown as '(unknown item …)'"
        )
    if unratioed:
        warnings.append(
            str(unratioed) + " line(s) had no unit ratio, so their quantity could "
            "not be converted to a base unit and is excluded from quantity_base"
        )

    # Narrowing filters — "how much OIL CANOLA did we buy" is one filtered
    # row, not the whole catalogue. Applied to the flattened lines so every
    # grouping and the summary see the same subset.
    if params.get("item_id"):
        flat = [r for r in flat if r["item_id"] == params["item_id"]]
    if params.get("query"):
        q = norm(params["query"])
        flat = [r for r in flat if q in norm(r["item_name"])]
    if params.get("group"):
        gq = str(params["group"]).strip().lower()
        flat = [
            r
            for r in flat
            if gq in str(r.get("group") or r.get("category") or "").lower()
        ]

    limit = int(params.get("limit") or _ROW_CAP)

    def cap_rows(rows, label_key):
        # Top rows by spend + one "(others)" rollup — the summary already
        # carries the full-period totals, so nothing is lost, only detail.
        if len(rows) <= limit:
            return rows
        rest = rows[limit:]
        others = {
            label_key: "(others)",
            "rows_rolled_up": len(rest),
            "spend": round(sum(r["spend"] for r in rest), 2),
        }
        warnings.append(
            "Showing the top "
            + str(limit)
            + " of "
            + str(len(rows))
            + " rows by spend; the rest are rolled into '(others)'. Pass a "
            "higher limit, or query/group filters, for more."
        )
        return rows[:limit] + [others]

    result = {
        "window": window,
        "group_by": group_by,
        "warnings": warnings,
    }

    if group_by in ("group", "super_group"):
        label_key = "group" if group_by == "group" else "super_group"
        buckets = {}
        for row in flat:
            if group_by == "group":
                key = row.get("group") or row.get("category") or "(no group)"
            else:
                key = (
                    group_category.get(row.get("group_id"))
                    or row.get("category")
                    or "(no category)"
                )
            bucket = buckets.get(key)
            if bucket is None:
                bucket = buckets[key] = {
                    label_key: key,
                    "spend": 0.0,
                    "credit_amount": 0.0,
                    "_items": {},
                    "_invoices": set(),
                }
            bucket["spend"] += row["spend"]
            if row["is_credit"] or row["quantity"] < 0:
                bucket["credit_amount"] += row["spend"]
            bucket["_invoices"].add(row["invoice_id"])
            bucket["_items"][row["item_name"]] = (
                bucket["_items"].get(row["item_name"], 0.0) + row["spend"]
            )
        rows = []
        for bucket in buckets.values():
            items = bucket.pop("_items")
            bucket["invoice_count"] = len(bucket.pop("_invoices"))
            bucket["distinct_items"] = len(items)
            # Quantities are NOT summed at group level — kilograms of flour
            # plus litres of oil is not a number. Spend is the comparable axis.
            bucket["top_items"] = [
                {"item": n, "spend": round(s, 2)}
                for n, s in sorted(items.items(), key=lambda kv: kv[1], reverse=True)[
                    :3
                ]
            ]
            bucket["spend"] = round(bucket["spend"], 2)
            bucket["credit_amount"] = round(bucket["credit_amount"], 2)
            rows.append(bucket)
        rows.sort(key=lambda r: r["spend"], reverse=True)
        result["summary"] = {
            "rows": len(rows),
            "lines": len(flat),
            "invoices": len({r["invoice_id"] for r in flat}),
            "distinct_items": len({r["item_id"] for r in flat}),
            "net_spend": round(sum(r["spend"] for r in flat), 2),
            "credit_amount": round(
                sum(r["spend"] for r in flat if r["is_credit"] or r["quantity"] < 0), 2
            ),
        }
        result["rows"] = cap_rows(rows, label_key)
        return result

    if group_by == "line":
        rows = [
            {
                **r,
                "quantity": round(r["quantity"], 4),
                "quantity_base": (
                    round(r["quantity_base"], 4)
                    if r["quantity_base"] is not None
                    else None
                ),
                "spend": round(r["spend"], 2),
            }
            for r in flat
        ]
        rows.sort(key=lambda r: (r["date"], r["item_name"]))
        if len(rows) > _LINE_CAP:
            log(
                "line rows capped at "
                + str(_LINE_CAP)
                + " of "
                + str(len(rows))
                + " — narrow the period or group by item"
            )
            warnings.append(
                "Showing the first "
                + str(_LINE_CAP)
                + " of "
                + str(len(rows))
                + " lines. Narrow the period, or use group_by=item."
            )
            rows = rows[:_LINE_CAP]
        result["summary"] = {
            "lines": len(flat),
            "invoices": len({r["invoice_id"] for r in flat}),
            "distinct_items": len({r["item_id"] for r in flat}),
            "net_spend": round(sum(r["spend"] for r in flat), 2),
        }
        result["rows"] = rows
        return result

    buckets = {}
    for row in flat:
        key = (
            (row["item_id"], row["supplier_id"])
            if group_by == "item_supplier"
            else (row["item_id"],)
        )
        bucket = buckets.get(key)
        if bucket is None:
            bucket = buckets[key] = {
                "item_id": row["item_id"],
                "item_code": row["item_code"],
                "item_name": row["item_name"],
                "category": row["category"],
                "base_unit": row["base_unit"],
                "quantity_base": 0.0,
                "spend": 0.0,
                "credit_amount": 0.0,
                "_invoices": set(),
                "_suppliers": set(),
                "_units": set(),
                "_prices": [],
            }
            if group_by == "item_supplier":
                bucket["supplier_id"] = row["supplier_id"]
                bucket["supplier_name"] = row["supplier_name"]
        if row["quantity_base"] is not None:
            bucket["quantity_base"] += row["quantity_base"]
        bucket["spend"] += row["spend"]
        if row["is_credit"] or row["quantity"] < 0:
            bucket["credit_amount"] += row["spend"]
        bucket["_invoices"].add(row["invoice_id"])
        if row["supplier_name"]:
            bucket["_suppliers"].add(row["supplier_name"])
        if row["unit_name"]:
            bucket["_units"].add(row["unit_name"])
        # Price observations come from actual RECEIPTS only. A credit carries
        # the original price with a negative quantity; letting it set "last
        # price" would report a change that never happened on an order.
        if (
            not row["is_credit"]
            and row["quantity"] > 0
            and row["cost_per_base_unit"] is not None
        ):
            bucket["_prices"].append((row["date"], row["cost_per_base_unit"]))
        if bucket["base_unit"] is None:
            bucket["base_unit"] = row["base_unit"]

    rows = []
    for bucket in buckets.values():
        prices = sorted(bucket.pop("_prices"), key=lambda p: p[0])
        invoices_seen = bucket.pop("_invoices")
        suppliers_seen = bucket.pop("_suppliers")
        units_seen = bucket.pop("_units")
        bucket["quantity_base"] = round(bucket["quantity_base"], 4)
        bucket["spend"] = round(bucket["spend"], 2)
        bucket["credit_amount"] = round(bucket["credit_amount"], 2)
        bucket["invoice_count"] = len(invoices_seen)
        bucket["supplier_count"] = len(suppliers_seen)
        bucket["suppliers"] = sorted(suppliers_seen)
        bucket["units_seen"] = sorted(units_seen)
        if prices:
            values = [p[1] for p in prices]
            first, last = values[0], values[-1]
            bucket["unit_cost_first"] = first
            bucket["unit_cost_last"] = last
            bucket["unit_cost_min"] = min(values)
            bucket["unit_cost_max"] = max(values)
            bucket["unit_cost_avg"] = round(sum(values) / len(values), 4)
            bucket["price_change_pct"] = (
                round((last - first) / first * 100, 1) if first else None
            )
        else:
            for field in (
                "unit_cost_first",
                "unit_cost_last",
                "unit_cost_min",
                "unit_cost_max",
                "unit_cost_avg",
                "price_change_pct",
            ):
                bucket[field] = None
        rows.append(bucket)

    rows.sort(key=lambda r: r["spend"], reverse=True)

    moved = [r for r in rows if r.get("price_change_pct")]
    result["summary"] = {
        "rows": len(rows),
        "lines": len(flat),
        "invoices": len({r["invoice_id"] for r in flat}),
        # From the unrounded LINES, not from the rounded rows above — adding
        # 267 already-rounded subtotals drifts the headline off the source by a
        # cent, and the headline is the number people quote.
        "net_spend": round(sum(r["spend"] for r in flat), 2),
        "credit_amount": round(
            sum(r["spend"] for r in flat if r["is_credit"] or r["quantity"] < 0), 2
        ),
        "items_with_price_change": len(moved),
        # Deliberately not "totals": summing a unit cost is meaningless, so only
        # the columns where a sum means something are added up here.
        "_note": (
            "net_spend is quantity x unit cost summed over every line, credits "
            "included (they subtract). Unit costs are per base unit and are "
            "never summed."
        ),
    }
    result["rows"] = cap_rows(rows, "item_name")
    return result
