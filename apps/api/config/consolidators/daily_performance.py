# ruff: noqa: F821 — `datetime` is injected into the sandbox namespace by
# app/connectors/function_executor.py; not an import.
# loadedhub.get_daily_performance — the operations join (Oct 2026).
#
# One row per trading day (or week / month) per venue with everything an
# operator reads together: sales incl. and ex GST, budget, last year, orders
# and average order, rostered and worked hours and cost, wage % and budget
# achievement. It composes the tools that already know each half:
#
#   loadedhub.get_sales    daily, compare budget + last_year (incl GST — the
#                          figure venues read), again ex GST (what wage % is
#                          measured on), and measure 'orders'
#   loadedhub.get_labour   attendance by day (roster vs clock-ins)
#
# Built for the Operations dashboard, which used to spend a model call per
# tile per venue resolving "today" and showed no comparison at all; the join
# gives it one read per tile and the comparisons on the same row. Requires
# consolidator_config {"max_api_calls": 14, "allowed_write_actions": []}.
#
# Lessons carried over from get_purchase_cogs: a dashboard hands this tool a
# venue_id, and the tools underneath take a venue by NAME, so the id is mapped
# through norm.list_venues; a failed or empty sales read is an error, never
# zero sales.

_INTERVALS = ("day", "week", "month")
_CONSUMED = {
    "period",
    "start",
    "end",
    "confirmed_by_user",
    "interval",
    "venues",
    "venue",
    "venue_id",
}
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
_MONTH_SHORT = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}
_DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct(numerator, denominator):
    if numerator is None or not denominator:
        return None
    return round(numerator / denominator, 4)


def _window_from(resolved):
    if not isinstance(resolved, dict):
        return None
    data = resolved.get("data") if "data" in resolved else resolved
    if not isinstance(data, dict):
        return None
    window = data.get("window")
    return window if isinstance(window, dict) else None


def _venue_list(call_api):
    listed = call_api("norm", "list_venues", {"connector": "loadedhub"})
    data = (
        listed.get("data") if isinstance(listed, dict) and "data" in listed else listed
    )
    rows = data.get("venues") if isinstance(data, dict) else None
    return [r for r in rows or [] if isinstance(r, dict)]


def _resolve_venues(params, call_api):
    """(names | None, venue_name | None, error | None). A list means a
    multi-venue read; a single name means this call's venue."""
    venues_param = params.get("venues")
    if isinstance(venues_param, str) and venues_param.strip():
        v = venues_param.strip()
        if v.lower() in ("all", "all venues", "*", "group"):
            names = [
                r["name"]
                for r in _venue_list(call_api)
                if r.get("connected") and r.get("name")
            ]
            if not names:
                return (
                    None,
                    None,
                    {"error": "could not list connected venues for the fan-out"},
                )
            return names, None, None
        return [v], None, None
    if isinstance(venues_param, list):
        names = [str(v).strip() for v in venues_param if str(v).strip()]
        if names:
            return names, None, None
    name = str(params.get("venue") or "").strip()
    if not name and params.get("venue_id"):
        for r in _venue_list(call_api):
            if str(r.get("id")) == str(params["venue_id"]) and r.get("name"):
                name = str(r["name"])
                break
    if not name:
        return (
            None,
            None,
            {"error": "no venue: pass venues='all', a list of names, or one venue"},
        )
    return None, name, None


def _failure(res, what):
    """Why a nested read cannot be trusted, or None."""
    if not isinstance(res, dict):
        return what + ": " + str(res)
    if res.get("error"):
        return what + ": " + str(res["error"])
    note = str(res.get("note") or "")
    if "error" in note.lower():
        return what + ": " + note
    return None


def _iso_date(text, years):
    """An ISO date out of a row's date — ISO already, or the labour tool's
    'Mon 13 Oct' label resolved against the window's year(s)."""
    s = str(text or "").strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    parts = s.split()
    if len(parts) >= 3 and parts[-1] in _MONTH_SHORT:
        try:
            day, month = int(parts[-2]), _MONTH_SHORT[parts[-1]]
        except ValueError:
            return None
        for year in years:
            try:
                candidate = datetime.date(year, month, day)
            except ValueError:
                continue
            return candidate.isoformat()
    return None


def _bucket_of(iso_date, interval):
    """(label, bucket start ISO) for a trading date."""
    day = datetime.date.fromisoformat(iso_date)
    if interval == "week":
        monday = day - datetime.timedelta(days=day.weekday())
        iso = day.isocalendar()
        return str(iso[0]) + "-W" + str(iso[1]).zfill(2), monday.isoformat()
    if interval == "month":
        return (
            _MONTH_NAMES[day.month - 1] + " " + str(day.year),
            day.replace(day=1).isoformat(),
        )
    # No strftime: it reaches for a locale import the sandbox does not allow.
    return (
        _DOW[day.weekday()]
        + " "
        + str(day.day).zfill(2)
        + " "
        + _MONTH_NAMES[day.month - 1][:3],
        iso_date,
    )


def _labour_days(result, years):
    """{(venue, iso date): figures} out of get_labour attendance by day — the
    flat rows of a single venue, or each venue's `days` in a group read."""
    out = {}
    rows = result.get("rows") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        return out

    def take(venue, row):
        date = _iso_date(row.get("_sort") or row.get("date"), years)
        if not date:
            return
        key = (venue, date)
        acc = out.setdefault(
            key,
            {
                "rostered_hours": 0.0,
                "actual_hours": 0.0,
                "rostered_cost": 0.0,
                "actual_cost": 0.0,
            },
        )
        for k in acc:
            acc[k] += _num(row.get(k)) or 0.0

    for r in rows:
        if not isinstance(r, dict):
            continue
        if isinstance(r.get("days"), list):
            for d in r["days"]:
                if isinstance(d, dict):
                    take(r.get("venue"), d)
        else:
            take(r.get("venue"), r)
    return out


def run(params, call_api, log, call_api_parallel=None):
    interval = str(params.get("interval") or "day").strip().lower()
    if interval not in _INTERVALS:
        return {
            "error": "interval must be 'day', 'week' or 'month' — got " + repr(interval)
        }

    period = str(params.get("period") or "").strip()
    start, end = params.get("start"), params.get("end")
    if not period and not (start and end):
        return {"error": "Give a period in plain English (e.g. 'today', 'this week')."}
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

    venues, venue_name, verr = _resolve_venues(params, call_api)
    if verr:
        return verr
    multi = venues is not None and len(venues) > 1
    w_start, w_end = str(window["start"]), str(window["end"])
    years = sorted({int(w_start[:4]), int(w_end[:4])})

    common = {"start": w_start, "end": w_end, "confirmed_by_user": True}
    if params.get("venue_id"):
        common["venue_id"] = params["venue_id"]
    if venues:
        common["venues"] = venues
    elif venue_name:
        common["venue"] = venue_name
    daily = {"breakdown": "daily", "interval": "1.00:00:00"}
    calls = [
        (
            "loadedhub",
            "get_sales",
            {**common, **daily, "compare": ["budget", "last_year"]},
        ),
        ("loadedhub", "get_sales", {**common, **daily, "tax": "exclude"}),
        ("loadedhub", "get_sales", {**common, **daily, "measure": "orders"}),
        (
            "loadedhub",
            "get_labour",
            {**common, "view": "attendance", "group_by": "day", "include": "both"},
        ),
    ]
    if call_api_parallel:
        sales_in, sales_ex, orders, labour = call_api_parallel(calls)
    else:
        sales_in, sales_ex, orders, labour = [call_api(c, a, p) for (c, a, p) in calls]

    for res, what in ((sales_in, "sales"), (sales_ex, "sales ex GST")):
        failure = _failure(res, what)
        if failure:
            return {"error": failure, "window": window}
    warnings = []
    for res, what in ((orders, "orders"), (labour, "labour")):
        failure = _failure(res, what)
        if failure:
            warnings.append(failure + " — those columns are blank")

    def venue_key(name):
        return name if multi else None

    # --- per (venue, day)
    days = {}

    def day_of(venue, row):
        date = _iso_date(row.get("date"), years)
        if not date:
            return None
        return days.setdefault(
            (venue_key(venue), date),
            {
                "date": date,
                "sales": None,
                "sales_ex_gst": None,
                "budget": None,
                "last_year": None,
                "orders": None,
                "order_value": None,
                "rostered_hours": None,
                "actual_hours": None,
                "rostered_cost": None,
                "actual_cost": None,
            },
        )

    for r in sales_in.get("rows") or []:
        if isinstance(r, dict) and (d := day_of(r.get("venue"), r)) is not None:
            d["sales"] = _num(r.get("actual")) or 0.0
            d["budget"] = _num(r.get("budget"))
            d["last_year"] = _num(r.get("last_year"))
    for r in (sales_ex.get("rows") or []) if isinstance(sales_ex, dict) else []:
        if isinstance(r, dict) and (d := day_of(r.get("venue"), r)) is not None:
            d["sales_ex_gst"] = _num(r.get("actual")) or 0.0
    if isinstance(orders, dict) and not _failure(orders, "orders"):
        for r in orders.get("rows") or []:
            if isinstance(r, dict) and (d := day_of(r.get("venue"), r)) is not None:
                d["orders"] = int(round(_num(r.get("orders")) or 0))
                d["order_value"] = _num(r.get("order_value"))
    if isinstance(labour, dict) and not _failure(labour, "labour"):
        for (venue, date), figs in _labour_days(labour, years).items():
            key = (venue_key(venue), date)
            d = days.get(key)
            if d is None:
                d = days[key] = {
                    "date": date,
                    "sales": None,
                    "sales_ex_gst": None,
                    "budget": None,
                    "last_year": None,
                    "orders": None,
                    "order_value": None,
                    "rostered_hours": None,
                    "actual_hours": None,
                    "rostered_cost": None,
                    "actual_cost": None,
                }
            for k, v in figs.items():
                d[k] = round(v, 2)

    # --- buckets
    buckets = {}
    for (venue, date), d in days.items():
        label, b_start = _bucket_of(date, interval)
        b = buckets.setdefault(
            (venue, b_start),
            {
                "bucket": label,
                "bucket_start": b_start,
                "venue": venue,
                "_days": 0,
                **{
                    k: None
                    for k in (
                        "sales",
                        "sales_ex_gst",
                        "budget",
                        "last_year",
                        "orders",
                        "order_value",
                        "rostered_hours",
                        "actual_hours",
                        "rostered_cost",
                        "actual_cost",
                    )
                },
            },
        )
        b["_days"] += 1
        for k in (
            "sales",
            "sales_ex_gst",
            "budget",
            "last_year",
            "orders",
            "order_value",
            "rostered_hours",
            "actual_hours",
            "rostered_cost",
            "actual_cost",
        ):
            if d.get(k) is not None:
                b[k] = (b[k] or 0) + d[k]

    def finish(b):
        row = {k: v for k, v in b.items() if not k.startswith("_")}
        for k in (
            "sales",
            "sales_ex_gst",
            "budget",
            "last_year",
            "order_value",
            "rostered_hours",
            "actual_hours",
            "rostered_cost",
            "actual_cost",
        ):
            if row.get(k) is not None:
                row[k] = round(row[k], 2)
        row["average_order"] = (
            round(row["order_value"] / row["orders"], 2)
            if row.get("orders") and row.get("order_value") is not None
            else None
        )
        row["budget_pct"] = _pct(row.get("sales"), row.get("budget"))
        row["vs_last_year_pct"] = (
            round((row["sales"] - row["last_year"]) / row["last_year"], 4)
            if row.get("sales") is not None and row.get("last_year")
            else None
        )
        row["hours_variance"] = (
            round(row["actual_hours"] - row["rostered_hours"], 2)
            if row.get("actual_hours") is not None
            and row.get("rostered_hours") is not None
            else None
        )
        row["wage_pct"] = _pct(row.get("actual_cost"), row.get("sales_ex_gst"))
        if not multi:
            row.pop("venue", None)
        return row

    rows = [finish(b) for b in buckets.values()]
    rows.sort(key=lambda r: (r["bucket_start"], r.get("venue") or ""))

    def totals(rs):
        t = {}
        for k in (
            "sales",
            "sales_ex_gst",
            "budget",
            "last_year",
            "orders",
            "order_value",
            "rostered_hours",
            "actual_hours",
            "rostered_cost",
            "actual_cost",
        ):
            vals = [r[k] for r in rs if r.get(k) is not None]
            t[k] = round(sum(vals), 2) if vals else None
        t["average_order"] = (
            round(t["order_value"] / t["orders"], 2)
            if t.get("orders") and t.get("order_value")
            else None
        )
        t["budget_pct"] = _pct(t.get("sales"), t.get("budget"))
        t["vs_last_year_pct"] = (
            round((t["sales"] - t["last_year"]) / t["last_year"], 4)
            if t.get("sales") is not None and t.get("last_year")
            else None
        )
        t["hours_variance"] = (
            round(t["actual_hours"] - t["rostered_hours"], 2)
            if t.get("actual_hours") is not None and t.get("rostered_hours") is not None
            else None
        )
        t["wage_pct"] = _pct(t.get("actual_cost"), t.get("sales_ex_gst"))
        return t

    summary = totals(rows)
    summary["buckets"] = len({r["bucket_start"] for r in rows})
    summary["interval"] = interval
    # One row per venue, for the single venue too, so a "by venue" chart
    # has something to draw whichever way the dashboard fans out.
    if multi:
        by_venue = [
            {"venue": v, **totals([r for r in rows if r.get("venue") == v])}
            for v in venues
        ]
        summary["venues"] = list(venues)
    else:
        by_venue = [{"venue": venue_name or (venues or [None])[0], **totals(rows)}]
    summary["by_venue"] = by_venue
    for res in (sales_in, orders, labour):
        if (
            isinstance(res, dict)
            and res.get("note")
            and "error" not in str(res["note"]).lower()
        ):
            warnings.append(str(res["note"]))

    return {
        "window": window,
        "interval": interval,
        "rows": rows,
        "summary": summary,
        "by_venue": by_venue,
        "warnings": warnings,
        "_note": (
            "sales include GST (what the venue reads on its POS); wage_pct is "
            "actual labour cost over sales EX GST; budget_pct is sales over "
            "budget; last_year is the same trading days 52 weeks back."
        ),
    }
