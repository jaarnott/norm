# ruff: noqa: F821 — `datetime` and `json` are injected into the sandbox
# namespace by app/connectors/function_executor.py; they are not imports.
#
# Canonical function_code for `loadedhub.get_weekly_financials` — the Weekly
# Summary (Norm Finance; installed by scripts/sync_payroll_config.py; plan
# docs/plans/finance-migration.md §4, §7). Orbit's Financial Report and
# Financial KPIs in one read: per venue, the week and the month-to-date the
# week starts in, each with sales (incl and ex GST), budget, orders,
# discounts, voids, the food / beverage split, wages by cost group with the
# wage percentages, rostered vs actual labour and the cash-up variance, plus
# the daily rows of the week for the table. Renders through the
# `finance_weekly` display component in in-app chat.
#
# How the figures are read, and why:
#   - every venue's windows come from ITS OWN trading calendar
#     (norm.resolve_dates per venue: DSC and the Glass Goose start at 06:00,
#     the others at 07:00 — D11), so one group read never applies one
#     venue's day start to another;
#   - the month-to-date read covers the week, so the daily feeds (sales,
#     orders, voids, cash-ups, budgets, roster vs actual) are read ONCE over
#     the MTD range and the week is the subset of trading days — half the
#     calls of reading each window;
#   - sales are Loaded's /pos/sales buckets from the trading-day start
#     (Loaded's buckets follow the requested start, so each is a trading
#     day): `invoices` is money taken after discounts incl GST and
#     `invoicesTax` the tax inside it — ex GST is the difference, never
#     15/115 (an exempt rate sits beside GST);
#   - the food / beverage split is the POS items' revenue group (what
#     set_revenue_group maintains) over pre-discount item sales; the
#     shares are reported with the mismatch against sales rather than hidden
#     behind a substring match on category names;
#   - wages are Loaded's clock-in summary by role, bucketed by the role's
#     cost group (f → food, b → beverage), Orbit's Financial Report source;
#     the budget comes from get_budgets called directly (a consolidator may
#     nest one level deep, and get_sales' compare=budget would be two);
#   - the Norm-side budget plan (locked budget, monthly lines, adjusted
#     wages) is read through the built-in norm_finance.get_budget_plan when
#     it is registered, and left null with a note until it is.
#
# A venue whose reads fail is a flagged row, never a silent zero in the
# group total.
#
# Requires consolidator_config: {"max_api_calls": 120} — 1 + 13 per venue.

_DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
_FIGURE_SUMS = (
    "sales",
    "gst",
    "sales_ex_gst",
    "budget",
    "orders",
    "order_value",
    "bills",
    "discounts",
    "voids",
    "void_count",
    "waste",
    "food_sales",
    "beverage_sales",
    "other_sales",
    "wages",
    "food_wages",
    "beverage_wages",
    "other_wages",
    "hours",
    "kitchen_hours",
    "rostered_cost",
    "rostered_hours",
    "actual_cost",
    "actual_hours",
    "cash_variance",
    "total_variance",
    "cashup_buckets",
)


def _num(value):
    if isinstance(value, bool):
        return 0.0
    return float(value) if isinstance(value, (int, float)) else 0.0


def _flag(value, default=False):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "y")


def _failed(payload):
    return isinstance(payload, dict) and bool(payload.get("error"))


def _rows_of(payload):
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return [r for r in value if isinstance(r, dict)]
    return []


def _window_from(resolved):
    if not isinstance(resolved, dict):
        return None
    data = resolved.get("data") if "data" in resolved else resolved
    if not isinstance(data, dict):
        return None
    window = data.get("window")
    return window if isinstance(window, dict) else None


def _parse_instant(text):
    s = str(text or "").strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.datetime.fromisoformat(s)
    except ValueError:
        return None


def _day_start_time(window):
    raw = str(window.get("day_start") or "07:00")
    parts = raw.split(":")
    try:
        return datetime.time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except ValueError:
        return datetime.time(7, 0)


def _trading_date(iso_text, day_start, local):
    """The trading day a bucket start belongs to. Loaded stamps some feeds
    with the venue's offset and others in UTC (the sales feed: 17:00Z for a
    06:00 NZDT bucket), so a UTC instant is first moved onto the venue's
    clock; a start before the day start is the previous date."""
    moment = local(_parse_instant(iso_text))
    if moment is None:
        return None
    day = moment.date()
    if moment.time() < day_start:
        day = day - datetime.timedelta(days=1)
    return day


def _offset_of(iso_text):
    moment = _parse_instant(iso_text)
    return moment.utcoffset() if moment is not None else None


def _dst_sunday(first, last):
    """The Sunday between two dates on which NZ or AU clocks change, if any:
    the last Sunday of September, the first Sunday of April or the first
    Sunday of October (QLD never changes, so its offsets never differ)."""
    d = first
    while d <= last:
        if d.weekday() == 6 and (
            (d.month == 4 and d.day <= 7)
            or (d.month == 10 and d.day <= 7)
            or (d.month == 9 and d.day + 7 > 30)
        ):
            return d
        d = d + datetime.timedelta(days=1)
    return None


def _localiser(start_iso, end_iso):
    """instant -> the venue's wall clock: a UTC instant moves onto the
    window's start offset, or its end offset once past the clock change the
    window may straddle; an instant that already carries an offset is kept."""
    start_off = _offset_of(start_iso)
    end_off = _offset_of(end_iso)
    change_at = None
    if start_off is not None and end_off is not None and start_off != end_off:
        sunday = _dst_sunday(
            _parse_instant(start_iso).date(), _parse_instant(end_iso).date()
        )
        if sunday is not None:
            change_at = datetime.datetime(
                sunday.year,
                sunday.month,
                sunday.day,
                2,
                30,
                tzinfo=datetime.timezone(start_off),
            )

    def local(moment):
        if moment is None or moment.utcoffset() != datetime.timedelta(0):
            return moment
        if start_off is None or start_off == datetime.timedelta(0):
            return moment
        off = start_off
        if change_at is not None and moment >= change_at:
            off = end_off
        return moment.astimezone(datetime.timezone(off))

    return local


def _group_of(name):
    s = str(name or "").strip().casefold()
    if not s:
        return "other"
    if "food" in s or "kitchen" in s:
        return "food"
    if "bev" in s or "drink" in s or "bar" in s or "liquor" in s:
        return "beverage"
    return "other"


def _cost_group(code):
    s = str(code or "").strip().casefold()
    if s == "f":
        return "food"
    if s == "b":
        return "beverage"
    return "other"


def _pct(numerator, denominator):
    if numerator is None or not denominator:
        return None
    return round(numerator / denominator, 4)


def _blank():
    return {k: 0.0 for k in _FIGURE_SUMS}


def _finish(f):
    """Ratios and rounding for one window's figures."""
    out = {}
    for k in _FIGURE_SUMS:
        v = f.get(k)
        if k in ("orders", "bills", "void_count", "cashup_buckets"):
            out[k] = int(round(v or 0))
        else:
            out[k] = round(v or 0.0, 2)
    out["budget_pct"] = _pct(out["sales"], out["budget"])
    out["vs_budget"] = round(out["sales"] - out["budget"], 2) if out["budget"] else None
    out["average_order"] = (
        round(out["order_value"] / out["orders"], 2) if out["orders"] else None
    )
    items_total = out["food_sales"] + out["beverage_sales"] + out["other_sales"]
    out["food_share"] = _pct(out["food_sales"], items_total)
    out["beverage_share"] = _pct(out["beverage_sales"], items_total)
    out["split_mismatch"] = round(items_total - out["sales"], 2)
    # Wage % on sales ex GST; the food and beverage lines on their own
    # share of ex-GST sales (Loaded's wage-group rule divides beverage by
    # TOTAL sales — this says which it used).
    out["wage_pct"] = _pct(out["wages"], out["sales_ex_gst"])
    food_ex = out["sales_ex_gst"] * (out["food_share"] or 0)
    bev_ex = out["sales_ex_gst"] * (out["beverage_share"] or 0)
    out["food_wage_pct"] = _pct(out["food_wages"], food_ex)
    out["beverage_wage_pct"] = _pct(out["beverage_wages"], bev_ex)
    out["hours_variance"] = round(out["actual_hours"] - out["rostered_hours"], 2)
    out["cost_variance"] = round(out["actual_cost"] - out["rostered_cost"], 2)
    return out


def _venue_rows(call_api):
    listed = call_api("norm", "list_venues", {"connector": "loadedhub"})
    if _failed(listed):
        return None, str(listed["error"])
    data = (
        listed.get("data") if isinstance(listed, dict) and "data" in listed else listed
    )
    rows = data.get("venues") if isinstance(data, dict) else None
    return [r for r in rows or [] if isinstance(r, dict)], None


def _pick_venues(params, call_api):
    """[(name, id)] to report on, or an error."""
    rows, err = _venue_rows(call_api)
    if err:
        return None, "could not list this organisation's venues: " + err
    by_name = {str(r.get("name") or "").casefold(): r for r in rows}
    venues_param = params.get("venues")
    names = []
    if isinstance(venues_param, str) and venues_param.strip():
        v = venues_param.strip()
        if v.lower() in ("all", "all venues", "*", "group"):
            picked = [r for r in rows if r.get("connected") and r.get("name")]
            if not picked:
                return None, "no connected Loaded venues to report on"
            return [(str(r["name"]), str(r.get("id") or "")) for r in picked], None
        if v.startswith("["):
            try:
                parsed = json.loads(v)
            except ValueError:
                parsed = None
            names = [str(x).strip() for x in parsed or [] if str(x).strip()]
        else:
            names = [p.strip() for p in v.split(",") if p.strip()]
    elif isinstance(venues_param, list):
        names = [str(x).strip() for x in venues_param if str(x).strip()]
    if not names:
        name = str(params.get("venue") or params.get("venue_name") or "").strip()
        if not name and params.get("venue_id"):
            for r in rows:
                if str(r.get("id")) == str(params["venue_id"]):
                    name = str(r.get("name") or "")
        if not name:
            return None, "no venue: pass venues='all', a list of names, or one venue"
        names = [name]
    out = []
    for n in names:
        r = by_name.get(n.casefold())
        if r is None:
            return (
                None,
                "'" + n + "' isn't a venue in this organisation — use its full name",
            )
        out.append((str(r["name"]), str(r.get("id") or "")))
    return out, None


def _week_dates(week_ending_text, today_text):
    if week_ending_text:
        d = datetime.date.fromisoformat(str(week_ending_text)[:10])
    else:
        # The last completed Monday–Sunday week.
        today = datetime.date.fromisoformat(str(today_text)[:10])
        d = today - datetime.timedelta(days=today.weekday() + 1)
    monday = d - datetime.timedelta(days=d.weekday())
    return monday, monday + datetime.timedelta(days=6)


def _collect(
    venue, venue_id, monday, sunday, call_api, call_api_parallel, log, with_days
):
    """One venue's week and MTD figures (or {"error": …})."""
    month_first = monday.replace(day=1)
    resolve = {"venue_id": venue_id} if venue_id else {}
    week = _window_from(
        call_api(
            "norm",
            "resolve_dates",
            {**resolve, "query": "week beginning " + monday.isoformat()},
        )
    )
    if not week or not week.get("start") or not week.get("end"):
        return {"error": "could not resolve the week through Norm's calendar"}
    if month_first == monday:
        mtd = {**week}
    else:
        month = _window_from(
            call_api(
                "norm",
                "resolve_dates",
                {
                    **resolve,
                    "query": _MONTHS[month_first.month - 1]
                    + " "
                    + str(month_first.year),
                },
            )
        )
        if not month or not month.get("start"):
            return {"error": "could not resolve the month through Norm's calendar"}
        mtd = {**week, "start": month["start"]}
    day_start = _day_start_time(week)
    start, end = mtd["start"], week["end"]
    local = _localiser(start, end)
    daily = "1.00:00:00"
    v = {"venue": venue}
    calls = [
        (
            "loadedhub",
            "get_sales_data",
            {**v, "start_datetime": start, "end_datetime": end, "interval": daily},
        ),
        (
            "loadedhub",
            "get_pos_orders",
            {**v, "start": start, "end": end, "interval": daily},
        ),
        (
            "loadedhub",
            "get_pos_voids_raw",
            {**v, "start": start, "end": end, "interval": daily},
        ),
        (
            "loadedhub",
            "get_cashup_sets_summary_raw",
            {**v, "start": start, "end": end, "interval": daily},
        ),
        (
            "loadedhub",
            "get_roster_vs_actual",
            {**v, "start": start, "end": end, "interval": daily},
        ),
        (
            "loadedhub",
            "get_budgets",
            {
                **v,
                "from_date": month_first.isoformat(),
                "to_date": sunday.isoformat(),
                "tax": "include",
            },
        ),
        (
            "loadedhub",
            "get_pos_item_sales",
            {**v, "start_time": week["start"], "end_time": week["end"]},
        ),
        (
            "loadedhub",
            "get_pos_item_sales",
            {**v, "start_time": start, "end_time": end},
        ),
        (
            "loadedhub",
            "get_clockins_summary_by_role_raw",
            {**v, "start_time": week["start"], "end_time": week["end"]},
        ),
        (
            "loadedhub",
            "get_clockins_summary_by_role_raw",
            {**v, "start_time": start, "end_time": end},
        ),
    ]
    results = (
        call_api_parallel(calls)
        if call_api_parallel
        else [call_api(c, a, p) for (c, a, p) in calls]
    )
    sales, orders, voids, cashups, vs_actual, budgets = results[:6]
    items_week, items_mtd, roles_week, roles_mtd = results[6:10]
    if _failed(sales):
        return {"error": "sales unavailable: " + str(sales["error"])}
    warnings = []
    for payload, what in (
        (orders, "orders"),
        (voids, "voids"),
        (cashups, "cash-ups"),
        (vs_actual, "roster vs actual"),
        (budgets, "budget"),
        (items_week, "item sales (week)"),
        (items_mtd, "item sales (month)"),
        (roles_week, "wages by role (week)"),
        (roles_mtd, "wages by role (month)"),
    ):
        if _failed(payload):
            warnings.append(what + " unavailable: " + str(payload["error"]))

    in_week = lambda d: d is not None and monday <= d <= sunday  # noqa: E731
    in_mtd = lambda d: d is not None and month_first <= d <= sunday  # noqa: E731
    wk, md = _blank(), _blank()
    days = {}

    def day(d):
        return days.setdefault(
            d.isoformat(),
            {
                "date": d.isoformat(),
                "day": _DOW[d.weekday()],
                "sales": 0.0,
                "sales_ex_gst": 0.0,
                "budget": None,
                "orders": 0,
                "discounts": 0.0,
                "voids": 0.0,
                "rostered_cost": None,
                "wages": None,
            },
        )

    def add(d, key, value):
        if in_week(d):
            wk[key] += value
        if in_mtd(d):
            md[key] += value

    for r in _rows_of(sales):
        d = _trading_date(r.get("startTime"), day_start, local)
        if d is None:
            continue
        inv, tax = _num(r.get("invoices")), _num(r.get("invoicesTax"))
        add(d, "sales", inv)
        add(d, "gst", tax)
        add(d, "sales_ex_gst", inv - tax)
        add(d, "discounts", _num(r.get("discounts")))
        add(d, "bills", _num(r.get("count")))
        if in_week(d):
            row = day(d)
            row["sales"] = round(row["sales"] + inv, 2)
            row["sales_ex_gst"] = round(row["sales_ex_gst"] + inv - tax, 2)
            row["discounts"] = round(row["discounts"] + _num(r.get("discounts")), 2)
    if not _failed(orders):
        for r in _rows_of(orders):
            d = _trading_date(r.get("startTime"), day_start, local)
            if d is None:
                continue
            add(d, "orders", _num(r.get("count")))
            add(d, "order_value", _num(r.get("amount")))
            if in_week(d):
                day(d)["orders"] += int(round(_num(r.get("count"))))
    if not _failed(voids):
        for r in _rows_of(voids):
            d = _trading_date(r.get("startTime"), day_start, local)
            if d is None:
                continue
            add(d, "voids", _num(r.get("voidAmount")))
            add(d, "void_count", _num(r.get("voidQuantity")))
            add(d, "waste", _num(r.get("wasteAmount")))
            if in_week(d):
                row = day(d)
                row["voids"] = round(row["voids"] + _num(r.get("voidAmount")), 2)
    if not _failed(cashups):
        for r in _rows_of(cashups):
            d = _trading_date(r.get("startTime"), day_start, local)
            if d is None:
                continue
            add(d, "cash_variance", _num(r.get("cashVariance")))
            add(d, "total_variance", _num(r.get("totalVariance")))
            add(d, "cashup_buckets", 1.0)
    if not _failed(vs_actual):
        for r in _rows_of(vs_actual):
            d = _trading_date(r.get("startTime"), day_start, local)
            if d is None:
                continue
            add(d, "rostered_cost", _num(r.get("rosteredCost")))
            add(d, "rostered_hours", _num(r.get("rosteredHours")))
            add(d, "actual_cost", _num(r.get("actualCost")))
            add(d, "actual_hours", _num(r.get("actualHours")))
            if in_week(d):
                row = day(d)
                row["rostered_cost"] = round(_num(r.get("rosteredCost")), 2)
                row["wages"] = round(_num(r.get("actualCost")), 2)
    if not _failed(budgets):
        for r in (budgets.get("days") if isinstance(budgets, dict) else None) or []:
            if not isinstance(r, dict):
                continue
            try:
                d = datetime.date.fromisoformat(str(r.get("date") or "")[:10])
            except ValueError:
                continue
            add(d, "budget", _num(r.get("amount")))
            if in_week(d):
                day(d)["budget"] = round(_num(r.get("amount")), 2)
    for payload, acc in ((items_week, wk), (items_mtd, md)):
        if _failed(payload):
            continue
        for r in _rows_of(payload):
            acc[_group_of(r.get("itemGroupName")) + "_sales"] += _num(r.get("amount"))
    for payload, acc in ((roles_week, wk), (roles_mtd, md)):
        if _failed(payload):
            continue
        for r in _rows_of(payload):
            group = _cost_group(r.get("roleCostGroup"))
            cost, hours = _num(r.get("cost")), _num(r.get("hours"))
            acc["wages"] += cost
            acc["hours"] += hours
            acc[group + "_wages"] += cost
            if group == "food":
                acc["kitchen_hours"] += hours

    week_out = _finish(wk)
    mtd_out = _finish(md)
    if with_days:
        rows = [days[k] for k in sorted(days)]
        for row in rows:
            row["wage_pct"] = _pct(row["wages"], row["sales_ex_gst"])
            row["budget_pct"] = _pct(row["sales"], row["budget"])
        week_out["days"] = rows
    return {
        "week": week_out,
        "mtd": mtd_out,
        "windows": {
            "week": {
                "start": week["start"],
                "end": week["end"],
                "day_start": week.get("day_start"),
            },
            "mtd": {"start": start, "end": end},
        },
        "warnings": warnings,
    }


def _sum_group(blocks):
    acc = _blank()
    for b in blocks:
        for k in _FIGURE_SUMS:
            acc[k] += _num(b.get(k))
    return _finish(acc)


def run(params, call_api, log, call_api_parallel=None):
    try:
        monday, sunday = _week_dates(params.get("week_ending"), params.get("today"))
    except (TypeError, ValueError):
        return {"error": "week_ending must be YYYY-MM-DD (the week's Sunday)"}
    with_days = _flag(params.get("include_days"), True)
    targets, err = _pick_venues(params, call_api)
    if err:
        return {"error": err}
    month_first = monday.replace(day=1)

    venues = []
    warnings = []
    plan_available = None
    for name, vid in targets:
        got = _collect(
            name, vid, monday, sunday, call_api, call_api_parallel, log, with_days
        )
        if got.get("error"):
            venues.append(
                {"venue": name, "venue_id": vid or None, "error": got["error"]}
            )
            warnings.append(name + ": " + got["error"])
            continue
        # The Norm-side plan: locked budget, monthly lines, adjusted wages —
        # when the built-in exists. Until then the Loaded budget alone.
        plan = call_api(
            "norm_finance",
            "get_budget_plan",
            {"venue": name, "venue_id": vid, "week_ending": sunday.isoformat()},
        )
        if _failed(plan):
            text = str(plan["error"])
            if "not found" in text.lower():
                if plan_available is None:
                    plan_available = False
                budget_plan = None
            else:
                budget_plan = None
                warnings.append(name + ": budget plan unavailable: " + text)
        else:
            plan_available = True
            budget_plan = (
                plan.get("data") if isinstance(plan, dict) and "data" in plan else plan
            )
        got["week"]["budget_plan"] = budget_plan
        got["mtd"]["budget_plan"] = None
        for w in got["warnings"]:
            warnings.append(name + ": " + w)
        venues.append(
            {
                "venue": name,
                "venue_id": vid or None,
                "week": got["week"],
                "mtd": got["mtd"],
                "windows": got["windows"],
            }
        )

    good = [v for v in venues if not v.get("error")]
    group = {
        "week": _sum_group([v["week"] for v in good]),
        "mtd": _sum_group([v["mtd"] for v in good]),
        "venues": len(good),
    }
    result = {
        "week": {
            "week_ending": sunday.isoformat(),
            "week_start": monday.isoformat(),
            "label": "Week ending "
            + _DOW[sunday.weekday()]
            + " "
            + str(sunday.day)
            + " "
            + _MONTHS[sunday.month - 1]
            + " "
            + str(sunday.year),
        },
        "mtd": {
            "from": month_first.isoformat(),
            "to": sunday.isoformat(),
            "label": str(month_first.day)
            + " – "
            + str(sunday.day)
            + " "
            + _MONTHS[sunday.month - 1]
            + " "
            + str(sunday.year),
        },
        "venues": venues,
        "group": group,
        "warnings": warnings,
        "budget_plan_available": bool(plan_available),
        "notes": [
            "sales include GST; sales_ex_gst = sales − the GST Loaded reports inside them",
            "wages are Loaded's clock-in summary by role (actual); wage_pct = wages ÷ sales ex GST",
            "food / beverage sales are pre-discount POS item sales by revenue group; "
            "food_wage_pct and beverage_wage_pct divide by that group's share of ex-GST sales",
            "the month-to-date runs from the 1st of the month the week starts in to the week's end",
        ]
        + (
            []
            if plan_available
            else [
                "budget side is Loaded's daily budget only — Norm's budget plan is not available yet"
            ]
        ),
    }
    return result
