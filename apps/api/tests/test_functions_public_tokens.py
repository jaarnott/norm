"""Customer links: unguessable, stored only as a hash, stable until revoked,
and dead (indistinguishably) once revoked or expired."""

from __future__ import annotations

import datetime as dt

import pytest

from app.config import settings
from app.db.functions_models import FnPortalAccess, FnPortalSession, FnPublicToken
from app.db.models import _now
from app.services.functions import NotFound, NotLive, enquiries, events, public_tokens
from app.services.functions.ctx import for_quote
from app.services.functions.errors import Conflict, Forbidden
from tests.functions_world import add_contact, make_world


@pytest.fixture
def w(db_session):
    return make_world(db_session)


def _enquiry(w):
    return enquiries.create_enquiry(
        w.db,
        w.ctx(),
        contact={"name": "Casey Client", "email": "casey@example.com"},
        venue_id=w.venue.id,
        booking_type="large_event_exclusive",
        subject="Casey's 40th",
    )


def _event(w, date=dt.date(2026, 12, 4)):
    contact = add_contact(w.db, w.org.id, email="pat@example.com")
    return events.create_event(
        w.db,
        w.ctx(),
        venue_id=w.venue.id,
        contact_id=contact.id,
        event_date=date,
        booking_type="group_booking",
    )


def _token(url: str) -> str:
    return url.rsplit("/", 1)[1]


class TestMinting:
    def test_quote_link_is_stable_and_only_its_hash_is_stored(self, w):
        enquiry = _enquiry(w)
        first = public_tokens.mint_quote_link(w.db, w.ctx(), enquiry.id)
        second = public_tokens.mint_quote_link(w.db, w.ctx(), enquiry.id)
        assert first == second
        assert first.startswith(f"{settings.app_url}/q/")
        token = _token(first)
        assert len(token) == 43 and public_tokens._TOKEN.fullmatch(token)
        rows = w.db.query(FnPublicToken).filter_by(enquiry_id=enquiry.id).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.token_hash == public_tokens.hash_token(token)
        for column in FnPublicToken.__table__.columns:
            assert getattr(row, column.name) != token  # never stored in the clear

    def test_quote_links_last_180_days_from_the_latest_mint(self, w):
        enquiry = _enquiry(w)
        public_tokens.mint_quote_link(w.db, w.ctx(), enquiry.id)
        row = w.db.query(FnPublicToken).filter_by(enquiry_id=enquiry.id).one()
        row.expires_at = _now() + dt.timedelta(days=3)
        public_tokens.mint_quote_link(w.db, w.ctx(), enquiry.id)
        assert row.expires_at > _now() + dt.timedelta(days=179)

    def test_portal_link_allows_emails_and_lives_until_event_plus_30_days(self, w):
        event = _event(w)
        url = public_tokens.mint_portal_link(
            w.db, w.ctx(), event.id, ["Pat@Example.com", "boss@example.com"]
        )
        assert url.startswith(f"{settings.app_url}/e/")
        emails = sorted(
            a.email for a in w.db.query(FnPortalAccess).filter_by(event_id=event.id)
        )
        assert emails == ["boss@example.com", "pat@example.com"]
        row = w.db.query(FnPublicToken).filter_by(event_id=event.id).one()
        assert row.expires_at.date() == dt.date(2027, 1, 4)
        # Allowing the same address again is a no-op.
        public_tokens.mint_portal_link(w.db, w.ctx(), event.id, ["pat@example.com"])
        assert w.db.query(FnPortalAccess).filter_by(event_id=event.id).count() == 2

    def test_minting_needs_live_staff_and_visibility(self, w, db_session):
        enquiry = _enquiry(w)
        w.org.functions_mode = "shadow"
        db_session.flush()
        with pytest.raises(NotLive):
            public_tokens.mint_quote_link(db_session, w.ctx(), enquiry.id)
        w.org.functions_mode = "live"
        db_session.flush()
        customer = for_quote(db_session, w.org.id, enquiry_id=enquiry.id)
        with pytest.raises(Forbidden):
            public_tokens.mint_quote_link(db_session, customer, enquiry.id)
        rival = w.ctx(w.other_user)
        with pytest.raises(NotFound):
            public_tokens.mint_quote_link(db_session, rival, enquiry.id)

    def test_no_portal_link_for_a_cancelled_event(self, w):
        event = _event(w)
        events.cancel_event(w.db, w.ctx(), event.id, reason="Postponed")
        with pytest.raises(Conflict):
            public_tokens.mint_portal_link(w.db, w.ctx(), event.id, ["pat@example.com"])


class TestResolving:
    def test_resolve_and_wrong_kind(self, w):
        enquiry = _enquiry(w)
        token = _token(public_tokens.mint_quote_link(w.db, w.ctx(), enquiry.id))
        assert public_tokens.resolve(w.db, token, "quote").enquiry_id == enquiry.id
        assert public_tokens.resolve(w.db, token, "portal") is None
        assert public_tokens.resolve(w.db, token[:-1] + "x", "quote") is None
        assert public_tokens.resolve(w.db, "not-a-token", "quote") is None
        assert public_tokens.resolve(w.db, None, "quote") is None

    def test_expired_quote_link_is_dead(self, w):
        enquiry = _enquiry(w)
        token = _token(public_tokens.mint_quote_link(w.db, w.ctx(), enquiry.id))
        row = public_tokens.lookup(w.db, token, "quote")
        row.expires_at = _now() - dt.timedelta(seconds=1)
        assert public_tokens.resolve(w.db, token, "quote") is None
        # ...but still recognisable in sent mail (lookup has no validity checks).
        assert public_tokens.lookup(w.db, token, "quote") is row

    def test_portal_link_follows_the_event_date(self, w):
        event = _event(w, date=dt.date(2026, 1, 10))
        token = _token(public_tokens.mint_portal_link(w.db, w.ctx(), event.id, []))
        now = dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc)
        assert public_tokens.resolve(w.db, token, "portal", now=now) is None
        event.event_date = dt.date(2026, 2, 20)  # moved later; link not re-sent
        w.db.flush()
        assert public_tokens.resolve(w.db, token, "portal", now=now) is not None

    def test_revoke_kills_the_link_and_the_next_mint_is_new(self, w):
        enquiry = _enquiry(w)
        old = _token(public_tokens.mint_quote_link(w.db, w.ctx(), enquiry.id))
        assert public_tokens.revoke(w.db, w.ctx(), enquiry_id=enquiry.id) == 1
        assert public_tokens.resolve(w.db, old, "quote") is None
        new = _token(public_tokens.mint_quote_link(w.db, w.ctx(), enquiry.id))
        assert new != old
        assert public_tokens.resolve(w.db, new, "quote") is not None

    def test_revoking_a_portal_link_ends_its_sessions(self, w):
        event = _event(w)
        public_tokens.mint_portal_link(w.db, w.ctx(), event.id, ["pat@example.com"])
        session = FnPortalSession(
            organization_id=w.org.id,
            session_hash="h" * 64,
            event_id=event.id,
            email="pat@example.com",
            expires_at=_now() + dt.timedelta(days=1),
        )
        w.db.add(session)
        w.db.flush()
        public_tokens.revoke(w.db, w.ctx(), event_id=event.id)
        w.db.refresh(session)
        assert session.revoked_at is not None

    def test_a_rotated_key_issues_a_new_link_and_keeps_the_old_one(
        self, w, monkeypatch
    ):
        enquiry = _enquiry(w)
        old = _token(public_tokens.mint_quote_link(w.db, w.ctx(), enquiry.id))
        monkeypatch.setattr(settings, "JWT_SECRET", "a-new-secret")
        new = _token(public_tokens.mint_quote_link(w.db, w.ctx(), enquiry.id))
        assert new != old
        # Links already sent keep working; the new one is stable from now on.
        assert public_tokens.resolve(w.db, old, "quote") is not None
        assert public_tokens.resolve(w.db, new, "quote") is not None
        assert _token(public_tokens.mint_quote_link(w.db, w.ctx(), enquiry.id)) == new

    def test_links_listing_rederives_urls(self, w):
        enquiry = _enquiry(w)
        url = public_tokens.mint_quote_link(w.db, w.ctx(), enquiry.id)
        listed = public_tokens.links(w.db, w.ctx(), enquiry_id=enquiry.id)
        assert [entry["url"] for entry in listed] == [url]
        with pytest.raises(NotFound):
            public_tokens.links(w.db, w.ctx(w.other_user), enquiry_id=enquiry.id)


def test_tokens_in_text_finds_quote_links():
    token = "A" * 40 + "b-_"
    body = (
        f'<p>Your proposal: <a href="https://bettercallnorm.com/q/{token}">view</a> '
        f"and again https://bettercallnorm.com/q/{token}. Portal: /e/{token}</p>"
    )
    assert public_tokens.tokens_in_text(body, "quote") == [token]
    assert public_tokens.tokens_in_text(body, "portal") == [token]
    assert public_tokens.tokens_in_text(f"/q/{token}x", "quote") == []
    assert public_tokens.tokens_in_text(None) == []
