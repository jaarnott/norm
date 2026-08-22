"""Tests for the get_staff_attendance consolidator.

Same harness as the other consolidator tests: the canonical code from
config/consolidators/ is exec'd under the REAL sandbox namespace, so CI runs the
exact code production runs.

The regression these lock down: Loaded's /api/time-clockins returns booked LEAVE
as pseudo clock-ins (type "Leave", rendered 7:00 AM-3:00 PM, 8h at pay rate).
The consolidator counted those as worked hours — on Bessie & Engineers
(Sat 15 Aug 2026, prod thread 51a90809) two leave entries surfaced as two
"ghost" unrostered 8h shifts that existed nowhere in Loaded's timesheet screen,
inflating the day +16h and the unrostered count by 2. Leave must be reported
separately and never counted as actual worked time or as an unrostered clock-in.
"""

import pathlib

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

FUNCTION_CODE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "staff_attendance.py"
).read_text(encoding="utf-8")

START = "2026-08-15T07:00:00+12:00"
END = "2026-08-16T06:59:59+12:00"


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


# Shapes captured live (20 Aug 2026, Bessie & Engineers): the roster call returns
# {"rosteredShifts": [...]}; time-clockins rows carry type "Clockin" for real
# entries and "Leave" for booked leave (synthetic 7:00 AM-3:00 PM, 8h).
ROSTER = {
    "rosteredShifts": [
        _shift(
            "s1",
            "Evelyn",
            "Clarke",
            "2026-08-15T17:30:00+12:00",
            "2026-08-15T21:30:00+12:00",
            4.0,
            100.0,
        ),
        _shift(
            "s2",
            "Olivia",
            "Dunn",
            "2026-08-15T18:00:00+12:00",
            "2026-08-15T21:45:00+12:00",
            3.75,
            90.0,
        ),
    ]
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


class Api:
    """Scriptable call_api / call_api_parallel."""

    def __init__(self, roster=ROSTER, clockins=CLOCKINS):
        self.roster = roster
        self.clockins = clockins
        self.logs = []
        self.resolved = {
            "window": {
                "start": "2026-08-10T07:00:00+12:00",
                "end": "2026-08-17T06:59:59+12:00",
                "trading_aligned": True,
                "description": "the business week",
            }
        }

    def _for(self, action):
        if action == "get_roster":
            return self.roster
        if action == "get_timeclock_entries":
            return self.clockins
        if action == "resolve_dates":
            # Echo a window for either a period phrase or an explicit range —
            # trading_aligned unless a test overrides it.
            return dict(self.resolved)
        raise AssertionError(f"unexpected action {action}")

    def call_api(self, connector, action, params=None):
        return self._for(action)

    def call_api_parallel(self, calls):
        return [self._for(a) for (_c, a, _p) in calls]

    def log(self, m):
        self.logs.append(str(m))


def run_fn(api, group_by):
    ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(FUNCTION_CODE, ns)
    params = {
        "venue": "Bessie & Engineers",
        "start_datetime": START,
        "end_datetime": END,
        "group_by": group_by,
    }
    return ns["run"](params, api.call_api, api.log, api.call_api_parallel)


class TestDetailView:
    def test_leave_split_out_of_clockins(self):
        out = run_fn(Api(), "detail")
        assert [r["name"] for r in out["clockins"]] == ["Zoe Kite", "Evelyn Clarke"]
        assert [r["name"] for r in out["leave"]] == ["Aliana Henderson", "Chloe Ward"]
        # The synthetic leave times are kept on the row for reference
        assert out["leave"][0]["start"] == "7:00 AM"
        assert out["leave"][0]["hours"] == 8.0

    def test_totals_count_only_worked_hours(self):
        out = run_fn(Api(), "detail")
        t = out["totals"]
        assert t["actual_hours"] == 9.2
        assert t["actual_cost"] == 210.0
        assert t["rostered_hours"] == 7.75
        assert t["variance"] == 1.45
        assert t["leave_hours"] == 16.0
        assert t["leave_cost"] == 404.0

    def test_no_leave_no_leave_fields(self):
        api = Api(clockins=[c for c in CLOCKINS if c["type"] == "Clockin"])
        out = run_fn(api, "detail")
        assert out["leave"] == []
        assert "leave_hours" not in out["totals"]


class TestStaffView:
    def test_leave_never_counts_as_actual(self):
        out = run_fn(Api(), "staff")
        by_name = {r["name"]: r for r in out["rows"]}
        aliana = by_name["Aliana Henderson"]
        assert aliana["actual_hours"] == 0.0
        assert aliana["leave_hours"] == 8.0
        assert aliana["variance"] == 0.0
        assert by_name["Evelyn Clarke"]["actual_hours"] == 5.2
        assert "leave_hours" not in by_name["Evelyn Clarke"]

    def test_totals(self):
        out = run_fn(Api(), "staff")
        assert out["totals"]["actual_hours"] == 9.2
        assert out["totals"]["leave_hours"] == 16.0
        assert out["totals"]["variance"] == 1.45


class TestDayView:
    def test_unrostered_ignores_leave(self):
        out = run_fn(Api(), "day")
        assert len(out["rows"]) == 1
        day = out["rows"][0]
        # Only Zoe Kite (a real clock-in with no roster) is unrostered — the two
        # leave entries are absences, not surprise shifts.
        assert day["unrostered"] == 1
        assert day["actual_hours"] == 9.2
        assert day["leave_hours"] == 16.0
        assert day["variance"] == 1.45

    def test_no_leave_day_has_no_leave_fields(self):
        api = Api(clockins=[c for c in CLOCKINS if c["type"] == "Clockin"])
        out = run_fn(api, "day")
        assert "leave_hours" not in out["rows"][0]
        assert "leave_hours" not in out["totals"]


class TestSandbox:
    def test_code_runs_under_the_real_sandbox_namespace(self):
        ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
        exec(FUNCTION_CODE, ns)
        assert callable(ns["run"])


class TestPeriodResolution:
    def _run(self, api, **params):
        ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
        exec(FUNCTION_CODE, ns)
        return ns["run"](
            {"venue": "Bessie & Engineers", **params},
            api.call_api,
            api.log,
            api.call_api_parallel,
        )

    def test_period_resolves_and_fetches_the_window(self):
        api = Api()
        out = self._run(api, period="last week", group_by="staff")
        assert "error" not in out
        # the resolved trading window reached the fetches, not raw params
        assert out["window"]["start"].startswith("2026-08-10")

    def test_explicit_unaligned_range_asks_first(self):
        api = Api()
        api.resolved = {
            "window": {
                "start": "2026-08-10T00:00:00+12:00",
                "end": "2026-08-17T00:00:00+12:00",
                "trading_aligned": False,
                "description": "Custom window - splits a trading session.",
            }
        }
        out = self._run(
            api,
            start_datetime="2026-08-10T00:00:00+12:00",
            end_datetime="2026-08-17T00:00:00+12:00",
        )
        assert out.get("needs_confirmation") is True
        assert "confirmed_by_user" in out["question"]

    def test_confirmed_unaligned_range_is_honoured(self):
        api = Api()
        api.resolved = {
            "window": {
                "start": "2026-08-10T00:00:00+12:00",
                "end": "2026-08-17T00:00:00+12:00",
                "trading_aligned": False,
            }
        }
        out = self._run(
            api,
            start_datetime="2026-08-10T00:00:00+12:00",
            end_datetime="2026-08-17T00:00:00+12:00",
            confirmed_by_user=True,
        )
        assert "needs_confirmation" not in out
        assert "error" not in out

    def test_no_period_and_no_range_is_refused(self):
        out = self._run(Api())
        assert "period" in out["error"]
