# ruff: noqa: F821 — `datetime` is injected into the sandbox namespace by
# app/connectors/function_executor.py; not an import.
#
# loadedhub.get_menu_item_costs — the chef's cost of goods (Oct 2026).
#
# Theoretical cost of goods per product (Loaded's own report: recipe cost ×
# sold, revenue ex GST) against ACTUAL purchases for the same window (received
# invoice lines, from get_received_items_for_period) — the gap between the two
# is what a chef acts on (waste, portioning, uncosted recipes). A product that
# sold with no cost has no recipe, or an uncosted one: the report says so per
# product and as a share of revenue.
#
# Composes:
#   loadedhub.get_cogs_detail_for_period   per product: product, group,
#                                          category, quantity, revenue (ex
#                                          GST), cost, discounts — one call
#                                          per bucket when a trend is asked
#   loadedhub.get_received_items_for_period group_by month / week — purchases
#                                          split Food / Beverage / Other plus
#                                          freight, per bucket
#
# Requires consolidator_config {"max_api_calls": 20, "allowed_write_actions": []}.

_INTERVALS = ("month", "week")
_MAX_BUCKETS = 13
_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct(numerator, denominator):
    if numerator is None or not denominator:
        return None
    return round(numerator / denominator, 4)


def _truthy(value, default):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("false", "0", "no", "off")


def _window_from(resolved):
    if not isinstance(resolved, dict):
        return None
    data = resolved.get("data") if "data" in resolved else resolved
    if not isinstance(data, dict):
        return None
    window = data.get("window")
    return window if isinstance(window, dict) else None


def _shift(iso_text, days):
    moment = datetime.datetime.fromisoformat(str(iso_text))
    return (moment + datetime.timedelta(days=days)).isoformat()


def _trading_date(iso_text, day_start=None):
    """The trading date an instant belongs to (the night before when the clock
    is earlier than the venue's day start; noon when none is known)."""
    moment = datetime.datetime.fromisoformat(str(iso_text))
    cut = datetime.time(12, 0)
    if hasattr(day_start, "hour"):
        cut = datetime.time(day_start.hour, getattr(day_start, "minute", 0))
    elif isinstance(day_start, str) and ":" in day_start:
        parts = day_start.split(":")
        try:
            cut = datetime.time(int(parts[0]), int(parts[1]))
        except ValueError:
            pass
    day = moment.date()
    if moment.time() < cut:
        day = day - datetime.timedelta(days=1)
    return day.isoformat()


def _bucket_of(iso_date, interval):
    day = datetime.date.fromisoformat(iso_date[:10])
    if interval == "week":
        monday = day - datetime.timedelta(days=day.weekday())
        iso = day.isocalendar()
        return str(iso[0]) + "-W" + str(iso[1]).zfill(2), monday.isoformat()
    return (
        _MONTH_NAMES[day.month - 1] + " " + str(day.year),
        day.replace(day=1).isoformat(),
    )


def _bucket_windows(w_start, w_end, interval, day_start):
    """[(label, bucket start date, window start ISO, window end ISO)] covering
    the window, each cut at the venue's day start so a trading day is whole.
    Oldest first; at most _MAX_BUCKETS (the most recent ones)."""
    start = datetime.datetime.fromisoformat(str(w_start))
    end = datetime.datetime.fromisoformat(str(w_end))
    first_date = datetime.date.fromisoformat(_trading_date(w_start, day_start))
    last_date = datetime.date.fromisoformat(_trading_date(w_end, day_start))
    clock = start.timetz()
    out = []
    cursor = first_date
    while cursor <= last_date:
        label, b_start = _bucket_of(cursor.isoformat(), interval)
        b_start_date = datetime.date.fromisoformat(b_start)
        if interval == "week":
            nxt = b_start_date + datetime.timedelta(days=7)
        else:
            nxt = (b_start_date.replace(day=28) + datetime.timedelta(days=4)).replace(
                day=1
            )
        ws = max(datetime.datetime.combine(b_start_date, clock), start)
        we = min(
            datetime.datetime.combine(nxt, clock) - datetime.timedelta(seconds=1), end
        )
        out.append((label, b_start, ws.isoformat(), we.isoformat()))
        cursor = nxt
    return out[-_MAX_BUCKETS:]


def _rows_of(payload):
    if isinstance(payload, list):
        return payload if payload and isinstance(payload[0], dict) else []
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
        for value in payload.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value
    return []


def _category_key(text):
    t = str(text or "").strip().lower()
    if t.startswith("food"):
        return "food"
    if t.startswith("bev") or t.startswith("drink"):
        return "beverage"
    return "other"


def run(params, call_api, log, call_api_parallel=None):
    interval = str(params.get("interval") or "").strip().lower()
    if interval and interval not in _INTERVALS:
        return {"error": "interval must be 'month' or 'week' — got " + repr(interval)}
    top = params.get("top") or 25
    try:
        top = max(1, int(top))
    except (TypeError, ValueError):
        top = 25
    include_freight = _truthy(params.get("include_freight"), True)

    period = str(params.get("period") or "").strip()
    start, end = params.get("start"), params.get("end")
    if not period and not (start and end):
        return {"error": "Give a period in plain English (e.g. 'this month')."}
    resolve_args = {}
    if params.get("venue_id"):
        resolve_args["venue_id"] = params["venue_id"]
    if period:
        resolve_args["query"] = period
    else:
        resolve_args["start"], resolve_args["end"] = start, end
    window = _window_from(call_api("norm", "resolve_dates", resolve_args))
    if not window or not window.get("start") or not window.get("end"):
        return {"error": "Could not resolve the period " + repr(period or "given")}
    if not window.get("trading_aligned") and not params.get("confirmed_by_user"):
        return {
            "needs_confirmation": True,
            "window": window,
            "question": (
                "These times are not this venue's trading day. "
                + str(window.get("description", ""))
                + " Did the user explicitly ask for these exact clock times? If "
                "yes, call again with confirmed_by_user=true."
            ),
        }
    w_start, w_end = str(window["start"]), str(window["end"])
    passthrough = {}
    for key in ("venue_id", "venue"):
        if params.get(key):
            passthrough[key] = params[key]

    # --- the reads: one product report per bucket (or one for the window),
    # and the purchases bucketed the same way.
    buckets = (
        _bucket_windows(w_start, w_end, interval, window.get("day_start"))
        if interval
        else [(None, None, w_start, w_end)]
    )
    calls = [
        (
            "loadedhub",
            "get_cogs_detail_for_period",
            {**passthrough, "start": bs, "end": be, "confirmed_by_user": True},
        )
        for (_label, _bstart, bs, be) in buckets
    ]
    calls.append(
        (
            "loadedhub",
            "get_received_items_for_period",
            {
                **passthrough,
                "start": w_start,
                "end": w_end,
                "confirmed_by_user": True,
                "group_by": interval or "month",
            },
        )
    )
    if call_api_parallel and len(calls) > 1:
        results = call_api_parallel(calls)
    else:
        results = [call_api(c, a, p) for (c, a, p) in calls]
    purchases = results[-1]
    detail_results = results[:-1]

    warnings = []
    if not isinstance(purchases, dict) or purchases.get("error"):
        warnings.append(
            "purchases unavailable ("
            + str(purchases.get("error") if isinstance(purchases, dict) else purchases)
            + ") — actual cost of goods not given"
        )
        purchases = {}

    # --- products, merged across buckets
    products = {}
    trend = []
    any_detail = False
    for (label, b_start, _bs, _be), res in zip(buckets, detail_results):
        if not isinstance(res, dict) or res.get("error"):
            msg = str(res.get("error") if isinstance(res, dict) else res)
            if label:
                warnings.append(label + ": product costs unavailable (" + msg + ")")
            else:
                return {"error": "product costs: " + msg, "window": window}
            continue
        any_detail = True
        b_rev = b_cost = 0.0
        for r in _rows_of(res):
            name = str(r.get("product") or "").strip() or "(unnamed)"
            p = products.get(name)
            if p is None:
                p = products[name] = {
                    "product": name,
                    "group": r.get("group"),
                    "category": r.get("category"),
                    "quantity": 0.0,
                    "revenue": 0.0,
                    "cost": 0.0,
                    "discounts": 0.0,
                }
            rev, cost = _num(r.get("revenue")) or 0.0, _num(r.get("cost")) or 0.0
            p["quantity"] += _num(r.get("quantity")) or 0.0
            p["revenue"] += rev
            p["cost"] += cost
            p["discounts"] += _num(r.get("discounts")) or 0.0
            b_rev += rev
            b_cost += cost
        if label:
            trend.append(
                {
                    "bucket": label,
                    "bucket_start": b_start,
                    "revenue": round(b_rev, 2),
                    "cost": round(b_cost, 2),
                    "theoretical_cogs_pct": _pct(b_cost, b_rev),
                    "purchases": None,
                    "actual_cogs_pct": None,
                }
            )
    if not any_detail:
        return {
            "error": "product costs: no report came back for the window",
            "window": window,
        }

    # --- actual purchases, total and per bucket / category
    actual_total = 0.0
    actual_by_cat = {"food": 0.0, "beverage": 0.0, "other": 0.0}
    by_bucket = {}
    for r in purchases.get("rows") or []:
        if not isinstance(r, dict):
            continue
        spend = sum(_num(r.get(k)) or 0.0 for k in ("food", "beverage", "other"))
        freight = _num(r.get("freight")) or 0.0
        amount = spend + (freight if include_freight else 0.0)
        actual_total += amount
        for k in actual_by_cat:
            actual_by_cat[k] += _num(r.get(k)) or 0.0
        if include_freight:
            actual_by_cat["other"] += freight
        if r.get("bucket_start"):
            by_bucket[r["bucket_start"]] = (
                by_bucket.get(r["bucket_start"], 0.0) + amount
            )
    for t in trend:
        if t["bucket_start"] in by_bucket:
            t["purchases"] = round(by_bucket[t["bucket_start"]], 2)
            t["actual_cogs_pct"] = _pct(t["purchases"], t["revenue"])

    # --- rows: products by revenue, with the ratios
    total_rev = sum(p["revenue"] for p in products.values())
    rows = []
    for p in products.values():
        row = {
            "product": p["product"],
            "group": p["group"],
            "category": p["category"],
            "quantity": round(p["quantity"], 2),
            "revenue": round(p["revenue"], 2),
            "cost": round(p["cost"], 2),
            "margin": round(p["revenue"] - p["cost"], 2),
            "cogs_pct": _pct(p["cost"], p["revenue"]) if p["cost"] else None,
            "revenue_share": _pct(p["revenue"], total_rev),
            "has_recipe": p["cost"] > 0,
        }
        rows.append(row)
    rows.sort(key=lambda r: -r["revenue"])
    missing = [r for r in rows if not r["has_recipe"] and r["revenue"] > 0]
    costed = [r for r in rows if r["has_recipe"]]
    dear = sorted(
        [r for r in costed if r["revenue"] > 0],
        key=lambda r: -(r["cogs_pct"] or 0),
    )

    def cap(items, n, label_key="product"):
        if len(items) <= n:
            return items
        kept, rest = items[:n], items[n:]
        others = {
            "product": "(others: " + str(len(rest)) + " products)",
            "group": None,
            "category": None,
            "quantity": round(sum(r["quantity"] for r in rest), 2),
            "revenue": round(sum(r["revenue"] for r in rest), 2),
            "cost": round(sum(r["cost"] for r in rest), 2),
            "margin": round(sum(r["margin"] for r in rest), 2),
            "cogs_pct": _pct(
                sum(r["cost"] for r in rest), sum(r["revenue"] for r in rest)
            ),
            "revenue_share": _pct(sum(r["revenue"] for r in rest), total_rev),
            "has_recipe": None,
        }
        return kept + [others]

    # --- by category: theoretical from the products, actual from the invoices
    cats = {}
    for p in products.values():
        key = _category_key(p["category"])
        c = cats.setdefault(
            key, {"category": key.capitalize(), "revenue": 0.0, "cost": 0.0}
        )
        c["revenue"] += p["revenue"]
        c["cost"] += p["cost"]
    by_category = []
    for key in ("food", "beverage", "other"):
        c = cats.get(key)
        if not c and not actual_by_cat.get(key):
            continue
        rev = round((c or {}).get("revenue", 0.0), 2)
        cost = round((c or {}).get("cost", 0.0), 2)
        bought = round(actual_by_cat.get(key, 0.0), 2) if purchases else None
        by_category.append(
            {
                "category": key.capitalize(),
                "revenue": rev,
                "cost": cost,
                "theoretical_cogs_pct": _pct(cost, rev),
                "purchases": bought,
                "actual_cogs_pct": _pct(bought, rev) if bought is not None else None,
            }
        )

    total_cost = sum(p["cost"] for p in products.values())
    rev_missing = sum(r["revenue"] for r in missing)
    summary = {
        "revenue": round(total_rev, 2),
        "cost": round(total_cost, 2),
        "theoretical_cogs_pct": _pct(total_cost, total_rev),
        "purchases": round(actual_total, 2) if purchases else None,
        "actual_cogs_pct": _pct(actual_total, total_rev) if purchases else None,
        "products": len(rows),
        "products_without_recipe": len(missing),
        "revenue_without_recipe": round(rev_missing, 2),
        "revenue_without_recipe_pct": _pct(rev_missing, total_rev),
        "include_freight": include_freight,
    }
    if (
        summary["theoretical_cogs_pct"] is not None
        and summary["actual_cogs_pct"] is not None
    ):
        summary["gap_pts"] = round(
            (summary["actual_cogs_pct"] - summary["theoretical_cogs_pct"]) * 100, 1
        )
    if rev_missing:
        warnings.append(
            str(round((summary["revenue_without_recipe_pct"] or 0) * 100, 1))
            + "% of revenue is on products with no recipe cost in Loaded ("
            + str(len(missing))
            + " products), so the theoretical cost of goods is understated by that "
            "much — the top ones are listed under missing."
        )

    return {
        "window": window,
        "interval": interval or None,
        "rows": cap(rows, top),
        "missing": missing[:top],
        "dear": dear[:top],
        "by_category": by_category,
        "trend": trend,
        "summary": summary,
        "warnings": warnings,
        "_note": (
            "revenue is ex GST and cost is Loaded's recipe cost × quantity sold "
            "(theoretical); purchases are received invoice lines ex GST for the "
            "same window (actual). A product with no cost has no recipe, or an "
            "uncosted one — it is not free."
        ),
    }
