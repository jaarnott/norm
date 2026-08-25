"""get_labour — THE labour domain tool: views, leave-split, fan-out.

Exec'd under the REAL sandbox namespace. Two incident classes ride along
from the tools it absorbs:

- Loaded's /api/time-clockins returns booked LEAVE as pseudo clock-ins
  (type "Leave", synthetic 7:00 AM-3:00 PM, 8h at pay rate). On Bessie
  (Sat 15 Aug 2026, prod thread 51a90809) two leave entries surfaced as
  two ghost unrostered 8h shifts, inflating the day +16h. Leave is split
  out and never counted as worked time — the staff_attendance doctrine,
  now the attendance view.
- The roster view is a RAW passthrough in the {window, data} envelope:
  the roster card and the claude.ai artifact parse that exact connector
  shape, and the shift write path needs every field intact.
"""

import pathlib

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

CODE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "get_labour.py"
).read_text(encoding="utf-8")

START = "2026-08-15T07:00:00+12:00"
END = "2026-08-16T06:59:59+12:00"

WINDOW = {
    "start": START,
    "end": END,
    "trading_aligned": True,
    "description": "Saturday trading day",
}


def _shift(sid, first, last, start, end, hours, cost, role="FLOOR TEAM", **extra):
    return {
        "staffMemberId": sid,
        "staffMemberFirstName": first,
        "staffMemberLastName": last,
        "roleName": role,
        "clockinTime": start,
        "clockoutTime": end,
        "totalHours": hours,
        "totalCost": cost,
        **extra,
    }


# Shapes captured live (20 Aug 2026, Bessie): the roster call returns
# {"rosteredShifts": [...]} plus roster-level fields the write path needs.
ROSTER = {
    "id": "roster-1",
    "venueId": "v-bessie",
    "rosteredShifts": [
        _shift(
            "s1",
            "Evelyn",
            "Clarke",
            "2026-08-15T17:30:00+12:00",
            "2026-08-15T21:30:00+12:00",
            4.0,
            100.0,
            id="shift-1",
            rosterId="roster-1",
            venueId="v-bessie",
            hourlyRate=25.0,
        ),
        _shift(
            "s2",
            "Olivia",
            "Dunn",
            "2026-08-15T18:00:00+12:00",
            "2026-08-15T21:45:00+12:00",
            3.75,
            90.0,
            id="shift-2",
            rosterId="roster-1",
            venueId="v-bessie",
            hourlyRate=24.0,
        ),
    ],
}

CLOCKINS = [
    _shift(
        "s1",
        "Evelyn",
        "Clarke",
        "2026-08-15T17:19:00+12:00",
        "2026-08-15T22:31:00+12:00",
        5.2,
        130.0,
        type="Clockin",
    ),
    _shift(
        "s3",
        "Zoe",
        "Kite",
        "2026-08-15T12:00:00+12:00",
        "2026-08-15T16:00:00+12:00",
        4.0,
        80.0,
        type="Clockin",
    ),
    _shift(
        "s4",
        "Aliana",
        "Henderson",
        "2026-08-15T07:00:00+12:00",
        "2026-08-15T15:00:00+12:00",
        8.0,
        208.0,
        type="Leave",
    ),
    _shift(
        "s5",
        "Chloe",
        "Ward",
        "2026-08-15T07:00:00+12:00",
        "2026-08-15T15:00:00+12:00",
        8.0,
        196.0,
        type="Leave",
    ),
]

STAFF_MEMBERS = [
    {
        "id": "s1",
        "name": "Evelyn Clarke",
        "defaultMemberRoleRoleName": "FLOOR TEAM",
        "defaultMemberRoleHourlyRate": 25.0,
        "email": "evelyn@x.com",
    },
    {
        "id": "s6",
        "name": "Marco Silva",
        "defaultMemberRoleRoleName": "KITCHEN TEAM",
        "salaryRate": 72000,
    },
]

# The REAL shape call_api hands back for internal norm.* tools: UNWRAPPED.
VENUES = {
    "connector": "loadedhub",
    "venues": [
        {"id": "v1", "name": "La Zeppa", "connected": True},
        {"id": "v2", "name": "Bessie & Royals", "connected": True},
        {"id": "v3", "name": "Mr Murdochs", "connected": False},
    ],
}


class Api:
    def __init__(self):
        self.seen = []
        self.roster = ROSTER
        self.clockins = CLOCKINS
        self.fail_venue = None

    def _for(self, connector, action, params):
        p = dict(params or {})
        self.seen.append((action, p))
        if action == "resolve_dates":
            return {"window": dict(WINDOW)}
        if action == "list_venues":
            return dict(VENUES)
        if p.get("venue") and p["venue"] == self.fail_venue:
            return {"error": "Loaded timed out"}
        if action == "get_roster":
            return self.roster
        if action == "get_timeclock_entries":
            return self.clockins
        if action == "get_roster_vs_actual":
            return [
                {
                    "startTime": START,
                    "rosteredHours": 7.75,
                    "actualHours": 9.2,
                    "rosteredCost": 190.0,
                    "actualCost": 210.0,
                }
            ]
        if action == "get_staff_members":
            return list(STAFF_MEMBERS)
        raise AssertionError(f"unexpected action {action}")

    def call_api(self, connector, action, params=None):
        return self._for(connector, action, params)

    def call_api_parallel(self, calls):
        return [self._for(c, a, p) for (c, a, p) in calls]

    def log(self, m):
        pass


def run(api, **params):
    ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(CODE, ns)
    return ns["run"](
        {"venue": "Bessie & Royals", "period": "saturday", **params},
        api.call_api,
        api.log,
        api.call_api_parallel,
    )


class TestAttendanceLeaveSplit:
    """The ghost-shift pins, ported verbatim from get_staff_attendance."""

    def test_detail_splits_leave_out_of_clockins(self):
        out = run(api := Api(), group_by="detail")
        assert out["view"] == "attendance"
        assert [r["name"] for r in out["clockins"]] == ["Zoe Kite", "Evelyn Clarke"]
        assert [r["name"] for r in out["leave"]] == ["Aliana Henderson", "Chloe Ward"]
        assert out["leave"][0]["start"] == "7:00 AM"
        t = out["totals"]
        assert t["actual_hours"] == 9.2
        assert t["rostered_hours"] == 7.75
        assert t["variance"] == 1.45
        assert t["leave_hours"] == 16.0
        assert t["leave_cost"] == 404.0

    def test_staff_view_leave_never_counts_as_actual(self):
        out = run(Api(), group_by="staff")
        by_name = {r["name"]: r for r in out["rows"]}
        aliana = by_name["Aliana Henderson"]
        assert aliana["actual_hours"] == 0.0
        assert aliana["leave_hours"] == 8.0
        assert aliana["variance"] == 0.0
        assert by_name["Evelyn Clarke"]["actual_hours"] == 5.2
        assert "leave_hours" not in by_name["Evelyn Clarke"]

    def test_day_view_unrostered_ignores_leave(self):
        out = run(Api(), group_by="day")
        assert len(out["rows"]) == 1
        day = out["rows"][0]
        # Only Zoe Kite (a real clock-in with no roster) is unrostered — the
        # two leave entries are absences, not surprise shifts.
        assert day["unrostered"] == 1
        assert day["actual_hours"] == 9.2
        assert day["leave_hours"] == 16.0

    def test_attendance_is_the_default_view(self):
        out = run(Api())
        assert out["view"] == "attendance"
        assert out["totals"]["actual_hours"] == 9.2


class TestAttendanceGroup:
    def test_venues_all_gives_per_venue_totals(self):
        api = Api()
        out = run(api, venues="all")
        rows = {r["venue"]: r for r in out["rows"]}
        # Connected venues only.
        assert set(rows) == {"La Zeppa", "Bessie & Royals"}
        assert rows["La Zeppa"]["actual_hours"] == 9.2
        assert rows["La Zeppa"]["leave_hours"] == 16.0
        assert out["totals"]["actual_hours"] == 18.4
        assert out["totals"]["variance"] == 2.9

    def test_a_failing_venue_is_a_flagged_row(self):
        api = Api()
        api.fail_venue = "La Zeppa"
        out = run(api, venues="all")
        lz = next(r for r in out["rows"] if r["venue"] == "La Zeppa")
        assert any("timed out" in e for e in lz["errors"])
        assert "La Zeppa" in out["note"]
        assert out["totals"]["actual_hours"] == 9.2  # only the good venue


class TestRosterView:
    def test_raw_passthrough_in_the_envelope(self):
        out = run(Api(), view="roster")
        assert out["view"] == "roster"
        assert out["window"] == WINDOW
        # The write path's fields survive by construction: passthrough.
        assert out["data"]["id"] == "roster-1"
        shift = out["data"]["rosteredShifts"][0]
        assert shift["id"] == "shift-1"
        assert shift["rosterId"] == "roster-1"
        assert shift["venueId"] == "v-bessie"
        assert shift["hourlyRate"] == 25.0
        assert shift["clockinTime"]

    def test_staff_name_narrows_shifts_but_keeps_roster_fields(self):
        out = run(Api(), view="roster", staff_name="olivia")
        rosters = out["data"]
        assert rosters[0]["id"] == "roster-1"  # roster-level fields intact
        names = [s["staffMemberFirstName"] for s in rosters[0]["rosteredShifts"]]
        assert names == ["Olivia"]

    def test_venues_is_refused_outside_attendance(self):
        out = run(Api(), view="roster", venues="all")
        assert "venues" in out["error"]


class TestVsActualView:
    def test_summary_and_daily_interval_default(self):
        api = Api()
        out = run(api, view="vs_actual")
        assert out["view"] == "vs_actual"
        fetch = next(p for a, p in api.seen if a == "get_roster_vs_actual")
        assert fetch["interval"] == "1.00:00:00"
        sums = out["summary"]["column_sums"]
        assert sums["rosteredHours"] == 7.75
        assert sums["actualHours"] == 9.2


class TestTimeclockView:
    def test_flags_ride_as_strings(self):
        """The executor's required-field check is falsy-based: a boolean
        False reads as 'missing', so the flags must be the strings LLM
        traffic always sent."""
        api = Api()
        out = run(api, view="timeclock")
        fetch = next(p for a, p in api.seen if a == "get_timeclock_entries")
        assert fetch["include_inactive"] == "false"
        assert fetch["include_only_clockins"] == "false"
        assert fetch["should_truncate_shifts"] == "true"
        assert out["summary"]["row_count"] == 4

    def test_staff_name_filters_entries(self):
        out = run(Api(), view="timeclock", staff_name="zoe")
        assert [c["staffMemberFirstName"] for c in out["data"]] == ["Zoe"]


class TestStaffView:
    def test_slim_reference_rows_no_window_needed(self):
        api = Api()
        out = run(api, view="staff")
        # No resolve_dates call — a reference list has no window.
        assert not [a for a, _ in api.seen if a == "resolve_dates"]
        fetch = next(p for a, p in api.seen if a == "get_staff_members")
        assert fetch["include_deleted"] == "false"
        rows = {r["name"]: r for r in out["rows"]}
        assert rows["Evelyn Clarke"]["role"] == "FLOOR TEAM"
        assert rows["Evelyn Clarke"]["hourly_rate"] == 25.0
        assert rows["Marco Silva"]["salary_rate"] == 72000
        assert "email" not in rows["Marco Silva"]  # empty fields dropped

    def test_staff_name_filters(self):
        out = run(Api(), view="staff", staff_name="marco")
        assert [r["name"] for r in out["rows"]] == ["Marco Silva"]


class TestGates:
    def test_no_period_is_refused(self):
        api = Api()
        ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
        exec(CODE, ns)
        out = ns["run"](
            {"venue": "Bessie & Royals"}, api.call_api, api.log, api.call_api_parallel
        )
        assert "period" in out["error"]

    def test_unaligned_explicit_range_asks_first(self):
        api = Api()

        def call(connector, action, p=None):
            if action == "resolve_dates":
                return {
                    "window": {
                        "start": "2026-08-10T00:00:00+12:00",
                        "end": "2026-08-17T00:00:00+12:00",
                        "trading_aligned": False,
                        "description": "Custom window - splits a trading session.",
                    }
                }
            return api._for(connector, action, p)

        ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
        exec(CODE, ns)
        out = ns["run"](
            {
                "venue": "Bessie & Royals",
                "start": "2026-08-10T00:00:00+12:00",
                "end": "2026-08-17T00:00:00+12:00",
            },
            call,
            api.log,
            None,
        )
        assert out.get("needs_confirmation") is True
        assert "confirmed_by_user" in out["question"]

    def test_unknown_view_is_refused(self):
        out = run(Api(), view="vibes")
        assert "unknown view" in out["error"]
