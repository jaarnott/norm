# ruff: noqa: F821 — `datetime` and `json` are injected into the sandbox
# namespace by app/connectors/function_executor.py; they are not imports.
#
# Canonical function_code for `loadedhub.get_labour` — THE labour domain
# tool (installed by scripts/sync_labour_config.py).
#
# One tool answers every read-side labour question. It absorbs five tools:
# get_roster_for_period, get_roster_vs_actual_for_period,
# get_timeclock_entries_for_period, get_staff_attendance (its engine lives
# on as the default view, leave-split doctrine intact) and
# get_staff_members. Views:
#
#   - attendance (DEFAULT): rostered vs actual hours/cost with booked
#     leave split out — Loaded returns leave as pseudo clock-ins and
#     counting it as worked time invents ghost shifts (prod thread
#     51a90809). group_by staff | day | detail; staff_name filter.
#     venues accepts a list or 'all' for per-venue totals in one call.
#   - roster: the RAW roster payload in the standard {window, data}
#     envelope — passthrough, no summarising. The roster card and the
#     claude.ai roster artifact parse this exact shape, and the shift
#     write path needs every field (ids, rates, clock times) intact.
#   - vs_actual: Loaded's own Roster vs actual report — rostered vs
#     actual cost/hours per bucket (interval, default daily). venues
#     accepts a list or 'all' (one report call per venue, Oct 2026), with
#     group_by 'total' (default) or 'day' for each venue's days.
#   - timeclock: the clock-in entries with a summary; the three required
#     boolean flags ride as strings because the executor's required-field
#     check is falsy-based (a real False reads as "missing").
#   - staff: the staff reference list, slimmed.
#
# include ('actual' | 'rostered' | 'both', default both) — on attendance
# it skips the fetch that isn't needed (the roster, for a worked-hours
# question); on vs_actual, whose report carries both, it trims the columns.
#
# The period arrives in plain English and resolves through Norm's
# venue-aware trading calendar; explicit start/end stay the exact-times
# path behind the trading-day confirmation gate.
#
# Requires consolidator_config: {"max_api_calls": 20}
# (venues='all' fans out 2 calls per venue — roster + timeclock — plus
# list_venues and resolve_dates: 14 for six venues. 12 failed every
# group-wide attendance question with "Too many API calls" — found 29 Sep
# 2026. 20 leaves room for nine venues.)

_CONSUMED = (
    "period",
    "start",
    "end",
    "start_datetime",
    "end_datetime",
    "confirmed_by_user",
    "venue_id",
    "mode",
    "view",
    "venues",
    "staff_name",
    "group_by",
    "interval",
    "include",
)

_DOW_NAMES = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
_MONTH_SHORT = {
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

_GROUP_BY = ("staff", "day", "detail")

_TIMECLOCK_FLAGS = {
    "include_inactive": "false",
    "include_only_clockins": "false",
    "should_truncate_shifts": "true",
}


#: What the caller wants back. Synonyms a model reaches for map onto the three.
_INCLUDE = {
    "both": "both",
    "all": "both",
    "actual": "actual",
    "actuals": "actual",
    "worked": "actual",
    "labour": "actual",
    "rostered": "rostered",
    "roster": "rostered",
}

_ROSTERED_KEYS = ("rostered_hours", "rostered_cost")
_ACTUAL_KEYS = ("actual_hours", "actual_cost", "leave_hours", "leave_cost")
#: Only meaningful with both sides.
_BOTH_KEYS = ("variance", "cost_variance", "unrostered")


def _trim(row, include):
    """Drop the side the caller didn't ask for, and the comparisons that need
    both — a zero rostered_hours on an actual-only answer would read as
    'nobody was rostered'."""
    if include == "both" or not isinstance(row, dict):
        return row
    drop = set(_BOTH_KEYS) | set(
        _ACTUAL_KEYS if include == "rostered" else _ROSTERED_KEYS
    )
    return {k: v for k, v in row.items() if k not in drop}


def _rows_of(payload):
    if isinstance(payload, list):
        return payload if payload and isinstance(payload[0], dict) else None
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value
    return None


def _summarise(payload):
    """Row count and column sums (the for_period wrapper's summary).

    Accumulate raw and round once — rounding every addition drifts by
    pennies over a hundred rows (caught by the golden verification)."""
    rows = _rows_of(payload)
    if not rows:
        return None
    sums = {}
    for row in rows:
        for key, value in row.items():
            if value is True or value is False:
                continue
            if isinstance(value, int) or isinstance(value, float):
                sums[key] = sums.get(key, 0) + value
    sums = {k: round(v, 2) for k, v in sums.items()}
    summary = {"row_count": len(rows)}
    if sums:
        summary["column_sums"] = sums
        summary["_note"] = (
            "column_sums adds up every numeric column. Sums of rates or unit "
            "prices are not meaningful — use only the columns that are."
        )
    return summary


def _resolve_window(params, call_api, log):
    """Returns (window, error_result). Exactly one is None."""
    period = (params.get("period") or "").strip()
    start = params.get("start") or params.get("start_datetime")
    end = params.get("end") or params.get("end_datetime")
    if not period and not (start and end):
        return None, {
            "error": (
                "Give a period in plain English (e.g. 'yesterday', 'last week'). "
                "Only pass start and end if the user asked for specific clock times."
            )
        }
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
        return None, {
            "error": "Could not resolve the period: " + str(resolved["error"])
        }
    window = resolved.get("window") if isinstance(resolved, dict) else None
    if not isinstance(window, dict):
        data = resolved.get("data") if isinstance(resolved, dict) else None
        window = data.get("window") if isinstance(data, dict) else None
    if not isinstance(window, dict):
        return None, {
            "error": (
                "Could not resolve '" + (period or "that range") + "' to a date "
                "range. Try a simpler period such as 'yesterday' or 'last week'."
            )
        }
    if not window.get("trading_aligned") and not params.get("confirmed_by_user"):
        log("explicit window is not a trading day; asking before fetching")
        return None, {
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
        }
    return window, None


def _resolve_venues(params, call_api):
    """Returns (venue_names, error_result) — attendance's group path."""
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


# ── shared time helpers (lifted from the staff_attendance engine) ─────────


def _normalise(dt_str):
    if not dt_str:
        return ""
    return str(dt_str).replace("%2B", "+")[:19]


def _extract_offset_mins(dt_str):
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


def _to_local_norm(dt_str, local_offset_mins):
    n = _normalise(dt_str)
    if not n or len(n) < 19:
        return n
    src_offset = _extract_offset_mins(dt_str)
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


def _fmt_date(d):
    return (
        _DOW_NAMES.get(d.weekday(), "")
        + " "
        + str(d.day)
        + " "
        + _MONTH_SHORT.get(d.month, "")
    )


def _fmt_time(dt_str):
    if not dt_str or len(dt_str) < 16:
        return ""
    h = int(dt_str[11:13])
    m = dt_str[14:16]
    ampm = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return str(h12) + ":" + m + " " + ampm


def _fmt_breaks(raw_breaks, local_offset_mins):
    result = []
    for b in raw_breaks or []:
        if b.get("deletedAt"):
            continue
        bs = _to_local_norm(b.get("breakStart", ""), local_offset_mins)
        be = _to_local_norm(b.get("breakEnd", ""), local_offset_mins)
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
                "start": _fmt_time(bs),
                "end": _fmt_time(be),
                "duration_mins": dur_mins,
                "paid": bool(b.get("paid", False)),
            }
        )
    return result


def _name_matches(first, last, query):
    if not query:
        return True
    q = query.lower().strip()
    full = ((first or "") + " " + (last or "")).lower().strip()
    return q in full or q in (first or "").lower() or q in (last or "").lower()


def _is_leave(entry):
    """Loaded returns booked leave as a pseudo clock-in with type 'Leave'.

    Leave must never count as worked time; anything else (only 'Clockin'
    observed live) stays an actual entry so unknown future types aren't
    silently dropped."""
    return str(entry.get("type") or "") == "Leave"


def _pair_calls(venue, start, end, include):
    """The (roster, timeclock) calls a venue needs — only the side(s) asked
    for. Returns (calls, sides) where sides names each call in order."""
    venue_params = {"venue": venue} if venue else {}
    calls, sides = [], []
    if include != "actual":
        calls.append(
            (
                "loadedhub",
                "get_roster",
                {"start_datetime": start, "end_datetime": end, **venue_params},
            )
        )
        sides.append("roster")
    if include != "rostered":
        calls.append(
            (
                "loadedhub",
                "get_timeclock_entries",
                {
                    "start_time": start,
                    "end_time": end,
                    **_TIMECLOCK_FLAGS,
                    **venue_params,
                },
            )
        )
        sides.append("timeclock")
    return calls, sides


def _fetch_pair(venue, start, end, call_api, call_api_parallel, include="both"):
    """One venue's [roster, timeclock] — a side not asked for is []."""
    calls, sides = _pair_calls(venue, start, end, include)
    results = (
        call_api_parallel(calls)
        if call_api_parallel
        else [call_api(c, a, p) for (c, a, p) in calls]
    )
    got = dict(zip(sides, results))
    return [got.get("roster", []), got.get("timeclock", [])]


def _as_list(payload):
    if isinstance(payload, list):
        return payload
    return [payload] if payload else []


# ── view: attendance (the staff_attendance engine, leave split intact) ────


def _attendance(window, venue, params, call_api, call_api_parallel, log):
    start = window["start"]
    end = window["end"]
    staff_name = params.get("staff_name", "")
    group_by = params.get("group_by", "staff")
    include = params.get("include", "both")
    window_out = {
        "start": start,
        "end": end,
        "description": window.get("description"),
    }

    log(
        "Fetching "
        + {"both": "roster + timeclock", "actual": "timeclock", "rostered": "roster"}[
            include
        ]
        + " for "
        + venue
        + " ("
        + str(start)[:10]
        + " to "
        + str(end)[:10]
        + ")"
    )
    results = _fetch_pair(venue, start, end, call_api, call_api_parallel, include)
    for payload in results:
        if isinstance(payload, dict) and payload.get("error"):
            return {
                "view": "attendance",
                "window": window_out,
                "error": str(payload["error"]),
            }
    rosters = _as_list(results[0])
    clockins = _as_list(results[1])

    req_start = _normalise(start)
    req_end = _normalise(end)

    def in_range(dt_str):
        n = _normalise(dt_str)
        return n and req_start <= n <= req_end

    # ── detail view: separate sorted lists (worked clock-ins vs leave) ───
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
                if not _name_matches(first, last, staff_name):
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
                shift_start_n = _normalise(shift_start)
                local_off = _extract_offset_mins(shift_start)
                try:
                    d = datetime.date.fromisoformat(shift_start_n[:10])
                    date_label = _fmt_date(d)
                except ValueError:
                    date_label = shift_start_n[:10]
                breaks = _fmt_breaks(shift.get("breaks", []), local_off)
                row = {
                    "_sort": shift_start_n,
                    "date": date_label,
                    "name": ((first or "") + " " + (last or "")).strip(),
                    "role": shift.get("roleName", ""),
                    "job": job,
                    "start": _fmt_time(shift_start),
                    "end": _fmt_time(_normalise(shift.get("clockoutTime", ""))),
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
            if not _name_matches(first, last, staff_name):
                continue
            ci_start_n = _normalise(ci_start)
            ci_end = c.get("clockoutTime", "")
            local_off = _extract_offset_mins(ci_start)
            try:
                d = datetime.date.fromisoformat(ci_start_n[:10])
                date_label = _fmt_date(d)
            except ValueError:
                date_label = ci_start_n[:10]
            breaks = _fmt_breaks(c.get("breaks", []), local_off)
            row = {
                "_sort": ci_start_n,
                "date": date_label,
                "name": ((first or "") + " " + (last or "")).strip(),
                "role": c.get("roleName", ""),
                "start": _fmt_time(ci_start),
                "end": _fmt_time(_normalise(ci_end)) if ci_end else "",
                "hours": round(float(c.get("totalHours", 0) or 0), 2),
                "cost": round(float(c.get("totalCost", 0) or 0), 2),
            }
            if breaks:
                row["breaks"] = breaks
            if _is_leave(c):
                # Loaded renders a leave day as 7:00 AM-3:00 PM; the times
                # are synthetic, so keep the row but never count it as worked.
                leave_rows.append(row)
            else:
                clockin_rows.append(row)

        rostered_rows.sort(key=lambda x: x["_sort"])
        clockin_rows.sort(key=lambda x: x["_sort"])
        leave_rows.sort(key=lambda x: x["_sort"])
        for rows in (rostered_rows, clockin_rows, leave_rows):
            for r in rows:
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
        out = {
            "view": "attendance",
            "window": window_out,
            "rostered": rostered_rows,
            "clockins": clockin_rows,
            "leave": leave_rows,
            "totals": _trim(totals, include),
        }
        if include == "actual":
            out.pop("rostered")
        elif include == "rostered":
            out.pop("clockins")
            out.pop("leave")
        if include != "both":
            out["include"] = include
        return out

    # ── staff + day views: collect flat shifts for aggregation ───────────
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
            if not _name_matches(first, last, staff_name):
                continue
            shifts.append(
                {
                    "kind": "rostered",
                    "staff_id": str(shift.get("staffMemberId", "")),
                    "name": ((first or "") + " " + (last or "")).strip(),
                    "role": shift.get("roleName", ""),
                    "day": _normalise(shift_start)[:10],
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
        if not _name_matches(first, last, staff_name):
            continue
        shifts.append(
            {
                "kind": "leave" if _is_leave(c) else "actual",
                "staff_id": str(c.get("staffMemberId", "")),
                "name": ((first or "") + " " + (last or "")).strip(),
                "role": c.get("roleName", ""),
                "day": _normalise(ci_start)[:10],
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
                    label = _fmt_date(d)
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
        # Leave never counts as an unrostered clock-in.
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
    out = {
        "view": "attendance",
        "window": window_out,
        "rows": [_trim(r, include) for r in rows],
        "totals": _trim(totals, include),
        "summary": str(staff_count) + " staff, grouped by " + group_by,
    }
    if include != "both":
        out["include"] = include
    return out


def _attendance_group(
    window,
    venues,
    staff_name,
    call_api,
    call_api_parallel,
    log,
    include="both",
    group_by="staff",
):
    """Per-venue attendance totals in one call (venues list or 'all').

    staff_name narrows every venue's totals to that person — it used to be
    dropped on this path, so a one-person question across venues answered
    for everyone (consolidator review, 1 Oct 2026). group_by 'day' adds each
    venue's days; it used to be ignored here, so a day-by-day question across
    venues came back as totals (Oct 2026). include skips the side not asked
    for — 'actual' needs only the clock-ins, half the Loaded calls."""
    start, end = window["start"], window["end"]
    req_start, req_end = _normalise(start), _normalise(end)
    calls, owners = [], []
    for v in venues:
        vcalls, vsides = _pair_calls(v, start, end, include)
        calls.extend(vcalls)
        owners.extend((v, side) for side in vsides)
    log(
        "Fanning out "
        + str(len(calls))
        + " calls over "
        + str(len(venues))
        + " venue(s)"
    )
    results = (
        call_api_parallel(calls)
        if call_api_parallel
        else [call_api(c, a, p) for (c, a, p) in calls]
    )
    by_venue = {v: {"roster": [], "timeclock": []} for v in venues}
    for (v, side), payload in zip(owners, results):
        by_venue[v][side] = payload

    def in_range(dt_str):
        n = _normalise(dt_str)
        return n and req_start <= n <= req_end

    def _day_row(day):
        try:
            label = _fmt_date(datetime.date.fromisoformat(day))
        except ValueError:
            label = day
        return {
            "date": label,
            "_sort": day,
            "rostered_hours": 0.0,
            "rostered_cost": 0.0,
            "actual_hours": 0.0,
            "actual_cost": 0.0,
        }

    rows = []
    for v in venues:
        roster_data = by_venue[v]["roster"]
        clockin_data = by_venue[v]["timeclock"]
        row = {"venue": v}
        errs = []
        for payload in (roster_data, clockin_data):
            if isinstance(payload, dict) and payload.get("error"):
                errs.append(str(payload["error"]))
        if errs:
            row["errors"] = errs
            rows.append(row)
            continue
        r_h = r_c = a_h = a_c = l_h = l_c = 0.0
        days = {}
        for roster in _as_list(roster_data):
            for shift in roster.get("rosteredShifts", []):
                st = shift.get("clockinTime", "")
                if not st or not in_range(st) or shift.get("datestampDeleted"):
                    continue
                if not _name_matches(
                    shift.get("staffMemberFirstName", ""),
                    shift.get("staffMemberLastName", ""),
                    staff_name,
                ):
                    continue
                h = float(shift.get("totalHours", 0) or 0)
                c = float(shift.get("totalCost", 0) or 0)
                r_h += h
                r_c += c
                d = days.setdefault(_normalise(st)[:10], _day_row(_normalise(st)[:10]))
                d["rostered_hours"] += h
                d["rostered_cost"] += c
        for c in _as_list(clockin_data):
            st = c.get("clockinTime", "")
            if not st or not in_range(st):
                continue
            if not _name_matches(
                c.get("staffMemberFirstName", ""),
                c.get("staffMemberLastName", ""),
                staff_name,
            ):
                continue
            h = float(c.get("totalHours", 0) or 0)
            cost = float(c.get("totalCost", 0) or 0)
            d = days.setdefault(_normalise(st)[:10], _day_row(_normalise(st)[:10]))
            if _is_leave(c):
                l_h += h
                l_c += cost
                d["leave_hours"] = d.get("leave_hours", 0.0) + h
                d["leave_cost"] = d.get("leave_cost", 0.0) + cost
            else:
                a_h += h
                a_c += cost
                d["actual_hours"] += h
                d["actual_cost"] += cost
        row["rostered_hours"] = round(r_h, 2)
        row["rostered_cost"] = round(r_c, 2)
        row["actual_hours"] = round(a_h, 2)
        row["actual_cost"] = round(a_c, 2)
        row["variance"] = round(row["actual_hours"] - row["rostered_hours"], 2)
        if l_h:
            row["leave_hours"] = round(l_h, 2)
            row["leave_cost"] = round(l_c, 2)
        row = _trim(row, include)
        if group_by == "day":
            day_rows = []
            for d in sorted(days.values(), key=lambda x: x["_sort"]):
                d.pop("_sort", None)
                for k in list(d):
                    if isinstance(d[k], float):
                        d[k] = round(d[k], 2)
                d["variance"] = round(d["actual_hours"] - d["rostered_hours"], 2)
                day_rows.append(_trim(d, include))
            row["days"] = day_rows
        rows.append(row)

    def _total(key):
        vals = [r.get(key) for r in rows if isinstance(r.get(key), (int, float))]
        return round(sum(vals), 2) if vals else 0.0

    totals = {
        k: _total(k)
        for k in ("rostered_hours", "rostered_cost", "actual_hours", "actual_cost")
    }
    totals["variance"] = round(totals["actual_hours"] - totals["rostered_hours"], 2)
    # Leave is reported beside worked time, never inside it — the per-venue
    # rows carried it and the group total quietly didn't.
    if any(r.get("leave_hours") for r in rows):
        totals["leave_hours"] = _total("leave_hours")
        totals["leave_cost"] = _total("leave_cost")
    result = {
        "view": "attendance",
        "window": {
            "start": start,
            "end": end,
            "description": window.get("description"),
        },
        "rows": rows,
        "totals": _trim(totals, include),
    }
    if include != "both":
        result["include"] = include
    if group_by not in ("day", "staff"):
        result["group_by_note"] = (
            "group_by '" + group_by + "' is per venue — ask one venue for it"
        )
    if staff_name:
        result["staff_name"] = staff_name
    skipped = [r["venue"] for r in rows if r.get("errors")]
    if skipped:
        result["note"] = (
            "some venues had errors and are excluded from totals: " + ", ".join(skipped)
        )
    return result


# ── other views ───────────────────────────────────────────────────────────


def _roster(window, params, call_api, log):
    """RAW roster passthrough in the standard envelope.

    The roster card and the claude.ai artifact parse this exact connector
    shape (unwrapping the envelope by structure), and the shift write path
    needs every field intact — so no summarising, ever. staff_name only
    narrows rosteredShifts; roster-level fields (id, venueId) survive."""
    args = {"start_datetime": window["start"], "end_datetime": window["end"]}
    if params.get("venue"):
        args["venue"] = params["venue"]
    data = call_api("loadedhub", "get_roster", args)
    if isinstance(data, dict) and data.get("error"):
        return {"view": "roster", "window": window, "error": str(data["error"])}
    staff_name = (params.get("staff_name") or "").strip()
    if staff_name:
        filtered = []
        for roster in _as_list(data):
            if not isinstance(roster, dict):
                continue
            r = dict(roster)
            r["rosteredShifts"] = [
                s
                for s in roster.get("rosteredShifts", [])
                if _name_matches(
                    s.get("staffMemberFirstName", ""),
                    s.get("staffMemberLastName", ""),
                    staff_name,
                )
            ]
            filtered.append(r)
        data = filtered
    return {"view": "roster", "window": window, "data": data}


_VS_ACTUAL_ROSTERED = ("rosteredHours", "rosteredCost")
_VS_ACTUAL_ACTUAL = ("actualHours", "actualCost")


def _vs_actual(window, params, call_api, log):
    """One venue's report, passed through as Loaded returns it."""
    include = params.get("include", "both")
    args = {
        "start": window["start"],
        "end": window["end"],
        "interval": params.get("interval") or "1.00:00:00",
    }
    if params.get("venue"):
        args["venue"] = params["venue"]
    data = call_api("loadedhub", "get_roster_vs_actual", args)
    if isinstance(data, dict) and data.get("error"):
        return {"view": "vs_actual", "window": window, "error": str(data["error"])}
    if include != "both" and isinstance(data, list):
        drop = _VS_ACTUAL_ACTUAL if include == "rostered" else _VS_ACTUAL_ROSTERED
        data = [
            {k: v for k, v in r.items() if k not in drop} if isinstance(r, dict) else r
            for r in data
        ]
    result = {"view": "vs_actual", "window": window}
    summary = _summarise(data)
    if summary:
        result["summary"] = summary
    if include != "both":
        result["include"] = include
    result["data"] = data
    return result


def _vs_actual_group(window, venues, params, call_api, call_api_parallel, log):
    """Loaded's Roster vs actual report for every venue in one call — one
    report per venue, fanned out. Before Oct 2026 this view read one venue per
    call, so a group wage question took a refused call and then one call per
    venue (prod thread ba8653f6: five)."""
    include = params.get("include", "both")
    split = params.get("group_by") == "day"
    interval = params.get("interval") or "1.00:00:00"
    calls = [
        (
            "loadedhub",
            "get_roster_vs_actual",
            {
                "start": window["start"],
                "end": window["end"],
                "interval": interval,
                "venue": v,
            },
        )
        for v in venues
    ]
    log("Fanning out " + str(len(calls)) + " roster-vs-actual report(s)")
    results = (
        call_api_parallel(calls)
        if call_api_parallel
        else [call_api(c, a, p) for (c, a, p) in calls]
    )

    def _num(row, key):
        try:
            return float(row.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    def _figures(r_h, r_c, a_h, a_c):
        return {
            "rostered_hours": round(r_h, 2),
            "rostered_cost": round(r_c, 2),
            "actual_hours": round(a_h, 2),
            "actual_cost": round(a_c, 2),
            "variance": round(a_h - r_h, 2),
            "cost_variance": round(a_c - r_c, 2),
        }

    rows = []
    for v, data in zip(venues, results):
        if isinstance(data, dict) and data.get("error"):
            rows.append({"venue": v, "errors": [str(data["error"])]})
            continue
        r_h = r_c = a_h = a_c = 0.0
        days = []
        for b in _rows_of(data) or []:
            br_h, br_c = _num(b, "rosteredHours"), _num(b, "rosteredCost")
            ba_h, ba_c = _num(b, "actualHours"), _num(b, "actualCost")
            r_h += br_h
            r_c += br_c
            a_h += ba_h
            a_c += ba_c
            if split:
                st = _normalise(b.get("startTime") or "")
                try:
                    label = _fmt_date(datetime.date.fromisoformat(st[:10]))
                except ValueError:
                    label = st[:10]
                days.append(
                    _trim({"date": label, **_figures(br_h, br_c, ba_h, ba_c)}, include)
                )
        row = {"venue": v, **_trim(_figures(r_h, r_c, a_h, a_c), include)}
        if split:
            row["days"] = days
        rows.append(row)

    good = [r for r in rows if not r.get("errors")]

    def _total(key):
        return round(sum(float(r.get(key) or 0) for r in good), 2)

    totals = _figures(
        _total("rostered_hours"),
        _total("rostered_cost"),
        _total("actual_hours"),
        _total("actual_cost"),
    )
    result = {
        "view": "vs_actual",
        "window": {
            "start": window["start"],
            "end": window["end"],
            "description": window.get("description"),
        },
        "rows": rows,
        "totals": _trim(totals, include),
    }
    if include != "both":
        result["include"] = include
    skipped = [r["venue"] for r in rows if r.get("errors")]
    if skipped:
        result["note"] = (
            "some venues had errors and are excluded from totals: " + ", ".join(skipped)
        )
    return result


def _timeclock(window, params, call_api, log):
    args = {
        "start_time": window["start"],
        "end_time": window["end"],
        **_TIMECLOCK_FLAGS,
    }
    if params.get("venue"):
        args["venue"] = params["venue"]
    staff_name = (params.get("staff_name") or "").strip()
    data = call_api("loadedhub", "get_timeclock_entries", args)
    if isinstance(data, dict) and data.get("error"):
        return {"view": "timeclock", "window": window, "error": str(data["error"])}
    if staff_name and isinstance(data, list):
        data = [
            c
            for c in data
            if _name_matches(
                c.get("staffMemberFirstName", ""),
                c.get("staffMemberLastName", ""),
                staff_name,
            )
        ]
    result = {"view": "timeclock", "window": window}
    summary = _summarise(data)
    if summary:
        # Loaded returns booked leave as pseudo clock-ins: column_sums adds
        # them in (294.75 leave hours inside one month's 1421.2 "hours" at
        # DSC). Worked and leave are split out so neither is misread.
        worked = [c for c in _as_list(data) if not _is_leave(c)]
        leave = [c for c in _as_list(data) if _is_leave(c)]

        def _sum(rows, key):
            return round(sum(float(c.get(key, 0) or 0) for c in rows), 2)

        summary["worked_hours"] = _sum(worked, "totalHours")
        summary["worked_cost"] = _sum(worked, "totalCost")
        summary["leave_hours"] = _sum(leave, "totalHours")
        summary["leave_cost"] = _sum(leave, "totalCost")
        summary["leave_entries"] = len(leave)
        summary["_note"] = (
            "column_sums adds up every numeric column INCLUDING leave entries "
            "(type 'Leave'). Use worked_hours / leave_hours for time worked "
            "and leave taken."
        )
        result["summary"] = summary
    result["data"] = data
    return result


def _staff(params, call_api, log):
    """The staff reference list, slimmed to what an agent needs."""
    args = {
        # Strings, not booleans — the executor's required check is falsy-based.
        "include_deleted": "false",
        "include_last_clocks": "false",
    }
    if params.get("venue"):
        args["venue"] = params["venue"]
    data = call_api("loadedhub", "get_staff_members", args)
    if isinstance(data, dict) and data.get("error"):
        return {"view": "staff", "error": str(data["error"])}
    staff_name = (params.get("staff_name") or "").strip()
    rows = []
    for s in _rows_of(data) or []:
        name = str(s.get("name") or "").strip()
        if staff_name and staff_name.lower() not in name.lower():
            continue
        row = {"id": s.get("id"), "name": name}
        for src, dst in (
            ("defaultMemberRoleRoleName", "role"),
            ("defaultMemberRoleHourlyRate", "hourly_rate"),
            ("salaryRate", "salary_rate"),
            ("email", "email"),
            ("phoneMobile", "phone"),
        ):
            v = s.get(src)
            if v not in (None, ""):
                row[dst] = v
        rows.append(row)
    return {"view": "staff", "rows": rows, "count": len(rows)}


# ── entry point ───────────────────────────────────────────────────────────

_VIEWS = ("attendance", "roster", "vs_actual", "timeclock", "staff")


def run(params, call_api, log, call_api_parallel=None):
    view = str(params.get("view") or "attendance").strip().lower()
    if view not in _VIEWS:
        return {
            "error": (
                "unknown view '" + view + "' — use attendance, roster, "
                "vs_actual, timeclock, or staff"
            )
        }

    if view == "staff":
        # A reference list — no window needed.
        return _staff(params, call_api, log)

    include = _INCLUDE.get(str(params.get("include") or "both").strip().lower())
    if include is None:
        return {
            "error": (
                "unknown include '"
                + str(params.get("include"))
                + "' — use actual, rostered or both"
            )
        }
    params = {**params, "include": include}

    window, err = _resolve_window(params, call_api, log)
    if err:
        return err

    if view == "attendance":
        group_by = str(params.get("group_by") or "staff").strip().lower()
        if group_by not in _GROUP_BY:
            return {
                "window": window,
                "error": (
                    "unknown group_by '" + group_by + "' — use staff, day or detail"
                ),
            }
        params = {**params, "group_by": group_by}
        if params.get("venues"):
            venues, verr = _resolve_venues(params, call_api)
            if verr:
                verr["window"] = window
                return verr
            if len(venues) > 1:
                return _attendance_group(
                    window,
                    venues,
                    str(params.get("staff_name") or "").strip(),
                    call_api,
                    call_api_parallel,
                    log,
                    include=include,
                    group_by=group_by,
                )
            merged = dict(params)
            merged["venue"] = venues[0]
            return _attendance(
                window, venues[0], merged, call_api, call_api_parallel, log
            )
        return _attendance(
            window,
            str(params.get("venue") or ""),
            params,
            call_api,
            call_api_parallel,
            log,
        )

    if view == "vs_actual":
        if (params.get("staff_name") or "").strip():
            return {
                "view": "vs_actual",
                "window": window,
                "error": (
                    "vs_actual is the venue's totals per day — it can't be narrowed "
                    "to one person. Use view 'attendance' with staff_name instead."
                ),
            }
        group_by = str(params.get("group_by") or "total").strip().lower()
        if group_by not in ("total", "day"):
            return {
                "window": window,
                "error": "vs_actual group_by is 'total' or 'day'",
            }
        params = {**params, "group_by": group_by}
        if params.get("venues"):
            venues, verr = _resolve_venues(params, call_api)
            if verr:
                verr["window"] = window
                return verr
            return _vs_actual_group(
                window, venues, params, call_api, call_api_parallel, log
            )
        return _vs_actual(window, params, call_api, log)

    if params.get("venues"):
        return {
            "window": window,
            "error": (
                "venues is for view 'attendance' or 'vs_actual' — roster and "
                "timeclock read one venue per call"
            ),
        }
    if view == "roster":
        return _roster(window, params, call_api, log)
    return _timeclock(window, params, call_api, log)
