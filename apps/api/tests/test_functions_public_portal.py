"""The client portal API (``/api/public/portal/<token>``).

Orbit's portal could be read and written by anyone holding the public anon
key, and "signed in" meant knowing an allowed email. These tests pin down the
replacement: a code by email (hashed, expiring, attempt- and send-limited,
enumeration-safe), a session bound to one event, writes only through F1's
client-scoped services, staff fields and venue-hidden fields refused, and
staff emailed when the client confirms or edits a confirmed event.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace

import pytest

from app.config import settings
from app.db.functions_models import (
    FnEvent,
    FnEventAv,
    FnEventChange,
    FnPortalAccess,
    FnPortalSession,
    FnPreorderMenuItem,
    FnVenueFunctionSettings,
)
from app.db.models import EmailLog, _now
from app.services.functions import events, menus, notify, public_tokens
from tests.functions_world import add_contact, make_world

DATE = dt.date(2026, 12, 4)


@pytest.fixture
def ip(client):
    value = f"198.51.100.{uuid.uuid4().int % 250}-{uuid.uuid4().hex[:8]}"
    client.headers["X-Forwarded-For"] = value
    # Outside local (CI runs as "testing") the session cookie is Secure, which
    # a client only sends back over https — as a browser on the real site does.
    client.base_url = "https://testserver"
    return value


@pytest.fixture
def codes(monkeypatch):
    """Capture the codes the portal would email."""
    sent: list[tuple[str, str]] = []

    def fake(*, to, code, venue_name, event_id):
        sent.append((to, code))
        return notify.Delivery("sent")

    monkeypatch.setattr(notify, "portal_code", fake)
    return sent


def _event(
    w, db, *, name="Pat's farewell", email="pat@example.com", contact_email=None
):
    contact = add_contact(
        db, w.org.id, name="Pat Planner", email=contact_email or email
    )
    event = events.create_event(
        db,
        w.ctx(w.owner),
        venue_id=w.venue.id,
        contact_id=contact.id,
        event_date=DATE,
        booking_type="group_booking",
        name=name,
        guests=30,
        start_time=dt.time(18, 0),
        end_time=dt.time(22, 0),
    )
    menu = menus.create_menu(
        db,
        w.ctx(w.owner),
        venue_id=w.venue.id,
        name="Canapés",
        event_id=event.id,
        items=[
            {"kind": "header", "name": "Hot"},
            {"name": "Arancini", "unit_price_cents": 450},
            {"name": "Sliders", "unit_price_cents": 600},
        ],
    )
    url = public_tokens.mint_portal_link(db, w.ctx(w.owner), event.id, [email])
    return event, menu, url.rsplit("/", 1)[1]


@pytest.fixture
def p(db_session, client, ip, codes):
    w = make_world(db_session)
    settings_row = (
        db_session.query(FnVenueFunctionSettings).filter_by(venue_id=w.venue.id).one()
    )
    settings_row.notify_emails = ["events@brewbar.example"]
    event, menu, token = _event(w, db_session)
    db_session.flush()
    return SimpleNamespace(
        w=w,
        db=db_session,
        event=event,
        menu=menu,
        token=token,
        codes=codes,
        settings=settings_row,
        base=f"/api/public/portal/{token}",
    )


def _request_code(client, p, email="pat@example.com", token=None):
    return client.post(
        f"/api/public/portal/{token or p.token}/code", json={"email": email}
    )


def _sign_in(client, p, email="pat@example.com"):
    assert _request_code(client, p, email).status_code == 202
    to, code = p.codes[-1]
    assert to == email
    r = client.post(f"{p.base}/verify", json={"email": email, "code": code})
    assert r.status_code == 200, r.text
    return r


def _save(client, p, **changes):
    return client.put(f"{p.base}/event", json=changes)


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


class TestSignIn:
    def test_the_link_alone_shows_only_the_venue(self, client, p):
        r = client.get(p.base)
        assert r.status_code == 200
        assert r.json() == {
            "authenticated": False,
            "venue": {"id": p.w.venue.id, "name": "Brew Bar", "brand": None},
        }
        assert "farewell" not in r.text and "Pat" not in r.text
        assert r.headers["cache-control"] == "no-store"

    def test_body_errors_carry_the_public_headers_too(self, client, p):
        r = client.post(f"{p.base}/code", json={})
        assert r.status_code == 422
        assert r.headers["cache-control"] == "no-store"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert p.codes == []

    def test_a_code_signs_in_with_an_httponly_cookie_scoped_to_the_link(
        self, client, p
    ):
        r = _sign_in(client, p)
        cookie = r.headers["set-cookie"]
        assert cookie.startswith("norm_portal=")
        assert "HttpOnly" in cookie
        assert f"Path={p.base}" in cookie
        assert "samesite=lax" in cookie.lower()
        body = r.json()
        assert body["authenticated"] is True and body["email"] == "pat@example.com"
        assert body["event"]["name"] == "Pat's farewell"
        assert client.get(p.base).json()["authenticated"] is True
        session = p.db.query(FnPortalSession).filter_by(event_id=p.event.id).one()
        raw = client.cookies.get("norm_portal")
        assert raw and session.session_hash != raw and raw not in session.session_hash

    def test_unknown_emails_get_the_same_answer_and_no_code(self, client, p):
        known = _request_code(client, p)
        unknown = _request_code(client, p, "stranger@example.com")
        malformed = _request_code(client, p, "not an email")
        assert known.status_code == unknown.status_code == malformed.status_code == 202
        assert known.json() == unknown.json() == malformed.json() == {"sent": True}
        assert [to for to, _ in p.codes] == ["pat@example.com"]

    def test_the_code_is_stored_only_as_a_hash(self, client, p):
        _request_code(client, p)
        _, code = p.codes[-1]
        access = p.db.query(FnPortalAccess).filter_by(event_id=p.event.id).one()
        p.db.refresh(access)
        assert access.otp_hash and code not in access.otp_hash
        assert access.otp_expires_at <= _now() + dt.timedelta(minutes=10)

    def test_codes_are_throttled_per_email(self, client, p):
        for _ in range(4):
            assert _request_code(client, p).status_code == 202
        assert len(p.codes) == 3

    def test_five_wrong_guesses_burn_the_code(self, client, p):
        _request_code(client, p)
        _, code = p.codes[-1]
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            r = client.post(
                f"{p.base}/verify", json={"email": "pat@example.com", "code": wrong}
            )
            assert r.status_code == 400
            assert r.json()["detail"]["code"] == "bad_code"
        r = client.post(
            f"{p.base}/verify", json={"email": "pat@example.com", "code": code}
        )
        assert r.status_code == 400

    def test_an_expired_code_is_refused(self, client, p):
        _request_code(client, p)
        _, code = p.codes[-1]
        access = p.db.query(FnPortalAccess).filter_by(event_id=p.event.id).one()
        access.otp_expires_at = _now() - dt.timedelta(seconds=1)
        p.db.flush()
        r = client.post(
            f"{p.base}/verify", json={"email": "pat@example.com", "code": code}
        )
        assert r.status_code == 400

    def test_a_code_for_one_email_does_not_open_another(self, client, p):
        public_tokens.mint_portal_link(
            p.db, p.w.ctx(p.w.owner), p.event.id, ["boss@example.com"]
        )
        _request_code(client, p)
        _, code = p.codes[-1]
        r = client.post(
            f"{p.base}/verify", json={"email": "boss@example.com", "code": code}
        )
        assert r.status_code == 400

    def test_session_expiry_and_lost_access_sign_you_out(self, client, p):
        _sign_in(client, p)
        session = p.db.query(FnPortalSession).filter_by(event_id=p.event.id).one()
        session.expires_at = _now() - dt.timedelta(seconds=1)
        p.db.flush()
        assert client.get(p.base).json()["authenticated"] is False
        session.expires_at = _now() + dt.timedelta(days=1)
        access = p.db.query(FnPortalAccess).filter_by(event_id=p.event.id).one()
        access.revoked_at = _now()
        p.db.flush()
        assert client.get(p.base).json()["authenticated"] is False

    def test_a_session_opens_only_its_own_event(self, client, p):
        _sign_in(client, p)
        raw = client.cookies.get("norm_portal")
        _other_event, _menu, other_token = _event(
            p.w, p.db, name="Other party", contact_email="other@example.com"
        )
        r = client.get(
            f"/api/public/portal/{other_token}",
            headers={"Cookie": f"norm_portal={raw}"},
        )
        assert r.json()["authenticated"] is False
        r = client.put(
            f"/api/public/portal/{other_token}/event",
            json={"fields": {"guests": 1}},
            headers={"Cookie": f"norm_portal={raw}"},
        )
        assert r.status_code == 401

    def test_logout_ends_the_session(self, client, p):
        _sign_in(client, p)
        assert client.post(f"{p.base}/logout").status_code == 200
        session = p.db.query(FnPortalSession).filter_by(event_id=p.event.id).one()
        p.db.refresh(session)
        assert session.revoked_at is not None
        assert client.get(p.base).json()["authenticated"] is False

    def test_print_needs_a_session(self, client, p):
        assert client.get(f"{p.base}/print").status_code == 401
        _sign_in(client, p)
        r = client.get(f"{p.base}/print")
        assert r.status_code == 200 and r.json()["event"]["name"] == "Pat's farewell"


class TestEditing:
    def test_client_edits_go_through_the_client_services(self, client, p):
        version = _sign_in(client, p).json()["event"]["version"]
        r = _save(
            client,
            p,
            expected_version=version,
            fields={"guests": 45, "allergies": "Two coeliacs", "arrival_time": "18:30"},
            contact={"phone": "021 555 0101"},
            lists={"timings": [{"at_time": "19:00", "activity": "Speeches"}]},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["event"]["guests"] == 45
        assert body["event"]["allergies"] == "Two coeliacs"
        assert body["event"]["arrival_time"] == "18:30:00"
        assert body["event"]["version"] > version
        assert body["contact"]["phone"] == "021 555 0101"
        assert body["lists"]["timings"][0]["activity"] == "Speeches"
        assert body["status"]["status"] == "in_progress"
        mine = (
            p.db.query(FnEventChange)
            .filter_by(event_id=p.event.id, kind="edited", actor_kind="client")
            .all()
        )
        touched = {k for c in mine for k in c.changes}
        assert {
            "guests",
            "allergies",
            "arrival_time",
            "contact.phone",
            "timings",
        } <= touched
        assert all(c.client_email == "pat@example.com" for c in mine)

    @pytest.mark.parametrize(
        "field,value",
        [
            ("bar_tab_cents", 1),
            ("payment_method", "invoice"),
            ("deposit_cents", 0),
            ("event_date", "2026-12-05"),
            ("area_id", None),
            ("name", "Renamed"),
            ("venue_id", "x"),
        ],
    )
    def test_staff_fields_are_refused(self, client, p, field, value):
        _sign_in(client, p)
        r = _save(client, p, fields={field: value})
        assert r.status_code == 403
        p.db.refresh(p.event)
        assert p.event.bar_tab_cents == 0 and p.event.name == "Pat's farewell"

    def test_contact_email_is_not_client_editable(self, client, p):
        _sign_in(client, p)
        assert (
            _save(client, p, contact={"email": "hijack@example.com"}).status_code == 403
        )

    def test_fields_the_venue_hides_are_neither_shown_nor_editable(self, client, p):
        p.settings.enabled_fields = {
            "group_booking": {
                "food.allergies": False,
                "beverages.table_ordering": False,
            }
        }
        p.db.flush()
        body = _sign_in(client, p).json()
        assert (
            "allergies" not in body["event"] and "table_ordering" not in body["event"]
        )
        assert "allergies" not in body["editable"]
        assert _save(client, p, fields={"allergies": "x"}).status_code == 403
        assert _save(client, p, fields={"table_ordering": True}).status_code == 403
        assert _save(client, p, fields={"guests": 31}).status_code == 200

    def test_a_stale_version_conflicts(self, client, p):
        version = _sign_in(client, p).json()["event"]["version"]
        assert (
            _save(
                client, p, expected_version=version, fields={"guests": 40}
            ).status_code
            == 200
        )
        r = _save(client, p, expected_version=version, fields={"guests": 41})
        assert r.status_code == 409

    def test_bad_values_are_invalid_not_saved(self, client, p):
        _sign_in(client, p)
        assert _save(client, p, fields={"guests": "lots"}).status_code == 422
        assert _save(client, p, fields={"arrival_time": "7pm"}).status_code == 422
        assert _save(client, p, fields={"table_ordering": "yes"}).status_code == 422
        assert _save(client, p, unknown={"x": 1}).status_code == 422
        assert (
            _save(client, p, expected_version="v3", fields={"guests": 2}).status_code
            == 422
        )

    def test_menu_quantities_only_on_this_events_menus(self, client, p):
        body = _sign_in(client, p).json()
        menu = body["menus"][0]
        arancini = next(i for i in menu["items"] if i["name"] == "Arancini")
        r = _save(
            client,
            p,
            menus=[
                {
                    "id": menu["id"],
                    "expected_version": menu["version"],
                    "quantities": {arancini["id"]: 12},
                }
            ],
        )
        assert r.status_code == 200, r.text
        assert p.db.get(FnPreorderMenuItem, arancini["id"]).quantity == 12
        _other, other_menu, _ = _event(
            p.w, p.db, name="Someone else's", email="x@example.com"
        )
        item = p.db.query(FnPreorderMenuItem).filter_by(menu_id=other_menu.id).first()
        r = _save(client, p, menus=[{"id": other_menu.id, "quantities": {item.id: 99}}])
        assert r.status_code == 404
        p.db.refresh(item)
        assert item.quantity == 0

    def test_list_kinds_the_venue_hides_survive_a_client_replace(self, client, p):
        for kind in ("in_house_mic", "external_av"):
            p.db.add(
                FnEventAv(organization_id=p.w.org.id, event_id=p.event.id, kind=kind)
            )
        p.settings.enabled_fields = {"group_booking": {"details.av.external_av": False}}
        p.db.flush()
        body = _sign_in(client, p).json()
        assert [r["kind"] for r in body["lists"]["av"]] == ["in_house_mic"]
        r = _save(
            client, p, lists={"av": [{"kind": "in_house_tv", "details": "2 screens"}]}
        )
        assert r.status_code == 200, r.text
        kinds = sorted(
            a.kind for a in p.db.query(FnEventAv).filter_by(event_id=p.event.id)
        )
        assert kinds == ["external_av", "in_house_tv"]
        assert (
            _save(client, p, lists={"av": [{"kind": "external_av"}]}).status_code == 403
        )

    def test_writes_need_a_session(self, client, p):
        assert _save(client, p, fields={"guests": 1}).status_code == 401
        assert (
            client.post(f"{p.base}/confirm", json={"sections": ["details"]}).status_code
            == 401
        )
        assert (
            client.post(f"{p.base}/invite", json={"email": "a@b.co"}).status_code == 401
        )

    def test_cross_site_writes_are_refused_outside_local(self, client, p, monkeypatch):
        _sign_in(client, p)
        monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
        evil = _save_with_origin(client, p, "https://evil.example")
        assert evil.status_code == 403
        ours = _save_with_origin(client, p, "https://staging.bettercallnorm.com")
        assert ours.status_code == 200


def _save_with_origin(client, p, origin):
    return client.put(
        f"{p.base}/event", json={"fields": {"guests": 33}}, headers={"Origin": origin}
    )


class TestConfirming:
    def test_confirming_every_section_confirms_the_event_and_emails_staff(
        self, client, p
    ):
        _sign_in(client, p)
        r = client.post(
            f"{p.base}/confirm",
            json={"sections": ["details", "beverages", "food"], "note": "All good"},
        )
        assert r.status_code == 200, r.text
        status = r.json()["status"]
        assert status["status"] == "confirmed"
        assert all(
            s["state"] == "confirmed" and s["by_client"]
            for s in status["sections"].values()
        )
        sent = _emails(p, "portal_confirmed")
        assert len(sent) == 1 and "events@brewbar.example" in sent[0].to_addresses
        assert "all details confirmed" in sent[0].subject

    def test_edits_after_confirming_flag_the_event_and_email_staff_once(
        self, client, p
    ):
        _sign_in(client, p)
        client.post(
            f"{p.base}/confirm", json={"sections": ["details", "beverages", "food"]}
        )
        r = _save(client, p, fields={"guests": 52})
        status = r.json()["status"]
        assert status["status"] == "confirmed"
        assert status["edited_since_confirmed"] is True
        _save(client, p, fields={"guests": 53})
        sent = _emails(p, "portal_edited")
        assert len(sent) == 1
        assert "guests" in sent[0].html_body

    def test_confirm_can_save_changes_in_the_same_request(self, client, p):
        _sign_in(client, p)
        r = client.post(
            f"{p.base}/confirm",
            json={
                "sections": ["food"],
                "changes": {"fields": {"food_notes": "Late arrivals"}},
            },
        )
        assert r.status_code == 200
        assert r.json()["event"]["food_notes"] == "Late arrivals"
        assert r.json()["status"]["sections"]["food"]["state"] == "confirmed"

    def test_unknown_sections_are_invalid(self, client, p):
        _sign_in(client, p)
        assert (
            client.post(f"{p.base}/confirm", json={"sections": ["payment"]}).status_code
            == 422
        )


class TestInvite:
    def test_a_colleague_is_allowed_in_and_emailed_the_link(self, client, p):
        _sign_in(client, p)
        r = client.post(f"{p.base}/invite", json={"email": "Boss@Example.com"})
        assert r.status_code == 200, r.text
        people = {x["email"]: x for x in r.json()["people"]}
        assert people["boss@example.com"]["invited_by_email"] == "pat@example.com"
        sent = _emails(p, "portal_invite")
        assert len(sent) == 1 and sent[0].to_addresses == ["boss@example.com"]
        assert f"/e/{p.token}" in sent[0].html_body
        assert _request_code(client, p, "boss@example.com").status_code == 202
        assert p.codes[-1][0] == "boss@example.com"

    def test_inviting_someone_already_in_sends_nothing(self, client, p):
        _sign_in(client, p)
        client.post(f"{p.base}/invite", json={"email": "pat@example.com"})
        assert _emails(p, "portal_invite") == []


class TestPortalAvailability:
    def test_the_portal_is_off_unless_live(self, client, p):
        p.w.org.functions_mode = "shadow"
        p.db.flush()
        assert client.get(p.base).status_code == 404
        assert _request_code(client, p).status_code == 404
        assert p.codes == []

    def test_a_revoked_link_closes_the_portal(self, client, p):
        _sign_in(client, p)
        public_tokens.revoke(p.db, p.w.ctx(p.w.owner), event_id=p.event.id)
        assert client.get(p.base).status_code == 404

    def test_a_cancelled_event_closes_its_portal(self, client, p):
        _sign_in(client, p)
        events.cancel_event(p.db, p.w.ctx(p.w.owner), p.event.id, reason="Postponed")
        assert client.get(p.base).status_code == 404
        assert _save(client, p, fields={"guests": 2}).status_code == 404
        assert p.db.get(FnEvent, p.event.id).guests == 30
        # Reinstating reopens it — same link, same session.
        events.reinstate_event(p.db, p.w.ctx(p.w.owner), p.event.id)
        assert client.get(p.base).json()["authenticated"] is True
