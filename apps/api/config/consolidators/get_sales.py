# ruff: noqa: F821 — `datetime` and `json` are injected into the sandbox
# namespace by app/connectors/function_executor.py; they are not imports.
#
# Canonical function_code for `loadedhub.get_sales` — THE sales domain tool
# (installed by scripts/sync_sales_config.py).
#
# One tool answers every read-side sales question. It absorbs what used to
# be eight separate tools (get_sales_for_period, get_pos_item_sales_for_period,
# get_staff_orders_for_period, get_staff_item_orders_for_period,
# get_pos_discounts_for_period, and norm_reports' get_periodic_sales /
# get_periodic_product_sales / get_periodic_staff_sales) plus the budget and
# last-year joins the model used to do by hand:
#
#   - period in plain English, resolved through Norm's venue-aware trading
#     calendar (the b9bda2c1 doctrine: a Saturday's 1am trade belongs to
#     Saturday, and midnight bucketing under-reported it by $4.5k),
#   - venues: one name (default: the thread's venue), a list, or "all"
#     (resolved via norm.list_venues; the engine resolves credentials one
#     venue per call, so multi-venue is a parallel fan-out here),
#   - breakdown: total | daily | items | staff | discounts,
#   - measure: sales (default) | orders — two different things, from two
#     feeds. Sales (/pos/sales): each bill when it was PAID, after discounts.
#     Orders (/pos/orders): each order when it was PLACED, at its value as
#     rung up (before discounts), paid or not — open tabs count. Absorbs
#     get_pos_orders_for_period (1 Oct 2026),
#   - tax: include (default) | exclude — Loaded's figures include tax and
#     carry the tax inside them (invoicesTax), so exclude subtracts it: the
#     figure Loaded's own reports call sales excluding tax. Budgets are
#     tax-inclusive too and are divided by 1 + their sales-tax rate, as
#     Loaded's own budget worksheet does. Amounts with no tax split (order
#     values, items, staff, discounts) are divided by 1 + the GST rate, as
#     Loaded's own reports do,
#   - interval: daily buckets by default; a sub-day interval (e.g.
#     '00:30:00') gives clock-time buckets attributed to their trading day,
#   - compare: "budget" and/or "last_year" (total and daily breakdowns) —
#     joins computed HERE, with last year defined as exactly 364 days back
#     (52 trading weeks, weekday aligned) so the baseline can never drift
#     between calls,
#   - time_windows: clock-time cuts (e.g. dinner 17:00–22:00) with
#     day-start-aware attribution, for total/daily and items breakdowns,
#   - token shaping: items and staff return the top rows by revenue with an
#     "(others)" rollup, so totals stay honest without flooding the model.
#
# A venue that errors or times out becomes a flagged row, not a stall — and
# never a silent gap in a group total.
#
# Requires consolidator_config: {"max_api_calls": 40}

_CONSUMED = (
    "period",
    "start",
    "end",
    "confirmed_by_user",
    "venue_id",
    "mode",
    "venues",
    "compare",
    "breakdown",
    "time_windows",
    "group_by",
    "day_of_week",
    "top",
    "category",
    "group",
    "sort_by",
    "staff_name",
    "interval",
    "measure",
    "tax",
)

_MEASURES = ("sales", "orders")
_TAX_LABEL = {"include": "included", "exclude": "excluded"}

_DOW_NAMES = {
    0: "Monday",
    1: "Tuesday",
    2: "Wednesday",
    3: "Thursday",
    4: "Friday",
    5: "Saturday",
    6: "Sunday",
}
_MONTH_NAMES = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}


def _window_from(resolved):
    if not isinstance(resolved, dict):
        return None
    data = resolved.get("data") if "data" in resolved else resolved
    if not isinstance(data, dict):
        return None
    window = data.get("window")
    return window if isinstance(window, dict) else None


def _rows_of(payload):
    if isinstance(payload, list):
        return payload if payload and isinstance(payload[0], dict) else None
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value
    return None


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def _amount_of(row, tax="include"):
    """One bucket's sales: money taken, after discounts. Loaded's `invoices`
    includes tax and `invoicesTax` is the tax inside it, so 'exclude'
    subtracts it — the figure Loaded's own reports call sales excluding tax."""
    v = row.get("invoices")
    if not isinstance(v, (int, float)):
        v = row.get("amount")
    if not isinstance(v, (int, float)):
        return None
    v = float(v)
    if tax == "exclude":
        v -= _num(row.get("invoicesTax"))
    return v


def _feed_call(measure, venue, start, end, interval):
    """One call to the feed a measure reads. Sales come from /pos/sales: each
    bill when it was PAID, after discounts. Orders come from /pos/orders:
    each order when it was PLACED, at its value as rung up (before
    discounts), paid or not. The two differ by design — hourly orders show
    when orders came in, hourly sales when money was taken."""
    if measure == "orders":
        return (
            "loadedhub",
            "get_pos_orders",
            {"venue": venue, "start": start, "end": end, "interval": interval},
        )
    return (
        "loadedhub",
        "get_sales_data",
        {
            "venue": venue,
            "start_datetime": start,
            "end_datetime": end,
            "interval": interval,
        },
    )


def _zero(measure):
    return {"orders": 0.0, "value": 0.0} if measure == "orders" else {"sales": 0.0}


def _add_figures(acc, row, tax, measure="sales", factor=1.0):
    """Add one bucket to running figures — sales: the money taken; orders:
    how many were placed and their value as rung up (`factor` takes tax off
    it, which the orders feed doesn't split out). Returns acc unchanged when
    the bucket carries no value."""
    if measure == "orders":
        amt = row.get("amount")
        if not isinstance(amt, (int, float)):
            return acc
        acc = dict(acc) if acc else _zero(measure)
        acc["orders"] += _num(row.get("count"))
        acc["value"] += float(amt) * factor
        return acc
    amt = _amount_of(row, tax)
    if amt is None:
        return acc
    acc = dict(acc) if acc else _zero(measure)
    acc["sales"] += amt
    return acc


def _figures(payload, tax, measure="sales", factor=1.0):
    """Summed figures of a feed payload, or None when it has none."""
    acc = None
    for row in _rows_of(payload) or []:
        acc = _add_figures(acc, row, tax, measure, factor)
    return acc


def _merge_figures(a, b):
    if not a:
        return b
    if not b:
        return a
    return {k: a[k] + b[k] for k in a}


def _primary(measure):
    """The field a comparison is made on."""
    return "orders" if measure == "orders" else "actual"


def _ly_key(measure):
    return "last_year_orders" if measure == "orders" else "last_year"


def _measure_fields(figs, measure):
    """Row fields for one measure. sales → `actual`, the money taken.
    orders → how many orders were placed, their value as rung up and the
    average order (order_value ÷ orders)."""
    if measure == "orders":
        if figs is None:
            return {"orders": None}
        orders = int(round(figs["orders"]))
        value = round(figs["value"], 2)
        return {
            "orders": orders,
            "order_value": value,
            "average_order": round(value / orders, 2) if orders else None,
        }
    return {"actual": None if figs is None else round(figs["sales"], 2)}


def _vs(row, measure, compare, pct):
    """vs_budget / vs_last_year from the row's own fields."""
    a = row.get(_primary(measure))
    if not isinstance(a, (int, float)):
        return
    if "budget" in compare:
        b = row.get("budget")
        if isinstance(b, (int, float)):
            row["vs_budget"] = round(a - b, 2)
            if pct:
                row["vs_budget_pct"] = round((a - b) / b * 100, 1) if b else None
    if "last_year" in compare:
        ly = row.get(_ly_key(measure))
        if isinstance(ly, (int, float)):
            row["vs_last_year"] = round(a - ly, 2)
            if pct:
                row["vs_last_year_pct"] = round((a - ly) / ly * 100, 1) if ly else None


def _budget_days(payload, tax):
    """{date: amount} from a get_budgets result; None when tax can't be
    removed. Loaded's budgets INCLUDE tax — its own budget worksheet divides
    each day by 1 + that day's sales-tax rate for the ex-tax figure, and
    'exclude' does the same."""
    out = {}
    days = payload.get("days") if isinstance(payload, dict) else None
    for d in days or []:
        amt = d.get("amount")
        if not isinstance(amt, (int, float)):
            continue
        if tax == "exclude":
            rate = d.get("sales_tax_rate")
            if not isinstance(rate, (int, float)):
                return None
            amt = amt / (1 + rate)
        out[str(d.get("date"))] = amt
    return out


def _budget_total(payload, tax):
    if tax == "include":
        total = payload.get("total") if isinstance(payload, dict) else None
        return round(float(total), 2) if isinstance(total, (int, float)) else None
    days = _budget_days(payload, tax)
    return None if days is None else round(sum(days.values()), 2)


def _interval_days(interval):
    """Whole days in a Loaded interval ('1.00:00:00' → 1, '7.00:00:00' → 7);
    0 for a sub-day one ('01:00:00', '00:30:00')."""
    head = str(interval or "1.00:00:00").strip().split(":")[0]
    try:
        if "." in head:
            return int(head.split(".")[0])
        hours = int(head)
    except ValueError:
        return 1
    return hours // 24


def _shift_iso(value, days):
    """An ISO datetime shifted by whole days, offset preserved."""
    s = str(value)
    d = datetime.date.fromisoformat(s[:10]) + datetime.timedelta(days=days)
    return d.isoformat() + s[10:]


def _budget_range(window):
    """Budgets are calendar-dated; a trading window's end lands in the small
    hours of the NEXT civil day, which is not one of the asked-for days."""
    b_from = str(window["start"])[:10]
    b_to = str(window["end"])[:10]
    if len(str(window["end"])) >= 13 and str(window["end"])[11:13] < "12":
        b_to = (
            datetime.date.fromisoformat(b_to) - datetime.timedelta(days=1)
        ).isoformat()
    return b_from, b_to


def _day_start_hour(window):
    ds = str(window.get("day_start") or "")
    try:
        return int(ds[:2]) if ds else 0
    except ValueError:
        return 0


def _fmt_date(d):
    return (
        _DOW_NAMES.get(d.weekday(), "")
        + " "
        + str(d.day).zfill(2)
        + " "
        + _MONTH_NAMES.get(d.month, "")[:3]
        + " "
        + str(d.year)
    )


def _month_chunks(start_date, end_date):
    months = []
    current = start_date.replace(day=1)
    while current <= end_date:
        m_start = current if current >= start_date else start_date
        next_month = (current.replace(day=28) + datetime.timedelta(days=4)).replace(
            day=1
        )
        m_end = next_month - datetime.timedelta(days=1)
        if m_end > end_date:
            m_end = end_date
        months.append((m_start, m_end))
        current = next_month
    return months


def _allowed_days(day_filter):
    if not day_filter:
        return None
    dow = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    df = str(day_filter).lower().strip()
    if df == "weekday":
        return {0, 1, 2, 3, 4}
    if df == "weekend":
        return {4, 5, 6}
    allowed = set()
    for d in df.split(","):
        d = d.strip()
        if d in dow:
            allowed.add(dow[d])
    return allowed


def _call_all(call_api, call_api_parallel, calls):
    if call_api_parallel and len(calls) > 1:
        return call_api_parallel(calls)
    return [call_api(c, a, p) for (c, a, p) in calls]


def _top_with_others(rows, amount_key, top, label_key, label="(others)"):
    """Top rows by amount + one rollup row, so totals stay honest without
    flooding the model (the received-items pattern)."""
    if not top or len(rows) <= top:
        return rows, None
    shown, rest = rows[:top], rows[top:]
    others = {label_key: label}
    for r in rest:
        for k, v in r.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                others[k] = round(others.get(k, 0) + v, 2)
    note = (
        "Showing the top "
        + str(top)
        + " of "
        + str(len(rows))
        + " rows by revenue; the rest are rolled into '(others)'. Pass a "
        "larger top to see more."
    )
    return shown + [others], note


# ── Window resolution (shared) ────────────────────────────────────────────


def _resolve_window(params, call_api, log):
    """Returns (window, auto_day_filter, error_result).

    A recurring phrase ("every Friday for the last 12 weeks") resolves to a
    list of matching days: the envelope (first..last) becomes the window
    and, when every resolved day lands on the same weekday, the day filter
    is filled in to match the phrase (ported from the periodic engines)."""
    period = (params.get("period") or "").strip()
    start = params.get("start")
    end = params.get("end")
    if not period and not (start and end):
        return (
            None,
            None,
            {
                "error": (
                    "Give a period in plain English (e.g. 'yesterday', 'last week'). "
                    "Only pass start and end if the user asked for specific clock times."
                )
            },
        )
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
        return (
            None,
            None,
            {"error": "Could not resolve the period: " + str(resolved["error"])},
        )
    window = _window_from(resolved)
    auto_dow = None
    if not window:
        data = (
            resolved.get("data")
            if isinstance(resolved, dict) and "data" in resolved
            else resolved
        )
        periods = data.get("periods") if isinstance(data, dict) else None
        if isinstance(periods, list) and periods:
            firsts = sorted(str(q.get("start")) for q in periods if q.get("start"))
            lasts = sorted(str(q.get("end")) for q in periods if q.get("end"))
            if firsts and lasts:
                window = {
                    "start": firsts[0],
                    "end": lasts[-1],
                    "day_start": (periods[0] or {}).get("day_start"),
                    "trading_aligned": True,
                    "recurring": True,
                    "description": period
                    + " ("
                    + str(len(periods))
                    + " matching days)",
                }
                dows = set()
                for d in firsts:
                    try:
                        dows.add(datetime.date.fromisoformat(d[:10]).weekday())
                    except ValueError:
                        pass
                if len(dows) == 1:
                    names = (
                        "monday",
                        "tuesday",
                        "wednesday",
                        "thursday",
                        "friday",
                        "saturday",
                        "sunday",
                    )
                    auto_dow = names[dows.pop()]
                    log("recurring period - filtering to " + auto_dow)
    if not window:
        return (
            None,
            None,
            {
                "error": (
                    "Could not resolve '" + (period or "that range") + "' to a date "
                    "range. Try a simpler period such as 'yesterday' or 'last week'."
                )
            },
        )
    if not window.get("trading_aligned") and not params.get("confirmed_by_user"):
        log("explicit window is not a trading day; asking before fetching")
        return (
            None,
            None,
            {
                "needs_confirmation": True,
                "window": window,
                "question": (
                    "These times are not this venue's trading day. "
                    + str(window.get("description", ""))
                    + " Did the user explicitly ask for these exact clock times? "
                    "If yes, call again with confirmed_by_user=true. If they asked "
                    "for a named period such as 'yesterday', call again with "
                    "period set instead and no start/end."
                ),
            },
        )
    return window, auto_dow, None


def _resolve_venues(params, call_api):
    """Returns (venue_names, error_result). Exactly one is None."""
    venues_param = params.get("venues")
    if isinstance(venues_param, str):
        v = venues_param.strip()
        if v.lower() in ("all", "all venues", "*", "group"):
            listed = call_api("norm", "list_venues", {"connector": "loadedhub"})
            # call_api hands internal norm.* results back UNWRAPPED
            # ({connector, venues}); tolerate an enveloped copy too.
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
                return None, {
                    "error": "could not list connected venues for the fan-out"
                }
            return names, None
        return [v], None
    if isinstance(venues_param, list):
        names = [str(v).strip() for v in venues_param if str(v).strip()]
        if names:
            return names, None
    if params.get("venue"):
        return [str(params["venue"]).strip()], None
    return None, {"error": "no venue: pass venues='all', a list of names, or one venue"}


# ── breakdown: total ──────────────────────────────────────────────────────


def _breakdown_total(
    window,
    venues,
    compare,
    measure,
    tax,
    call_api,
    call_api_parallel,
    log,
    factors=None,
):
    days = (
        datetime.date.fromisoformat(str(window["end"])[:10])
        - datetime.date.fromisoformat(str(window["start"])[:10])
    ).days + 1
    interval = str(days) + ".00:00:00"

    # Last year = exactly 364 days back: 52 weeks, so Monday stays Monday.
    # Deterministic here so every venue in one call — and every call in one
    # conversation — uses the SAME baseline.
    ly_start = _shift_iso(window["start"], -364)
    ly_end = _shift_iso(window["end"], -364)
    b_from, b_to = _budget_range(window)

    calls = []
    meta = []
    for v in venues:
        calls.append(_feed_call(measure, v, window["start"], window["end"], interval))
        meta.append((v, "actual"))
        if "budget" in compare:
            calls.append(
                (
                    "loadedhub",
                    "get_budgets",
                    {"venue": v, "from_date": b_from, "to_date": b_to},
                )
            )
            meta.append((v, "budget"))
        if "last_year" in compare:
            calls.append(_feed_call(measure, v, ly_start, ly_end, interval))
            meta.append((v, "last_year"))

    log(
        "Fanning out "
        + str(len(calls))
        + " calls over "
        + str(len(venues))
        + " venue(s)"
    )
    results = _call_all(call_api, call_api_parallel, calls)

    pk, lyk = _primary(measure), _ly_key(measure)
    per_venue = {v: {"venue": v} for v in venues}
    for (v, kind), payload in zip(meta, results):
        row = per_venue[v]
        if isinstance(payload, dict) and payload.get("error"):
            row.setdefault("errors", []).append(kind + ": " + str(payload["error"]))
            continue
        if kind == "budget":
            row["budget"] = _budget_total(payload, tax)
            if row["budget"] is None and tax == "exclude":
                row.setdefault("errors", []).append(
                    "budget: no sales-tax rate to remove tax with"
                )
        else:
            figs = _figures(payload, tax, measure, (factors or {}).get(v, 1.0))
            if kind == "actual":
                row.update(_measure_fields(figs, measure))
            else:
                row[lyk] = _measure_fields(figs, measure)[pk]

    rows = []
    for v in venues:
        row = per_venue[v]
        _vs(row, measure, compare, pct=True)
        rows.append(row)

    def _total(key):
        # A venue with ANY error is excluded from every total — mixing one
        # venue's budget into a group total whose actual it is missing from
        # would misstate the variance.
        vals = [
            r[key]
            for r in rows
            if not r.get("errors") and isinstance(r.get(key), (int, float))
        ]
        return round(sum(vals), 2) if vals else None

    if measure == "orders":
        totals = {k: _total(k) for k in ("orders", "order_value")}
        o, val = totals["orders"], totals["order_value"]
        totals["average_order"] = round(val / o, 2) if o and val is not None else None
    else:
        totals = {"actual": _total("actual")}
    if "budget" in compare:
        totals["budget"] = _total("budget")
        if totals[pk] is not None and totals["budget"]:
            totals["vs_budget"] = round(totals[pk] - totals["budget"], 2)
    if "last_year" in compare:
        totals[lyk] = _total(lyk)
        if totals[pk] is not None and totals[lyk]:
            totals["vs_last_year"] = round(totals[pk] - totals[lyk], 2)

    skipped = [r["venue"] for r in rows if r.get("errors")]
    result = {
        "window": window,
        "measure": measure,
        "tax": _TAX_LABEL[tax],
        "rows": rows,
        "totals": totals,
    }
    if "last_year" in compare:
        result["last_year_window"] = {
            "start": ly_start,
            "end": ly_end,
            "note": "exactly 364 days (52 weeks) back — weekday aligned",
        }
    if skipped:
        result["note"] = (
            "some venues had errors and are excluded from totals: " + ", ".join(skipped)
        )
    return result


# ── breakdown: daily ──────────────────────────────────────────────────────


def _breakdown_daily(
    window,
    venues,
    compare,
    interval,
    allowed,
    measure,
    tax,
    call_api,
    call_api_parallel,
    log,
    factors=None,
):
    """One row per bucket. Day-or-longer intervals key by date; a sub-day
    interval ('00:30:00', '01:00:00') keys by local clock time and gives each
    bucket its TRADING day — a 1am bucket belongs to the day before."""
    interval = interval or "1.00:00:00"
    span = _interval_days(interval)
    sub_day = span == 0
    day_start_hour = _day_start_hour(window)
    ly_start = _shift_iso(window["start"], -364)
    ly_end = _shift_iso(window["end"], -364)
    b_from, b_to = _budget_range(window)

    calls = []
    meta = []
    for v in venues:
        calls.append(_feed_call(measure, v, window["start"], window["end"], interval))
        meta.append((v, "actual"))
        if "budget" in compare:
            calls.append(
                (
                    "loadedhub",
                    "get_budgets",
                    {"venue": v, "from_date": b_from, "to_date": b_to},
                )
            )
            meta.append((v, "budget"))
        if "last_year" in compare:
            calls.append(_feed_call(measure, v, ly_start, ly_end, interval))
            meta.append((v, "last_year"))

    log("Daily fetch: " + str(len(calls)) + " calls")
    results = _call_all(call_api, call_api_parallel, calls)

    def _kept(date):
        if allowed is None:
            return True
        try:
            return datetime.date.fromisoformat(date).weekday() in allowed
        except ValueError:
            return False

    def _bucket(st):
        """(key, trading date, clock time) for a bucket's local start time."""
        date = st[:10]
        if not sub_day:
            return date, date, None
        try:
            d = datetime.date.fromisoformat(date)
            hour = int(st[11:13])
        except ValueError:
            return None, None, None
        if hour < day_start_hour:
            d = d - datetime.timedelta(days=1)
        return st[:16], d.isoformat(), st[11:16]

    actual = {}  # (venue, key) -> figures
    info = {}  # (venue, key) -> (trading date, clock time, startTime)
    ly = {}
    budget_days = {}
    errors = {}
    for (v, kind), payload in zip(meta, results):
        if isinstance(payload, dict) and payload.get("error"):
            errors.setdefault(v, []).append(kind + ": " + str(payload["error"]))
            continue
        if kind == "budget":
            days = _budget_days(payload, tax)
            if days is None:
                errors.setdefault(v, []).append(
                    "budget: no sales-tax rate to remove tax with"
                )
                continue
            for date, amt in days.items():
                if _kept(date):
                    budget_days[(v, date)] = amt
            continue
        for row in _rows_of(payload) or []:
            st = str(row.get("startTime", ""))
            if kind != "actual":
                # Key last year's bucket by the CURRENT-year time it aligns
                # to (+364 days), so the join is by position in the week.
                try:
                    st = _shift_iso(st, 364)
                except ValueError:
                    continue
            key, date, clock = _bucket(st)
            if key is None or not _kept(date):
                continue
            target = actual if kind == "actual" else ly
            figs = _add_figures(
                target.get((v, key)), row, tax, measure, (factors or {}).get(v, 1.0)
            )
            if figs is None:
                continue
            target[(v, key)] = figs
            if kind == "actual":
                info[(v, key)] = (date, clock, st)

    def _budget_for(v, date):
        # A bucket of several days (interval '7.00:00:00') carries the budget
        # of every day in it, not just its first.
        try:
            d0 = datetime.date.fromisoformat(date)
        except ValueError:
            return None
        vals = [
            budget_days.get((v, (d0 + datetime.timedelta(days=i)).isoformat()))
            for i in range(max(span, 1))
        ]
        vals = [x for x in vals if isinstance(x, (int, float))]
        return round(sum(vals), 2) if vals else None

    pk, lyk = _primary(measure), _ly_key(measure)
    rows = []
    for v in venues:
        venue_rows = []
        for vv, key in sorted(k for k in actual if k[0] == v):
            date, clock, st = info[(v, key)]
            try:
                dow = _DOW_NAMES.get(datetime.date.fromisoformat(date).weekday(), "")
            except ValueError:
                dow = ""
            row = {"venue": v, "date": date, "day": dow}
            if sub_day:
                row["time"] = clock
                row["startTime"] = st
            row.update(_measure_fields(actual[(v, key)], measure))
            if "budget" in compare:
                row["budget"] = _budget_for(v, date)
            if "last_year" in compare:
                figs = ly.get((v, key))
                row[lyk] = _measure_fields(figs, measure)[pk] if figs else None
            _vs(row, measure, compare, pct=False)
            venue_rows.append(row)
        if sub_day:
            # Clock-time buckets before opening and after close are empty;
            # trim them from each end, keep any gap in the middle.
            def _empty(r):
                return not r.get(pk) and not r.get(lyk)

            while venue_rows and _empty(venue_rows[0]):
                venue_rows.pop(0)
            while venue_rows and _empty(venue_rows[-1]):
                venue_rows.pop()
        rows.extend(venue_rows)

    zero = _zero(measure)
    totals = {}
    for v in venues:
        figs = None
        for (vv, _), f in actual.items():
            if vv == v:
                figs = _merge_figures(figs, f)
        t = _measure_fields(figs or zero, measure)
        if "budget" in compare:
            t["budget"] = round(
                sum(b for (vv, _), b in budget_days.items() if vv == v), 2
            )
        if "last_year" in compare:
            ly_figs = None
            for (vv, _), f in ly.items():
                if vv == v:
                    ly_figs = _merge_figures(ly_figs, f)
            t[lyk] = _measure_fields(ly_figs or zero, measure)[pk]
        totals[v] = t

    result = {
        "window": window,
        "measure": measure,
        "tax": _TAX_LABEL[tax],
        "interval": interval,
        "rows": rows,
        "totals": totals,
    }
    if "last_year" in compare:
        result["last_year_window"] = {
            "start": ly_start,
            "end": ly_end,
            "note": "exactly 364 days (52 weeks) back — weekday aligned",
        }
    if errors:
        result["note"] = "some venues had errors: " + json.dumps(errors)
    return result


# ── time-window engine (sales by clock-time cut) ──────────────────────────


def _time_window_sales(
    window,
    venues,
    time_windows,
    group_by,
    day_filter,
    measure,
    tax,
    call_api,
    call_api_parallel,
    log,
):
    """Hourly fetch + day-start-aware attribution, ported from the
    norm_reports periodic engine: every hour before the venue's day start
    belongs to the PREVIOUS trading day (Loaded's own daily figures
    attribute a Saturday's 1am trade to Saturday — civil-midnight bucketing
    under-reported that Saturday by $4.5k; prod thread b9bda2c1)."""
    if isinstance(time_windows, str):
        time_windows = json.loads(time_windows)
    day_start_hour = _day_start_hour(window)
    start_date = datetime.date.fromisoformat(str(window["start"])[:10])
    end_str_full = str(window["end"])
    end_date = datetime.date.fromisoformat(end_str_full[:10])
    # A trading window's end is the small hours of the NEXT civil day
    # (Mon 06:59) — not a day this report should include.
    if len(end_str_full) >= 13 and end_str_full[11:13] < "12":
        end_date = end_date - datetime.timedelta(days=1)
    tz_offset = str(window["start"])[19:] or "+12:00"
    allowed = _allowed_days(day_filter)

    boundary = "T" + str(day_start_hour).zfill(2) + ":00:00"
    calls = []
    call_venues = []
    for v in venues:
        for m_start, m_end in _month_chunks(start_date, end_date):
            calls.append(
                _feed_call(
                    measure,
                    v,
                    m_start.isoformat() + boundary + tz_offset,
                    (m_end + datetime.timedelta(days=1)).isoformat()
                    + boundary
                    + tz_offset,
                    "01:00:00",
                )
            )
            call_venues.append(v)
    log("Hourly fetch: " + str(len(calls)) + " calls")
    results = _call_all(call_api, call_api_parallel, calls)

    labels = [w.get("label", "Window " + str(i)) for i, w in enumerate(time_windows)]
    amounts = {}  # (venue, date, label) -> amount
    errors = {}
    for v, payload in zip(call_venues, results):
        if isinstance(payload, dict) and payload.get("error"):
            errors.setdefault(v, []).append(str(payload["error"]))
            continue
        for row in _rows_of(payload) or []:
            st = str(row.get("startTime", ""))
            if measure == "orders":
                amt = _num(row.get("count"))
            else:
                amt = _amount_of(row, tax)
            if not amt:
                continue
            try:
                hour = int(st[11:13])
                row_date = datetime.date.fromisoformat(st[:10])
            except (ValueError, IndexError):
                continue
            if hour < day_start_hour:
                row_date = row_date - datetime.timedelta(days=1)
            if allowed is not None and row_date.weekday() not in allowed:
                continue
            for w in time_windows:
                sh = int(w.get("start_hour", 0))
                eh = int(w.get("end_hour", 23))
                label = w.get("label", str(sh) + "-" + str(eh))
                in_window = (sh <= hour < eh) if eh > sh else (hour >= sh or hour < eh)
                if in_window:
                    key = (v, row_date, label)
                    amounts[key] = amounts.get(key, 0) + amt

    def _bucket(day):
        if group_by == "month":
            return _MONTH_NAMES.get(day.month, "") + " " + str(day.year), day.replace(
                day=1
            )
        if group_by == "week":
            iso = day.isocalendar()
            return str(iso[0]) + "-W" + str(iso[1]).zfill(2), day
        if group_by == "total":
            return "Total", start_date
        return _fmt_date(day), day

    agg = {}  # (venue, bucket_label) -> {label: amt}, with sort key
    order = {}
    for (v, day, label), amt in amounts.items():
        bucket_label, sort_key = _bucket(day)
        k = (v, bucket_label)
        agg.setdefault(k, {})
        agg[k][label] = agg[k].get(label, 0) + amt
        if k not in order or sort_key < order[k]:
            order[k] = sort_key

    rows = []
    for v, bucket_label in sorted(agg, key=lambda k: (k[0], order[k])):
        row = {"venue": v, "period": bucket_label}
        for lbl in labels:
            row[lbl] = round(agg[(v, bucket_label)].get(lbl, 0), 2)
            if measure == "orders":
                row[lbl] = int(row[lbl])
        rows.append(row)

    totals = {}
    for v in venues:
        totals[v] = {
            lbl: round(sum(r[lbl] for r in rows if r["venue"] == v and lbl in r), 2)
            for lbl in labels
        }

    result = {
        "window": window,
        "measure": measure,
        "tax": _TAX_LABEL[tax],
        "rows": rows,
        "totals": totals,
    }
    if measure == "orders":
        result["note_measure"] = (
            "each window's value is the number of orders placed in it"
        )
    if errors:
        result["note"] = "some venues had errors: " + json.dumps(errors)
    return result


# ── breakdown: items ──────────────────────────────────────────────────────


def _breakdown_items(
    window,
    venues,
    params,
    allowed,
    call_api,
    call_api_parallel,
    log,
    factors=None,
):
    time_windows = params.get("time_windows")
    if isinstance(time_windows, str) and time_windows:
        time_windows = json.loads(time_windows)
    category_filter = str(params.get("category") or "")
    group_filter = str(params.get("group") or "")
    sort_by = params.get("sort_by") or "sales"
    top = params.get("top")
    top = int(top) if top else 25
    # group_by='month' keeps a per-month row per item (trend view, ported
    # from the periodic product engine). Whole-window merge otherwise.
    by_month = str(params.get("group_by") or "").strip().lower() == "month"

    day_start_hour = _day_start_hour(window)
    tz_offset = str(window["start"])[19:] or "+12:00"
    start_date = datetime.date.fromisoformat(str(window["start"])[:10])
    end_str_full = str(window["end"])
    end_date = datetime.date.fromisoformat(end_str_full[:10])
    if len(end_str_full) >= 13 and end_str_full[11:13] < "12":
        end_date = end_date - datetime.timedelta(days=1)

    calls = []
    meta = []  # (venue, window_label or None)
    if time_windows:
        # The item-sales API cannot filter by hour, so clock windows become
        # one call per venue per window per chunk: per-day when that fits
        # the call budget, per-month otherwise (ported from the periodic
        # product engine).
        per_day = []
        for v in venues:
            day = start_date
            while day <= end_date:
                if allowed is None or day.weekday() in allowed:
                    for w in time_windows:
                        sh = int(w.get("start_hour", 0))
                        eh = int(w.get("end_hour", 23))
                        label = w.get("label", str(sh) + "-" + str(eh))
                        per_day.append((v, day, sh, eh, label))
                day = day + datetime.timedelta(days=1)
        if len(per_day) > 20:
            # Item sales cannot be sliced by clock time over a long range: the
            # feed has no hour-of-day filter, so each day × window is its own
            # call. The old monthly fallback issued ONE call per month per
            # window with clock-hour endpoints (e.g. 1 Jun 12:00 → 30 Jun
            # 15:00) — a span of the WHOLE month, not 12–3pm daily — so every
            # window silently returned ~the full-period total. Refuse with
            # guidance instead of lying.
            return {
                "window": window,
                "error": (
                    "item sales can't be sliced by clock time over this range: "
                    "the feed has no hour-of-day filter, so each day × window is "
                    "a separate call and this needs "
                    + str(len(per_day))
                    + " (max 20 per call). Narrow the period (e.g. a week at a "
                    "time) or use fewer windows, then combine the results."
                ),
            }
        for v, day, sh, eh, label in per_day:
            # A window whose end hour is not past its start hour crosses
            # midnight (e.g. 23:00–03:00). Its end lands on the NEXT day —
            # keeping it on the same day gives start > end, an empty range that
            # returned $0.
            end_day = day if eh > sh else day + datetime.timedelta(days=1)
            calls.append(
                (
                    "loadedhub",
                    "get_pos_item_sales",
                    {
                        "venue": v,
                        "start_time": day.isoformat()
                        + "T"
                        + str(sh).zfill(2)
                        + ":00:00"
                        + tz_offset,
                        "end_time": end_day.isoformat()
                        + "T"
                        + str(eh).zfill(2)
                        + ":00:00"
                        + tz_offset,
                    },
                )
            )
            meta.append((v, label, None))
    else:
        # Whole-window fetch, day-start aligned via monthly chunks.
        boundary = "T" + str(day_start_hour).zfill(2) + ":00:00"
        for v in venues:
            for m_start, m_end in _month_chunks(start_date, end_date):
                calls.append(
                    (
                        "loadedhub",
                        "get_pos_item_sales",
                        {
                            "venue": v,
                            "start_time": m_start.isoformat() + boundary + tz_offset,
                            "end_time": (m_end + datetime.timedelta(days=1)).isoformat()
                            + boundary
                            + tz_offset,
                        },
                    )
                )
                month = (
                    _MONTH_NAMES.get(m_start.month, "") + " " + str(m_start.year)
                    if by_month
                    else None
                )
                meta.append((v, None, month))

    results = _call_all(call_api, call_api_parallel, calls)
    labels = []
    if time_windows:
        labels = [
            w.get("label", "Window " + str(i)) for i, w in enumerate(time_windows)
        ]

    merged = {}
    errors = {}
    for (v, wlabel, month), payload in zip(meta, results):
        if isinstance(payload, dict) and payload.get("error"):
            errors.setdefault(v, []).append(str(payload["error"]))
            continue
        for item in _rows_of(payload) or []:
            name = item.get("itemName", "Unknown")
            cat = item.get("itemCategoryName", "") or ""
            grp = item.get("itemGroupName", "") or ""
            if category_filter and category_filter.lower() not in cat.lower():
                continue
            if group_filter and group_filter.lower() not in grp.lower():
                continue
            amt = item.get("amount")
            qty = item.get("quantity")
            amt = float(amt) if isinstance(amt, (int, float)) else 0.0
            qty = float(qty) if isinstance(qty, (int, float)) else 0.0
            if factors:
                amt = amt * factors.get(v, 1.0)
            # Key by name AND group AND category: distinct items can share a
            # name (e.g. "Misc" under Beverage and under Food) and merging
            # by name alone collapses them. group_by='month' adds the month
            # so each item keeps a per-month trend row.
            key = (name, grp, cat, month)
            if key not in merged:
                merged[key] = {"item": name, "group": grp, "category": cat}
                if month:
                    merged[key]["period"] = month
                if labels:
                    for lbl in labels:
                        merged[key][lbl + " sales"] = 0
                        merged[key][lbl + " qty"] = 0
                else:
                    merged[key]["sales"] = 0
                    merged[key]["quantity"] = 0
            row = merged[key]
            # Accumulate raw and round once at the end — rounding every
            # addition drifts by pennies over hundreds of rows.
            if wlabel:
                row[wlabel + " sales"] = row.get(wlabel + " sales", 0) + amt
                row[wlabel + " qty"] = row.get(wlabel + " qty", 0) + qty
            else:
                row["sales"] = row["sales"] + amt
                row["quantity"] = row["quantity"] + qty

    rows = list(merged.values())
    num_keys = (
        [lbl + " sales" for lbl in labels] + [lbl + " qty" for lbl in labels]
        if labels
        else ["sales", "quantity"]
    )
    # Totals from the RAW accumulations, then round the rows — summing
    # already-rounded rows drifts by pennies against the feed's own total.
    raw_totals = {k: round(sum(r.get(k, 0) or 0 for r in rows), 2) for k in num_keys}
    for r in rows:
        for k in num_keys:
            if k in r:
                r[k] = round(r[k], 2)
    if labels:
        sort_field = labels[0] + (" qty" if sort_by == "quantity" else " sales")
    else:
        sort_field = "quantity" if sort_by == "quantity" else "sales"
    rows.sort(key=lambda r: r.get(sort_field, 0) or 0, reverse=True)

    totals = {"row_count": len(rows), **raw_totals}

    rows, note = _top_with_others(rows, sort_field, top, "item")
    result = {
        "window": window,
        "rows": rows,
        "totals": totals,
        # Loaded prices items before discounts: these add up to MORE than
        # the total breakdown's sales, by the discounts given.
        "note_amounts": "item sales are before discounts",
    }
    if len(venues) > 1:
        result["venues"] = venues
        result["note_venues"] = "rows are merged across the listed venues"
    if note:
        result["note"] = note
    if errors:
        result["errors"] = errors
    return result


# ── breakdown: staff ──────────────────────────────────────────────────────


def _breakdown_staff(
    window, venues, params, call_api, call_api_parallel, log, factors=None
):
    staff_name = str(params.get("staff_name") or "").strip()
    top = params.get("top")
    top = int(top) if top else 0
    interval = str(params.get("interval") or "").strip()

    calls = [
        (
            "loadedhub",
            "get_staff_orders",
            {"venue": v, "start": window["start"], "end": window["end"]},
        )
        for v in venues
    ]
    results = _call_all(call_api, call_api_parallel, calls)

    merged = {}
    ids = {}  # (venue, name) -> staff id, for the drill-down paths
    errors = {}
    for v, payload in zip(venues, results):
        if isinstance(payload, dict) and payload.get("error"):
            errors.setdefault(v, []).append(str(payload["error"]))
            continue
        for s in _rows_of(payload) or []:
            name = str(s.get("label", "Unknown")).strip()
            amt = s.get("amount")
            qty = s.get("quantity")
            amt = float(amt) if isinstance(amt, (int, float)) else 0.0
            qty = float(qty) if isinstance(qty, (int, float)) else 0.0
            if factors:
                amt = amt * factors.get(v, 1.0)
            if amt <= 0:
                continue
            if name not in merged:
                merged[name] = {"staff": name, "orders": 0, "sales": 0}
            merged[name]["sales"] = merged[name]["sales"] + amt
            merged[name]["orders"] = int(merged[name]["orders"] + qty)
            if s.get("id"):
                ids[(v, name)] = str(s["id"])

    raw_sales = round(sum(r["sales"] for r in merged.values()), 2)
    for r in merged.values():
        r["sales"] = round(r["sales"], 2)
    rows = sorted(merged.values(), key=lambda r: -r["sales"])

    # Drill-down: one staff member's product mix (absorbs
    # get_staff_item_orders_for_period). Name match is case-insensitive
    # substring, resolved against the staff list just fetched.
    if staff_name:
        matches = [(v, name) for (v, name) in ids if staff_name.lower() in name.lower()]
        names = sorted({name for _, name in matches})
        if not names:
            return {
                "window": window,
                "error": (
                    "no staff member matching '"
                    + staff_name
                    + "' had sales in this window"
                ),
                "staff_with_sales": [r["staff"] for r in rows],
            }
        if len(names) > 1:
            return {
                "window": window,
                "error": "staff_name is ambiguous: " + ", ".join(names),
            }
        item_calls = [
            (
                "loadedhub",
                "get_staff_item_orders",
                {
                    "venue": v,
                    "start": window["start"],
                    "end": window["end"],
                    "staff_id": ids[(v, name)],
                },
            )
            for (v, name) in matches
        ]
        item_results = _call_all(call_api, call_api_parallel, item_calls)
        items = {}
        for (v, _), payload in zip(matches, item_results):
            if isinstance(payload, dict) and payload.get("error"):
                return {"window": window, "error": str(payload["error"])}
            for item in _rows_of(payload) or []:
                name2 = item.get("itemName") or item.get("label") or "Unknown"
                amt = item.get("amount")
                qty = item.get("quantity")
                amt = float(amt) if isinstance(amt, (int, float)) else 0.0
                qty = float(qty) if isinstance(qty, (int, float)) else 0.0
                if factors:
                    amt = amt * factors.get(v, 1.0)
                if name2 not in items:
                    items[name2] = {"item": name2, "quantity": 0, "sales": 0}
                items[name2]["sales"] = items[name2]["sales"] + amt
                items[name2]["quantity"] = items[name2]["quantity"] + qty
        for it in items.values():
            it["sales"] = round(it["sales"], 2)
            it["quantity"] = round(it["quantity"], 2)
        item_rows = sorted(items.values(), key=lambda r: -r["sales"])
        item_rows, note = _top_with_others(item_rows, "sales", top or 25, "item")
        result = {
            "window": window,
            "staff": names[0],
            "rows": item_rows,
            "totals": {
                "sales": round(sum(i["sales"] for i in items.values()), 2),
                "quantity": round(sum(i["quantity"] for i in items.values()), 2),
            },
        }
        if note:
            result["note"] = note
        return result

    # Interval winners (ported from the periodic staff engine): who led
    # each bucket. Single venue only — cross-venue winners are not a thing.
    winners = None
    win_counts = None
    if interval:
        if len(venues) > 1:
            return {
                "window": window,
                "error": (
                    "interval winners read one venue — call once per venue "
                    "or drop interval for the group ranking"
                ),
            }
        v = venues[0]
        per_staff_calls = [
            (
                "loadedhub",
                "get_staff_orders",
                {
                    "venue": v,
                    "start": window["start"],
                    "end": window["end"],
                    "staff_id": ids[(v, r["staff"])],
                    "interval": interval,
                },
            )
            for r in rows
            if (v, r["staff"]) in ids
        ]
        per_staff = _call_all(call_api, call_api_parallel, per_staff_calls)
        slot_best = {}
        named = [r["staff"] for r in rows if (v, r["staff"]) in ids]
        for name, payload in zip(named, per_staff):
            for row in _rows_of(payload) or []:
                st = str(row.get("startTime", ""))
                amt = _amount_of(row)
                if not amt:
                    continue
                if factors:
                    amt = amt * factors.get(v, 1.0)
                cur = slot_best.get(st)
                if cur is None or amt > cur[1]:
                    slot_best[st] = (name, amt)
        winners = [
            {"slot": st, "winner": w[0], "sales": round(w[1], 2)}
            for st, w in sorted(slot_best.items())
        ]
        win_counts = {}
        for w in winners:
            win_counts[w["winner"]] = win_counts.get(w["winner"], 0) + 1

    shown, note = _top_with_others(rows, "sales", top, "staff")
    result = {
        "window": window,
        "rows": shown,
        "totals": {
            "sales": raw_sales,
            "orders": int(sum(r["orders"] for r in rows)),
            "staff_count": len(rows),
        },
    }
    if len(venues) > 1:
        result["venues"] = venues
        result["note_venues"] = "rows are merged across the listed venues"
    if winners is not None:
        result["interval_winners"] = winners
        result["win_counts"] = win_counts
    if note:
        result["note"] = note
    if errors:
        result["errors"] = errors
    return result


# ── breakdown: discounts ──────────────────────────────────────────────────


def _breakdown_discounts(
    window, venues, call_api, call_api_parallel, log, factors=None
):
    calls = [
        (
            "loadedhub",
            "get_pos_discounts",
            {"venue": v, "start": window["start"], "end": window["end"]},
        )
        for v in venues
    ]
    results = _call_all(call_api, call_api_parallel, calls)
    merged = {}
    errors = {}
    for v, payload in zip(venues, results):
        if isinstance(payload, dict) and payload.get("error"):
            errors.setdefault(v, []).append(str(payload["error"]))
            continue
        for row in _rows_of(payload) or []:
            # One row per discount TYPE ("20% Member Deal"). This read `label`
            # until 1 Oct 2026 — a field the feed doesn't have — so every type
            # merged into one row named "Unknown".
            name = str(row.get("discountType") or row.get("label") or "Unknown").strip()
            if name not in merged:
                merged[name] = {
                    "discount": name,
                    "discounts_amount": 0,
                    "discounts_count": 0,
                    "discounted_invoices": 0,
                }
            m = merged[name]
            for src, dst in (
                ("discountsAmount", "discounts_amount"),
                ("discountsCount", "discounts_count"),
                ("discountInvoices", "discounted_invoices"),
            ):
                val = row.get(src)
                if isinstance(val, (int, float)):
                    # both are money (discountInvoices: the value of the
                    # bills the discounts were on); the count is a count
                    if factors and dst != "discounts_count":
                        val = val * factors.get(v, 1.0)
                    m[dst] = m[dst] + val
    # Totals from the raw sums, then round the rows (the items pattern).
    totals = {
        k: round(sum(m[k] for m in merged.values()), 2)
        for k in ("discounts_amount", "discounts_count")
    }
    for m in merged.values():
        for k in ("discounts_amount", "discounts_count", "discounted_invoices"):
            m[k] = round(m[k], 2)
    rows = sorted(merged.values(), key=lambda r: -r["discounts_amount"])
    result = {"window": window, "rows": rows, "totals": totals}
    if len(venues) > 1:
        result["venues"] = venues
        result["note_venues"] = "rows are merged across the listed venues"
    if errors:
        result["errors"] = errors
    return result


# ── tax off item / staff / discount amounts ───────────────────────────────


def _tax_factors(venues, call_api, call_api_parallel, log):
    """{venue: 1 / (1 + its GST rate)}, the rates as percentages, and any
    per-venue errors.

    Item, staff and discount amounts come from Loaded with tax included and
    no tax split. Loaded's own reports take tax off those at the company's
    sales-tax rate (SalesTaxHelper.SubtractSalesTax), so this does the same.
    The company's rates are e.g. [Exempt 0%, GST 15%]; the standard rate is
    the highest. (Not the effective rate inside the takings: exempt sales
    pull that down — 12.8% over one La Zeppa week — and it would misstate
    every taxed item.)"""
    calls = [("loadedhub", "get_sales_tax_rates", {"venue": v}) for v in venues]
    log("Sales-tax rate fetch: " + str(len(calls)) + " calls")
    results = _call_all(call_api, call_api_parallel, calls)
    factors, rates, errors = {}, {}, {}
    for v, payload in zip(venues, results):
        if isinstance(payload, dict) and payload.get("error"):
            errors[v] = str(payload["error"])
            continue
        found = [
            r.get("rate")
            for r in (payload if isinstance(payload, list) else _rows_of(payload) or [])
            if isinstance(r, dict) and isinstance(r.get("rate"), (int, float))
        ]
        if found and max(found) > 0:
            factors[v] = 1 / (1 + max(found))
            rates[v] = round(max(found) * 100, 2)
    return factors, rates, errors


# ── entry point ───────────────────────────────────────────────────────────


def _tax_mode(params):
    """(mode, error). Figures include tax unless asked otherwise."""
    t = str(params.get("tax") or "include").strip().lower()
    if t in ("include", "included", "inclusive", "incl", "with"):
        return "include", None
    if t in ("exclude", "excluded", "exclusive", "excl", "ex", "without"):
        return "exclude", None
    return None, "tax must be 'include' (the default) or 'exclude'"


def run(params, call_api, log, call_api_parallel=None):
    window, auto_dow, err = _resolve_window(params, call_api, log)
    if err:
        return err

    venues, err = _resolve_venues(params, call_api)
    if err:
        err["window"] = window
        return err

    breakdown = str(params.get("breakdown") or "total").strip().lower()
    measure = str(params.get("measure") or "sales").strip().lower()
    if measure not in _MEASURES:
        return {
            "window": window,
            "error": "measure must be 'sales' (money taken) or 'orders' (order count)",
        }
    tax, err = _tax_mode(params)
    if err:
        return {"window": window, "error": err}
    compare = params.get("compare") or []
    if isinstance(compare, str):
        compare = [c.strip().lower() for c in compare.split(",") if c.strip()]
    compare = [c for c in compare if c in ("budget", "last_year")]
    time_windows = params.get("time_windows")
    day_of_week = params.get("day_of_week") or auto_dow
    allowed = _allowed_days(day_of_week)
    interval = params.get("interval")

    if "budget" in compare and measure == "orders":
        return {
            "window": window,
            "error": (
                "budgets are money, not orders — compare='budget' works with "
                "measure 'sales'. For orders, compare='last_year' works."
            ),
        }
    if (
        "budget" in compare
        and breakdown == "daily"
        and interval
        and _interval_days(interval) == 0
    ):
        return {
            "window": window,
            "error": (
                "budgets are set per day — compare='budget' needs a daily (or "
                "longer) interval. Drop the budget, or the sub-day interval."
            ),
        }

    if breakdown not in ("total", "daily", "items", "staff", "discounts"):
        return {
            "error": (
                "unknown breakdown '"
                + breakdown
                + "' — use total, daily, items, staff, or discounts"
            )
        }
    if time_windows and compare and breakdown in ("total", "daily"):
        return {
            "window": window,
            "error": (
                "compare does not combine with time_windows — run them as two calls"
            ),
        }
    if compare and breakdown not in ("total", "daily"):
        return {
            "window": window,
            "error": "compare works with breakdown 'total' or 'daily' only",
        }
    if breakdown == "items" and allowed is not None and not time_windows:
        return {
            "window": window,
            "error": (
                "the item-sales feed cannot filter by day of week over a "
                "whole window — add time_windows (clock cuts) to slice "
                "matching days, or drop day_of_week"
            ),
        }

    # Tax off amounts the feed doesn't split: order values, items, staff,
    # discounts. (Sales carry their own recorded tax; counts carry none.)
    factors = None
    tax_note = None
    counts_only = measure == "orders" and time_windows
    if (
        tax == "exclude"
        and not counts_only
        and (measure == "orders" or breakdown in ("items", "staff", "discounts"))
    ):
        factors, rates, tax_errors = _tax_factors(
            venues, call_api, call_api_parallel, log
        )
        missing = [v for v in venues if v not in factors]
        tax_note = (
            "Loaded gives these amounts with tax in and no tax split, so tax "
            "was taken off at the sales-tax rate, as Loaded's own reports do: "
            + ", ".join(v + " " + str(rates[v]) + "%" for v in venues if v in rates)
        )
        if missing:
            tax_note += (
                ". NOT removed for "
                + ", ".join(missing)
                + " (no sales-tax rate"
                + (": " + json.dumps(tax_errors) if tax_errors else "")
                + ") — their amounts still include tax"
            )

    result = _dispatch(
        params,
        window,
        venues,
        breakdown,
        measure,
        tax,
        compare,
        time_windows,
        day_of_week,
        allowed,
        interval,
        factors,
        call_api,
        call_api_parallel,
        log,
    )
    if isinstance(result, dict) and not result.get("error"):
        result.setdefault("tax", _TAX_LABEL[tax])
        if tax_note:
            result["note_tax"] = tax_note
    return result


def _dispatch(
    params,
    window,
    venues,
    breakdown,
    measure,
    tax,
    compare,
    time_windows,
    day_of_week,
    allowed,
    interval,
    factors,
    call_api,
    call_api_parallel,
    log,
):
    if breakdown in ("total", "daily") and time_windows:
        return _time_window_sales(
            window,
            venues,
            time_windows,
            str(
                params.get("group_by") or ("each" if breakdown == "daily" else "total")
            ),
            day_of_week,
            measure,
            tax,
            call_api,
            call_api_parallel,
            log,
        )

    if breakdown == "total":
        if allowed is not None:
            # A day filter needs daily buckets: run the daily path filtered
            # and collapse to one totals row per venue.
            daily = _breakdown_daily(
                window,
                venues,
                compare,
                None,
                allowed,
                measure,
                tax,
                call_api,
                call_api_parallel,
                log,
                factors=factors,
            )
            rows = []
            for v in venues:
                t = (daily.get("totals") or {}).get(v) or {}
                row = {"venue": v, **t}
                _vs(row, measure, compare, pct=False)
                rows.append(row)
            if measure == "orders":
                totals = {
                    k: round(
                        sum(r[k] for r in rows if isinstance(r.get(k), (int, float))),
                        2,
                    )
                    for k in ("orders", "order_value")
                }
                totals["average_order"] = (
                    round(totals["order_value"] / totals["orders"], 2)
                    if totals["orders"]
                    else None
                )
            else:
                totals = {
                    "actual": round(
                        sum(
                            r["actual"]
                            for r in rows
                            if isinstance(r.get("actual"), (int, float))
                        ),
                        2,
                    )
                }
            out = {
                "window": window,
                "measure": measure,
                "tax": _TAX_LABEL[tax],
                "day_of_week": day_of_week,
                "rows": rows,
                "totals": totals,
            }
            if daily.get("note"):
                out["note"] = daily["note"]
            if daily.get("last_year_window"):
                out["last_year_window"] = daily["last_year_window"]
            return out
        return _breakdown_total(
            window,
            venues,
            compare,
            measure,
            tax,
            call_api,
            call_api_parallel,
            log,
            factors=factors,
        )
    if breakdown == "daily":
        result = _breakdown_daily(
            window,
            venues,
            compare,
            interval,
            allowed,
            measure,
            tax,
            call_api,
            call_api_parallel,
            log,
            factors=factors,
        )
        if day_of_week:
            result["day_of_week"] = day_of_week
        return result
    if breakdown == "items":
        return _breakdown_items(
            window,
            venues,
            params,
            allowed,
            call_api,
            call_api_parallel,
            log,
            factors=factors,
        )
    if breakdown == "staff":
        return _breakdown_staff(
            window, venues, params, call_api, call_api_parallel, log, factors=factors
        )
    return _breakdown_discounts(
        window, venues, call_api, call_api_parallel, log, factors=factors
    )
