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
)

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


def _amount_of(row):
    v = row.get("invoices")
    if not isinstance(v, (int, float)):
        v = row.get("amount")
    return float(v) if isinstance(v, (int, float)) else None


def _sales_total(payload):
    """Net sales from a get_sales_data payload (sums invoices/amount rows)."""
    rows = _rows_of(payload) or []
    total = 0.0
    seen = False
    for row in rows:
        v = _amount_of(row)
        if v is not None:
            total += v
            seen = True
    return round(total, 2) if seen else None


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
            data = listed.get("data") if isinstance(listed, dict) else None
            rows = (data or {}).get("venues") if isinstance(data, dict) else None
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


def _breakdown_total(window, venues, compare, call_api, call_api_parallel, log):
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
        calls.append(
            (
                "loadedhub",
                "get_sales_data",
                {
                    "venue": v,
                    "start_datetime": window["start"],
                    "end_datetime": window["end"],
                    "interval": interval,
                },
            )
        )
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
            calls.append(
                (
                    "loadedhub",
                    "get_sales_data",
                    {
                        "venue": v,
                        "start_datetime": ly_start,
                        "end_datetime": ly_end,
                        "interval": interval,
                    },
                )
            )
            meta.append((v, "last_year"))

    log(
        "Fanning out "
        + str(len(calls))
        + " calls over "
        + str(len(venues))
        + " venue(s)"
    )
    results = _call_all(call_api, call_api_parallel, calls)

    per_venue = {v: {"venue": v} for v in venues}
    for (v, kind), payload in zip(meta, results):
        row = per_venue[v]
        if isinstance(payload, dict) and payload.get("error"):
            row.setdefault("errors", []).append(kind + ": " + str(payload["error"]))
            continue
        if kind == "budget":
            total = payload.get("total") if isinstance(payload, dict) else None
            row["budget"] = (
                round(float(total), 2) if isinstance(total, (int, float)) else None
            )
        else:
            key = "actual" if kind == "actual" else "last_year"
            row[key] = _sales_total(payload)

    rows = []
    for v in venues:
        row = per_venue[v]
        a, b, ly = row.get("actual"), row.get("budget"), row.get("last_year")
        if a is not None and b is not None:
            row["vs_budget"] = round(a - b, 2)
            row["vs_budget_pct"] = round((a - b) / b * 100, 1) if b else None
        if a is not None and ly is not None:
            row["vs_last_year"] = round(a - ly, 2)
            row["vs_last_year_pct"] = round((a - ly) / ly * 100, 1) if ly else None
        rows.append(row)

    def _total(key):
        vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        return round(sum(vals), 2) if vals else None

    totals = {"actual": _total("actual")}
    if "budget" in compare:
        totals["budget"] = _total("budget")
        if totals["actual"] is not None and totals["budget"]:
            totals["vs_budget"] = round(totals["actual"] - totals["budget"], 2)
    if "last_year" in compare:
        totals["last_year"] = _total("last_year")
        if totals["actual"] is not None and totals["last_year"]:
            totals["vs_last_year"] = round(totals["actual"] - totals["last_year"], 2)

    skipped = [r["venue"] for r in rows if r.get("errors")]
    result = {"window": window, "rows": rows, "totals": totals}
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
    window, venues, compare, interval, allowed, call_api, call_api_parallel, log
):
    interval = interval or "1.00:00:00"
    ly_start = _shift_iso(window["start"], -364)
    ly_end = _shift_iso(window["end"], -364)
    b_from, b_to = _budget_range(window)

    calls = []
    meta = []
    for v in venues:
        calls.append(
            (
                "loadedhub",
                "get_sales_data",
                {
                    "venue": v,
                    "start_datetime": window["start"],
                    "end_datetime": window["end"],
                    "interval": interval,
                },
            )
        )
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
            calls.append(
                (
                    "loadedhub",
                    "get_sales_data",
                    {
                        "venue": v,
                        "start_datetime": ly_start,
                        "end_datetime": ly_end,
                        "interval": interval,
                    },
                )
            )
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

    actual = {}
    budget_days = {}
    ly_daily = {}
    errors = {}
    for (v, kind), payload in zip(meta, results):
        if isinstance(payload, dict) and payload.get("error"):
            errors.setdefault(v, []).append(kind + ": " + str(payload["error"]))
            continue
        if kind == "budget":
            for d in (payload or {}).get("days") or []:
                if _kept(str(d.get("date"))):
                    budget_days[(v, d.get("date"))] = d.get("amount")
            continue
        for row in _rows_of(payload) or []:
            amt = _amount_of(row)
            if amt is None:
                continue
            date = str(row.get("startTime", ""))[:10]
            if kind == "actual":
                if not _kept(date):
                    continue
                actual[(v, date)] = round(actual.get((v, date), 0.0) + amt, 2)
            else:
                # Key last year's bucket by the CURRENT-year date it aligns
                # to (+364 days), so the join is by position in the week.
                shifted = (
                    datetime.date.fromisoformat(date) + datetime.timedelta(days=364)
                ).isoformat()
                if not _kept(shifted):
                    continue
                ly_daily[(v, shifted)] = round(ly_daily.get((v, shifted), 0.0) + amt, 2)

    rows = []
    for v in venues:
        for (vv, date), amt in sorted(actual.items()):
            if vv != v:
                continue
            try:
                dow = _DOW_NAMES.get(datetime.date.fromisoformat(date).weekday(), "")
            except ValueError:
                dow = ""
            row = {"venue": v, "date": date, "day": dow, "actual": amt}
            if "budget" in compare:
                b = budget_days.get((v, date))
                row["budget"] = b
                if isinstance(b, (int, float)):
                    row["vs_budget"] = round(amt - b, 2)
            if "last_year" in compare:
                ly = ly_daily.get((v, date))
                row["last_year"] = ly
                if isinstance(ly, (int, float)):
                    row["vs_last_year"] = round(amt - ly, 2)
            rows.append(row)

    totals = {}
    for v in venues:
        t = {"actual": round(sum(a for (vv, _), a in actual.items() if vv == v), 2)}
        if "budget" in compare:
            vals = [b for (vv, _), b in budget_days.items() if vv == v]
            t["budget"] = round(
                sum(v2 for v2 in vals if isinstance(v2, (int, float))), 2
            )
        if "last_year" in compare:
            t["last_year"] = round(
                sum(a for (vv, _), a in ly_daily.items() if vv == v), 2
            )
        totals[v] = t

    result = {"window": window, "rows": rows, "totals": totals}
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
                (
                    "loadedhub",
                    "get_sales_data",
                    {
                        "venue": v,
                        "start_datetime": m_start.isoformat() + boundary + tz_offset,
                        "end_datetime": (m_end + datetime.timedelta(days=1)).isoformat()
                        + boundary
                        + tz_offset,
                        "interval": "01:00:00",
                    },
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
            amt = _amount_of(row)
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
        rows.append(row)

    totals = {}
    for v in venues:
        totals[v] = {
            lbl: round(sum(r[lbl] for r in rows if r["venue"] == v and lbl in r), 2)
            for lbl in labels
        }

    result = {"window": window, "rows": rows, "totals": totals}
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
        if len(per_day) <= 20:
            for v, day, sh, eh, label in per_day:
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
                            "end_time": day.isoformat()
                            + "T"
                            + str(eh).zfill(2)
                            + ":00:00"
                            + tz_offset,
                        },
                    )
                )
                meta.append((v, label, None))
        elif allowed is not None:
            # The monthly fallback cannot honour a day filter (whole-month
            # item calls include every weekday) — refuse rather than
            # silently widen the answer.
            return {
                "window": window,
                "error": (
                    "too many day-filtered item calls for one request ("
                    + str(len(per_day))
                    + ") — narrow the period or the windows"
                ),
            }
        else:
            for v in venues:
                for m_start, m_end in _month_chunks(start_date, end_date):
                    for w in time_windows:
                        sh = int(w.get("start_hour", 0))
                        eh = int(w.get("end_hour", 23))
                        label = w.get("label", str(sh) + "-" + str(eh))
                        calls.append(
                            (
                                "loadedhub",
                                "get_pos_item_sales",
                                {
                                    "venue": v,
                                    "start_time": m_start.isoformat()
                                    + "T"
                                    + str(sh).zfill(2)
                                    + ":00:00"
                                    + tz_offset,
                                    "end_time": m_end.isoformat()
                                    + "T"
                                    + str(eh).zfill(2)
                                    + ":00:00"
                                    + tz_offset,
                                },
                            )
                        )
                        meta.append((v, label, None))
            log("items: monthly window strategy (" + str(len(calls)) + " calls)")
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
    result = {"window": window, "rows": rows, "totals": totals}
    if len(venues) > 1:
        result["venues"] = venues
        result["note_venues"] = "rows are merged across the listed venues"
    if note:
        result["note"] = note
    if errors:
        result["errors"] = errors
    return result


# ── breakdown: staff ──────────────────────────────────────────────────────


def _breakdown_staff(window, venues, params, call_api, call_api_parallel, log):
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
            if amt <= 0:
                continue
            if name not in merged:
                merged[name] = {"staff": name, "orders": 0, "sales": 0}
            merged[name]["sales"] = merged[name]["sales"] + amt
            merged[name]["orders"] = int(merged[name]["orders"] + qty)
            if s.get("id"):
                ids[(v, name)] = str(s["id"])

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
        for payload in item_results:
            if isinstance(payload, dict) and payload.get("error"):
                return {"window": window, "error": str(payload["error"])}
            for item in _rows_of(payload) or []:
                name2 = item.get("itemName") or item.get("label") or "Unknown"
                amt = item.get("amount")
                qty = item.get("quantity")
                amt = float(amt) if isinstance(amt, (int, float)) else 0.0
                qty = float(qty) if isinstance(qty, (int, float)) else 0.0
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
            "sales": round(sum(r["sales"] for r in rows), 2),
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


def _breakdown_discounts(window, venues, call_api, call_api_parallel, log):
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
            name = str(row.get("label", "Unknown")).strip()
            if name not in merged:
                merged[name] = {
                    "staff": name,
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
                    m[dst] = m[dst] + val
    for m in merged.values():
        for k in ("discounts_amount", "discounts_count", "discounted_invoices"):
            m[k] = round(m[k], 2)
    rows = sorted(merged.values(), key=lambda r: -r["discounts_amount"])
    result = {
        "window": window,
        "rows": rows,
        "totals": {
            "discounts_amount": round(sum(r["discounts_amount"] for r in rows), 2),
            "discounts_count": round(sum(r["discounts_count"] for r in rows), 2),
        },
    }
    if len(venues) > 1:
        result["venues"] = venues
        result["note_venues"] = "rows are merged across the listed venues"
    if errors:
        result["errors"] = errors
    return result


# ── entry point ───────────────────────────────────────────────────────────


def run(params, call_api, log, call_api_parallel=None):
    window, auto_dow, err = _resolve_window(params, call_api, log)
    if err:
        return err

    venues, err = _resolve_venues(params, call_api)
    if err:
        err["window"] = window
        return err

    breakdown = str(params.get("breakdown") or "total").strip().lower()
    compare = params.get("compare") or []
    if isinstance(compare, str):
        compare = [c.strip().lower() for c in compare.split(",") if c.strip()]
    compare = [c for c in compare if c in ("budget", "last_year")]
    time_windows = params.get("time_windows")
    day_of_week = params.get("day_of_week") or auto_dow
    allowed = _allowed_days(day_of_week)

    if breakdown in ("total", "daily") and time_windows:
        if compare:
            return {
                "window": window,
                "error": (
                    "compare does not combine with time_windows — run them as two calls"
                ),
            }
        return _time_window_sales(
            window,
            venues,
            time_windows,
            str(
                params.get("group_by") or ("each" if breakdown == "daily" else "total")
            ),
            day_of_week,
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
                params.get("interval"),
                allowed,
                call_api,
                call_api_parallel,
                log,
            )
            rows = []
            for v in venues:
                t = (daily.get("totals") or {}).get(v) or {}
                row = {"venue": v, "actual": t.get("actual")}
                a = row["actual"]
                if "budget" in compare:
                    row["budget"] = t.get("budget")
                    if isinstance(a, (int, float)) and t.get("budget"):
                        row["vs_budget"] = round(a - t["budget"], 2)
                if "last_year" in compare:
                    row["last_year"] = t.get("last_year")
                    if isinstance(a, (int, float)) and t.get("last_year"):
                        row["vs_last_year"] = round(a - t["last_year"], 2)
                rows.append(row)
            out = {
                "window": window,
                "day_of_week": day_of_week,
                "rows": rows,
                "totals": {
                    "actual": round(
                        sum(
                            r["actual"]
                            for r in rows
                            if isinstance(r.get("actual"), (int, float))
                        ),
                        2,
                    )
                },
            }
            if daily.get("note"):
                out["note"] = daily["note"]
            if daily.get("last_year_window"):
                out["last_year_window"] = daily["last_year_window"]
            return out
        return _breakdown_total(
            window, venues, compare, call_api, call_api_parallel, log
        )
    if breakdown == "daily":
        result = _breakdown_daily(
            window,
            venues,
            compare,
            params.get("interval"),
            allowed,
            call_api,
            call_api_parallel,
            log,
        )
        if day_of_week:
            result["day_of_week"] = day_of_week
        return result
    if breakdown == "items":
        if compare:
            return {
                "window": window,
                "error": "compare works with breakdown 'total' or 'daily' only",
            }
        if allowed is not None and not time_windows:
            return {
                "window": window,
                "error": (
                    "the item-sales feed cannot filter by day of week over a "
                    "whole window — add time_windows (clock cuts) to slice "
                    "matching days, or drop day_of_week"
                ),
            }
        return _breakdown_items(
            window, venues, params, allowed, call_api, call_api_parallel, log
        )
    if breakdown == "staff":
        if compare:
            return {
                "window": window,
                "error": "compare works with breakdown 'total' or 'daily' only",
            }
        return _breakdown_staff(
            window, venues, params, call_api, call_api_parallel, log
        )
    if breakdown == "discounts":
        if compare:
            return {
                "window": window,
                "error": "compare works with breakdown 'total' or 'daily' only",
            }
        return _breakdown_discounts(window, venues, call_api, call_api_parallel, log)

    return {
        "error": (
            "unknown breakdown '"
            + breakdown
            + "' — use total, daily, items, staff, or discounts"
        )
    }
