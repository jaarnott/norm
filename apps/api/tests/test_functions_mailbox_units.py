"""The mailbox service's pure parts and its Gmail client:

* addresses parsed by the standard library (quoted names with commas,
  encoded words), messages built without header injection;
* incoming HTML made safe (scripts, handlers, ``javascript:``, CSS that
  loads URLs) while keeping its layout;
* the three message flags and "our" addresses (public mail domains never
  count as ours); the Actioned label names, tolerantly matched;
* the Gmail client: one refresh per 401, scope and rate-limit errors sorted,
  writes refused unless live, a timeout on a write marked ambiguous;
* real tokens through Norm's OAuth machinery: an expired token refreshed
  (encrypted at rest when a key is set), a dead grant flagged.
"""

from __future__ import annotations

import datetime as dt
import time
import email
from email import policy
from unittest.mock import MagicMock

import httpx
import pytest

from app.db.config_models import ConnectionSpec
from app.db.models import Connection
from app.services.functions.errors import Invalid, NotLive
from app.services.functions.mailbox import (
    classify,
    display,
    gmail_client,
    mime,
    mirror,
    pricing,
)
from tests.conftest import _make_venue
from tests.functions_mailbox_fakes import FakeTokens

UTC = dt.timezone.utc
OURS = classify.OurAddresses(
    mailboxes=frozenset({"events@brewbar.co.nz", "garden@cookbros.co.nz"}),
    domains=frozenset({"cookbros.co.nz"}),
)


class TestAddresses:
    def test_quoted_names_with_commas_and_encoded_words(self):
        pairs = mime.parse_address_list('"Smith, Jane" <Jane@X.com>, bob@y.com')
        assert pairs == [("Smith, Jane", "jane@x.com"), ("", "bob@y.com")]
        assert mime.parse_address("=?UTF-8?B?Sm9zw6k=?= <jose@x.com>") == (
            "José",
            "jose@x.com",
        )

    def test_clean_addresses_refuses_rather_than_drops(self):
        assert mime.clean_addresses(["A@x.com", "a@x.com", "B <b@y.nz>"]) == [
            "a@x.com",
            "b@y.nz",
        ]
        with pytest.raises(Invalid):
            mime.clean_addresses(["not an address"])
        with pytest.raises(Invalid):
            mime.clean_addresses(["a@x.com\r\nBcc: z@z.com"])

    def test_subjects(self):
        assert mime.reply_subject("Party") == "Re: Party"
        assert mime.reply_subject("RE: Party") == "RE: Party"
        assert mime.forward_subject("Re: Party") == "Fwd: Party"


class TestBuild:
    def test_a_message_is_well_formed_and_injection_proof(self):
        raw = mime.build_message(
            from_email="events@brewbar.co.nz",
            from_name="Zoë from Brew Bar",
            to=["casey@x.com"],
            cc=["mum@x.com"],
            bcc=["boss@cookbros.co.nz"],
            subject="Your booking\r\nBcc: attacker@evil.com",
            html="<p>Hi <b>Casey</b></p>",
            message_id="<norm.1@brewbar.co.nz>",
            in_reply_to="<a@x.com>",
            references="<z@x.com> <a@x.com>",
        )
        msg = email.message_from_bytes(raw, policy=policy.default)
        assert msg["Subject"] == "Your booking Bcc: attacker@evil.com"
        assert str(msg["From"]) == "Zoë from Brew Bar <events@brewbar.co.nz>"
        assert msg["Bcc"] == "boss@cookbros.co.nz"
        assert msg["In-Reply-To"] == "<a@x.com>"
        assert msg.get_body(("plain",)).get_content().strip() == "Hi Casey"
        assert "<b>Casey</b>" in msg.get_body(("html",)).get_content()
        assert b"\r\n" in raw

    def test_new_message_ids_are_unique_and_on_the_mailbox_domain(self):
        a, b = (
            mime.new_message_id("x@brewbar.co.nz"),
            mime.new_message_id("x@brewbar.co.nz"),
        )
        assert a != b and a.endswith("@brewbar.co.nz>")


class TestDisplay:
    def test_incoming_html_keeps_layout_and_loses_everything_active(self):
        dirty = (
            '<table style="width:100%;background-image:url(http://track/x)">'
            '<tr><td onclick="steal()" style="color:red">Hi</td></tr></table>'
            '<a href="javascript:alert(1)">x</a><a href="https://ok.nz">ok</a>'
            "<script>alert(1)</script><style>body{display:none}</style>"
            '<iframe src="https://evil"></iframe><form action="/x"><input></form>'
            '<img src="https://img.nz/a.png" onerror="x()">'
        )
        clean = display.safe_html(dirty)
        for bad in (
            "script",
            "onclick",
            "onerror",
            "javascript:",
            "iframe",
            "<form",
            "background-image",
            "display:none",
        ):
            assert bad not in clean
        assert "<table" in clean and "color:red" in clean
        assert 'href="https://ok.nz"' in clean and 'src="https://img.nz/a.png"' in clean


class TestFlags:
    def flags(self, frm, to, cc=(), labels=(), headers=None, subject="Hi"):
        return classify.message_flags(
            mailbox_email="events@brewbar.co.nz",
            from_email=frm,
            to_emails=list(to),
            cc_emails=list(cc),
            label_ids=list(labels),
            headers=headers or {},
            subject=subject,
            ours=OURS,
        )

    def test_the_matrix(self):
        mailbox = "events@brewbar.co.nz"
        assert self.flags("casey@x.com", [mailbox]) == (True, False, False)
        assert self.flags(mailbox, ["casey@x.com"], labels=["SENT"]) == (
            False,
            False,
            False,
        )
        assert self.flags("alias@brewbar.co.nz", ["casey@x.com"], labels=["SENT"]) == (
            False,
            False,
            False,
        )
        assert self.flags("boss@cookbros.co.nz", [mailbox]) == (False, True, False)
        assert self.flags(mailbox, ["boss@cookbros.co.nz"], labels=["SENT"]) == (
            False,
            True,
            False,
        )
        assert self.flags("garden@cookbros.co.nz", [mailbox]) == (False, True, False)
        assert self.flags("boss@cookbros.co.nz", ["casey@x.com"], cc=[mailbox]) == (
            False,
            False,
            False,
        )
        # web-form notification: the mailbox to itself (and staff), nobody outside
        assert self.flags(
            mailbox, [mailbox, "boss@cookbros.co.nz"], labels=["SENT", "INBOX"]
        ) == (True, False, False)
        assert self.flags(mailbox, ["casey@x.com"], cc=[mailbox], labels=["SENT"]) == (
            False,
            False,
            False,
        )

    def test_auto_replies(self):
        assert self.flags(
            "c@x.com", ["e@b.nz"], headers={"auto-submitted": "auto-replied"}
        )[2]
        assert not self.flags("c@x.com", ["e@b.nz"], headers={"auto-submitted": "no"})[
            2
        ]
        assert self.flags("c@x.com", ["e@b.nz"], headers={"precedence": "bulk"})[2]
        assert self.flags("c@x.com", ["e@b.nz"], headers={"x-autoreply": "yes"})[2]
        assert self.flags("mailer-daemon@googlemail.com", ["e@b.nz"])[2]
        assert self.flags("c@x.com", ["e@b.nz"], subject="Out of Office: away")[2]
        assert not self.flags(
            "noreply@squarespace.info", ["e@b.nz"], subject="Form submission"
        )[2]

    def test_public_mail_domains_are_never_ours(self, db_session):
        from tests.functions_world import make_world

        w = make_world(db_session)
        w.staff.email = "sam@gmail.com"
        w.owner.email = "olive@cookbros.co.nz"
        db_session.flush()
        ours = classify.our_addresses(db_session, w.org.id)
        assert "cookbros.co.nz" in ours.domains and "gmail.com" not in ours.domains
        assert not ours.is_ours("customer@gmail.com")
        assert ours.is_ours("anyone@cookbros.co.nz")


def test_actioned_label_names_match_tolerantly():
    assert mirror.folder_for_label("Actioned - Enquiry") == "enquiry"
    assert mirror.folder_for_label("actioned – reservations") == "reservation"
    assert mirror.folder_for_label("Actioned  -  All Other Mail") == "other"
    assert mirror.folder_for_label("Actioned - Event") == "reservation"
    assert mirror.folder_for_label("Enquiry") is None


def _client(handler, tokens=None, writable=True):
    return gmail_client.GmailClient(
        tokens or FakeTokens(),
        writable=writable,
        transport=httpx.MockTransport(handler),
    )


class TestGmailClient:
    def test_a_401_refreshes_once_and_retries(self):
        seen = []

        def handler(request):
            seen.append(request.headers["Authorization"])
            return httpx.Response(
                401 if len(seen) == 1 else 200, json={"historyId": "5"}
            )

        tokens = FakeTokens()
        with _client(handler, tokens) as c:
            assert c.profile() == {"historyId": "5"}
        assert len(seen) == 2 and tokens.forced == 1

    @pytest.mark.parametrize(
        "status,reason,kind",
        [
            (403, "insufficientPermissions", gmail_client.MailboxAuthError),
            (403, "rateLimitExceeded", gmail_client.GmailRateLimited),
            (429, "rateLimitExceeded", gmail_client.GmailRateLimited),
            (404, "notFound", gmail_client.GmailNotFound),
            (503, "backendError", gmail_client.GmailTransient),
            (403, "accessNotConfigured", gmail_client.GmailError),
        ],
    )
    def test_errors_are_sorted(self, status, reason, kind):
        def handler(request):
            return httpx.Response(
                status,
                json={
                    "error": {
                        "code": status,
                        "message": "m",
                        "errors": [{"reason": reason}],
                    }
                },
            )

        with _client(handler) as c, pytest.raises(kind) as exc:
            c.profile()
        if kind is gmail_client.GmailError:
            assert type(exc.value) is gmail_client.GmailError

    def test_writes_refuse_unless_live(self):
        with _client(lambda r: httpx.Response(200, json={}), writable=False) as c:
            with pytest.raises(NotLive):
                c.send("raw")
            with pytest.raises(NotLive):
                c.modify_thread("t", add=["L"])
            assert c.labels() == []  # reads are fine

    def test_a_timeout_on_a_write_is_ambiguous_but_not_a_refused_connection(self):
        def timeout(request):
            raise httpx.ReadTimeout("slow", request=request)

        def refused(request):
            raise httpx.ConnectError("down", request=request)

        with _client(timeout) as c, pytest.raises(gmail_client.GmailTransient) as exc:
            c.send("raw")
        assert exc.value.ambiguous is True
        with _client(refused) as c, pytest.raises(gmail_client.GmailTransient) as exc:
            c.send("raw")
        assert exc.value.ambiguous is False


class TestConnectionTokens:
    def _setup(self, db, **kw):
        venue = _make_venue(db)
        conn = Connection(
            connector_name="venue_mailbox",
            venue_id=venue.id,
            config={},
            enabled="true",
            access_token="old",
            refresh_token="ref",
            **kw,
        )
        db.add(conn)
        spec = (
            db.query(ConnectionSpec).filter_by(connector_name="venue_mailbox").first()
        )
        if spec is None:
            spec = ConnectionSpec(
                connector_name="venue_mailbox",
                display_name="Venue mailbox",
                auth_type="oauth2",
                auth_config={},
                tools=[],
            )
            db.add(spec)
        spec.oauth_config = {
            "token_url": "https://oauth2.googleapis.com/token",
            "client_id": "cid",
            "client_secret": "s",
        }
        db.flush()
        return conn

    def test_a_fresh_token_is_used_and_an_expired_one_refreshed(
        self, db_session, monkeypatch
    ):
        conn = self._setup(
            db_session, token_expires_at=dt.datetime.now(UTC) - dt.timedelta(minutes=1)
        )
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"access_token": "new", "expires_in": 3600}
        post = MagicMock(return_value=resp)
        monkeypatch.setattr("app.services.oauth_service.httpx.post", post)
        tokens = gmail_client.ConnectionTokens(
            conn.id, db=db_session, config_db=db_session
        )
        assert tokens.access_token() == "new"
        assert post.call_args.kwargs["data"]["refresh_token"] == "ref"
        assert tokens.access_token() == "new" and post.call_count == 1

    def test_a_refused_refresh_is_a_dead_grant_and_flags_the_connection(
        self, db_session, monkeypatch
    ):
        conn = self._setup(
            db_session, token_expires_at=dt.datetime.now(UTC) - dt.timedelta(minutes=1)
        )
        resp = MagicMock(status_code=400, text='{"error": "invalid_grant"}')
        monkeypatch.setattr(
            "app.services.oauth_service.httpx.post", MagicMock(return_value=resp)
        )
        tokens = gmail_client.ConnectionTokens(
            conn.id, db=db_session, config_db=db_session
        )
        with pytest.raises(gmail_client.MailboxAuthError):
            tokens.access_token()
        db_session.expire_all()
        assert db_session.get(Connection, conn.id).needs_reconnect is True
        with pytest.raises(gmail_client.MailboxAuthError):
            tokens.access_token()  # fails closed from now on, without asking Google


class TestAdversarialInput:
    """Anyone can email a venue mailbox, and these run when staff open the
    thread or the agent reads it. The regexes they replaced were quadratic
    (60 KB of "<script>" took minutes); the bound here is generous for a
    loaded CI machine — a quadratic regression takes seconds, not 250 ms."""

    BOUND = 0.25

    def _fast(self, fn, value):
        start = time.perf_counter()
        out = fn(value)
        assert time.perf_counter() - start < self.BOUND, fn.__name__
        return out

    @pytest.mark.parametrize(
        "body",
        [
            "<script>" * 7_500,  # 60 KB of unclosed script openings
            "<style x>" * 6_700,
            "<" * 200_000,  # 200 KB of "<" with no ">"
            "<li" * 66_000,
            "<a" * 100_000,
            "<div>" * 30_000,  # nesting the HTML parser handles quadratically
            "<ul><li>" * 10_000,
        ],
    )
    def test_bodies_are_processed_in_linear_time(self, body):
        text = self._fast(display.html_to_text, body)
        html = self._fast(display.safe_html, body)
        self._fast(display.looks_like_html, body)
        self._fast(pricing.contains_price, text)
        assert "<script" not in html.lower()

    def test_huge_address_headers_are_bounded(self):
        self._fast(mime.parse_address_list, "a@b.co, " * 14_000)
        self._fast(mime.parse_address, "<" * 100_000)
        self._fast(mime.parse_address, "=?a?b?" * 10_000)

    def test_behaviour_on_real_mail_is_unchanged(self):
        # (Also html_to_text's own contract: paragraphs, list bullets, <br>.
        # The strict email check, mime.is_email = addresses.is_email, is held
        # in test_functions_mailbox_security.py::TestStrictEmail.)
        assert (
            display.html_to_text(
                "<p>Hi&nbsp;Casey &amp;lt;b&amp;gt; 2 &lt; 3</p>"
                "<ul><li>One</li><li>Two</li></ul><br>Bye<script>x()</script>"
            )
            == "Hi Casey &lt;b&gt; 2 < 3\n\n- One\n- Two\n\nBye"
        )
        assert display.looks_like_html("<p>hi</p>")
        assert not display.looks_like_html("2 < 3 and 4 > 1")
        assert pricing.contains_price(display.html_to_text("<p>It's <b>$1,200</b></p>"))

    def test_absurd_markup_is_shown_as_text_not_parsed(self):
        html = display.safe_html("<div>" * 200 + "Hello <b>there</b>" + "</div>" * 200)
        assert "Hello" in html and "there" in html
        assert "<div" not in html  # degraded to escaped text paragraphs
