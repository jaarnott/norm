"""The second security review's findings on the mailbox, held as tests:

* **Settings → Venues can't swap a Functions mailbox's grant.** The generic
  ``/oauth/callback`` for ``venue_mailbox`` stores onto the very connection
  a Functions mailbox runs on; it now runs the mailbox checks first (nothing
  stored for an address another organisation holds, or that would replace
  the live Functions mailbox) while Hiring's calendar-only connects keep
  working. And at use time the grant must still sign in as the mailbox: the
  sync marks a mismatch ``needs_reconnect``, sends refuse.
* **Reply-To never decides whose message it was**: an inbound message with
  ``Reply-To: <our mailbox>`` is answered to its sender, not to its To list.
* **An idempotency key only resumes a send on the same mailbox/record** a
  caller can see — never another venue's.
* **One strict address rule** (``functions/addresses.py``, shared with
  contacts): no RFC 5322 specials, which the mail library would write
  unquoted.
"""

from __future__ import annotations

import datetime as dt
import uuid
from unittest.mock import MagicMock

import pytest

from app.auth.security import create_access_token
from app.db.functions_models import FnMailboxAccount, FnOutbox, FnProposal
from app.db.models import Connection, OAuthState, OrganizationMembership, Role
from app.services.functions import addresses
from app.services.functions.errors import Conflict
from app.services.functions.mailbox import accounts as accounts_svc
from app.services.functions.mailbox import inbox, mime, outbox, sending, sync
from tests.functions_mailbox_fakes import (
    MAILBOX,
    FakeGmail,
    install,
    mailbox_world,
    other_mailbox,
    sessions,
)
from tests.functions_world import add_contact, add_enquiry
from tests.test_functions_mailbox_inbox import _spec

UTC = dt.timezone.utc
CUSTOMER = "casey@clientco.nz"
RIVAL = "hello@rivalbar.co.nz"


@pytest.fixture()
def fake(monkeypatch):
    fake = FakeGmail()
    install(monkeypatch, fake)
    return fake


def _grant_connectors(db, w, user):
    role = Role(
        id=str(uuid.uuid4()),
        organization_id=w.org.id,
        name=f"conn-{uuid.uuid4().hex[:6]}",
        display_name="Connector",
        permissions=["settings:connectors"],
    )
    db.add(role)
    db.flush()
    db.query(OrganizationMembership).filter_by(user_id=user.id).one().role_id = role.id
    db.flush()


# ---------------------------------------------------------------------------
# 1. The generic connect route, and the grant checked at use time
# ---------------------------------------------------------------------------


class TestSettingsConnect:
    def _callback(self, client, db, monkeypatch, *, venue, user):
        _spec(db)
        db.add(
            OAuthState(
                connector_name="venue_mailbox",
                state="st-settings",
                venue_id=venue.id,
                user_id=user.id,
            )
        )
        db.flush()
        resp = MagicMock(status_code=200)
        resp.json.return_value = {
            "access_token": "fresh-acc",
            "refresh_token": "fresh-ref",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/userinfo.email "
            "https://www.googleapis.com/auth/calendar.events",
        }
        monkeypatch.setattr(
            "app.services.oauth_service.httpx.post", lambda *a, **k: resp
        )
        return client.get("/api/oauth/callback?code=c&state=st-settings")

    def _grant(self, db, conn_id):
        db.expire_all()
        conn = db.get(Connection, conn_id)
        return conn.access_token, conn.refresh_token

    def test_another_organisations_mailbox_cannot_be_put_under_ours(
        self, client, db_session, fake, monkeypatch
    ):
        """The review's case: Settings → Venues on Brew Bar, completed by the
        owner of Rival Co's Functions mailbox."""
        w = mailbox_world(db_session)
        other_mailbox(db_session, w)
        fake.userinfo_email = RIVAL
        r = self._callback(client, db_session, monkeypatch, venue=w.venue, user=w.staff)
        assert r.status_code == 400 and "another organisation" in r.text
        assert self._grant(db_session, w.conn.id) == ("tok", "ref")

    def test_a_live_functions_mailbox_cannot_be_swapped_from_settings(
        self, client, db_session, fake, monkeypatch
    ):
        w = mailbox_world(db_session)
        fake.userinfo_email = "someone.else@gmail.com"
        r = self._callback(client, db_session, monkeypatch, venue=w.venue, user=w.staff)
        assert r.status_code == 400 and MAILBOX in r.text
        assert self._grant(db_session, w.conn.id) == ("tok", "ref")

    def test_reconnecting_the_same_mailbox_from_settings_heals_it(
        self, client, db_session, fake, monkeypatch
    ):
        w = mailbox_world(db_session)
        r = self._callback(client, db_session, monkeypatch, venue=w.venue, user=w.staff)
        assert r.status_code == 200, r.text
        assert self._grant(db_session, w.conn.id) == ("fresh-acc", "fresh-ref")

    def test_hirings_calendar_connect_still_works(
        self, client, db_session, fake, monkeypatch
    ):
        w = mailbox_world(db_session)
        fake.userinfo_email = "garden.manager@gmail.com"
        r = self._callback(
            client, db_session, monkeypatch, venue=w.venue2, user=w.owner
        )
        assert r.status_code == 200, r.text
        db_session.expire_all()
        conn = (
            db_session.query(Connection)
            .filter_by(connector_name="venue_mailbox", venue_id=w.venue2.id)
            .one()
        )
        assert conn.refresh_token == "fresh-ref"
        assert conn.oauth_metadata["email"] == "garden.manager@gmail.com"
        assert conn.oauth_metadata["connected_by_user_id"] == w.owner.id

    def test_the_settings_authorize_records_who_started_it(
        self, client, db_session, fake
    ):
        w = mailbox_world(db_session)
        _spec(db_session)
        _grant_connectors(db_session, w, w.staff)
        r = client.get(
            f"/api/oauth/authorize/venue_mailbox?venue_id={w.venue.id}",
            headers={
                "Authorization": f"Bearer {create_access_token({'sub': w.staff.id})}"
            },
        )
        assert r.status_code == 200, r.text
        state = r.json()["authorize_url"].split("state=")[1].split("&")[0]
        row = db_session.query(OAuthState).filter_by(state=state).one()
        assert (row.venue_id, row.user_id) == (w.venue.id, w.staff.id)


class TestGrantIdentityAtUseTime:
    def test_a_swapped_grant_is_caught_by_the_sync(self, db_session, fake):
        """However a grant for another account lands on the connection, the
        next tick notices and stops (needs_reconnect, venue told once)."""
        w = mailbox_world(db_session)
        fake.address = RIVAL  # the connection now signs in as Rival Co's mailbox
        out = sync.sync_account(w.account.id, session_factory=sessions(db_session))
        assert out["needs_reconnect"] and RIVAL in out["error"]
        db_session.expire_all()
        acct = db_session.query(FnMailboxAccount).filter_by(id=w.account.id).one()
        assert (
            acct.status == "needs_reconnect" and acct.reconnect_notified_at is not None
        )

    def test_sends_refuse_on_a_swapped_grant(self, db_session, fake):
        w = mailbox_world(db_session)
        fake.address = RIVAL
        with pytest.raises(accounts_svc.MailboxUnavailable):
            outbox.send(
                db_session,
                w.ctx(),
                w.account.id,
                to=[CUSTOMER],
                subject="Hi",
                body_html="Hello",
                purpose="compose",
                idempotency_key="swap-1",
            )
        assert fake.sent == []


# ---------------------------------------------------------------------------
# 2. Reply-To
# ---------------------------------------------------------------------------


def _target(headers: dict, labels=("INBOX",)) -> dict:
    return {
        "id": "m1",
        "labelIds": list(labels),
        "payload": {"headers": [{"name": k, "value": v} for k, v in headers.items()]},
    }


class TestReplyRecipients:
    def test_reply_to_naming_our_mailbox_does_not_make_it_ours(self):
        to, others = outbox.reply_recipients(
            _target(
                {
                    "From": "Someone <someone@outside.test>",
                    "Reply-To": MAILBOX,
                    "To": f"{MAILBOX}, third1@elsewhere.test, third2@elsewhere.test",
                }
            ),
            MAILBOX,
        )
        assert to == ["someone@outside.test"]
        assert others == ["third1@elsewhere.test", "third2@elsewhere.test"]

    def test_a_real_reply_to_is_honoured_and_our_own_mail_goes_to_its_to(self):
        to, _ = outbox.reply_recipients(
            _target({"From": f"Form <{MAILBOX}>", "Reply-To": CUSTOMER, "To": MAILBOX}),
            MAILBOX,
        )
        assert to == [CUSTOMER]  # a form notification the mailbox sent itself
        to, _ = outbox.reply_recipients(
            _target(
                {"From": "Casey <casey@clientco.nz>", "Reply-To": "bookings@agency.nz"}
            ),
            MAILBOX,
        )
        assert to == ["bookings@agency.nz"]
        to, _ = outbox.reply_recipients(
            _target(
                {"From": MAILBOX, "To": f"{CUSTOMER}, {MAILBOX}"}, labels=("SENT",)
            ),
            MAILBOX,
        )
        assert to == [CUSTOMER]

    def test_derived_addresses_are_cleaned(self):
        """An unusable Reply-To falls back to From; junk and the mailbox
        itself never reach a recipient list."""
        to, others = outbox.reply_recipients(
            _target(
                {
                    "From": f"Casey <{CUSTOMER}>",
                    "Reply-To": "also bad",
                    "To": MAILBOX.upper(),
                    "Cc": f"ok@fine.nz, not-an-address, {MAILBOX}, {CUSTOMER}",
                }
            ),
            MAILBOX,
        )
        assert to == [CUSTOMER] and others == ["ok@fine.nz"]

    def test_compose_and_prepare_follow_the_sender(self, db_session, fake):
        w = mailbox_world(db_session)
        fake.add_message(
            frm="Someone <someone@outside.test>",
            to=[MAILBOX, "third@elsewhere.test"],
            reply_to=MAILBOX,
            thread_id="t-rt",
        )
        prepared = inbox.prepare_compose(
            db_session, w.ctx(), w.account.id, mode="reply_all", gmail_thread_id="t-rt"
        )
        assert prepared["to"] == ["someone@outside.test"]
        assert prepared["cc"] == ["third@elsewhere.test"]
        outbox.send(
            db_session,
            w.ctx(),
            w.account.id,
            to=[],
            subject=None,
            body_html="Thanks!",
            thread_id="t-rt",
            purpose="reply",
            idempotency_key="rt-1",
        )
        assert fake.sent_message()["To"] == "someone@outside.test"


# ---------------------------------------------------------------------------
# 3. Idempotency keys
# ---------------------------------------------------------------------------


def _garden(db, w):
    conn = Connection(
        connector_name="venue_mailbox",
        venue_id=w.venue2.id,
        config={},
        enabled="true",
        access_token="tokg",
        refresh_token="refg",
        token_expires_at=dt.datetime.now(UTC) + dt.timedelta(hours=1),
    )
    db.add(conn)
    db.flush()
    acct = FnMailboxAccount(
        organization_id=w.org.id,
        venue_id=w.venue2.id,
        google_email="events@gardenbar.co.nz",
        connection_id=conn.id,
        status="active",
        history_id=1000,
        last_ok_at=dt.datetime.now(UTC),
    )
    db.add(acct)
    db.flush()
    return acct


def _row(db, w, acct, status, **kw):
    row = FnOutbox(
        organization_id=w.org.id,
        idempotency_key="garden-key",
        rfc822_message_id=f"<{uuid.uuid4().hex}@gardenbar.co.nz>",
        account_id=acct.id,
        to_emails=["vip@example.com"],
        subject="Private: Garden Bar booking",
        body_html="<p>Garden Bar only</p>",
        status=status,
        purpose="compose",
        attempts=1,
        gmail_message_id="gm-garden" if status == "sent" else None,
        gmail_thread_id="gt-garden" if status == "sent" else None,
        **kw,
    )
    db.add(row)
    db.flush()
    return row


class TestIdempotencyScope:
    def _send(self, db, ctx, account_id):
        return outbox.send(
            db,
            ctx,
            account_id,
            to=["x@example.com"],
            subject="hi",
            body_html="hello",
            purpose="compose",
            idempotency_key="garden-key",
        )

    @pytest.mark.parametrize("status", ["failed", "sent", "sending"])
    def test_another_venues_send_is_neither_resent_nor_reported(
        self, db_session, fake, status
    ):
        """The review's case: Brew Bar staff reuse Garden Bar's key."""
        w = mailbox_world(db_session)
        row = _row(db_session, w, _garden(db_session, w), status)
        with pytest.raises(Conflict) as exc:
            self._send(db_session, w.ctx(w.staff), w.account.id)
        assert "Garden" not in exc.value.message and "vip@" not in exc.value.message
        db_session.refresh(row)
        assert (row.status, row.attempts) == (status, 1)
        assert fake.sent == []

    def test_a_key_is_bound_to_its_mailbox_even_for_someone_who_sees_both(
        self, db_session, fake
    ):
        w = mailbox_world(db_session)
        _row(db_session, w, _garden(db_session, w), "sent")
        with pytest.raises(Conflict):
            self._send(db_session, w.ctx(w.owner), w.account.id)

    def test_a_key_is_bound_to_its_enquiry(self, db_session, fake):
        w = mailbox_world(db_session)
        contact = add_contact(db_session, w.org.id, email=CUSTOMER)
        first = add_enquiry(db_session, w.org.id, contact.id, w.venue.id, subject="A")
        second = add_enquiry(db_session, w.org.id, contact.id, w.venue.id, subject="B")
        db_session.add(
            FnProposal(organization_id=w.org.id, enquiry_id=first.id, html="<p>P</p>")
        )
        db_session.flush()
        sending.send_enquiry_email(
            db_session,
            w.ctx(),
            first.id,
            mode="email",
            body_html="Hi",
            idempotency_key="k-enq",
        )
        with pytest.raises(Conflict):
            sending.send_enquiry_email(
                db_session,
                w.ctx(),
                second.id,
                mode="email",
                body_html="Hi",
                idempotency_key="k-enq",
            )
        assert len(fake.sent) == 1


# ---------------------------------------------------------------------------
# 4. One strict address rule
# ---------------------------------------------------------------------------


class TestStrictEmail:
    @pytest.mark.parametrize(
        "value",
        [
            "a@b(c).com",  # RFC 5322 comment: written unquoted, read as a@b.com
            "a@b:c.com",
            "a@b;c.com",
            "a@b,c.com",
            "a@b<c>.com",
            "a@[127.0.0.1]",
            '"a b"@c.com',
            "a b@c.com",
            "a..b@c.com",
            ".a@c.com",
            "a.@c.com",
            "a@-b.com",
            "a@b-.com",
            "a@b",
            "a@b..com",
            "a@b_c.com",
            "a@b.com\r\nBcc: x@y.com",
            "a@b.com.",
            "ä@b.com",
            "",
            None,
            "a@" + "b" * 64 + ".com",  # a 64-character label
            "a" * 250 + "@b.co",  # longer than 254
            "a" * 65 + "@b.co",  # local part over 64
            "a@10.0.0.1",  # all-digit last label
        ],
    )
    def test_refused(self, value):
        assert not addresses.is_email(value)
        assert not mime.is_email(value)

    @pytest.mark.parametrize(
        "value",
        [
            "casey@clientco.nz",
            "first.last+events@sub.example.co.nz",
            "o'brien@pub.ie",
            "a_b-c@x-y.io",
            "1@2.nz",
            "Casey@ClientCo.NZ",
            " casey@clientco.nz ",
        ],
    )
    def test_accepted(self, value):
        assert addresses.is_email(value) and mime.is_email(value)

    def test_linear_on_crafted_input(self):
        import time

        start = time.perf_counter()
        for value in ("a" * 50_000 + "@", "a." * 25_000 + "@b", "a@" + "b-" * 25_000):
            assert not addresses.is_email(value)
        assert time.perf_counter() - start < 0.5
