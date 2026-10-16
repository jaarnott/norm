"""Sends through a venue mailbox (services/functions/mailbox/outbox.py and
sending.py), against a fake Gmail.

* written and committed before Gmail is called; one email per idempotency
  key, however many times it is posted; a failed send retried with the same
  Message-ID;
* a lost Gmail answer leaves the row ``sending`` for the reconciler, which
  finds it by our Message-ID (or, when Gmail replaced it, by subject and
  recipient) — or gives up and marks it failed;
* how a reply looks: threading headers, "<First> from <Venue>", signature,
  quoted history; a forward is a new conversation;
* the mirror row is written at once (the enquiry is "replied" without a
  sync) and the facts recorded: proposal version sent + quoted, portal link
  sent, the thread linked;
* fail closed: shadow mode, a mailbox needing reconnection, a dead grant;
* the pricing warning, and its audited override.
"""

from __future__ import annotations

import datetime as dt
import logging

import pytest
from sqlalchemy import update

from app.db.functions_models import (
    FnEventChange,
    FnMailboxAccount,
    FnMailboxMessage,
    FnOutbox,
    FnPortalAccess,
    FnProposal,
    FnProposalEvent,
    FnProposalVersion,
    FnThreadLink,
)
from app.services.functions import enquiries as enquiries_svc
from app.services.functions import events as events_svc
from app.services.functions import status as status_svc
from app.services.functions.errors import Invalid, NotLive
from app.services.functions.mailbox import accounts as accounts_svc
from app.services.functions.mailbox import inbox, outbox, pricing, sending
from tests.functions_mailbox_fakes import (
    MAILBOX,
    FakeGmail,
    FakeTokens,
    install,
    mailbox_world,
    sessions,
)
from tests.functions_world import add_contact, add_enquiry

UTC = dt.timezone.utc
CUSTOMER = "casey@clientco.nz"


@pytest.fixture()
def fake(monkeypatch):
    fake = FakeGmail()
    install(monkeypatch, fake)
    return fake


def _thread(fake, thread_id="t-1", **kw) -> str:
    return fake.add_message(
        frm=f"Casey Client <{CUSTOMER}>",
        to=[MAILBOX],
        subject="Wedding in March",
        body="Can we book the deck?",
        thread_id=thread_id,
        message_id="<casey-1@clientco.nz>",
        headers={"References": "<earlier@clientco.nz>"},
        **kw,
    )


def _enquiry(db, w, thread_id=None):
    contact = add_contact(db, w.org.id, email=CUSTOMER)
    enquiry = add_enquiry(db, w.org.id, contact.id, w.venue.id, subject="Wedding")
    if thread_id:
        enquiries_svc.link_thread(
            db,
            w.ctx(),
            account_id=w.account.id,
            gmail_thread_id=thread_id,
            enquiry_id=enquiry.id,
        )
    return enquiry


def _reply(db, w, key="k-1", **kw):
    args = dict(
        to=[],
        subject=None,
        body_html="<p>Yes — the deck is free.</p>",
        thread_id="t-1",
        purpose="reply",
        idempotency_key=key,
    )
    args.update(kw)
    return outbox.send(db, w.ctx(), w.account.id, **args)


class TestReply:
    def test_a_reply_is_threaded_signed_quoted_and_mirrored_at_once(
        self, db_session, fake
    ):
        w = mailbox_world(db_session)
        w.account.signature_html = "<div>Brew Bar · 1 Quay St</div>"
        db_session.flush()
        _thread(fake)
        enquiry = _enquiry(db_session, w, "t-1")

        result = _reply(db_session, w)
        assert result.status == "sent" and not result.duplicate
        sent = fake.sent[-1]
        assert sent["threadId"] == "t-1"
        msg = fake.sent_message()
        assert msg["In-Reply-To"] == "<casey-1@clientco.nz>"
        assert msg["References"] == "<earlier@clientco.nz> <casey-1@clientco.nz>"
        assert msg["To"] == CUSTOMER
        assert msg["Subject"] == "Re: Wedding in March"
        assert str(msg["From"]) == f"Sam from Brew Bar <{MAILBOX}>"
        assert msg["Message-ID"] == result.rfc822_message_id
        html = msg.get_body(("html",)).get_content()
        assert "the deck is free" in html
        assert 'class="norm-signature"' in html and "1 Quay St" in html
        assert 'class="gmail_quote"' in html and "Can we book the deck?" in html

        row = (
            db_session.query(FnMailboxMessage)
            .filter_by(
                account_id=w.account.id, gmail_message_id=result.gmail_message_id
            )
            .one()
        )
        assert (row.is_inbound, row.is_internal) == (False, False)
        stage, _ = status_svc.enquiry_status(db_session, w.org.id, enquiry.id, None)
        assert stage == "replied"

    def test_the_same_key_is_one_email(self, db_session, fake):
        w = mailbox_world(db_session)
        _thread(fake)
        first = _reply(db_session, w, key="dup")
        second = _reply(db_session, w, key="dup", body_html="<p>different</p>")
        assert second.duplicate and second.outbox_id == first.outbox_id
        assert second.status == "sent" and len(fake.sent) == 1

    def test_a_send_in_flight_is_reported_not_repeated(self, db_session, fake):
        w = mailbox_world(db_session)
        _thread(fake)
        db_session.add(
            FnOutbox(
                organization_id=w.org.id,
                idempotency_key="busy",
                rfc822_message_id="<busy@x>",
                account_id=w.account.id,
                status="sending",
                purpose="reply",
            )
        )
        db_session.flush()
        result = _reply(db_session, w, key="busy")
        assert result.duplicate and result.status == "sending"
        assert fake.sent == []

    def test_a_refused_send_fails_and_a_retry_reuses_the_message_id(
        self, db_session, fake
    ):
        w = mailbox_world(db_session)
        _thread(fake)
        fake.fail["POST /messages/send"] = [(400, "invalidArgument")]
        with pytest.raises(outbox.SendFailed):
            _reply(db_session, w, key="retry")
        row = db_session.query(FnOutbox).filter_by(idempotency_key="retry").one()
        assert row.status == "failed" and "refused" in row.error
        first_id = row.rfc822_message_id

        again = _reply(db_session, w, key="retry")
        assert again.status == "sent" and again.rfc822_message_id == first_id
        assert fake.sent_message()["Message-ID"] == first_id
        db_session.refresh(row)
        assert row.attempts == 2

    def test_a_forward_is_a_new_conversation(self, db_session, fake):
        w = mailbox_world(db_session)
        _thread(fake)
        result = outbox.send(
            db_session,
            w.ctx(),
            w.account.id,
            to=["boss@cookbros.co.nz"],
            subject=None,
            body_html="FYI",
            thread_id="t-1",
            purpose="forward",
            idempotency_key="fwd",
        )
        assert result.status == "sent"
        assert fake.sent[-1]["threadId"] is None
        msg = fake.sent_message()
        assert msg["Subject"] == "Fwd: Wedding in March"
        assert msg["In-Reply-To"] is None
        assert "Forwarded message" in msg.get_body(("html",)).get_content()

    def test_header_injection_is_refused(self, db_session, fake):
        w = mailbox_world(db_session)
        _thread(fake)
        with pytest.raises(Invalid):
            _reply(db_session, w, to=["casey@clientco.nz\nBcc: everyone@x.com"])
        assert fake.sent == []


class TestFailClosed:
    def test_shadow_mode_refuses_before_anything_is_written(self, db_session, fake):
        w = mailbox_world(db_session, mode="shadow")
        _thread(fake)
        with pytest.raises(NotLive):
            _reply(db_session, w)
        assert (
            db_session.query(FnOutbox).filter_by(organization_id=w.org.id).count() == 0
        )
        assert fake.requests == []

    def test_a_mailbox_needing_reconnection_refuses(self, db_session, fake):
        w = mailbox_world(db_session)
        _thread(fake)
        w.account.status = "needs_reconnect"
        db_session.flush()
        with pytest.raises(accounts_svc.MailboxUnavailable):
            _reply(db_session, w)
        w.account.status = "active"
        w.conn.needs_reconnect = True
        db_session.flush()
        with pytest.raises(accounts_svc.MailboxUnavailable):
            _reply(db_session, w)
        assert fake.sent == []

    def test_a_dead_grant_at_send_time_fails_the_row(self, db_session, monkeypatch):
        fake = FakeGmail()
        w = mailbox_world(db_session)
        _thread(fake)
        install(monkeypatch, fake, FakeTokens(dead="invalid_grant"))
        with pytest.raises(accounts_svc.MailboxUnavailable):
            _reply(db_session, w, key="dead")
        assert fake.sent == []


class TestReconciler:
    def _lost(self, db, w, fake, key="lost"):
        fake.lose_send_response = True
        result = _reply(db, w, key=key)
        fake.lose_send_response = False
        assert result.status == "sending"
        row = db.query(FnOutbox).filter_by(idempotency_key=key).one()
        row.created_at = dt.datetime.now(UTC) - dt.timedelta(minutes=5)
        db.flush()
        return row

    def test_a_lost_answer_is_found_by_message_id_and_recorded(self, db_session, fake):
        w = mailbox_world(db_session)
        _thread(fake)
        enquiry = _enquiry(db_session, w, "t-1")
        row = self._lost(db_session, w, fake)
        account = accounts_svc.get_account(db_session, w.ctx(), w.account.id)
        from app.services.functions.mailbox import gmail_client
        from app.services.functions.mailbox.mirror import AccountRef

        acct = AccountRef.of(account)
        with gmail_client.client_for(acct, writable=True) as client:
            n = outbox.reconcile_account(
                acct, client, sessions(db_session), dt.datetime.now(UTC)
            )
        assert n == 1
        db_session.refresh(row)
        assert row.status == "sent" and row.gmail_message_id == fake.sent[-1]["id"]
        assert len(fake.sent) == 1  # never sent twice
        stage, _ = status_svc.enquiry_status(db_session, w.org.id, enquiry.id, None)
        assert stage == "replied"

    def test_a_replaced_message_id_is_found_by_subject_and_recipient(
        self, db_session, fake
    ):
        w = mailbox_world(db_session)
        _thread(fake)
        fake.rewrite_message_id = True
        row = self._lost(db_session, w, fake)
        acct = _acct(db_session, w)
        from app.services.functions.mailbox import gmail_client

        with gmail_client.client_for(acct, writable=True) as client:
            outbox.reconcile_account(
                acct, client, sessions(db_session), dt.datetime.now(UTC)
            )
        db_session.refresh(row)
        assert row.status == "sent"

    def test_a_send_that_never_happened_is_failed_after_a_while(self, db_session, fake):
        w = mailbox_world(db_session)
        _thread(fake)
        row = self._lost(db_session, w, fake)
        fake.messages.pop(fake.sent[-1]["id"])  # it never reached the mailbox
        fake.sent.clear()
        acct = _acct(db_session, w)
        from app.services.functions.mailbox import gmail_client

        with gmail_client.client_for(acct, writable=True) as client:
            outbox.reconcile_account(
                acct, client, sessions(db_session), dt.datetime.now(UTC)
            )
        db_session.refresh(row)
        assert row.status == "sending"  # not yet: it may still turn up
        row.created_at = dt.datetime.now(UTC) - dt.timedelta(minutes=30)
        db_session.flush()
        with gmail_client.client_for(acct, writable=True) as client:
            outbox.reconcile_account(
                acct, client, sessions(db_session), dt.datetime.now(UTC)
            )
        db_session.refresh(row)
        assert row.status == "failed" and "safe to try again" in row.error
        again = _reply(db_session, w, key="lost")
        assert again.status == "sent" and len(fake.sent) == 1


def _acct(db, w):
    from app.services.functions.mailbox.mirror import AccountRef

    return AccountRef.of(db.query(FnMailboxAccount).filter_by(id=w.account.id).one())


class TestFacts:
    def test_a_proposal_send_freezes_a_version_and_makes_the_enquiry_quoted(
        self, db_session, fake
    ):
        w = mailbox_world(db_session)
        _thread(fake)
        enquiry = _enquiry(db_session, w, "t-1")
        db_session.add(
            FnProposal(
                organization_id=w.org.id, enquiry_id=enquiry.id, html="<h1>Hi</h1>"
            )
        )
        db_session.flush()
        out = sending.send_enquiry_email(
            db_session,
            w.ctx(),
            enquiry.id,
            mode="proposal",
            body_html="<p>Your proposal: {{quote_link}}</p>",
            idempotency_key="prop-1",
        )
        assert out["status"] == "sent" and "/q/" in out["quote_url"]
        html = fake.sent_message().get_body(("html",)).get_content()
        assert out["quote_url"] in html and "{{quote_link}}" not in html
        version = (
            db_session.query(FnProposalVersion).filter_by(enquiry_id=enquiry.id).one()
        )
        assert version.sent_at is not None and version.id == out["proposal_version_id"]
        fact = db_session.query(FnProposalEvent).filter_by(enquiry_id=enquiry.id).one()
        assert (fact.kind, fact.source, fact.outbox_id) == (
            "sent",
            "outbox",
            out["outbox_id"],
        )
        stage, _ = status_svc.enquiry_status(db_session, w.org.id, enquiry.id, None)
        assert stage == "quoted"

        again = sending.send_enquiry_email(
            db_session,
            w.ctx(),
            enquiry.id,
            mode="proposal",
            body_html="<p>x</p>",
            idempotency_key="prop-1",
        )
        assert again["duplicate"] and len(fake.sent) == 1
        assert (
            db_session.query(FnProposalVersion).filter_by(enquiry_id=enquiry.id).count()
            == 1
        )

    def test_send_to_client_allows_the_emails_and_records_link_sent(
        self, db_session, fake
    ):
        w = mailbox_world(db_session)
        contact = add_contact(db_session, w.org.id, email=CUSTOMER)
        event = events_svc.create_event(
            db_session,
            w.ctx(),
            venue_id=w.venue.id,
            contact_id=contact.id,
            event_date=dt.date.today() + dt.timedelta(days=30),
            booking_type="seated_dining",
        )
        out = sending.send_event_portal_link(
            db_session, w.ctx(), event.id, idempotency_key="portal-1"
        )
        assert out["status"] == "sent" and "/e/" in out["portal_url"]
        msg = fake.sent_message()
        assert msg["To"] == CUSTOMER and fake.sent[-1]["threadId"] is None
        assert out["portal_url"] in msg.get_body(("html",)).get_content()
        allowed = db_session.query(FnPortalAccess).filter_by(event_id=event.id).all()
        assert [a.email for a in allowed] == [CUSTOMER]
        change = (
            db_session.query(FnEventChange)
            .filter_by(event_id=event.id, kind="link_sent")
            .one()
        )
        assert change.changes["outbox_id"] == out["outbox_id"]
        assert (
            events_svc.event_state(db_session, w.ctx(), event.id).status
            == "sent_to_client"
        )
        link = db_session.query(FnThreadLink).filter_by(event_id=event.id).one()
        assert link.gmail_thread_id == out["gmail_thread_id"]

    def test_a_new_email_about_an_enquiry_links_its_thread(self, db_session, fake):
        w = mailbox_world(db_session)
        enquiry = _enquiry(db_session, w)
        out = sending.send_enquiry_email(
            db_session,
            w.ctx(),
            enquiry.id,
            mode="email",
            body_html="Thanks for getting in touch!",
            idempotency_key="new-1",
        )
        assert out["status"] == "sent"
        assert fake.sent_message()["Subject"] == "Wedding"
        link = db_session.query(FnThreadLink).filter_by(enquiry_id=enquiry.id).one()
        assert link.gmail_thread_id == out["gmail_thread_id"]
        stage, _ = status_svc.enquiry_status(db_session, w.org.id, enquiry.id, None)
        assert stage == "replied"


class TestPricing:
    def test_a_price_without_a_proposal_warns_and_an_override_is_recorded(
        self, db_session, fake, caplog
    ):
        w = mailbox_world(db_session)
        _thread(fake)
        enquiry = _enquiry(db_session, w, "t-1")
        args = dict(
            mode="reply",
            body_html="<p>The deck is $2,500 minimum spend.</p>",
            gmail_thread_id="t-1",
            idempotency_key="price-1",
        )
        with pytest.raises(pricing.PricingWarning):
            inbox.compose_send(db_session, w.ctx(), w.account.id, **args)
        assert fake.sent == []
        with caplog.at_level(logging.WARNING):
            result = inbox.compose_send(
                db_session,
                w.ctx(),
                w.account.id,
                price_override_reason="Customer asked for a ballpark on the phone",
                **args,
            )
        assert result.status == "sent"
        assert any("pricing_override" in r.message for r in caplog.records)
        row = db_session.query(FnOutbox).filter_by(idempotency_key="price-1").one()
        if hasattr(FnOutbox, "price_override_reason"):
            assert row.price_override_reason.startswith("Customer asked")
        assert row.enquiry_id is None or row.enquiry_id == enquiry.id

    def test_no_warning_once_quoted_or_without_a_price(self, db_session, fake):
        w = mailbox_world(db_session)
        enquiry = _enquiry(db_session, w)
        assert (
            pricing.check(db_session, w.ctx(), enquiry.id, "<p>See you then!</p>", None)
            is None
        )
        db_session.add(
            FnProposalEvent(
                organization_id=w.org.id,
                enquiry_id=enquiry.id,
                kind="sent",
                source="import",
            )
        )
        db_session.flush()
        assert (
            pricing.check(db_session, w.ctx(), enquiry.id, "It's $40 pp", None) is None
        )

    def test_price_patterns(self):
        assert pricing.contains_price("That would be $1,200")
        assert pricing.contains_price("NZD 900 all in")
        assert pricing.contains_price("45 per head")
        assert pricing.contains_price("minimum spend of 3000")
        assert not pricing.contains_price("We have space for 120 people on the 3rd")


def test_the_outbox_row_exists_before_gmail_is_called(db_session, fake):
    """The row (and its Message-ID) is committed first: when Gmail is asked,
    the send is already on record."""
    w = mailbox_world(db_session)
    _thread(fake)
    seen = {}

    def check(request):
        if request.url.path.endswith("/messages/send"):
            seen["rows"] = [
                (r.status, r.rfc822_message_id)
                for r in db_session.query(FnOutbox).filter_by(idempotency_key="first")
            ]

    fake.on_request = check
    result = _reply(db_session, w, key="first")
    assert seen["rows"] == [("sending", result.rfc822_message_id)]


def test_cross_org_account_is_not_found(db_session, fake):
    from tests.functions_mailbox_fakes import other_mailbox

    w = mailbox_world(db_session)
    rival = other_mailbox(db_session, w)
    from app.services.functions.errors import NotFound

    with pytest.raises(NotFound):
        outbox.send(
            db_session,
            w.ctx(),
            rival.account.id,
            to=[CUSTOMER],
            subject="x",
            body_html="x",
            purpose="compose",
            idempotency_key="xorg",
        )
    db_session.execute(
        update(FnOutbox).where(FnOutbox.idempotency_key == "never").values(error=None)
    )
    assert fake.sent == []
