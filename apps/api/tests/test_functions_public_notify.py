"""Outgoing email from the public surface, and retention of what it collects.

Nothing leaves a non-production server (a local database holds real
customers); a one-time code is never written down, not even in the email log;
and the personal data the portal and forms collect is trimmed on schedule.
"""

from __future__ import annotations

import datetime as dt
import logging

import pytest

from app.db.functions_models import FnFormSubmission, FnPortalAccess, FnPortalSession
from app.db.models import EmailLog, _now
from app.services.functions import events, notify, public_tokens, retention
from app.services.functions import forms as forms_svc
from tests.functions_world import add_contact, make_world


@pytest.fixture
def w(db_session):
    return make_world(db_session)


class TestSending:
    def test_outside_production_it_is_logged_not_sent(self, w, monkeypatch):
        called = []
        monkeypatch.setattr(notify, "_resend", lambda payload: called.append(payload))
        result = notify.send_email(
            w.db,
            to=["Events@Brewbar.example"],
            subject="Hello\r\n",
            html_body="<p>x</p>",
            template="test",
            org_id=w.org.id,
        )
        assert result.status == "suppressed" and called == []
        log = w.db.get(EmailLog, result.log_id)
        assert log.status == "suppressed"
        assert log.subject == "Hello"
        assert log.to_addresses == ["events@brewbar.example"]
        assert log.template_name == "functions:test"

    def test_in_production_it_sends_with_reply_to_and_logs(self, w, monkeypatch):
        sent = []
        monkeypatch.setattr(notify, "suppressed", lambda: False)
        monkeypatch.setattr(notify.settings, "RESEND_API_KEY", "re_test")
        monkeypatch.setattr(
            notify, "_resend", lambda payload: sent.append(payload) or "id-1"
        )
        result = notify.send_email(
            w.db,
            to=["a@example.com"],
            subject="Hi\r\nBcc: attacker@example.com",
            html_body="<p>x</p>",
            template="test",
            reply_to="customer@example.com",
            from_name="Brew Bar",
        )
        assert result.status == "sent" and result.provider_id == "id-1"
        assert sent[0]["subject"] == "Hi Bcc: attacker@example.com"
        assert sent[0]["reply_to"] == "customer@example.com"
        assert sent[0]["from"].startswith("Brew Bar <")
        assert w.db.get(EmailLog, result.log_id).status == "sent"

    def test_a_failed_send_is_reported_not_raised(self, w, monkeypatch):
        monkeypatch.setattr(notify, "suppressed", lambda: False)
        monkeypatch.setattr(notify.settings, "RESEND_API_KEY", "re_test")

        def boom(payload):
            raise RuntimeError("provider down")

        monkeypatch.setattr(notify, "_resend", boom)
        result = notify.send_email(
            w.db, to=["a@example.com"], subject="Hi", html_body="x", template="test"
        )
        assert result.status == "failed" and "provider down" in result.error

    def test_no_recipients_is_skipped(self, w):
        result = notify.send_email(
            w.db, to=[], subject="Hi", html_body="x", template="t"
        )
        assert result.status == "skipped"

    def test_a_portal_code_is_never_logged_to_email_logs(self, w, monkeypatch, caplog):
        with caplog.at_level(logging.WARNING, logger="app.services.functions.notify"):
            notify.portal_code(
                to="pat@example.com",
                code="123456",
                venue_name="Brew Bar",
                event_id="e1",
            )
        assert "123456" in caplog.text  # dev only: how a local tester gets in
        sent = []
        monkeypatch.setattr(notify, "suppressed", lambda: False)
        monkeypatch.setattr(notify.settings, "RESEND_API_KEY", "re_test")
        monkeypatch.setattr(
            notify, "_resend", lambda payload: sent.append(payload) or "id"
        )
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="app.services.functions.notify"):
            notify.portal_code(
                to="pat@example.com",
                code="654321",
                venue_name="Brew Bar",
                event_id="e1",
            )
        assert "654321" in sent[0]["html"]
        assert "654321" not in caplog.text  # production logs never hold a code
        logged = w.db.query(EmailLog).filter(
            EmailLog.template_name == "functions:portal_code"
        )
        assert logged.count() == 0  # never, anywhere

    def test_staff_recipients_are_the_owner_and_notify_list(self, w):
        from app.db.functions_models import FnVenueFunctionSettings

        row = w.db.query(FnVenueFunctionSettings).filter_by(venue_id=w.venue.id).one()
        row.notify_emails = ["Events@Brewbar.example", "not-an-address"]
        w.db.flush()
        assert notify.staff_recipients(
            w.db, w.org.id, w.venue.id, w.staff.id
        ) == sorted(["events@brewbar.example", w.staff.email.lower()])
        assert notify.staff_recipients(w.db, w.org.id, w.venue2.id) == []

    def test_staff_emails_escape_customer_text(self, w):
        from app.db.functions_models import FnVenueFunctionSettings

        row = w.db.query(FnVenueFunctionSettings).filter_by(venue_id=w.venue.id).one()
        row.notify_emails = ["events@brewbar.example"]
        w.db.flush()
        result = notify.option_responded(
            w.db,
            org_id=w.org.id,
            venue_id=w.venue.id,
            owner_user_id=None,
            enquiry_id="enq",
            event_id="ev",
            kind="confirm",
            contact_name="<b>Casey</b>",
            summary_rows=[("Option", "<script>alert(1)</script>")],
            note="<img src=x onerror=alert(1)>",
        )
        body = w.db.get(EmailLog, result.log_id).html_body
        assert "<script>" not in body and "<img" not in body and "<b>Casey" not in body
        assert "&lt;script&gt;" in body
        assert "page=functions-events&amp;event=ev" in body


class TestRetention:
    def test_purge_trims_codes_sessions_and_submission_details(self, w):
        contact = add_contact(w.db, w.org.id, email="pat@example.com")
        event = events.create_event(
            w.db,
            w.ctx(),
            venue_id=w.venue.id,
            contact_id=contact.id,
            event_date=dt.date(2026, 12, 4),
            booking_type="group_booking",
        )
        public_tokens.mint_portal_link(
            w.db, w.ctx(), event.id, ["old@example.com", "new@example.com"]
        )
        now = _now()
        old_code, new_code = (
            w.db.query(FnPortalAccess).filter_by(email=e, event_id=event.id).one()
            for e in ("old@example.com", "new@example.com")
        )
        for access, age in (
            (old_code, dt.timedelta(days=2)),
            (new_code, dt.timedelta(minutes=5)),
        ):
            access.otp_hash = "h"
            access.otp_expires_at = now - age + dt.timedelta(minutes=10)
            access.otp_attempts = 2
            access.otp_sends_in_window = 1
            access.otp_window_started_at = now - age
        sessions = [
            FnPortalSession(
                organization_id=w.org.id,
                session_hash=f"{i}" * 64,
                event_id=event.id,
                email="new@example.com",
                expires_at=expires,
                revoked_at=revoked,
            )
            for i, (expires, revoked) in enumerate(
                [
                    (now - dt.timedelta(seconds=1), None),  # expired
                    (
                        now + dt.timedelta(days=5),
                        now - dt.timedelta(days=2),
                    ),  # revoked long ago
                    (now + dt.timedelta(days=5), None),  # live
                ]
            )
        ]
        w.db.add_all(sessions)
        form = forms_svc.create_form(
            w.db, w.ctx(w.owner), venue_id=w.venue.id, name="Old form"
        )
        subs = [
            FnFormSubmission(
                organization_id=w.org.id,
                form_id=form.id,
                venue_id=w.venue.id,
                payload={},
                submitted_at=now - age,
                ip_hash="abc",
                user_agent="UA",
            )
            for age in (dt.timedelta(days=200), dt.timedelta(days=10))
        ]
        w.db.add_all(subs)
        w.db.flush()

        counts = retention.purge(w.db, now=now)
        assert counts["otp_cleared"] >= 1
        assert counts["sessions_deleted"] >= 2
        assert counts["submissions_scrubbed"] >= 1
        for row in (old_code, new_code, *subs):
            w.db.refresh(row)
        assert old_code.otp_hash is None and old_code.otp_window_started_at is None
        assert new_code.otp_hash == "h" and new_code.otp_attempts == 2
        left = w.db.query(FnPortalSession).filter_by(event_id=event.id).all()
        assert [s.session_hash for s in left] == ["2" * 64]
        assert subs[0].ip_hash is None and subs[0].user_agent is None
        assert subs[1].ip_hash == "abc" and subs[1].user_agent == "UA"
