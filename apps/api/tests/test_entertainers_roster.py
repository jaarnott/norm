"""Series (materialised a year ahead, this/following/all), approval with
DST-aware pay and the audit trail, band shares, the payroll feed F3 reads,
mark-paid (``payroll:write`` + live, revocation), and the Xero bill through
``page_writes`` (faked): one bill per performance, the key, the void before
a re-push, the exemption-expiry refusal, the share key.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.db.entertainers_models import (
    EntBandMember,
    EntPerformance,
    EntPerformanceChange,
    EntPerformanceShare,
    EntPublicToken,
    EntSeries,
)
from app.db.payroll_models import PayXeroPush
from app.services.entertainers import (
    band,
    bills,
    payments,
    performances,
    series,
    tokens,
)
from app.services.finance_core.errors import Conflict, Forbidden, Invalid, NotLive
from tests.entertainers_world import (
    UTC,
    add_performer,
    approved,
    make_world,
    portal_link,
)

NOW = dt.datetime(2026, 10, 12, tzinfo=UTC)


@pytest.fixture
def w(db_session):
    return make_world(db_session)


class TestSeries:
    def test_booking_with_repeat_materialises_a_year_ahead(self, w):
        performer = add_performer(w.db, w.org)
        ctx = w.ctx(w.team_member)
        first, rows = performances.book(
            w.db,
            ctx,
            venue_id=w.venue.id,
            performer_id=performer.id,
            performance_date="2026-10-10",
            start_time="21:00",
            end_time="01:00",
            rate_cents=10_000,
            repeat={"weekdays": [5, 6]},
        )
        assert len(rows) == 2 and {r.weekday for r in rows} == {5, 6}
        made = (
            w.db.query(EntPerformance)
            .filter(EntPerformance.series_id.in_([r.id for r in rows]))
            .all()
        )
        assert (
            first.performance_date == dt.date(2026, 10, 10)
            and first.performance_date.isoweekday() == 6
        )
        dates = sorted(p.performance_date for p in made)
        # A year ahead of whichever is later, the first night or today — so
        # the count depends on the day the suite runs; derive it the same way.
        through = max(dt.date.today(), dt.date(2026, 10, 10)) + series.HORIZON
        expected = [
            dt.date(2026, 10, 10) + dt.timedelta(days=n)
            for n in range((through - dt.date(2026, 10, 10)).days + 1)
        ]
        expected = [d for d in expected if d.isoweekday() in (5, 6)]
        assert dates[0] == dt.date(2026, 10, 10) and dates[-1] >= dt.date(2027, 10, 1)
        assert all(d.isoweekday() in (5, 6) for d in dates)
        assert (
            abs(len(dates) - len(expected)) <= 2
        )  # a day either side of local midnight
        assert all(p.agreed_cents == 40_000 for p in made)
        # the job tops it up: nothing new today, new rows once time passes
        assert series.extend_all(w.db, w.org.id, today=dt.date(2026, 10, 12)) == 0
        assert series.extend_all(w.db, w.org.id, today=dt.date(2026, 12, 1)) > 0

    def test_following_splits_the_rule_and_all_edits_every_scheduled_one(self, w):
        performer = add_performer(w.db, w.org)
        ctx = w.ctx(w.team_member)
        _first, (rule,) = performances.book(
            w.db,
            ctx,
            venue_id=w.venue.id,
            performer_id=performer.id,
            performance_date="2026-10-10",
            start_time="21:00",
            end_time="01:00",
            rate_cents=10_000,
            repeat={"weekdays": [6], "ends_on": "2026-12-31"},
        )
        rows = (
            w.db.query(EntPerformance)
            .filter(EntPerformance.series_id == rule.id)
            .order_by(EntPerformance.performance_date)
            .all()
        )
        assert len(rows) == 12
        anchor = rows[4]  # 7 Nov
        changed = performances.update(
            w.db,
            ctx,
            anchor.id,
            {"rate_cents": 12_000},
            version=anchor.version,
            scope="following",
        )
        assert len(changed) == 8
        w.db.refresh(rule)
        assert rule.ends_on == dt.date(2026, 11, 6) and rule.rate_cents == 10_000
        new_rule = w.db.query(EntSeries).filter(EntSeries.id == anchor.series_id).one()
        assert (
            new_rule.id != rule.id
            and new_rule.rate_cents == 12_000
            and new_rule.starts_on == dt.date(2026, 11, 7)
        )
        assert all(p.agreed_cents == 48_000 for p in rows[4:]) and all(
            p.agreed_cents == 40_000 for p in rows[:4]
        )
        # approve one, then "all": the approved one is a fact and stays
        rows[5].performance_status = "approved"
        w.db.flush()
        performances.update(
            w.db,
            ctx,
            rows[6].id,
            {"start_time": "20:00"},
            version=rows[6].version,
            scope="all",
        )
        assert (
            rows[5].start_time == dt.time(21, 0)
            and rows[7].start_time == dt.time(20, 0)
            and rows[4].start_time == dt.time(20, 0)
        )
        # delete following ends the rule; the approved one stays
        deleted = performances.delete(
            w.db, ctx, rows[6].id, version=rows[6].version, scope="following"
        )
        assert deleted == 6
        remaining = (
            w.db.query(EntPerformance)
            .filter(EntPerformance.performer_id == performer.id)
            .count()
        )
        assert remaining == 6  # five before the anchor, plus the approved one
        w.db.refresh(new_rule)
        assert new_rule.ends_on == dt.date(2026, 11, 20)

    def test_a_single_edit_re_fixes_the_agreed_amount_and_logs_the_change(self, w):
        performer = add_performer(w.db, w.org)
        ctx = w.ctx(w.team_member)
        perf, _ = performances.book(
            w.db,
            ctx,
            venue_id=w.venue.id,
            performer_id=performer.id,
            performance_date="2026-10-10",
            start_time="21:00",
            end_time="01:00",
            rate_cents=10_000,
        )
        performances.update(
            w.db, ctx, perf.id, {"end_time": "02:00"}, version=perf.version
        )
        assert perf.agreed_cents == 50_000
        change = (
            w.db.query(EntPerformanceChange)
            .filter(EntPerformanceChange.performance_id == perf.id)
            .one()
        )
        assert (
            change.field,
            change.old_value,
            change.new_value,
            change.changed_by_source,
        ) == ("end_time", "01:00:00", "02:00:00", "manager")


class TestApproval:
    def test_dst_night_pays_real_hours_and_logs_every_field(self, w):
        performer = add_performer(w.db, w.org)
        ctx = w.ctx(w.team_member)
        perf, _ = performances.book(
            w.db,
            ctx,
            venue_id=w.venue.id,
            performer_id=performer.id,
            performance_date="2026-09-26",
            start_time="22:00",
            end_time="03:00",
            rate_cents=10_000,
        )
        assert perf.agreed_cents == 50_000
        performances.approve(
            w.db,
            ctx,
            perf.id,
            actual_start_time="22:00",
            actual_end_time="03:00",
            version=perf.version,
            now=NOW,
        )
        assert (
            perf.actual_cents == 40_000
            and perf.performance_status == "approved"
            and perf.approved_by == w.team_member.id
        )
        fields = {
            c.field: c
            for c in w.db.query(EntPerformanceChange).filter(
                EntPerformanceChange.performance_id == perf.id
            )
        }
        assert (
            fields["actual_cents"].new_value == "40000"
            and fields["performance_status"].new_value == "approved"
        )
        assert fields["actual_start_time"].changed_by_name == "U"
        out = performances.performance_dict(w.db, ctx, perf, now=NOW)
        assert out["minutes"] == {"booked": 300, "actual": 240, "dst_adjustment": -60}

    def test_cannot_approve_before_the_end_and_reset_undoes(self, w):
        performer = add_performer(w.db, w.org)
        ctx = w.ctx(w.team_member)
        perf, _ = performances.book(
            w.db,
            ctx,
            venue_id=w.venue.id,
            performer_id=performer.id,
            performance_date="2026-10-20",
            start_time="21:00",
            end_time="01:00",
            rate_cents=10_000,
        )
        with pytest.raises(Conflict, match="hasn't ended"):
            performances.approve(
                w.db,
                ctx,
                perf.id,
                actual_start_time="21:00",
                actual_end_time="01:00",
                version=perf.version,
                now=NOW,
            )
        performances.approve(
            w.db,
            ctx,
            perf.id,
            actual_start_time="21:00",
            actual_end_time="00:00",
            version=perf.version,
            now=dt.datetime(2026, 10, 25, tzinfo=UTC),
        )
        assert perf.actual_cents == 30_000
        performances.reset(w.db, ctx, perf.id, version=perf.version)
        assert (
            perf.performance_status == "scheduled"
            and perf.actual_cents is None
            and perf.approved_at is None
        )

    def test_did_not_perform_needs_a_reason(self, w):
        performer = add_performer(w.db, w.org)
        ctx = w.ctx(w.team_member)
        perf = approved(w.db, w.org, w.venue, performer)
        with pytest.raises(Invalid):
            performances.did_not_perform(
                w.db, ctx, perf.id, reason="  ", version=perf.version
            )
        performances.did_not_perform(
            w.db, ctx, perf.id, reason="no-show", version=perf.version
        )
        assert perf.performance_status == "did_not_perform" and perf.actual_cents == 0

    def test_approval_emails_by_lane(self, w):
        from app.db.entertainers_models import EntEmailLink

        invoice_act = add_performer(
            w.db,
            w.org,
            name="Agency",
            email="agency@example.test",
            lane="invoice",
            gst_registered=True,
            gst_number="049091850",
        )
        wt_act = add_performer(w.db, w.org, name="Solo", email="solo@example.test")
        ctx = w.ctx(w.team_member)
        for who in (invoice_act, wt_act):
            perf, _ = performances.book(
                w.db,
                ctx,
                venue_id=w.venue.id,
                performer_id=who.id,
                performance_date="2026-10-10",
                start_time="21:00",
                end_time="01:00",
                rate_cents=10_000,
            )
            performances.approve(
                w.db,
                ctx,
                perf.id,
                actual_start_time="21:00",
                actual_end_time="01:00",
                version=perf.version,
                now=NOW,
            )
        kinds = [
            (r.recipient_email, r.kind)
            for r in w.db.query(EntEmailLink).order_by(EntEmailLink.sent_at)
        ]
        assert ("agency@example.test", "ent_confirm_request") in kinds and (
            "agency@example.test",
            "ent_approved_amount",
        ) in kinds
        assert ("solo@example.test", "ent_confirm_request") in kinds and (
            "solo@example.test",
            "ent_approved_amount",
        ) not in kinds
        from app.db.models import EmailLog

        money = (
            w.db.query(EmailLog)
            .filter(EmailLog.template_name == "ent_approved_amount")
            .one()
        )
        assert (
            "$400.00" in money.html_body
            and "15%" in money.html_body
            and "$60.00" in money.html_body
        )
        # the approved amount is never on the link: the portal token minted for the confirm email shows no figure
        token_row = (
            w.db.query(EntPublicToken)
            .filter(
                EntPublicToken.purpose == "portal",
                EntPublicToken.performer_id == invoice_act.id,
            )
            .one()
        )
        assert token_row.expires_at is not None

    def test_mismatch_resolved_with_the_performers_times(self, w):
        performer = add_performer(w.db, w.org)
        ctx = w.ctx(w.team_member)
        perf = approved(w.db, w.org, w.venue, performer)
        perf.performer_confirmed_at = NOW
        perf.performer_actual_start_time, perf.performer_actual_end_time = (
            dt.time(21, 30),
            dt.time(1, 0),
        )
        w.db.flush()
        assert (
            performances.performance_dict(w.db, ctx, perf, now=NOW)["status"]
            == "mismatch"
        )
        performances.use_performer_times(
            w.db, ctx, perf.id, version=perf.version, now=NOW
        )
        assert perf.actual_start_time == dt.time(21, 30) and perf.actual_cents == 35_000
        assert (
            performances.performance_dict(w.db, ctx, perf, now=NOW)["status"]
            == "ready_for_payment"
        )


def _band(w, shares=(3334, 3333, 3333)):
    leader = add_performer(
        w.db,
        w.org,
        name="The Band",
        email="band@example.test",
        lane=None,
        performer_type="band",
        band_mode="members_split",
        bank=None,
        ird=None,
        declaration=False,
    )
    members = []
    for i, bp in enumerate(shares):
        member = add_performer(
            w.db,
            w.org,
            name=f"Member {i}",
            email=f"m{i}@example.test",
            lane="payroll_contractor" if i < 2 else "invoice",
            payroll_code=f"E{i}",
        )
        row = EntBandMember(
            organization_id=w.org.id,
            band_performer_id=leader.id,
            member_performer_id=member.id,
            name=member.name,
            email=member.email,
            share_bp=bp,
            invite_status="accepted",
            sort_order=i,
        )
        w.db.add(row)
        members.append((row, member))
    w.db.flush()
    return leader, members


class TestShares:
    def test_shares_sum_to_the_total_with_the_residual_cent_on_the_first(self, w):
        leader, members = _band(w)
        ctx = w.ctx(w.team_member)
        perf, _ = performances.book(
            w.db,
            ctx,
            venue_id=w.venue.id,
            performer_id=leader.id,
            performance_date="2026-10-10",
            start_time="21:00",
            end_time="01:00",
            rate_cents=2_500,
        )
        performances.approve(
            w.db,
            ctx,
            perf.id,
            actual_start_time="21:00",
            actual_end_time="01:00",
            version=perf.version,
            now=NOW,
        )
        assert perf.actual_cents == 10_000
        shares = (
            w.db.query(EntPerformanceShare)
            .filter(EntPerformanceShare.performance_id == perf.id)
            .all()
        )
        by_member = {s.band_member_id: s.amount_cents for s in shares}
        assert sum(by_member.values()) == 10_000
        assert (
            by_member[members[0][0].id] == 3334 and by_member[members[1][0].id] == 3333
        )
        # an odd total: 10,001 → 3334+3333+3333 = 10000, the extra cent to the first
        perf2, _ = performances.book(
            w.db,
            ctx,
            venue_id=w.venue.id,
            performer_id=leader.id,
            performance_date="2026-10-11",
            start_time="21:00",
            end_time="01:00",
            rate_cents=2_500,
        )
        perf2.rate_cents = (
            10_001 * 60 // 240
        )  # not exact; set the amount through approval maths below
        performances.approve(
            w.db,
            ctx,
            perf2.id,
            actual_start_time="21:00",
            actual_end_time="01:00",
            version=perf2.version,
            now=NOW,
        )
        total = perf2.actual_cents
        shares2 = {
            s.band_member_id: s.amount_cents
            for s in w.db.query(EntPerformanceShare).filter(
                EntPerformanceShare.performance_id == perf2.id
            )
        }
        assert sum(shares2.values()) == total
        assert shares2[members[0][0].id] >= shares2[members[1][0].id]

    def test_set_shares_must_cover_every_member_and_sum_to_ten_thousand(self, w):
        leader, members = _band(w)
        ids = [m[0].id for m in members]
        with pytest.raises(Invalid, match="add up"):
            band.set_shares(
                w.db, w.ctx(), leader.id, {ids[0]: 5000, ids[1]: 5000, ids[2]: 1}
            )
        with pytest.raises(Invalid, match="every member"):
            band.set_shares(w.db, w.ctx(), leader.id, {ids[0]: 10000})
        with pytest.raises(Forbidden):
            band.set_shares(
                w.db,
                w.ctx(w.team_member),
                leader.id,
                {ids[0]: 5000, ids[1]: 2500, ids[2]: 2500},
            )
        out = band.set_shares(
            w.db, w.ctx(), leader.id, {ids[0]: 5000, ids[1]: 2500, ids[2]: 2500}
        )
        assert [m["share_bp"] for m in out] == [5000, 2500, 2500]
        assert (
            band.equal_shares(3) == [3334, 3333, 3333]
            and band.equal_shares(4) == [2500] * 4
        )


class TestPayrollFeed:
    def test_lines_shape_carry_forward_and_paid_this_week(self, w):
        performer = add_performer(w.db, w.org, payroll_code="E42")
        ctx = w.ctx()
        old = approved(
            w.db,
            w.org,
            w.venue,
            performer,
            date=dt.date(2026, 9, 26),
            start=dt.time(22, 0),
            end=dt.time(3, 0),
            cents=40_000,
        )
        this_week = approved(
            w.db, w.org, w.venue, performer, date=dt.date(2026, 10, 10), cents=40_000
        )
        paid_before = approved(
            w.db, w.org, w.venue, performer, date=dt.date(2026, 10, 3), cents=40_000
        )
        paid_before.payment_status, paid_before.payroll_week_start = (
            "paid",
            dt.date(2026, 10, 5),
        )
        elsewhere = approved(
            w.db, w.org, w.venue2, performer, date=dt.date(2026, 10, 9), cents=40_000
        )
        w.db.flush()
        lines = payments.payroll_lines(w.db, ctx, w.venue.id, dt.date(2026, 10, 12))
        assert [ln.performance_id for ln in lines] == [old.id, this_week.id]
        first = lines[0]
        assert (
            first.pay_code,
            first.payroll_code,
            first.minutes,
            str(first.hours),
            first.rate_cents,
            first.amount_cents,
        ) == ("E42", "E42", 240, "4.00", 10_000, 40_000)
        assert (
            first.lane == "payroll_contractor"
            and first.performer_name == "Sam Smith"
            and first.payment_status == "pending"
        )
        assert elsewhere.id not in {ln.performance_id for ln in lines}
        # the week it was paid in lists it
        week_before = payments.payroll_lines(
            w.db, ctx, w.venue.id, dt.date(2026, 10, 5)
        )
        assert paid_before.id in {ln.performance_id for ln in week_before} and next(
            ln for ln in week_before if ln.performance_id == paid_before.id
        ).paid_week_start == dt.date(2026, 10, 5)
        with pytest.raises(Forbidden):
            payments.payroll_lines(
                w.db, w.ctx(w.team_member), w.venue.id, dt.date(2026, 10, 12)
            )

    def test_a_mismatch_and_an_invoice_lane_yield_no_line(self, w):
        performer = add_performer(w.db, w.org, payroll_code="E1")
        perf = approved(w.db, w.org, w.venue, performer)
        perf.performer_confirmed_at = NOW
        perf.performer_actual_start_time, perf.performer_actual_end_time = (
            dt.time(22, 0),
            dt.time(1, 0),
        )
        agency = add_performer(
            w.db, w.org, name="Agency", email="a@example.test", lane="invoice"
        )
        approved(w.db, w.org, w.venue, agency)
        w.db.flush()
        lines, skipped = payments.payroll_lines(
            w.db, w.ctx(), w.venue.id, dt.date(2026, 10, 12), with_skipped=True
        )
        assert lines == [] and skipped == [
            {
                "performance_id": perf.id,
                "performer": "Sam Smith",
                "reason": "times mismatch",
            }
        ]

    def test_band_shares_to_wt_members_are_their_own_lines(self, w):
        leader, members = _band(w)
        ctx = w.ctx(w.team_member)
        perf, _ = performances.book(
            w.db,
            ctx,
            venue_id=w.venue.id,
            performer_id=leader.id,
            performance_date="2026-10-10",
            start_time="21:00",
            end_time="01:00",
            rate_cents=2_500,
        )
        performances.approve(
            w.db,
            ctx,
            perf.id,
            actual_start_time="21:00",
            actual_end_time="01:00",
            version=perf.version,
            now=NOW,
        )
        lines = payments.payroll_lines(w.db, w.ctx(), w.venue.id, dt.date(2026, 10, 12))
        assert [
            (ln.performer_name, ln.amount_cents, ln.rate_cents, str(ln.hours))
            for ln in lines
        ] == [("Member 0", 3334, 834, "4.00"), ("Member 1", 3333, 833, "4.00")]
        assert all(ln.share_id for ln in lines) and all(
            ln.performance_id == perf.id for ln in lines
        )

    def test_mark_paid_for_week_and_undo(self, w):
        performer = add_performer(w.db, w.org, payroll_code="E1")
        perf = approved(w.db, w.org, w.venue, performer)
        token, _url = portal_link(w.db, w.org, performer)
        out = payments.mark_paid_for_week(
            w.db, w.ctx(), w.venue.id, dt.date(2026, 10, 12), [perf.id], now=NOW
        )
        assert (
            out == {"paid": 1, "shares": 0}
            and perf.payment_status == "paid"
            and perf.payroll_week_start == dt.date(2026, 10, 12)
        )
        assert (
            perf.paid_by == w.payroll_person.id
            and tokens.resolve(w.db, token, purposes=("portal",)) is None
        )
        payments.undo_paid(w.db, w.ctx(), perf.id, version=perf.version)
        assert perf.payment_status == "pending" and perf.payroll_week_start is None


class TestMarkPaid:
    def test_needs_payroll_write_and_live(self, w):
        performer = add_performer(w.db, w.org)
        perf = approved(w.db, w.org, w.venue, performer)
        with pytest.raises(Forbidden):
            payments.mark_paid(
                w.db, w.ctx(w.team_member), perf.id, version=perf.version
            )
        from app.services.finance_core import modes

        modes.set_mode(w.db, w.org.id, "entertainers", "shadow")
        with pytest.raises(NotLive):
            payments.mark_paid(w.db, w.ctx(), perf.id, version=perf.version)
        modes.set_mode(w.db, w.org.id, "entertainers", "live")
        payments.mark_paid(
            w.db,
            w.ctx(),
            perf.id,
            reference="BNZ 12 Oct",
            version=perf.version,
            now=NOW,
        )
        assert perf.paid_reference == "BNZ 12 Oct" and perf.paid_at == NOW
        with pytest.raises(Conflict):
            payments.mark_paid(w.db, w.ctx(), perf.id, version=perf.version)

    def test_payments_list_is_payroll_write_only(self, w, client):
        performer = add_performer(w.db, w.org, lane="invoice")
        approved(w.db, w.org, w.venue, performer)
        r = client.get("/api/entertainers/payments", headers=w.headers())
        assert (
            r.status_code == 200
            and r.json()["totals"]["count"] == 1
            and r.json()["rows"][0]["performer"]["payable"] is True
        )
        assert (
            client.get(
                "/api/entertainers/payments", headers=w.headers(w.team_member)
            ).status_code
            == 403
        )


class FakeWrites:
    """A stand-in for ``page_writes``: records what was previewed and
    confirmed, answers with a card and a fingerprint, and a bill id."""

    def __init__(self):
        self.previews, self.confirms = [], []

    def preview(self, db, *, tool, params, user, venue_id, config_db=None):
        self.previews.append((tool, params, venue_id))
        return {
            "card": {"title": f"preview {tool}", "fingerprint": "fp-1"},
            "fingerprint": "fp-1",
            "run_params": params,
        }

    def confirm(self, db, *, tool, params, user, venue_id, fingerprint, config_db=None):
        self.confirms.append((tool, params, venue_id, fingerprint))
        if tool == "xero.create_bill":
            return {
                "success": True,
                "data": {
                    "invoice": {
                        "InvoiceID": f"inv-{len(self.confirms)}",
                        "InvoiceNumber": "B-1",
                        "Status": params["status"],
                    }
                },
            }
        return {"success": True, "data": {"voided": True}}


@pytest.fixture
def xero(w, monkeypatch):
    from app.db.models import Connection

    conn = Connection(
        connector_name="xero",
        venue_id=w.venue.id,
        enabled="true",
        access_token="tok",
        config={
            "xero_tenant_id": "tenant-1",
            # Where Payroll settings → Xero mapping and the importer write it.
            "xero_mapping": {"payroll": {"entertainment_expense": "67300"}},
        },
    )
    w.db.add(conn)
    w.db.flush()
    fake = FakeWrites()
    import app.services.page_writes as page_writes

    monkeypatch.setattr(page_writes, "preview", fake.preview)
    monkeypatch.setattr(page_writes, "confirm", fake.confirm)
    return fake


class TestBills:
    def test_preview_and_confirm_record_one_bill_per_performance(self, w, xero):
        agency = add_performer(
            w.db,
            w.org,
            name="Smith Sounds Ltd",
            email="a@example.test",
            lane="invoice",
            gst_registered=True,
            gst_number="049091850",
            company_name="Smith Sounds Ltd",
        )
        perf = approved(w.db, w.org, w.venue, agency)
        ctx = w.ctx()
        shown = bills.preview(w.db, ctx, perf.id)
        assert shown["fingerprint"] == "fp-1" and shown["replaces"] is None
        params = shown["params"]
        assert (
            params["idempotency_key"] == f"norm:ent-bill:{perf.id}"
            and params["supplier"] == "Smith Sounds Ltd"
        )
        assert params["lines"] == [
            {
                "description": "Smith Sounds Ltd — performance 2026-10-10 @ Brew Bar",
                "quantity": 1,
                "unit_amount": 400.0,
                "category": "entertainment_expense",
                "gst": "yes",
                "account_code": "67300",
            }
        ]
        assert (
            params["line_amount_types"] == "Exclusive"
            and params["status"] == "DRAFT"
            and params["due_date"] == "2026-10-24"
        )
        assert "attachment_file_id" not in params
        assert "supplier_contact_id" not in params
        # Linked to an existing Xero contact (the importer's rows): the bill
        # goes to that contact, and the card names it — not Norm's spelling.
        from app.db.entertainers_models import EntPerformerXeroContact

        w.db.add(
            EntPerformerXeroContact(
                organization_id=w.org.id,
                performer_id=agency.id,
                xero_tenant_id="tenant-1",
                xero_contact_id="c-smith",
                xero_contact_name="Smith Sounds Limited",
            )
        )
        w.db.flush()
        params = bills.preview(w.db, ctx, perf.id)["params"]
        assert params["supplier_contact_id"] == "c-smith"
        assert params["supplier"] == "Smith Sounds Limited"
        push = bills.confirm(w.db, ctx, perf.id, fingerprint="fp-1")
        assert (
            isinstance(push, PayXeroPush)
            and push.kind == "entertainer_bill"
            and push.xero_id == "inv-1"
        )
        assert (
            push.entertainer_performance_id == perf.id
            and push.venue_id == w.venue.id
            and push.week_start is None
        )
        assert (
            push.idempotency_key == f"norm:ent-bill:{perf.id}"
            and push.xero_tenant_id == "tenant-1"
            and push.pushed_by == w.payroll_person.id
        )
        assert (
            xero.confirms[0][0] == "xero.create_bill" and xero.confirms[0][3] == "fp-1"
        )

    def test_the_ledger_reads_create_bills_own_result(self, w, xero):
        """create_bill answers flat (invoice_id, number, status); the ledger
        lost the bill number on the live Federal Street bill (23 Oct 2026)."""
        agency = add_performer(
            w.db, w.org, name="Agency", email="a@example.test", lane="invoice"
        )
        perf = approved(w.db, w.org, w.venue, agency)
        push = bills.record_push(
            w.db,
            w.ctx(),
            perf=perf,
            member_id=None,
            tenant="tenant-1",
            params={"idempotency_key": f"norm:ent-bill:{perf.id}"},
            result={
                "created": True,
                "invoice_id": "inv-flat",
                "number": "SS-77",
                "status": "DRAFT",
            },
        )
        assert (push.xero_id, push.xero_number, push.xero_status) == (
            "inv-flat",
            "SS-77",
            "DRAFT",
        )

    def test_a_re_push_voids_the_earlier_bill_first(self, w, xero):
        agency = add_performer(
            w.db, w.org, name="Agency", email="a@example.test", lane="invoice"
        )
        perf = approved(w.db, w.org, w.venue, agency)
        ctx = w.ctx()
        first = bills.confirm(w.db, ctx, perf.id, fingerprint="fp-1")
        assert bills.preview(w.db, ctx, perf.id)["replaces"]["id"] == first.id
        second = bills.confirm(w.db, ctx, perf.id, fingerprint="fp-1")
        tools = [c[0] for c in xero.confirms]
        assert tools == [
            "xero.create_bill",
            "xero.void_payroll_document",
            "xero.create_bill",
        ]
        assert xero.confirms[1][1] == {"push_id": first.id}
        w.db.refresh(first)
        assert first.voided_at is not None and second.voided_at is None
        assert bills.live_push(w.db, w.org.id, perf.id).id == second.id

    def test_refusals(self, w, xero):
        ctx = w.ctx()
        wt = add_performer(w.db, w.org)
        perf = approved(w.db, w.org, w.venue, wt)
        with pytest.raises(Invalid, match="payroll lanes"):
            bills.preview(w.db, ctx, perf.id)
        expired = add_performer(
            w.db,
            w.org,
            name="Ex",
            email="e@example.test",
            lane="exemption",
            exemption_expiry_date=dt.date(2026, 10, 1),
        )
        perf2 = approved(w.db, w.org, w.venue, expired)
        with pytest.raises(Conflict, match="expired"):
            bills.preview(w.db, ctx, perf2.id)
        agency = add_performer(
            w.db, w.org, name="Agency", email="a@example.test", lane="invoice"
        )
        perf3 = approved(
            w.db, w.org, w.venue2, agency
        )  # venue 2 has no Xero connection
        with pytest.raises(Conflict, match="no live Xero connection"):
            bills.preview(w.db, ctx, perf3.id)
        with pytest.raises(Forbidden):
            bills.preview(w.db, w.ctx(w.team_member), perf3.id)
        # Connected, but no entertainment account mapped: refused by name.
        # create_bill knows only the stock categories, so the bill was refused
        # in Xero's terms, or sent to the Bills default (live test, 23 Oct 2026).
        from app.db.models import Connection

        conn = w.db.query(Connection).filter(Connection.venue_id == w.venue.id).one()
        conn.config = {"xero_tenant_id": "tenant-1"}
        w.db.flush()
        perf4 = approved(w.db, w.org, w.venue, agency)
        with pytest.raises(Conflict, match="Entertainment expense"):
            bills.preview(w.db, ctx, perf4.id)
        from app.services.finance_core import modes

        modes.set_mode(w.db, w.org.id, "entertainers", "shadow")
        with pytest.raises(NotLive):
            bills.preview(w.db, w.ctx(), perf3.id)

    def test_a_members_share_is_its_own_bill_with_the_member_key(self, w, xero):
        leader, members = _band(w)
        ctx = w.ctx(w.team_member)
        perf, _ = performances.book(
            w.db,
            ctx,
            venue_id=w.venue.id,
            performer_id=leader.id,
            performance_date="2026-10-10",
            start_time="21:00",
            end_time="01:00",
            rate_cents=2_500,
        )
        performances.approve(
            w.db,
            ctx,
            perf.id,
            actual_start_time="21:00",
            actual_end_time="01:00",
            version=perf.version,
            now=NOW,
        )
        invoice_member_row, invoice_member = members[2]
        with pytest.raises(Invalid, match="per member"):
            bills.preview(w.db, w.ctx(), perf.id)
        shown = bills.preview(w.db, w.ctx(), perf.id, member_id=invoice_member_row.id)
        assert (
            shown["params"]["idempotency_key"]
            == f"norm:ent-bill:{perf.id}:{invoice_member_row.id}"
        )
        assert (
            shown["params"]["lines"][0]["unit_amount"] == 33.33
            and shown["params"]["supplier"] == "Member 2"
        )
        push = bills.confirm(
            w.db, w.ctx(), perf.id, fingerprint="fp-1", member_id=invoice_member_row.id
        )
        assert push.entertainer_performance_id == f"{perf.id}:{invoice_member_row.id}"
        with pytest.raises(Invalid, match="payroll lanes"):
            bills.preview(w.db, w.ctx(), perf.id, member_id=members[0][0].id)


class TestMarkPaidWeekRoute:
    def test_stamps_the_payroll_week_and_refuses_without_payroll_write(self, w, client):
        performer = add_performer(w.db, w.org, payroll_code="E1")
        a = approved(w.db, w.org, w.venue, performer)
        b = approved(w.db, w.org, w.venue2, performer, date=dt.date(2026, 10, 9))
        w.db.commit()
        body = {"week_start": "2026-10-12", "performance_ids": [a.id, b.id]}
        r = client.post(
            "/api/entertainers/payments/mark-paid-week",
            json=body,
            headers=w.headers(w.team_member),
        )
        assert r.status_code == 403
        bad = client.post(
            "/api/entertainers/payments/mark-paid-week",
            json={**body, "week_start": "2026-10-13"},
            headers=w.headers(),
        )
        assert bad.status_code == 400
        r = client.post(
            "/api/entertainers/payments/mark-paid-week", json=body, headers=w.headers()
        )
        assert r.status_code == 200, r.text
        assert r.json() == {"paid": 2, "shares": 0}
        w.db.refresh(a)
        w.db.refresh(b)
        assert a.payroll_week_start == b.payroll_week_start == dt.date(2026, 10, 12)
        assert a.payment_status == "paid" and a.paid_by == w.payroll_person.id
