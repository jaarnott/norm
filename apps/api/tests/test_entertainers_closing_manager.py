"""The closing Sales Manager (D22): the morning lookup stores Loaded's answer
on the performance and matches a Norm user by email, case-blind; the
approval email carries the deep link; no Norm user → the digest; four
emails, then the digest; the venue digest goes to the venue's managers; the
morning job claims once per org-day. The Loaded rule itself (clock-in beats
roster, the latest end wins, trading-day bucketing) is F2's consolidator,
tested in ``tests/test_consolidator_finance.py``; here the lookup is a fake.
"""

from __future__ import annotations

import datetime as dt
import html

from app.db.entertainers_models import EntEmailLink
from app.db.finance_models import FinJobRun
from app.db.models import EmailLog
from app.services.entertainers import closing_manager, email_links, jobs
from tests.entertainers_world import UTC, add_performance, add_performer, make_world

NOW = dt.datetime(2026, 10, 11, 20, 30, tzinfo=UTC)  # Mon 12 Oct 09:30 NZDT


def fake_lookup(answer):
    calls = []

    def lookup(db, venue, dates, roles):
        calls.append((venue.name, [d.isoformat() for d in dates], list(roles)))
        return answer(venue) if callable(answer) else answer

    lookup.calls = calls
    return lookup


class TestResolve:
    def test_stores_the_answer_and_matches_the_norm_user_case_blind(self, db_session):
        w = make_world(db_session)
        performer = add_performer(w.db, w.org)
        perf = add_performance(w.db, w.org, w.venue, performer)  # 10 Oct, ended
        lookup = fake_lookup(
            {
                "2026-10-10": {
                    "name": "Casey Closer",
                    "staff_member_id": "sm-1",
                    "email": "CLOSER@cookbros.test",
                    "source": "both",
                }
            }
        )
        report = closing_manager.resolve_for_org(
            w.db, w.org.id, now=NOW, lookup_fn=lookup
        )
        assert report["resolved"] == 1 and report["failed"] == []
        assert lookup.calls == [("Brew Bar", ["2026-10-10"], ["Sales Manager"])]
        w.db.refresh(perf)
        assert (
            perf.closing_manager_name == "Casey Closer"
            and perf.closing_manager_staff_id == "sm-1"
        )
        assert (
            perf.closing_manager_email == "closer@cookbros.test"
            and perf.closing_manager_user_id == w.manager.id
        )
        assert (
            perf.closing_manager_source == "both"
            and perf.closing_manager_resolved_at == NOW
        )

    def test_nobody_found_is_source_none_and_a_failed_venue_is_reported(
        self, db_session
    ):
        w = make_world(db_session)
        performer = add_performer(w.db, w.org)
        perf = add_performance(w.db, w.org, w.venue, performer)
        add_performance(w.db, w.org, w.venue2, performer, date=dt.date(2026, 10, 9))
        lookup = fake_lookup(
            lambda venue: (
                {"error": "token dead"}
                if venue.name == "Garden Bar"
                else {"2026-10-10": None}
            )
        )
        report = closing_manager.resolve_for_org(
            w.db, w.org.id, now=NOW, lookup_fn=lookup
        )
        assert report["none"] == 1 and report["failed"] == [
            {"venue": "Garden Bar", "error": "token dead"}
        ]
        w.db.refresh(perf)
        assert (
            perf.closing_manager_source == "none"
            and perf.closing_manager_resolved_at is not None
        )

    def test_the_roster_column_covers_the_last_seven_days_but_not_the_future(
        self, db_session
    ):
        w = make_world(db_session)
        performer = add_performer(w.db, w.org)
        recent = add_performance(
            w.db, w.org, w.venue, performer, date=dt.date(2026, 10, 8)
        )
        recent.performance_status = "approved"
        old_approved = add_performance(
            w.db, w.org, w.venue, performer, date=dt.date(2026, 9, 20)
        )
        old_approved.performance_status = "approved"
        future = add_performance(
            w.db, w.org, w.venue, performer, date=dt.date(2026, 10, 20)
        )
        w.db.flush()
        lookup = fake_lookup({})
        closing_manager.resolve_for_org(w.db, w.org.id, now=NOW, lookup_fn=lookup)
        assert lookup.calls[0][1] == ["2026-10-08"]
        assert (
            future.closing_manager_resolved_at is None
            and old_approved.closing_manager_resolved_at is None
        )

    def test_settings_roles_are_passed(self, db_session):
        w = make_world(db_session)
        from app.services.entertainers._shared import settings_for

        settings_for(w.db, w.org.id).closing_manager_roles = [
            "Sales Manager",
            "Duty Manager",
        ]
        performer = add_performer(w.db, w.org)
        add_performance(w.db, w.org, w.venue, performer)
        lookup = fake_lookup({})
        closing_manager.resolve_for_org(w.db, w.org.id, now=NOW, lookup_fn=lookup)
        assert lookup.calls[0][2] == ["Sales Manager", "Duty Manager"]


def _resolved(w, perf, *, user=True, source="clockin"):
    perf.closing_manager_staff_id = "sm-1"
    perf.closing_manager_name = "Casey Closer"
    perf.closing_manager_email = (
        "closer@cookbros.test" if user else "nobody@elsewhere.test"
    )
    perf.closing_manager_user_id = w.manager.id if user else None
    perf.closing_manager_source = source
    perf.closing_manager_resolved_at = NOW
    w.db.flush()


class TestApprovalEmails:
    def test_the_email_carries_the_deep_link_and_reminders_stop_at_four(
        self, db_session
    ):
        w = make_world(db_session)
        performer = add_performer(w.db, w.org)
        perf = add_performance(w.db, w.org, w.venue, performer)
        _resolved(w, perf)
        out = closing_manager.send_approval_requests(w.db, w.org.id, now=NOW)
        assert out["sent"] == 1 and out["exhausted"] == [] and out["no_login"] == []
        link = (
            w.db.query(EntEmailLink)
            .filter(EntEmailLink.kind == "ent_manager_approval")
            .one()
        )
        assert (
            link.recipient_kind == "closing_manager"
            and link.recipient_email == "closer@cookbros.test"
        )
        log = w.db.query(EmailLog).filter(EmailLog.id == link.email_log_id).one()
        assert f"/?page=ent-roster&performance={perf.id}" in html.unescape(
            log.html_body
        )
        assert "Casey" in log.html_body and "Sam Smith" in log.html_body
        # same day: nothing; three days on: a reminder; four in all, then exhausted
        assert (
            closing_manager.send_approval_requests(w.db, w.org.id, now=NOW)["sent"] == 0
        )
        for n in range(1, 4):
            later = NOW + dt.timedelta(days=3 * n)
            assert (
                closing_manager.send_approval_requests(w.db, w.org.id, now=later)[
                    "sent"
                ]
                == 1
            ), n
        assert (
            email_links.send_count(
                w.db, w.org.id, "ent_manager_approval", target_id=perf.id
            )
            == 4
        )
        final = closing_manager.send_approval_requests(
            w.db, w.org.id, now=NOW + dt.timedelta(days=12)
        )
        assert final["sent"] == 0 and [p.id for p in final["exhausted"]] == [perf.id]
        last = w.db.query(EmailLog).order_by(EmailLog.created_at.desc()).first()
        assert "Reminder" in last.subject

    def test_no_norm_login_gets_no_email_and_goes_on_the_digest(self, db_session):
        w = make_world(db_session)
        performer = add_performer(w.db, w.org)
        perf = add_performance(w.db, w.org, w.venue, performer)
        _resolved(w, perf, user=False)
        out = closing_manager.send_approval_requests(w.db, w.org.id, now=NOW)
        assert out["sent"] == 0 and [p.id for p in out["no_login"]] == [perf.id]
        sent = closing_manager.send_digest(w.db, w.org.id, out, now=NOW)
        assert sent == 1
        link = (
            w.db.query(EntEmailLink)
            .filter(EntEmailLink.kind == "ent_manager_digest")
            .one()
        )
        assert (
            link.recipient_kind == "venue_manager"
            and link.recipient_email == "closer@cookbros.test"
        )
        log = w.db.query(EmailLog).filter(EmailLog.id == link.email_log_id).one()
        assert (
            "no Norm login" in log.html_body
            and "nobody@elsewhere.test" not in log.html_body
        )
        assert "Casey Closer" in log.html_body and perf.id in log.html_body

    def test_orbits_four_sends_count(self, db_session):
        w = make_world(db_session)
        performer = add_performer(w.db, w.org)
        perf = add_performance(w.db, w.org, w.venue, performer)
        _resolved(w, perf, source="orbit")
        for n in range(4):
            email_links.record(
                w.db,
                None,
                org_id=w.org.id,
                kind="manager_approval",
                recipient_kind="closing_manager",
                recipient_email=None,
                performer_id=performer.id,
                performance_ids=[perf.id],
                sent_at=NOW - dt.timedelta(days=20 - n),
                source="orbit",
            )
        out = closing_manager.send_approval_requests(w.db, w.org.id, now=NOW)
        assert out["sent"] == 0 and [p.id for p in out["exhausted"]] == [perf.id]

    def test_the_digest_names_venues_with_nobody_found_and_unconfirmed_performers(
        self, db_session
    ):
        w = make_world(db_session)
        performer = add_performer(w.db, w.org)
        nobody = add_performance(
            w.db, w.org, w.venue, performer, date=dt.date(2026, 10, 9)
        )
        nobody.closing_manager_source = "none"
        nobody.closing_manager_resolved_at = NOW
        unconfirmed = add_performance(
            w.db, w.org, w.venue, performer, date=dt.date(2026, 10, 3)
        )
        unconfirmed.performance_status = "approved"
        (
            unconfirmed.actual_start_time,
            unconfirmed.actual_end_time,
            unconfirmed.actual_cents,
        ) = unconfirmed.start_time, unconfirmed.end_time, 40_000
        w.db.flush()
        for n in range(4):
            email_links.record(
                w.db,
                None,
                org_id=w.org.id,
                kind="ent_confirm_request",
                recipient_kind="performer",
                recipient_email="sam@example.test",
                performer_id=performer.id,
                performance_ids=[unconfirmed.id],
                sent_at=NOW - dt.timedelta(days=12 - 3 * n),
            )
        out = closing_manager.send_approval_requests(w.db, w.org.id, now=NOW)
        assert [p.id for p in out["no_manager"]] == [nobody.id]
        assert closing_manager.send_digest(w.db, w.org.id, out, now=NOW) == 1
        log = (
            w.db.query(EmailLog)
            .filter(EmailLog.template_name == "ent_manager_digest")
            .one()
        )
        assert (
            "No closing manager found" in log.html_body
            and "still unconfirmed" in log.html_body
        )
        link = (
            w.db.query(EntEmailLink)
            .filter(EntEmailLink.kind == "ent_manager_digest")
            .one()
        )
        assert set(link.performance_ids) == {nobody.id, unconfirmed.id}

    def test_nothing_open_means_no_digest(self, db_session):
        w = make_world(db_session)
        assert (
            closing_manager.send_digest(
                w.db,
                w.org.id,
                {"exhausted": [], "no_manager": [], "no_login": []},
                now=NOW,
            )
            == 0
        )


class TestMorningJob:
    def test_runs_once_per_org_day_at_or_after_eight_local(
        self, db_session, monkeypatch
    ):
        w = make_world(db_session)
        performer = add_performer(w.db, w.org)
        perf = add_performance(w.db, w.org, w.venue, performer)
        lookup = fake_lookup(
            {
                "2026-10-10": {
                    "name": "Casey Closer",
                    "staff_member_id": "sm-1",
                    "email": "closer@cookbros.test",
                    "source": "clockin",
                }
            }
        )
        monkeypatch.setattr(closing_manager, "lookup", lookup)
        early = dt.datetime(2026, 10, 11, 18, 0, tzinfo=UTC)  # 07:00 NZDT: not yet
        assert jobs.run_due(w.db, early)["morning"] == {}
        out = jobs.run_due(w.db, NOW)["morning"][w.org.id]
        assert (
            out["errors"] == []
            and out["closing_manager"]["resolved"] == 1
            and out["approval_emails"]["sent"] == 1
        )
        run = (
            w.db.query(FinJobRun)
            .filter(
                FinJobRun.job == "ent.morning", FinJobRun.organization_id == w.org.id
            )
            .one()
        )
        assert (
            run.finished_at is not None
            and run.error is None
            and run.slot == "2026-10-12"
        )
        assert jobs.run_due(w.db, NOW + dt.timedelta(hours=1))["morning"] == {}
        w.db.refresh(perf)
        assert perf.closing_manager_user_id == w.manager.id
        assert (
            w.db.query(EntEmailLink)
            .filter(EntEmailLink.kind == "ent_manager_approval")
            .count()
            == 1
        )

    def test_one_failing_step_does_not_stop_the_rest(self, db_session, monkeypatch):
        w = make_world(db_session)
        performer = add_performer(w.db, w.org)
        add_performance(w.db, w.org, w.venue, performer)

        def boom(db, venue, dates, roles):
            raise RuntimeError("Loaded is down")

        monkeypatch.setattr(closing_manager, "lookup", boom)
        out = jobs.run_due(w.db, NOW)["morning"][w.org.id]
        assert (
            out["errors"] == [] and out["closing_manager"]["failed"] == 1
        )  # a failed venue is reported, not raised
        run = (
            w.db.query(FinJobRun)
            .filter(
                FinJobRun.job == "ent.morning", FinJobRun.organization_id == w.org.id
            )
            .one()
        )
        assert run.error is None and "digest" in out

    def test_shadow_runs_the_job_too_with_emails_to_the_contact(
        self, db_session, monkeypatch
    ):
        w = make_world(db_session, mode="shadow")
        performer = add_performer(w.db, w.org)
        add_performance(w.db, w.org, w.venue, performer)
        monkeypatch.setattr(
            closing_manager,
            "lookup",
            fake_lookup(
                {
                    "2026-10-10": {
                        "name": "Casey Closer",
                        "staff_member_id": "sm-1",
                        "email": "closer@cookbros.test",
                        "source": "clockin",
                    }
                }
            ),
        )
        jobs.run_due(w.db, NOW)
        link = (
            w.db.query(EntEmailLink)
            .filter(EntEmailLink.kind == "ent_manager_approval")
            .one()
        )
        log = w.db.query(EmailLog).filter(EmailLog.id == link.email_log_id).one()
        assert log.to_addresses == ["payroll@cookbros.test"]
