"""get_labour strips pay detail for anyone without payroll:read (owner
decision D8, Oct 2026): hourlyRate, payRateBreakdown and each shift's own cost
go; a venue's totals stay. The consolidator asks the engine
(``norm.caller_holds``) and fails closed when it can't answer.

Exec'd under the REAL sandbox namespace with the labour test's fixtures."""

from tests import consolidator_harness as harness
from tests.test_labour_consolidator import CODE, Api


class NoPay(Api):
    """An engine answering for a person without payroll:read."""

    def _for(self, connector, action, params):
        if action == "caller_holds":
            self.seen.append((action, dict(params or {})))
            return {"holds": False}
        return super()._for(connector, action, params)


class CannotSay(Api):
    """An engine with no such tool — the old config, or a bare script."""

    def _for(self, connector, action, params):
        if action == "caller_holds":
            raise AssertionError("unexpected action caller_holds")
        return super()._for(connector, action, params)


def _run(api, **params):
    return harness.run(CODE, api, venue="Bessie", period="saturday", **params)


class TestWithoutPayrollRead:
    def test_the_roster_keeps_every_field_but_the_pay_ones(self):
        out = _run(NoPay(), view="roster")
        shifts = out["data"]["rosteredShifts"]
        assert len(shifts) == 2
        first = shifts[0]
        assert first["id"] == "shift-1" and first["clockinTime"]
        assert first["totalHours"] == 4.0
        for key in ("hourlyRate", "payRateBreakdown", "totalCost"):
            assert key not in first
        assert out["data"]["venueId"] == "v-bessie"  # roster-level fields survive
        assert "payroll:read" in out["pay_detail"]

    def test_attendance_detail_drops_per_shift_cost_and_keeps_totals(self):
        out = _run(NoPay(), view="attendance", group_by="detail")
        for row in out["rostered"] + out["clockins"] + out["leave"]:
            assert "cost" not in row and "hours" in row
        assert out["totals"]["rostered_cost"] == 190.0
        assert out["totals"]["actual_cost"] > 0

    def test_attendance_by_staff_keeps_the_aggregates(self):
        out = _run(NoPay(), view="attendance", group_by="staff")
        assert all("actual_cost" in r for r in out["rows"])
        assert "pay_detail" in out

    def test_timeclock_entries_lose_their_cost_the_summary_keeps_it(self):
        out = _run(NoPay(), view="timeclock")
        assert all("totalCost" not in c for c in out["data"])
        assert out["summary"]["worked_cost"] > 0

    def test_the_staff_list_loses_rates(self):
        out = _run(NoPay(), view="staff")
        assert out["rows"] and all(
            "hourly_rate" not in r and "name" in r for r in out["rows"]
        )

    def test_vs_actual_is_the_venues_report_and_untouched(self):
        api = NoPay()
        out = _run(api, view="vs_actual")
        assert "pay_detail" not in out
        assert not [a for a, _ in api.seen if a == "caller_holds"]

    def test_an_engine_that_cannot_say_strips(self):
        out = _run(CannotSay(), view="roster")
        assert "hourlyRate" not in out["data"]["rosteredShifts"][0]


class TestWithPayrollRead:
    def test_a_holder_sees_rates_and_no_note(self):
        api = Api()  # answers caller_holds with True
        out = _run(api, view="roster")
        assert out["data"]["rosteredShifts"][0]["hourlyRate"] == 25.0
        assert "pay_detail" not in out
        assert ("caller_holds", {"scope": "payroll:read"}) in api.seen
