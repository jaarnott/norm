"""Wages reconciliation, the journal and ICO drafts, the mapping, the push
ledger, the status refresh, the two engine-only built-ins and the
``/api/payroll/reconciliation`` + ``/api/payroll/xero`` routes (F4; plan §10,
§17; xero-api.md).

One fixture week drives everything: La Zeppa, WE 18 Oct 2026, with Glass
Goose as the counterparty. Its figures are built so that Orbit's nine-term
formula reconciles to the cent (report 03 §4) and the journal balances from
the data — the Other-team and Reclass-Gratuities lines are exactly what
Orbit's builder lacked and a person plugged by hand (report 03 §13 item 1).

Every signed-in check is a NON-admin user with a system role (owner,
payroll_admin, manager, team_member): a platform admin bypasses every org
rule, so a test as admin would prove nothing. The end-to-end route tests run
the real consolidators under the real sandbox with Xero faked at the HTTP
boundary (``spec_executor.execute_spec``) — nothing reaches Xero.
"""

from __future__ import annotations

import copy
import datetime as dt
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.agents import payroll_xero_tools as tools
from app.connectors.base import ConnectorResult
from app.connectors.tool_executor import ToolResult
from app.db.config_models import ConnectionSpec
from app.db.models import Connection, OrganizationMembership, Thread
from app.db.payroll_models import (
    PayAccrual,
    PayAceInput,
    PayAdjustment,
    PayEntity,
    PayGratuityAllocation,
    PayGratuityBatch,
    PayJournalDraft,
    PayLeavePayout,
    PayShiftFact,
    PaySnapshot,
    PayWeek,
    PayXeroPush,
)
from sqlalchemy.orm.attributes import flag_modified

from app.services import caller_scope
from app.services.finance_core import modes
from app.services.finance_core.ctx import build_ctx
from app.services.finance_core.errors import Conflict, Forbidden, Invalid
from app.services.payroll import intercompany as ico_svc
from app.services.payroll import journal as journal_svc
from app.services.payroll import reconciliation as recon_svc
from app.services.payroll import xero_mapping, xero_pushes, xero_status
from tests.org_helpers import (
    auth_headers,
    grant_venue,
    make_org,
    make_user,
    make_venue,
    seed_system_roles,
)

MONDAY = dt.date(2026, 10, 12)
SUNDAY = dt.date(2026, 10, 18)
UTC = dt.timezone.utc

# The chart of accounts the fake Xero serves (classes per report 03 §8.2).
CHART = [
    ("814", "Wages Payable - Payroll", "CURRLIAB", "LIABILITY"),
    ("21305", "Payroll Taxes", "CURRLIAB", "LIABILITY"),
    ("21260", "Unpaid Wages", "CURRLIAB", "LIABILITY"),
    ("21270", "Security Owed", "CURRLIAB", "LIABILITY"),
    ("21280", "Intercompany Wages", "CURRLIAB", "LIABILITY"),
    ("55150", "Bar & Floor Team", "EXPENSE", "EXPENSE"),
    ("55160", "Other Team", "EXPENSE", "EXPENSE"),
    ("55170", "Kitchen Team", "EXPENSE", "EXPENSE"),
    ("55300", "Leave & Entitlements", "EXPENSE", "EXPENSE"),
    ("67300", "DJ's", "EXPENSE", "EXPENSE"),
    ("41100", "Bar Sales", "REVENUE", "REVENUE"),
    ("52100", "Salaries & Wages", "EXPENSE", "EXPENSE"),
    ("63400", "Security", "EXPENSE", "EXPENSE"),
]
NAMES = {code: name for code, name, _, _ in CHART}
ACCOUNTS = [
    {"code": c, "name": n, "type": t, "class": k, "tax_type": "NONE"}
    for c, n, t, k in CHART
]
PAYROLL_MAP = {
    "wages_payable": "814",
    "payroll_taxes_payable": "21305",
    "unpaid_wages": "21260",
    "security_owed": "21270",
    "intercompany_wages": "21280",
    "bar_floor": "55150",
    "kitchen": "55170",
    "other_team": "55160",
    "leave_entitlements": "55300",
    "entertainers": "67300",
    "gratuities_reclass": "41100",
    "rounding": "55300",
}


# ===========================================================================
# World
# ===========================================================================


def _member(db, org, role, venues=()):
    user = make_user(db)
    db.add(
        OrganizationMembership(
            id=str(uuid.uuid4()),
            user_id=user.id,
            organization_id=org.id,
            role="member",
            role_id=role.id,
        )
    )
    db.flush()
    for v in venues:
        grant_venue(db, user, v)
    return user


def _connect(db, venue, tenant, contacts):
    db.add(
        Connection(
            connector_name="xero",
            venue_id=venue.id,
            enabled="true",
            access_token="tok",
            config={
                "xero_tenant_id": tenant,
                "xero_short_code": "!abc",
                "xero_mapping": {
                    "payroll": {
                        k: {"code": v, "name": NAMES[v]} for k, v in PAYROLL_MAP.items()
                    },
                    "intercompany": {
                        "wages_account": "21280",
                        "leave_account": "55300",
                        "contacts": contacts,
                    },
                },
            },
            oauth_metadata={"tenant_name": f"{venue.name} Ltd"},
        )
    )
    db.flush()


def _snapshot(db, org, week, source="close"):
    snap = PaySnapshot(
        organization_id=org.id,
        week_id=week.id,
        source=source,
        shift_count=0,
        content_sha256="a" * 64,
    )
    db.add(snap)
    db.flush()
    return snap


def _fact(
    db,
    org,
    snap,
    *,
    staff,
    name,
    cents,
    hours,
    group="bar_floor",
    pay_code=None,
    shift_type="Clockin",
    from_other=False,
    worked=None,
    worked_loaded="lh-self",
    leave_request_id=None,
    approved=True,
):
    f = PayShiftFact(
        organization_id=org.id,
        snapshot_id=snap.id,
        source_shift_id=str(uuid.uuid4()),
        staff_member_id=staff,
        staff_name=name,
        pay_code=pay_code or staff.upper(),
        shift_type=shift_type,
        remuneration_type="HourlyRate",
        role_name=group,
        cost_group=group,
        worked_venue_loaded_id=worked_loaded,
        worked_venue_id=worked,
        is_from_other_venue=from_other,
        clockin_at=dt.datetime(2026, 10, 13, 5, tzinfo=UTC),
        clockout_at=dt.datetime(2026, 10, 13, 11, tzinfo=UTC),
        trading_date=dt.date(2026, 10, 13),
        hours=Decimal(str(hours)),
        cost_cents=cents,
        leave_request_id=leave_request_id,
        leave_type_name="NZ Annual" if leave_request_id else None,
        is_approved=approved,
    )
    db.add(f)
    db.flush()
    snap.shift_count += 1
    return f


def make_world(db, *, mode="live"):
    roles = seed_system_roles(db)
    org = make_org(db, "Cook Bros")
    modes.set_mode(db, org.id, "payroll", mode)
    modes.set_mode(db, org.id, "entertainers", mode)
    a = make_venue(db, "La Zeppa", org)
    b = make_venue(db, "Glass Goose", org)
    head = make_venue(db, "Head Office", org)
    head.is_trading = False
    owner = _member(db, org, roles["owner"], venues=[a, b])
    payroll_admin = _member(db, org, roles["payroll_admin"], venues=[a, b])
    manager = _member(db, org, roles["manager"], venues=[a, b])
    team_member = _member(db, org, roles["team_member"], venues=[a])
    other_org = make_org(db, "Rival Co")
    modes.set_mode(db, other_org.id, "payroll", "live")
    other_venue = make_venue(db, "Rival Bar", other_org)
    other_user = _member(db, other_org, roles["owner"], venues=[other_venue])

    _connect(
        db,
        a,
        "tenant-a",
        {
            b.id: {
                "contact_id": "c-gg",
                "name": "Cook Brothers Bars Federal Street Limited",
            }
        },
    )
    _connect(
        db,
        b,
        "tenant-b",
        {
            a.id: {
                "contact_id": "c-lz",
                "name": "Cook Brothers Bars Victoria Park Limited",
            }
        },
    )
    for venue, company in ((a, "lh-a"), (b, "lh-b")):
        db.add(
            PayEntity(
                organization_id=org.id,
                venue_id=venue.id,
                loaded_company_id=company,
                payroll_on=True,
            )
        )
    db.flush()
    week_a = PayWeek(organization_id=org.id, venue_id=a.id, week_start=MONDAY)
    week_b = PayWeek(organization_id=org.id, venue_id=b.id, week_start=MONDAY)
    db.add_all([week_a, week_b])
    db.flush()
    snap_a = _snapshot(db, org, week_a)
    snap_b = _snapshot(db, org, week_b)

    # La Zeppa's own week: bar & floor 11,000 (incl. a 300 SECURITY shift),
    # kitchen 6,000, an untagged role 500, one leave request, and Dana lent
    # to Glass Goose for 400.
    _fact(db, org, snap_a, staff="ana", name="Ana", cents=500000, hours=20)
    _fact(db, org, snap_a, staff="ben", name="Ben", cents=570000, hours=22)
    _fact(
        db,
        org,
        snap_a,
        staff="sec",
        name="Sam Security",
        cents=30000,
        hours=6,
        pay_code="SECURITY",
    )
    _fact(
        db,
        org,
        snap_a,
        staff="cat",
        name="Cat",
        cents=600000,
        hours=30,
        group="kitchen",
    )
    _fact(db, org, snap_a, staff="dee", name="Dee", cents=50000, hours=5, group="other")
    _fact(
        db,
        org,
        snap_a,
        staff="dana",
        name="Dana",
        cents=40000,
        hours=10,
        from_other=True,
        worked=b.id,
        worked_loaded="lh-b",
    )
    _fact(
        db,
        org,
        snap_a,
        staff="ana",
        name="Ana",
        cents=125000,
        hours=8,
        shift_type="Leave",
        leave_request_id="lr-1",
    )
    # Glass Goose's week: Fay at home, Eli lent to La Zeppa for 200.
    _fact(db, org, snap_b, staff="fay", name="Fay", cents=300000, hours=15)
    _fact(
        db,
        org,
        snap_b,
        staff="eli",
        name="Eli",
        cents=20000,
        hours=4,
        group="other",
        from_other=True,
        worked=a.id,
        worked_loaded="lh-a",
    )

    db.add(
        PayLeavePayout(
            organization_id=org.id,
            venue_id=a.id,
            week_start=MONDAY,
            loaded_request_id="lr-1",
            staff_member_id="ana",
            staff_name="Ana",
            leave_type="NZ Annual",
            requested_cents=125000,
            paid_cents=120000,
            workflow_kind="annual",
        )
    )
    db.add(
        PayAccrual(
            organization_id=org.id,
            week_id=week_a.id,
            accrued_cents=20000,
            paid_cents=15000,
        )
    )
    adjustment = PayAdjustment(
        organization_id=org.id,
        week_id=week_a.id,
        label="Public holiday pay",
        amount_cents=110000,
        xero_account_code="55300",
        xero_account_name="Leave & Entitlements",
        report_target="wages",
    )
    db.add(adjustment)
    ace = PayAceInput(
        organization_id=org.id,
        week_id=week_a.id,
        gross_cents=2026000,
        net_cents=1492220,
        esct_cents=7794,
        kiwisaver_cents=48046,
    )
    db.add(ace)
    batch = PayGratuityBatch(
        organization_id=org.id,
        venue_id=a.id,
        week_start=MONDAY,
        period="2026-09",
        total_cents=10000,
    )
    db.add(batch)
    db.flush()
    db.add_all(
        [
            PayGratuityAllocation(
                organization_id=org.id,
                batch_id=batch.id,
                staff_member_id="dana",
                staff_name="Dana",
                amount_cents=6000,
                payroll_venue_id=a.id,
            ),
            PayGratuityAllocation(
                organization_id=org.id,
                batch_id=batch.id,
                staff_member_id="eli",
                staff_name="Eli",
                amount_cents=4000,
                payroll_venue_id=b.id,
            ),
        ]
    )
    db.flush()
    w = SimpleNamespace(
        db=db,
        org=org,
        roles=roles,
        a=a,
        b=b,
        head=head,
        owner=owner,
        payroll_admin=payroll_admin,
        manager=manager,
        team_member=team_member,
        other_org=other_org,
        other_venue=other_venue,
        other_user=other_user,
        week_a=week_a,
        week_b=week_b,
        snap_a=snap_a,
        snap_b=snap_b,
        ace=ace,
        adjustment=adjustment,
    )
    w.ctx = lambda user=None: build_ctx(db, user or owner)
    return w


@pytest.fixture
def w(db_session, monkeypatch):
    world = make_world(db_session)

    # The entertainers module is F5's: one paid line at La Zeppa (350.00)
    # and one still pending (carried forward — listed, not counted).
    def fake_rows(db, ctx, venue_id, week_start):
        if venue_id != world.a.id:
            return [], True, []
        lines = [
            SimpleNamespace(
                performance_id="p1",
                share_id=None,
                performer_name="DJ Vee",
                pay_code="DJV",
                hours=Decimal("3.5"),
                rate_cents=10000,
                amount_cents=35000,
                payment_status="paid",
                paid_at=None,
            ),
        ]
        return recon_svc_rows(lines)

    def recon_svc_rows(lines):
        rows = []
        for ln in lines:
            rows.append(
                {
                    "performance_id": ln.performance_id,
                    "share_id": ln.share_id,
                    "performer": ln.performer_name,
                    "pay_code": ln.pay_code,
                    "hours": float(ln.hours),
                    "rate_cents": ln.rate_cents,
                    "amount_cents": ln.amount_cents,
                    "payment_status": ln.payment_status,
                    "included": ln.payment_status == "paid",
                }
            )
        return rows, True, []

    monkeypatch.setattr(recon_svc, "entertainer_rows", fake_rows)
    monkeypatch.setattr(
        xero_mapping, "chart", lambda db, config_db, venue_id: list(ACCOUNTS)
    )
    return world


# ===========================================================================
# The formula
# ===========================================================================


class TestReconciliation:
    def test_the_nine_terms_reconcile_to_the_cent(self, w):
        r = recon_svc.compute(w.db, w.ctx(), w.a.id, MONDAY)
        t = r["terms"]
        assert r["week_ending"] == "2026-10-18" and r["snapshot"]["source"] == "close"
        # Worked at La Zeppa: own non-leave shifts + Eli (Glass Goose's) — not Dana.
        assert t["timeclock"]["cents"] == 1770000
        assert t["timeclock"]["by_cost_group"] == {
            "bar_floor": 1100000,
            "kitchen": 600000,
            "other": 70000,
        }
        assert t["timeclock"]["shifts"] == 6 and t["timeclock"]["hours"] == 87.0
        # In: Dana at Glass Goose 400. Out: Eli here 200 + Eli's gratuity share paid by GG 40.
        assert (
            t["intercompany"]["in_cents"],
            t["intercompany"]["out_cents"],
            t["intercompany"]["cents"],
        ) == (40000, 24000, 16000)
        assert (
            t["leave_paid"]["cents"] == 120000
            and t["leave_paid"]["requested_cents"] == 125000
        )
        assert t["security"]["cents"] == 30000 and len(t["security"]["fact_ids"]) == 1
        assert (t["accrued_additions"]["cents"], t["accrued_deductions"]["cents"]) == (
            20000,
            15000,
        )
        assert t["accrual"] == {
            "opening_cents": 0,
            "closing_cents": 5000,
            "opening_from": "no prior week",
        }
        assert t["other_adjustments"]["cents"] == 110000
        assert t["entertainers"]["cents"] == 35000
        assert t["gratuities"]["cents"] == 10000 and len(t["gratuities"]["rows"]) == 2
        assert r["computed_cents"] == 2026000
        assert r["ace"]["payroll_taxes_cents"] == 2026000 - 1492220 + 7794 + 48046
        assert r["difference_cents"] == 0 and r["band"] == "ok"
        assert r["blockers"] == [] and r["status"] == "none"

    def test_every_term_carries_its_rows(self, w):
        t = recon_svc.compute(w.db, w.ctx(), w.a.id, MONDAY)["terms"]
        assert len(t["timeclock"]["fact_ids"]) == 6
        assert t["leave_paid"]["rows"][0]["loaded_request_id"] == "lr-1"
        assert t["other_adjustments"]["rows"][0]["id"] == w.adjustment.id
        assert {r["staff_name"] for r in t["gratuities"]["rows"]} == {"Dana", "Eli"}
        cps = {
            (g["direction"], g["counterparty"]): g
            for g in t["intercompany"]["counterparties"]
        }
        assert cps[("in", "Glass Goose")]["lines"][0]["fact_ids"]
        out = cps[("out", "Glass Goose")]
        assert [ln["staff_name"] for ln in out["lines"]] == ["Eli", "Eli — Gratuities"]
        assert out["lines"][1]["gratuity"] is True

    def test_missing_ace_inputs_is_a_blocker_with_no_difference(self, w):
        w.db.delete(w.ace)
        w.db.flush()
        r = recon_svc.compute(w.db, w.ctx(), w.a.id, MONDAY)
        assert r["ace"] is None and r["difference_cents"] is None and r["band"] is None
        assert "ACE inputs not entered for this week" in r["blockers"]

    def test_rehearsal_rows_never_count(self, w):
        w.ace.rehearsal = True
        w.adjustment.rehearsal = True
        w.db.flush()
        r = recon_svc.compute(w.db, w.ctx(), w.a.id, MONDAY)
        assert r["ace"] is None and r["terms"]["other_adjustments"]["cents"] == 0

    def test_an_undecided_leave_request_and_a_codeless_adjustment_block(self, w):
        _fact(
            w.db,
            w.org,
            w.snap_a,
            staff="ben",
            name="Ben",
            cents=20000,
            hours=4,
            shift_type="Leave",
            leave_request_id="lr-2",
        )
        w.adjustment.xero_account_code = None
        w.db.flush()
        r = recon_svc.compute(w.db, w.ctx(), w.a.id, MONDAY)
        assert "1 leave request in the week has no payout decided" in r["blockers"]
        assert "adjustment 'Public holiday pay' has no GL code" in r["blockers"]
        assert r["terms"]["leave_paid"]["undecided_request_ids"] == ["lr-2"]

    def test_without_a_close_snapshot_the_figures_come_from_the_monday_one(self, w):
        w.snap_a.source = "monday"
        w.db.flush()
        r = recon_svc.compute(w.db, w.ctx(), w.a.id, MONDAY)
        assert r["snapshot"]["source"] == "monday"
        assert (
            "no close snapshot yet — figures are from the monday snapshot"
            in r["blockers"]
        )
        assert r["terms"]["timeclock"]["cents"] == 1770000
        w.db.delete(w.snap_a)
        w.db.flush()
        r = recon_svc.compute(w.db, w.ctx(), w.a.id, MONDAY)
        assert (
            r["snapshot"] is None and "no close snapshot for this week" in r["blockers"]
        )

    def test_a_pending_entertainer_line_is_listed_not_counted_and_blocks(
        self, w, monkeypatch
    ):
        rows = [
            {
                "performance_id": "p1",
                "share_id": None,
                "performer": "DJ Vee",
                "pay_code": "DJV",
                "hours": 3.5,
                "rate_cents": 10000,
                "amount_cents": 35000,
                "payment_status": "paid",
                "included": True,
            },
            {
                "performance_id": "p2",
                "share_id": None,
                "performer": "Band",
                "pay_code": "BND",
                "hours": 2.0,
                "rate_cents": 10000,
                "amount_cents": 20000,
                "payment_status": "pending",
                "included": False,
            },
        ]
        monkeypatch.setattr(
            recon_svc,
            "entertainer_rows",
            lambda db, ctx, v, ws: (
                rows,
                True,
                ["1 entertainer performance approved for payroll is not marked paid"],
            ),
        )
        r = recon_svc.compute(w.db, w.ctx(), w.a.id, MONDAY)
        assert r["terms"]["entertainers"]["cents"] == 35000
        assert (
            "1 entertainer performance approved for payroll is not marked paid"
            in r["blockers"]
        )

    def test_the_real_entertainer_feed_answers_zero_with_no_performances(
        self, w, monkeypatch
    ):
        monkeypatch.undo()
        rows, available, blockers = recon_svc.entertainer_rows(
            w.db, w.ctx(), w.a.id, MONDAY
        )
        assert rows == [] and blockers == []
        assert available in (True, False)  # F5's module present or not — both are zero

    def test_a_manager_is_refused_and_another_orgs_venue_is_invisible(self, w):
        with pytest.raises(Forbidden):
            recon_svc.compute(w.db, w.ctx(w.manager), w.a.id, MONDAY)
        with pytest.raises(Forbidden):
            recon_svc.compute(w.db, w.ctx(), w.other_venue.id, MONDAY)
        # A payroll admin sees only the venues they hold.
        assert (
            recon_svc.compute(w.db, w.ctx(w.payroll_admin), w.a.id, MONDAY)[
                "difference_cents"
            ]
            == 0
        )

    def test_the_group_overview_has_one_row_per_entity(self, w):
        g = recon_svc.group_overview(w.db, w.ctx(), MONDAY)
        rows = {r["venue"]: r for r in g["venues"]}
        assert set(rows) == {"La Zeppa", "Glass Goose"}
        lz, gg = rows["La Zeppa"], rows["Glass Goose"]
        assert (
            lz["difference_cents"] == 0
            and lz["band"] == "ok"
            and lz["adjustments_cents"] == 2026000 - 1770000
        )
        # Glass Goose: Fay 3,000 + Dana 400 worked there; net −160; no ACE yet.
        assert (
            gg["timeclock_cents"] == 340000
            and gg["ace_gross_cents"] is None
            and gg["band"] is None
        )
        assert gg["blockers"] >= 1
        assert (
            g["group"]["entities_without_ace"] == 1
            and g["group"]["difference_cents"] == 0
        )

    def test_week_adjustments_by_report_target(self, w):
        w.db.add(
            PayAdjustment(
                organization_id=w.org.id,
                week_id=w.week_a.id,
                label="Lieu day",
                amount_cents=-5000,
                xero_account_code="55300",
                report_target="leave",
            )
        )
        w.db.flush()
        assert recon_svc.week_adjustments(w.db, w.org.id, w.a.id, SUNDAY) == {
            "wages_cents": 110000,
            "leave_cents": -5000,
        }
        assert recon_svc.week_adjustments(w.db, w.org.id, w.b.id, MONDAY) == {
            "wages_cents": 0,
            "leave_cents": 0,
        }

    def test_the_accrual_opening_rolls_forward_from_the_prior_week(self, w):
        prior = PayWeek(
            organization_id=w.org.id,
            venue_id=w.a.id,
            week_start=MONDAY - dt.timedelta(days=7),
        )
        w.db.add(prior)
        w.db.flush()
        w.db.add(
            PayAccrual(
                organization_id=w.org.id,
                week_id=prior.id,
                opening_cents=1000,
                accrued_cents=500,
                paid_cents=200,
            )
        )
        w.db.flush()
        acc = recon_svc.compute(w.db, w.ctx(), w.a.id, MONDAY)["terms"]["accrual"]
        assert acc == {
            "opening_cents": 1300,
            "closing_cents": 6300,
            "opening_from": "prior week",
        }


class TestIntercompanyPage:
    def test_each_entity_sees_its_own_in_and_out(self, w):
        p = ico_svc.picture(w.db, w.ctx(), MONDAY)
        views = {v["venue"]: v for v in p["venues"]}
        lz, gg = views["La Zeppa"], views["Glass Goose"]
        assert (lz["in_cents"], lz["out_cents"], lz["net_cents"]) == (
            40000,
            24000,
            16000,
        )
        assert (gg["in_cents"], gg["out_cents"], gg["net_cents"]) == (
            24000,
            40000,
            -16000,
        )
        assert lz["in"][0]["contact"] == {
            "contact_id": "c-gg",
            "name": "Cook Brothers Bars Federal Street Limited",
        }
        assert gg["out"][0]["counterparty"] == "La Zeppa"

    def test_an_unknown_loaded_venue_is_a_warning_not_a_crash(self, w):
        _fact(
            w.db,
            w.org,
            w.snap_a,
            staff="zed",
            name="Zed",
            cents=1000,
            hours=1,
            from_other=True,
            worked=None,
            worked_loaded="lh-unknown",
        )
        v = ico_svc.picture(w.db, w.ctx(), MONDAY, w.a.id)["venues"][0]
        assert any("lh-unknown" in x for x in v["warnings"])
        assert v["in_cents"] == 41000  # still our cost


# ===========================================================================
# The journal
# ===========================================================================

#: Orbit's builder (buildWageJournal.ts) on the same inputs — the non-zero
#: lines of ``payload.built``; a Python port of its arithmetic.
ORBIT_BUILT = [
    ("Net Wages", "CR", 1492220, "814"),
    ("Payroll Taxes", "CR", 2026000 - 1492220 + 7794 + 48046, "21305"),
    ("Unpaid Wages", "CR", 5000, "21260"),
    ("Security Owed", "CR", 30000, "21270"),
    ("Intercompany — To Be Invoiced", "DR", 16000, "21280"),
    ("Bar & Floor Team", "DR", 1100000, "55150"),
    ("Kitchen Team", "DR", 600000, "55170"),
    ("Leave & Entitlements", "DR", 120000 + 7794 + 48046, "55300"),
    ("Entertainers (via payroll)", "DR", 35000, "67300"),
    ("Public holiday pay", "DR", 110000, "55300"),
]


def _lines(out):
    return [
        (ln["description"], ln["side"], ln["amount_cents"], ln["account_code"])
        for ln in out["lines"]
    ]


class TestJournal:
    def test_norms_lines_equal_orbits_built_plus_the_two_lines_orbit_lacked(self, w):
        out = journal_svc.assemble(w.db, w.ctx(), w.a.id, MONDAY)
        assert out["blockers"] == []
        mine = _lines(out)
        extra = [ln for ln in mine if ln[0] in ("Other team", "Reclass Gratuities")]
        assert extra == [
            ("Other team", "DR", 70000, "55160"),
            ("Reclass Gratuities", "DR", 10000, "41100"),
        ]
        assert [ln for ln in mine if ln not in extra] == ORBIT_BUILT
        # Norm balances from the data; Orbit's residual was exactly the two lines.
        assert (
            out["debit_cents"] == out["credit_cents"] == 2116840
            and out["residual_cents"] == 0
        )
        orbit_dr = sum(c for _, s, c, _ in ORBIT_BUILT if s == "DR")
        orbit_cr = sum(c for _, s, c, _ in ORBIT_BUILT if s == "CR")
        assert orbit_dr - orbit_cr == -(70000 + 10000)

    def test_account_names_ride_on_the_lines(self, w):
        out = journal_svc.assemble(w.db, w.ctx(), w.a.id, MONDAY)
        by = {ln["key"]: ln for ln in out["lines"]}
        assert by["wages_payable"]["account_name"] == "Wages Payable - Payroll"
        assert by["other_team"]["account_name"] == "Other Team"

    def test_a_residual_under_a_dollar_posts_a_rounding_line(self, w):
        w.ace.gross_cents += 49
        w.db.flush()
        out = journal_svc.assemble(w.db, w.ctx(), w.a.id, MONDAY)
        assert out["blockers"] == [] and out["residual_cents"] == -49
        assert out["lines"][-1]["key"] == "rounding"
        assert (
            out["lines"][-1]["side"],
            out["lines"][-1]["amount_cents"],
            out["lines"][-1]["account_code"],
        ) == ("DR", 49, "55300")
        assert out["debit_cents"] == out["credit_cents"]

    def test_a_dollar_or_more_refuses(self, w):
        w.ace.gross_cents += 150
        w.db.flush()
        out = journal_svc.assemble(w.db, w.ctx(), w.a.id, MONDAY)
        assert any("does not balance" in b and "$-1.50" in b for b in out["blockers"])
        with pytest.raises(Invalid):
            journal_svc.build(w.db, w.ctx(), w.a.id, MONDAY)

    def test_two_cents_with_no_rounding_account_is_absorbed_by_the_largest_line(
        self, w
    ):
        row = xero_mapping.connection(w.db, w.a.id)
        config = dict(row.config)
        config["xero_mapping"]["payroll"].pop("rounding")
        row.config = config
        w.db.flush()
        w.ace.gross_cents -= (
            2  # credits short by 2c → debits exceed → the largest credit takes it
        )
        w.db.flush()
        out = journal_svc.assemble(w.db, w.ctx(), w.a.id, MONDAY)
        assert out["blockers"] == [] and out["residual_cents"] == 2
        net = next(ln for ln in out["lines"] if ln["key"] == "wages_payable")
        assert (
            net["amount_cents"] == 1492222
            and net["source"]["absorbed_residual_cents"] == 2
        )
        assert any("absorbed" in n for n in out["notes"])
        assert out["debit_cents"] == out["credit_cents"]

    def test_a_missing_mapping_key_is_a_named_blocker_never_a_default(self, w):
        row = xero_mapping.connection(w.db, w.a.id)
        config = dict(row.config)
        config["xero_mapping"]["payroll"].pop("kitchen")
        config["xero_mapping"]["payroll"].pop("gratuities_reclass")
        row.config = config
        w.db.flush()
        out = journal_svc.assemble(w.db, w.ctx(), w.a.id, MONDAY)
        assert "Xero mapping missing: kitchen" in out["blockers"]
        assert "Xero mapping missing: gratuities_reclass" in out["blockers"]
        kitchen = next(ln for ln in out["lines"] if ln["key"] == "kitchen")
        assert kitchen["account_code"] is None

    def test_build_stores_the_journal_and_the_ico_drafts(self, w):
        out = journal_svc.build(w.db, w.ctx(), w.a.id, MONDAY)
        assert out["built"] is True and out["journal"]["stale"] is False
        assert out["journal"]["lines"] and out["journal"]["debit_cents"] == 2116840
        payload = out["journal"]["journal"]
        assert payload["Narration"] == "Payroll Journal - La Zeppa - WE 18.10.2026"
        assert (
            payload["Date"] == "2026-10-18"
            and payload["Status"] == "DRAFT"
            and payload["LineAmountTypes"] == "NoTax"
        )
        amounts = {
            ln["Description"]: ln["LineAmount"] for ln in payload["JournalLines"]
        }
        assert (
            amounts["Net Wages"] == -14922.20 and amounts["Bar & Floor Team"] == 11000.0
        )
        assert round(sum(amounts.values()), 2) == 0.0
        ico = {(d["kind"], d["counterparty"]): d for d in out["ico"]}
        inv = ico[("ico_invoice", "Glass Goose")]
        assert (
            inv["wages_cents"],
            inv["uplift_bp"],
            inv["uplift_cents"],
            inv["subtotal_cents"],
        ) == (40000, 1200, 4800, 44800)
        assert inv["contact"]["contact_id"] == "c-gg"
        bill = ico[("ico_bill", "Glass Goose")]
        assert (bill["wages_cents"], bill["uplift_cents"]) == (24000, 2880)
        assert [
            ln.get("staff_name")
            for ln in bill["lines"]
            if ln.get("key") != "leave_uplift"
        ] == ["Eli", "Eli — Gratuities"]
        assert (
            w.db.query(PayJournalDraft)
            .filter(PayJournalDraft.week_id == w.week_a.id)
            .count()
            == 3
        )

    def test_a_changed_input_makes_the_draft_stale_and_a_rebuild_replaces_it(self, w):
        first = journal_svc.build(w.db, w.ctx(), w.a.id, MONDAY)
        basis = first["journal"]["basis_sha256"]
        w.adjustment.amount_cents += 100
        w.ace.gross_cents += 100
        w.db.flush()
        now = journal_svc.current(w.db, w.ctx(), w.a.id, MONDAY)
        assert (
            now["journal"]["stale"] is True and now["proposed"]["basis_sha256"] != basis
        )
        again = journal_svc.build(w.db, w.ctx(), w.a.id, MONDAY)
        assert (
            again["journal"]["id"] != first["journal"]["id"]
            and again["journal"]["stale"] is False
        )
        assert (
            w.db.query(PayJournalDraft)
            .filter(PayJournalDraft.week_id == w.week_a.id)
            .count()
            == 3
        )

    def test_a_mapping_change_moves_the_basis_too(self, w):
        before = journal_svc.assemble(w.db, w.ctx(), w.a.id, MONDAY)["basis_sha256"]
        row = xero_mapping.connection(w.db, w.a.id)
        config = dict(row.config)
        config["xero_mapping"]["payroll"]["kitchen"] = {
            "code": "55150",
            "name": "Bar & Floor Team",
        }
        row.config = config
        w.db.flush()
        assert (
            journal_svc.assemble(w.db, w.ctx(), w.a.id, MONDAY)["basis_sha256"]
            != before
        )

    def test_tracking_rides_on_every_line_when_set(self, w):
        lines = [
            {
                "key": "x",
                "description": "X",
                "account_code": "814",
                "account_name": None,
                "side": "CR",
                "amount_cents": 100,
            }
        ]
        payload = journal_svc.xero_journal(
            lines, "V", SUNDAY, {"category_id": "tc", "option_id": "to"}
        )
        assert payload["JournalLines"][0]["Tracking"] == [
            {"TrackingCategoryID": "tc", "TrackingOptionID": "to"}
        ]

    def test_build_needs_payroll_write(self, w):
        with pytest.raises(Forbidden):
            journal_svc.build(w.db, w.ctx(w.manager), w.a.id, MONDAY)


# ===========================================================================
# The push ledger
# ===========================================================================


class TestPushLedger:
    def test_the_same_key_twice_is_one_row_and_a_second_live_row_is_refused(self, w):
        ctx = w.ctx()
        kw = dict(
            kind="journal",
            venue_id=w.a.id,
            week_start=MONDAY,
            xero_id="mj-1",
            xero_status="DRAFT",
        )
        first = xero_pushes.record_push(w.db, ctx, idempotency_key="k1", **kw)
        assert (
            first.xero_tenant_id == "tenant-a"
            and first.pushed_by == w.owner.id
            and first.week_id == w.week_a.id
        )
        assert (
            xero_pushes.record_push(w.db, ctx, idempotency_key="k1", **kw).id
            == first.id
        )
        with pytest.raises(Conflict):
            with w.db.begin_nested():
                xero_pushes.record_push(
                    w.db, ctx, idempotency_key="k2", **{**kw, "xero_id": "mj-2"}
                )
        xero_pushes.void_push(w.db, ctx, first.id, xero_status="DELETED")
        w.db.expire_all()
        assert (
            xero_pushes.live_push(
                w.db, w.org.id, venue_id=w.a.id, week_start=MONDAY, kind="journal"
            )
            is None
        )
        second = xero_pushes.record_push(
            w.db, ctx, idempotency_key="k2", **{**kw, "xero_id": "mj-2"}
        )
        assert second.id != first.id
        assert first.voided_by == w.owner.id and first.xero_status == "DELETED"

    def test_an_entertainer_bill_has_no_week_and_is_guarded_per_performance(self, w):
        ctx = w.ctx()
        kw = dict(
            kind="entertainer_bill",
            venue_id=w.a.id,
            xero_id="inv-1",
            xero_status="DRAFT",
        )
        xero_pushes.record_push(
            w.db, ctx, idempotency_key="e1", entertainer_performance_id="p1", **kw
        )
        xero_pushes.record_push(
            w.db, ctx, idempotency_key="e2", entertainer_performance_id="p2", **kw
        )
        with pytest.raises(Conflict):
            with w.db.begin_nested():
                xero_pushes.record_push(
                    w.db,
                    ctx,
                    idempotency_key="e3",
                    entertainer_performance_id="p1",
                    **kw,
                )
        assert len(xero_pushes.list_pushes(w.db, ctx, kind="entertainer_bill")) == 2

    def test_the_entertainers_modules_exact_call(self, w):
        """F5 calls record_push with these keywords by lazy import and falls
        back to writing the row itself on a TypeError — so the signature is
        part of the contract (build-notes, F5 → F4)."""
        row = xero_pushes.record_push(
            w.db,
            w.ctx(w.payroll_admin),
            kind="entertainer_bill",
            venue_id=w.a.id,
            week_start=None,
            entertainer_performance_id="perf-1:member-2",
            xero_tenant_id="tenant-stale",
            xero_id="inv-7",
            xero_number="INV-7",
            xero_status="DRAFT",
            request_payload={"invoice": {}},
            response={"InvoiceID": "inv-7"},
            idempotency_key="norm:ent-bill:perf-1:member-2",
            pushed_by=w.owner.id,
        )
        # The venue's own connection wins over the tenant the caller passed;
        # the person is the ctx's, not the keyword's.
        assert row.xero_tenant_id == "tenant-a" and row.pushed_by == w.payroll_admin.id
        assert (
            row.week_id is None and row.entertainer_performance_id == "perf-1:member-2"
        )
        # The head office venue has no Xero connection: the caller's tenant then stands.
        row = xero_pushes.record_push(
            w.db,
            w.ctx(),
            kind="entertainer_bill",
            venue_id=w.head.id,
            entertainer_performance_id="perf-9",
            idempotency_key="k9",
            xero_id="inv-9",
            xero_tenant_id="tenant-head",
        )
        assert row.xero_tenant_id == "tenant-head"

    def test_only_a_connected_venue_and_a_payroll_writer(self, w):
        with pytest.raises(Invalid):
            xero_pushes.record_push(
                w.db,
                w.ctx(),
                kind="journal",
                venue_id=w.head.id,
                idempotency_key="h",
                xero_id="x",
            )
        with pytest.raises(Forbidden):
            xero_pushes.record_push(
                w.db,
                w.ctx(w.manager),
                kind="journal",
                venue_id=w.a.id,
                idempotency_key="m",
                xero_id="x",
            )
        with pytest.raises(Invalid):
            xero_pushes.record_push(
                w.db,
                w.ctx(),
                kind="nope",
                venue_id=w.a.id,
                idempotency_key="n",
                xero_id="x",
            )


# ===========================================================================
# The mapping
# ===========================================================================


class TestMapping:
    def test_read_gives_bare_codes_whichever_shape_is_stored(self, w):
        out = xero_mapping.read(w.db, w.ctx(), w.a.id, config_db=w.db)
        assert out["connected"] and out["payroll"]["wages_payable"] == "814"
        assert (
            out["intercompany"]["wages_account"] == "21280"
            and out["intercompany"]["contacts"][w.b.id]["contact_id"] == "c-gg"
        )
        assert out["missing_for_journal"] == []
        assert any(
            a["code"] == "814" and a["class"] == "LIABILITY" for a in out["accounts"]
        )

    def test_the_class_rules(self, w):
        pay = {
            **PAYROLL_MAP,
            "intercompany_wages": "52100",
            "security_owed": "63400",
            "kitchen": "99999",
        }
        out = xero_mapping.save(
            w.db,
            w.ctx(),
            w.a.id,
            pay,
            {"wages_account": "21280"},
            config_db=w.db,
            validate_only=True,
        )
        by = {(i["level"], i["key"]): i["message"] for i in out["issues"]}
        assert "CBI" in by[("error", "intercompany_wages")]
        assert "security" in by[("warning", "security_owed")].lower()
        assert "not an active account" in by[("error", "kitchen")]
        assert out["saved"] is False

    def test_save_writes_the_importers_shape_and_refuses_on_an_error(self, w):
        ctx = w.ctx()
        with pytest.raises(Invalid):
            xero_mapping.save(
                w.db, ctx, w.a.id, {"nonsense": "1"}, None, config_db=w.db
            )
        out = xero_mapping.save(
            w.db,
            ctx,
            w.a.id,
            {**PAYROLL_MAP, "security_owed": "63400"},
            {
                "wages_account": "21280",
                "leave_account": "55300",
                "contacts": {
                    w.b.id: {"contact_id": "c-new", "name": "GG Ltd"},
                    w.a.id: {"contact_id": "self"},
                },
            },
            config_db=w.db,
        )
        assert (
            out["saved"] is False
        )  # the venue named as its own counterparty is an error
        assert any(i["key"] == f"intercompany.contacts.{w.a.id}" for i in out["issues"])
        out = xero_mapping.save(
            w.db,
            ctx,
            w.a.id,
            {**PAYROLL_MAP, "security_owed": "63400"},
            {
                "wages_account": "21280",
                "leave_account": "55300",
                "contacts": {w.b.id: {"contact_id": "c-new", "name": "GG Ltd"}},
            },
            config_db=w.db,
        )
        assert out["saved"] is True and [i["level"] for i in out["issues"]] == [
            "warning"
        ]
        stored = xero_mapping.connection(w.db, w.a.id).config["xero_mapping"]
        assert stored["payroll"]["security_owed"] == {
            "code": "63400",
            "name": "Security",
        }
        assert stored["account_names"]["21280"] == "Intercompany Wages"
        assert xero_mapping.contacts_of(w.db, w.a.id) == {
            w.b.id: {"contact_id": "c-new", "name": "GG Ltd"}
        }
        with pytest.raises(Forbidden):
            xero_mapping.save(
                w.db, w.ctx(w.manager), w.a.id, PAYROLL_MAP, None, config_db=w.db
            )


# ===========================================================================
# The status refresh
# ===========================================================================


class TestStatusRefresh:
    def _fake(self, monkeypatch, statuses):
        from app.connectors import tool_executor

        calls = []

        def fake(connector_name, action, params, db, config_db, venue_id=None, **kw):
            calls.append((action, params, venue_id))
            key = "ManualJournals" if action == "get_manual_journal" else "Invoices"
            status = statuses.get(
                params.get("manual_journal_id") or params.get("invoice_id")
            )
            if status == "404":
                return ToolResult(success=False, payload=None, error="HTTP 404")
            return ToolResult(
                success=True, payload={key: [{"Status": status}]}, error=None
            )

        monkeypatch.setattr(tool_executor, "execute_connector_tool", fake)
        return calls

    def test_statuses_move_and_a_gone_document_frees_the_slot(self, w, monkeypatch):
        ctx = w.ctx()
        j = xero_pushes.record_push(
            w.db,
            ctx,
            kind="journal",
            venue_id=w.a.id,
            week_start=MONDAY,
            idempotency_key="j",
            xero_id="mj-1",
            xero_status="DRAFT",
        )
        b = xero_pushes.record_push(
            w.db,
            ctx,
            kind="ico_bill",
            venue_id=w.a.id,
            week_start=MONDAY,
            counterparty_venue_id=w.b.id,
            idempotency_key="b",
            xero_id="inv-1",
            xero_status="DRAFT",
        )
        e = xero_pushes.record_push(
            w.db,
            ctx,
            kind="entertainer_bill",
            venue_id=w.b.id,
            entertainer_performance_id="p1",
            idempotency_key="e",
            xero_id="inv-2",
            xero_status="DRAFT",
        )
        calls = self._fake(
            monkeypatch, {"mj-1": "POSTED", "inv-1": "DELETED", "inv-2": "404"}
        )
        out = xero_status.refresh(w.db, ctx, config_db=w.db)
        assert (out["checked"], out["updated"], out["voided"], out["errors"]) == (
            3,
            3,
            2,
            [],
        )
        assert {(a, v) for a, _, v in calls} == {
            ("get_manual_journal", w.a.id),
            ("get_invoice", w.a.id),
            ("get_invoice", w.b.id),
        }
        w.db.expire_all()
        assert j.xero_status == "POSTED" and j.voided_at is None
        assert (
            b.xero_status == "DELETED"
            and b.voided_at is not None
            and b.voided_by is None
        )
        assert e.xero_status == "DELETED" and e.voided_at is not None
        assert (
            xero_pushes.live_push(
                w.db,
                w.org.id,
                venue_id=w.a.id,
                week_start=MONDAY,
                kind="ico_bill",
                counterparty_venue_id=w.b.id,
            )
            is None
        )

    def test_one_failure_is_recorded_and_the_rest_continue(self, w, monkeypatch):
        from app.connectors import tool_executor

        ctx = w.ctx()
        xero_pushes.record_push(
            w.db,
            ctx,
            kind="journal",
            venue_id=w.a.id,
            week_start=MONDAY,
            idempotency_key="j",
            xero_id="mj-1",
            xero_status="DRAFT",
        )
        xero_pushes.record_push(
            w.db,
            ctx,
            kind="journal",
            venue_id=w.b.id,
            week_start=MONDAY,
            idempotency_key="j2",
            xero_id="mj-2",
            xero_status="DRAFT",
        )

        def fake(connector_name, action, params, db, config_db, venue_id=None, **kw):
            if params.get("manual_journal_id") == "mj-1":
                return ToolResult(success=False, payload=None, error="Xero is down")
            return ToolResult(
                success=True,
                payload={"ManualJournals": [{"Status": "POSTED"}]},
                error=None,
            )

        monkeypatch.setattr(tool_executor, "execute_connector_tool", fake)
        out = xero_status.refresh(w.db, ctx, config_db=w.db)
        assert (
            out["checked"] == 2
            and out["updated"] == 1
            and "Xero is down" in out["errors"][0]["error"]
        )

    def test_the_slot_is_due_wednesday_three_am_local(self):
        nz = dt.timezone(dt.timedelta(hours=13))
        assert (
            xero_status._slot_due(dt.datetime(2026, 10, 13, 23, 0, tzinfo=nz)) is None
        )  # Tuesday
        assert (
            xero_status._slot_due(dt.datetime(2026, 10, 14, 2, 59, tzinfo=nz)) is None
        )
        assert (
            xero_status._slot_due(dt.datetime(2026, 10, 14, 3, 0, tzinfo=nz))
            == "2026-10-12"
        )
        assert (
            xero_status._slot_due(dt.datetime(2026, 10, 18, 20, 0, tzinfo=nz))
            == "2026-10-12"
        )

    def test_run_due_claims_once_per_org_week(self, w, monkeypatch):
        ran = []
        monkeypatch.setattr(
            xero_status,
            "refresh",
            lambda db, ctx, config_db, venue_id=None: (
                ran.append(ctx.org_id)
                or {"checked": 0, "updated": 0, "voided": 0, "errors": []}
            ),
        )
        now = dt.datetime(2026, 10, 13, 15, 0, tzinfo=UTC)  # Wed 04:00 NZDT
        out = xero_status.run_due(w.db, now, config_db=w.db)
        assert w.org.id in out and ran.count(w.org.id) == 1
        assert w.org.id not in xero_status.run_due(w.db, now, config_db=w.db)
        assert ran.count(w.org.id) == 1
        last = xero_status.last_run(w.db, w.ctx())
        assert (
            last["slot"] == "2026-10-12"
            and last["finished_at"]
            and last["error"] is None
        )


# ===========================================================================
# The built-ins
# ===========================================================================


def _as(db, user):
    return caller_scope.use(caller_scope.for_user(db, user))


class TestBuiltIns:
    def test_the_draft_for_a_payroll_writer(self, w):
        built = journal_svc.build(w.db, w.ctx(), w.a.id, MONDAY)
        with _as(w.db, w.payroll_admin):
            out = tools._get_journal_draft(
                {
                    "venue": "La Zeppa",
                    "week_ending": "2026-10-18",
                    "basis_sha256": built["journal"]["basis_sha256"],
                },
                w.db,
                None,
            )
        assert out["success"] is True
        d = out["data"]
        assert (
            d["organization_id"] == w.org.id
            and d["venue_id"] == w.a.id
            and d["week_start"] == "2026-10-12"
        )
        assert d["draft"]["id"] == built["journal"]["id"] and d["journal"][
            "Narration"
        ].endswith("WE 18.10.2026")
        assert d["live_push"] is None and d["blockers"] == []
        with _as(w.db, w.payroll_admin):
            ico = tools._get_journal_draft(
                {"venue_id": w.a.id, "week_ending": "2026-10-18", "kind": "ico"},
                w.db,
                None,
            )["data"]
        assert {d["kind"] for d in ico["documents"]} == {
            "ico_invoice",
            "ico_bill",
        } and ico["wages_account"] == "21280"

    def test_the_refusals(self, w):
        journal_svc.build(w.db, w.ctx(), w.a.id, MONDAY)
        params = {"venue_id": w.a.id, "week_ending": "2026-10-18"}
        assert "who is asking" in tools._get_journal_draft(params, w.db, None)["error"]
        with _as(w.db, w.manager):
            assert (
                "payroll:write" in tools._get_journal_draft(params, w.db, None)["error"]
            )
        with _as(w.db, w.other_user):
            assert "venue" in tools._get_journal_draft(params, w.db, None)["error"]
        with _as(w.db, w.owner):
            assert (
                "changed since you saw them"
                in tools._get_journal_draft(
                    {**params, "basis_sha256": "0" * 64}, w.db, None
                )["error"]
            )
            assert (
                "rebuilt"
                in tools._get_journal_draft({**params, "draft_id": "nope"}, w.db, None)[
                    "error"
                ]
            )
            w.ace.gross_cents += 100
            w.db.flush()
            assert (
                "rebuild the draft"
                in tools._get_journal_draft(params, w.db, None)["error"]
            )

    def test_an_unattended_run_and_shadow_mode_are_refused(self, w):
        thread = Thread(
            id=str(uuid.uuid4()),
            user_id=w.owner.id,
            domain="finance",
            status="in_progress",
            intent="finance.automated_task",
            raw_prompt="x",
        )
        w.db.add(thread)
        w.db.flush()
        out = tools._get_journal_draft(
            {"venue_id": w.a.id, "week_ending": "2026-10-18"}, w.db, thread.id
        )
        assert out["error"] == "unattended run"
        modes.set_mode(w.db, w.org.id, "payroll", "shadow")
        with _as(w.db, w.owner):
            out = tools._get_journal_draft(
                {"venue_id": w.a.id, "week_ending": "2026-10-18"}, w.db, None
            )
        assert "shadow" in out["error"]
        with _as(w.db, w.owner):
            assert (
                "shadow"
                in tools._record_push(
                    {
                        "kind": "journal",
                        "venue_id": w.a.id,
                        "idempotency_key": "k",
                        "xero_id": "x",
                    },
                    w.db,
                    None,
                )["error"]
            )

    def test_record_push_takes_the_person_from_the_scope_and_is_idempotent(self, w):
        doc = {
            "kind": "journal",
            "venue_id": w.a.id,
            "week_ending": "2026-10-18",
            "xero_id": "mj-9",
            "xero_status": "DRAFT",
            "idempotency_key": "norm:payroll-journal:k",
        }
        with _as(w.db, w.payroll_admin):
            first = tools._record_push({**doc, "pushed_by": w.owner.id}, w.db, None)
            again = tools._record_push({"documents": [doc]}, w.db, None)
        assert (
            first["success"]
            and first["data"]["push_id"] == again["data"]["pushes"][0]["id"]
        )
        row = (
            w.db.query(PayXeroPush)
            .filter(PayXeroPush.id == first["data"]["push_id"])
            .one()
        )
        assert row.pushed_by == w.payroll_admin.id and row.week_start == MONDAY
        with _as(w.db, w.payroll_admin):
            seen = tools._get_journal_draft({"push_id": row.id}, w.db, None)["data"]
            voided = tools._record_push(
                {"op": "void", "push_id": row.id, "xero_status": "DELETED"}, w.db, None
            )
        assert seen["push"]["xero_id"] == "mj-9"
        assert voided["data"]["voided"] is True and voided["data"]["push"]["voided_at"]

    def test_the_rows_are_complete_for_the_sync_script(self):
        actions = {r["action"]: r for r in tools.TOOL_ROWS}
        assert set(actions) == {"get_journal_draft", "record_push"}
        for row in actions.values():
            assert row["engine_only"] is True and row["requires"] == ["payroll:write"]
            assert set(row["optional_fields"]) <= set(row["field_schema"])
        assert actions["record_push"]["effect"] == "write"


# ===========================================================================
# Routes — including the push end to end with Xero faked at the HTTP boundary
# ===========================================================================


def _install_specs(db):
    """The config rows the sandbox resolves: the xero spec as the sync script
    builds it, the norm spec's engine-only settings row, and the norm_payroll
    spec (F3's rows and F4's)."""
    import scripts.sync_xero_connector as sync

    db.add(
        ConnectionSpec(
            connector_name="xero",
            display_name="Xero",
            category="accounting",
            execution_mode="template",
            auth_type="oauth2",
            auth_config={"token_field": "access_token"},
            base_url_template=sync.BASE_URL,
            tools=sync._tool_rows(),
            endpoints=list(sync.ENDPOINTS),
            credential_fields=sync.CREDENTIAL_FIELDS,
            oauth_config=dict(sync.OAUTH_CONFIG),
            enabled=True,
        )
    )
    db.add(
        ConnectionSpec(
            connector_name="norm",
            display_name="Norm",
            execution_mode="internal",
            auth_type="none",
            enabled=True,
            tools=[sync.NORM_SETTINGS_TOOL],
        )
    )
    from app.agents import payroll_tools

    db.add(
        ConnectionSpec(
            connector_name="norm_payroll",
            display_name="Loaded Payroll",
            execution_mode="internal",
            auth_type="none",
            enabled=True,
            tools=[*payroll_tools.TOOL_ROWS, *tools.TOOL_ROWS],
        )
    )
    db.flush()


class FakeXero:
    """Xero at the HTTP boundary: what each endpoint row answers."""

    def __init__(self):
        self.sent = []
        self.journal_status = "DRAFT"
        self.counter = 0

    def __call__(
        self,
        spec,
        operation,
        fields,
        credentials,
        db,
        thread_id=None,
        venue_id=None,
        **kw,
    ):
        action = operation["action"]
        self.sent.append((action, dict(fields), credentials.get("xero_tenant_id")))
        if action == "create_manual_journal":
            self.counter += 1
            body = {
                "ManualJournals": [
                    {
                        "ManualJournalID": f"mj-{self.counter}",
                        "Status": "DRAFT",
                        "Narration": fields["journal"]["Narration"],
                    }
                ]
            }
        elif action == "get_manual_journal":
            body = {
                "ManualJournals": [
                    {
                        "ManualJournalID": fields["manual_journal_id"],
                        "Status": self.journal_status,
                    }
                ]
            }
        elif action == "update_manual_journal":
            body = {
                "ManualJournals": [
                    {
                        "ManualJournalID": fields["manual_journal_id"],
                        "Status": fields["journal"]["Status"],
                    }
                ]
            }
        elif action == "list_tax_rates":
            body = {
                "TaxRates": [
                    {
                        "TaxType": "OUTPUT2",
                        "EffectiveRate": 15.0,
                        "Status": "ACTIVE",
                        "CanApplyToRevenue": True,
                        "CanApplyToExpenses": False,
                    },
                    {
                        "TaxType": "INPUT2",
                        "EffectiveRate": 15.0,
                        "Status": "ACTIVE",
                        "CanApplyToRevenue": False,
                        "CanApplyToExpenses": True,
                    },
                    {
                        "TaxType": "NONE",
                        "EffectiveRate": 0.0,
                        "Status": "ACTIVE",
                        "CanApplyToRevenue": True,
                        "CanApplyToExpenses": True,
                    },
                ]
            }
        elif action == "list_accounts":
            body = {
                "Accounts": [
                    {
                        "Code": c,
                        "Name": n,
                        "Type": t,
                        "Class": k,
                        "Status": "ACTIVE",
                        "TaxType": "NONE",
                    }
                    for c, n, t, k in CHART
                ]
            }
        elif action == "create_invoice":
            self.counter += 1
            inv = fields["invoice"]
            body = {
                "Invoices": [
                    {
                        "InvoiceID": f"inv-{self.counter}",
                        "InvoiceNumber": f"INV-{self.counter}",
                        "Status": "DRAFT",
                        "SubTotal": 1,
                        "TotalTax": 0.15,
                        "Total": 1.15,
                        "Type": inv["Type"],
                    }
                ]
            }
        elif action == "get_invoice":
            body = {
                "Invoices": [{"InvoiceID": fields["invoice_id"], "Status": "DRAFT"}]
            }
        elif action == "update_invoice":
            body = {
                "Invoices": [
                    {
                        "InvoiceID": fields["invoice_id"],
                        "Status": fields["invoice"]["Status"],
                    }
                ]
            }
        else:
            raise AssertionError(f"unexpected Xero call {action}")
        return ConnectorResult(
            success=True, reference=None, response_payload=body
        ), None


class _Cfg:
    """The sandbox opens its own config session to resolve specs
    (``_ConfigSessionLocal`` — the DB shared with production); point it at
    the test session so it sees the rows installed above and nothing live."""

    def __init__(self, db):
        self.db = db

    def query(self, *a):
        return self.db.query(*a)

    def expunge(self, obj):
        pass

    def close(self):
        pass


@pytest.fixture
def live(w, client, monkeypatch):
    from app.connectors import spec_executor
    from app.db import engine

    _install_specs(w.db)
    monkeypatch.setattr(engine, "_ConfigSessionLocal", lambda: _Cfg(w.db))
    xero = FakeXero()
    monkeypatch.setattr(spec_executor, "execute_spec", xero)
    w.xero = xero
    w.client = client
    return w


class TestRoutes:
    def test_owner_200_manager_403_team_member_403(self, w, client):
        url = "/api/payroll/reconciliation?week_ending=2026-10-18"
        assert client.get(url, headers=auth_headers(w.owner)).status_code == 200
        assert client.get(url, headers=auth_headers(w.payroll_admin)).status_code == 200
        assert client.get(url, headers=auth_headers(w.manager)).status_code == 403
        assert client.get(url, headers=auth_headers(w.team_member)).status_code == 403

    def test_the_formula_and_the_intercompany_page(self, w, client):
        r = client.get(
            f"/api/payroll/reconciliation/{w.a.id}?week_ending=2026-10-18",
            headers=auth_headers(w.owner),
        )
        assert r.status_code == 200 and r.json()["difference_cents"] == 0
        r = client.get(
            f"/api/payroll/reconciliation/{w.other_venue.id}?week_ending=2026-10-18",
            headers=auth_headers(w.owner),
        )
        assert r.status_code == 404
        r = client.get(
            f"/api/payroll/reconciliation/intercompany?week_ending=2026-10-18&venue_id={w.a.id}",
            headers=auth_headers(w.owner),
        )
        assert r.status_code == 200 and r.json()["venues"][0]["net_cents"] == 16000
        assert (
            client.get(
                "/api/payroll/reconciliation/intercompany?week_ending=18-10-2026",
                headers=auth_headers(w.owner),
            ).status_code
            == 400
        )

    def test_off_is_a_404_and_shadow_refuses_a_push(self, w, client):
        modes.set_mode(w.db, w.org.id, "payroll", "off")
        assert (
            client.get(
                "/api/payroll/reconciliation?week_ending=2026-10-18",
                headers=auth_headers(w.owner),
            ).status_code
            == 404
        )
        modes.set_mode(w.db, w.org.id, "payroll", "shadow")
        assert (
            client.get(
                "/api/payroll/reconciliation?week_ending=2026-10-18",
                headers=auth_headers(w.owner),
            ).status_code
            == 200
        )
        r = client.post(
            "/api/payroll/xero/journal/preview",
            json={"venue_id": w.a.id, "week_ending": "2026-10-18"},
            headers=auth_headers(w.owner),
        )
        assert r.status_code == 409 and r.json()["detail"]["code"] == "not_live"
        r = client.post(
            "/api/payroll/xero/intercompany/confirm",
            json={"venue_id": w.a.id, "week_ending": "2026-10-18"},
            headers=auth_headers(w.owner),
        )
        assert r.status_code == 409

    def test_build_and_read_the_drafts(self, w, client):
        r = client.post(
            "/api/payroll/xero/journal/build",
            json={"venue_id": w.a.id, "week_ending": "2026-10-18"},
            headers=auth_headers(w.manager),
        )
        assert r.status_code == 403
        r = client.post(
            "/api/payroll/xero/journal/build",
            json={"venue_id": w.a.id, "week_ending": "2026-10-18"},
            headers=auth_headers(w.owner),
        )
        assert r.status_code == 200 and r.json()["journal"]["debit_cents"] == 2116840
        r = client.get(
            f"/api/payroll/xero/journal?venue_id={w.a.id}&week_ending=2026-10-18",
            headers=auth_headers(w.payroll_admin),
        )
        assert (
            r.status_code == 200
            and len(r.json()["ico"]) == 2
            and r.json()["blockers"] == []
        )
        w.ace.gross_cents += 150
        w.db.flush()
        r = client.post(
            "/api/payroll/xero/journal/build",
            json={"venue_id": w.a.id, "week_ending": "2026-10-18"},
            headers=auth_headers(w.owner),
        )
        assert (
            r.status_code == 400 and "does not balance" in r.json()["detail"]["message"]
        )

    def test_the_mapping_routes(self, w, client):
        r = client.get(
            f"/api/payroll/xero/mapping?venue_id={w.a.id}",
            headers=auth_headers(w.owner),
        )
        assert r.status_code == 200 and r.json()["payroll"]["kitchen"] == "55170"
        bad = {
            "venue_id": w.a.id,
            "payroll": {**PAYROLL_MAP, "intercompany_wages": "52100"},
        }
        r = client.put(
            "/api/payroll/xero/mapping", json=bad, headers=auth_headers(w.owner)
        )
        assert (
            r.status_code == 400
            and r.json()["detail"]["issues"][0]["key"] == "intercompany_wages"
        )
        good = {
            "venue_id": w.a.id,
            "payroll": PAYROLL_MAP,
            "intercompany": {
                "wages_account": "21280",
                "leave_account": "55300",
                "contacts": {w.b.id: {"contact_id": "c-gg", "name": "GG"}},
            },
        }
        r = client.put(
            "/api/payroll/xero/mapping", json=good, headers=auth_headers(w.owner)
        )
        assert r.status_code == 200 and r.json()["saved"] is True
        assert (
            client.put(
                "/api/payroll/xero/mapping", json=good, headers=auth_headers(w.manager)
            ).status_code
            == 403
        )

    def test_push_inspect_void_and_double_approve_is_one_journal(self, live):
        w, client, xero = live, live.client, live.xero
        built = client.post(
            "/api/payroll/xero/journal/build",
            json={"venue_id": w.a.id, "week_ending": "2026-10-18"},
            headers=auth_headers(w.owner),
        ).json()
        body = {
            "venue_id": w.a.id,
            "week_ending": "2026-10-18",
            "draft_id": built["journal"]["id"],
            "basis_sha256": built["journal"]["basis_sha256"],
        }

        # A manager never gets a card.
        assert (
            client.post(
                "/api/payroll/xero/journal/preview",
                json=body,
                headers=auth_headers(w.manager),
            ).status_code
            == 403
        )

        shown = client.post(
            "/api/payroll/xero/journal/preview",
            json=body,
            headers=auth_headers(w.owner),
        )
        assert shown.status_code == 200, shown.text
        card = shown.json()["card"]
        fields = {c["field"]: c["after"] for c in card["changes"]}
        assert (
            card["title"] == "Post the payroll journal to Xero"
            and card["target"] == "La Zeppa — WE 18.10.2026"
        )
        assert fields["Net Wages → 814 Wages Payable - Payroll"] == "CR $14,922.20"
        assert fields["Other team → 55160 Other Team"] == "DR $700.00"
        assert fields["Total debits"] == fields["Total credits"] == "$21,168.40"
        assert fields["Residual"] == "$0.00 (balanced)"
        assert xero.sent == []  # nothing reached Xero for the card

        done = client.post(
            "/api/payroll/xero/journal/confirm",
            json={**body, "fingerprint": shown.json()["fingerprint"]},
            headers=auth_headers(w.owner),
        )
        assert done.status_code == 200, done.text
        data = done.json()["data"]
        assert (
            data["pushed"] is True
            and data["xero_id"] == "mj-1"
            and data["ledger_error"] is None
        )
        assert data["idempotency_key"].startswith(
            f"norm:payroll-journal:{w.org.id}:{w.a.id}:2026-10-12:"
        )
        sent = [s for s in xero.sent if s[0] == "create_manual_journal"]
        assert (
            len(sent) == 1
            and sent[0][1]["idempotency_key"] == data["idempotency_key"]
            and sent[0][2] == "tenant-a"
        )
        assert (
            sent[0][1]["journal"]["LineAmountTypes"] == "NoTax"
            and sent[0][1]["journal"]["Status"] == "DRAFT"
        )
        rows = client.get(
            f"/api/payroll/xero/pushes?venue_id={w.a.id}&week_ending=2026-10-18",
            headers=auth_headers(w.owner),
        ).json()["pushes"]
        assert (
            [r["kind"] for r in rows] == ["journal"]
            and rows[0]["xero_id"] == "mj-1"
            and rows[0]["pushed_by"] == w.owner.id
        )
        recon = client.get(
            f"/api/payroll/reconciliation/{w.a.id}?week_ending=2026-10-18",
            headers=auth_headers(w.owner),
        ).json()
        assert recon["status"] == "pushed"

        # A second approval of the same card: the live row stands, Xero is not asked again.
        again = client.post(
            "/api/payroll/xero/journal/confirm",
            json={**body, "fingerprint": shown.json()["fingerprint"]},
            headers=auth_headers(w.owner),
        )
        assert again.status_code == 400 and "already in Xero" in again.json()["detail"]
        assert len([s for s in xero.sent if s[0] == "create_manual_journal"]) == 1
        assert (
            client.post(
                "/api/payroll/xero/journal/preview",
                json=body,
                headers=auth_headers(w.owner),
            ).status_code
            == 400
        )

        # Replace: the old one is deleted (it is DRAFT), a new one created, one live row.
        shown2 = client.post(
            "/api/payroll/xero/journal/preview",
            json={**body, "replace": True},
            headers=auth_headers(w.owner),
        )
        assert shown2.status_code == 200 and any(
            "voided first" in x for x in shown2.json()["card"]["warnings"]
        )
        done2 = client.post(
            "/api/payroll/xero/journal/confirm",
            json={**body, "replace": True, "fingerprint": shown2.json()["fingerprint"]},
            headers=auth_headers(w.owner),
        )
        assert (
            done2.status_code == 200
            and done2.json()["data"]["xero_id"] == "mj-2"
            and done2.json()["data"]["replaced_push_id"] == rows[0]["id"]
        )
        assert [s for s in xero.sent if s[0] == "update_manual_journal"][0][1][
            "journal"
        ] == {"Status": "DELETED"}
        live_rows = client.get(
            f"/api/payroll/xero/pushes?venue_id={w.a.id}&week_ending=2026-10-18",
            headers=auth_headers(w.owner),
        ).json()["pushes"]
        assert len(live_rows) == 1 and live_rows[0]["xero_id"] == "mj-2"
        assert (
            len(
                client.get(
                    f"/api/payroll/xero/pushes?venue_id={w.a.id}&include_voided=true",
                    headers=auth_headers(w.owner),
                ).json()["pushes"]
            )
            == 2
        )

        # Void: the status is read first; a draft is deleted; the ledger row is voided.
        push_id = live_rows[0]["id"]
        shown3 = client.post(
            "/api/payroll/xero/void/preview",
            json={"push_id": push_id, "venue_id": w.a.id},
            headers=auth_headers(w.owner),
        )
        assert shown3.status_code == 200, shown3.text
        fields = {c["field"]: c["after"] for c in shown3.json()["card"]["changes"]}
        assert fields["Status now"] == "DRAFT" and "DELETED" in fields["What happens"]
        done3 = client.post(
            "/api/payroll/xero/void/confirm",
            json={"push_id": push_id, "fingerprint": shown3.json()["fingerprint"]},
            headers=auth_headers(w.owner),
        )
        assert (
            done3.status_code == 200
            and done3.json()["data"]["xero_status_after"] == "DELETED"
        )
        assert (
            client.get(
                f"/api/payroll/xero/pushes?venue_id={w.a.id}&week_ending=2026-10-18",
                headers=auth_headers(w.owner),
            ).json()["pushes"]
            == []
        )
        assert (
            client.post(
                "/api/payroll/xero/void/preview",
                json={"push_id": push_id},
                headers=auth_headers(w.owner),
            ).status_code
            == 400
        )
        # The page may name the venue; it has to be the push's own.
        wrong = client.post(
            "/api/payroll/xero/void/preview",
            json={"push_id": push_id, "venue_id": w.b.id},
            headers=auth_headers(w.owner),
        )
        assert (
            wrong.status_code == 400
            and "another venue" in wrong.json()["detail"]["message"]
        )

    def test_the_ico_pair_and_a_missing_contact(self, live):
        w, client, xero = live, live.client, live.xero
        client.post(
            "/api/payroll/xero/journal/build",
            json={"venue_id": w.a.id, "week_ending": "2026-10-18"},
            headers=auth_headers(w.owner),
        )
        body = {"venue_id": w.a.id, "week_ending": "2026-10-18"}
        shown = client.post(
            "/api/payroll/xero/intercompany/preview",
            json=body,
            headers=auth_headers(w.owner),
        )
        assert shown.status_code == 200, shown.text
        card = shown.json()["card"]
        heads = [c["field"] for c in card["changes"] if not c["field"].startswith("↳")]
        assert heads == ["Invoice to Glass Goose", "Bill from Glass Goose"]
        fields = {c["field"]: c["after"] for c in card["changes"]}
        # The leave line takes its account's default rate (NONE in the fake chart), so GST is on the wages.
        assert (
            "wages $400.00 · leave $48.00 · GST $60.00 · total $508.00"
            in fields["Invoice to Glass Goose"]
        )
        assert (
            "wages $240.00 · leave $28.80 · GST $40.32 · total $309.12"
            in fields["Bill from Glass Goose"]
        )
        assert fields["↳ Dana — 10.00h"].endswith("(OUTPUT2)") and fields[
            "↳ Eli — Gratuities — 0.00h"
        ].endswith("(INPUT2)")
        # Leave to an EXPENSE account: on the invoice no tax type (the account's
        # default), on the bill the expense type.
        leave = [
            c["after"]
            for c in card["changes"]
            if c["field"] == "↳ Leave & Entitlements (12%)"
        ]
        assert leave[0].endswith("(account default NONE)") and leave[1].endswith(
            "(INPUT2)"
        )
        done = client.post(
            "/api/payroll/xero/intercompany/confirm",
            json={**body, "fingerprint": shown.json()["fingerprint"]},
            headers=auth_headers(w.owner),
        )
        assert done.status_code == 200, done.text
        data = done.json()["data"]
        assert (
            [p["kind"] for p in data["pushed"]] == ["ico_invoice", "ico_bill"]
            and data["refused"] == []
            and data["ledger_error"] is None
        )
        sent = {
            s[1]["invoice"]["Type"]: s[1] for s in xero.sent if s[0] == "create_invoice"
        }
        assert (
            sent["ACCREC"]["idempotency_key"]
            == f"norm:ico:{w.org.id}:{w.a.id}:2026-10-12:invoice:{w.b.id}"
        )
        assert (
            sent["ACCPAY"]["invoice"]["InvoiceNumber"]
            == "ICO Wages WE 18.10.2026 — La Zeppa"
        )
        assert sent["ACCREC"]["invoice"]["DueDate"] == "2026-11-01" and sent["ACCREC"][
            "invoice"
        ]["Contact"] == {"ContactID": "c-gg"}
        rows = client.get(
            f"/api/payroll/xero/pushes?venue_id={w.a.id}&week_ending=2026-10-18",
            headers=auth_headers(w.owner),
        ).json()["pushes"]
        assert {r["kind"] for r in rows} == {"ico_invoice", "ico_bill"} and all(
            r["counterparty_venue_id"] == w.b.id for r in rows
        )
        # Pushed groups are skipped next time; nothing left means a refusal.
        again = client.post(
            "/api/payroll/xero/intercompany/preview",
            json=body,
            headers=auth_headers(w.owner),
        )
        assert again.status_code == 400 and "already pushed" in again.json()["detail"]

        # Glass Goose's own pair from its own screen — with no contact for La Zeppa it is refused by name.
        row = xero_mapping.connection(w.db, w.b.id)
        config = copy.deepcopy(row.config)
        config["xero_mapping"]["intercompany"]["contacts"] = {}
        row.config = config
        flag_modified(
            row, "config"
        )  # the build route commits; the row must reload cleared
        # Glass Goose reconciles too once its ACE figures are in: Fay 3,000 +
        # Dana 400 worked there, net intercompany −160 → gross 3,240.
        w.db.add(
            PayAceInput(
                organization_id=w.org.id,
                week_id=w.week_b.id,
                gross_cents=324000,
                net_cents=250000,
            )
        )
        w.db.flush()
        built = client.post(
            "/api/payroll/xero/journal/build",
            json={"venue_id": w.b.id, "week_ending": "2026-10-18"},
            headers=auth_headers(w.owner),
        )
        assert built.status_code == 200, built.text
        assert {d["kind"] for d in built.json()["ico"]} == {"ico_invoice", "ico_bill"}
        r = client.post(
            "/api/payroll/xero/intercompany/preview",
            json={"venue_id": w.b.id, "week_ending": "2026-10-18"},
            headers=auth_headers(w.owner),
        )
        assert (
            r.status_code == 400
            and "no Xero contact mapped for La Zeppa" in r.json()["detail"]
        )

    def test_the_status_routes(self, w, client, monkeypatch):
        monkeypatch.setattr(
            xero_status,
            "refresh",
            lambda db, ctx, config_db, venue_id=None: {
                "checked": 0,
                "updated": 0,
                "voided": 0,
                "errors": [],
            },
        )
        assert client.get(
            "/api/payroll/xero/status", headers=auth_headers(w.owner)
        ).json() == {"last_run": None}
        assert (
            client.post(
                "/api/payroll/xero/status/refresh",
                json={},
                headers=auth_headers(w.manager),
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/payroll/xero/status/refresh",
                json={},
                headers=auth_headers(w.owner),
            ).json()["checked"]
            == 0
        )


class TestConfigRows:
    def test_the_connector_rows_pass_the_validator(self, w):
        """The rows the sync script would write, checked in memory by the
        validator production runs (dry-run only: the config DB is shared)."""
        from app.services.config_validator import validate_config

        _install_specs(w.db)
        summary = validate_config(config_db=w.db)
        mine = (
            "post_wage_journal",
            "post_intercompany_documents",
            "void_payroll_document",
            "create_manual_journal",
            "get_manual_journal",
            "update_manual_journal",
            "attach_to_invoice",
            "get_journal_draft",
            "record_push",
        )
        errors = [
            i
            for i in summary.get("issues", [])
            if getattr(i, "level", None) == "error"
            or (isinstance(i, dict) and i.get("level") == "error")
        ]
        named = [i for i in errors if any(m in str(i) for m in mine)]
        assert named == [], named

    def test_requires_on_the_existing_five(self):
        import scripts.sync_xero_connector as sync

        requires = {r["action"]: r.get("requires") for r in sync.TOOLS}
        assert (
            requires["get_bills"]
            == requires["get_contacts"]
            == requires["get_accounts"]
            == ["finance:read"]
        )
        assert requires["get_profit_and_loss"] == ["payroll:read"]
        assert requires["create_bill"] == ["orders:write"]
        assert (
            "attach_to_invoice"
            in sync.CREATE_BILL_TOOL["consolidator_config"]["allowed_write_actions"]
        )
        attach = next(e for e in sync.ENDPOINTS if e["action"] == "attach_to_invoice")
        assert attach["body_file_id"] == "file_id" and attach["read_only"] is False
        void = next(t for t in sync.TOOLS if t["action"] == "void_payroll_document")
        assert (
            sync.INLINE_LABELS["xero.void_payroll_document"]["approval"]["allow_auto"]
            is False
        )
        assert void["requires"] == ["payroll:write"]
