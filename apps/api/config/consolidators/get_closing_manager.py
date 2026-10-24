# ruff: noqa: F821 — `datetime` and `json` are injected into the sandbox
# namespace by app/connectors/function_executor.py; they are not imports.
#
# Canonical function_code for `loadedhub.get_closing_manager` — ENGINE-ONLY
# (installed by scripts/sync_payroll_config.py, never claimed by an App).
#
# Who closed the venue on a given night: the Sales Manager whose shift ended
# latest on that trading day. The 08:00 entertainers job and the Roster
# tab's "Closing manager" column call it (plan docs/plans/finance-migration.md
# §7, §11.7); its answer is who gets the "please confirm the performance"
# email. Orbit's resolve-sales-manager rule, kept:
#
#   - bucket every shift by the TRADING day of its start — a 01:00 start at
#     a 07:00 venue belongs to the previous date (the venue's own day_start,
#     D11), so a manager who clocked in at 16:58 and out at 01:12 is that
#     night's closer;
#   - a clock-in beats the roster: per date the latest-ENDING clock-in in the
#     named roles wins (`source` "both" when that person was rostered too),
#     else the latest-ending rostered shift ("rostered_only"), else null;
#   - deleted rows are skipped; roles compare trimmed and case-blind (Loaded
#     returns "SALES MANAGER");
#   - the email comes from the staff record, never guessed.
#
# Reads the EXISTING rows get_timeclock_entries, get_roster and
# get_staff_members (no new endpoint, no change to a shared one) plus
# norm.resolve_dates for the venue's day start and offsets: exactly four calls.
#
# Requires consolidator_config: {"max_api_calls": 4}

_DEFAULT_ROLES = ("Sales Manager",)
_MAX_DATES = 31


def _list_arg(value):
    """A list however it arrived: a list, JSON text of one, or comma text."""
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


def _norm_role(name):
    return str(name or "").strip().casefold()


def _window_from(resolved):
    if not isinstance(resolved, dict):
        return None
    data = resolved.get("data") if "data" in resolved else resolved
    if not isinstance(data, dict):
        return None
    window = data.get("window")
    return window if isinstance(window, dict) else None


def _parse_instant(text):
    """An aware datetime from Loaded's ISO text, or None. A bare 'Z' is UTC."""
    if not text:
        return None
    s = str(text).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        moment = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment


def _offset_of(iso_text):
    """The UTC offset (a timedelta) carried by an ISO string, or None."""
    moment = _parse_instant(iso_text)
    return moment.utcoffset() if moment is not None else None


def _dst_sunday(dates):
    """The Sunday in `dates` on which NZ or AU clocks change, if any: the last
    Sunday of September, the first Sunday of April or the first Sunday of
    October (QLD never changes, so its offsets never differ)."""
    for d in dates:
        if d.weekday() != 6:
            continue
        if d.month == 4 and d.day <= 7:
            return d
        if d.month == 10 and d.day <= 7:
            return d
        if d.month == 9 and d.day + 7 > 30:
            return d
    return None


def _localiser(window, dates):
    """A function instant -> venue-local wall clock. Loaded stamps clock-ins
    with the venue's own offset; rosters can arrive in UTC (the shape's |tz
    converts them in production). A UTC-stamped instant is moved onto the
    venue's offset: the window's start offset, or its end offset once past
    the DST change the window straddles."""
    start_off = _offset_of(window.get("start"))
    end_off = _offset_of(window.get("end"))
    change_at = None
    if start_off is not None and end_off is not None and start_off != end_off:
        sunday = _dst_sunday(dates)
        if sunday is not None:
            # Both NZ and AU change between 02:00 and 03:00 local on that
            # Sunday; 02:30 in the OLD offset is inside the change either way.
            change_at = datetime.datetime(
                sunday.year,
                sunday.month,
                sunday.day,
                2,
                30,
                tzinfo=datetime.timezone(start_off),
            )

    def local(moment):
        if moment.utcoffset() != datetime.timedelta(0) or start_off is None:
            return moment
        if start_off == datetime.timedelta(0):
            return moment
        off = start_off
        if change_at is not None and moment >= change_at and end_off is not None:
            off = end_off
        return moment.astimezone(datetime.timezone(off))

    return local


def _day_start_time(window):
    raw = str(window.get("day_start") or "07:00")
    parts = raw.split(":")
    try:
        return datetime.time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except ValueError:
        return datetime.time(7, 0)


def _trading_date(local_moment, day_start):
    day = local_moment.date()
    if local_moment.time() < day_start:
        day = day - datetime.timedelta(days=1)
    return day.isoformat()


def _rows_of(payload, key=None):
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        if key and isinstance(payload.get(key), list):
            return [r for r in payload[key] if isinstance(r, dict)]
        for value in payload.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return [r for r in value if isinstance(r, dict)]
    return []


def _name_of(row):
    first = str(row.get("staffMemberFirstName") or "").strip()
    last = str(row.get("staffMemberLastName") or "").strip()
    full = (first + " " + last).strip()
    return full or str(row.get("staffMemberName") or row.get("name") or "").strip()


def _venue_name(params, call_api):
    name = str(params.get("venue") or params.get("venue_name") or "").strip()
    if name or not params.get("venue_id"):
        return name
    listed = call_api("norm", "list_venues", {"connector": "loadedhub"})
    data = (
        listed.get("data") if isinstance(listed, dict) and "data" in listed else listed
    )
    for r in (data or {}).get("venues") or [] if isinstance(data, dict) else []:
        if isinstance(r, dict) and str(r.get("id")) == str(params["venue_id"]):
            return str(r.get("name") or "")
    return ""


def run(params, call_api, log):
    dates = []
    seen = set()
    for text in _list_arg(params.get("dates")):
        try:
            d = datetime.date.fromisoformat(text[:10])
        except ValueError:
            return {"error": "dates must be YYYY-MM-DD, got " + repr(text)}
        if d not in seen:
            seen.add(d)
            dates.append(d)
    if not dates:
        return {"error": "dates is required: the trading days to look up (YYYY-MM-DD)"}
    if len(dates) > _MAX_DATES:
        return {
            "error": "at most "
            + str(_MAX_DATES)
            + " dates per call — got "
            + str(len(dates))
        }
    dates.sort()
    roles = _list_arg(params.get("roles")) or list(_DEFAULT_ROLES)
    wanted = {_norm_role(r) for r in roles}

    # The venue's calendar for the whole span in ONE call: a "D1 to D2"
    # range resolves to D1 at day start → D2's day end, each instant with its
    # own offset (a span across the DST change gets both), plus day_start.
    resolve_args = {
        "query": dates[0].isoformat() + " to " + dates[-1].isoformat(),
    }
    if params.get("venue_id"):
        resolve_args["venue_id"] = params["venue_id"]
    window = _window_from(call_api("norm", "resolve_dates", resolve_args))
    if not window or not window.get("start") or not window.get("end"):
        return {"error": "could not resolve the venue's trading days for those dates"}
    day_start = _day_start_time(window)
    local = _localiser(window, dates)

    venue = str(params.get("venue") or params.get("venue_name") or "").strip()
    venue_arg = {"venue": venue} if venue else {}
    if params.get("venue_id"):
        venue_arg["venue_id"] = params["venue_id"]

    clockins = call_api(
        "loadedhub",
        "get_timeclock_entries",
        {
            **venue_arg,
            "start_time": window["start"],
            "end_time": window["end"],
            # Strings on purpose: the executor's required-field check is
            # falsy-based, so a real False reads as "missing".
            "include_inactive": "false",
            "include_only_clockins": "false",
            "should_truncate_shifts": "false",
        },
    )
    if isinstance(clockins, dict) and clockins.get("error"):
        return {
            "error": "could not read clock-ins from Loaded: " + str(clockins["error"])
        }
    roster = call_api(
        "loadedhub",
        "get_roster",
        {
            **venue_arg,
            "start_datetime": window["start"],
            "end_datetime": window["end"],
        },
    )
    roster_failed = isinstance(roster, dict) and roster.get("error")
    staff = call_api(
        "loadedhub",
        "get_staff_members",
        {**venue_arg, "include_deleted": "false", "include_last_clocks": "false"},
    )
    staff_failed = isinstance(staff, dict) and staff.get("error")
    emails = {}
    if not staff_failed:
        for s in _rows_of(staff):
            if s.get("id"):
                emails[str(s["id"])] = s.get("email") or None

    def candidate(row):
        """(trading date, candidate) for a shift in a wanted role, or None."""
        if row.get("datestampDeleted"):
            return None
        if _norm_role(row.get("roleName")) not in wanted:
            return None
        start = _parse_instant(row.get("clockinTime") or row.get("startTime"))
        if start is None:
            return None
        end = _parse_instant(row.get("clockoutTime") or row.get("endTime"))
        start_local = local(start)
        end_local = local(end) if end is not None else None
        return (
            _trading_date(start_local, day_start),
            {
                "staff_member_id": str(row.get("staffMemberId") or ""),
                "name": _name_of(row),
                "role_name": row.get("roleName"),
                "start": start_local,
                # Still on shift: the start stands in for the end, as Orbit did.
                "end": end_local if end_local is not None else start_local,
                "clockin_at": start_local.isoformat(),
                "clockout_at": end_local.isoformat() if end_local else None,
            },
        )

    clocked = {}
    for row in _rows_of(clockins):
        # Leave and roster pseudo-shifts ride the clock-in feed too; only a
        # real clock-in says someone was there.
        if str(row.get("type") or "Clockin") != "Clockin":
            continue
        hit = candidate(row)
        if hit:
            clocked.setdefault(hit[0], []).append(hit[1])
    rostered = {}
    if not roster_failed:
        for r in _rows_of(roster):
            shifts = r.get("rosteredShifts") if isinstance(r, dict) else None
            for row in shifts if isinstance(shifts, list) else []:
                if not isinstance(row, dict):
                    continue
                hit = candidate(row)
                if hit:
                    rostered.setdefault(hit[0], []).append(hit[1])

    def latest(rows):
        best = None
        for r in rows:
            if best is None or r["end"] > best["end"]:
                best = r
        return best

    by_date = {}
    counts = {"clockin": 0, "both": 0, "rostered_only": 0, "none": 0}
    for d in dates:
        key = d.isoformat()
        pick = None
        if clocked.get(key):
            closer = latest(clocked[key])
            also = any(
                r["staff_member_id"] == closer["staff_member_id"]
                for r in rostered.get(key) or []
            )
            pick = (closer, "both" if also else "clockin")
        elif rostered.get(key):
            pick = (latest(rostered[key]), "rostered_only")
        if pick is None:
            by_date[key] = None
            counts["none"] += 1
            continue
        who, source = pick
        counts[source] += 1
        by_date[key] = {
            "name": who["name"],
            "staff_member_id": who["staff_member_id"],
            "email": emails.get(who["staff_member_id"]),
            "source": source,
            "clockin_at": who["clockin_at"],
            "clockout_at": who["clockout_at"],
            "role_name": who["role_name"],
        }

    result = {
        "venue": venue or None,
        "roles": roles,
        "window": {
            "start": window["start"],
            "end": window["end"],
            "day_start": window.get("day_start"),
            "timezone": window.get("timezone"),
        },
        "by_date": by_date,
        "counts": counts,
    }
    notes = []
    if roster_failed:
        notes.append(
            "roster unavailable: " + str(roster["error"]) + " — clock-ins only"
        )
    if staff_failed:
        notes.append("staff list unavailable: " + str(staff["error"]) + " — no emails")
    if notes:
        result["notes"] = notes
    return result
