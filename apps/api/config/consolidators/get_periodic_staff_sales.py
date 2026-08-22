"""Staff sales across a period.

Canonical source for the `get_periodic_staff_sales` consolidator on the norm_reports spec
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
    interval = params.get("interval", "")
    top_n = params.get("top_n", 0)
    group_by = params.get("group_by", "each")

    # Period in plain English resolves through Norm's venue calendar;
    # explicit period_start/period_end remain the exact-dates path. A
    # recurring phrase ("every Friday for the last 12 weeks") resolves to
    # the matching days: the envelope (first..last) becomes the range.
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
            if len(periods) > 1:
                return {
                    "error": (
                        "this tool reads one continuous range - pass a simple "
                        "period like 'last 12 weeks' (recurring day-of-week "
                        "comparisons live in get_periodic_sales)"
                    )
                }
        if not period_start or not period_end:
            return {"error": "could not resolve '" + period + "' to dates"}

    if not venue or not period_start or not period_end:
        return {"error": "venue, period_start, and period_end are required"}

    if isinstance(top_n, str):
        top_n = int(top_n) if top_n else 0

    end_date = datetime.date.fromisoformat(period_end)
    tz_offset = params.get("tz_offset", "%2B12:00")

    start_str = period_start + "T00:00:00" + tz_offset
    end_str = (
        (end_date + datetime.timedelta(days=1)).isoformat() + "T00:00:00" + tz_offset
    )

    # Step 1: Get per-staff totals
    log("Fetching staff totals for " + venue)
    totals_result = call_api(
        "loadedhub",
        "get_staff_orders",
        {
            "venue": venue,
            "start": start_str,
            "end": end_str,
        },
    )

    if not isinstance(totals_result, list):
        return {"error": "Failed to fetch staff totals: " + str(totals_result)}

    # Parse and sort staff by sales
    staff_list = []
    for s in totals_result:
        name = s.get("label", "Unknown")
        sid = s.get("id", "")
        amount = s.get("amount") or s.get("invoices") or 0
        qty = s.get("quantity") or s.get("count") or 0
        if not isinstance(amount, (int, float)):
            amount = 0
        if not isinstance(qty, (int, float)):
            qty = 0
        if amount > 0:
            staff_list.append(
                {
                    "name": name.strip(),
                    "id": str(sid),
                    "sales": round(float(amount), 2),
                    "orders": int(qty),
                }
            )

    staff_list.sort(key=lambda x: x["sales"], reverse=True)

    # Apply top_n filter
    if top_n and top_n > 0:
        staff_list = staff_list[:top_n]

    log("Found " + str(len(staff_list)) + " staff with sales")

    # If no interval requested, just return totals
    if not interval:
        return {
            "staff_totals": staff_list,
            "summary": str(len(staff_list))
            + " staff, sorted by sales, "
            + period_start
            + " to "
            + period_end,
        }

    # Step 2: Get interval breakdown for each staff member (parallel)
    log(
        "Fetching " + interval + " interval data for " + str(len(staff_list)) + " staff"
    )

    interval_calls = []
    for s in staff_list:
        interval_calls.append(
            (
                "loadedhub",
                "get_staff_orders",
                {
                    "venue": venue,
                    "start": start_str,
                    "end": end_str,
                    "staff_id": s["id"],
                    "interval": interval,
                },
            )
        )

    interval_results = call_api_parallel(interval_calls)

    # Step 3: Build interval data per staff
    # For each time slot, track all staff amounts
    slot_data = {}  # slot_key -> {staff_name: amount}
    staff_interval_data = {}  # staff_name -> [{period, amount, count}]

    HOUR_NAMES = {}
    for h in range(24):
        if h == 0:
            HOUR_NAMES[h] = "12:00 AM"
        elif h < 12:
            HOUR_NAMES[h] = str(h) + ":00 AM"
        elif h == 12:
            HOUR_NAMES[h] = "12:00 PM"
        else:
            HOUR_NAMES[h] = str(h - 12) + ":00 PM"

    for i, result in enumerate(interval_results):
        if not isinstance(result, list):
            continue
        staff_name = staff_list[i]["name"]
        staff_interval_data[staff_name] = []

        for row in result:
            st = str(row.get("startTime", ""))
            amount = row.get("amount") or row.get("invoices") or 0
            count = row.get("count") or row.get("quantity") or 0
            if not isinstance(amount, (int, float)):
                amount = 0
            if amount == 0:
                continue

            # Parse time for display
            try:
                hour = int(st[11:13])
                minute = int(st[13:16].replace(":", "")) if len(st) > 13 else 0
                date_part = st[:10]
            except (ValueError, IndexError):
                continue

            # Format slot name with date context
            if minute > 0:
                time_label = str(hour % 12 or 12) + ":" + str(minute).zfill(2)
            else:
                time_label = str(hour % 12 or 12) + ":00"
            time_label = time_label + (" PM" if hour >= 12 else " AM")

            DOW_NAMES = {
                0: "Mon",
                1: "Tue",
                2: "Wed",
                3: "Thu",
                4: "Fri",
                5: "Sat",
                6: "Sun",
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
            try:
                d = datetime.date.fromisoformat(date_part)
                dow = DOW_NAMES.get(d.weekday(), "")
                date_label = dow + " " + str(d.day) + " " + MONTH_SHORT.get(d.month, "")
            except ValueError:
                date_label = date_part

            if group_by == "day":
                slot_key = date_label
            else:
                slot_key = st  # Full timestamp for uniqueness

            # Always include date + time in the period label
            slot_label = date_label + " " + time_label

            if slot_key not in slot_data:
                slot_data[slot_key] = {
                    "period": slot_label if group_by == "each" else date_label
                }
            slot_data[slot_key][staff_name] = round(float(amount), 2)

            staff_interval_data.setdefault(staff_name, []).append(
                {
                    "period": slot_label,
                    "date": date_part,
                    "sales": round(float(amount), 2),
                    "orders": int(count),
                }
            )

    # Step 4: Build interval winners
    interval_winners = []
    for slot_key in sorted(slot_data.keys()):
        slot = slot_data[slot_key]
        period_label = slot.get("period", slot_key)
        best_name = ""
        best_amount = 0
        for s in staff_list:
            amt = slot.get(s["name"], 0)
            if amt > best_amount:
                best_amount = amt
                best_name = s["name"]
        if best_name and best_amount > 0:
            interval_winners.append(
                {
                    "period": period_label,
                    "winner": best_name,
                    "sales": best_amount,
                }
            )

    # Compute win counts
    win_counts = {}
    for w in interval_winners:
        win_counts[w["winner"]] = win_counts.get(w["winner"], 0) + 1

    # Find overall top performer
    top_performer = staff_list[0]["name"] if staff_list else "N/A"
    top_sales = staff_list[0]["sales"] if staff_list else 0

    log(
        "Result: "
        + str(len(staff_list))
        + " staff, "
        + str(len(interval_winners))
        + " interval slots"
    )

    return {
        "staff_totals": staff_list,
        "interval_winners": interval_winners,
        "win_counts": win_counts,
        "summary": str(len(staff_list))
        + " staff, "
        + str(len(interval_winners))
        + " slots, top: "
        + top_performer
        + " ($"
        + str(int(top_sales))
        + ")",
    }
