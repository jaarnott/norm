"""Product sales by clock-time window across a period.

Canonical source for the `get_periodic_product_sales` consolidator on the norm_reports spec
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
    group_by = params.get("group_by", "total")
    category_filter = params.get("category", "")
    group_filter = params.get("group", "")
    sort_by = params.get("sort_by", "sales")
    top_n = params.get("top_n", 0)
    time_windows = params.get("time_windows", [])
    day_of_week = params.get("day_of_week", "")

    # Period in plain English resolves through Norm's venue calendar;
    # explicit period_start/period_end remain the exact-dates path. A
    # recurring phrase ("every Friday for the last 12 weeks") resolves to
    # the matching days: the envelope (first..last) becomes the range and, when every resolved day lands on the same weekday, the day filter is filled in to match the phrase.
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
            if not day_of_week and len(starts) > 1:
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
                    day_of_week = names[dows.pop()]
                    log("recurring period - filtering to " + day_of_week)
        if not period_start or not period_end:
            return {"error": "could not resolve '" + period + "' to dates"}

    if not venue or not period_start or not period_end:
        return {"error": "venue, period_start, and period_end are required"}

    if isinstance(time_windows, str):
        time_windows = json.loads(time_windows)
    if isinstance(top_n, str):
        top_n = int(top_n) if top_n else 0

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

    allowed_days = None
    if day_of_week:
        df = day_of_week.lower().strip()
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

    # If time_windows specified, use the periodic approach (per-day calls with hour filtering)
    # Otherwise, use monthly chunks (more efficient for simple period queries)
    if time_windows:
        return _periodic_product_sales(
            params,
            call_api,
            log,
            call_api_parallel,
            venue,
            start_date,
            end_date,
            tz_offset,
            time_windows,
            allowed_days,
            group_by,
            category_filter,
            group_filter,
            sort_by,
            top_n,
            MONTH_NAMES,
        )

    # Monthly chunk strategy: one API call per month
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

    log("Fetching product sales for " + str(len(months)) + " months at " + venue)

    api_calls = []
    for m_start, m_end in months:
        start_str = m_start.isoformat() + "T00:00:00" + tz_offset
        end_str = (
            (m_end + datetime.timedelta(days=1)).isoformat() + "T00:00:00" + tz_offset
        )
        api_calls.append(
            (
                "loadedhub",
                "get_pos_item_sales",
                {
                    "venue": venue,
                    "start_time": start_str,
                    "end_time": end_str,
                },
            )
        )

    results = call_api_parallel(api_calls)

    # Aggregate products across months
    if group_by == "month":
        # Keep per-month breakdown
        month_products = {}
        for i, result in enumerate(results):
            if not isinstance(result, list):
                continue
            m_start, m_end = months[i]
            mk = MONTH_NAMES.get(m_start.month, "") + " " + str(m_start.year)
            for item in result:
                name = item.get("itemName", "Unknown")
                cat = item.get("itemCategoryName", "")
                grp = item.get("itemGroupName", "")
                sales = (
                    item.get("amount") or item.get("sales") or item.get("invoices") or 0
                )
                qty = item.get("quantity") or 0

                if category_filter and category_filter.lower() not in cat.lower():
                    continue
                if group_filter and group_filter.lower() not in grp.lower():
                    continue

                if not isinstance(sales, (int, float)):
                    sales = 0
                if not isinstance(qty, (int, float)):
                    qty = 0

                key = (name, mk)
                if key not in month_products:
                    month_products[key] = {
                        "itemName": name,
                        "category": cat,
                        "group": grp,
                        "period": mk,
                        "sales": 0,
                        "quantity": 0,
                    }
                month_products[key]["sales"] = month_products[key]["sales"] + float(
                    sales
                )
                month_products[key]["quantity"] = month_products[key]["quantity"] + int(
                    qty
                )

        rows = list(month_products.values())
    else:
        # Aggregate across all months (total)
        product_totals = {}
        for result in results:
            if not isinstance(result, list):
                continue
            for item in result:
                name = item.get("itemName", "Unknown")
                cat = item.get("itemCategoryName", "")
                grp = item.get("itemGroupName", "")
                sales = (
                    item.get("amount") or item.get("sales") or item.get("invoices") or 0
                )
                qty = item.get("quantity") or 0

                if category_filter and category_filter.lower() not in cat.lower():
                    continue
                if group_filter and group_filter.lower() not in grp.lower():
                    continue

                if not isinstance(sales, (int, float)):
                    sales = 0
                if not isinstance(qty, (int, float)):
                    qty = 0

                if name not in product_totals:
                    product_totals[name] = {
                        "itemName": name,
                        "category": cat,
                        "group": grp,
                        "sales": 0,
                        "quantity": 0,
                    }
                product_totals[name]["sales"] = product_totals[name]["sales"] + float(
                    sales
                )
                product_totals[name]["quantity"] = product_totals[name][
                    "quantity"
                ] + int(qty)

        rows = list(product_totals.values())

    # Sort
    sort_field = "sales" if sort_by != "quantity" else "quantity"
    rows.sort(key=lambda r: r.get(sort_field, 0), reverse=True)

    # Round sales
    for r in rows:
        r["sales"] = round(r["sales"], 2)

    # Top N
    total_count = len(rows)
    if top_n and top_n > 0:
        rows = rows[:top_n]

    # Grand totals
    grand_sales = round(sum(r["sales"] for r in rows), 2)
    grand_qty = sum(r["quantity"] for r in rows)

    log(
        "Result: "
        + str(len(rows))
        + " products"
        + (" (top " + str(top_n) + " of " + str(total_count) + ")" if top_n else "")
    )
    return {
        "rows": rows,
        "totals": {"sales": grand_sales, "quantity": grand_qty},
        "summary": str(total_count)
        + " products, "
        + str(len(months))
        + " months, sorted by "
        + sort_field,
    }


def _periodic_product_sales(
    params,
    call_api,
    log,
    call_api_parallel,
    venue,
    start_date,
    end_date,
    tz_offset,
    time_windows,
    allowed_days,
    group_by,
    category_filter,
    group_filter,
    sort_by,
    top_n,
    MONTH_NAMES,
):
    """Handle time-window product analysis (e.g. lunch vs dinner product mix)."""

    # For time-window analysis, we need per-day calls since the API doesn't support hour filtering
    # Use the same strategy as get_periodic_sales: monthly hourly data, filter server-side
    # BUT get_pos_item_sales doesn't have an interval param — it returns product totals for the whole period
    # So we need to call per-window per chunk (one call per window per month)

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

    window_labels = [
        w.get("label", "Window " + str(i)) for i, w in enumerate(time_windows)
    ]

    # For each month x window, call the API with the window's hours
    api_calls = []
    call_meta = []
    for m_start, m_end in months:
        for window in time_windows:
            sh = int(window.get("start_hour", 0))
            eh = int(window.get("end_hour", 23))
            label = window.get("label", str(sh) + "-" + str(eh))
            mk = MONTH_NAMES.get(m_start.month, "") + " " + str(m_start.year)

            # Call per day within the month for this window
            day = m_start
            while day <= m_end:
                if allowed_days is None or day.weekday() in allowed_days:
                    start_str = (
                        day.isoformat() + "T" + str(sh).zfill(2) + ":00:00" + tz_offset
                    )
                    end_str = (
                        day.isoformat() + "T" + str(eh).zfill(2) + ":00:00" + tz_offset
                    )
                    api_calls.append(
                        (
                            "loadedhub",
                            "get_pos_item_sales",
                            {
                                "venue": venue,
                                "start_time": start_str,
                                "end_time": end_str,
                            },
                        )
                    )
                    call_meta.append({"label": label, "month": mk})
                day = day + datetime.timedelta(days=1)

    if len(api_calls) > 20:
        # Too many calls for per-day approach — fall back to per-month-per-window
        api_calls = []
        call_meta = []
        for m_start, m_end in months:
            for window in time_windows:
                sh = int(window.get("start_hour", 0))
                eh = int(window.get("end_hour", 23))
                label = window.get("label", str(sh) + "-" + str(eh))
                mk = MONTH_NAMES.get(m_start.month, "") + " " + str(m_start.year)
                start_str = (
                    m_start.isoformat() + "T" + str(sh).zfill(2) + ":00:00" + tz_offset
                )
                end_str = (
                    m_end.isoformat() + "T" + str(eh).zfill(2) + ":00:00" + tz_offset
                )
                api_calls.append(
                    (
                        "loadedhub",
                        "get_pos_item_sales",
                        {
                            "venue": venue,
                            "start_time": start_str,
                            "end_time": end_str,
                        },
                    )
                )
                call_meta.append({"label": label, "month": mk})
        log("Using monthly window strategy: " + str(len(api_calls)) + " calls")
    else:
        log("Using daily window strategy: " + str(len(api_calls)) + " calls")

    results = call_api_parallel(api_calls)

    # Aggregate: product x window (x month if group_by=month)
    product_data = {}
    for i, result in enumerate(results):
        if not isinstance(result, list):
            continue
        meta = call_meta[i]
        for item in result:
            name = item.get("itemName", "Unknown")
            cat = item.get("itemCategoryName", "")
            grp = item.get("itemGroupName", "")
            sales = item.get("amount") or item.get("sales") or item.get("invoices") or 0
            qty = item.get("quantity") or 0

            if category_filter and category_filter.lower() not in cat.lower():
                continue
            if group_filter and group_filter.lower() not in grp.lower():
                continue

            if not isinstance(sales, (int, float)):
                sales = 0
            if not isinstance(qty, (int, float)):
                qty = 0

            if group_by == "month":
                key = (name, meta["month"])
            else:
                key = (name,)

            if key not in product_data:
                product_data[key] = {"itemName": name, "category": cat, "group": grp}
                if group_by == "month":
                    product_data[key]["period"] = meta["month"]
                for lbl in window_labels:
                    product_data[key][lbl + " sales"] = 0
                    product_data[key][lbl + " qty"] = 0

            product_data[key][meta["label"] + " sales"] = product_data[key].get(
                meta["label"] + " sales", 0
            ) + float(sales)
            product_data[key][meta["label"] + " qty"] = product_data[key].get(
                meta["label"] + " qty", 0
            ) + int(qty)

    rows = list(product_data.values())

    # Sort by first window's sales
    first_sales_key = window_labels[0] + " sales" if window_labels else "sales"
    sort_field = (
        first_sales_key
        if sort_by != "quantity"
        else (window_labels[0] + " qty" if window_labels else "quantity")
    )
    rows.sort(key=lambda r: r.get(sort_field, 0), reverse=True)

    # Round
    for r in rows:
        for lbl in window_labels:
            r[lbl + " sales"] = round(r.get(lbl + " sales", 0), 2)

    total_count = len(rows)
    if top_n and top_n > 0:
        rows = rows[:top_n]

    log(
        "Result: "
        + str(len(rows))
        + " products across "
        + str(len(window_labels))
        + " windows"
    )
    return {
        "rows": rows,
        "summary": str(total_count)
        + " products, "
        + str(len(window_labels))
        + " windows"
        + (", top " + str(top_n) if top_n else ""),
    }
