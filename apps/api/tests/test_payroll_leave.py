"""Leave payouts (plan §9 step 3, §9.1): the four sick-leave gates, the bond
consent refusal, the annual and generic workflows, the eligibility facts
from the person's own clock-ins, the health-information read rule, and the
shadow rule with rehearsal.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from app.services.finance_core.errors import Invalid, NotFound, NotLive
from app.services.payroll import leave as leave_svc
from app.services.payroll import rules as rules_svc
from tests.payroll_world import (
    MONDAY,
    FakeLoaded,
    install,
    make_world,
    payrun_result,
    shift,
)


@pytest.fixture
def w(db_session):
    return make_world(db_session)


def _sick_inputs(**over):
    base = {
        "qualification": {"six_months": True, "avg_hours": True, "continuous": True},
        "balance": {"available_days": 5, "daily_hours": 6},
        "certificate": {"received": True},
        "payout": {
            "day_hours": [
                {"date": "2026-10-13", "hours": 6},
                {"date": "2026-10-14", "hours": 6},
            ],
            "hourly_rate_cents": 2500,
        },
    }
    for key, value in over.items():
        base[key] = {**base[key], **value}
    return base


def _record(w, **over):
    args = dict(
        venue_id=w.laz.id,
        week_start=MONDAY,
        loaded_request_id="lr-sick",
        staff_member_id="b",
        staff_name="Ben Roy",
        leave_type="NZ Sick",
        requested_cents=30000,
        inputs=_sick_inputs(),
    )
    args.update(over)
    return leave_svc.record_payout(w.db, w.ctx(), **args)


class TestSickGates:
    def test_all_gates_pass_pays_hours_times_rate(self, w):
        payout = _record(w)
        assert payout.workflow_kind == "sick" and payout.paid_cents == 30000
        decision = payout.workflow_inputs["decision"]
        assert decision["all_gates_passed"] is True
        assert (decision["requested_hours"], decision["available_hours"]) == (
            12.0,
            30.0,
        )

    @pytest.mark.parametrize(
        "broken",
        [
            {"qualification": {"six_months": False}},
            {"qualification": {"avg_hours": False}},
            {"qualification": {"continuous": False}},
            {"balance": {"available_days": 1}},  # 1 day × 6 h = 6 h < 12 h requested
            {"certificate": {"received": False}},
        ],
    )
    def test_any_failed_gate_pays_zero(self, w, broken):
        payout = _record(w, inputs=_sick_inputs(**broken))
        assert payout.paid_cents == 0
        assert payout.workflow_inputs["decision"]["all_gates_passed"] is False
        assert (
            payout.workflow_inputs["decision"]["calculated_cents"] == 30000
        )  # the figure stays visible

    def test_a_named_override_passes_its_gate(self, w):
        inputs = _sick_inputs(
            certificate={
                "received": False,
                "override": True,
                "override_notes": "James approved 14 Oct",
            }
        )
        payout = _record(w, inputs=inputs)
        assert payout.paid_cents == 30000
        assert (
            payout.workflow_inputs["certificate"]["override_notes"]
            == "James approved 14 Oct"
        )

    def test_balance_uses_the_persons_day_not_a_flat_eight(self, w):
        # 2 days' balance: at the rule's default 8 h/day that is 16 h ≥ 12 h; at this person's 5 h/day it is 10 h < 12 h.
        assert (
            _record(
                w,
                inputs=_sick_inputs(balance={"available_days": 2, "daily_hours": None}),
            ).paid_cents
            == 30000
        )
        assert (
            _record(
                w,
                loaded_request_id="lr-2",
                inputs=_sick_inputs(balance={"available_days": 2, "daily_hours": 5}),
            ).paid_cents
            == 0
        )

    def test_the_default_day_is_the_dated_rule(self, w):
        rules_svc.set_rule(
            w.db,
            w.ctx(),
            rule="sick_day_hours",
            effective_from=dt.date(2026, 1, 1),
            params={"hours": 4},
            source="test",
        )
        # 2 days × 4 h = 8 h < 12 h → blocked under the organisation's own rule.
        assert (
            _record(
                w,
                inputs=_sick_inputs(balance={"available_days": 2, "daily_hours": None}),
            ).paid_cents
            == 0
        )
        assert rules_svc.params_for(
            w.db, w.org.id, "sick_day_hours", dt.date(2025, 6, 1)
        ) == {"hours": 8}

    def test_paid_override_is_zero_when_a_gate_fails(self, w):
        payout = _record(
            w, paid_cents=12345, inputs=_sick_inputs(certificate={"received": False})
        )
        assert payout.paid_cents == 0

    def test_sick_inputs_are_health_information(self, w):
        payout = _record(w)
        reader = dataclasses.replace(w.ctx(), scopes=frozenset({"payroll:read"}))
        assert leave_svc.payout_dict(payout, reader)["workflow_inputs"] == {
            "restricted": True
        }
        assert (
            leave_svc.payout_dict(payout, w.ctx())["workflow_inputs"]["decision"][
                "all_gates_passed"
            ]
            is True
        )


class TestAnnualAndGeneric:
    def test_annual_is_min_of_requested_and_available_less_bond(self, w):
        payout = leave_svc.record_payout(
            w.db,
            w.ctx(),
            venue_id=w.laz.id,
            week_start=MONDAY,
            loaded_request_id="lr-a",
            leave_type="NZ Annual",
            requested_cents=60000,
            inputs={"holiday_pay_available_cents": 50000, "bond_held_in_ace": True},
        )
        assert payout.workflow_kind == "annual"
        assert payout.paid_cents == 35000  # 500 − 150 bond
        assert (
            payout.bond_deducted_cents == 15000 and payout.bond_consent_file_id is None
        )
        assert payout.workflow_inputs["decision"] == {
            "max_payable_cents": 35000,
            "calculated_cents": 35000,
            "bond_consent": "held_in_ace",
        }

    def test_the_bond_needs_a_consent_record(self, w):
        with pytest.raises(Invalid, match="written consent"):
            leave_svc.record_payout(
                w.db,
                w.ctx(),
                venue_id=w.laz.id,
                week_start=MONDAY,
                loaded_request_id="lr-a",
                leave_type="NZ Annual",
                requested_cents=60000,
                inputs={"holiday_pay_available_cents": 50000},
            )
        # No bond to hold → no consent needed.
        payout = leave_svc.record_payout(
            w.db,
            w.ctx(),
            venue_id=w.laz.id,
            week_start=MONDAY,
            loaded_request_id="lr-a",
            leave_type="NZ Annual",
            requested_cents=60000,
            inputs={"holiday_pay_available_cents": 50000, "uniform_bond_cents": 0},
        )
        assert payout.paid_cents == 50000 and payout.bond_deducted_cents == 0

    def test_a_consent_file_must_exist_in_this_organisation(self, w):
        with pytest.raises(NotFound):
            leave_svc.record_payout(
                w.db,
                w.ctx(),
                venue_id=w.laz.id,
                week_start=MONDAY,
                loaded_request_id="lr-a",
                leave_type="NZ Annual",
                requested_cents=60000,
                inputs={
                    "holiday_pay_available_cents": 50000,
                    "bond_consent_file_id": "nope",
                },
            )

    def test_annual_override_cannot_exceed_either_cap(self, w):
        with pytest.raises(Invalid, match="exceeds"):
            leave_svc.record_payout(
                w.db,
                w.ctx(),
                venue_id=w.laz.id,
                week_start=MONDAY,
                loaded_request_id="lr-a",
                leave_type="NZ Annual",
                requested_cents=60000,
                paid_cents=40000,
                inputs={"holiday_pay_available_cents": 50000, "bond_held_in_ace": True},
            )

    def test_generic_pays_at_most_requested(self, w):
        payout = leave_svc.record_payout(
            w.db,
            w.ctx(),
            venue_id=w.laz.id,
            week_start=MONDAY,
            loaded_request_id="lr-b",
            leave_type="NZ Bereavement",
            requested_cents=24000,
            paid_cents=20000,
        )
        assert payout.workflow_kind == "generic" and payout.paid_cents == 20000
        with pytest.raises(Invalid, match="exceeds"):
            leave_svc.record_payout(
                w.db,
                w.ctx(),
                venue_id=w.laz.id,
                week_start=MONDAY,
                loaded_request_id="lr-b",
                leave_type="NZ Bereavement",
                requested_cents=24000,
                paid_cents=25000,
                version=payout.version,
            )

    def test_one_row_per_request_and_week_with_a_version(self, w):
        from app.services.finance_core.errors import Conflict

        first = _record(w)
        again = _record(w, paid_cents=100, version=first.version)
        assert again.id == first.id and again.version == 2 and again.paid_cents == 100
        with pytest.raises(Conflict):
            _record(w, version=1)


class TestShadow:
    def test_shadow_refuses_unless_rehearsal(self, db_session):
        w = make_world(db_session, payroll="shadow")
        with pytest.raises(NotLive):
            _record(w)
        payout = _record(w, rehearsal=True)
        assert payout.rehearsal is True
        leave_svc.delete_payout(
            w.db, w.ctx(), payout.id
        )  # a rehearsal row may be removed in shadow
        # A row that is not a rehearsal (an Orbit import, say) stays untouchable in shadow.
        imported = _record(w, loaded_request_id="orbit-row", rehearsal=True)
        imported.rehearsal = False
        w.db.flush()
        with pytest.raises(NotLive):
            leave_svc.delete_payout(w.db, w.ctx(), imported.id)

    def test_off_refuses_everything(self, db_session):
        w = make_world(db_session, payroll="off")
        with pytest.raises(NotLive):
            _record(w, rehearsal=True)


class TestEligibility:
    def _rows(self, weeks_with_hours, hours=12.0):
        rows = []
        for i, monday in enumerate(weeks_with_hours):
            start = (
                dt.datetime.combine(
                    monday + dt.timedelta(days=1), dt.time(17, 0)
                ).isoformat()
                + "+13:00"
            )
            rows.append(
                shift(
                    f"e{i}",
                    "b",
                    "Ben Roy",
                    code="LZ002",
                    start=start,
                    end=start,
                    hours=hours,
                    cost_cents=int(hours * 2500),
                )
            )
        return rows

    def test_qualifies_from_loaded_start_date_and_own_clockins(self, w, monkeypatch):
        as_of = dt.date(2026, 10, 18)
        mondays = [MONDAY - dt.timedelta(days=7 * i) for i in range(26)]
        fake = install(
            monkeypatch,
            FakeLoaded(
                get_staff_member_raw={
                    "datestampStarted": "2025-11-03T00:00:00",
                    "IRDNumber": "never-read",
                },
                get_payrun=lambda v, p: payrun_result(
                    v, MONDAY, self._rows(mondays), view="staff"
                ),
            ),
        )
        out = leave_svc.sick_eligibility(w.db, w.ctx(), w.laz.id, "b", as_of)
        assert (
            out["employment_start_date"] == "2025-11-03"
            and out["start_date_source"] == "loaded"
        )
        assert out["qualifies_six_months"] is True and out["months_employed"] > 11
        assert out["weeks_counted"] == 26 and out["avg_hours_per_week"] == 12.0
        assert (
            out["qualifies_avg_hours"] is True and out["qualifies_continuous"] is True
        )
        assert out["usual_daily_hours"] == 12.0
        payrun_call = next(c for c in fake.calls if c[1] == "get_payrun")
        assert payrun_call[2]["weeks"] == 26 and payrun_call[2]["staff_member_ids"] == [
            "b"
        ]

    def test_a_gap_week_fails_continuity_unless_the_month_rule_holds(
        self, w, monkeypatch
    ):
        as_of = dt.date(2026, 10, 18)
        mondays = [MONDAY - dt.timedelta(days=7 * i) for i in range(26)]
        gappy = [m for m in mondays if m != MONDAY - dt.timedelta(days=21)]
        install(
            monkeypatch,
            FakeLoaded(
                get_payrun=lambda v, p: payrun_result(
                    v, MONDAY, self._rows(gappy, hours=8.0), view="staff"
                )
            ),
        )
        out = leave_svc.sick_eligibility(
            w.db, w.ctx(), w.laz.id, "b", as_of, start_date="2026-01-05"
        )
        assert out["start_date_source"] == "approver"
        # 8 h in every other week of September → the month has 24 h < 40: not continuous.
        assert out["qualifies_continuous"] is False
        install(
            monkeypatch,
            FakeLoaded(
                get_payrun=lambda v, p: payrun_result(
                    v, MONDAY, self._rows(gappy, hours=20.0), view="staff"
                )
            ),
        )
        out = leave_svc.sick_eligibility(
            w.db, w.ctx(), w.laz.id, "b", as_of, start_date="2026-01-05"
        )
        assert out["qualifies_continuous"] is True  # ≥ 40 h every month

    def test_a_short_tenure_fails_six_months(self, w, monkeypatch):
        install(
            monkeypatch,
            FakeLoaded(
                get_payrun=lambda v, p: payrun_result(v, MONDAY, [], view="staff")
            ),
        )
        out = leave_svc.sick_eligibility(
            w.db, w.ctx(), w.laz.id, "b", dt.date(2026, 10, 18), start_date="2026-08-01"
        )
        assert (
            out["qualifies_six_months"] is False and out["qualifies_avg_hours"] is False
        )


class TestRequestedBesidePaid:
    def test_requested_rows_join_the_stored_payout(self, w, monkeypatch):
        rows = [
            shift(
                "l1",
                "a",
                "Ana Lee",
                code="LZ001",
                shift_type="Leave",
                hours=8.0,
                cost_cents=20000,
                leave_request_id="lr1",
                leave_type="NZ Annual",
            ),
            shift(
                "l2",
                "b",
                "Ben Roy",
                code="LZ002",
                shift_type="Leave",
                hours=6.0,
                cost_cents=15000,
                leave_request_id="lr-sick",
                leave_type="NZ Sick",
            ),
        ]
        install(
            monkeypatch,
            FakeLoaded(
                get_payrun=lambda v, p: payrun_result(v, MONDAY, rows, view="leave")
            ),
        )
        _record(
            w,
            requested_cents=15000,
            inputs=_sick_inputs(
                payout={"day_hours": [{"date": "2026-10-13", "hours": 6}]}
            ),
        )
        out = leave_svc.requested(w.db, w.ctx(), w.laz.id, MONDAY)
        by_id = {r["leave_request_id"]: r for r in out["rows"]}
        assert (
            by_id["lr1"]["payout"] is None and by_id["lr1"]["workflow_kind"] == "annual"
        )
        assert by_id["lr-sick"]["payout"]["paid_cents"] == 15000
        assert out["totals"] == {"requested_cents": 35000, "paid_cents": 15000}


def test_sick_email_goes_to_the_payroll_contact_in_shadow(db_session, monkeypatch):
    w = make_world(db_session, payroll="shadow")
    sent = []

    def fake_send(template, to, context, db, organization_id=None, thread_id=None):
        from app.db.models import EmailLog

        log = EmailLog(
            organization_id=organization_id,
            sender_type="system",
            sender_email="norm@test",
            to_addresses=to,
            subject=context.get("subject", ""),
            template_name=template,
            status="sent",
        )
        db.add(log)
        db.flush()
        sent.append((template, to, context))
        return log.id

    import app.services.email_service as email_service

    monkeypatch.setattr(email_service, "send_system_email", fake_send)
    payout = _record(
        w, rehearsal=True, inputs=_sick_inputs(certificate={"received": False})
    )
    out = leave_svc.send_sick_email(
        w.db, w.ctx(), payout, kind="certificate", to="manager@venue.test"
    )
    assert out["status"] == "sent" and out["to"] == "pay@cookbrothers.test"
    assert (
        sent[0][0] == "payroll_sick_certificate"
        and sent[0][2]["redirected_from"] == "manager@venue.test"
    )
    with pytest.raises(Invalid):
        leave_svc.send_sick_email(w.db, w.ctx(), payout, kind="other", to="x@y.z")
