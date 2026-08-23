"""Sales by clock-time window across a period.

Canonical source for the `get_periodic_sales` consolidator on the norm_reports spec
(synced by scripts/sync_periodic_reports_config.py). Until 22 Aug 2026 this
code lived ONLY in the config DB — the one consolidator family with no
reviewed file in the repo — and it required the model to supply exact
period_start/period_end dates it computed itself: the documented incident
class (a Saturday reported midnight-to-midnight). It now takes a period in
plain English and resolves it through Norm's venue calendar; explicit dates
remain the fallback. The clock-time window analysis is unchanged.
"""
# ruff: noqa: F821 — `datetime` and `json` are injected by the function-executor
# sandbox (_SAFE_MODULES), not imported.


def run(params, call_api, log, call_api_parallel):
    venue = params.get("venue", "")
    period_start = params.get("period_start", "")
    period_end = params.get("period_end", "")
    time_windows = params.get("time_windows", [])
    group_by = params.get("group_by", "each")
    day_filter = params.get("day_of_week", "")

    # Period in plain English resolves through Norm's venue calendar;
    # explicit period_start/period_end remain the exact-dates path. A
    # recurring phrase ("every Friday for the last 12 weeks") resolves to
    # the matching days: the envelope (first..last) becomes the range and, when every resolved day lands on the same weekday, the day filter is filled in to match the phrase.
    # When the period path resolves a window, the venue's day start rides
    # along ("07:00"): fetches then run day-start to day-start and every
    # hour before it belongs to the PREVIOUS trading day — Loaded's own
    # daily figures attribute a Saturday's 1am trade to Saturday, and civil
    # midnight bucketing under-reported that Saturday by $4.5k (prod thread
    # b9bda2c1, 23 Aug 2026). Explicit period_start/period_end stay civil
    # calendar days, documented as such.
    day_start_hour = 0
    period = (params.get("period") or "").strip()
    if period and not (period_start and period_end):
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
        periods = data.get("periods") if isinstance(data, dict) else None
        if isinstance(window, dict) and window.get("start"):
            ds = str(window.get("day_start") or "")
            try:
                day_start_hour = int(ds[:2]) if ds else 0
            except ValueError:
                day_start_hour = 0
            period_start = str(window.get("start"))[:10]
            wend = str(window.get("end") or "")
            period_end = wend[:10]
            # A trading window's end is the small hours of the NEXT civil
            # day (Mon 06:59) - not a day this report should include.
            if len(wend) >= 13 and wend[11:13] < "12":
                period_end = (
                    datetime.date.fromisoformat(period_end) - datetime.timedelta(days=1)
                ).isoformat()
        elif isinstance(periods, list) and periods:
            starts = sorted(str(q.get("start"))[:10] for q in periods if q.get("start"))
            ends = sorted(str(q.get("end"))[:10] for q in periods if q.get("end"))
            if starts and ends:
                period_start, period_end = starts[0], ends[-1]
            if not day_filter and len(starts) > 1:
                names = (
                    "monday",
                    "tuesday",
                    "wednesday",
                    "thursday",
                    "friday",
                    "saturday",
                    "sunday",
                )
                dows = {datetime.date.fromisoformat(d).weekday() for d in starts}
                if len(dows) == 1:
                    day_filter = names[dows.pop()]
                    log("recurring period - filtering to " + day_filter)
        if not period_start or not period_end:
            return {"error": "could not resolve '" + period + "' to dates"}

    if not venue or not period_start or not period_end or not time_windows:
        return {
            "error": "venue, period_start, period_end, and time_windows are required"
        }

    if isinstance(time_windows, str):
        time_windows = json.loads(time_windows)

    start_date = datetime.date.fromisoformat(period_start)
    end_date = datetime.date.fromisoformat(period_end)
    tz_offset = params.get("tz_offset", "%2B12:00")

    DOW = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    DOW_NAMES = {
        0: "Monday",
        1: "Tuesday",
        2: "Wednesday",
        3: "Thursday",
        4: "Friday",
        5: "Saturday",
        6: "Sunday",
    }
    MONTH_NAMES = {
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
    MONTH_SHORT = {
        1: "Jan",
        2: "Feb",
        3: "Mar",
        4: "Apr",
        5: "May",
        6: "Jun",
        7: "Jul",
        8: "Aug",
        9: "Sep",
        10: "Oct",
        11: "Nov",
        12: "Dec",
    }

    allowed_days = None
    if day_filter:
        df = day_filter.lower().strip()
        if df == "weekday":
            allowed_days = {0, 1, 2, 3, 4}
        elif df == "weekend":
            allowed_days = {4, 5, 6}
        else:
            allowed_days = set()
            for d in df.split(","):
                d = d.strip()
                if d in DOW:
                    allowed_days.add(DOW[d])

    # Strategy: fetch hourly data per month (few API calls), then filter/sum server-side
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

    log(
        "Fetching hourly data for "
        + str(len(months))
        + " months ("
        + str(len(months))
        + " API calls)"
    )

    api_calls = []
    boundary = "T" + str(day_start_hour).zfill(2) + ":00:00"
    for m_start, m_end in months:
        start_str = m_start.isoformat() + boundary + tz_offset
        end_str = (
            (m_end + datetime.timedelta(days=1)).isoformat() + boundary + tz_offset
        )
        api_calls.append(
            (
                "loadedhub",
                "get_sales_data",
                {
                    "venue": venue,
                    "start_datetime": start_str,
                    "end_datetime": end_str,
                    "interval": "01:00:00",
                },
            )
        )

    month_results = call_api_parallel(api_calls)

    # Process hourly rows: check which window and day each belongs to
    window_labels = [
        w.get("label", "Window " + str(i)) for i, w in enumerate(time_windows)
    ]
    day_window_amounts = {}

    for result in month_results:
        if not isinstance(result, list):
            continue
        for row in result:
            start_time_str = str(row.get("startTime", ""))
            amount = row.get("invoices") or row.get("amount") or 0
            if not isinstance(amount, (int, float)) or amount == 0:
                continue

            # Parse date and hour from startTime (e.g. 2026-01-06T11:00:00+13:00)
            try:
                date_part = start_time_str[:10]
                hour = int(start_time_str[11:13])
                row_date = datetime.date.fromisoformat(date_part)
                if hour < day_start_hour:
                    # Before the venue's day start: this trade belongs to
                    # the PREVIOUS trading day (a Saturday's 1am sales are
                    # Saturday's, as Loaded's own daily figures report).
                    row_date = row_date - datetime.timedelta(days=1)
            except (ValueError, IndexError):
                continue

            # Day-of-week filter
            if allowed_days is not None and row_date.weekday() not in allowed_days:
                continue

            # Check which time window this hour falls into
            for window in time_windows:
                sh = int(window.get("start_hour", 0))
                eh = int(window.get("end_hour", 23))
                label = window.get("label", str(sh) + "-" + str(eh))

                in_window = False
                if eh > sh:
                    in_window = sh <= hour < eh
                else:
                    in_window = hour >= sh or hour < eh

                if in_window:
                    key = (row_date, label)
                    if key not in day_window_amounts:
                        day_window_amounts[key] = 0
                    day_window_amounts[key] = day_window_amounts[key] + float(amount)

    def fmt_date(d):
        return (
            DOW_NAMES.get(d.weekday(), "")
            + " "
            + str(d.day).zfill(2)
            + " "
            + MONTH_SHORT.get(d.month, "")
            + " "
            + str(d.year)
        )

    def fmt_month(d):
        return MONTH_NAMES.get(d.month, "") + " " + str(d.year)

    # Aggregate by group_by
    if group_by == "each":
        all_days = sorted(set(k[0] for k in day_window_amounts.keys()))
        rows = []
        for day in all_days:
            row = {"period": fmt_date(day)}
            for lbl in window_labels:
                row[lbl] = round(day_window_amounts.get((day, lbl), 0), 2)
            rows.append(row)
    elif group_by == "month":
        month_agg = {}
        month_order = {}
        for (day, label), amt in day_window_amounts.items():
            mk = fmt_month(day)
            if mk not in month_agg:
                month_agg[mk] = {}
                month_order[mk] = day.replace(day=1)
            month_agg[mk][label] = month_agg[mk].get(label, 0) + amt
        rows = []
        for mk in sorted(month_agg.keys(), key=lambda k: month_order[k]):
            row = {"period": mk}
            for lbl in window_labels:
                row[lbl] = round(month_agg[mk].get(lbl, 0), 2)
            rows.append(row)
    elif group_by == "week":
        week_agg = {}
        for (day, label), amt in day_window_amounts.items():
            iso = day.isocalendar()
            wk = str(iso[0]) + "-W" + str(iso[1]).zfill(2)
            if wk not in week_agg:
                week_agg[wk] = {}
            week_agg[wk][label] = week_agg[wk].get(label, 0) + amt
        rows = []
        for wk in sorted(week_agg.keys()):
            row = {"period": wk}
            for lbl in window_labels:
                row[lbl] = round(week_agg[wk].get(lbl, 0), 2)
            rows.append(row)
    elif group_by == "total":
        totals = {}
        for (day, label), amt in day_window_amounts.items():
            totals[label] = totals.get(label, 0) + amt
        rows = [{"period": "Total"}]
        for lbl in window_labels:
            rows[0][lbl] = round(totals.get(lbl, 0), 2)
    else:
        rows = []

    totals_row = {}
    for lbl in window_labels:
        totals_row[lbl] = round(sum(r.get(lbl, 0) for r in rows), 2)

    day_count = len(set(k[0] for k in day_window_amounts.keys()))
    log(
        "Result: "
        + str(len(rows))
        + " rows from "
        + str(day_count)
        + " days ("
        + str(len(api_calls))
        + " API calls)"
    )
    return {
        "rows": rows,
        "totals": totals_row,
        "summary": str(day_count)
        + " days, "
        + str(len(time_windows))
        + " windows, grouped by "
        + group_by,
    }
