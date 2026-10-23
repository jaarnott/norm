"""The Finance config layer — get_payrun, get_closing_manager, set_budgets
(both rows) and get_weekly_financials — exec'd under the REAL sandbox
namespace (tests/consolidator_harness.py) over faked endpoint payloads shaped
like the live ones (La Zeppa, 20 Oct 2026: the payroll clock-in feed, the
pay summary, wages by role, cash-ups, voids, the budget rows).

`norm.resolve_dates` is faked with the real zoneinfo conversion, so the
budget write's offset probes and the trading-day bucketing are exercised
across the actual NZ clock change (Sun 27 Sep 2026, +12:00 → +13:00) and
not against a constant.

Plan: docs/plans/finance-migration.md §7 (7.1), §9, §11.7, §17.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import pathlib
from zoneinfo import ZoneInfo

import pytest

from app.connectors.function_executor import (
    PreviewReady,
    _reject_unsafe_source,
    fingerprint,
)
from tests import consolidator_harness as harness

NZ = ZoneInfo("Pacific/Auckland")
_DIR = harness.CONSOLIDATORS

PAYRUN = harness.source("get_payrun")
CLOSING = harness.source("get_closing_manager")
WEEKLY = harness.source("get_weekly_financials")
COMMON = harness.source("set_budgets_common")
SET_BUDGETS = COMMON + "\n\n" + harness.source("set_budgets")
SET_BUDGETS_TYPED = COMMON + "\n\n" + harness.source("set_budgets_typed")

VENUES = {
    "venues": [
        {"id": "v-laz", "name": "La Zeppa", "connected": True},
        {"id": "v-dsc", "name": "Dunedin Social Club", "connected": True},
        {"id": "v-off", "name": "Mr Murdochs", "connected": False},
    ]
}
DAY_START = {"v-laz": "07:00", "v-dsc": "06:00"}


# ── a resolve_dates that behaves like the real one ────────────────────────


def _local(date: dt.date, hhmm: str, tz=NZ) -> dt.datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return dt.datetime(date.year, date.month, date.day, h, m, tzinfo=tz)


def fake_resolve_dates(params: dict) -> dict:
    """Mon-based weeks, "D1 to D2" ranges, months, single dates, and explicit
    instants — each end with the offset the zone gives it at that instant."""
    venue_id = params.get("venue_id")
    ds = DAY_START.get(venue_id, "07:00")
    query = str(params.get("query") or "").strip()
    if params.get("start") and params.get("end"):
        s = dt.datetime.fromisoformat(params["start"]).astimezone(NZ)
        e = dt.datetime.fromisoformat(params["end"]).astimezone(NZ)
        return _window(s, e, ds, "custom")
    if query.startswith("week beginning "):
        monday = dt.date.fromisoformat(query[len("week beginning ") :])
        s = _local(monday, ds)
        e = _local(monday + dt.timedelta(days=7), ds) - dt.timedelta(seconds=1)
        return _window(s, e, ds, "week")
    if " to " in query:
        a, b = query.split(" to ")
        s = _local(dt.date.fromisoformat(a), ds)
        e = _local(dt.date.fromisoformat(b) + dt.timedelta(days=1), ds) - dt.timedelta(
            seconds=1
        )
        return _window(s, e, ds, "range")
    months = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]  # fmt: skip
    parts = query.split()
    if len(parts) == 2 and parts[0] in months:
        m, y = months.index(parts[0]) + 1, int(parts[1])
        first = dt.date(y, m, 1)
        nxt = dt.date(y + (m == 12), (m % 12) + 1, 1)
        return _window(
            _local(first, ds), _local(nxt, ds) - dt.timedelta(seconds=1), ds, "month"
        )
    d = dt.date.fromisoformat(query)
    return _window(
        _local(d, ds),
        _local(d + dt.timedelta(days=1), ds) - dt.timedelta(seconds=1),
        ds,
        "day",
    )


def _window(s, e, ds, kind):
    return {
        "success": True,
        "data": {
            "window": {
                "start": s.isoformat(),
                "end": e.isoformat(),
                "kind": kind,
                "timezone": "Pacific/Auckland",
                "day_start": ds,
                "trading_aligned": True,
                "description": kind,
            }
        },
    }


class FakeApi:
    """Scripted endpoints: a payload, or a callable of the params."""

    def __init__(self, responses: dict):
        self.responses = dict(responses)
        self.responses.setdefault("norm.resolve_dates", fake_resolve_dates)
        self.responses.setdefault("norm.list_venues", VENUES)
        self.calls: list[tuple[str, str, dict]] = []

    def call_api(self, connector, action, params):
        self.calls.append((connector, action, dict(params or {})))
        key = f"{connector}.{action}"
        if key not in self.responses:
            # What the real door says for a row that isn't installed.
            return {"error": f"Tool not found: {key}"}
        r = self.responses[key]
        return copy.deepcopy(r(params) if callable(r) else r)

    def call_api_parallel(self, calls):
        return [self.call_api(c, a, p) for (c, a, p) in calls]

    def count(self, action=None):
        return len([c for c in self.calls if action is None or c[1] == action])


# ── live-shaped payloads ──────────────────────────────────────────────────


def shift(
    sid,
    first,
    last,
    start,
    end,
    *,
    hours,
    rate,
    kind="Clockin",
    role=("r-floor", "FLOOR TEAM"),
    pay_code="PC1",
    staff="s1",
    approved=True,
    deleted=None,
    other=False,
    venue_id="c-laz",
    venue_name="La Zeppa",
    rules=None,
    remuneration="HourlyRate",
    leave=None,
):
    total = round(hours * rate, 2)
    lines = (
        [
            {
                "title": "Ordinary Pay",
                "hoursWorked": hours,
                "payRate": rate,
                "total": total,
                "periodStart": start,
                "periodEnd": end,
                "isOrdinaryPay": True,
                "includeInPayrollExport": True,
                "order": 1,
            }
        ]
        if kind != "Leave"
        else []
    )
    row = {
        "id": sid,
        "type": kind,
        "remunerationType": remuneration,
        "posIdentifier": None,
        "staffMemberId": staff,
        "staffMemberFirstName": first,
        "staffMemberLastName": last,
        "staffMemberPayCode": pay_code,
        "roleId": role[0],
        "roleName": role[1],
        "hourlyRate": rate,
        "adjustedHourlyRate": rate,
        "adjustedHourlyRatePrecise": rate,
        "clockinTime": start,
        "clockoutTime": end,
        "datestampCreated": start,
        "datestampDeleted": deleted,
        "datestampApproved": start if approved else None,
        "isFinalised": False,
        "isCompanyClockin": False,
        "breaks": [],
        "payRateBreakdown": lines,
        "totalHours": hours,
        "totalCost": total,
        "payrollExportTotal": total,
        "showOnFinancialReports": True,
        "rules": rules or [],
        "venueId": venue_id,
        "venueFullName": venue_name,
        "venueShortName": venue_name,
        "isFromOtherVenue": other,
    }
    if leave:
        row["leaveRequestId"], row["leaveTypeName"] = leave
        row["leaveTypeRef"] = "M:x"
    return row


ROLES = [
    {"id": "r-floor", "name": "FLOOR TEAM", "costGroup": "b", "deptCode": None},
    {"id": "r-kitchen", "name": "KITCHEN", "costGroup": "f", "deptCode": None},
    {"id": "r-sm", "name": "SALES MANAGER", "costGroup": "b", "deptCode": None},
]
PH_RULE = {
    "id": "rule-1",
    "ruleName": "Public Holiday",
    "ruleRate": 1.5,
    "totalHours": 4.0,
    "processedTotalHours": 4.0,
    "processedRuleCost": 60.0,
    "payCode": "PublicHoliday",
}

# Pay week ending Sun 18 Oct 2026 (NZDT, +13:00).
CLOCKINS = [
    shift(
        "sh-1",
        "Ada",
        "Lovelace",
        "2026-10-13T17:00:00+13:00",
        "2026-10-13T23:30:00+13:00",
        hours=6.5,
        rate=30.0,
    ),
    # Clocked out at 01:00 Wed: still Tuesday's trading day (day start 07:00).
    shift(
        "sh-2",
        "Ada",
        "Lovelace",
        "2026-10-14T18:00:00+13:00",
        "2026-10-15T01:00:00+13:00",
        hours=7.0,
        rate=30.0,
        rules=[PH_RULE],
    ),
    shift(
        "sh-3",
        "Bob",
        "Marley",
        "2026-10-15T09:00:00+13:00",
        "2026-10-15T15:00:00+13:00",
        hours=6.0,
        rate=28.0,
        role=("r-kitchen", "KITCHEN"),
        pay_code="PC2",
        staff="s2",
        approved=False,
    ),
    # Worked at the Glass Goose, paid by La Zeppa.
    shift(
        "sh-4",
        "Bob",
        "Marley",
        "2026-10-16T09:00:00+13:00",
        "2026-10-16T14:00:00+13:00",
        hours=5.0,
        rate=28.0,
        role=("r-gg-k", "KITCHEN"),
        pay_code="PC2",
        staff="s2",
        other=True,
        venue_id="c-gg",
        venue_name="Glass Goose",
    ),
    # Security contractor.
    shift(
        "sh-5",
        "Sam",
        "Guard",
        "2026-10-17T21:00:00+13:00",
        "2026-10-18T03:00:00+13:00",
        hours=6.0,
        rate=35.0,
        pay_code="SECURITY",
        staff="s3",
    ),
    # No pay code.
    shift(
        "sh-6",
        "Nina",
        "New",
        "2026-10-17T10:00:00+13:00",
        "2026-10-17T14:00:00+13:00",
        hours=4.0,
        rate=25.0,
        pay_code=None,
        staff="s4",
    ),
    # Salaried default hours.
    shift(
        "sh-7",
        "Cal",
        "Chef",
        "2026-10-13T08:00:00+13:00",
        "2026-10-13T16:00:00+13:00",
        hours=8.0,
        rate=40.0,
        kind="DefaultHours",
        role=("r-kitchen", "KITCHEN"),
        pay_code="PC5",
        staff="s5",
        remuneration="Salary",
    ),
    # Leave: Loaded's pseudo-shift, no breakdown lines, cost in totalCost.
    shift(
        "sh-8",
        "Ada",
        "Lovelace",
        "2026-10-16T07:00:00+13:00",
        "2026-10-16T15:00:00+13:00",
        hours=8.0,
        rate=30.0,
        kind="Leave",
        leave=("lr-1", "New Zealand Annual Leave"),
    ),
    # Deleted (stale, stamped in UTC) and unfinished: dropped and counted.
    shift(
        "sh-9",
        "Old",
        "Timer",
        "2024-03-01T22:10:00+00:00",
        None,
        hours=0,
        rate=30.0,
        deleted="2024-03-02T00:00:00+00:00",
        staff="s9",
    ),
    shift(
        "sh-10",
        "Ada",
        "Lovelace",
        "2026-10-18T17:00:00+13:00",
        None,
        hours=0,
        rate=30.0,
    ),
]


def pay_summary(total_wages, hours_worked, leave_pay=240.0, all_approved=True):
    return {
        "staff": [
            {
                "staffMemberId": "s1",
                "name": "x",
                "payCode": "PC1",
                "hoursRostered": 10,
                "hoursWorked": 13.5,
                "totalWages": 405.0,
                "allApproved": True,
                "isLinkedToCurrentVenue": True,
                "clockins": [],
            },
            {
                "staffMemberId": "s2",
                "name": "x",
                "payCode": "PC2",
                "hoursRostered": 10,
                "hoursWorked": 11,
                "totalWages": 308.0,
                "allApproved": all_approved,
                "isLinkedToCurrentVenue": True,
                "clockins": [],
            },
        ],
        "totalsByRole": [],
        "totalsByPayAdjustment": [],
        "total": {
            "hoursRostered": 20.0,
            "hoursWorked": hours_worked,
            "totalWages": total_wages,
        },
        "leave": [
            {
                "leaveRequestId": "lr-1",
                "staffMemberId": "s1",
                "name": "x",
                "payCode": "PC1",
                "isLinkedToCurrentVenue": True,
                "leaveTypeName": "New Zealand Annual Leave",
                "startTime": "2026-10-16T07:00:00+13:00",
                "hours": 8,
                "totalPay": leave_pay,
            }
        ],
        "leaveTotal": {"hours": 8, "totalPay": leave_pay},
    }


# Σ worked rows (sh-1..sh-7): 195 + 210 + 168 + 140 + 210 + 100 + 320 = 1343.
WORKED_WAGES = 1343.0
WORKED_HOURS = 42.5
LEAVE_TYPES = [
    {
        "id": "M:a",
        "name": "New Zealand Annual Leave",
        "isPaid": True,
        "percentage": 100,
    },
    {"id": "M:u", "name": "Unpaid Leave", "isPaid": False, "percentage": 0},
]
STAFF = [
    {
        "id": "s1",
        "name": "x",
        "email": "ada@example.test",
        "remunerationTimeline": [{"id": "t1"}],
    },
    {"id": "s2", "name": "x", "email": None, "remunerationTimeline": [{"id": "t2"}]},
    {"id": "s4", "name": "x", "email": "nina@example.test", "remunerationTimeline": []},
]


def payrun_api(**over):
    responses = {
        "loadedhub.get_payrun_clockins_raw": CLOCKINS,
        "loadedhub.get_pay_summary_raw": pay_summary(WORKED_WAGES, WORKED_HOURS),
        "loadedhub.get_staff_roles": ROLES,
        "loadedhub.get_staff_members": STAFF,
        "loadedhub.get_leave_types": LEAVE_TYPES,
    }
    responses.update(over)
    return FakeApi(responses)


class TestGetPayrun:
    def test_rows_are_pay_shift_facts_with_drops_counted(self):
        api = payrun_api()
        out = harness.run(
            PAYRUN, api, venue="La Zeppa", week_ending="2026-10-18", view="all"
        )
        assert "error" not in out
        assert out["week_start"] == "2026-10-12" and out["week_ending"] == "2026-10-18"
        assert out["window"]["start"] == "2026-10-12T07:00:00+13:00"
        assert out["window"]["end"] == "2026-10-19T06:59:59+13:00"
        assert out["dropped"] == {"deleted": 1, "unfinished": 1}
        assert out["dropped_unfinished"][0]["staff_name"] == "Ada Lovelace"
        by_id = {r["source_shift_id"]: r for r in out["rows"]}
        assert set(by_id) == {f"sh-{i}" for i in range(1, 9)}
        r1 = by_id["sh-1"]
        assert r1["staff_name"] == "Ada Lovelace" and r1["pay_code"] == "PC1"
        assert r1["cost_cents"] == 19500 and r1["hours"] == 6.5
        assert r1["cost_group"] == "bar_floor" and r1["trading_date"] == "2026-10-13"
        assert r1["is_approved"] is True and r1["breakdown"]["pay_rate_breakdown"]
        # The PH premium rides on top of cost, from Loaded's rules.
        r2 = by_id["sh-2"]
        assert r2["ph_cost_cents"] == 6000 and r2["ph_hours"] == 4.0
        assert r2["trading_date"] == "2026-10-14"
        # Kitchen role → kitchen; another venue's role → other, flagged by id.
        assert by_id["sh-3"]["cost_group"] == "kitchen"
        assert by_id["sh-3"]["is_approved"] is False
        r4 = by_id["sh-4"]
        assert r4["is_from_other_venue"] is True
        assert r4["worked_venue_loaded_id"] == "c-gg" and r4["cost_group"] == "other"
        # Leave keeps its request and type, costed from totalCost.
        r8 = by_id["sh-8"]
        assert r8["shift_type"] == "Leave" and r8["leave_request_id"] == "lr-1"
        assert r8["cost_cents"] == 24000 and r8["is_approved"] is True
        assert by_id["sh-7"]["remuneration_type"] == "Salary"
        # The feed call carried the fixed types fragment and no staff filter.
        feed = next(c for c in api.calls if c[1] == "get_payrun_clockins_raw")
        assert feed[2]["venue"] == "La Zeppa"
        assert feed[2]["q"].count("shiftTypesToInclude=") == 4
        assert "staffMemberIds" not in feed[2]["q"]
        assert api.count() <= 8

    def test_parity_line_against_loaded_pay_summary(self):
        out = harness.run(
            PAYRUN, payrun_api(), venue="La Zeppa", week_ending="2026-10-18"
        )
        p = out["parity"]
        assert p["rows_wages"] == WORKED_WAGES and p["difference"] == 0.0
        assert p["rows_hours"] == WORKED_HOURS
        assert p["rows_leave"] == 240.0 and p["leave_difference"] == 0.0
        assert p["within_tolerance"] is True and p["all_approved"] is True

    def test_parity_flags_a_mismatch(self):
        api = payrun_api(
            **{
                "loadedhub.get_pay_summary_raw": pay_summary(
                    1200.0, 40.0, all_approved=False
                )
            }
        )
        out = harness.run(PAYRUN, api, venue="La Zeppa", week_ending="2026-10-18")
        p = out["parity"]
        assert p["difference"] == 143.0 and p["within_tolerance"] is False
        assert p["all_approved"] is False

    def test_staff_view_excludes_leave_and_security(self):
        out = harness.run(
            PAYRUN, payrun_api(), venue="La Zeppa", week_ending="2026-10-18"
        )
        assert out["view"] == "staff"
        ids = {r["source_shift_id"] for r in out["rows"]}
        assert "sh-5" not in ids and "sh-8" not in ids and "sh-1" in ids
        people = {p["staff_name"]: p for p in out["sections"]["staff"]["rows"]}
        ada = people["Ada Lovelace"]
        assert (
            ada["shifts"] == 2 and ada["hours"] == 13.5 and ada["cost_cents"] == 40500
        )
        assert ada["ph_cost_cents"] == 6000 and ada["all_approved"] is True
        assert people["Bob Marley"]["other_venue_hours"] == 5.0
        assert people["Bob Marley"]["all_approved"] is False
        assert out["totals"]["security_cost_cents"] == 21000
        assert out["totals"]["leave_cost_cents"] == 24000
        assert "leave" not in out["sections"]

    def test_security_leave_salaried_and_hygiene_views(self):
        sec = harness.run(
            PAYRUN,
            payrun_api(),
            venue="La Zeppa",
            week_ending="2026-10-18",
            view="security",
        )
        assert [r["source_shift_id"] for r in sec["rows"]] == ["sh-5"]
        assert sec["sections"]["security"]["pay_code"] == "SECURITY"
        leave = harness.run(
            PAYRUN,
            payrun_api(),
            venue="La Zeppa",
            week_ending="2026-10-18",
            view="leave",
        )
        row = leave["sections"]["leave"]["rows"][0]
        assert row["leave_request_id"] == "lr-1" and row["is_paid_type"] is True
        assert row["dates"] == ["2026-10-16"] and row["cost_cents"] == 24000
        sal = harness.run(
            PAYRUN,
            payrun_api(),
            venue="La Zeppa",
            week_ending="2026-10-18",
            view="salaried",
        )
        assert [p["staff_name"] for p in sal["sections"]["salaried"]["rows"]] == [
            "Cal Chef"
        ]
        assert (
            sal["sections"]["salaried"]["rows"][0]["by_shift_type"]["DefaultHours"][
                "hours"
            ]
            == 8.0
        )
        hyg = harness.run(
            PAYRUN,
            payrun_api(),
            venue="La Zeppa",
            week_ending="2026-10-18",
            view="hygiene",
        )
        h = hyg["sections"]["hygiene"]
        assert [u["staff_name"] for u in h["unapproved_clockins"]] == ["Bob Marley"]
        assert h["staff_without_pay_code"] == ["Nina New"]
        assert h["staff_without_timeline"] == ["Nina New"]
        assert h["public_holiday_dates"] == ["2026-10-14"]
        assert h["salaried_count"] == 1 and h["parity"]["within_tolerance"] is True
        assert len(h["unfinished_clockins"]) == 1

    def test_rows_false_keeps_the_sections_only(self):
        out = harness.run(
            PAYRUN,
            payrun_api(),
            venue="La Zeppa",
            week_ending="2026-10-18",
            rows="false",
        )
        assert out["rows"] == [] and out["row_count"] == 6
        assert out["sections"]["staff"]["totals"]["cost_cents"] == 113300

    def test_any_date_snaps_to_its_pay_week_and_says_so(self):
        out = harness.run(
            PAYRUN, payrun_api(), venue="La Zeppa", week_ending="2026-10-14"
        )
        assert out["week_ending"] == "2026-10-18"
        assert any("not a Sunday" in n for n in out["notes"])

    def test_staff_ids_and_weeks_widen_the_feed(self):
        api = payrun_api()
        out = harness.run(
            PAYRUN,
            api,
            venue="La Zeppa",
            week_ending="2026-10-18",
            weeks="4",
            staff_member_ids='["s1","s2"]',
        )
        assert out["week_start"] == "2026-09-21"
        feed = next(c for c in api.calls if c[1] == "get_payrun_clockins_raw")
        assert feed[2]["q"].endswith("&staffMemberIds=s1&staffMemberIds=s2")
        # Four weeks back crosses the clock change: the start is NZST.
        assert feed[2]["start_time"] == "2026-09-21T07:00:00+12:00"
        assert feed[2]["end_time"] == "2026-10-19T06:59:59+13:00"

    def test_a_venue_id_becomes_the_name_the_rows_need(self):
        api = payrun_api()
        out = harness.run(PAYRUN, api, venue_id="v-laz", week_ending="2026-10-18")
        assert out["venue"] == "La Zeppa" and out["venue_id"] == "v-laz"
        assert (
            next(c for c in api.calls if c[1] == "get_pay_summary_raw")[2]["venue"]
            == "La Zeppa"
        )

    def test_a_failed_feed_is_an_error_not_an_empty_week(self):
        api = payrun_api(
            **{"loadedhub.get_payrun_clockins_raw": {"error": "API error 500"}}
        )
        out = harness.run(PAYRUN, api, venue="La Zeppa", week_ending="2026-10-18")
        assert "clock-ins" in out["error"] and "500" in out["error"]

    def test_refusals(self):
        api = payrun_api()
        assert "week_ending" in harness.run(PAYRUN, api, venue="La Zeppa")["error"]
        assert "venue" in harness.run(PAYRUN, api, week_ending="2026-10-18")["error"]
        assert (
            "view"
            in harness.run(
                PAYRUN, api, venue="La Zeppa", week_ending="2026-10-18", view="x"
            )["error"]
        )
        assert (
            "intercompany"
            in harness.run(PAYRUN, api, venues="all", week_ending="2026-10-18")["error"]
        )

    def test_intercompany_across_every_entity_nets_in_and_out(self):
        laz = [
            shift(
                "a1",
                "A",
                "A",
                "2026-10-13T17:00:00+13:00",
                "2026-10-13T22:00:00+13:00",
                hours=5.0,
                rate=20.0,
                venue_id="c-laz",
                venue_name="La Zeppa",
            ),
            shift(
                "a2",
                "A",
                "A",
                "2026-10-14T17:00:00+13:00",
                "2026-10-14T22:00:00+13:00",
                hours=5.0,
                rate=20.0,
                other=True,
                venue_id="c-dsc",
                venue_name="Dunedin Social Club",
            ),
        ]
        dsc = [
            shift(
                "b1",
                "B",
                "B",
                "2026-10-13T17:00:00+13:00",
                "2026-10-13T22:00:00+13:00",
                hours=5.0,
                rate=30.0,
                staff="s7",
                venue_id="c-dsc",
                venue_name="Dunedin Social Club",
            ),
            shift(
                "b2",
                "B",
                "B",
                "2026-10-15T17:00:00+13:00",
                "2026-10-15T19:00:00+13:00",
                hours=2.0,
                rate=30.0,
                staff="s7",
                other=True,
                venue_id="c-laz",
                venue_name="La Zeppa",
            ),
        ]

        def feed(params):
            return laz if params.get("venue") == "La Zeppa" else dsc

        api = payrun_api(**{"loadedhub.get_payrun_clockins_raw": feed})
        out = harness.run(
            PAYRUN, api, venues="all", week_ending="2026-10-18", view="intercompany"
        )
        assert out["venues"] == ["La Zeppa", "Dunedin Social Club"]
        assert {r["source_shift_id"] for r in out["rows"]} == {"a2", "b2"}
        assert {r["payroll_venue"] for r in out["rows"]} == {
            "La Zeppa",
            "Dunedin Social Club",
        }
        pairs = {
            (p["payroll_venue"], p["counterparty"]): p
            for p in out["sections"]["intercompany"]["by_counterparty"]
        }
        laz_dsc = pairs[("La Zeppa", "Dunedin Social Club")]
        assert laz_dsc["in_cents"] == 10000 and laz_dsc["out_cents"] == 6000
        assert laz_dsc["net_cents"] == 4000
        dsc_laz = pairs[("Dunedin Social Club", "La Zeppa")]
        assert dsc_laz["in_cents"] == 6000 and dsc_laz["out_cents"] == 10000
        assert dsc_laz["net_cents"] == -4000
        # list_venues, resolve_dates, then a feed and a roles read per entity.
        assert api.count() == 2 + 2 * 2
        assert api.count("get_pay_summary_raw") == 0


# ── get_closing_manager ──────────────────────────────────────────────────


def clockin(sid, staff, first, last, role, start, end, deleted=None, kind="Clockin"):
    return {
        "id": sid,
        "type": kind,
        "staffMemberId": staff,
        "staffMemberFirstName": first,
        "staffMemberLastName": last,
        "roleName": role,
        "clockinTime": start,
        "clockoutTime": end,
        "datestampDeleted": deleted,
    }


def roster(shifts):
    return [{"id": "roster-1", "rosteredShifts": shifts}]


STAFF_EMAILS = [
    {"id": "m1", "name": "x", "email": "m1@example.test"},
    {"id": "m2", "name": "x", "email": "m2@example.test"},
    {"id": "m3", "name": "x", "email": None},
]


class TestGetClosingManager:
    def test_clockin_beats_roster_and_the_latest_end_wins(self):
        api = FakeApi(
            {
                "loadedhub.get_timeclock_entries": [
                    clockin(
                        "c1",
                        "m1",
                        "Mia",
                        "One",
                        "SALES MANAGER",
                        "2026-10-17T15:00:00+13:00",
                        "2026-10-17T23:00:00+13:00",
                    ),
                    # Ends later — the closer — though she started earlier.
                    clockin(
                        "c2",
                        "m2",
                        "Zoe",
                        "Two",
                        "SALES MANAGER",
                        "2026-10-17T14:00:00+13:00",
                        "2026-10-18T01:30:00+13:00",
                    ),
                    clockin(
                        "c3",
                        "m3",
                        "Pat",
                        "Floor",
                        "FLOOR TEAM",
                        "2026-10-17T16:00:00+13:00",
                        "2026-10-18T02:00:00+13:00",
                    ),
                ],
                "loadedhub.get_roster": roster(
                    [
                        clockin(
                            "r1",
                            "m1",
                            "Mia",
                            "One",
                            "Sales Manager",
                            "2026-10-17T15:00:00+13:00",
                            "2026-10-17T23:00:00+13:00",
                            kind="Roster",
                        )
                    ]
                ),
                "loadedhub.get_staff_members": STAFF_EMAILS,
            }
        )
        out = harness.run(
            CLOSING, api, venue="La Zeppa", venue_id="v-laz", dates=["2026-10-17"]
        )
        pick = out["by_date"]["2026-10-17"]
        assert pick["name"] == "Zoe Two" and pick["staff_member_id"] == "m2"
        assert pick["email"] == "m2@example.test" and pick["source"] == "clockin"
        assert pick["clockout_at"] == "2026-10-18T01:30:00+13:00"
        assert out["counts"] == {"clockin": 1, "both": 0, "rostered_only": 0, "none": 0}
        assert api.count() == 4
        # The range resolved once, through the venue's own calendar.
        rd = next(c for c in api.calls if c[1] == "resolve_dates")
        assert rd[2] == {"query": "2026-10-17 to 2026-10-17", "venue_id": "v-laz"}

    def test_rostered_only_fallback_both_and_none(self):
        api = FakeApi(
            {
                "loadedhub.get_timeclock_entries": [
                    clockin(
                        "c1",
                        "m1",
                        "Mia",
                        "One",
                        " sales manager ",
                        "2026-10-17T15:00:00+13:00",
                        "2026-10-17T23:00:00+13:00",
                    ),
                ],
                "loadedhub.get_roster": roster(
                    [
                        clockin(
                            "r1",
                            "m1",
                            "Mia",
                            "One",
                            "Sales Manager",
                            "2026-10-17T15:00:00+13:00",
                            "2026-10-17T23:00:00+13:00",
                            kind="Roster",
                        ),
                        clockin(
                            "r2",
                            "m2",
                            "Zoe",
                            "Two",
                            "Sales Manager",
                            "2026-10-18T15:00:00+13:00",
                            "2026-10-18T22:00:00+13:00",
                            kind="Roster",
                        ),
                        clockin(
                            "r3",
                            "m3",
                            "Pat",
                            "Gone",
                            "Sales Manager",
                            "2026-10-18T15:00:00+13:00",
                            "2026-10-19T01:00:00+13:00",
                            kind="Roster",
                            deleted="2026-10-10T00:00:00+13:00",
                        ),
                    ]
                ),
                "loadedhub.get_staff_members": STAFF_EMAILS,
            }
        )
        out = harness.run(
            CLOSING,
            api,
            venue="La Zeppa",
            venue_id="v-laz",
            dates='["2026-10-17","2026-10-18","2026-10-19"]',
        )
        assert out["by_date"]["2026-10-17"]["source"] == "both"
        d18 = out["by_date"]["2026-10-18"]
        # The deleted later shift is skipped; the live rostered one stands.
        assert d18["name"] == "Zoe Two" and d18["source"] == "rostered_only"
        assert out["by_date"]["2026-10-19"] is None
        assert out["counts"] == {"clockin": 0, "both": 1, "rostered_only": 1, "none": 1}

    @pytest.mark.parametrize(
        "venue_id, day_start, after_midnight_date",
        [("v-laz", "07:00", "2026-10-17"), ("v-dsc", "06:00", "2026-10-17")],
    )
    def test_a_shift_after_midnight_belongs_to_the_previous_trading_day(
        self, venue_id, day_start, after_midnight_date
    ):
        api = FakeApi(
            {
                "loadedhub.get_timeclock_entries": [
                    # Started 00:30 Sunday: Saturday's trading day at both venues.
                    clockin(
                        "c1",
                        "m1",
                        "Mia",
                        "One",
                        "SALES MANAGER",
                        "2026-10-18T00:30:00+13:00",
                        "2026-10-18T03:00:00+13:00",
                    ),
                    # Started at the day start: Sunday's.
                    clockin(
                        "c2",
                        "m2",
                        "Zoe",
                        "Two",
                        "SALES MANAGER",
                        f"2026-10-18T{day_start}:00+13:00",
                        "2026-10-18T12:00:00+13:00",
                    ),
                ],
                "loadedhub.get_roster": roster([]),
                "loadedhub.get_staff_members": STAFF_EMAILS,
            }
        )
        out = harness.run(
            CLOSING,
            api,
            venue="x",
            venue_id=venue_id,
            dates=["2026-10-17", "2026-10-18"],
        )
        assert out["window"]["day_start"] == day_start
        assert out["by_date"][after_midnight_date]["name"] == "Mia One"
        assert out["by_date"]["2026-10-18"]["name"] == "Zoe Two"

    def test_across_the_clock_change_a_utc_stamped_roster_still_buckets_right(self):
        # Sat 26 Sep 2026 is NZST (+12); Sun 27 Sep 03:00 onward is NZDT (+13).
        api = FakeApi(
            {
                "loadedhub.get_timeclock_entries": [],
                "loadedhub.get_roster": roster(
                    [
                        # 26 Sep 23:00 NZST = 11:00Z; ends 27 Sep 01:30 NZST = 13:30Z → Saturday.
                        clockin(
                            "r1",
                            "m1",
                            "Mia",
                            "One",
                            "Sales Manager",
                            "2026-09-26T11:00:00+00:00",
                            "2026-09-26T13:30:00+00:00",
                            kind="Roster",
                        ),
                        # 27 Sep 20:00 NZDT = 07:00Z; ends 28 Sep 00:30 NZDT = 11:30Z → Sunday.
                        clockin(
                            "r2",
                            "m2",
                            "Zoe",
                            "Two",
                            "Sales Manager",
                            "2026-09-27T07:00:00+00:00",
                            "2026-09-27T11:30:00+00:00",
                            kind="Roster",
                        ),
                    ]
                ),
                "loadedhub.get_staff_members": STAFF_EMAILS,
            }
        )
        out = harness.run(
            CLOSING,
            api,
            venue="La Zeppa",
            venue_id="v-laz",
            dates=["2026-09-26", "2026-09-27"],
        )
        assert out["window"]["start"].endswith("+12:00") and out["window"][
            "end"
        ].endswith("+13:00")
        assert out["by_date"]["2026-09-26"]["name"] == "Mia One"
        assert (
            out["by_date"]["2026-09-26"]["clockout_at"] == "2026-09-27T01:30:00+12:00"
        )
        assert out["by_date"]["2026-09-27"]["name"] == "Zoe Two"
        assert (
            out["by_date"]["2026-09-27"]["clockout_at"] == "2026-09-28T00:30:00+13:00"
        )

    def test_refusals_and_a_failed_clockin_read(self):
        api = FakeApi({"loadedhub.get_timeclock_entries": {"error": "boom"}})
        assert "dates" in harness.run(CLOSING, api, venue="La Zeppa")["error"]
        too_many = [f"2026-10-{d:02d}" for d in range(1, 32)] + ["2026-11-01"]
        assert (
            "31" in harness.run(CLOSING, api, venue="La Zeppa", dates=too_many)["error"]
        )
        assert (
            "clock-ins"
            in harness.run(CLOSING, api, venue="La Zeppa", dates=["2026-10-17"])[
                "error"
            ]
        )


# ── set_budgets ──────────────────────────────────────────────────────────


def budget_row(trading_date: str, amount: float, bid: str | None = None):
    """A Loaded row for a trading day: stored at the NEXT midnight, with the
    offset the zone gives that instant."""
    d = dt.date.fromisoformat(trading_date) + dt.timedelta(days=1)
    stored = dt.datetime(d.year, d.month, d.day, tzinfo=NZ)
    return {
        "id": bid or f"b-{trading_date}",
        "date": stored.isoformat(),
        "amount": amount,
        "salesTax": 0.15,
    }


def budgets_api(rows, *, mode="live", ledger=True, writes_ok=True):
    responses = {
        "norm_finance.get_mode": {"mode": mode},
        "loadedhub.get_budgets_raw": lambda p: [
            r
            for r in rows
            if p["from_date"]
            <= (
                dt.date.fromisoformat(r["date"][:10]) - dt.timedelta(days=1)
            ).isoformat()
            < p["to_date"]
        ],
        "loadedhub.update_budget_raw": (lambda p: {"body": ""})
        if writes_ok
        else {"error": "API error 404"},
        "loadedhub.create_budget_raw": lambda p: {
            "id": "new-" + p["loaded_date"][:10],
            "date": p["loaded_date"],
            "amount": p["amount"],
            "salesTax": 0.15,
        },
    }
    if ledger:
        responses["norm_finance.record_budget_push"] = {"success": True, "id": "push-1"}
    return FakeApi(responses)


def run_preview(code, api, **params):
    """Run a write under the real preview rule: stop at preview(card) and
    hand back (card, basis fingerprint)."""
    captured = {}

    def preview(card=None, basis=None):
        card = card() if callable(card) else card
        basis = basis() if callable(basis) else basis
        captured["card"] = card
        captured["fingerprint"] = fingerprint(card if basis is None else basis)
        raise PreviewReady()

    try:
        harness.call(code, params, api.call_api, preview=preview)
    except PreviewReady:
        return captured["card"], captured["fingerprint"]
    raise AssertionError("the run never reached preview(...)")


WEEK_ROWS = [
    budget_row("2026-10-12", 1978.0),
    budget_row("2026-10-13", 2392.0),
    budget_row("2026-10-14", 2194.0),
    budget_row("2026-10-16", 9521.0),
]
WEEK_DAYS = [
    {"date": "2026-10-12", "amount": 2100},
    {"date": "2026-10-13", "amount": 3350},
    {"date": "2026-10-14", "amount": 2194},
    {"date": "2026-10-15", "amount": 0},
    {"date": "2026-10-16", "amount": 9521.004},
    {"date": "2026-10-17", "amount": 15000},
]


class TestSetBudgets:
    def test_the_card_shows_every_day_before_after_and_the_rules(self):
        api = budgets_api(WEEK_ROWS)
        card, fp = run_preview(
            SET_BUDGETS, api, venue="La Zeppa", days=json.dumps(WEEK_DAYS)
        )
        assert card["title"] == "Set daily budgets in Loaded"
        assert card["target"] == "La Zeppa — Mon 12 Oct – Sat 17 Oct 2026"
        changes = {c["field"]: c for c in card["changes"]}
        assert changes["Mon 12 Oct"] == {
            "field": "Mon 12 Oct",
            "before": "$1,978.00",
            "after": "$2,100.00",
        }
        assert changes["Sat 17 Oct"] == {
            "field": "Sat 17 Oct",
            "before": "— (no budget)",
            "after": "$15,000.00 (new)",
        }
        assert changes["Wed 14 Oct"]["after"] == "$2,194.00 (unchanged)"
        assert "Thu 15 Oct" not in changes  # $0 without allow_zero: skipped
        assert any(w.startswith("Tue 13 Oct moves +40%") for w in card["warnings"])
        assert any(
            "1 day(s) at $0 skipped" in w and "2026-10-15" in w
            for w in card["warnings"]
        )
        assert "Amounts include GST" in card["note"]
        assert "2 to update, 1 to create, 2 unchanged, 1 skipped" in card["note"]
        # Nothing was written before the preview.
        assert (
            api.count("update_budget_raw") == 0 and api.count("create_budget_raw") == 0
        )
        # The read covered the range to-exclusive: to = last day + 1.
        read = next(c for c in api.calls if c[1] == "get_budgets_raw")
        assert read[2] == {
            "venue": "La Zeppa",
            "from_date": "2026-10-12",
            "to_date": "2026-10-18",
        }
        # Loaded moving a day's amount changes what the person approved.
        moved = [
            dict(r, amount=r["amount"] + 1) if r["id"] == "b-2026-10-12" else r
            for r in WEEK_ROWS
        ]
        _, fp2 = run_preview(
            SET_BUDGETS,
            budgets_api(moved),
            venue="La Zeppa",
            days=json.dumps(WEEK_DAYS),
        )
        assert fp2 != fp

    def test_the_approved_run_puts_rows_and_posts_missing_days_then_records(self):
        api = budgets_api(WEEK_ROWS)
        out = harness.run(SET_BUDGETS, api, venue="La Zeppa", days=WEEK_DAYS)
        assert out["updated"] == 2 and out["created"] == 1 and out["unchanged"] == 2
        assert out["skipped_zero"] == ["2026-10-15"] and out["failed"] == []
        puts = [c[2] for c in api.calls if c[1] == "update_budget_raw"]
        assert puts == [
            {
                "venue": "La Zeppa",
                "budget_id": "b-2026-10-12",
                "loaded_date": "2026-10-13T00:00:00+13:00",
                "amount": "2100.00",
                "sales_tax": "0.1500",
            },
            {
                "venue": "La Zeppa",
                "budget_id": "b-2026-10-13",
                "loaded_date": "2026-10-14T00:00:00+13:00",
                "amount": "3350.00",
                "sales_tax": "0.1500",
            },
        ]
        posts = [c[2] for c in api.calls if c[1] == "create_budget_raw"]
        assert posts == [
            {
                "venue": "La Zeppa",
                "loaded_date": "2026-10-18T00:00:00+13:00",
                "amount": "15000.00",
            }
        ]
        # The new day lies past the last row, so its midnight was probed
        # once (one-sided neighbours never settle an offset).
        assert api.count("resolve_dates") == 1
        ledger = next(c[2] for c in api.calls if c[1] == "record_budget_push")
        assert ledger["tool"] == "set_budgets" and ledger["venue_id"] == "v-laz"
        assert ledger["from"] == "2026-10-12" and ledger["to"] == "2026-10-17"
        by_date = {d["date"]: d for d in ledger["days"]}
        assert (
            by_date["2026-10-17"]["action"] == "create"
            and by_date["2026-10-17"]["loaded_id"] == "new-2026-10-18"
        )
        assert by_date["2026-10-15"]["action"] == "skipped"
        assert out["ledger"] == "recorded"
        assert api.calls[0][1] == "get_mode"

    def test_allow_zero_sends_the_zero_days(self, monkeypatch):
        """Through the REAL executor and endpoint rows: its required-field
        check is falsy, so a $0 sent as the float 0.0 was refused as
        "Missing required fields: amount" before reaching Loaded (Loaded's
        test environment, 23 Oct 2026). Only the HTTP layer is faked."""
        import types

        from app.connectors import http_pool
        from app.connectors.spec_executor import execute_spec
        from scripts import sync_payroll_config as cfg

        api = budgets_api([budget_row("2026-10-11", 50.0)] + WEEK_ROWS)
        out = harness.run(
            SET_BUDGETS,
            api,
            venue="La Zeppa",
            days=[
                {"date": "2026-10-11", "amount": 0},
                {"date": "2026-10-12", "amount": 0},
                {"date": "2026-10-15", "amount": 0},
            ],
            allow_zero="true",
        )
        assert out["updated"] == 2 and out["created"] == 1
        assert out["skipped_zero"] == []

        sent = []

        class _Client:
            def request(self, **kw):
                sent.append(kw)
                return types.SimpleNamespace(
                    status_code=204 if kw["method"] == "PUT" else 200,
                    headers={},
                    text="",
                    json=lambda: {"id": "new"},
                )

        monkeypatch.setattr(http_pool, "get_client", lambda: _Client())
        rows = {e["action"]: e for e in cfg.ENDPOINTS}
        spec = types.SimpleNamespace(
            connector_name="loadedhub",
            execution_mode="template",
            auth_type="bearer",
            auth_config={"token_field": "access_token"},
            base_url_template="https://",
            oauth_config=None,
        )
        for _, action, params in api.calls:
            if action not in ("update_budget_raw", "create_budget_raw"):
                continue
            params = {k: v for k, v in params.items() if k != "venue"}
            result, _ = execute_spec(
                spec, rows[action], params, {"access_token": "t"}, None
            )
            assert result.success, result.error_message
        assert [(k["method"], k["json"]["amount"]) for k in sent] == [
            ("PUT", 0),
            ("PUT", 0),
            ("POST", 0),
        ]
        assert sent[0]["json"]["salesTax"] == 0.15

    def test_offsets_across_the_clock_change_come_from_probes(self):
        """No Loaded rows at all, Thu 24 – Wed 30 Sep 2026: the midnights
        after 24–26 Sep are NZST (+12:00), those after 27 Sep on are NZDT
        (+13:00). The probes find the change; days are never guessed."""
        api = budgets_api([])
        days = [{"date": f"2026-09-{d}", "amount": 1000 + d} for d in range(24, 31)]
        out = harness.run(SET_BUDGETS, api, venue="La Zeppa", days=days)
        assert out["created"] == 7 and out["failed"] == []
        posts = {
            c[2]["loaded_date"][:10]: c[2]["loaded_date"]
            for c in api.calls
            if c[1] == "create_budget_raw"
        }
        assert posts["2026-09-25"] == "2026-09-25T00:00:00+12:00"
        assert posts["2026-09-27"] == "2026-09-27T00:00:00+12:00"  # trading day 26 Sep
        assert posts["2026-09-28"] == "2026-09-28T00:00:00+13:00"  # trading day 27 Sep
        assert posts["2026-10-01"] == "2026-10-01T00:00:00+13:00"
        # One guess, two end probes, then a bisection: never one per day.
        assert 3 <= api.count("resolve_dates") <= 9
        for c in api.calls:
            if c[1] == "resolve_dates" and c[2].get("start"):
                assert c[2]["venue_id"] == "v-laz"

    def test_a_row_on_each_side_settles_a_missing_day_without_a_probe(self):
        """Only rows inside the pushed range are read (from=F, to=T+1), so
        the neighbours have to be in the push for their offsets to count."""
        rows = [budget_row("2026-10-05", 100.0), budget_row("2026-10-07", 100.0)]
        api = budgets_api(rows)
        out = harness.run(
            SET_BUDGETS,
            api,
            venue="La Zeppa",
            days=[
                {"date": "2026-10-05", "amount": 100},
                {"date": "2026-10-06", "amount": 500},
                {"date": "2026-10-07", "amount": 100},
            ],
        )
        assert out["created"] == 1 and out["unchanged"] == 2
        assert api.count("resolve_dates") == 0
        assert (
            next(c[2] for c in api.calls if c[1] == "create_budget_raw")["loaded_date"]
            == "2026-10-07T00:00:00+13:00"
        )

    def test_rows_on_both_sides_of_the_change_force_a_probe(self):
        rows = [budget_row("2026-09-25", 100.0), budget_row("2026-09-29", 100.0)]
        assert rows[0]["date"].endswith("+12:00") and rows[1]["date"].endswith("+13:00")
        api = budgets_api(rows)
        harness.run(
            SET_BUDGETS,
            api,
            venue="La Zeppa",
            days=[
                {"date": "2026-09-26", "amount": 5},
                {"date": "2026-09-27", "amount": 6},
            ],
        )
        posts = {
            c[2]["loaded_date"][:10]: c[2]["loaded_date"]
            for c in api.calls
            if c[1] == "create_budget_raw"
        }
        assert posts == {
            "2026-09-27": "2026-09-27T00:00:00+12:00",
            "2026-09-28": "2026-09-28T00:00:00+13:00",
        }
        assert api.count("resolve_dates") >= 1

    def test_the_92_day_cap(self):
        api = budgets_api([])
        days = [
            {
                "date": (dt.date(2026, 11, 1) + dt.timedelta(days=i)).isoformat(),
                "amount": 10,
            }
            for i in range(93)
        ]
        out = harness.run(SET_BUDGETS, api, venue="La Zeppa", days=days)
        assert "93 days" in out["error"] and "92" in out["error"]
        assert api.count("get_budgets_raw") == 0
        # Two dates 93 days apart are the same refusal, however few days.
        out = harness.run(
            SET_BUDGETS,
            api,
            venue="La Zeppa",
            days=[
                {"date": "2026-11-01", "amount": 1},
                {"date": "2027-02-01", "amount": 1},
            ],
        )
        assert "92" in out["error"]

    def test_single_connection_refusals(self):
        api = budgets_api(WEEK_ROWS)
        assert (
            "venue is required"
            in harness.run(SET_BUDGETS, api, days=WEEK_DAYS)["error"]
        )
        assert (
            "isn't a venue"
            in harness.run(SET_BUDGETS, api, venue="Nowhere", days=WEEK_DAYS)["error"]
        )
        assert (
            "isn't connected"
            in harness.run(SET_BUDGETS, api, venue="Mr Murdochs", days=WEEK_DAYS)[
                "error"
            ]
        )
        assert api.count("get_budgets_raw") == 0
        # A venue_id from the page helper resolves to the name.
        out = harness.run(
            SET_BUDGETS,
            budgets_api(WEEK_ROWS),
            venue_id="v-laz",
            days=[{"date": "2026-10-12", "amount": 1}],
        )
        assert out["venue"] == "La Zeppa"

    def test_refused_unless_norm_finance_is_live(self):
        api = budgets_api(WEEK_ROWS, mode="shadow")
        out = harness.run(SET_BUDGETS, api, venue="La Zeppa", days=WEEK_DAYS)
        assert "'shadow'" in out["error"] and api.count("get_budgets_raw") == 0
        api = FakeApi({})  # the built-in is not installed at all
        out = harness.run(SET_BUDGETS, api, venue="La Zeppa", days=WEEK_DAYS)
        assert "isn't available" in out["error"]

    def test_a_missing_ledger_is_reported_not_fatal(self):
        api = budgets_api(WEEK_ROWS, ledger=False)
        out = harness.run(
            SET_BUDGETS,
            api,
            venue="La Zeppa",
            days=[{"date": "2026-10-12", "amount": 1}],
        )
        assert out["updated"] == 1
        assert out["ledger"].startswith(
            "not recorded (Tool not found: norm_finance.record_budget_push"
        )

    def test_a_failed_write_is_listed_and_the_rest_still_go(self):
        api = budgets_api(WEEK_ROWS, writes_ok=False)
        out = harness.run(SET_BUDGETS, api, venue="La Zeppa", days=WEEK_DAYS)
        assert out["updated"] == 0 and out["created"] == 1
        assert out["failed"] == [
            "2026-10-12: API error 404",
            "2026-10-13: API error 404",
        ]
        assert "2 day(s) failed" in out["note"]

    def test_bad_days_are_refused_before_any_read(self):
        api = budgets_api(WEEK_ROWS)
        assert (
            "days is required"
            in harness.run(SET_BUDGETS, api, venue="La Zeppa")["error"]
        )
        assert (
            "not a number"
            in harness.run(
                SET_BUDGETS,
                api,
                venue="La Zeppa",
                days=[{"date": "2026-10-12", "amount": "x"}],
            )["error"]
        )
        assert (
            "negative"
            in harness.run(
                SET_BUDGETS,
                api,
                venue="La Zeppa",
                days=[{"date": "2026-10-12", "amount": -1}],
            )["error"]
        )
        assert (
            "twice"
            in harness.run(
                SET_BUDGETS,
                api,
                venue="La Zeppa",
                days=[
                    {"date": "2026-10-12", "amount": 1},
                    {"date": "2026-10-12", "amount": 2},
                ],
            )["error"]
        )
        assert api.count("get_budgets_raw") == 0

    def test_the_typed_twin_is_the_same_write_under_its_own_name(self):
        api = budgets_api(WEEK_ROWS)
        out = harness.run(SET_BUDGETS_TYPED, api, venue="La Zeppa", days=WEEK_DAYS)
        assert out["updated"] == 2 and out["created"] == 1
        ledger = next(c[2] for c in api.calls if c[1] == "record_budget_push")
        assert ledger["tool"] == "set_budgets_typed"
        # The two files differ only in the row name they hand the ledger.
        a = (_DIR / "set_budgets.py").read_text(encoding="utf-8")
        b = (_DIR / "set_budgets_typed.py").read_text(encoding="utf-8")
        assert 'return _push_budgets(params, call_api, log, "set_budgets")' in a
        assert 'return _push_budgets(params, call_api, log, "set_budgets_typed")' in b
        for code in (SET_BUDGETS, SET_BUDGETS_TYPED):
            _reject_unsafe_source(code)  # the prepended form production execs


# ── get_weekly_financials ────────────────────────────────────────────────


def bucket(date: str, **fields):
    """A daily feed bucket starting at the venue's day start — in UTC, as
    the sales feed stamps them."""
    start = _local(dt.date.fromisoformat(date), "07:00").astimezone(dt.timezone.utc)
    return {"startTime": start.isoformat(), **fields}


def weekly_api(**over):
    # Week Mon 12 – Sun 18 Oct; month-to-date from Thu 1 Oct. Two days of
    # the month before the week carry figures so week ≠ MTD.
    days = ["2026-10-01", "2026-10-02"] + [f"2026-10-{d}" for d in range(12, 19)]
    responses = {
        "loadedhub.get_sales_data": [
            bucket(
                d,
                invoices=1150.0,
                invoicesTax=150.0,
                discounts=50.0,
                surcharges=0,
                quantity=90,
                count=40,
            )
            for d in days
        ],
        "loadedhub.get_pos_orders": [bucket(d, amount=1200.0, count=42) for d in days],
        "loadedhub.get_pos_voids_raw": [
            {
                **bucket(
                    d,
                    invoices=0.0,
                    voidReason=None,
                    voidQuantity=2,
                    voidAmount=30.0,
                    wasteQuantity=1,
                    wasteAmount=5,
                    transactions=None,
                ),
                "startTime": _local(dt.date.fromisoformat(d), "07:00").isoformat(),
            }
            for d in days
        ],
        "loadedhub.get_cashup_sets_summary_raw": [
            {
                "startTime": _local(dt.date.fromisoformat(d), "07:00").isoformat(),
                "totalVariance": -2.0,
                "cashVariance": -1.5,
            }
            for d in days
        ],
        "loadedhub.get_roster_vs_actual": [
            bucket(
                d,
                rosteredCost=300.0,
                actualCost=320.0,
                rosteredHours=10.0,
                actualHours=11.0,
                categories=[],
            )
            for d in days
        ],
        "loadedhub.get_budgets": {
            "days": [{"date": d, "day": "x", "amount": 1000.0} for d in days],
            "total": 9000.0,
        },
        "loadedhub.get_pos_item_sales": lambda p: [
            {
                "itemName": "Burger",
                "itemGroupName": "Food",
                "amount": 700.0 * _span(p),
                "quantity": 10,
            },
            {
                "itemName": "Lager",
                "itemGroupName": "Beverage",
                "amount": 500.0 * _span(p),
                "quantity": 20,
            },
            {
                "itemName": "Tee",
                "itemGroupName": "Merch",
                "amount": 10.0 * _span(p),
                "quantity": 1,
            },
        ],
        "loadedhub.get_clockins_summary_by_role_raw": lambda p: [
            {
                "roleId": "r-floor",
                "roleName": "FLOOR TEAM",
                "roleCostGroup": "b",
                "cost": 200.0 * _span(p),
                "hours": 7.0 * _span(p),
            },
            {
                "roleId": "r-kitchen",
                "roleName": "KITCHEN",
                "roleCostGroup": "f",
                "cost": 120.0 * _span(p),
                "hours": 4.0 * _span(p),
            },
        ],
    }
    responses.update(over)
    return FakeApi(responses)


def _span(p):
    """Trading days a feed call covers (7 for the week, 18 for the MTD)."""
    s = dt.datetime.fromisoformat(p.get("start_time") or p["start"]).date()
    e = dt.datetime.fromisoformat(p.get("end_time") or p["end"]).date()
    return 7 if (e - s).days < 10 else 18


class TestGetWeeklyFinancials:
    def test_week_and_month_to_date_per_venue_with_every_figure(self):
        api = weekly_api()
        out = harness.run(WEEKLY, api, venue="La Zeppa", week_ending="2026-10-18")
        assert out["week"]["label"] == "Week ending Sun 18 Oct 2026"
        assert out["mtd"] == {
            "from": "2026-10-01",
            "to": "2026-10-18",
            "label": "1 – 18 Oct 2026",
        }
        v = out["venues"][0]
        assert v["venue"] == "La Zeppa" and "error" not in v
        wk, md = v["week"], v["mtd"]
        assert (
            wk["sales"] == 8050.0
            and wk["gst"] == 1050.0
            and wk["sales_ex_gst"] == 7000.0
        )
        assert md["sales"] == 10350.0  # nine trading days with sales in the month
        assert wk["budget"] == 7000.0 and md["budget"] == 9000.0
        assert wk["budget_pct"] == 1.15 and wk["vs_budget"] == 1050.0
        assert wk["orders"] == 294 and wk["average_order"] == round(1200.0 * 7 / 294, 2)
        assert wk["discounts"] == 350.0 and wk["bills"] == 280
        assert wk["voids"] == 210.0 and wk["void_count"] == 14 and wk["waste"] == 35.0
        assert (
            wk["cash_variance"] == -10.5
            and wk["total_variance"] == -14.0
            and wk["cashup_buckets"] == 7
        )
        assert wk["rostered_cost"] == 2100.0 and wk["actual_cost"] == 2240.0
        assert wk["rostered_hours"] == 70.0 and wk["actual_hours"] == 77.0
        assert wk["hours_variance"] == 7.0 and wk["cost_variance"] == 140.0
        # Wages by cost group, and the percentages on ex-GST sales.
        assert (
            wk["wages"] == 2240.0
            and wk["food_wages"] == 840.0
            and wk["beverage_wages"] == 1400.0
        )
        assert wk["wage_pct"] == round(2240.0 / 7000.0, 4)
        assert wk["hours"] == 77.0 and wk["kitchen_hours"] == 28.0
        # The split from item sales by revenue group, with its mismatch shown.
        assert (
            wk["food_sales"] == 4900.0
            and wk["beverage_sales"] == 3500.0
            and wk["other_sales"] == 70.0
        )
        assert wk["food_share"] == round(4900.0 / 8470.0, 4)
        assert wk["split_mismatch"] == 420.0
        assert wk["food_wage_pct"] == round(840.0 / (7000.0 * wk["food_share"]), 4)
        assert md["wages"] == 5760.0 and md["food_sales"] == 12600.0
        # Daily rows for the week only, from the MTD feeds.
        assert [d["date"] for d in wk["days"]] == [
            f"2026-10-{d}" for d in range(12, 19)
        ]
        d0 = wk["days"][0]
        assert d0 == {
            "date": "2026-10-12",
            "day": "Mon",
            "sales": 1150.0,
            "sales_ex_gst": 1000.0,
            "budget": 1000.0,
            "orders": 42,
            "discounts": 50.0,
            "voids": 30.0,
            "rostered_cost": 300.0,
            "wages": 320.0,
            "wage_pct": 0.32,
            "budget_pct": 1.15,
        }
        assert "days" not in md
        # The Norm plan is absent: said so, not an error.
        assert out["budget_plan_available"] is False and wk["budget_plan"] is None
        assert any("not available yet" in n for n in out["notes"])
        assert out["warnings"] == []
        assert api.count() == 1 + 13

    def test_the_feeds_are_read_once_over_the_month_to_date(self):
        api = weekly_api()
        harness.run(WEEKLY, api, venue="La Zeppa", week_ending="2026-10-18")
        sales = [c[2] for c in api.calls if c[1] == "get_sales_data"]
        assert sales == [
            {
                "venue": "La Zeppa",
                "start_datetime": "2026-10-01T07:00:00+13:00",
                "end_datetime": "2026-10-19T06:59:59+13:00",
                "interval": "1.00:00:00",
            }
        ]
        budgets = next(c[2] for c in api.calls if c[1] == "get_budgets")
        assert budgets == {
            "venue": "La Zeppa",
            "from_date": "2026-10-01",
            "to_date": "2026-10-18",
            "tax": "include",
        }
        items = [c[2] for c in api.calls if c[1] == "get_pos_item_sales"]
        assert (
            items[0]["start_time"] == "2026-10-12T07:00:00+13:00"
            and items[1]["start_time"] == "2026-10-01T07:00:00+13:00"
        )
        # The week resolved through the venue's own calendar.
        assert all(
            c[2].get("venue_id") == "v-laz"
            for c in api.calls
            if c[1] == "resolve_dates"
        )

    def test_a_week_that_starts_the_month_has_mtd_equal_to_the_week(self):
        api = weekly_api()
        out = harness.run(WEEKLY, api, venue="La Zeppa", week_ending="2026-06-07")
        v = out["venues"][0]
        assert (
            out["mtd"]["from"] == "2026-06-01"
            and v["windows"]["mtd"]["start"] == v["windows"]["week"]["start"]
        )
        assert api.count("resolve_dates") == 1

    def test_the_group_adds_venues_and_a_failed_venue_is_flagged(self):
        def sales(p):
            if p["venue"] == "Dunedin Social Club":
                return {"error": "API error 503"}
            return weekly_api().responses["loadedhub.get_sales_data"]

        api = weekly_api(**{"loadedhub.get_sales_data": sales})
        out = harness.run(WEEKLY, api, venues="all", week_ending="2026-10-18")
        assert [v["venue"] for v in out["venues"]] == [
            "La Zeppa",
            "Dunedin Social Club",
        ]
        assert out["venues"][1]["error"].startswith("sales unavailable")
        assert out["group"]["venues"] == 1 and out["group"]["week"]["sales"] == 8050.0
        assert any(
            "Dunedin Social Club: sales unavailable" in w for w in out["warnings"]
        )
        # The DSC window used its own 06:00 day start.
        dsc = [
            c[2]
            for c in api.calls
            if c[1] == "resolve_dates" and c[2].get("venue_id") == "v-dsc"
        ]
        assert dsc and "query" in dsc[0]

    def test_the_norm_budget_plan_rides_along_when_registered(self):
        api = weekly_api(
            **{
                "norm_finance.get_budget_plan": {
                    "success": True,
                    "data": {"locked_budget": 7200.0},
                }
            }
        )
        out = harness.run(WEEKLY, api, venue="La Zeppa", week_ending="2026-10-18")
        assert out["budget_plan_available"] is True
        assert out["venues"][0]["week"]["budget_plan"] == {"locked_budget": 7200.0}
        plan = next(c[2] for c in api.calls if c[1] == "get_budget_plan")
        assert plan == {
            "venue": "La Zeppa",
            "venue_id": "v-laz",
            "week_ending": "2026-10-18",
        }

    def test_a_side_feed_failing_is_a_warning_with_blank_figures(self):
        api = weekly_api(
            **{"loadedhub.get_cashup_sets_summary_raw": {"error": "API error 500"}}
        )
        out = harness.run(WEEKLY, api, venue="La Zeppa", week_ending="2026-10-18")
        assert any("cash-ups unavailable" in w for w in out["warnings"])
        assert (
            out["venues"][0]["week"]["cash_variance"] == 0.0
            and out["venues"][0]["week"]["cashup_buckets"] == 0
        )
        assert out["venues"][0]["week"]["sales"] == 8050.0

    def test_default_week_is_the_last_completed_one(self):
        api = weekly_api()
        out = harness.run(WEEKLY, api, venue="La Zeppa", today="2026-10-20")
        assert out["week"]["week_ending"] == "2026-10-18"

    def test_refusals(self):
        api = weekly_api()
        assert "no venue" in harness.run(WEEKLY, api, week_ending="2026-10-18")["error"]
        assert (
            "isn't a venue"
            in harness.run(WEEKLY, api, venue="Nowhere", week_ending="2026-10-18")[
                "error"
            ]
        )
        assert (
            "week_ending"
            in harness.run(WEEKLY, api, venue="La Zeppa", week_ending="soon")["error"]
        )


# ── the installer's rows ─────────────────────────────────────────────────


class TestInstaller:
    def test_rows_carry_code_shapes_effects_and_scopes(self):
        from scripts import sync_payroll_config as cfg

        rows = {r["action"]: r for r in cfg.tool_rows()}
        assert set(rows) == {
            "get_payrun",
            "get_weekly_financials",
            "set_budgets",
            "set_budgets_typed",
            "get_closing_manager",
            "get_pay_adjustments",
        }
        for r in rows.values():
            assert r["effect"] in ("read", "write") and r["field_schema"]
            assert r["consolidator_config"]["function_code"]
            _reject_unsafe_source(r["consolidator_config"]["function_code"])
            assert isinstance(r["consolidator_config"]["shapes"], dict)
        assert rows["get_payrun"]["requires"] == ["payroll:read"]
        assert rows["get_weekly_financials"]["requires"] == ["finance:read"]
        assert rows["get_weekly_financials"]["display_component"] == "finance_weekly"
        assert rows["set_budgets"]["approval"] == {
            "default": "ask",
            "allow_auto": False,
            "label": "set daily budgets in Loaded",
        }
        assert rows["set_budgets_typed"]["engine_only"] is True
        assert rows["set_budgets_typed"]["approval"]["allow_auto"] is True
        assert rows["get_closing_manager"]["engine_only"] is True
        # No `requires`: no dollar rates, and the nightly finance job reads it
        # with no person behind it.
        assert "requires" not in rows["get_pay_adjustments"]
        assert not rows["get_pay_adjustments"].get("engine_only")
        for twin in ("set_budgets", "set_budgets_typed"):
            cc = rows[twin]["consolidator_config"]
            assert cc["allowed_write_actions"] == [
                "update_budget_raw",
                "create_budget_raw",
                "norm_finance.record_budget_push",
            ]
            assert cc["max_api_calls"] == 100
            assert (
                cc["function_code"].startswith("# ruff: noqa")
                and "_push_budgets" in cc["function_code"]
            )
        assert rows["get_payrun"]["consolidator_config"]["max_api_calls"] == 24
        assert (
            rows["get_weekly_financials"]["consolidator_config"]["max_api_calls"] == 120
        )
        assert rows["get_closing_manager"]["consolidator_config"]["max_api_calls"] == 4
        eps = {e["action"]: e for e in cfg.ENDPOINTS}
        assert len(eps) == 10 and all(e["engine_only"] for e in eps.values())
        assert all(
            e["read_only"] and e["method"] == "GET"
            for a, e in eps.items()
            if a not in ("update_budget_raw", "create_budget_raw")
        )
        assert eps["get_pay_adjustment_packages_raw"]["path_template"].endswith(
            "/pay-rules/packages?includeActivatedPackagesOnly=true&detail=Full"
        )
        assert (
            eps["update_budget_raw"]["read_only"] is False
            and eps["update_budget_raw"]["method"] == "PUT"
        )
        assert (
            eps["create_budget_raw"]["read_only"] is False
            and eps["create_budget_raw"]["method"] == "POST"
        )
        assert 204 in eps["update_budget_raw"]["success_status_codes"]
        assert (
            "filterByPayrollVenue=true"
            in eps["get_payrun_clockins_raw"]["path_template"]
        )
        assert "{{ q }}" in eps["get_leave_requests_filtered_raw"]["path_template"]
        # The two live rows this build must not touch are not in the list.
        assert "get_timeclock_entries" not in eps and "get_leave_requests" not in eps

    def test_shapes_name_only_endpoints_the_tool_calls(self):
        from app.services.spec_inventory import calls_in_code
        from scripts import sync_payroll_config as cfg

        shapes = json.loads((_DIR / "shapes.json").read_text(encoding="utf-8"))
        for r in cfg.tool_rows():
            own = shapes.get(f"loadedhub.{r['action']}") or {}
            called = {
                f"{c}.{a}"
                for c, a in calls_in_code(
                    r["consolidator_config"]["function_code"],
                    {"loadedhub", "norm", "norm_finance"},
                )
            }
            for key in own:
                assert key in called, f"{r['action']} shapes {key} but never calls it"
            for shape in own.values():
                assert shape.get("enabled") is True and shape.get("fields")


def test_the_file_list_matches_the_build():
    present = {p.stem for p in pathlib.Path(_DIR).glob("*.py")}
    for name in (
        "get_payrun",
        "get_closing_manager",
        "get_weekly_financials",
        "set_budgets",
        "set_budgets_typed",
        "set_budgets_common",
        "get_pay_adjustments",
    ):
        assert name in present
