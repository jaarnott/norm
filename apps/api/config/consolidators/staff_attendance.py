"""get_staff_attendance — rostered vs actual hours from LoadedHub.

Canonical source for the `get_staff_attendance` consolidator on the loadedhub
connector spec (synced into the config DB by scripts/sync_staff_attendance_config.py;
exec'd under the real sandbox namespace by tests/test_staff_attendance_consolidator.py).

Loaded's /api/time-clockins endpoint returns BOOKED LEAVE as pseudo clock-ins
(type "Leave", rendered as a 7:00 AM-3:00 PM 8h entry costed at the pay rate)
alongside real entries (type "Clockin"). Loaded's own screens show leave in the
leave calendar, not on the timesheet — so counting leave as worked hours invents
"ghost shifts" (Bessie, Sat 15 Aug 2026: two leave entries read as two unrostered
8h shifts, +16h variance). Leave is therefore split out into its own list/totals
in every view and never counted as actual worked time.
"""
# ruff: noqa: F821 — `datetime` and `json` are injected by the function-executor
# sandbox (_SAFE_MODULES), not imported.


def run(params, call_api, log, call_api_parallel):
    start = params.get("start_datetime", "")
    end = params.get("end_datetime", "")
    venue = params.get("venue", "")
    staff_name = params.get("staff_name", "")
    group_by = params.get("group_by", "staff")

    # Attendance windows are TRADING windows — resolve a plain-English
    # period through Norm's venue calendar (same pattern as
    # for_period.py / received_items_for_period.py). Explicit datetimes
    # remain the exact-times path, gated when they don't line up with the
    # venue's trading day so a midnight window can't silently split a
    # trading session.
    period = (params.get("period") or "").strip()
    if period or (start and end):
        resolve_args = {}
        if params.get("venue_id"):
            resolve_args["venue_id"] = params["venue_id"]
        if period:
            resolve_args["query"] = period
        else:
            resolve_args["start"] = start
            resolve_args["end"] = end
        resolved = call_api("norm", "resolve_dates", resolve_args)
        window = resolved.get("window") if isinstance(resolved, dict) else None
        if not isinstance(window, dict):
            data = resolved.get("data") if isinstance(resolved, dict) else None
            window = data.get("window") if isinstance(data, dict) else None
        if not isinstance(window, dict):
            return {
                "error": (
                    "could not resolve "
                    + (repr(period) if period else "that range")
                    + " to a window — try a simpler period such as 'last week'"
                )
            }
        if not window.get("trading_aligned") and not params.get("confirmed_by_user"):
            log("explicit window is not a trading day; asking before fetching")
            return {
                "needs_confirmation": True,
                "window": window,
                "question": (
                    "These times are not this venue's trading day. "
                    + str(window.get("description", ""))
                    + " Did the user explicitly ask for these exact clock "
                    "times? If yes, call again with confirmed_by_user=true. "
                    "If they asked for a named period, pass period instead "
                    "and no start/end."
                ),
            }
        start = window["start"]
        end = window["end"]
        window_out = {
            "start": start,
            "end": end,
            "description": window.get("description"),
        }
    else:
        return {
            "error": (
                "give a period in plain English (e.g. 'last week') or both "
                "start_datetime and end_datetime"
            )
        }

    venue_params = {}
    if venue:
        venue_params["venue"] = venue

    log(
        "Fetching roster + timeclock for "
        + venue
        + " ("
        + start[:10]
        + " to "
        + end[:10]
        + ")"
    )

    results = call_api_parallel(
        [
            (
                "loadedhub",
                "get_roster",
                {"start_datetime": start, "end_datetime": end, **venue_params},
            ),
            (
                "loadedhub",
                "get_timeclock_entries",
                {
                    "start_time": start,
                    "end_time": end,
                    "include_inactive": "false",
                    "include_only_clockins": "false",
                    "should_truncate_shifts": "true",
                    **venue_params,
                },
            ),
        ]
    )
    roster_data = results[0]
    clockin_data = results[1]

    rosters = (
        roster_data
        if isinstance(roster_data, list)
        else [roster_data]
        if roster_data
        else []
    )
    clockins = (
        clockin_data
        if isinstance(clockin_data, list)
        else [clockin_data]
        if clockin_data
        else []
    )

    def normalise(dt_str):
        if not dt_str:
            return ""
        return str(dt_str).replace("%2B", "+")[:19]

    def extract_offset_mins(dt_str):
        """Extract UTC offset in minutes from ISO datetime string e.g. +12:00 → 720, +00:00 → 0."""
        s = str(dt_str or "")
        for pos in range(len(s) - 1, 15, -1):
            if s[pos] in ("+", "-"):
                tz = s[pos:]
                parts = tz[1:].split(":")
                try:
                    h = int(parts[0])
                    m = int(parts[1]) if len(parts) > 1 else 0
                    mins = h * 60 + m
                    return mins if s[pos] == "+" else -mins
                except (ValueError, IndexError):
                    pass
                break
        return 0

    def to_local_norm(dt_str, local_offset_mins):
        """Convert an ISO datetime (any offset) to local time as YYYY-MM-DDTHH:MM:SS."""
        n = normalise(dt_str)
        if not n or len(n) < 19:
            return n
        src_offset = extract_offset_mins(dt_str)
        diff = local_offset_mins - src_offset
        if diff == 0:
            return n
        try:
            dt = datetime.datetime(
                int(n[0:4]),
                int(n[5:7]),
                int(n[8:10]),
                int(n[11:13]),
                int(n[14:16]),
                int(n[17:19]),
            )
            dt = dt + datetime.timedelta(minutes=diff)

            def z2(v):
                return str(v).zfill(2)

            return (
                str(dt.year)
                + "-"
                + z2(dt.month)
                + "-"
                + z2(dt.day)
                + "T"
                + z2(dt.hour)
                + ":"
                + z2(dt.minute)
                + ":"
                + z2(dt.second)
            )
        except Exception:
            return n

    req_start = normalise(start)
    req_end = normalise(end)

    def in_range(dt_str):
        n = normalise(dt_str)
        return n and req_start <= n <= req_end

    def name_matches(first, last, query):
        if not query:
            return True
        q = query.lower().strip()
        full = ((first or "") + " " + (last or "")).lower().strip()
        return q in full or q in (first or "").lower() or q in (last or "").lower()

    def is_leave(entry):
        """Loaded returns booked leave as a pseudo clock-in with type 'Leave'.

        Leave must never count as worked time; anything else (only 'Clockin'
        observed live) stays an actual entry so unknown future types aren't
        silently dropped."""
        return str(entry.get("type") or "") == "Leave"

    DOW_NAMES = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
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

    def fmt_date(d):
        return (
            DOW_NAMES.get(d.weekday(), "")
            + " "
            + str(d.day)
            + " "
            + MONTH_SHORT.get(d.month, "")
        )

    def fmt_time(dt_str):
        if not dt_str or len(dt_str) < 16:
            return ""
        h = int(dt_str[11:13])
        m = dt_str[14:16]
        ampm = "AM" if h < 12 else "PM"
        h12 = h % 12 or 12
        return str(h12) + ":" + m + " " + ampm

    def fmt_breaks(raw_breaks, local_offset_mins):
        """Format a breaks array into clean dicts with local times and duration."""
        result = []
        for b in raw_breaks or []:
            if b.get("deletedAt"):
                continue
            bs = to_local_norm(b.get("breakStart", ""), local_offset_mins)
            be = to_local_norm(b.get("breakEnd", ""), local_offset_mins)
            # Compute duration in minutes
            dur_mins = 0
            if bs and be and len(bs) >= 16 and len(be) >= 16:
                try:
                    s_dt = datetime.datetime(
                        int(bs[0:4]),
                        int(bs[5:7]),
                        int(bs[8:10]),
                        int(bs[11:13]),
                        int(bs[14:16]),
                    )
                    e_dt = datetime.datetime(
                        int(be[0:4]),
                        int(be[5:7]),
                        int(be[8:10]),
                        int(be[11:13]),
                        int(be[14:16]),
                    )
                    dur_mins = max(0, int((e_dt - s_dt).total_seconds() // 60))
                except Exception:
                    pass
            result.append(
                {
                    "start": fmt_time(bs),
                    "end": fmt_time(be),
                    "duration_mins": dur_mins,
                    "paid": bool(b.get("paid", False)),
                }
            )
        return result

    # ── detail view: separate sorted lists (worked clock-ins vs leave) ─────────
    if group_by == "detail":
        rostered_rows = []
        for roster in rosters:
            for shift in roster.get("rosteredShifts", []):
                shift_start = shift.get("clockinTime", "")
                if not shift_start or not in_range(shift_start):
                    continue
                if shift.get("datestampDeleted"):
                    continue
                first = shift.get("staffMemberFirstName", "")
                last = shift.get("staffMemberLastName", "")
                if not name_matches(first, last, staff_name):
                    continue
                jobs_raw = shift.get("jobs", "")
                job = ""
                if jobs_raw:
                    try:
                        parsed = (
                            json.loads(jobs_raw)
                            if isinstance(jobs_raw, str)
                            else jobs_raw
                        )
                        job = (
                            ", ".join(str(j) for j in parsed)
                            if isinstance(parsed, list)
                            else str(parsed)
                        )
                    except Exception:
                        job = str(jobs_raw)[:50]
                shift_start_n = normalise(shift_start)
                local_off = extract_offset_mins(shift_start)
                try:
                    d = datetime.date.fromisoformat(shift_start_n[:10])
                    date_label = fmt_date(d)
                except ValueError:
                    date_label = shift_start_n[:10]
                breaks = fmt_breaks(shift.get("breaks", []), local_off)
                row = {
                    "_sort": shift_start_n,
                    "date": date_label,
                    "name": ((first or "") + " " + (last or "")).strip(),
                    "role": shift.get("roleName", ""),
                    "job": job,
                    "start": fmt_time(shift_start),
                    "end": fmt_time(normalise(shift.get("clockoutTime", ""))),
                    "hours": round(float(shift.get("totalHours", 0) or 0), 2),
                    "cost": round(float(shift.get("totalCost", 0) or 0), 2),
                }
                if breaks:
                    row["breaks"] = breaks
                rostered_rows.append(row)

        clockin_rows = []
        leave_rows = []
        for c in clockins:
            ci_start = c.get("clockinTime", "")
            if not ci_start or not in_range(ci_start):
                continue
            first = c.get("staffMemberFirstName", "")
            last = c.get("staffMemberLastName", "")
            if not name_matches(first, last, staff_name):
                continue
            ci_start_n = normalise(ci_start)
            ci_end = c.get("clockoutTime", "")
            local_off = extract_offset_mins(ci_start)
            try:
                d = datetime.date.fromisoformat(ci_start_n[:10])
                date_label = fmt_date(d)
            except ValueError:
                date_label = ci_start_n[:10]
            breaks = fmt_breaks(c.get("breaks", []), local_off)
            row = {
                "_sort": ci_start_n,
                "date": date_label,
                "name": ((first or "") + " " + (last or "")).strip(),
                "role": c.get("roleName", ""),
                "start": fmt_time(ci_start),
                "end": fmt_time(normalise(ci_end)) if ci_end else "",
                "hours": round(float(c.get("totalHours", 0) or 0), 2),
                "cost": round(float(c.get("totalCost", 0) or 0), 2),
            }
            if breaks:
                row["breaks"] = breaks
            if is_leave(c):
                # Loaded renders a leave day as 7:00 AM-3:00 PM; the times are
                # synthetic, so keep the row but never count it as worked.
                leave_rows.append(row)
            else:
                clockin_rows.append(row)

        rostered_rows.sort(key=lambda x: x["_sort"])
        clockin_rows.sort(key=lambda x: x["_sort"])
        leave_rows.sort(key=lambda x: x["_sort"])
        for r in rostered_rows:
            r.pop("_sort", None)
        for r in clockin_rows:
            r.pop("_sort", None)
        for r in leave_rows:
            r.pop("_sort", None)

        totals = {
            "rostered_hours": round(sum(r["hours"] for r in rostered_rows), 2),
            "rostered_cost": round(sum(r["cost"] for r in rostered_rows), 2),
            "actual_hours": round(sum(r["hours"] for r in clockin_rows), 2),
            "actual_cost": round(sum(r["cost"] for r in clockin_rows), 2),
        }
        totals["variance"] = round(totals["actual_hours"] - totals["rostered_hours"], 2)
        if leave_rows:
            totals["leave_hours"] = round(sum(r["hours"] for r in leave_rows), 2)
            totals["leave_cost"] = round(sum(r["cost"] for r in leave_rows), 2)

        log(
            "Result: "
            + str(len(rostered_rows))
            + " rostered shifts, "
            + str(len(clockin_rows))
            + " clockins, "
            + str(len(leave_rows))
            + " leave"
        )
        return {
            "window": window_out,
            "rostered": rostered_rows,
            "clockins": clockin_rows,
            "leave": leave_rows,
            "totals": totals,
        }

    # ── staff + day views: collect flat shifts for aggregation ─────────────────
    shifts = []

    for roster in rosters:
        for shift in roster.get("rosteredShifts", []):
            shift_start = shift.get("clockinTime", "")
            if not shift_start or not in_range(shift_start):
                continue
            if shift.get("datestampDeleted"):
                continue
            first = shift.get("staffMemberFirstName", "")
            last = shift.get("staffMemberLastName", "")
            if not name_matches(first, last, staff_name):
                continue
            shifts.append(
                {
                    "kind": "rostered",
                    "staff_id": str(shift.get("staffMemberId", "")),
                    "name": ((first or "") + " " + (last or "")).strip(),
                    "role": shift.get("roleName", ""),
                    "day": normalise(shift_start)[:10],
                    "hours": round(float(shift.get("totalHours", 0) or 0), 2),
                    "cost": round(float(shift.get("totalCost", 0) or 0), 2),
                }
            )

    for c in clockins:
        ci_start = c.get("clockinTime", "")
        if not ci_start or not in_range(ci_start):
            continue
        first = c.get("staffMemberFirstName", "")
        last = c.get("staffMemberLastName", "")
        if not name_matches(first, last, staff_name):
            continue
        shifts.append(
            {
                "kind": "leave" if is_leave(c) else "actual",
                "staff_id": str(c.get("staffMemberId", "")),
                "name": ((first or "") + " " + (last or "")).strip(),
                "role": c.get("roleName", ""),
                "day": normalise(ci_start)[:10],
                "hours": round(float(c.get("totalHours", 0) or 0), 2),
                "cost": round(float(c.get("totalCost", 0) or 0), 2),
            }
        )

    log("Processed " + str(len(shifts)) + " shift records")

    if group_by == "staff":
        staff_agg = {}
        for s in shifts:
            key = s["name"] or s["staff_id"]
            if key not in staff_agg:
                staff_agg[key] = {
                    "name": s["name"],
                    "role": s["role"],
                    "rostered_hours": 0.0,
                    "rostered_cost": 0.0,
                    "actual_hours": 0.0,
                    "actual_cost": 0.0,
                }
            a = staff_agg[key]
            if s["kind"] == "rostered":
                a["rostered_hours"] = round(a["rostered_hours"] + s["hours"], 2)
                a["rostered_cost"] = round(a["rostered_cost"] + s["cost"], 2)
            elif s["kind"] == "leave":
                a["leave_hours"] = round(a.get("leave_hours", 0.0) + s["hours"], 2)
                a["leave_cost"] = round(a.get("leave_cost", 0.0) + s["cost"], 2)
            else:
                a["actual_hours"] = round(a["actual_hours"] + s["hours"], 2)
                a["actual_cost"] = round(a["actual_cost"] + s["cost"], 2)
        for a in staff_agg.values():
            a["variance"] = round(a["actual_hours"] - a["rostered_hours"], 2)
        rows = sorted(staff_agg.values(), key=lambda x: x["actual_hours"], reverse=True)

    elif group_by == "day":
        day_agg = {}
        for s in shifts:
            day = s["day"]
            if day not in day_agg:
                try:
                    d = datetime.date.fromisoformat(day)
                    label = fmt_date(d)
                except ValueError:
                    label = day
                day_agg[day] = {
                    "date": label,
                    "_sort": day,
                    "rostered_hours": 0.0,
                    "rostered_cost": 0.0,
                    "actual_hours": 0.0,
                    "actual_cost": 0.0,
                    "unrostered": 0,
                }
            a = day_agg[day]
            if s["kind"] == "rostered":
                a["rostered_hours"] = round(a["rostered_hours"] + s["hours"], 2)
                a["rostered_cost"] = round(a["rostered_cost"] + s["cost"], 2)
            elif s["kind"] == "leave":
                a["leave_hours"] = round(a.get("leave_hours", 0.0) + s["hours"], 2)
                a["leave_cost"] = round(a.get("leave_cost", 0.0) + s["cost"], 2)
            else:
                a["actual_hours"] = round(a["actual_hours"] + s["hours"], 2)
                a["actual_cost"] = round(a["actual_cost"] + s["cost"], 2)
        rostered_staff_days = set(
            (s["staff_id"], s["day"]) for s in shifts if s["kind"] == "rostered"
        )
        # Leave never counts as an unrostered clock-in (kind "leave" is excluded).
        for s in shifts:
            if (
                s["kind"] == "actual"
                and (s["staff_id"], s["day"]) not in rostered_staff_days
            ):
                day_agg[s["day"]]["unrostered"] = day_agg[s["day"]]["unrostered"] + 1
        for a in day_agg.values():
            a["variance"] = round(a["actual_hours"] - a["rostered_hours"], 2)
        rows = sorted(day_agg.values(), key=lambda x: x["_sort"])
        for r in rows:
            r.pop("_sort", None)

    else:
        rows = []

    totals = {
        "rostered_hours": round(
            sum(s["hours"] for s in shifts if s["kind"] == "rostered"), 2
        ),
        "rostered_cost": round(
            sum(s["cost"] for s in shifts if s["kind"] == "rostered"), 2
        ),
        "actual_hours": round(
            sum(s["hours"] for s in shifts if s["kind"] == "actual"), 2
        ),
        "actual_cost": round(
            sum(s["cost"] for s in shifts if s["kind"] == "actual"), 2
        ),
    }
    totals["variance"] = round(totals["actual_hours"] - totals["rostered_hours"], 2)
    leave_hours = round(sum(s["hours"] for s in shifts if s["kind"] == "leave"), 2)
    if leave_hours:
        totals["leave_hours"] = leave_hours
        totals["leave_cost"] = round(
            sum(s["cost"] for s in shifts if s["kind"] == "leave"), 2
        )

    staff_count = len(set(s["name"] for s in shifts if s["name"]))
    log(
        "Result: "
        + str(len(rows))
        + " rows, "
        + str(staff_count)
        + " staff, grouped by "
        + group_by
    )

    return {
        "window": window_out,
        "rows": rows,
        "totals": totals,
        "summary": str(staff_count) + " staff, grouped by " + group_by,
    }
