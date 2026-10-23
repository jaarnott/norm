"""The one sender and the Emails page (D32): every send writes its
``ent_email_links`` row; shadow routes to the payroll contact (none →
suppressed, logged); idempotent per kind, target and send number; the code
is never written down; a money email's body is refused to a viewer without
``payroll:write``; resend inside the throttle; the Resend webhook refuses a
bad signature and moves a row on a good one.
"""

from __future__ import annotations

import base64
import datetime as dt
import hmac
import json
import time

import pytest

from app.config import settings
from app.db.entertainers_models import EntEmailLink
from app.db.models import EmailLog
from app.routers.email_events import verify_signature
from app.services.entertainers import emails
from app.services.finance_core import modes
from tests.entertainers_world import add_performer, approved, make_world


def _deliver(db, org, performer, kind="ent_confirm_request", perf=None, **ctx):
    context = {
        "first_name": "Sam",
        "venue_name": "Brew Bar",
        "date_text": "Saturday 10 October 2026",
        "time_text": "21:00–01:00",
        "url": "https://x/p/t",
        "expires_days": 14,
        **ctx,
    }
    return emails.deliver(
        db,
        org_id=org.id,
        kind=kind,
        to=performer.email,
        context=context,
        performer_id=performer.id,
        venue_id=perf.venue_id if perf else None,
        performance_ids=[perf.id] if perf else None,
        target_id=perf.id if perf else performer.id,
    )


class TestSender:
    def test_live_outside_production_is_logged_suppressed_with_the_exact_body(
        self, db_session
    ):
        w = make_world(db_session)
        performer = add_performer(w.db, w.org)
        perf = approved(w.db, w.org, w.venue, performer)
        link = _deliver(w.db, w.org, performer, perf=perf)
        log = w.db.query(EmailLog).filter(EmailLog.id == link.email_log_id).one()
        assert log.status == "suppressed" and log.to_addresses == ["sam@example.test"]
        assert (
            "https://x/p/t" in log.html_body
            and log.idempotency_key == f"ent:ent_confirm_request:{perf.id}:1"
        )
        assert (
            link.recipient_kind == "performer"
            and link.performance_ids == [perf.id]
            and link.venue_id == w.venue.id
        )

    def test_shadow_routes_to_the_payroll_contact_and_says_so(self, db_session):
        w = make_world(db_session, mode="shadow")
        performer = add_performer(w.db, w.org)
        link = _deliver(w.db, w.org, performer)
        log = w.db.query(EmailLog).filter(EmailLog.id == link.email_log_id).one()
        assert (
            log.to_addresses == ["payroll@cookbros.test"]
            and link.recipient_email == "sam@example.test"
        )
        assert "was meant for" in log.html_body and "sam@example.test" in log.html_body

    def test_shadow_with_no_contact_is_suppressed_and_logged(self, db_session):
        w = make_world(db_session, mode="shadow", contact=None)
        performer = add_performer(w.db, w.org)
        link = _deliver(w.db, w.org, performer)
        log = w.db.query(EmailLog).filter(EmailLog.id == link.email_log_id).one()
        assert log.status == "suppressed" and "no payroll contact" in log.error_message

    def test_idempotent_per_kind_target_and_send_number(self, db_session):
        w = make_world(db_session)
        performer = add_performer(w.db, w.org)
        perf = approved(w.db, w.org, w.venue, performer)
        first = _deliver(w.db, w.org, performer, perf=perf)
        second = _deliver(w.db, w.org, performer, perf=perf)  # send number 2: allowed
        assert first is not None and second is not None
        log2 = w.db.query(EmailLog).filter(EmailLog.id == second.email_log_id).one()
        assert log2.idempotency_key.endswith(":2")
        # a retry that finds its own number already sent does nothing: the
        # log row is the record, even with the page's link row gone
        w.db.delete(second)
        w.db.flush()
        assert _deliver(w.db, w.org, performer, perf=perf) is None
        assert (
            w.db.query(EntEmailLink)
            .filter(EntEmailLink.kind == "ent_confirm_request")
            .count()
            == 1
        )
        assert (
            w.db.query(EmailLog)
            .filter(EmailLog.template_name == "ent_confirm_request")
            .count()
            == 2
        )

    def test_the_send_path_goes_through_send_system_email(
        self, db_session, monkeypatch
    ):
        w = make_world(db_session)
        performer = add_performer(w.db, w.org)
        seen = {}

        def fake_send(
            template_name,
            to,
            context,
            db,
            organization_id=None,
            thread_id=None,
            from_name=None,
            reply_to=None,
        ):
            seen.update(
                template=template_name,
                to=to,
                org=organization_id,
                from_name=from_name,
                reply_to=reply_to,
            )
            log = EmailLog(
                organization_id=organization_id,
                sender_type="system",
                sender_email="n@x",
                to_addresses=to,
                subject="s",
                template_name=template_name,
                html_body="<p>code 123456</p>",
                provider="resend",
                status="sent",
                provider_message_id="re_1",
            )
            db.add(log)
            db.flush()
            return log.id

        import app.services.email_service as email_service

        monkeypatch.setattr(email_service, "send_system_email", fake_send)
        monkeypatch.setattr(emails, "suppressed", lambda: False)
        link = _deliver(
            w.db, w.org, performer, kind="ent_code", code="123456", minutes=10
        )
        # From Norm's address in the organisation's name (D26), and a reply
        # reaches the payroll contact rather than the no-reply box.
        assert seen == {
            "template": "ent_code",
            "to": ["sam@example.test"],
            "org": w.org.id,
            "from_name": "Cook Bros",
            "reply_to": "payroll@cookbros.test",
        }
        log = w.db.query(EmailLog).filter(EmailLog.id == link.email_log_id).one()
        assert (
            log.html_body is None
            and log.app_slug == "entertainers"
            and log.status == "sent"
        )

    def test_resend_gets_norms_address_the_orgs_name_and_reply_to(
        self, db_session, monkeypatch
    ):
        """D26: no sender of its own — Norm's address, the organisation's
        name (quoted when it holds a comma), replies to the payroll contact;
        with no contact, no Reply-To."""
        import sys
        from types import SimpleNamespace

        w = make_world(db_session)
        w.org.name = "Cook Bros, Ltd"
        w.org.entertainers_mode = "live"
        performer = add_performer(w.db, w.org)
        payloads = []
        fake = SimpleNamespace(
            api_key=None,
            Emails=SimpleNamespace(send=lambda p: payloads.append(p) or {"id": "re_9"}),
        )
        monkeypatch.setitem(sys.modules, "resend", fake)
        monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test")
        monkeypatch.setattr(emails, "suppressed", lambda: False)
        _deliver(w.db, w.org, performer)
        assert payloads[0]["from"] == (
            f'"Cook Bros, Ltd" <{settings.EMAIL_FROM_ADDRESS}>'
        )
        assert payloads[0]["reply_to"] == "payroll@cookbros.test"
        assert payloads[0]["to"] == ["sam@example.test"]
        w.org.payroll_contact_email = None
        _deliver(w.db, w.org, performer, kind="ent_confirm_reminder")
        assert "reply_to" not in payloads[1]

    def test_an_unknown_kind_is_a_programming_error(self, db_session):
        w = make_world(db_session)
        with pytest.raises(ValueError):
            emails.deliver(
                w.db, org_id=w.org.id, kind="ent_nope", to="a@b.c", context={}
            )


class TestEmailsPage:
    def test_list_and_filters_on_venue_access_and_money_bodies_on_payroll_write(
        self, db_session, client
    ):
        w = make_world(db_session)
        performer = add_performer(w.db, w.org, lane="invoice")
        perf = approved(w.db, w.org, w.venue, performer)
        other = approved(w.db, w.org, w.venue2, performer, date=dt.date(2026, 10, 3))
        _deliver(w.db, w.org, performer, perf=perf)
        money = _deliver(
            w.db,
            w.org,
            performer,
            kind="ent_approved_amount",
            perf=perf,
            times="21:00–01:00",
            amount_text="$400.00",
            gst_text="no GST",
            reference="NORM-1",
        )
        at_venue2 = _deliver(w.db, w.org, performer, perf=other)
        w.db.commit()
        team = w.headers(w.team_member)
        rows = client.get("/api/entertainers/emails", headers=team).json()
        ids = {r["id"] for r in rows["emails"]}
        assert money.id in ids and at_venue2.id not in ids  # venue 2 is not theirs
        assert rows["total"] == 2
        assert (
            client.get(
                "/api/entertainers/emails",
                params={"kind": "ent_approved_amount"},
                headers=team,
            ).json()["total"]
            == 1
        )
        assert (
            client.get(
                "/api/entertainers/emails",
                params={"performance_id": perf.id},
                headers=team,
            ).json()["total"]
            == 2
        )
        r = client.get(f"/api/entertainers/emails/{money.id}", headers=team)
        assert r.status_code == 403
        r = client.get(f"/api/entertainers/emails/{money.id}", headers=w.headers())
        assert (
            r.status_code == 200
            and "$400.00" in r.json()["html_body"]
            and r.json()["is_money"] is True
        )
        plain = next(r for r in rows["emails"] if r["kind"] == "ent_confirm_request")
        r = client.get(f"/api/entertainers/emails/{plain['id']}", headers=team)
        assert r.status_code == 200 and "https://x/p/t" in r.json()["html_body"]
        assert (
            client.get(
                f"/api/entertainers/emails/{at_venue2.id}", headers=team
            ).status_code
            == 404
        )

    def test_resend_inside_the_throttle(self, db_session, client):
        w = make_world(db_session)
        performer = add_performer(w.db, w.org, status="invited")
        r = client.post(
            f"/api/entertainers/performers/{performer.id}/invite", headers=w.headers()
        )
        assert r.status_code == 200
        link = w.db.query(EntEmailLink).filter(EntEmailLink.kind == "ent_invite").one()
        r = client.post(
            f"/api/entertainers/emails/{link.id}/resend", headers=w.headers()
        )
        assert r.status_code == 409  # less than a day ago
        link.sent_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)
        w.db.commit()
        r = client.post(
            f"/api/entertainers/emails/{link.id}/resend", headers=w.headers()
        )
        assert r.status_code == 200 and r.json()["sent"] is True
        assert (
            w.db.query(EntEmailLink).filter(EntEmailLink.kind == "ent_invite").count()
            == 2
        )
        money = _deliver(
            w.db,
            w.org,
            performer,
            kind="ent_approved_amount",
            times="x",
            amount_text="$1",
            gst_text="",
            reference="r",
        )
        w.db.commit()
        assert (
            client.post(
                f"/api/entertainers/emails/{money.id}/resend", headers=w.headers()
            ).status_code
            == 400
        )

    def test_orbits_history_rows_count_toward_the_throttle(self, db_session):
        w = make_world(db_session)
        performer = add_performer(w.db, w.org)
        perf = approved(w.db, w.org, w.venue, performer)
        from app.services.entertainers import email_links

        for n in range(4):
            email_links.record(
                w.db,
                None,
                org_id=w.org.id,
                kind="confirm_request",
                recipient_kind="performer",
                recipient_email=None,
                performer_id=performer.id,
                performance_ids=[perf.id],
                sent_at=dt.datetime(2026, 9, 1 + n, tzinfo=dt.timezone.utc),
                source="orbit",
            )
        assert (
            email_links.send_count(
                w.db,
                w.org.id,
                ("ent_confirm_request", "ent_confirm_reminder"),
                target_id=perf.id,
            )
            == 4
        )
        from app.services.entertainers import performances

        assert (
            performances.send_confirm_reminders(
                w.db, w.org.id, now=dt.datetime(2026, 10, 12, tzinfo=dt.timezone.utc)
            )
            == 0
        )


SECRET = "whsec_" + base64.b64encode(b"a-test-signing-key").decode()


def _sign(body: bytes, *, msg_id="msg_1", ts=None, secret=SECRET) -> dict:
    ts = ts or int(time.time())
    key = base64.b64decode(secret.split("_", 1)[1])
    sig = base64.b64encode(
        hmac.new(key, f"{msg_id}.{ts}.".encode() + body, "sha256").digest()
    ).decode()
    return {"svix-id": msg_id, "svix-timestamp": str(ts), "svix-signature": f"v1,{sig}"}


class TestWebhook:
    def _log(self, db, org, message_id="re_abc"):
        log = EmailLog(
            organization_id=org.id,
            sender_type="system",
            sender_email="n@x",
            to_addresses=["a@b.c"],
            subject="s",
            template_name="ent_invite",
            html_body="<p/>",
            provider="resend",
            status="sent",
            provider_message_id=message_id,
        )
        db.add(log)
        db.commit()
        return log

    def test_a_good_signature_moves_the_row_to_bounced(
        self, db_session, client, monkeypatch
    ):
        w = make_world(db_session)
        log = self._log(w.db, w.org)
        monkeypatch.setattr(settings, "RESEND_WEBHOOK_SECRET", SECRET)
        body = json.dumps(
            {
                "type": "email.bounced",
                "data": {"email_id": "re_abc", "bounce": {"message": "mailbox full"}},
            }
        ).encode()
        r = client.post(
            "/api/public/email-events/resend",
            content=body,
            headers={**_sign(body), "content-type": "application/json"},
        )
        assert r.status_code == 200 and r.json() == {"ok": True, "updated": True}
        assert r.headers["X-Robots-Tag"] == "noindex, nofollow"
        w.db.refresh(log)
        assert log.status == "bounced" and log.error_message == "mailbox full"
        # a later "delivered" never undoes the bounce
        body = json.dumps(
            {"type": "email.delivered", "data": {"email_id": "re_abc"}}
        ).encode()
        assert (
            client.post(
                "/api/public/email-events/resend", content=body, headers=_sign(body)
            ).json()["updated"]
            is True
        )
        w.db.refresh(log)
        assert log.status == "bounced"

    def test_a_bad_signature_is_400_and_nothing_changes(
        self, db_session, client, monkeypatch
    ):
        w = make_world(db_session)
        log = self._log(w.db, w.org)
        monkeypatch.setattr(settings, "RESEND_WEBHOOK_SECRET", SECRET)
        body = json.dumps(
            {"type": "email.bounced", "data": {"email_id": "re_abc"}}
        ).encode()
        wrong = _sign(body, secret="whsec_" + base64.b64encode(b"another-key").decode())
        assert (
            client.post(
                "/api/public/email-events/resend", content=body, headers=wrong
            ).status_code
            == 400
        )
        assert (
            client.post("/api/public/email-events/resend", content=body).status_code
            == 400
        )
        stale = _sign(body, ts=int(time.time()) - 3600)
        assert (
            client.post(
                "/api/public/email-events/resend", content=body, headers=stale
            ).status_code
            == 400
        )
        tampered = _sign(body)
        assert (
            client.post(
                "/api/public/email-events/resend", content=body + b" ", headers=tampered
            ).status_code
            == 400
        )
        w.db.refresh(log)
        assert log.status == "sent"

    def test_no_secret_configured_is_503(self, client, monkeypatch):
        monkeypatch.setattr(settings, "RESEND_WEBHOOK_SECRET", "")
        body = b"{}"
        assert (
            client.post(
                "/api/public/email-events/resend", content=body, headers=_sign(body)
            ).status_code
            == 503
        )

    def test_other_events_and_unknown_ids_are_acknowledged(
        self, db_session, client, monkeypatch
    ):
        make_world(db_session)
        monkeypatch.setattr(settings, "RESEND_WEBHOOK_SECRET", SECRET)
        body = json.dumps(
            {"type": "email.opened", "data": {"email_id": "re_abc"}}
        ).encode()
        assert client.post(
            "/api/public/email-events/resend", content=body, headers=_sign(body)
        ).json() == {"ok": True, "updated": False, "ignored": "email.opened"}
        body = json.dumps(
            {"type": "email.delivered", "data": {"email_id": "re_unknown"}}
        ).encode()
        assert (
            client.post(
                "/api/public/email-events/resend", content=body, headers=_sign(body)
            ).json()["updated"]
            is False
        )

    def test_verify_signature_accepts_any_of_several_v1_values(self):
        body = b'{"x":1}'
        good = _sign(body)
        rotated = f"v1,{base64.b64encode(b'x' * 32).decode()} {good['svix-signature']}"
        assert verify_signature(
            SECRET,
            msg_id="msg_1",
            timestamp=good["svix-timestamp"],
            signature=rotated,
            body=body,
        )
        assert not verify_signature(
            SECRET,
            msg_id="msg_2",
            timestamp=good["svix-timestamp"],
            signature=good["svix-signature"],
            body=body,
        )
        assert not verify_signature(
            "not-base64-!!",
            msg_id="msg_1",
            timestamp=good["svix-timestamp"],
            signature=good["svix-signature"],
            body=body,
        )


def test_modes_module_is_the_switch(db_session):
    w = make_world(db_session, mode="shadow")
    assert modes.mode(w.db, w.org.id, "entertainers") == "shadow"


def test_subjects_are_plain_text_on_one_line(db_session):
    """ "Bessie &amp; Royals" reached an inbox once (Oct 2026): subjects are not HTML."""
    w = make_world(db_session)
    w.venue.name = "Bessie & Royals"
    performer = add_performer(w.db, w.org)
    perf = approved(w.db, w.org, w.venue, performer)
    link = _deliver(w.db, w.org, performer, perf=perf, venue_name="Bessie & Royals\nX")
    log = w.db.query(EmailLog).filter(EmailLog.id == link.email_log_id).one()
    assert (
        "Bessie & Royals X" in log.subject
        and "&amp;" not in log.subject
        and "\n" not in log.subject
    )
    assert "Bessie &amp; Royals" in log.html_body  # the body is still escaped
