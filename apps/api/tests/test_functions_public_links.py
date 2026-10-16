"""Taking customer access away: revoking quote and portal links, removing one
email from a portal, the portal closing with a cancelled event, and the
lockout after too many wrong codes (second security review).

Staff endpoints live in routers/functions.py; the rules in public_tokens.py
and portal.py.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace

import pytest

from app.auth.security import create_access_token
from app.db.functions_models import FnPortalAccess, FnPortalSession
from app.services.functions import enquiries, events, notify, public_tokens
from app.services.functions import portal as portal_svc
from tests.functions_world import add_contact, make_world

DATE = dt.date(2026, 12, 4)


def _auth(user):
    return {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}


@pytest.fixture
def codes(monkeypatch):
    sent: list[tuple[str, str]] = []

    def fake(*, to, code, venue_name, event_id):
        sent.append((to, code))
        return notify.Delivery("sent")

    monkeypatch.setattr(notify, "portal_code", fake)
    return sent


@pytest.fixture
def s(db_session, client, codes):
    client.headers["X-Forwarded-For"] = f"192.0.2.9-{uuid.uuid4().hex[:8]}"
    client.base_url = "https://testserver"  # the portal cookie is Secure in CI
    w = make_world(db_session)
    staff = w.ctx(w.owner)
    contact = add_contact(
        db_session, w.org.id, name="Pat Planner", email="pat@example.com"
    )
    event = events.create_event(
        db_session,
        staff,
        venue_id=w.venue.id,
        contact_id=contact.id,
        event_date=DATE,
        booking_type="group_booking",
        name="Pat's farewell",
    )
    portal_url = public_tokens.mint_portal_link(
        db_session, staff, event.id, ["pat@example.com"]
    )
    enquiry = enquiries.create_enquiry(
        db_session,
        staff,
        contact={"name": "Casey", "email": "casey@example.com"},
        venue_id=w.venue.id,
        booking_type="large_event_exclusive",
    )
    quote_url = public_tokens.mint_quote_link(db_session, staff, enquiry.id)
    db_session.flush()
    token = portal_url.rsplit("/", 1)[1]
    return SimpleNamespace(
        w=w,
        db=db_session,
        event=event,
        enquiry=enquiry,
        token=token,
        base=f"/api/public/portal/{token}",
        quote_token=quote_url.rsplit("/", 1)[1],
        codes=codes,
    )


def _code(client, s, email="pat@example.com"):
    assert client.post(f"{s.base}/code", json={"email": email}).status_code == 202


def _sign_in(client, s, email="pat@example.com"):
    _code(client, s, email)
    r = client.post(f"{s.base}/verify", json={"email": email, "code": s.codes[-1][1]})
    assert r.status_code == 200, r.text


class TestRevokingLinks:
    def test_staff_revoke_a_quote_link(self, client, s):
        assert client.get(f"/api/public/quote/{s.quote_token}").status_code == 200
        r = client.post(
            f"/api/functions/enquiries/{s.enquiry.id}/quote-link/revoke",
            headers=_auth(s.w.owner),
        )
        assert r.status_code == 200, r.text
        assert r.json()["revoked"] == 1
        assert r.json()["quote_links"][0]["revoked_at"] is not None
        assert client.get(f"/api/public/quote/{s.quote_token}").status_code == 404

    def test_staff_revoke_a_portal_link_and_sign_everyone_out(self, client, s):
        _sign_in(client, s)
        r = client.post(
            f"/api/functions/events/{s.event.id}/portal-link/revoke",
            headers=_auth(s.w.owner),
        )
        assert r.status_code == 200 and r.json()["revoked"] == 1
        assert client.get(s.base).status_code == 404
        session = s.db.query(FnPortalSession).filter_by(event_id=s.event.id).one()
        s.db.refresh(session)
        assert session.revoked_at is not None


class TestPortalAccess:
    def test_removing_an_email_signs_it_out_and_stops_codes(self, client, s):
        _sign_in(client, s)
        r = client.delete(
            f"/api/functions/events/{s.event.id}/portal-access/Pat@Example.com",
            headers=_auth(s.w.owner),
        )
        assert r.status_code == 200, r.text
        (person,) = r.json()["people"]
        assert person["allowed"] is False and person["removed_at"]
        assert person["locked_for_wrong_codes"] is False
        assert client.get(s.base).json()["authenticated"] is False
        sent_before = len(s.codes)
        _code(client, s)  # same generic answer...
        assert len(s.codes) == sent_before  # ...and no code
        # Staff allow it back.
        r = client.post(
            f"/api/functions/events/{s.event.id}/portal-access",
            json={"email": "pat@example.com"},
            headers=_auth(s.w.owner),
        )
        assert r.status_code == 200 and r.json()["people"][0]["allowed"] is True
        _sign_in(client, s)

    def test_removing_an_unknown_email_is_404(self, client, s):
        r = client.delete(
            f"/api/functions/events/{s.event.id}/portal-access/nobody@example.com",
            headers=_auth(s.w.owner),
        )
        assert r.status_code == 404

    def test_ten_wrong_codes_lock_the_email_until_staff_allow_it(self, client, s):
        wrong = 0
        for _ in range(2):  # two codes, five wrong tries each
            _code(client, s)
            code = s.codes[-1][1]
            guess = "000000" if code != "000000" else "111111"
            for _ in range(portal_svc.OTP_MAX_ATTEMPTS):
                r = client.post(
                    f"{s.base}/verify", json={"email": "pat@example.com", "code": guess}
                )
                assert r.status_code == 400
                assert (
                    r.json()["detail"]["code"] == "bad_code"
                )  # same answer throughout
                wrong += 1
        assert wrong == portal_svc.FAILS_PER_DAY
        access = s.db.query(FnPortalAccess).filter_by(event_id=s.event.id).one()
        s.db.refresh(access)
        assert access.revoked_at is not None
        sent_before = len(s.codes)
        _code(client, s)
        assert len(s.codes) == sent_before  # no more codes to the client's inbox
        people = client.get(
            f"/api/functions/events/{s.event.id}/portal-access",
            headers=_auth(s.w.owner),
        ).json()["people"]
        assert people[0]["locked_for_wrong_codes"] is True
        r = client.post(
            f"/api/functions/events/{s.event.id}/portal-access",
            json={"email": "pat@example.com"},
            headers=_auth(s.w.owner),
        )
        assert r.status_code == 200
        assert r.json()["people"][0]["locked_for_wrong_codes"] is False
        _sign_in(client, s)

    def test_fewer_wrong_codes_do_not_lock(self, client, s):
        _code(client, s)
        code = s.codes[-1][1]
        guess = "000000" if code != "000000" else "111111"
        for _ in range(3):
            client.post(
                f"{s.base}/verify", json={"email": "pat@example.com", "code": guess}
            )
        r = client.post(
            f"{s.base}/verify", json={"email": "pat@example.com", "code": code}
        )
        assert r.status_code == 200


class TestScope:
    ENDPOINTS = (
        ("post", "/api/functions/enquiries/{enquiry}/quote-link/revoke", None),
        ("post", "/api/functions/events/{event}/portal-link/revoke", None),
        ("get", "/api/functions/events/{event}/portal-access", None),
        (
            "post",
            "/api/functions/events/{event}/portal-access",
            {"email": "x@example.com"},
        ),
        ("delete", "/api/functions/events/{event}/portal-access/pat@example.com", None),
    )

    def _call(self, client, s, user, method, path, body):
        url = path.format(enquiry=s.enquiry.id, event=s.event.id)
        kwargs = {"headers": _auth(user)}
        if body is not None:
            kwargs["json"] = body
        return getattr(client, method)(url, **kwargs)

    @pytest.mark.parametrize("method,path,body", ENDPOINTS)
    def test_another_organisation_gets_404(self, client, s, method, path, body):
        assert (
            self._call(client, s, s.w.other_user, method, path, body).status_code == 404
        )
        assert public_tokens.resolve(s.db, s.token, "portal") is not None

    def test_staff_without_the_venue_get_404(self, client, s):
        contact = add_contact(s.db, s.w.org.id, name="Gail", email="gail@example.com")
        garden = events.create_event(
            s.db,
            s.w.ctx(s.w.owner),
            venue_id=s.w.venue2.id,
            contact_id=contact.id,
            event_date=DATE,
            booking_type="group_booking",
        )
        public_tokens.mint_portal_link(
            s.db, s.w.ctx(s.w.owner), garden.id, ["gail@example.com"]
        )
        for method, path, body in self.ENDPOINTS[1:]:
            url = path.format(event=garden.id).replace("pat@", "gail@")
            kwargs = {"headers": _auth(s.w.staff)}
            if body is not None:
                kwargs["json"] = body
            assert getattr(client, method)(url, **kwargs).status_code == 404, url

    def test_shadow_mode_refuses_the_writes(self, client, s):
        s.w.org.functions_mode = "shadow"
        s.db.flush()
        for method, path, body in self.ENDPOINTS:
            r = self._call(client, s, s.w.owner, method, path, body)
            if method == "get":
                assert r.status_code == 200
            else:
                assert r.status_code == 409 and r.json()["detail"]["code"] == "not_live"
