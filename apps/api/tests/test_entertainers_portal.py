"""The performer's link: off in shadow; the render contract carries no
amount, rate, bank, IRD or contact detail; confirm and did-not-perform;
the mismatch email; PDF magic and parse; size and per-link quotas; headers;
revocation on deactivation and mark-paid; rate limits.
"""

from __future__ import annotations

import datetime as dt
import io

import pytest

from app.db.entertainers_models import EntEmailLink, EntFile, EntPerformance
from app.services.entertainers import payments, performers, tokens
from app.services.finance_core import modes
from tests.entertainers_world import (
    UTC,
    add_performer,
    approved,
    assert_no_sensitive,
    make_world,
    portal_link,
    walk,
)

HEADERS = {
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
    "Cache-Control": "no-store",
}


def _pdf() -> bytes:
    from pypdf import PdfWriter

    out = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(out)
    return out.getvalue()


def _png() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def w(db_session):
    return make_world(db_session)


@pytest.fixture
def world(w):
    performer = add_performer(w.db, w.org, lane="invoice")
    past = approved(
        w.db,
        w.org,
        w.venue,
        performer,
        date=dt.date(2026, 10, 10),
        rate_cents=12_000,
        cents=48_000,
    )
    from tests.entertainers_world import add_performance

    future = add_performance(
        w.db, w.org, w.venue2, performer, date=dt.date(2099, 1, 1), rate_cents=15_000
    )
    token, url = portal_link(w.db, w.org, performer)
    return w, performer, past, future, token


class TestRenderContract:
    def test_page_lists_performances_with_no_money_or_contact_details(
        self, client, world
    ):
        w, performer, past, future, token = world
        r = client.get(f"/api/public/entertainers/{token}")
        assert r.status_code == 200, r.text
        for key, value in HEADERS.items():
            assert r.headers[key] == value
        body = r.json()
        assert (
            body["performer_name"] == "Sam Smith" and body["can_upload_invoice"] is True
        )
        assert [p["id"] for p in body["recent"]] == [past.id]
        assert [p["id"] for p in body["upcoming"]] == [future.id]
        row = body["recent"][0]
        assert (
            row["venue_name"] == "Brew Bar"
            and row["start_time"] == "21:00"
            and row["status"] == "awaiting_your_confirmation"
        )
        assert row["can_confirm"] is True
        for path, value in walk(body, []):
            leaf = path.rsplit(".", 1)[-1]
            assert (
                "cents" not in leaf and "rate" not in leaf and "amount" not in leaf
            ), path
            assert leaf not in (
                "email",
                "phone",
                "bank_account",
                "ird_number",
                "date_of_birth",
            ), path
            if isinstance(value, str):
                assert value not in (
                    "sam@example.test",
                    "021 000 000",
                    "12-3456-7890138-00",
                    "049091850",
                ), path
        assert (
            "48000" not in r.text and "480.00" not in r.text and "12000" not in r.text
        )
        assert_no_sensitive(body)

    def test_off_in_shadow_with_not_yet(self, db_session, client):
        w = make_world(db_session, mode="shadow")
        performer = add_performer(w.db, w.org)
        token, _url = portal_link(w.db, w.org, performer)
        r = client.get(f"/api/public/entertainers/{token}")
        assert r.status_code == 404 and r.json()["detail"]["code"] == "not_yet"
        assert r.headers["X-Robots-Tag"] == "noindex, nofollow"
        modes.set_mode(w.db, w.org.id, "entertainers", "off")
        assert (
            client.get(f"/api/public/entertainers/{token}").json()["detail"]["code"]
            == "not_found"
        )

    def test_a_bad_link_is_404_with_headers(self, client):
        r = client.get("/api/public/entertainers/" + "B" * 43)
        assert r.status_code == 404 and r.headers["Referrer-Policy"] == "no-referrer"


class TestConfirm:
    def test_confirm_records_times_and_matching_is_ready_for_payment(
        self, client, world
    ):
        w, performer, past, _future, token = world
        r = client.post(
            f"/api/public/entertainers/{token}/performances/{past.id}/confirm",
            json={"actual_start_time": "21:05", "actual_end_time": "01:00"},
        )
        assert r.status_code == 200, r.text
        row = r.json()["recent"][0]
        assert row["status"] == "confirmed" and row["your_times"]["start"] == "21:05"
        w.db.refresh(past)
        assert past.performer_confirmed_at is not None
        assert (
            w.db.query(EntEmailLink).filter(EntEmailLink.kind == "ent_mismatch").count()
            == 0
        )
        changes = {
            c.field
            for c in past.__class__.__mro__
            and __import__(
                "app.db.entertainers_models", fromlist=["x"]
            ).EntPerformanceChange.__mro__
            and w.db.query(
                __import__(
                    "app.db.entertainers_models", fromlist=["x"]
                ).EntPerformanceChange
            ).filter_by(performance_id=past.id)
        }
        assert "performer_actual_start_time" in changes

    def test_differing_times_show_times_differ_and_email_the_performer(
        self, client, world
    ):
        w, performer, past, _future, token = world
        r = client.post(
            f"/api/public/entertainers/{token}/performances/{past.id}/confirm",
            json={"actual_start_time": "21:30", "actual_end_time": "01:00"},
        )
        assert r.status_code == 200
        assert r.json()["recent"][0]["status"] == "times_differ"
        link = (
            w.db.query(EntEmailLink).filter(EntEmailLink.kind == "ent_mismatch").one()
        )
        assert link.recipient_email == "sam@example.test" and link.performance_ids == [
            past.id
        ]

    def test_did_not_perform(self, client, world):
        w, performer, past, _future, token = world
        r = client.post(
            f"/api/public/entertainers/{token}/performances/{past.id}/did-not-perform",
            json={"reason": "venue closed"},
        )
        assert (
            r.status_code == 200
            and r.json()["recent"][0]["status"] == "did_not_perform"
        )

    def test_cannot_confirm_an_upcoming_or_another_performers_performance(
        self, client, world
    ):
        w, performer, past, future, token = world
        r = client.post(
            f"/api/public/entertainers/{token}/performances/{future.id}/confirm",
            json={"actual_start_time": "21:00", "actual_end_time": "01:00"},
        )
        assert r.status_code == 409
        other = add_performer(w.db, w.org, name="Other", email="o@example.test")
        theirs = approved(w.db, w.org, w.venue, other)
        r = client.post(
            f"/api/public/entertainers/{token}/performances/{theirs.id}/confirm",
            json={"actual_start_time": "21:00", "actual_end_time": "01:00"},
        )
        assert r.status_code == 404


class TestUploads:
    def _upload(
        self,
        client,
        token,
        perf_id,
        data=None,
        name="inv.pdf",
        ctype="application/pdf",
        **fields,
    ):
        return client.post(
            f"/api/public/entertainers/{token}/invoices",
            files={"file": (name, data if data is not None else _pdf(), ctype)},
            data={"performance_ids": f'["{perf_id}"]', **fields},
        )

    def test_a_pdf_lands_in_ent_files_against_the_performance_and_is_pending(
        self, client, world
    ):
        w, performer, past, _future, token = world
        r = self._upload(
            client, token, past.id, invoice_number="INV-7", invoice_amount="480.00"
        )
        assert r.status_code == 200, r.text
        assert r.json()["recent"][0]["invoice"] == {"uploaded": True, "number": "INV-7"}
        assert "480" not in r.text
        file = w.db.query(EntFile).one()
        assert (
            file.kind == "invoice"
            and file.check_status == "pending"
            and file.invoice_cents == 48_000
        )
        w.db.refresh(past)
        assert past.invoice_file_id == file.id

    def test_a_renamed_non_pdf_is_refused_by_magic_and_parse(self, client, world):
        w, performer, past, _future, token = world
        assert (
            self._upload(
                client, token, past.id, data=b"%PDF-1.4 not really a pdf"
            ).status_code
            == 400
        )
        assert (
            self._upload(
                client, token, past.id, data=b"<html>hi</html>", ctype="application/pdf"
            ).status_code
            == 400
        )
        assert (
            self._upload(
                client,
                token,
                past.id,
                data=b"%PDF-1.4\n/JavaScript (app.alert(1))",
                ctype="application/pdf",
            ).status_code
            == 400
        )
        assert w.db.query(EntFile).count() == 0

    def test_an_image_goes_through_sniff_image(self, client, world):
        w, performer, past, _future, token = world
        r = self._upload(
            client, token, past.id, data=_png(), name="photo.png", ctype="image/png"
        )
        assert r.status_code == 200, r.text
        assert w.db.query(EntFile).one().content_type == "image/png"
        assert (
            self._upload(
                client,
                token,
                past.id,
                data=b"<svg/>",
                name="x.svg",
                ctype="image/svg+xml",
            ).status_code
            == 400
        )

    def test_size_cap(self, client, world):
        w, performer, past, _future, token = world
        big = _pdf() + b"%" + b"0" * (10 * 1024 * 1024)
        r = self._upload(client, token, past.id, data=big)
        assert r.status_code in (400, 413)

    def test_only_the_performers_own_approved_performances(self, client, world):
        w, performer, past, future, token = world
        assert self._upload(client, token, future.id).status_code == 400
        other = add_performer(w.db, w.org, name="Other", email="o@example.test")
        theirs = approved(w.db, w.org, w.venue, other)
        assert self._upload(client, token, theirs.id).status_code == 404

    def test_five_uploads_per_link_per_day(self, client, world):
        w, performer, past, _future, token = world
        for i in range(5):
            data = _pdf() + b"\n%" + str(i).encode()
            assert self._upload(client, token, past.id, data=data).status_code == 200, i
        r = self._upload(client, token, past.id, data=_pdf() + b"\n%six")
        assert r.status_code == 429
        assert (
            client.get(f"/api/public/entertainers/{token}").json()["uploads_left_today"]
            == 0
        )


class TestRevocation:
    def test_mark_paid_revokes_the_portal_link(self, client, world):
        w, performer, past, _future, token = world
        past.performer_confirmed_at = dt.datetime(2026, 10, 11, tzinfo=UTC)
        past.performer_actual_start_time, past.performer_actual_end_time = (
            past.start_time,
            past.end_time,
        )
        w.db.flush()
        assert client.get(f"/api/public/entertainers/{token}").status_code == 200
        payments.mark_paid(w.db, w.ctx(), past.id, reference="paid", version=None)
        assert client.get(f"/api/public/entertainers/{token}").status_code == 404
        assert tokens.resolve(w.db, token, purposes=("portal",)) is None

    def test_deactivation_revokes_the_portal_link(self, client, world):
        w, performer, past, _future, token = world
        performers.deactivate(w.db, w.ctx(), performer.id)
        assert client.get(f"/api/public/entertainers/{token}").status_code == 404

    def test_the_portal_link_is_stable_until_revoked(self, world):
        w, performer, past, _future, token = world
        _row, again, _url = tokens.mint(
            w.db, org_id=w.org.id, purpose="portal", performer_id=performer.id
        )
        assert again == token
        tokens.revoke_for_performer(w.db, w.org.id, performer.id, purposes=("portal",))
        _row, fresh, _url = tokens.mint(
            w.db, org_id=w.org.id, purpose="portal", performer_id=performer.id
        )
        assert fresh != token


class TestRateLimit:
    def test_views_are_limited_per_ip(self, client, world):
        w, performer, past, _future, token = world
        statuses = {
            client.get(
                f"/api/public/entertainers/{token}",
                headers={"x-forwarded-for": "203.0.113.9, 10.0.0.1"},
            ).status_code
            for _ in range(121)
        }
        assert 429 in statuses
        r = client.get(
            f"/api/public/entertainers/{token}",
            headers={"x-forwarded-for": "203.0.113.9, 10.0.0.1"},
        )
        assert (
            r.status_code == 429
            and r.headers.get("Retry-After")
            and r.headers["X-Robots-Tag"] == "noindex, nofollow"
        )


def test_performance_status_in_portal_never_leaks_cents(world):
    w, performer, past, _future, token = world
    from app.services.entertainers import portal

    row, perf = portal.resolve(w.db, token)
    page = portal.page(w.db, row, perf)
    assert isinstance(past, EntPerformance)
    assert "actual_cents" not in str(page) and "agreed_cents" not in str(page)
