"""The Weekly Summary's Norm side (plan §4, Phase 6; 04 §7): the budget
plan's proration against hand-worked numbers, the adjusted wages/leave
from the payroll reconciliation (present or absent), and the Summary route
— one ``get_weekly_financials`` per venue on its own token, the viewer's
venues only, the group row for ``payroll:read`` holders only.
"""

from __future__ import annotations

import datetime as dt
import sys
import types

import pytest

from app.services.finance import budgets, summary
from tests.finance_f6_world import MONDAY, SUNDAY, make_world
from tests.org_helpers import auth_headers

FY = 2027


def _lock_week(w, venue, first: dt.date, days: int, cents: int, fiscal_year=FY):
    ctx = w.ctx()
    v = budgets.create_version(w.db, ctx, venue.id, fiscal_year, label="t")
    budgets.set_days(
        w.db,
        ctx,
        v.id,
        [
            {"date": (first + dt.timedelta(days=i)).isoformat(), "amount_cents": cents}
            for i in range(days)
        ],
    )
    budgets.lock_version(w.db, ctx, v.id)
    return v


class TestBudgetPlan:
    def test_the_week_is_prorated_as_orbits_financial_summary_did(self, db_session):
        w = make_world(db_session)
        ctx = w.ctx()
        # 1–22 Nov 2026 at $100/day; November has 30 days.
        _lock_week(w, w.laz, dt.date(2026, 11, 1), 22, 10_000)
        budgets.set_lines(
            db_session,
            ctx,
            w.laz.id,
            FY,
            11,
            {
                "wage_pct": {"value_ratio": "0.30"},
                "cogs_pct": {"value_ratio": "0.30"},
                "opex_cents": {"value_cents": 300_000},  # $3,000 → $100/day
                "leave_cents": {"value_cents": 30_000},  # $300 → $10/day
                "depreciation_cents": {"value_cents": 3_000},  # $30 → $1/day
                "rebates_pct": {"value_ratio": "0.02"},
            },
        )
        plan = summary.budget_plan(db_session, ctx, w.laz.id, SUNDAY)
        week = plan["week"]
        assert (week["start"], week["end"], week["days_with_budget"]) == (
            MONDAY.isoformat(),
            SUNDAY.isoformat(),
            7,
        )
        assert week["budget"] == 700.0 and week["budget_cents"] == 70_000
        assert week["budget_ex_gst"] == 608.7
        lines = week["lines"]
        assert (
            lines["wages"] == round(608.6957 * 0.30, 2) == 182.61
            and lines["wage_pct"] == 0.3
        )
        assert lines["cogs"] == 182.61 and lines["cogs_pct"] == 0.3
        assert (
            lines["opex"] == 700.0
            and lines["leave"] == 70.0
            and lines["depreciation"] == 7.0
        )
        assert lines["rebates_pct"] == 0.02
        assert lines["gross_profit"] == round(608.6957 - 182.6087 - 70 - 182.6087, 2)
        assert lines["operating_profit"] == round(lines["gross_profit"] - 700, 2)
        mtd = plan["mtd"]
        assert (mtd["start"], mtd["end"], mtd["days_with_budget"]) == (
            "2026-11-01",
            SUNDAY.isoformat(),
            22,
        )
        assert mtd["budget"] == 2200.0 and mtd["lines"]["opex"] == 2200.0
        assert plan["fiscal_year"] == 2027 and plan["version"]["label"] == "t"
        # F4's reconciliation answers zeros for a week with no adjustments.
        assert week["adjustments"] == {"wages": 0.0, "leave": 0.0}
        assert mtd["adjustments"] is None

    def test_a_dollar_wage_line_spreads_by_days_in_month_across_a_month_boundary(
        self, db_session
    ):
        w = make_world(db_session)
        ctx = w.ctx()
        # Mon 30 Nov – Sun 6 Dec 2026: one November day (wage %), six December days (wage $).
        _lock_week(w, w.laz, dt.date(2026, 11, 30), 7, 11_500)  # $115 → $100 ex GST
        budgets.set_lines(
            db_session, ctx, w.laz.id, FY, 11, {"wage_pct": {"value_ratio": "0.25"}}
        )
        budgets.set_lines(
            db_session, ctx, w.laz.id, FY, 12, {"wage_cents": {"value_cents": 310_000}}
        )  # $3,100 / 31 = $100/day
        plan = summary.budget_plan(db_session, ctx, w.laz.id, "2026-12-06")
        assert plan["week"]["lines"]["wages"] == round(100 * 0.25 + 6 * 100, 2) == 625.0
        # The MTD anchors on the month the week STARTS in: November.
        assert plan["mtd"]["start"] == "2026-11-01"

    def test_no_locked_budget_is_zero_with_a_note_never_an_error(self, db_session):
        w = make_world(db_session)
        plan = summary.budget_plan(db_session, w.ctx(), w.laz.id, SUNDAY)
        assert plan["week"]["budget_cents"] == 0 and plan["version"] is None
        assert any("no locked budget for FY2027" in n for n in plan["notes"])

    def test_adjusted_wages_and_leave_come_from_the_payroll_reconciliation(
        self, db_session, monkeypatch
    ):
        w = make_world(db_session)
        seen = {}

        def week_adjustments(db, org_id, venue_id, week_start):
            seen["args"] = (org_id, venue_id, week_start)
            return {"wages_cents": 12_345, "leave_cents": 678}

        fake = types.ModuleType("app.services.payroll.reconciliation")
        fake.week_adjustments = week_adjustments
        monkeypatch.setitem(sys.modules, "app.services.payroll.reconciliation", fake)
        plan = summary.budget_plan(db_session, w.ctx(), w.laz.id, SUNDAY)
        assert plan["week"]["adjustments"] == {"wages": 123.45, "leave": 6.78}
        assert seen["args"] == (w.org.id, w.laz.id, MONDAY)
        assert not any("unavailable" in n for n in plan["notes"])

    def test_the_plan_needs_finance_read_and_the_venue(self, db_session):
        from app.services.finance_core.errors import Forbidden

        w = make_world(db_session)
        with pytest.raises(Forbidden):
            summary.budget_plan(db_session, w.ctx(w.team_member), w.laz.id, SUNDAY)
        with pytest.raises(Forbidden):
            summary.budget_plan(db_session, w.ctx(w.manager), w.dsc.id, SUNDAY)

    def test_the_gst_rate_follows_the_currency(self, db_session):
        w = make_world(db_session)
        assert summary.gst_rate(db_session, w.org.id) == summary.GST_RATES["NZD"]
        w.org.currency = "AUD"
        db_session.flush()
        assert float(summary.gst_rate(db_session, w.org.id)) == 0.10
        assert summary.week_of("2026-11-18") == (MONDAY, SUNDAY) and summary.week_of(
            SUNDAY
        ) == (MONDAY, SUNDAY)


# ---------------------------------------------------------------- route --


def _figures(sales: float) -> dict:
    ex = round(sales / 1.15, 2)
    return {
        "sales": sales,
        "sales_ex_gst": ex,
        "gst": round(sales - ex, 2),
        "budget": 60000.0,
        "wages": round(sales * 0.25, 2),
        "wage_pct": round(sales * 0.25 / ex, 4) if ex else None,
        "orders": 100,
        "order_value": sales,
        "average_order": round(sales / 100, 2),
        "budget_plan": None,
    }


@pytest.fixture
def api(client, db_session, monkeypatch):
    w = make_world(db_session)
    w.calls = []
    sales = {"La Zeppa": 60000.0, "Dunedin Social Club": 40000.0, "Head Office": 0.0}

    def fake_execute(**kw):
        from app.connectors.tool_executor import ToolResult

        w.calls.append(kw)
        name = kw["params"]["venue"]
        if name == "Dunedin Social Club" and w.fail_dsc:
            return ToolResult(success=False, payload=None, error="Loaded said no")
        return ToolResult(
            success=True,
            payload={
                "week": {
                    "week_ending": "2026-11-22",
                    "label": "Week ending Sun 22 Nov 2026",
                },
                "mtd": {"label": "1 – 22 Nov 2026"},
                "venues": [
                    {
                        "venue": name,
                        "venue_id": kw["params"]["venue_id"],
                        "week": _figures(sales[name]),
                        "mtd": _figures(sales[name] * 3),
                    }
                ],
                "group": {},
                "warnings": [f"{name}: cash-ups unavailable"],
                "budget_plan_available": True,
                "notes": [],
            },
        )

    w.fail_dsc = False
    monkeypatch.setattr(
        "app.connectors.tool_executor.execute_connector_tool", fake_execute
    )
    return client, w


class TestSummaryRoute:
    def test_the_owner_gets_every_venue_on_its_own_token_and_the_group(self, api):
        client, w = api
        out = client.get(
            "/api/finance/summary",
            params={"week_ending": "2026-11-22"},
            headers=auth_headers(w.owner),
        )
        assert out.status_code == 200, out.text
        body = out.json()
        assert [v["venue"] for v in body["venues"]] == [
            "Dunedin Social Club",
            "Head Office",
            "La Zeppa",
        ]
        assert (
            body["week_ending"] == "2026-11-22" and body["week_start"] == "2026-11-16"
        )
        assert body["group"]["week"]["sales"] == 100000.0
        assert body["group"]["week"]["wage_pct"] == round(
            25000.0 / round(100000.0 / 1.15, 2), 4
        )
        assert body["group"]["week"]["budget_pct"] == round(100000.0 / 180000.0, 4)
        assert body["can_see_group"] is True
        assert len(body["warnings"]) == 3
        for call in w.calls:
            assert (
                call["action"] == "get_weekly_financials"
                and call["strict_venue"] is True
            )
            assert (
                call["venue_id"] == call["params"]["venue_id"]
                and call["user"].id == w.owner.id
            )
            assert call["params"]["week_ending"] == "2026-11-22"

    def test_a_manager_sees_only_their_venues_and_no_group(self, api):
        client, w = api
        out = client.get(
            "/api/finance/summary",
            params={"week_ending": "2026-11-22"},
            headers=auth_headers(w.manager),
        )
        assert out.status_code == 200, out.text
        body = out.json()
        assert [v["venue"] for v in body["venues"]] == ["La Zeppa"]
        assert body["group"] is None and body["can_see_group"] is False
        assert [c["params"]["venue"] for c in w.calls] == ["La Zeppa"]
        # Asking for Dunedin by id is refused, not silently dropped.
        out = client.get(
            "/api/finance/summary",
            params={"week_ending": "2026-11-22", "venues": w.dsc.id},
            headers=auth_headers(w.manager),
        )
        assert out.status_code == 403

    def test_a_payroll_admin_sees_the_head_office_and_a_team_member_nothing(self, api):
        client, w = api
        out = client.get(
            "/api/finance/summary",
            params={"week_ending": "2026-11-22"},
            headers=auth_headers(w.payroll_admin),
        )
        assert out.status_code == 200
        assert {v["venue"] for v in out.json()["venues"]} == {
            "La Zeppa",
            "Dunedin Social Club",
        }
        assert out.json()["group"] is not None
        assert (
            client.get(
                "/api/finance/summary", headers=auth_headers(w.team_member)
            ).status_code
            == 403
        )

    def test_a_failed_venue_is_a_flagged_row_and_stays_out_of_the_group(self, api):
        client, w = api
        w.fail_dsc = True
        body = client.get(
            "/api/finance/summary",
            params={"week_ending": "2026-11-22"},
            headers=auth_headers(w.owner),
        ).json()
        dsc = next(v for v in body["venues"] if v["venue"] == "Dunedin Social Club")
        assert dsc["error"] == "Loaded said no" and "week" not in dsc
        assert body["group"]["week"]["sales"] == 60000.0
