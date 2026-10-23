"""The tick jobs (DESIGN-CONTRACT §6): each claimed once per entity-week
through ``fin_job_runs`` at its venue-local time, a failure recorded on the
run, the hygiene email to the payroll contact (suppressed without one), the
weekly parity email, and the retention floor.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.db.entertainers_models import EntPerformance, EntPerformer
from app.db.finance_models import FinJobRun
from app.db.models import EmailLog
from app.db.payroll_models import PayLeavePayout, PaySnapshot, PayWeek
from app.services.payroll import entities as entities_svc
from app.services.payroll import jobs as jobs_svc
from app.services.payroll import retention as retention_svc
from app.services.payroll import snapshot as snapshot_svc
from tests.payroll_world import (
    MONDAY,
    NZ,
    FakeLoaded,
    install,
    make_world,
    payrun_result,
    shift,
)

ROWS = [shift("s1", "a", "Ana Lee", code="LZ001", hours=6.0, cost_cents=18000)]


@pytest.fixture
def w(db_session):
    w = make_world(db_session)
    for venue in (w.laz, w.dsc):
        e = entities_svc.get_or_create(w.db, w.ctx(w.owner), venue.id)
        e.payroll_on = True
    w.db.flush()
    return w


@pytest.fixture
def mail(monkeypatch):
    sent = []

    def fake_send(template, to, context, db, organization_id=None, **_sender):
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
    return sent


def _runs(db, job):
    return {
        (r.scope_id, r.slot): r
        for r in db.query(FinJobRun).filter(FinJobRun.job == job).all()
    }


class TestSnapshotJob:
    def test_claims_at_day_start_plus_thirty_minutes_per_entity(self, w, monkeypatch):
        fake = install(
            monkeypatch,
            FakeLoaded(get_payrun=lambda v, p: payrun_result(v, MONDAY, ROWS)),
        )
        # Monday 19 Oct 07:10 NZ: La Zeppa (07:00) is not due, DSC (06:00) is.
        jobs_svc.run_due(w.db, dt.datetime(2026, 10, 19, 7, 10, tzinfo=NZ))
        runs = _runs(w.db, jobs_svc.JOB_SNAPSHOT)
        assert set(runs) == {(w.dsc.id, "2026-10-12")}
        assert [c[0] for c in fake.calls] == ["Dunedin Social Club"]
        jobs_svc.run_due(w.db, dt.datetime(2026, 10, 19, 7, 31, tzinfo=NZ))
        runs = _runs(w.db, jobs_svc.JOB_SNAPSHOT)
        assert set(runs) == {(w.dsc.id, "2026-10-12"), (w.laz.id, "2026-10-12")}
        assert all(r.finished_at is not None and r.error is None for r in runs.values())
        # A second tick finds both slots taken.
        jobs_svc.run_due(w.db, dt.datetime(2026, 10, 19, 7, 40, tzinfo=NZ))
        assert len(fake.calls) == 2
        snap = (
            w.db.query(PaySnapshot)
            .join(PayWeek, PayWeek.id == PaySnapshot.week_id)
            .filter(PayWeek.venue_id == w.laz.id)
            .one()
        )
        assert snap.source == "monday" and snap.taken_by is None

    def test_a_failure_is_recorded_on_the_run_and_the_tick_goes_on(
        self, w, monkeypatch
    ):
        def answer(venue, params):
            if venue.id == w.laz.id:
                return {"error": "token expired"}
            return payrun_result(venue, MONDAY, ROWS)

        install(monkeypatch, FakeLoaded(get_payrun=answer))
        report = jobs_svc.run_due(w.db, dt.datetime(2026, 10, 19, 7, 31, tzinfo=NZ))
        runs = _runs(w.db, jobs_svc.JOB_SNAPSHOT)
        assert "token expired" in runs[(w.laz.id, "2026-10-12")].error
        assert runs[(w.dsc.id, "2026-10-12")].error is None
        assert any(
            "error" in v for k, v in report.items() if k.startswith("payroll.snapshot")
        )

    def test_hygiene_email_at_twenty_hundred_with_the_snapshot_report(
        self, w, monkeypatch, mail
    ):
        install(
            monkeypatch,
            FakeLoaded(get_payrun=lambda v, p: payrun_result(v, MONDAY, ROWS)),
        )
        jobs_svc.run_due(w.db, dt.datetime(2026, 10, 19, 19, 59, tzinfo=NZ))
        assert not _runs(w.db, jobs_svc.JOB_HYGIENE)
        jobs_svc.run_due(w.db, dt.datetime(2026, 10, 19, 20, 1, tzinfo=NZ))
        assert set(_runs(w.db, jobs_svc.JOB_HYGIENE)) == {
            (w.laz.id, "2026-10-12"),
            (w.dsc.id, "2026-10-12"),
        }
        hygiene = [m for m in mail if m[0] == "payroll_hygiene"]
        assert len(hygiene) == 2
        context = hygiene[0][2]
        assert (context["taken"], context["of"]) == (2, 2) and hygiene[0][1] == [
            "pay@cookbrothers.test"
        ]

    def test_no_contact_means_suppressed_not_sent(self, w, monkeypatch, mail):
        install(
            monkeypatch,
            FakeLoaded(get_payrun=lambda v, p: payrun_result(v, MONDAY, ROWS)),
        )
        w.org.payroll_contact_email = None
        w.db.flush()
        jobs_svc.run_due(w.db, dt.datetime(2026, 10, 19, 20, 1, tzinfo=NZ))
        assert mail == []
        logs = (
            w.db.query(EmailLog)
            .filter(EmailLog.template_name == "payroll_hygiene")
            .all()
        )
        assert len(logs) == 2 and all(log.status == "suppressed" for log in logs)
        # The entity's own override wins over the organisation's missing address.
        entities_svc.get_or_create(
            w.db, w.ctx(w.owner), w.laz.id
        ).payroll_contact_email = "laz@pay.test"
        w.db.flush()
        for r in list(_runs(w.db, jobs_svc.JOB_HYGIENE).values()):
            w.db.delete(r)
        w.db.flush()
        jobs_svc.run_due(w.db, dt.datetime(2026, 10, 19, 20, 1, tzinfo=NZ))
        assert [m[1] for m in mail] == [["laz@pay.test"]]

    def test_close_snapshot_tuesday_ten_unless_start_close_took_one(
        self, w, monkeypatch, mail
    ):
        fake = install(
            monkeypatch,
            FakeLoaded(get_payrun=lambda v, p: payrun_result(v, MONDAY, ROWS)),
        )
        jobs_svc.run_due(
            w.db, dt.datetime(2026, 10, 19, 20, 1, tzinfo=NZ)
        )  # Monday: snapshots and hygiene done
        snapshot_svc.take(
            w.db, w.ctx(w.owner), w.laz.id, MONDAY, source="close"
        )  # Start close pressed
        fake.calls.clear()
        jobs_svc.run_due(w.db, dt.datetime(2026, 10, 20, 9, 59, tzinfo=NZ))
        assert not _runs(w.db, jobs_svc.JOB_CLOSE)
        report = jobs_svc.run_due(w.db, dt.datetime(2026, 10, 20, 10, 1, tzinfo=NZ))
        runs = _runs(w.db, jobs_svc.JOB_CLOSE)
        assert set(runs) == {(w.laz.id, "2026-10-12"), (w.dsc.id, "2026-10-12")}
        assert [c[0] for c in fake.calls if c[1] == "get_payrun"] == [
            "Dunedin Social Club"
        ]
        assert report[f"payroll.close_snapshot:{w.laz.id}:2026-10-12"] == {
            "skipped": "close snapshot already taken"
        }

    def test_gratuity_reminder_on_the_first(self, w, mail, monkeypatch):
        install(monkeypatch, FakeLoaded())
        jobs_svc.run_due(w.db, dt.datetime(2026, 11, 1, 8, 55, tzinfo=NZ))
        assert not _runs(w.db, jobs_svc.JOB_GRATUITY)
        jobs_svc.run_due(w.db, dt.datetime(2026, 11, 1, 9, 5, tzinfo=NZ))
        assert set(_runs(w.db, jobs_svc.JOB_GRATUITY)) == {
            (w.laz.id, "2026-10"),
            (w.dsc.id, "2026-10"),
        }
        reminders = [m for m in mail if m[0] == "payroll_gratuity_reminder"]
        assert len(reminders) == 2 and reminders[0][2]["period_label"] == "October 2026"

    def test_parity_weekly_waits_for_the_importer_then_emails(
        self, w, monkeypatch, mail
    ):
        from app.services.finance_core import jobs as core_jobs

        install(
            monkeypatch,
            FakeLoaded(get_payrun=lambda v, p: payrun_result(v, MONDAY, ROWS)),
        )
        snapshot_svc.take(w.db, w.ctx(w.owner), w.laz.id, MONDAY, source="close")
        now = dt.datetime(2026, 10, 21, 4, 5, tzinfo=NZ)
        core_jobs.claim(
            w.db,
            org_id=w.org.id,
            job="importer.nightly",
            scope_id="-",
            slot="2026-10-21",
            now=now - dt.timedelta(minutes=5),
        )
        jobs_svc.run_due(w.db, now)
        assert not _runs(w.db, jobs_svc.JOB_PARITY)  # the importer is still running
        run = w.db.query(FinJobRun).filter(FinJobRun.job == "importer.nightly").one()
        core_jobs.finish(w.db, run)
        jobs_svc.run_due(w.db, now)
        assert set(_runs(w.db, jobs_svc.JOB_PARITY)) == {("-", "2026-10-12")}
        parity_mail = [m for m in mail if m[0] == "payroll_parity"]
        assert len(parity_mail) == 1 and parity_mail[0][1] == ["pay@cookbrothers.test"]
        assert parity_mail[0][2]["runs"] and parity_mail[0][2]["unexplained"] >= 0

    def test_off_organisations_run_nothing(self, db_session, monkeypatch):
        w = make_world(db_session, payroll="off")
        install(monkeypatch, FakeLoaded())
        jobs_svc.run_due(w.db, dt.datetime(2026, 10, 19, 20, 1, tzinfo=NZ))
        assert (
            w.db.query(FinJobRun).filter(FinJobRun.organization_id == w.org.id).count()
            == 0
        )


class TestRetention:
    def test_the_floor_is_seven_years_plus_one(self, w):
        now = dt.datetime(2026, 10, 20, tzinfo=dt.timezone.utc)
        ctx = w.ctx(w.owner)
        from app.services.payroll import weeks as weeks_svc

        kept = weeks_svc.get_or_create(
            w.db, ctx, w.laz.id, dt.date(2019, 4, 1)
        )  # 7.5 years: under the floor
        gone = weeks_svc.get_or_create(
            w.db, ctx, w.laz.id, dt.date(2018, 9, 3)
        )  # 8.1 years
        w.db.add(
            PayLeavePayout(
                organization_id=w.org.id,
                venue_id=w.laz.id,
                week_start=dt.date(2018, 9, 3),
                loaded_request_id="old",
                workflow_kind="generic",
            )
        )
        w.db.add(
            PayLeavePayout(
                organization_id=w.org.id,
                venue_id=w.laz.id,
                week_start=dt.date(2019, 4, 1),
                loaded_request_id="kept",
                workflow_kind="generic",
            )
        )
        w.db.flush()
        out = retention_svc.purge(w.db, w.org.id, now=now)
        # 8 years back from 20 Oct 2026, two leap days inside: 20 Oct 2018.
        assert (
            out["weeks_purged"] == 1
            and out["payouts_purged"] == 1
            and out["floor"] == "2018-10-20"
        )
        assert w.db.query(PayWeek).filter(PayWeek.id == kept.id).count() == 1
        assert w.db.query(PayWeek).filter(PayWeek.id == gone.id).count() == 0
        assert [p.loaded_request_id for p in w.db.query(PayLeavePayout).all()] == [
            "kept"
        ]

    def test_invited_performers_go_after_ninety_days_unless_they_performed(self, w):
        now = dt.datetime(2026, 10, 20, tzinfo=dt.timezone.utc)
        old = now - dt.timedelta(days=91)
        stale = EntPerformer(
            organization_id=w.org.id,
            name="Never Replied",
            onboarding_status="invited",
            created_at=old,
        )
        booked = EntPerformer(
            organization_id=w.org.id,
            name="Played Once",
            onboarding_status="invited",
            created_at=old,
        )
        fresh = EntPerformer(
            organization_id=w.org.id, name="Just Invited", onboarding_status="invited"
        )
        w.db.add_all([stale, booked, fresh])
        w.db.flush()
        w.db.add(
            EntPerformance(
                organization_id=w.org.id,
                venue_id=w.laz.id,
                performer_id=booked.id,
                performance_date=dt.date(2026, 9, 1),
                start_time=dt.time(20, 0),
                end_time=dt.time(23, 0),
            )
        )
        w.db.flush()
        out = retention_svc.purge(w.db, w.org.id, now=now)
        assert out["invited_deleted"] == 1
        assert {p.name for p in w.db.query(EntPerformer).all()} == {
            "Played Once",
            "Just Invited",
        }
