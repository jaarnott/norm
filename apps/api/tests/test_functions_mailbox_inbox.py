"""The Inbox API (routers/functions_inbox.py) through HTTP, against a fake
Gmail.

* **Tenancy on every endpoint.** Another organisation's user gets 404 for
  every mailbox, thread, link, draft, send and record id of this one, and
  never sees it in a list; staff see only their venues' mailboxes.
* **The gates.** Functions off → 404 everywhere; shadow mode reads (lists,
  threads, live search, "sync now") and refuses every write with 409
  ``not_live``.
* **What the screen does**: thread lists with folder / linked / search
  filters, live sanitised bodies, filing mirrored to the Actioned labels,
  link / unlink / enquiry-from-thread, composer defaults (reply all), sends
  (200 sent, 202 still sending, 409 pricing warning, override), drafts and
  the hand-over, proposal and portal-link sends.
* **Connecting a mailbox**: a clear refusal without a Google client; the
  consent URL; the callback attaching the importer's placeholder by address
  (signature cleaned, labels mapped to folders), refusing a mailbox that
  belongs to another venue (and putting the venue's grant back), and a grant
  without Gmail.
"""

from __future__ import annotations

import datetime as dt
import uuid
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest

from app.auth.security import create_access_token
from app.db.config_models import ConnectionSpec
from app.db.functions_models import (
    FnEmailDraft,
    FnMailboxAccount,
    FnMailboxLabel,
    FnOutbox,
    FnProposal,
    FnThreadFolder,
    FnThreadLink,
)
from app.db.models import Connection, OAuthState, Role
from app.services.functions import events as events_svc
from app.services.functions.mailbox import sync
from tests.functions_mailbox_fakes import (
    MAILBOX,
    FakeGmail,
    install,
    mailbox_world,
    sessions,
)
from tests.functions_world import add_contact, add_enquiry, make_world

UTC = dt.timezone.utc
CUSTOMER = "casey@clientco.nz"


def H(user) -> dict:
    return {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}


def call(client, method, path, user, **kw):
    return client.request(method, f"/api/functions{path}", headers=H(user), **kw)


@pytest.fixture()
def fake(monkeypatch):
    fake = FakeGmail()
    install(monkeypatch, fake)
    return fake


def _seed(db, w, fake):
    """Two threads in the mirror: Casey's enquiry (with a script in its
    body) and a supplier's note."""
    fake.add_message(
        frm=f"Casey Client <{CUSTOMER}>",
        to=[MAILBOX, "partner@clientco.nz"],
        cc=["mum@clientco.nz"],
        subject="Wedding in March",
        body="Can we book the deck?",
        html='<p>Can we book the deck?</p><script>steal()</script><img src="x" onerror="y()">',
        thread_id="t-casey",
        message_id="<casey@clientco.nz>",
        when=dt.datetime.now(UTC) - dt.timedelta(hours=1),
    )
    fake.add_message(
        frm="Fresh Produce <orders@produce.co.nz>",
        to=[MAILBOX],
        subject="Invoice 42",
        thread_id="t-supplier",
        when=dt.datetime.now(UTC) - dt.timedelta(hours=3),
    )
    out = sync.sync_account(w.account.id, session_factory=sessions(db))
    assert out.get("ok"), out
    db.expire_all()


@pytest.fixture()
def w(db_session, fake):
    w = mailbox_world(db_session)
    _seed(db_session, w, fake)
    return w


class TestMailboxes:
    def test_list_shows_venues_and_health(self, client, w):
        r = call(client, "GET", "/inbox/mailboxes", w.owner)
        assert r.status_code == 200
        body = r.json()
        names = {v["venue_name"] for v in body["venues"]}
        assert {"Brew Bar", "Garden Bar"} <= names
        (mailbox,) = [m for m in body["mailboxes"] if m["id"] == w.account.id]
        assert mailbox["health"]["state"] == "connected"
        assert mailbox["folders"] == {
            "enquiry": True,
            "reservation": True,
            "other": True,
        }
        staff = call(client, "GET", "/inbox/mailboxes", w.staff).json()
        assert {v["venue_name"] for v in staff["venues"]} == {"Brew Bar"}

    def test_sync_now_is_allowed_in_shadow_and_skips_fresh_mailboxes(
        self, client, db_session, w
    ):
        w.org.functions_mode = "shadow"
        db_session.flush()
        r = call(client, "POST", "/inbox/sync", w.staff, json={})
        assert r.status_code == 200
        assert r.json()["results"][0]["skipped"] == "fresh"

    def test_sync_now_runs_a_stale_mailbox_and_never_waits_for_a_busy_one(
        self, client, db_session, w, fake
    ):
        w.account.last_ok_at = dt.datetime.now(UTC) - dt.timedelta(minutes=5)
        db_session.flush()
        fake.add_message(frm=f"Casey <{CUSTOMER}>", to=[MAILBOX], subject="Later")
        r = call(client, "POST", f"/inbox/mailboxes/{w.account.id}/sync", w.staff)
        assert r.status_code == 200 and r.json()["results"][0]["ok"] is True
        assert r.json()["results"][0]["messages"] == 1

        db_session.expire_all()
        acct = db_session.query(FnMailboxAccount).filter_by(id=w.account.id).one()
        acct.last_ok_at = dt.datetime.now(UTC) - dt.timedelta(minutes=5)
        acct.lease_owner = "scheduler"
        acct.lease_until = dt.datetime.now(UTC) + dt.timedelta(minutes=1)
        db_session.flush()
        r = call(client, "POST", f"/inbox/mailboxes/{w.account.id}/sync", w.staff)
        assert r.json()["results"][0]["skipped"] == "busy"


class TestThreads:
    def test_list_filters_and_search(self, client, w):
        r = call(client, "GET", "/inbox/threads", w.staff)
        assert r.status_code == 200
        threads = r.json()["threads"]
        assert [t["gmail_thread_id"] for t in threads] == ["t-casey", "t-supplier"]
        casey = threads[0]
        assert casey["awaiting_reply"] is True and casey["folder"] is None
        assert casey["unread_count"] == 1 and casey["linked"] is False
        found = call(client, "GET", "/inbox/threads?q=produce", w.staff).json()[
            "threads"
        ]
        assert [t["gmail_thread_id"] for t in found] == ["t-supplier"]
        found = call(client, "GET", "/inbox/threads?q=partner@clientco", w.staff).json()
        assert [t["gmail_thread_id"] for t in found["threads"]] == ["t-casey"]
        assert call(client, "GET", "/inbox/threads?folder=unfiled", w.staff).json()[
            "threads"
        ]
        assert (
            call(client, "GET", "/inbox/threads?folder=enquiry", w.staff).json()[
                "threads"
            ]
            == []
        )

    def test_a_thread_comes_with_live_sanitised_bodies(self, client, w):
        r = call(client, "GET", f"/inbox/threads/{w.account.id}/t-casey", w.staff)
        assert r.status_code == 200
        body = r.json()
        assert body["bodies_available"] is True
        (msg,) = body["messages"]
        assert "Can we book the deck?" in msg["html"]
        assert "script" not in msg["html"] and "onerror" not in msg["html"]
        assert msg["is_inbound"] is True and msg["text"]

    def test_filing_is_mirrored_to_the_actioned_label(
        self, client, db_session, w, fake
    ):
        r = call(
            client,
            "PUT",
            f"/inbox/threads/{w.account.id}/t-casey/folder",
            w.staff,
            json={"folder": "enquiry"},
        )
        assert r.status_code == 200 and r.json()["gmail_synced"] is True
        (modify,) = fake.calls("POST", "/threads/t-casey/modify")
        import json

        sent = json.loads(modify.content)
        assert sent["addLabelIds"] == ["Label_enq"]
        assert sorted(sent["removeLabelIds"]) == ["Label_oth", "Label_res"]
        listed = call(client, "GET", "/inbox/threads?folder=enquiry", w.staff).json()
        assert [t["gmail_thread_id"] for t in listed["threads"]] == ["t-casey"]
        row = (
            db_session.query(FnThreadFolder).filter_by(gmail_thread_id="t-casey").one()
        )
        assert row.gmail_synced_at is not None and row.set_by_user_id == w.staff.id

    def test_link_unlink_and_enquiry_from_thread(self, client, db_session, w):
        contact = add_contact(db_session, w.org.id, name="Casey", email=CUSTOMER)
        enquiry = add_enquiry(db_session, w.org.id, contact.id, w.venue.id)
        r = call(
            client,
            "POST",
            f"/inbox/threads/{w.account.id}/t-supplier/links",
            w.staff,
            json={"enquiry_id": enquiry.id},
        )
        assert r.status_code == 201, r.text
        link_id = r.json()["link_id"]
        linked = call(client, "GET", "/inbox/threads?linked=true", w.staff).json()
        assert [t["gmail_thread_id"] for t in linked["threads"]] == ["t-supplier"]
        assert (
            call(client, "DELETE", f"/inbox/links/{link_id}", w.staff).status_code
            == 200
        )

        r = call(
            client,
            "POST",
            f"/inbox/threads/{w.account.id}/t-casey/enquiry",
            w.staff,
            json={"booking_type": "large_event_exclusive"},
        )
        assert r.status_code == 201, r.text
        out = r.json()
        assert out["contact_id"] == contact.id  # matched by the sender's email
        link = db_session.query(FnThreadLink).filter_by(id=out["link_id"]).one()
        assert (link.enquiry_id, link.gmail_thread_id) == (out["enquiry_id"], "t-casey")

    def test_live_search_finds_older_mail(self, client, w, fake):
        old = fake.add_message(
            frm="Old Friend <old@friend.nz>",
            to=[MAILBOX],
            subject="Last year",
            thread_id="t-old",
            record=False,
        )
        assert old
        r = call(
            client, "GET", f"/inbox/search?account_id={w.account.id}&q=last", w.staff
        )
        assert r.status_code == 200
        by_id = {t["gmail_thread_id"]: t for t in r.json()["threads"]}
        assert by_id["t-old"]["mirrored"] is False
        assert by_id["t-casey"]["mirrored"] is True


class TestComposer:
    def test_reply_all_defaults(self, client, w):
        r = call(
            client,
            "POST",
            "/inbox/compose/prepare",
            w.staff,
            json={
                "account_id": w.account.id,
                "mode": "reply_all",
                "gmail_thread_id": "t-casey",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["to"] == [CUSTOMER]
        assert body["cc"] == ["partner@clientco.nz", "mum@clientco.nz"]
        assert body["subject"] == "Re: Wedding in March"
        assert "Can we book the deck?" in body["quoted_html"]

    def test_send_pricing_warning_and_override(self, client, db_session, w, fake):
        contact = add_contact(db_session, w.org.id, email=CUSTOMER)
        enquiry = add_enquiry(db_session, w.org.id, contact.id, w.venue.id)
        db_session.add(
            FnThreadLink(
                organization_id=w.org.id,
                account_id=w.account.id,
                gmail_thread_id="t-casey",
                enquiry_id=enquiry.id,
            )
        )
        db_session.flush()
        body = {
            "account_id": w.account.id,
            "mode": "reply",
            "gmail_thread_id": "t-casey",
            "body_html": "<p>The deck is $3,000 minimum spend.</p>",
            "idempotency_key": str(uuid.uuid4()),
        }
        r = call(client, "POST", "/inbox/compose/send", w.staff, json=body)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "pricing_warning"
        r = call(
            client,
            "POST",
            "/inbox/compose/send",
            w.staff,
            json={**body, "price_override_reason": "They asked for a rough figure"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "sent" and len(fake.sent) == 1
        status = call(client, "GET", f"/inbox/outbox/{r.json()['outbox_id']}", w.staff)
        assert status.status_code == 200 and status.json()["status"] == "sent"

    def test_a_lost_gmail_answer_is_202_sending(self, client, w, fake):
        fake.lose_send_response = True
        r = call(
            client,
            "POST",
            "/inbox/compose/send",
            w.staff,
            json={
                "account_id": w.account.id,
                "mode": "reply",
                "gmail_thread_id": "t-casey",
                "body_html": "On it!",
                "idempotency_key": "lost-1",
            },
        )
        assert r.status_code == 202 and r.json()["status"] == "sending"

    def test_drafts_over_http_including_the_hand_over(
        self, client, db_session, w, fake
    ):
        r = call(
            client,
            "POST",
            "/inbox/drafts",
            w.staff,
            json={
                "account_id": w.account.id,
                "gmail_thread_id": "t-casey",
                "to": [CUSTOMER],
                "subject": "Re: Wedding in March",
                "body_html": "Draft one",
            },
        )
        assert r.status_code == 201, r.text
        draft = r.json()
        r = call(
            client,
            "PATCH",
            f"/inbox/drafts/{draft['id']}",
            w.staff,
            json={"expected_version": draft["version"], "body_html": "Draft two"},
        )
        assert r.status_code == 200 and "Draft two" in r.json()["body_html"]
        stale = call(
            client,
            "PATCH",
            f"/inbox/drafts/{draft['id']}",
            w.staff,
            json={"expected_version": draft["version"], "body_html": "x"},
        )
        assert stale.status_code == 409
        r = call(
            client,
            "POST",
            f"/inbox/drafts/{draft['id']}/send-to-gmail",
            w.staff,
            json={},
        )
        assert (
            r.status_code == 200 and r.json()["owner"] == "gmail" and r.json()["locked"]
        )
        r = call(client, "POST", f"/inbox/drafts/{draft['id']}/bring-back", w.staff)
        assert r.status_code == 200 and r.json()["owner"] == "norm"
        listed = call(
            client, "GET", f"/inbox/drafts?account_id={w.account.id}", w.staff
        )
        assert [d["id"] for d in listed.json()["drafts"]] == [draft["id"]]
        r = call(
            client,
            "POST",
            f"/inbox/drafts/{draft['id']}/send",
            w.staff,
            json={"idempotency_key": "draft-send-1"},
        )
        assert r.status_code == 200 and r.json()["status"] == "sent"
        assert (
            db_session.query(FnEmailDraft).filter_by(id=draft["id"]).one().status
            == "sent"
        )

    def test_proposal_and_portal_sends(self, client, db_session, w, fake):
        contact = add_contact(db_session, w.org.id, email=CUSTOMER)
        enquiry = add_enquiry(
            db_session, w.org.id, contact.id, w.venue.id, subject="Wedding"
        )
        db_session.add(
            FnProposal(organization_id=w.org.id, enquiry_id=enquiry.id, html="<p>P</p>")
        )
        db_session.flush()
        r = call(
            client,
            "POST",
            f"/proposals/{enquiry.id}/send",
            w.staff,
            json={
                "mode": "proposal",
                "body_html": "Here it is",
                "idempotency_key": "p-1",
            },
        )
        assert r.status_code == 200, r.text
        assert (
            r.json()["quote_url"]
            in fake.sent_message().get_body(("html",)).get_content()
        )

        event = events_svc.create_event(
            db_session,
            w.ctx(),
            venue_id=w.venue.id,
            contact_id=contact.id,
            event_date=dt.date.today() + dt.timedelta(days=20),
            booking_type="seated_dining",
        )
        r = call(
            client,
            "POST",
            f"/events/{event.id}/send-to-client",
            w.staff,
            json={"to": [CUSTOMER], "idempotency_key": "portal-1"},
        )
        assert r.status_code == 200, r.text
        assert "/e/" in r.json()["portal_url"]


class TestGates:
    def test_shadow_mode_reads_but_refuses_writes(self, client, db_session, w):
        w.org.functions_mode = "shadow"
        db_session.flush()
        assert call(client, "GET", "/inbox/threads", w.staff).status_code == 200
        assert (
            call(
                client, "GET", f"/inbox/threads/{w.account.id}/t-casey", w.staff
            ).status_code
            == 200
        )
        writes = [
            (
                "PUT",
                f"/inbox/threads/{w.account.id}/t-casey/folder",
                {"folder": "other"},
            ),
            ("POST", f"/inbox/threads/{w.account.id}/t-casey/enquiry", {}),
            (
                "POST",
                "/inbox/compose/send",
                {
                    "account_id": w.account.id,
                    "mode": "reply",
                    "gmail_thread_id": "t-casey",
                    "body_html": "x",
                    "idempotency_key": "s-1",
                },
            ),
            ("POST", "/inbox/drafts", {"account_id": w.account.id, "body_html": "x"}),
        ]
        for method, path, body in writes:
            r = call(client, method, path, w.staff, json=body)
            assert r.status_code == 409, (path, r.text)
            assert r.json()["detail"]["code"] == "not_live"

    def test_functions_off_is_404_everywhere(self, client, db_session, w):
        w.org.functions_mode = "off"
        db_session.flush()
        for method, path in [
            ("GET", "/inbox/mailboxes"),
            ("GET", "/inbox/threads"),
            ("GET", f"/inbox/threads/{w.account.id}/t-casey"),
            ("POST", "/inbox/sync"),
        ]:
            assert call(client, method, path, w.staff).status_code == 404


class TestTenancy:
    def test_another_organisation_gets_404_for_every_id(self, client, db_session, w):
        contact = add_contact(db_session, w.org.id, email=CUSTOMER)
        enquiry = add_enquiry(db_session, w.org.id, contact.id, w.venue.id)
        event = events_svc.create_event(
            db_session,
            w.ctx(),
            venue_id=w.venue.id,
            contact_id=contact.id,
            event_date=dt.date.today() + dt.timedelta(days=20),
            booking_type="seated_dining",
        )
        link = FnThreadLink(
            organization_id=w.org.id,
            account_id=w.account.id,
            gmail_thread_id="t-casey",
            enquiry_id=enquiry.id,
        )
        draft = FnEmailDraft(
            organization_id=w.org.id, account_id=w.account.id, subject="x"
        )
        sent = FnOutbox(
            organization_id=w.org.id,
            idempotency_key="mine",
            rfc822_message_id=f"<{uuid.uuid4().hex}@x>",
            account_id=w.account.id,
            status="sent",
            purpose="reply",
        )
        db_session.add_all([link, draft, sent])
        db_session.flush()
        rival = w.other_user
        a, t = w.account.id, "t-casey"
        attempts = [
            ("GET", f"/inbox/threads/{a}/{t}", None),
            ("PUT", f"/inbox/threads/{a}/{t}/folder", {"folder": "other"}),
            ("POST", f"/inbox/threads/{a}/{t}/links", {"enquiry_id": enquiry.id}),
            ("POST", f"/inbox/threads/{a}/{t}/enquiry", {}),
            ("DELETE", f"/inbox/links/{link.id}", None),
            ("GET", f"/inbox/search?account_id={a}&q=x", None),
            ("POST", f"/inbox/mailboxes/{a}/sync", None),
            (
                "POST",
                "/inbox/compose/prepare",
                {"account_id": a, "mode": "reply", "gmail_thread_id": t},
            ),
            (
                "POST",
                "/inbox/compose/send",
                {
                    "account_id": a,
                    "mode": "new",
                    "to": ["x@y.nz"],
                    "subject": "x",
                    "body_html": "x",
                    "idempotency_key": "r",
                },
            ),
            ("GET", f"/inbox/outbox/{sent.id}", None),
            ("GET", f"/inbox/drafts/{draft.id}", None),
            ("PATCH", f"/inbox/drafts/{draft.id}", {"subject": "x"}),
            ("DELETE", f"/inbox/drafts/{draft.id}", None),
            ("POST", f"/inbox/drafts/{draft.id}/send", {"idempotency_key": "r2"}),
            ("POST", f"/inbox/drafts/{draft.id}/send-to-gmail", {}),
            ("POST", f"/inbox/drafts/{draft.id}/bring-back", None),
            ("GET", f"/inbox/drafts?account_id={a}", None),
            ("GET", f"/inbox/drafts?enquiry_id={enquiry.id}", None),
            ("POST", "/inbox/drafts", {"account_id": a, "body_html": "x"}),
            (
                "POST",
                f"/proposals/{enquiry.id}/send",
                {"mode": "email", "body_html": "x", "idempotency_key": "r3"},
            ),
            ("POST", f"/events/{event.id}/send-to-client", {"idempotency_key": "r4"}),
        ]
        for method, path, body in attempts:
            kw = {"json": body} if body is not None else {}
            r = call(client, method, path, rival, **kw)
            assert r.status_code == 404, (method, path, r.status_code, r.text)
        mailboxes = call(client, "GET", "/inbox/mailboxes", rival).json()["mailboxes"]
        assert all(m["id"] != a for m in mailboxes)
        assert call(client, "GET", "/inbox/threads", rival).json()["threads"] == []
        assert (
            call(client, "GET", f"/inbox/threads?account_id={a}", rival).status_code
            == 404
        )

    def test_staff_see_only_their_venues_mailboxes(self, client, db_session, w):
        conn = Connection(
            connector_name="venue_mailbox",
            venue_id=w.venue2.id,
            config={},
            enabled="true",
        )
        db_session.add(conn)
        db_session.flush()
        garden = FnMailboxAccount(
            organization_id=w.org.id,
            venue_id=w.venue2.id,
            google_email="garden@cookbros.co.nz",
            connection_id=conn.id,
            status="active",
            history_id=1,
        )
        db_session.add(garden)
        db_session.flush()
        assert (
            call(
                client, "GET", f"/inbox/threads?account_id={garden.id}", w.staff
            ).status_code
            == 404
        )
        assert (
            call(client, "GET", f"/inbox/threads/{garden.id}/t-x", w.staff).status_code
            == 404
        )
        owner = call(client, "GET", "/inbox/mailboxes", w.owner).json()["mailboxes"]
        assert garden.id in {m["id"] for m in owner}


# ---------------------------------------------------------------------------
# Connecting a mailbox
# ---------------------------------------------------------------------------


def _spec(db, *, client_id="cid", secret="secret", scopes=None):
    spec = db.query(ConnectionSpec).filter_by(connector_name="venue_mailbox").first()
    oauth = {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scopes": scopes
        or "https://www.googleapis.com/auth/userinfo.email "
        "https://www.googleapis.com/auth/gmail.modify",
    }
    if client_id:
        oauth.update({"client_id": client_id, "client_secret": secret})
    if spec is None:
        spec = ConnectionSpec(
            connector_name="venue_mailbox",
            display_name="Venue mailbox (Google)",
            category="calendar",
            auth_type="oauth2",
            auth_config={},
            tools=[],
        )
        db.add(spec)
    spec.oauth_config = oauth
    db.flush()
    return spec


def _connector(db, w):
    role = Role(
        id=str(uuid.uuid4()),
        organization_id=w.org.id,
        name=f"conn-{uuid.uuid4().hex[:6]}",
        display_name="Connector",
        permissions=["settings:connectors"],
    )
    db.add(role)
    db.flush()
    from app.db.models import OrganizationMembership

    m = db.query(OrganizationMembership).filter_by(user_id=w.owner.id).one()
    m.role_id = role.id
    db.flush()
    return w.owner


@pytest.fixture()
def bare(db_session, fake):
    """A live org whose Brew Bar has only the importer's placeholder."""
    w = make_world(db_session)
    placeholder = FnMailboxAccount(
        organization_id=w.org.id, venue_id=w.venue.id, google_email=MAILBOX.upper()
    )
    db_session.add(placeholder)
    db_session.flush()
    w.placeholder = placeholder
    return w


class TestConnect:
    def test_no_google_client_is_a_clear_refusal(self, client, db_session, bare):
        _spec(db_session, client_id=None)
        user = _connector(db_session, bare)
        r = call(
            client,
            "POST",
            "/inbox/mailboxes/connect",
            user,
            json={"venue_id": bare.venue.id},
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "google_not_configured"
        assert "Google client not configured" in r.json()["detail"]["message"]

    def test_connect_needs_the_connectors_permission(self, client, db_session, bare):
        _spec(db_session)
        r = call(
            client,
            "POST",
            "/inbox/mailboxes/connect",
            bare.staff,
            json={"venue_id": bare.venue.id},
        )
        assert r.status_code == 403

    def test_consent_url_asks_incrementally_for_this_venue(
        self, client, db_session, bare
    ):
        _spec(db_session)
        user = _connector(db_session, bare)
        r = call(
            client,
            "POST",
            "/inbox/mailboxes/connect",
            user,
            json={"venue_id": bare.venue.id},
        )
        assert r.status_code == 200, r.text
        url = urlparse(r.json()["authorize_url"])
        q = parse_qs(url.query)
        assert url.netloc == "accounts.google.com"
        assert q["include_granted_scopes"] == ["true"]
        assert q["login_hint"] == [MAILBOX.upper()]
        assert q["redirect_uri"][0].endswith(
            "/api/functions/inbox/mailboxes/oauth/callback"
        )
        assert "gmail.modify" in q["scope"][0]
        state = db_session.query(OAuthState).filter_by(state=q["state"][0]).one()
        # The person who started the connect is on the state (checked again,
        # and named, when Google sends them back).
        assert (state.connector_name, state.venue_id, state.user_id) == (
            "venue_mailbox",
            bare.venue.id,
            user.id,
        )
        other = call(
            client,
            "POST",
            "/inbox/mailboxes/connect",
            user,
            json={"venue_id": bare.other_venue.id},
        )
        assert other.status_code == 403

    def _callback(
        self,
        client,
        db,
        w,
        monkeypatch,
        *,
        scope=None,
        venue=None,
        initiator="connector",
    ):
        """Google's redirect for a state as ``start_connect`` makes it: for
        ``venue`` (default Brew Bar), started by ``initiator`` (default the
        org's connector; None = a state Norm's connect didn't make)."""
        _spec(db)
        if initiator == "connector":
            initiator = _connector(db, w)
        db.add(
            OAuthState(
                connector_name="venue_mailbox",
                state="st-1",
                venue_id=(venue or w.venue).id,
                user_id=initiator.id if initiator is not None else None,
            )
        )
        db.flush()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "access_token": "fresh-acc",
            "refresh_token": "fresh-ref",
            "expires_in": 3600,
            "scope": scope
            or "https://www.googleapis.com/auth/gmail.modify "
            "https://www.googleapis.com/auth/userinfo.email",
        }
        monkeypatch.setattr(
            "app.services.oauth_service.httpx.post", lambda *a, **k: resp
        )
        return client.get(
            "/api/functions/inbox/mailboxes/oauth/callback?code=c&state=st-1"
        )

    def _nothing_stored_for(self, db, venue):
        db.expire_all()
        conn = (
            db.query(Connection)
            .filter_by(connector_name="venue_mailbox", venue_id=venue.id)
            .first()
        )
        assert conn is None or (
            conn.access_token is None and conn.refresh_token is None
        )

    def test_the_callback_attaches_the_placeholder(
        self, client, db_session, bare, monkeypatch
    ):
        r = self._callback(client, db_session, bare, monkeypatch)
        assert r.status_code == 200, r.text
        db_session.expire_all()
        acct = (
            db_session.query(FnMailboxAccount).filter_by(id=bare.placeholder.id).one()
        )
        conn = (
            db_session.query(Connection)
            .filter_by(connector_name="venue_mailbox", venue_id=bare.venue.id)
            .one()
        )
        assert acct.connection_id == conn.id and acct.status == "resyncing"
        assert conn.refresh_token == "fresh-ref"
        assert (
            "1 Quay St" in acct.signature_html and "script" not in acct.signature_html
        )
        assert acct.display_name == "Brew Bar Events"
        folders = {
            label.gmail_label_id: label.folder
            for label in db_session.query(FnMailboxLabel).filter_by(account_id=acct.id)
        }
        assert folders["Label_enq"] == "enquiry" and folders["INBOX"] is None

    def test_a_connect_records_who_did_it_and_tells_the_mailbox(
        self, client, db_session, bare, monkeypatch, caplog
    ):
        import logging

        from app.db.models import EmailLog

        with caplog.at_level(
            logging.INFO, logger="app.services.functions.mailbox.accounts"
        ):
            r = self._callback(client, db_session, bare, monkeypatch)
        assert r.status_code == 200, r.text
        db_session.expire_all()
        conn = (
            db_session.query(Connection)
            .filter_by(connector_name="venue_mailbox", venue_id=bare.venue.id)
            .one()
        )
        assert conn.oauth_metadata["connected_by_user_id"] == bare.owner.id
        assert conn.oauth_metadata["email"] == MAILBOX
        audit = [
            r.getMessage()
            for r in caplog.records
            if "mailbox.connected" in r.getMessage()
        ]
        assert len(audit) == 1
        for part in (bare.org.id, bare.venue.id, bare.owner.id, MAILBOX):
            assert part in audit[0]
        (notice,) = (
            db_session.query(EmailLog)
            .filter(
                EmailLog.organization_id == bare.org.id,
                EmailLog.template_name == "functions:mailbox_connected",
            )
            .all()
        )
        assert notice.to_addresses == [MAILBOX]
        assert notice.status == "suppressed"  # never sent outside production
        for part in (
            "Brew Bar",
            "Cook Bros",
            bare.owner.full_name,
            "tell your manager",
        ):
            assert part in notice.html_body

    def test_another_organisations_mailbox_is_refused_with_nothing_stored(
        self, client, db_session, bare, monkeypatch
    ):
        """The review's reproduction: org B (Rival Co) starts a connect for
        ITS venue, and org A's mailbox owner completes Google's consent on
        that link. Org A already has the address (Brew Bar's placeholder) —
        it must not be attached to org B, and no token may be filed for B."""
        r = self._callback(
            client,
            db_session,
            bare,
            monkeypatch,
            venue=bare.other_venue,
            initiator=bare.other_user,  # org B's owner
        )
        assert r.status_code == 400
        assert "another organisation" in r.text and "Nothing was saved" in r.text
        self._nothing_stored_for(db_session, bare.other_venue)
        assert (
            db_session.query(FnMailboxAccount)
            .filter_by(organization_id=bare.other_org.id)
            .count()
            == 0
        )
        placeholder = (
            db_session.query(FnMailboxAccount).filter_by(id=bare.placeholder.id).one()
        )
        assert placeholder.connection_id is None

    def test_a_connected_mailbox_of_another_organisation_is_refused_too(
        self, client, db_session, bare, monkeypatch
    ):
        conn = Connection(
            connector_name="venue_mailbox",
            venue_id=bare.venue.id,
            config={},
            enabled="true",
            access_token="a-acc",
            refresh_token="a-ref",
        )
        db_session.add(conn)
        db_session.flush()
        bare.placeholder.connection_id = conn.id
        bare.placeholder.status = "active"
        db_session.flush()
        r = self._callback(
            client,
            db_session,
            bare,
            monkeypatch,
            venue=bare.other_venue,
            initiator=bare.other_user,
        )
        assert r.status_code == 400
        self._nothing_stored_for(db_session, bare.other_venue)
        db_session.expire_all()
        assert db_session.get(Connection, conn.id).refresh_token == "a-ref"

    def test_a_state_norms_connect_did_not_make_is_refused(
        self, client, db_session, bare, monkeypatch
    ):
        """The reproduction as written: a state with no one behind it."""
        r = self._callback(
            client,
            db_session,
            bare,
            monkeypatch,
            venue=bare.other_venue,
            initiator=None,
        )
        assert r.status_code == 400
        self._nothing_stored_for(db_session, bare.other_venue)

    def test_a_starter_who_lost_the_venue_is_refused(
        self, client, db_session, bare, monkeypatch
    ):
        r = self._callback(client, db_session, bare, monkeypatch, initiator=bare.staff)
        assert r.status_code == 200  # staff has Brew Bar
        db_session.expire_all()

        bare.placeholder.connection_id = None
        db_session.flush()
        r = self._callback(
            client,
            db_session,
            bare,
            monkeypatch,
            venue=bare.venue2,
            initiator=bare.staff,
        )
        assert r.status_code == 400
        self._nothing_stored_for(db_session, bare.venue2)

    def test_a_mailbox_belonging_to_another_venue_is_refused_with_nothing_stored(
        self, client, db_session, bare, monkeypatch
    ):
        r = self._callback(client, db_session, bare, monkeypatch, venue=bare.venue2)
        assert r.status_code == 400 and "Brew Bar" in r.text
        self._nothing_stored_for(db_session, bare.venue2)
        assert (
            db_session.query(FnMailboxAccount)
            .filter_by(id=bare.placeholder.id)
            .one()
            .connection_id
            is None
        )

    def test_a_grant_without_gmail_is_refused(
        self, client, db_session, bare, monkeypatch
    ):
        r = self._callback(
            client,
            db_session,
            bare,
            monkeypatch,
            scope="https://www.googleapis.com/auth/userinfo.email",
        )
        assert r.status_code == 400 and "Gmail access" in r.text
        self._nothing_stored_for(db_session, bare.venue)

    def test_an_unknown_state_is_refused(self, client, db_session, bare):
        r = client.get(
            "/api/functions/inbox/mailboxes/oauth/callback?code=c&state=nope"
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------

PDF = b"%PDF-1.4 floor plan"


def _with_attachments(fake, **kw):
    return fake.add_message(
        frm=f"Casey <{CUSTOMER}>",
        to=[MAILBOX],
        subject="Floor plan",
        thread_id="t-att",
        attachments=[
            {
                "filename": "Floor plan – deck.pdf",
                "mime_type": "application/pdf",
                "data": PDF,
            },
            {
                "filename": "menu.html",
                "mime_type": "text/html",
                "data": b"<script>x()</script>",
            },
        ],
        **kw,
    )


def _part(fake, mid, filename):
    return next(
        p
        for p in fake.messages[mid]["payload"]["parts"]
        if p.get("filename") == filename
    )


def _att_url(w, mid, ref, thread="t-att"):
    return f"/inbox/threads/{w.account.id}/{thread}/messages/{mid}/attachments/{ref}"


class TestAttachments:
    def test_the_thread_lists_attachments_and_one_downloads(self, client, w, fake):
        mid = _with_attachments(fake)
        detail = call(
            client, "GET", f"/inbox/threads/{w.account.id}/t-att", w.staff
        ).json()
        (msg,) = detail["messages"]
        pdf = msg["attachments"][0]
        assert {"attachment_id", "filename", "mime_type", "size", "part_id"} <= set(pdf)
        assert (pdf["filename"], pdf["mime_type"], pdf["size"]) == (
            "Floor plan – deck.pdf",
            "application/pdf",
            len(PDF),
        )
        r = call(client, "GET", _att_url(w, mid, pdf["attachment_id"]), w.staff)
        assert r.status_code == 200 and r.content == PDF
        assert r.headers["content-type"] == "application/pdf"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in r.headers["content-security-policy"]
        assert r.headers["content-disposition"] == (
            'attachment; filename="Floor plan _ deck.pdf"; '
            "filename*=UTF-8''Floor%20plan%20%E2%80%93%20deck.pdf"
        )

    def test_active_types_are_served_as_bytes(self, client, w, fake):
        mid = _with_attachments(fake)
        part = _part(fake, mid, "menu.html")
        r = call(client, "GET", _att_url(w, mid, part["body"]["attachmentId"]), w.staff)
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/octet-stream"
        assert r.headers["content-disposition"].startswith("attachment;")

    def test_part_ids_survive_gmail_changing_attachment_ids(self, client, w, fake):
        mid = _with_attachments(fake)
        fake.rotate_attachment_ids = True
        detail = call(
            client, "GET", f"/inbox/threads/{w.account.id}/t-att", w.staff
        ).json()
        pdf = detail["messages"][0]["attachments"][0]
        r = call(client, "GET", _att_url(w, mid, pdf["part_id"]), w.staff)
        assert r.status_code == 200 and r.content == PDF

    def test_too_big_and_wrong_thread_and_gone(self, client, w, fake):
        mid = fake.add_message(
            frm=f"Casey <{CUSTOMER}>",
            to=[MAILBOX],
            thread_id="t-att",
            attachments=[
                {"filename": "video.mov", "data": b"x", "size": 26 * 1024 * 1024}
            ],
        )
        aid = fake.messages[mid]["payload"]["parts"][-1]["body"]["attachmentId"]
        r = call(client, "GET", _att_url(w, mid, aid), w.staff)
        assert (
            r.status_code == 413
            and r.json()["detail"]["code"] == "attachment_too_large"
        )
        assert (
            call(
                client, "GET", _att_url(w, mid, aid, thread="t-casey"), w.staff
            ).status_code
            == 404
        )
        assert call(client, "GET", _att_url(w, mid, "nope"), w.staff).status_code == 404
        assert (
            call(client, "GET", _att_url(w, "m9999", aid), w.staff).status_code == 404
        )

    def test_shadow_mode_may_download(self, client, db_session, w, fake):
        mid = _with_attachments(fake)
        w.org.functions_mode = "shadow"
        db_session.flush()
        part = _part(fake, mid, "Floor plan – deck.pdf")
        r = call(client, "GET", _att_url(w, mid, part["partId"]), w.staff)
        assert r.status_code == 200 and r.content == PDF

    def test_other_organisations_and_venues_get_404(self, client, db_session, w, fake):
        mid = _with_attachments(fake)
        part = _part(fake, mid, "Floor plan – deck.pdf")
        url = _att_url(w, mid, part["partId"])
        assert call(client, "GET", url, w.other_user).status_code == 404
        # Garden Bar's mailbox is out of reach for Brew Bar staff.
        conn = Connection(
            connector_name="venue_mailbox",
            venue_id=w.venue2.id,
            config={},
            enabled="true",
            access_token="t",
            refresh_token="r",
        )
        db_session.add(conn)
        db_session.flush()
        garden = FnMailboxAccount(
            organization_id=w.org.id,
            venue_id=w.venue2.id,
            google_email="garden@x.nz",
            connection_id=conn.id,
            status="active",
            history_id=1,
        )
        db_session.add(garden)
        db_session.flush()
        other = f"/inbox/threads/{garden.id}/t-att/messages/{mid}/attachments/{part['partId']}"
        assert call(client, "GET", other, w.staff).status_code == 404
        fake.address = "garden@x.nz"  # the fake now answers as Garden Bar's mailbox
        assert (
            call(client, "GET", other, w.owner).status_code == 200
        )  # owner sees all venues

    def test_an_unconnected_mailbox_cannot_fetch(self, client, db_session, w, fake):
        mid = _with_attachments(fake)
        w.account.connection_id = None
        w.account.status = "needs_reconnect"
        db_session.flush()
        r = call(client, "GET", _att_url(w, mid, "1"), w.staff)
        assert (
            r.status_code == 409 and r.json()["detail"]["code"] == "mailbox_unavailable"
        )
