# ruff: noqa: F821 — `datetime` and `json` are injected into the sandbox
# namespace by app/connectors/function_executor.py; they are not imports.
#
# Canonical function_code for `loadedhub.get_payrun` — THE one shaping of a
# pay week (installed by scripts/sync_payroll_config.py; plan
# docs/plans/finance-migration.md §7, §9). The Monday snapshot job stores its
# `rows` as pay_shift_facts; the Payroll page and chat read the same answer,
# so there is one implementation of "what does this entity pay this week".
#
# What it does, and why each rule is here:
#   - the pay week runs Monday → Monday at the venue's day start (Norm's
#     trading calendar, through norm.resolve_dates — never +7 days by hand,
#     which breaks across the daylight-saving change);
#   - the clock-ins come from get_payrun_clockins_raw: Loaded's own
#     filterByPayrollVenue attribution (who THIS company pays, including
#     shifts worked elsewhere), every shift type, deleted and open rows
#     included so they can be counted — then DROPPED: a deleted row is not
#     payable and an open clock-in has no cost (Loaded's Pay Summary drops
#     them too; Orbit mapped them as $0 rows and inflated its counts);
#   - cost is Σ payRateBreakdown[].total and the public-holiday premium is
#     Σ rules[].processedRuleCost (Loaded prices PH; Norm prices nothing);
#   - the worked venue is Loaded's venueId on the shift and isFromOtherVenue
#     is Loaded's flag — never a name match (Orbit's fuzzy venue names);
#   - cost_group comes from the role's costGroup (b → bar_floor, f →
#     kitchen) for the journal's wage split;
#   - a parity line against Loaded's own pay summary says whether the rows
#     add up to what Loaded would export.
#
# Views (sections on request, so chat never carries a fortnight of
# breakdowns): staff | security | leave | salaried | hygiene | intercompany |
# all. `all` is what the snapshot stores (rows WITH breakdowns).
#
# Requires consolidator_config: {"max_api_calls": 24} — 8 calls serve a
# venue; `view: intercompany` with `venues: all` fans the clock-in and
# roles reads across every entity (2 + 2 × venues).

_VIEWS = ("staff", "security", "leave", "salaried", "hygiene", "intercompany", "all")
_SHIFT_TYPES = "shiftTypesToInclude=Clockin&shiftTypesToInclude=Roster&shiftTypesToInclude=DefaultHours&shiftTypesToInclude=Leave"
_COST_GROUPS = {"b": "bar_floor", "f": "kitchen"}
_DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MAX_WEEKS = 26


def _list_arg(value):
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            if isinstance(parsed, list):
                return [str(v).strip() for v in parsed if str(v).strip()]
        return [p.strip() for p in text.split(",") if p.strip()]
    return []


def _flag(value, default=False):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "y")


def _num(value):
    if isinstance(value, bool):
        return 0.0
    return float(value) if isinstance(value, (int, float)) else 0.0


def _cents(value):
    return int(round(_num(value) * 100))


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
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return [r for r in value if isinstance(r, dict)]
    return []


def _failed(payload):
    return isinstance(payload, dict) and bool(payload.get("error"))


def _parse_instant(text):
    if not text:
        return None
    s = str(text).strip()
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


def _trading_date(moment, day_start):
    day = moment.date()
    if moment.time() < day_start:
        day = day - datetime.timedelta(days=1)
    return day.isoformat()


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


def _localiser(window):
    """instant -> the venue's wall clock. Live rows carry the venue's offset;
    stale and deleted rows (and the shape-less path) can arrive in UTC, which
    the trading-day rule must not read as clock time. A UTC instant is moved
    onto the window's start offset, or its end offset once past the clock
    change a window may straddle."""
    start_off = _offset_of(window.get("start"))
    end_off = _offset_of(window.get("end"))
    change_at = None
    if start_off is not None and end_off is not None and start_off != end_off:
        first = _parse_instant(window["start"]).date()
        last = _parse_instant(window["end"]).date()
        sunday = _dst_sunday(first, last)
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


def _venues(call_api):
    listed = call_api("norm", "list_venues", {"connector": "loadedhub"})
    data = (
        listed.get("data") if isinstance(listed, dict) and "data" in listed else listed
    )
    rows = data.get("venues") if isinstance(data, dict) else None
    return [r for r in rows or [] if isinstance(r, dict)]


def _week_bounds(week_ending_text):
    """(monday, sunday) of the pay week holding week_ending — any date in
    the week snaps to that week."""
    d = datetime.date.fromisoformat(str(week_ending_text)[:10])
    monday = d - datetime.timedelta(days=d.weekday())
    return monday, monday + datetime.timedelta(days=6)


def _name(row):
    first = str(row.get("staffMemberFirstName") or "").strip()
    last = str(row.get("staffMemberLastName") or "").strip()
    return (first + " " + last).strip() or str(row.get("staffMemberName") or "")


def _shape_rows(raw_rows, roles, day_start, local, with_breakdown, dropped, unfinished):
    """pay_shift_facts rows out of Loaded's shift list; drops counted."""
    rows = []
    for r in raw_rows:
        if r.get("datestampDeleted"):
            dropped["deleted"] += 1
            continue
        shift_type = str(r.get("type") or "Clockin")
        start = local(_parse_instant(r.get("clockinTime")))
        end = local(_parse_instant(r.get("clockoutTime")))
        if shift_type == "Clockin" and end is None:
            dropped["unfinished"] += 1
            unfinished.append(
                {"staff_name": _name(r), "clockin_at": r.get("clockinTime")}
            )
            continue
        if start is None:
            # No clock-in instant at all: nothing to attribute to a day or
            # pay. Counted on its own so the shape of `dropped` stays honest.
            dropped["unparseable"] = dropped.get("unparseable", 0) + 1
            continue
        lines = [ln for ln in (r.get("payRateBreakdown") or []) if isinstance(ln, dict)]
        rules = [ru for ru in (r.get("rules") or []) if isinstance(ru, dict)]
        if lines:
            hours = sum(_num(ln.get("hoursWorked")) for ln in lines)
            cost = sum(_num(ln.get("total")) for ln in lines)
        else:
            hours = _num(r.get("totalHours"))
            cost = _num(r.get("totalCost"))
            if not hours and end is not None:
                hours = (end - start).total_seconds() / 3600.0
        export_total = r.get("payrollExportTotal")
        role = roles.get(str(r.get("roleId") or ""))
        cost_group = "other"
        if role:
            cost_group = _COST_GROUPS.get(
                str(role.get("costGroup") or "").strip().lower(), "other"
            )
        row = {
            "source_shift_id": str(r.get("id") or ""),
            "staff_member_id": str(r.get("staffMemberId") or ""),
            "staff_name": _name(r),
            "pay_code": r.get("staffMemberPayCode") or None,
            "shift_type": shift_type,
            "remuneration_type": r.get("remunerationType"),
            "role_id": r.get("roleId"),
            "role_name": r.get("roleName"),
            "cost_group": cost_group,
            "worked_venue_loaded_id": r.get("venueId"),
            "worked_venue_name": r.get("venueShortName") or r.get("venueFullName"),
            "is_from_other_venue": bool(r.get("isFromOtherVenue")),
            "clockin_at": start.isoformat(),
            "clockout_at": end.isoformat() if end else None,
            "trading_date": _trading_date(start, day_start),
            "hours": round(hours, 2),
            "cost_cents": _cents(cost),
            "export_cost_cents": _cents(export_total)
            if isinstance(export_total, (int, float))
            else _cents(cost),
            "ph_hours": round(sum(_num(ru.get("totalHours")) for ru in rules), 2),
            "ph_cost_cents": _cents(
                sum(_num(ru.get("processedRuleCost")) for ru in rules)
            ),
            "leave_request_id": r.get("leaveRequestId") or None,
            "leave_type_name": r.get("leaveTypeName") or None,
            # Only a clock-in is approved or not; the other types have nothing
            # to approve, so they never show as "unapproved".
            "is_approved": bool(r.get("datestampApproved"))
            if shift_type == "Clockin"
            else True,
        }
        if with_breakdown:
            row["breakdown"] = {"pay_rate_breakdown": lines, "rules": rules}
        rows.append(row)
    rows.sort(key=lambda x: (x["staff_name"], x["clockin_at"]))
    return rows


def _per_staff(rows):
    """Per-person totals over a set of rows."""
    out = {}
    for r in rows:
        acc = out.get(r["staff_member_id"])
        if acc is None:
            acc = out[r["staff_member_id"]] = {
                "staff_member_id": r["staff_member_id"],
                "staff_name": r["staff_name"],
                "pay_code": r["pay_code"],
                "remuneration_type": r["remuneration_type"],
                "shifts": 0,
                "hours": 0.0,
                "cost_cents": 0,
                "ph_hours": 0.0,
                "ph_cost_cents": 0,
                "other_venue_hours": 0.0,
                "all_approved": True,
            }
        acc["shifts"] += 1
        acc["hours"] += r["hours"]
        acc["cost_cents"] += r["cost_cents"]
        acc["ph_hours"] += r["ph_hours"]
        acc["ph_cost_cents"] += r["ph_cost_cents"]
        if r["is_from_other_venue"]:
            acc["other_venue_hours"] += r["hours"]
        if not r["is_approved"]:
            acc["all_approved"] = False
        if r["remuneration_type"] == "Salary":
            acc["remuneration_type"] = "Salary"
        if not acc["pay_code"] and r["pay_code"]:
            acc["pay_code"] = r["pay_code"]
    people = sorted(out.values(), key=lambda p: p["staff_name"])
    for p in people:
        p["hours"] = round(p["hours"], 2)
        p["ph_hours"] = round(p["ph_hours"], 2)
        p["other_venue_hours"] = round(p["other_venue_hours"], 2)
    return people


def _totals(rows):
    return {
        "shifts": len(rows),
        "hours": round(sum(r["hours"] for r in rows), 2),
        "cost_cents": sum(r["cost_cents"] for r in rows),
        "ph_hours": round(sum(r["ph_hours"] for r in rows), 2),
        "ph_cost_cents": sum(r["ph_cost_cents"] for r in rows),
    }


def _leave_section(rows, leave_types):
    paid = {}
    for lt in leave_types:
        paid[str(lt.get("name") or "").strip().casefold()] = lt.get("isPaid")
    grouped = {}
    for r in rows:
        key = (r["leave_request_id"] or r["source_shift_id"], r["staff_member_id"])
        acc = grouped.get(key)
        if acc is None:
            acc = grouped[key] = {
                "leave_request_id": r["leave_request_id"],
                "staff_member_id": r["staff_member_id"],
                "staff_name": r["staff_name"],
                "pay_code": r["pay_code"],
                "leave_type_name": r["leave_type_name"],
                "dates": [],
                "hours": 0.0,
                "cost_cents": 0,
                "is_paid_type": paid.get(
                    str(r["leave_type_name"] or "").strip().casefold()
                ),
            }
        if r["trading_date"] not in acc["dates"]:
            acc["dates"].append(r["trading_date"])
        acc["hours"] += r["hours"]
        acc["cost_cents"] += r["cost_cents"]
    out = sorted(grouped.values(), key=lambda x: (x["staff_name"], x["dates"]))
    for o in out:
        o["hours"] = round(o["hours"], 2)
    return {"rows": out, "totals": _totals(rows)}


def _salaried_section(rows):
    salaried_ids = {
        r["staff_member_id"] for r in rows if r["remuneration_type"] == "Salary"
    }
    out = {}
    for r in rows:
        if r["staff_member_id"] not in salaried_ids or r["shift_type"] == "Leave":
            continue
        acc = out.get(r["staff_member_id"])
        if acc is None:
            acc = out[r["staff_member_id"]] = {
                "staff_member_id": r["staff_member_id"],
                "staff_name": r["staff_name"],
                "pay_code": r["pay_code"],
                "hours": 0.0,
                "cost_cents": 0,
                "by_shift_type": {},
            }
        acc["hours"] += r["hours"]
        acc["cost_cents"] += r["cost_cents"]
        by = acc["by_shift_type"].setdefault(
            r["shift_type"], {"shifts": 0, "hours": 0.0, "cost_cents": 0}
        )
        by["shifts"] += 1
        by["hours"] = round(by["hours"] + r["hours"], 2)
        by["cost_cents"] += r["cost_cents"]
    people = sorted(out.values(), key=lambda p: p["staff_name"])
    for p in people:
        p["hours"] = round(p["hours"], 2)
    return {"rows": people, "count": len(people)}


def _parity(rows, summary, people_count):
    """The rows against Loaded's own pay summary for the same window."""
    if _failed(summary) or not isinstance(summary, dict):
        return {
            "error": "pay summary unavailable: "
            + str(summary.get("error") if isinstance(summary, dict) else summary)
        }
    total = summary.get("total") if isinstance(summary.get("total"), dict) else {}
    leave_total = (
        summary.get("leaveTotal") if isinstance(summary.get("leaveTotal"), dict) else {}
    )
    staff = [s for s in (summary.get("staff") or []) if isinstance(s, dict)]
    worked = [r for r in rows if r["shift_type"] != "Leave"]
    leave = [r for r in rows if r["shift_type"] == "Leave"]
    rows_wages = round(sum(r["export_cost_cents"] for r in worked) / 100.0, 2)
    rows_leave = round(sum(r["cost_cents"] for r in leave) / 100.0, 2)
    ps_wages = _num(total.get("totalWages"))
    ps_leave = _num(leave_total.get("totalPay"))
    diff = round(rows_wages - ps_wages, 2)
    leave_diff = round(rows_leave - ps_leave, 2)
    tolerance = round(0.05 * max(people_count, 1), 2)
    return {
        "pay_summary_total_wages": round(ps_wages, 2),
        "rows_wages": rows_wages,
        "difference": diff,
        "pay_summary_leave": round(ps_leave, 2),
        "rows_leave": rows_leave,
        "leave_difference": leave_diff,
        "pay_summary_hours_worked": round(_num(total.get("hoursWorked")), 2),
        "rows_hours": round(sum(r["hours"] for r in worked), 2),
        "all_approved": all(bool(s.get("allApproved")) for s in staff)
        if staff
        else None,
        "staff_in_summary": len(staff),
        "within_tolerance": abs(diff) <= tolerance,
        "tolerance": tolerance,
    }


def _intercompany_pairs(per_venue):
    """per_venue: [(payroll venue name, own Loaded id, rows)]. In = we pay
    someone who worked at them (we invoice); Out = they pay someone who
    worked here (they bill us); net = in − out."""
    own_ids = {name: own for name, own, _ in per_venue}
    name_of = {own: name for name, own in own_ids.items() if own}
    pairs = {}

    def bucket(a, b):
        return pairs.setdefault(
            (a, b),
            {
                "payroll_venue": a,
                "counterparty_loaded_id": b,
                "counterparty": name_of.get(b),
                "in_hours": 0.0,
                "in_cents": 0,
                "out_hours": 0.0,
                "out_cents": 0,
            },
        )

    for name, own, rows in per_venue:
        for r in rows:
            if not r["is_from_other_venue"]:
                continue
            worked = str(r["worked_venue_loaded_id"] or "")
            if not worked:
                continue
            b = bucket(name, worked)
            b["in_hours"] += r["hours"]
            b["in_cents"] += r["cost_cents"]
            if worked in name_of and own:
                mirror = bucket(name_of[worked], own)
                mirror["out_hours"] += r["hours"]
                mirror["out_cents"] += r["cost_cents"]
    out = []
    for p in pairs.values():
        p["in_hours"] = round(p["in_hours"], 2)
        p["out_hours"] = round(p["out_hours"], 2)
        p["net_cents"] = p["in_cents"] - p["out_cents"]
        out.append(p)
    out.sort(key=lambda p: (p["payroll_venue"], p["counterparty"] or ""))
    return out


def _own_loaded_id(rows):
    counts = {}
    for r in rows:
        if not r["is_from_other_venue"] and r["worked_venue_loaded_id"]:
            key = str(r["worked_venue_loaded_id"])
            counts[key] = counts.get(key, 0) + 1
    best = None
    for key, n in counts.items():
        if best is None or n > counts[best]:
            best = key
    return best


def run(params, call_api, log, call_api_parallel=None):
    view = str(params.get("view") or "staff").strip().lower()
    if view not in _VIEWS:
        return {"error": "unknown view '" + view + "' — use " + ", ".join(_VIEWS)}
    if not params.get("week_ending"):
        return {"error": "week_ending (YYYY-MM-DD, the pay week's Sunday) is required"}
    try:
        monday, sunday = _week_bounds(params["week_ending"])
    except ValueError:
        return {"error": "week_ending must be YYYY-MM-DD"}
    try:
        weeks = int(params.get("weeks") or 1)
    except (TypeError, ValueError):
        return {"error": "weeks must be a whole number"}
    if weeks < 1 or weeks > _MAX_WEEKS:
        return {"error": "weeks must be 1–" + str(_MAX_WEEKS)}
    first_monday = monday - datetime.timedelta(days=7 * (weeks - 1))
    want_rows = _flag(params.get("rows"), True)
    security_code = str(params.get("security_pay_code") or "SECURITY").strip()
    staff_ids = _list_arg(params.get("staff_member_ids"))

    venues_param = params.get("venues")
    fan_out = isinstance(venues_param, str) and venues_param.strip().lower() in (
        "all",
        "all venues",
        "*",
        "group",
    )
    if fan_out and view != "intercompany":
        return {
            "error": "venues='all' is only for view 'intercompany' — a pay week is "
            "one payroll company; name the venue"
        }

    venue = str(params.get("venue") or params.get("venue_name") or "").strip()
    venue_rows = None
    if fan_out or (not venue and params.get("venue_id")):
        venue_rows = _venues(call_api)
        if not venue and params.get("venue_id"):
            for r in venue_rows:
                if str(r.get("id")) == str(params["venue_id"]):
                    venue = str(r.get("name") or "")
    if not fan_out and not venue:
        return {"error": "venue (the payroll company's name) is required"}

    # The pay week(s): Monday → Monday at the venue's day start, with the
    # offsets Norm's calendar gives each end (DST-correct).
    resolve_args = {"query": "week beginning " + first_monday.isoformat()}
    if params.get("venue_id"):
        resolve_args["venue_id"] = params["venue_id"]
    window = _window_from(call_api("norm", "resolve_dates", resolve_args))
    if not window or not window.get("start") or not window.get("end"):
        return {"error": "could not resolve the pay week through Norm's calendar"}
    if weeks > 1:
        last = _window_from(
            call_api(
                "norm",
                "resolve_dates",
                {**resolve_args, "query": "week beginning " + monday.isoformat()},
            )
        )
        if not last or not last.get("end"):
            return {
                "error": "could not resolve the last pay week through Norm's calendar"
            }
        window = {**window, "end": last["end"]}
    day_start = _day_start_time(window)
    local = _localiser(window)
    q = _SHIFT_TYPES
    for sid in staff_ids:
        q += "&staffMemberIds=" + sid

    def clockin_call(name):
        args = {
            "start_time": window["start"],
            "end_time": window["end"],
            "q": q,
        }
        if name:
            args["venue"] = name
        elif params.get("venue_id"):
            args["venue_id"] = params["venue_id"]
        return ("loadedhub", "get_payrun_clockins_raw", args)

    def roles_call(name):
        return ("loadedhub", "get_staff_roles", {"venue": name} if name else {})

    def roles_of(payload):
        return {
            str(r.get("id")): r for r in _rows_of(payload) if r.get("id") is not None
        }

    dropped = {"deleted": 0, "unfinished": 0}
    unfinished = []
    notes = []

    # ── group-wide intercompany: every entity's clock-ins ───────────────
    if fan_out:
        names = [r["name"] for r in venue_rows if r.get("connected") and r.get("name")]
        if not names:
            return {"error": "no connected Loaded venues to fan out over"}
        calls = [clockin_call(n) for n in names] + [roles_call(n) for n in names]
        results = (
            call_api_parallel(calls)
            if call_api_parallel
            else [call_api(c, a, p) for (c, a, p) in calls]
        )
        per_venue = []
        all_rows = []
        failed = []
        for i, name in enumerate(names):
            payload, roles_payload = results[i], results[len(names) + i]
            if _failed(payload):
                failed.append(name + ": " + str(payload["error"]))
                continue
            roles = roles_of(roles_payload) if not _failed(roles_payload) else {}
            rows = _shape_rows(
                _rows_of(payload), roles, day_start, local, False, dropped, unfinished
            )
            for r in rows:
                r["payroll_venue"] = name
            per_venue.append((name, _own_loaded_id(rows), rows))
            all_rows.extend([r for r in rows if r["is_from_other_venue"]])
        if failed:
            notes.append(
                "some entities could not be read and are missing from the "
                "intercompany picture: " + "; ".join(failed)
            )
        return {
            "venues": names,
            "week_ending": sunday.isoformat(),
            "week_start": first_monday.isoformat(),
            "window": {
                "start": window["start"],
                "end": window["end"],
                "day_start": window.get("day_start"),
                "timezone": window.get("timezone"),
            },
            "view": "intercompany",
            "rows": all_rows if want_rows else [],
            "dropped": dropped,
            "totals": _totals(all_rows),
            "sections": {
                "intercompany": {
                    "by_counterparty": _intercompany_pairs(per_venue),
                    "entities": [
                        {"payroll_venue": n, "loaded_id": own, "shifts": len(rows)}
                        for n, own, rows in per_venue
                    ],
                }
            },
            "notes": notes
            + [
                "in = this entity paid staff who worked at the counterparty (it "
                "invoices them); out = the counterparty paid staff who worked "
                "here (they bill us); net = in − out, in cents"
            ],
        }

    # ── one payroll company ─────────────────────────────────────────────
    calls = [
        clockin_call(venue),
        (
            "loadedhub",
            "get_pay_summary_raw",
            {"venue": venue, "start_time": window["start"], "end_time": window["end"]},
        ),
        roles_call(venue),
    ]
    need_staff = view in ("hygiene", "all")
    need_leave_types = view in ("leave", "all")
    if need_staff:
        calls.append(
            (
                "loadedhub",
                "get_staff_members",
                {
                    "venue": venue,
                    "include_deleted": "true",
                    "include_last_clocks": "false",
                },
            )
        )
    if need_leave_types:
        calls.append(("loadedhub", "get_leave_types", {"venue": venue}))
    results = (
        call_api_parallel(calls)
        if call_api_parallel
        else [call_api(c, a, p) for (c, a, p) in calls]
    )
    clockins, summary, roles_payload = results[0], results[1], results[2]
    staff_payload = results[3] if need_staff else None
    leave_types_payload = results[-1] if need_leave_types else None
    if _failed(clockins):
        return {
            "error": "could not read the pay week's clock-ins from Loaded: "
            + str(clockins["error"]),
            "venue": venue,
        }
    roles = {}
    if _failed(roles_payload):
        notes.append(
            "roles unavailable (" + str(roles_payload["error"]) + ") — every "
            "cost_group is 'other'"
        )
    else:
        roles = roles_of(roles_payload)
    rows = _shape_rows(
        _rows_of(clockins),
        roles,
        day_start,
        local,
        view == "all",
        dropped,
        unfinished,
    )
    if any(r["is_from_other_venue"] and r["cost_group"] == "other" for r in rows):
        notes.append(
            "shifts worked at another venue carry that venue's role, which is "
            "not in this company's role list — their cost_group is 'other'"
        )

    worked = [r for r in rows if r["shift_type"] != "Leave"]
    leave_rows = [r for r in rows if r["shift_type"] == "Leave"]
    security = [r for r in worked if (r["pay_code"] or "") == security_code]
    main = [r for r in worked if (r["pay_code"] or "") != security_code]
    people = _per_staff(main)
    parity = _parity(rows, summary, len(people))

    sections = {}
    if view in ("staff", "all"):
        sections["staff"] = {"rows": people, "totals": _totals(main)}
    if view in ("security", "all"):
        sections["security"] = {
            "pay_code": security_code,
            "rows": _per_staff(security),
            "totals": _totals(security),
        }
    if view in ("leave", "all"):
        leave_types = (
            _rows_of(leave_types_payload) if not _failed(leave_types_payload) else []
        )
        if _failed(leave_types_payload):
            notes.append(
                "leave types unavailable: " + str(leave_types_payload["error"])
            )
        sections["leave"] = _leave_section(leave_rows, leave_types)
    if view in ("salaried", "all"):
        sections["salaried"] = _salaried_section(rows)
    if view in ("hygiene", "all"):
        in_rows = {}
        for r in rows:
            in_rows.setdefault(r["staff_member_id"], r["staff_name"])
        without_timeline = []
        if staff_payload is not None and not _failed(staff_payload):
            for s in _rows_of(staff_payload):
                sid = str(s.get("id") or "")
                if sid in in_rows and not s.get("remunerationTimeline"):
                    without_timeline.append(in_rows[sid])
        elif staff_payload is not None:
            notes.append("staff list unavailable: " + str(staff_payload["error"]))
        sections["hygiene"] = {
            "unapproved_clockins": [
                {
                    "staff_name": r["staff_name"],
                    "clockin_at": r["clockin_at"],
                    "clockout_at": r["clockout_at"],
                }
                for r in rows
                if r["shift_type"] == "Clockin" and not r["is_approved"]
            ],
            "staff_without_pay_code": sorted(
                {r["staff_name"] for r in worked if not r["pay_code"]}
            ),
            "unfinished_clockins": unfinished,
            "staff_without_timeline": sorted(set(without_timeline)),
            "salaried_count": len(
                {
                    r["staff_member_id"]
                    for r in rows
                    if r["remuneration_type"] == "Salary"
                }
            ),
            "public_holiday_dates": sorted(
                {r["trading_date"] for r in rows if r["ph_cost_cents"] > 0}
            ),
            "parity": parity,
        }
    if view in ("intercompany", "all"):
        other = [r for r in worked if r["is_from_other_venue"]]
        by = {}
        for r in other:
            key = str(r["worked_venue_loaded_id"] or "")
            acc = by.setdefault(
                key,
                {
                    "worked_venue_loaded_id": key,
                    "worked_venue_name": r["worked_venue_name"],
                    "direction": "in",
                    "hours": 0.0,
                    "cost_cents": 0,
                    "shifts": 0,
                },
            )
            acc["hours"] = round(acc["hours"] + r["hours"], 2)
            acc["cost_cents"] += r["cost_cents"]
            acc["shifts"] += 1
        sections["intercompany"] = {
            "counterparties": sorted(
                by.values(), key=lambda x: x["worked_venue_name"] or ""
            ),
            "rows": other if want_rows else [],
            "note": (
                "one company's token shows only what IT pays for work done "
                "elsewhere (in); call with venues='all' for both directions"
            ),
        }

    if view == "all":
        shown = rows
    elif view == "staff":
        shown = main
    elif view == "security":
        shown = security
    elif view == "leave":
        shown = leave_rows
    elif view == "salaried":
        salaried_ids = {
            r["staff_member_id"] for r in rows if r["remuneration_type"] == "Salary"
        }
        shown = [r for r in worked if r["staff_member_id"] in salaried_ids]
    elif view == "hygiene":
        shown = [
            r for r in rows if r["shift_type"] == "Clockin" and not r["is_approved"]
        ]
    else:
        shown = [r for r in worked if r["is_from_other_venue"]]

    week_label = (
        "week beginning " + first_monday.isoformat() + " ending " + sunday.isoformat()
        if weeks > 1
        else "week ending " + _DOW[sunday.weekday()] + " " + sunday.isoformat()
    )
    result = {
        "venue": venue,
        "week_ending": sunday.isoformat(),
        "week_start": first_monday.isoformat(),
        "weeks": weeks,
        "window": {
            "start": window["start"],
            "end": window["end"],
            "day_start": window.get("day_start"),
            "timezone": window.get("timezone"),
            "label": week_label,
        },
        "view": view,
        "rows": shown if want_rows else [],
        "row_count": len(shown),
        "dropped": dropped,
        "dropped_unfinished": unfinished,
        "totals": {
            **_totals(worked),
            "leave_hours": round(sum(r["hours"] for r in leave_rows), 2),
            "leave_cost_cents": sum(r["cost_cents"] for r in leave_rows),
            "security_cost_cents": sum(r["cost_cents"] for r in security),
            "other_venue_cost_cents": sum(
                r["cost_cents"] for r in worked if r["is_from_other_venue"]
            ),
        },
        "sections": sections,
        "parity": parity,
    }
    if params.get("venue_id"):
        result["venue_id"] = params["venue_id"]
    if staff_ids:
        result["staff_member_ids"] = staff_ids
    if str(params.get("week_ending"))[:10] != sunday.isoformat():
        notes.append(
            "week_ending "
            + str(params.get("week_ending"))[:10]
            + " is not a Sunday — read as the pay week ending "
            + sunday.isoformat()
        )
    if notes:
        result["notes"] = notes
    return result
