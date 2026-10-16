"""The quote page API (``/api/public/quote/<token>``) and public images.

The owner's rule runs through it: the customer confirming an option on this
page creates the event — exactly once, only for an option the sent quote
offered, only while it still says what the quote said. Every way a link can
be wrong answers the same 404, and nothing works unless the organisation is
live.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from types import SimpleNamespace

import pytest

from app.db.functions_models import (
    FnEvent,
    FnFile,
    FnMinSpendResponse,
    FnProposal,
    FnProposalEvent,
    FnProposalImage,
    FnProposalVersion,
    FnVenueAreaPhoto,
    FnVenueBrand,
    FnVenueFunctionSettings,
)
from app.db.models import EmailLog, _now
from app.services.functions import enquiries, min_spends, public_tokens
from tests.functions_world import make_world

DATE = dt.date(2026, 12, 4)


@pytest.fixture
def ip(client):
    """A fresh client IP per test, so rate-limit windows never collide with
    another test (or another run on the shared database)."""
    value = f"203.0.113.{uuid.uuid4().int % 250}-{uuid.uuid4().hex[:8]}"
    client.headers["X-Forwarded-For"] = value
    return value


def _snap(option, area_name=None):
    return {
        "min_spend_id": option.id,
        "label": option.label,
        "venue_id": option.venue_id,
        "area_id": option.area_id,
        "area_name": area_name,
        "event_date": option.event_date.isoformat(),
        "start_time": option.start_time.isoformat(),
        "end_time": option.end_time.isoformat(),
        "ends_next_day": option.ends_next_day,
        "amount_cents": option.amount_cents,
        "food_cents": 0,
        "total_cents": option.amount_cents,
    }


def _option(w, enquiry, area, amount, label):
    return min_spends.add_min_spend(
        w.db,
        w.ctx(w.owner),
        enquiry_id=enquiry.id,
        venue_id=w.venue.id,
        area_id=area.id,
        amount_cents=amount,
        label=label,
        event_date=DATE,
        start_time=dt.time(19, 0),
        end_time=dt.time(23, 30),
    )


@pytest.fixture
def q(db_session, client, ip):
    w = make_world(db_session)
    staff = w.ctx(w.owner)
    enquiry = enquiries.create_enquiry(
        db_session,
        staff,
        contact={"name": "Casey Client", "email": "casey@example.com"},
        venue_id=w.venue.id,
        booking_type="large_event_exclusive",
        guests=60,
        subject="Casey's 40th",
        owner_user_id=w.staff.id,
    )
    snug = _option(w, enquiry, w.snug, 400_000, "The Snug")  # no approval needed
    deck = _option(w, enquiry, w.deck, 900_000, "The Deck")
    min_spends.approve_min_spend(db_session, w.ctx(w.approver), deck.id)
    pending = _option(w, enquiry, w.deck, 1_200_000, "Whole venue")  # unapproved
    settings_row = (
        db_session.query(FnVenueFunctionSettings).filter_by(venue_id=w.venue.id).one()
    )
    settings_row.notify_emails = ["events@brewbar.example"]
    proposal = FnProposal(
        organization_id=w.org.id,
        enquiry_id=enquiry.id,
        html="<h1>Hi Casey</h1>",
        css="h1{color:navy}",
    )
    db_session.add(proposal)
    db_session.flush()
    version = FnProposalVersion(
        organization_id=w.org.id,
        proposal_id=proposal.id,
        enquiry_id=enquiry.id,
        version_number=1,
        html="<h1>Hi Casey</h1><p onclick='x()'>Your night</p><script>alert(1)</script>",
        css="h1{color:navy;background:url(https://evil.example/t.png)}",
        options=[_snap(snug, "Snug"), _snap(deck, "Deck"), _snap(pending, "Deck")],
        sent_at=_now() - dt.timedelta(days=1),
    )
    db_session.add(version)
    db_session.flush()
    url = public_tokens.mint_quote_link(db_session, staff, enquiry.id)
    return SimpleNamespace(
        w=w,
        db=db_session,
        enquiry=enquiry,
        snug=snug,
        deck=deck,
        pending=pending,
        proposal=proposal,
        version=version,
        token=url.rsplit("/", 1)[1],
    )


def _get(client, q):
    return client.get(f"/api/public/quote/{q.token}")


def _post(client, q, option_id, kind="confirm", **body):
    return client.post(
        f"/api/public/quote/{q.token}/options/{option_id}/{kind}", json=body or {}
    )


def _emails(ns, template):
    """This test's own email log (the shared dev database has others')."""
    return (
        ns.db.query(EmailLog)
        .filter(
            EmailLog.template_name == f"functions:{template}",
            EmailLog.organization_id == ns.w.org.id,
        )
        .all()
    )


class TestQuotePage:
    def test_shows_the_sent_proposal_sanitised_with_option_states(self, client, q):
        r = _get(client, q)
        assert r.status_code == 200, r.text
        body = r.json()
        doc = body["proposal"]["document"]
        assert "Hi Casey" in doc and "Your night" in doc
        assert (
            "<script" not in doc and "onclick" not in doc and "evil.example" not in doc
        )
        assert "Content-Security-Policy" in doc
        assert body["proposal"]["version_number"] == 1
        assert body["enquiry"]["contact_first_name"] == "Casey"
        assert body["venue"]["name"] == "Brew Bar"
        states = {o["label"]: (o["state"], o["can_confirm"]) for o in body["options"]}
        assert states == {
            "The Snug": ("available", True),
            "The Deck": ("available", True),
            "Whole venue": ("unavailable", False),
        }
        assert body["options"][0]["amount_text"] == "$4,000"
        assert body["money"] == {"currency": "NZD", "tax_label": "incl. GST"}

    def test_public_headers(self, client, q):
        for r in (_get(client, q), client.get("/api/public/quote/nope")):
            assert r.headers["cache-control"] == "no-store"
            assert r.headers["referrer-policy"] == "no-referrer"
            assert "noindex" in r.headers["x-robots-tag"]
            assert r.headers["x-content-type-options"] == "nosniff"

    def test_views_are_recorded_at_most_hourly(self, client, q):
        _get(client, q)
        _get(client, q)
        views = (
            q.db.query(FnProposalEvent)
            .filter_by(enquiry_id=q.enquiry.id, kind="viewed")
            .all()
        )
        assert len(views) == 1
        assert views[0].source == "quote_page"
        assert views[0].proposal_version_id == q.version.id
        views[0].occurred_at = _now() - dt.timedelta(hours=2)
        q.db.flush()
        _get(client, q)
        assert (
            q.db.query(FnProposalEvent)
            .filter_by(enquiry_id=q.enquiry.id, kind="viewed")
            .count()
            == 2
        )

    def test_valid_until_is_the_venues_date(self, client, q):
        # 20:00 UTC on the 11th is 09:00 on the 12th in Auckland.
        q.version.sent_at = dt.datetime(2026, 10, 11, 20, 0, tzinfo=dt.timezone.utc)
        q.db.flush()
        assert _get(client, q).json()["proposal"]["valid_until"] == "2026-10-26"

    def test_no_sent_version_yet_shows_no_options(self, client, q):
        q.version.sent_at = None
        q.db.flush()
        body = _get(client, q).json()
        assert body["proposal"] is None and body["options"] == []


class TestConfirm:
    def test_confirm_creates_exactly_one_event_and_emails_staff_once(self, client, q):
        first = _post(client, q, q.deck.id, note="See you there!")
        assert first.status_code == 200, first.text
        second = _post(client, q, q.deck.id)
        assert second.status_code == 200, second.text
        events = q.db.query(FnEvent).filter_by(enquiry_id=q.enquiry.id).all()
        assert len(events) == 1
        assert events[0].created_via == "min_spend"
        assert events[0].confirmation_channel == "quote_page"
        response = (
            q.db.query(FnMinSpendResponse).filter_by(min_spend_id=q.deck.id).one()
        )
        assert response.channel == "quote_page" and response.note == "See you there!"
        token_row = public_tokens.lookup(q.db, q.token, "quote")
        assert response.public_token_id == token_row.id
        options = {o["label"]: o["state"] for o in second.json()["options"]}
        assert options["The Deck"] == "confirmed"
        assert options["The Snug"] == "withdrawn"
        assert second.json()["booked"] is True
        sent = _emails(q, "quote_confirm")
        assert len(sent) == 1
        assert sorted(sent[0].to_addresses) == sorted(
            ["events@brewbar.example", q.w.staff.email.lower()]
        )
        assert sent[0].status == "suppressed"  # outside production

    def test_a_second_option_is_refused_politely(self, client, q):
        assert _post(client, q, q.deck.id).status_code == 200
        r = _post(client, q, q.snug.id)
        assert r.status_code == 409
        assert "already confirmed another option" in r.json()["detail"]["message"]
        assert q.db.query(FnEvent).filter_by(enquiry_id=q.enquiry.id).count() == 1

    def test_unapproved_option_cannot_be_confirmed(self, client, q):
        r = _post(client, q, q.pending.id)
        assert r.status_code == 403
        assert q.db.query(FnEvent).filter_by(enquiry_id=q.enquiry.id).count() == 0

    def test_option_not_on_the_sent_quote_is_404(self, client, q):
        extra = _option(q.w, q.enquiry, q.w.snug, 100_000, "Added later")
        assert _post(client, q, extra.id).status_code == 404

    def test_another_organisations_option_is_404(self, client, q):
        w = q.w
        rival = enquiries.create_enquiry(
            q.db,
            w.ctx(w.other_user),
            contact={"name": "Rex", "email": "rex@example.com"},
            venue_id=w.other_venue.id,
            booking_type="large_event_exclusive",
        )
        theirs = min_spends.add_min_spend(
            q.db,
            w.ctx(w.other_user),
            enquiry_id=rival.id,
            venue_id=w.other_venue.id,
            amount_cents=1,
            event_date=DATE,
        )
        q.version.options = list(q.version.options) + [{"min_spend_id": theirs.id}]
        q.db.flush()
        assert _post(client, q, theirs.id).status_code == 404
        assert q.db.query(FnEvent).filter_by(enquiry_id=rival.id).count() == 0

    def test_an_option_changed_since_sending_cannot_be_confirmed(self, client, q):
        min_spends.update_min_spend(
            q.db, q.w.ctx(q.w.owner), q.snug.id, amount_cents=350_000
        )
        body = _get(client, q).json()
        snug = next(o for o in body["options"] if o["label"] == "The Snug")
        assert snug["changed_since_sent"] is True and snug["can_confirm"] is False
        r = _post(client, q, q.snug.id)
        assert r.status_code == 409
        assert "changed since your quote" in r.json()["detail"]["message"]

    def test_decline_is_recorded_once_and_does_not_block_a_later_yes(self, client, q):
        assert (
            _post(client, q, q.snug.id, "decline", note="Too small").status_code == 200
        )
        r = _post(client, q, q.snug.id, "decline")
        assert r.status_code == 200
        declines = (
            q.db.query(FnMinSpendResponse).filter_by(min_spend_id=q.snug.id).all()
        )
        assert [d.kind for d in declines] == ["decline"]
        assert len(_emails(q, "quote_decline")) == 1
        snug = next(o for o in r.json()["options"] if o["label"] == "The Snug")
        assert snug["state"] == "declined" and snug["can_confirm"] is True
        assert _post(client, q, q.snug.id).status_code == 200

    def test_declining_a_confirmed_option_is_refused(self, client, q):
        _post(client, q, q.deck.id)
        r = _post(client, q, q.deck.id, "decline")
        assert r.status_code == 409
        assert "contact the venue" in r.json()["detail"]["message"]

    def test_confirms_are_rate_limited(self, client, q):
        codes = [_post(client, q, q.pending.id).status_code for _ in range(21)]
        assert codes[:20] == [403] * 20
        assert codes[20] == 429


class TestDeadLinks:
    def _same_404(self, r):
        assert r.status_code == 404
        return r.json()

    def test_unknown_revoked_expired_and_not_live_all_look_the_same(self, client, q):
        unknown = self._same_404(client.get("/api/public/quote/" + "x" * 43))
        q.w.org.functions_mode = "shadow"
        q.db.flush()
        shadow = self._same_404(_get(client, q))
        assert self._same_404(_post(client, q, q.deck.id)) == shadow
        q.w.org.functions_mode = "off"
        q.db.flush()
        off = self._same_404(_get(client, q))
        q.w.org.functions_mode = "live"
        q.db.flush()
        row = public_tokens.lookup(q.db, q.token, "quote")
        row.expires_at = _now() - dt.timedelta(minutes=1)
        q.db.flush()
        expired = self._same_404(_get(client, q))
        row.expires_at = _now() + dt.timedelta(days=1)
        row.revoked_at = _now()
        q.db.flush()
        revoked = self._same_404(_get(client, q))
        row.revoked_at = None
        q.enquiry.archived_at = _now()
        q.db.flush()
        archived = self._same_404(_get(client, q))
        assert unknown == shadow == off == expired == revoked == archived
        assert q.db.query(FnEvent).filter_by(enquiry_id=q.enquiry.id).count() == 0

    def test_a_portal_token_is_not_a_quote_token(self, client, q):
        assert _post(client, q, q.deck.id).status_code == 200
        event = q.db.query(FnEvent).filter_by(enquiry_id=q.enquiry.id).one()
        portal_url = public_tokens.mint_portal_link(
            q.db, q.w.ctx(q.w.owner), event.id, []
        )
        portal_token = portal_url.rsplit("/", 1)[1]
        assert client.get(f"/api/public/quote/{portal_token}").status_code == 404
        assert client.get(f"/api/public/portal/{q.token}").status_code == 404


# ---------------------------------------------------------------------------
# Public images
# ---------------------------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _file(db, org_id, data=PNG, content_type="image/png"):
    row = FnFile(
        organization_id=org_id,
        sha256=hashlib.sha256(data + uuid.uuid4().bytes).hexdigest(),
        content_type=content_type,
        size_bytes=len(data),
        data=data,
    )
    db.add(row)
    db.flush()
    return row


class TestPublicFiles:
    def test_a_brand_logo_is_served_with_immutable_caching(self, client, q):
        logo = _file(q.db, q.w.org.id)
        q.db.add(
            FnVenueBrand(
                organization_id=q.w.org.id, venue_id=q.w.venue.id, logo_file_id=logo.id
            )
        )
        q.db.flush()
        r = client.get(f"/api/public/files/{logo.id}")
        assert r.status_code == 200
        assert r.content == PNG
        assert r.headers["content-type"] == "image/png"
        assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in r.headers["content-security-policy"]

    def test_library_area_photo_and_proposal_images_are_served(self, client, q):
        lib, photo, inline = (_file(q.db, q.w.org.id) for _ in range(3))
        q.db.add(
            FnProposalImage(
                organization_id=q.w.org.id,
                venue_id=q.w.venue.id,
                file_id=lib.id,
                name="Deck",
            )
        )
        q.db.add(
            FnVenueAreaPhoto(
                organization_id=q.w.org.id, area_id=q.w.deck.id, file_id=photo.id
            )
        )
        q.proposal.html = f'<img src="/api/public/files/{inline.id}">'
        q.db.flush()
        for f in (lib, photo, inline):
            assert client.get(f"/api/public/files/{f.id}").status_code == 200

    def test_an_unreferenced_file_is_not_public(self, client, q):
        private = _file(q.db, q.w.org.id)
        assert client.get(f"/api/public/files/{private.id}").status_code == 404

    def test_only_raster_images_and_only_while_functions_is_on(self, client, q):
        svg = _file(q.db, q.w.org.id, b"<svg onload='alert(1)'/>", "image/svg+xml")
        pdf = _file(q.db, q.w.org.id, b"%PDF-1.4", "application/pdf")
        logo = _file(q.db, q.w.org.id)
        q.db.add(
            FnVenueBrand(
                organization_id=q.w.org.id,
                venue_id=q.w.venue.id,
                logo_file_id=logo.id,
                cover_file_id=svg.id,
                closing_file_id=pdf.id,
            )
        )
        q.db.flush()
        assert client.get(f"/api/public/files/{svg.id}").status_code == 404
        assert client.get(f"/api/public/files/{pdf.id}").status_code == 404
        assert client.get(f"/api/public/files/{logo.id}").status_code == 200
        q.w.org.functions_mode = "off"
        q.db.flush()
        assert client.get(f"/api/public/files/{logo.id}").status_code == 404
        assert client.get("/api/public/files/not-a-file").status_code == 404
