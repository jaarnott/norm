# ruff: noqa: F821 — `datetime` and `json` are injected into the sandbox
# namespace by app/connectors/function_executor.py; not imports.
#
# Canonical function_code for `loadedhub.get_purchase_cogs` — cost of goods as
# PURCHASES against sales, for a venue that does no stocktakes (installed by
# scripts/sync_purchase_cogs_config.py, Oct 2026; plan in
# docs/plans/procurement-cogs-dashboard.md).
#
# Requires consolidator_config:
#   {"max_api_calls": 12, "allowed_write_actions": []}   # reads only, always
#   (resolve_dates + list_venues, one received-items read, one daily sales
#   read, and one item-sales read per venue for the category split)
#
# WHAT IT IS FOR
#
# Loaded's own cost-of-goods reports need stock counts or recipes. A venue
# that only receives its invoices has neither, but it has what it bought and
# what it sold, and "purchases over sales" is the cost of goods such a venue
# runs on. Both halves already come from tools the agent uses — the received
# invoice lines (`get_received_items_for_period`, by invoice date, ex-GST,
# split by Loaded's three categories, with freight) and POS sales
# (`get_sales`, ex-GST) — so this joins them per month or trading week, per
# venue, and gives the dashboard and the agent one set of numbers.
#
# WHAT THE NUMBERS MEAN
#
# - purchases = received invoice lines, ex-GST, credits negative; freight is
#   the invoices' own freight and is IN purchases unless include_freight is
#   false. Food + Beverage + Other + freight = what the invoices charged ex-GST
#   (the summary's reconciliation says whether it does, to the cent).
# - sales = POS sales ex-GST (Loaded's actual invoicesTax removed, so the GST
#   rate is never assumed). The food / beverage split of sales comes from
#   Loaded's POS item categories, applied to each bucket's sales as the
#   period's shares: a venue whose POS items are not categorised in Loaded
#   shows that share as uncategorised, and its food / beverage cost of goods
#   stays null rather than guessing.
# - cogs_pct = purchases / sales; null when there are no sales.
#
# Buckets are the invoice date (purchases) and the trading date (sales) in
# the same calendar months or ISO weeks, labelled the way get_sales labels
# them, so the two sides join by label.

_INTERVALS = ("month", "week")
_CONSUMED = (
    "period",
    "start",
    "end",
    "confirmed_by_user",
    "interval",
    "venues",
    "venue",
    "venue_id",
    "include_freight",
    "compare_previous",
)
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
_CATEGORY_KEY = {"food": "food", "beverage": "beverage", "other": "other"}
_CATEGORY_LABEL = {
    "food": "food",
    "beverage": "beverage",
    "other": "other",
    "other stock": "other",
}


def _num(value):
    if value is None or value is True or value is False:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _truthy(value, default):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "yes", "1")


def _bucket_of(date_text, mode):
    """(label, start date) — the same labels get_received_items_for_period
    and get_sales use for their month / week rows."""
    try:
        day = datetime.date.fromisoformat(str(date_text or "")[:10])
    except ValueError:
        return None, None
    if mode == "week":
        iso = day.isocalendar()
        monday = day - datetime.timedelta(days=day.weekday())
        return str(iso[0]) + "-W" + str(iso[1]).zfill(2), monday.isoformat()
    return _MONTH_NAMES[day.month - 1] + " " + str(day.year), day.replace(
        day=1
    ).isoformat()


def _window_from(resolved):
    if not isinstance(resolved, dict):
        return None
    data = resolved.get("data") if "data" in resolved else resolved
    if not isinstance(data, dict):
        return None
    window = data.get("window")
    return window if isinstance(window, dict) else None


def _shift(iso_text, seconds):
    """An ISO datetime moved by `seconds`, offset kept."""
    moment = datetime.datetime.fromisoformat(str(iso_text))
    return (moment + datetime.timedelta(seconds=seconds)).isoformat()


def _resolve_venues(params, call_api):
    """(venue_names | None, error | None). None = this call's own venue."""
    venues_param = params.get("venues")
    if isinstance(venues_param, str) and venues_param.strip():
        v = venues_param.strip()
        if v.lower() not in ("all", "all venues", "*", "group"):
            return [v], None
        listed = call_api("norm", "list_venues", {"connector": "loadedhub"})
        data = (
            listed.get("data")
            if isinstance(listed, dict) and "data" in listed
            else listed
        )
        rows = data.get("venues") if isinstance(data, dict) else None
        names = [
            r["name"]
            for r in rows or []
            if isinstance(r, dict) and r.get("connected") and r.get("name")
        ]
        if not names:
            return None, {"error": "could not list connected venues for the fan-out"}
        return names, None
    if isinstance(venues_param, list):
        names = [str(v).strip() for v in venues_param if str(v).strip()]
        if names:
            return names, None
    return None, None


def _pct(numerator, denominator):
    if denominator is None or denominator == 0 or numerator is None:
        return None
    return round(numerator / denominator, 4)


def _category_shares(items_result):
    """{food, beverage, other, uncategorised}: each POS category's share of
    the venue's item sales, from one get_sales items read."""
    rows = items_result.get("rows") if isinstance(items_result, dict) else None
    totals = {"food": 0.0, "beverage": 0.0, "other": 0.0, "uncategorised": 0.0}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        amount = _num(r.get("sales")) or 0.0
        key = _CATEGORY_LABEL.get(
            str(r.get("category") or "").strip().lower(), "uncategorised"
        )
        totals[key] += amount
    grand = sum(totals.values())
    if grand <= 0:
        return None
    return {k: v / grand for k, v in totals.items()}


def run(params, call_api, log, call_api_parallel=None):
    interval = str(params.get("interval") or "month").strip().lower()
    if interval not in _INTERVALS:
        return {"error": "interval must be 'month' or 'week' — got " + repr(interval)}
    include_freight = _truthy(params.get("include_freight"), True)
    compare_previous = _truthy(params.get("compare_previous"), True)

    period = str(params.get("period") or "").strip()
    start = params.get("start")
    end = params.get("end")
    if not period and not (start and end):
        return {
            "error": (
                "Give a period in plain English (e.g. 'last month', 'last 12 "
                "months') — only pass start and end for exact clock times."
            )
        }

    # The window, on the venue's calendar — resolved as a tool call so this
    # code, shared by every organisation, never carries a calendar.
    resolve_args = {}
    if params.get("venue_id"):
        resolve_args["venue_id"] = params["venue_id"]
    if period:
        resolve_args["query"] = period
    else:
        resolve_args["start"] = start
        resolve_args["end"] = end
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

    venues, venue_error = _resolve_venues(params, call_api)
    if venue_error:
        return venue_error
    multi = venues is not None and len(venues) > 1

    # The previous period rides in the same reads: the window is extended
    # backwards by its own length, and buckets before the asked start are the
    # comparison. One read each, no second round trip.
    w_start, w_end = str(window["start"]), str(window["end"])
    length = (
        datetime.datetime.fromisoformat(w_end)
        - datetime.datetime.fromisoformat(w_start)
    ).total_seconds() + 1
    fetch_start = _shift(w_start, -length) if compare_previous else w_start
    common = {"start": fetch_start, "end": w_end, "confirmed_by_user": True}
    if params.get("venue_id"):
        common["venue_id"] = params["venue_id"]
    if venues:
        common["venues"] = venues

    purchases = call_api(
        "loadedhub",
        "get_received_items_for_period",
        {**common, "group_by": interval},
    )
    if not isinstance(purchases, dict) or purchases.get("error"):
        return {
            "error": "purchases: "
            + str(purchases.get("error") if isinstance(purchases, dict) else purchases),
            "window": window,
        }
    sales = call_api(
        "loadedhub",
        "get_sales",
        {
            **common,
            "breakdown": "daily",
            "interval": "1.00:00:00",
            "tax": "exclude",
        },
    )
    if not isinstance(sales, dict) or sales.get("error"):
        return {
            "error": "sales: "
            + str(sales.get("error") if isinstance(sales, dict) else sales),
            "window": window,
        }

    # Category shares of sales, one items read per venue (the items read
    # merges venues, and the split is per venue).
    targets = venues or [None]
    share_calls = []
    for v in targets:
        args = {
            "period": period,
            "breakdown": "items",
            "tax": "exclude",
            "top": 5000,
        }
        if not period:
            args.update({"start": w_start, "end": w_end, "confirmed_by_user": True})
        if v:
            args["venues"] = [v]
        if params.get("venue_id"):
            args["venue_id"] = params["venue_id"]
        share_calls.append(("loadedhub", "get_sales", args))
    if call_api_parallel and len(share_calls) > 1:
        share_results = call_api_parallel(share_calls)
    else:
        share_results = [call_api(c, a, p) for (c, a, p) in share_calls]
    shares = {}
    share_notes = []
    for v, res in zip(targets, share_results):
        if isinstance(res, dict) and res.get("error"):
            share_notes.append(
                (str(v) + ": " if v else "")
                + "sales by category unavailable ("
                + str(res["error"])
                + ")"
            )
            continue
        shares[v] = _category_shares(res)

    def venue_key(name):
        # Single-venue reads carry no venue (or None); multi-venue ones carry
        # the name. One key per venue either way.
        return name if multi else None

    # --- purchases per (venue, bucket)
    bought = {}
    for r in purchases.get("rows") or []:
        if not isinstance(r, dict) or not r.get("bucket"):
            continue
        key = (venue_key(r.get("venue")), r["bucket"])
        bought[key] = {
            "bucket_start": r.get("bucket_start"),
            "food": _num(r.get("food")) or 0.0,
            "beverage": _num(r.get("beverage")) or 0.0,
            "other": _num(r.get("other")) or 0.0,
            "freight": _num(r.get("freight")) or 0.0,
            "credit_amount": _num(r.get("credit_amount")) or 0.0,
            "invoice_count": int(r.get("invoice_count") or 0),
        }

    # --- sales per (venue, bucket), from daily rows
    sold = {}
    for r in sales.get("rows") or []:
        if not isinstance(r, dict) or not r.get("date"):
            continue
        label, bucket_start = _bucket_of(r["date"], interval)
        if not label:
            continue
        key = (venue_key(r.get("venue")), label)
        entry = sold.setdefault(key, {"bucket_start": bucket_start, "sales": 0.0})
        entry["sales"] += _num(r.get("actual")) or 0.0

    def make_row(key):
        v, label = key
        b = bought.get(key) or {}
        s = sold.get(key) or {}
        food = b.get("food", 0.0)
        beverage = b.get("beverage", 0.0)
        other = b.get("other", 0.0)
        freight = b.get("freight", 0.0)
        total_purchases = (
            food + beverage + other + (freight if include_freight else 0.0)
        )
        sales_amt = s.get("sales", 0.0)
        share = shares.get(v if multi else targets[0])
        food_sales = beverage_sales = other_sales = uncategorised = None
        if share:
            food_sales = sales_amt * share["food"]
            beverage_sales = sales_amt * share["beverage"]
            other_sales = sales_amt * share["other"]
            uncategorised = sales_amt * share["uncategorised"]
        row = {
            "bucket": label,
            "bucket_start": b.get("bucket_start") or s.get("bucket_start"),
            "purchases": round(total_purchases, 2),
            "food": round(food, 2),
            "beverage": round(beverage, 2),
            "other": round(other, 2),
            "freight": round(freight, 2),
            "credit_amount": round(b.get("credit_amount", 0.0), 2),
            "invoice_count": b.get("invoice_count", 0),
            "sales": round(sales_amt, 2),
            "food_sales": None if food_sales is None else round(food_sales, 2),
            "beverage_sales": (
                None if beverage_sales is None else round(beverage_sales, 2)
            ),
            "other_sales": None if other_sales is None else round(other_sales, 2),
            "uncategorised_sales": (
                None if uncategorised is None else round(uncategorised, 2)
            ),
            "cogs_pct": _pct(total_purchases, sales_amt),
            "food_cogs_pct": _pct(food, food_sales),
            "beverage_cogs_pct": _pct(beverage, beverage_sales),
        }
        if multi:
            row["venue"] = v
        return row

    keys = set(bought) | set(sold)
    all_rows = [make_row(k) for k in keys]
    all_rows.sort(key=lambda r: (r["bucket_start"] or "", r.get("venue") or ""))

    def bucket_end(start_text):
        day = datetime.date.fromisoformat(start_text)
        if interval == "week":
            return (day + datetime.timedelta(days=7)).isoformat()
        nxt = day.replace(year=day.year + (day.month == 12), month=day.month % 12 + 1)
        return nxt.isoformat()

    # A bucket is the asked period's when any of it falls inside the window
    # (a week that straddles the 1st belongs to "this month"); buckets that
    # end before the window began are the comparison period.
    asked_from, asked_to = w_start[:10], w_end[:10]
    current, previous = [], []
    for r in all_rows:
        start_text = r["bucket_start"] or ""
        if not start_text:
            continue
        if bucket_end(start_text) > asked_from and start_text <= asked_to:
            current.append(r)
        elif start_text < asked_from:
            previous.append(r)

    def totals(rows):
        t = {
            k: round(sum(_num(r.get(k)) or 0.0 for r in rows), 2)
            for k in (
                "purchases",
                "food",
                "beverage",
                "other",
                "freight",
                "credit_amount",
                "sales",
            )
        }
        have_split = any(r.get("food_sales") is not None for r in rows)
        for k in ("food_sales", "beverage_sales", "other_sales", "uncategorised_sales"):
            t[k] = (
                round(sum(_num(r.get(k)) or 0.0 for r in rows), 2)
                if have_split
                else None
            )
        t["invoice_count"] = sum(int(r.get("invoice_count") or 0) for r in rows)
        t["cogs_pct"] = _pct(t["purchases"], t["sales"])
        t["food_cogs_pct"] = _pct(t["food"], t["food_sales"])
        t["beverage_cogs_pct"] = _pct(t["beverage"], t["beverage_sales"])
        t["uncategorised_sales_pct"] = (
            _pct(t["uncategorised_sales"], t["sales"]) if have_split else None
        )
        return t

    summary = totals(current)
    summary["buckets"] = len({r["bucket"] for r in current})
    summary["venues"] = sorted({r.get("venue") for r in current if r.get("venue")})
    summary["include_freight"] = include_freight
    purchase_summary = purchases.get("summary") or {}
    if isinstance(purchase_summary.get("reconciliation"), dict):
        summary["reconciliation"] = purchase_summary["reconciliation"]
    if compare_previous:
        prev = totals(previous)
        summary["previous"] = {
            k: prev[k]
            for k in (
                "purchases",
                "sales",
                "cogs_pct",
                "food",
                "beverage",
                "food_cogs_pct",
                "beverage_cogs_pct",
            )
        }
        if summary["cogs_pct"] is not None and prev["cogs_pct"] is not None:
            summary["cogs_pct_change"] = round(
                summary["cogs_pct"] - prev["cogs_pct"], 4
            )
        if prev["purchases"]:
            summary["purchases_change_pct"] = round(
                (summary["purchases"] - prev["purchases"]) / abs(prev["purchases"]), 4
            )

    warnings = list(purchases.get("warnings") or []) + share_notes
    if summary.get("uncategorised_sales_pct"):
        warnings.append(
            str(round(summary["uncategorised_sales_pct"] * 100, 1))
            + "% of sales are on POS items with no category in Loaded, so the "
            "food / beverage cost of goods is understated by that much — "
            "categorise the POS items in Loaded."
        )
    if not shares:
        warnings.append(
            "sales by category unavailable, so food / beverage cost of goods "
            "is not given"
        )

    return {
        "window": window,
        "interval": interval,
        "rows": current,
        "summary": summary,
        "warnings": warnings,
        "_note": (
            "purchases are received invoice lines ex-GST by invoice date (credits "
            "negative), sales are POS sales ex-GST; cogs_pct = purchases / sales. "
            "This is purchases-based cost of goods, not a stock-movement figure."
        ),
    }
