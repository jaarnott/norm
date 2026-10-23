"""Venue staff signing in with an emailed code (docs/plans/staff-access.md,
11.5): who gets a code, the throttle and the burn, what the first sign-in
makes, leaving, what /auth/me adds, and the device-token check in
get_current_user."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt

from app.auth.security import JWT_ALGORITHM, JWT_SECRET, create_access_token
from app.db.models import (
    EmailLog,
    OrganizationMembership,
    Role,
    StaffLoginCode,
    StaffMember,
    User,
    UserVenueAccess,
    VenueDevice,
)
from app.routers import staff_auth
from app.services import staff_access
from tests.conftest import _make_membership, _make_user, _test_app
from tests.org_helpers import (
    auth_headers,
    grant_venue,
    make_org,
    make_venue,
    seed_system_roles,
)

if not any(getattr(r, "path", "") == "/api/auth/staff/code" for r in _test_app.routes):
    _test_app.include_router(staff_auth.router)

CODE_URL = "/api/auth/staff/code"
VERIFY_URL = "/api/auth/staff/verify"


@pytest.fixture(autouse=True)
def _fresh_rate_limit():
    """The per-IP limiter is in memory and every test is the same client."""
    from app.middleware.rate_limit import limiter

    limiter.reset()
    yield


@pytest.fixture()
def sent(monkeypatch):
    """The codes the router hands to the email task, in order."""
    out: list[staff_access.IssuedCode] = []
    monkeypatch.setattr(staff_auth, "_send_code", out.append)
    return out


def _system_roles(db):
    """The system roles by name: the migration seeds them, so a test database
    usually has them already; a bare one gets them here."""
    rows = db.query(Role).filter(Role.organization_id.is_(None)).all()
    if not rows:
        return seed_system_roles(db)
    return {r.name: r for r in rows}


def _staff(db, org, email, *, name="Sam Chef", venues=(), roles=("Chef",), **f):
    row = StaffMember(
        id=str(uuid.uuid4()),
        organization_id=org.id,
        source=f.pop("source", "loadedhub"),
        external_id=f.pop("external_id", uuid.uuid4().hex[:8]),
        name=name,
        email=email,
        loaded_roles=list(roles),
        venue_ids=[v.id for v in venues],
        **f,
    )
    db.add(row)
    db.flush()
    return row


def _enabled_org(db, name="La Zeppa"):
    org = make_org(db, name)
    org.staff_access_enabled = True
    db.flush()
    return org


def _kitchen_role(db, org):
    role = Role(
        id=str(uuid.uuid4()),
        organization_id=org.id,
        name="kitchen",
        display_name="Kitchen",
        is_system=False,
        permissions=["tasks:read"],
        agents=[],
        apps=["kitchen", "training"],
        loaded_roles=["Chef", "Kitchen Hand"],
    )
    db.add(role)
    db.flush()
    return role


def _membership(db, user, org):
    return (
        db.query(OrganizationMembership)
        .filter(
            OrganizationMembership.user_id == user.id,
            OrganizationMembership.organization_id == org.id,
        )
        .first()
    )


def _venue_ids(db, user):
    return {
        r.venue_id
        for r in db.query(UserVenueAccess).filter(UserVenueAccess.user_id == user.id)
    }


def _sign_in(client, sent, email):
    assert client.post(CODE_URL, json={"email": email}).status_code == 202
    code = sent[-1].code
    resp = client.post(VERIFY_URL, json={"email": email, "code": code})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Who gets a code
# ---------------------------------------------------------------------------


class TestRequestCode:
    def test_a_code_goes_only_to_active_staff_of_an_organisation_with_access_on(
        self, client, db_session, sent
    ):
        _system_roles(db_session)
        on = _enabled_org(db_session)
        off = make_org(db_session, "Not yet")
        _staff(db_session, on, "sam@x.com")
        _staff(db_session, on, "gone@x.com", is_active=False)
        _staff(db_session, off, "pat@x.com")

        answers = []
        for email in ("nobody@x.com", "gone@x.com", "pat@x.com", "not an email"):
            r = client.post(CODE_URL, json={"email": email})
            answers.append((r.status_code, r.json()))
            assert sent == []
        r = client.post(CODE_URL, json={"email": "  Sam@X.com "})
        answers.append((r.status_code, r.json()))
        # the same answer every time, whatever happened
        assert all(a == (202, {"sent": True}) for a in answers)
        assert [c.email for c in sent] == ["sam@x.com"]
        assert len(sent[0].code) == 6 and sent[0].code.isdigit()
        assert sent[0].name == "Sam Chef"
        # a row only for the address that got a code, holding a hash not the code
        rows = db_session.query(StaffLoginCode).all()
        assert [r.email for r in rows] == ["sam@x.com"]
        assert rows[0].code_hash and sent[0].code not in rows[0].code_hash

    def test_an_address_gets_three_codes_in_a_window_then_quietly_none(
        self, client, db_session, sent
    ):
        _system_roles(db_session)
        org = _enabled_org(db_session)
        _staff(db_session, org, "sam@x.com")
        for _ in range(4):
            r = client.post(CODE_URL, json={"email": "sam@x.com"})
            assert (r.status_code, r.json()) == (202, {"sent": True})
        assert len(sent) == 3
        # the last code issued still works
        r = client.post(VERIFY_URL, json={"email": "sam@x.com", "code": sent[-1].code})
        assert r.status_code == 200

    def test_the_service_window_holds_for_callers_other_than_the_router(
        self, db_session
    ):
        _system_roles(db_session)
        org = _enabled_org(db_session)
        _staff(db_session, org, "sam@x.com")
        t0 = datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)
        for i in range(3):
            assert (
                staff_access.request_code(db_session, "sam@x.com", now=t0) is not None
            )
        assert staff_access.request_code(db_session, "sam@x.com", now=t0) is None
        # a new window
        later = t0 + staff_access.CODE_SEND_WINDOW
        assert staff_access.request_code(db_session, "sam@x.com", now=later) is not None


# ---------------------------------------------------------------------------
# Wrong codes
# ---------------------------------------------------------------------------


class TestVerifyCode:
    def test_five_wrong_codes_burn_it_and_an_unknown_email_looks_the_same(
        self, client, db_session, sent
    ):
        _system_roles(db_session)
        org = _enabled_org(db_session)
        _staff(db_session, org, "sam@x.com")
        client.post(CODE_URL, json={"email": "sam@x.com"})
        right = sent[0].code
        wrong = f"{(int(right) + 1) % 1_000_000:06d}"
        for _ in range(5):
            r = client.post(VERIFY_URL, json={"email": "sam@x.com", "code": wrong})
            assert r.status_code == 400
            assert r.json()["detail"]["code"] == "bad_code"
        # the right code is no use now
        r = client.post(VERIFY_URL, json={"email": "sam@x.com", "code": right})
        assert r.status_code == 400
        # and an email with no code at all is refused the same way
        r = client.post(VERIFY_URL, json={"email": "nobody@x.com", "code": right})
        assert r.status_code == 400 and r.json()["detail"]["code"] == "bad_code"
        assert db_session.query(User).filter(User.email == "sam@x.com").count() == 0

    def test_a_code_is_spent_by_use_and_dead_after_ten_minutes(self, db_session):
        _system_roles(db_session)
        org = _enabled_org(db_session)
        _staff(db_session, org, "sam@x.com")
        t0 = datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)
        issued = staff_access.request_code(db_session, "sam@x.com", now=t0)
        late = t0 + staff_access.CODE_TTL
        assert not staff_access.verify_code(
            db_session, "sam@x.com", issued.code, now=late
        )
        issued = staff_access.request_code(db_session, "sam@x.com", now=t0)
        assert staff_access.verify_code(db_session, "sam@x.com", issued.code, now=t0)
        assert not staff_access.verify_code(
            db_session, "sam@x.com", issued.code, now=t0
        )

    def test_ten_wrong_codes_in_a_day_lock_the_address_until_tomorrow(self, db_session):
        _system_roles(db_session)
        org = _enabled_org(db_session)
        _staff(db_session, org, "sam@x.com")
        t0 = datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)
        for _ in range(2):
            issued = staff_access.request_code(db_session, "sam@x.com", now=t0)
            bad = f"{(int(issued.code) + 1) % 1_000_000:06d}"
            for _ in range(5):
                assert not staff_access.verify_code(
                    db_session, "sam@x.com", bad, now=t0
                )
        assert staff_access.failures_today(db_session, "sam@x.com", t0) == 10
        assert staff_access.request_code(db_session, "sam@x.com", now=t0) is None
        tomorrow = t0 + timedelta(days=1)
        assert staff_access.request_code(db_session, "sam@x.com", now=tomorrow)


# ---------------------------------------------------------------------------
# The first sign-in
# ---------------------------------------------------------------------------


class TestClaim:
    def test_first_sign_in_makes_the_account_membership_role_and_venues(
        self, client, db_session, sent
    ):
        _system_roles(db_session)
        org = _enabled_org(db_session)
        kitchen = _kitchen_role(db_session, org)
        v1, v2 = make_venue(db_session, "Bar", org), make_venue(db_session, "Deck", org)
        _staff(db_session, org, "sam@x.com", venues=(v1, v2), roles=("chef",))

        out = _sign_in(client, sent, "sam@x.com")
        assert out["token_type"] == "bearer"
        assert out["user"]["email"] == "sam@x.com"
        assert out["user"]["full_name"] == "Sam Chef"
        assert out["organizations"] == [{"id": org.id, "name": "La Zeppa"}]

        user = db_session.query(User).filter(User.email == "sam@x.com").one()
        assert user.is_active and not user.password_set and not user.is_device
        m = _membership(db_session, user, org)
        assert m.role_id == kitchen.id and m.role == "member"
        assert _venue_ids(db_session, user) == {v1.id, v2.id}
        staff = db_session.query(StaffMember).filter_by(email="sam@x.com").one()
        assert staff.user_id == user.id and staff.last_seen_at is not None

        # the token is a staff token that lives a month, and it works
        claims = jwt.decode(out["access_token"], JWT_SECRET, algorithms=[JWT_ALGORITHM])
        assert claims["type"] == "staff" and claims["sub"] == user.id
        life = datetime.fromtimestamp(claims["exp"], tz=timezone.utc) - datetime.now(
            timezone.utc
        )
        assert timedelta(days=29) < life <= timedelta(days=30)
        me = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {out['access_token']}"}
        )
        assert me.status_code == 200 and me.json()["org_role"]["name"] == "kitchen"

    def test_no_mapping_falls_back_to_team_member_and_a_hand_picked_role_wins(
        self, client, db_session, sent
    ):
        roles = _system_roles(db_session)
        org = _enabled_org(db_session)
        kitchen = _kitchen_role(db_session, org)
        _staff(db_session, org, "nomap@x.com", roles=("Juggler",))
        _staff(
            db_session,
            org,
            "picked@x.com",
            roles=("Chef",),
            role_id=roles["manager"].id,
        )
        _sign_in(client, sent, "nomap@x.com")
        _sign_in(client, sent, "picked@x.com")
        nomap = db_session.query(User).filter_by(email="nomap@x.com").one()
        picked = db_session.query(User).filter_by(email="picked@x.com").one()
        assert _membership(db_session, nomap, org).role_id == roles["team_member"].id
        assert _membership(db_session, picked, org).role_id == roles["manager"].id
        assert kitchen.id not in {
            _membership(db_session, u, org).role_id for u in (nomap, picked)
        }

    def test_an_existing_member_keeps_their_role_and_gains_venues(
        self, client, db_session, sent
    ):
        roles = _system_roles(db_session)
        org = _enabled_org(db_session)
        _kitchen_role(db_session, org)
        v1, v2 = make_venue(db_session, "Bar", org), make_venue(db_session, "Deck", org)
        rich = _make_user(db_session, email="rich@x.com", full_name="Rich")
        _make_membership(db_session, rich, org).role_id = roles["manager"].id
        grant_venue(db_session, rich, v1)
        _staff(
            db_session, org, "rich@x.com", name="Rich M", venues=(v2,), roles=("Chef",)
        )

        out = _sign_in(client, sent, "rich@x.com")
        assert out["user"]["id"] == rich.id
        assert db_session.query(User).filter_by(email="rich@x.com").count() == 1
        assert _membership(db_session, rich, org).role_id == roles["manager"].id
        assert _venue_ids(db_session, rich) == {v1.id, v2.id}
        assert rich.password_set and rich.full_name == "Rich"

    def test_one_email_in_two_organisations_joins_both(self, client, db_session, sent):
        _system_roles(db_session)
        a, b = _enabled_org(db_session, "Org A"), _enabled_org(db_session, "Org B")
        ka = _kitchen_role(db_session, a)
        va, vb = make_venue(db_session, "A1", a), make_venue(db_session, "B1", b)
        _staff(db_session, a, "sam@x.com", venues=(va,))
        _staff(db_session, b, "sam@x.com", venues=(vb,), roles=("Bartender",))
        # a venue of the OTHER organisation on a staff row is ignored
        _staff(
            db_session, b, "sam@x.com", venues=(va,), external_id="dup", source="manual"
        )

        out = _sign_in(client, sent, "sam@x.com")
        assert {o["name"] for o in out["organizations"]} == {"Org A", "Org B"}
        user = db_session.query(User).filter_by(email="sam@x.com").one()
        assert _membership(db_session, user, a).role_id == ka.id
        assert _membership(db_session, user, b).role_id != ka.id
        assert _venue_ids(db_session, user) == {va.id, vb.id}
        # signing in again changes nothing
        again = _sign_in(client, sent, "sam@x.com")
        assert again["user"]["id"] == user.id
        assert (
            db_session.query(OrganizationMembership).filter_by(user_id=user.id).count()
            == 2
        )

    def test_a_leaver_signed_back_on_is_reactivated(self, db_session):
        _system_roles(db_session)
        org = _enabled_org(db_session)
        staff = _staff(db_session, org, "sam@x.com")
        user = staff_access.claim_account(db_session, "sam@x.com")
        staff_access.revoke(db_session, staff)
        assert not user.is_active
        assert staff_access.claim_account(db_session, "sam@x.com") is user
        assert user.is_active and _membership(db_session, user, org) is not None


# ---------------------------------------------------------------------------
# Leaving
# ---------------------------------------------------------------------------


class TestRevoke:
    def test_revoke_removes_access_and_deactivates_a_staff_only_account(
        self, client, db_session, sent
    ):
        _system_roles(db_session)
        org = _enabled_org(db_session)
        v = make_venue(db_session, "Bar", org)
        staff = _staff(db_session, org, "sam@x.com", venues=(v,))
        out = _sign_in(client, sent, "sam@x.com")
        headers = {"Authorization": f"Bearer {out['access_token']}"}
        assert client.get("/api/auth/me", headers=headers).status_code == 200
        user = db_session.query(User).filter_by(email="sam@x.com").one()

        staff_access.revoke(db_session, staff)
        db_session.flush()
        assert _membership(db_session, user, org) is None
        assert _venue_ids(db_session, user) == set()
        assert not user.is_active
        assert user.email == "sam@x.com"  # their history stays
        # the phone's session stops at its next request
        assert client.get("/api/auth/me", headers=headers).status_code == 401

    def test_revoke_takes_only_what_the_claim_gave(self, db_session):
        """Rich was a Manager before he appeared on the roster: the directory
        did not let him in, so Loaded dropping him (or a manager switching
        his row off) must not take his membership or venues away."""
        roles = _system_roles(db_session)
        org = _enabled_org(db_session)
        other = make_org(db_session, "Elsewhere")
        v, ov = make_venue(db_session, "Bar", org), make_venue(db_session, "Far", other)
        rich = _make_user(db_session, email="rich@x.com")
        _make_membership(db_session, rich, org).role_id = roles["manager"].id
        _make_membership(db_session, rich, other).role_id = roles["manager"].id
        grant_venue(db_session, rich, v)
        grant_venue(db_session, rich, ov)
        staff = _staff(db_session, org, "rich@x.com", venues=(v,))
        staff_access.claim_account(db_session, "rich@x.com")
        assert staff.user_id == rich.id and not staff.granted_membership

        staff_access.revoke(db_session, staff)
        assert _membership(db_session, rich, org) is not None
        assert _membership(db_session, rich, other) is not None
        assert _venue_ids(db_session, rich) == {v.id, ov.id}
        assert rich.is_active

        # an account Norm made stays active while a membership remains elsewhere
        both = _enabled_org(db_session, "Second")
        s1 = _staff(db_session, org, "two@x.com")
        _staff(db_session, both, "two@x.com")
        two = staff_access.claim_account(db_session, "two@x.com")
        staff_access.revoke(db_session, s1)
        assert two.is_active and _membership(db_session, two, both) is not None

    def test_a_code_sign_in_adopts_the_tablet_placeholder(self, db_session):
        """A tablet PIN made Sam a placeholder account (no email of his own
        was trusted). His first code sign-in makes that account his — same
        id, so what he did on the tablet stays his — rather than a second
        one."""
        _system_roles(db_session)
        org = _enabled_org(db_session)
        v = make_venue(db_session, "Bar", org)
        staff = _staff(db_session, org, "sam@x.com", venues=(v,))
        placeholder = User(
            id=str(uuid.uuid4()),
            email=staff_access.placeholder_email(staff),
            hashed_password="x",
            full_name="Sam Chef",
            role="user",
            is_active=True,
            password_set=False,
        )
        db_session.add(placeholder)
        db_session.flush()
        staff_access.ensure_membership(db_session, placeholder, staff)
        assert staff.user_id == placeholder.id and staff.granted_membership

        user = staff_access.claim_account(db_session, "sam@x.com")
        assert user is placeholder and user.email == "sam@x.com"
        assert db_session.query(User).filter(User.email == "sam@x.com").count() == 1
        assert _membership(db_session, user, org) is not None
        # An account that already owns the address wins over a placeholder.
        other_org = _enabled_org(db_session, "Second")
        pat_row = _staff(db_session, other_org, "pat@x.com", name="Pat")
        pat = _make_user(db_session, email="pat@x.com")
        stray = User(
            id=str(uuid.uuid4()),
            email=staff_access.placeholder_email(pat_row),
            hashed_password="x",
            full_name="Pat",
            role="user",
            is_active=True,
            password_set=False,
        )
        db_session.add(stray)
        db_session.flush()
        staff_access.ensure_membership(db_session, stray, pat_row)
        assert staff_access.claim_account(db_session, "pat@x.com") is pat
        assert pat_row.user_id == pat.id

    def test_revoke_without_a_claim_or_beside_another_record_does_nothing(
        self, db_session
    ):
        roles = _system_roles(db_session)
        org = _enabled_org(db_session)
        rich = _make_user(db_session, email="rich@x.com")
        _make_membership(db_session, rich, org).role_id = roles["manager"].id
        unclaimed = _staff(db_session, org, "rich@x.com")
        staff_access.revoke(db_session, unclaimed)
        assert _membership(db_session, rich, org) is not None

        loaded = _staff(db_session, org, "sam@x.com")
        _staff(db_session, org, "sam@x.com", source="manual", external_id=None)
        sam = staff_access.claim_account(db_session, "sam@x.com")
        staff_access.revoke(db_session, loaded)
        assert _membership(db_session, sam, org) is not None and sam.is_active


# ---------------------------------------------------------------------------
# /auth/me
# ---------------------------------------------------------------------------


class TestMe:
    def test_me_reports_access_and_the_staff_record(self, client, db_session, sent):
        _system_roles(db_session)
        org = _enabled_org(db_session)
        _kitchen_role(db_session, org)
        v = make_venue(db_session, "Bar", org)
        _staff(db_session, org, "sam@x.com", venues=(v,))
        out = _sign_in(client, sent, "sam@x.com")
        me = client.get(
            "/api/auth/me", headers={"Authorization": f"Bearer {out['access_token']}"}
        ).json()
        assert me["access"] == {
            "agents": [],
            "apps": ["kitchen", "training"],
            "staff": True,
        }
        assert me["is_device"] is False
        staff = db_session.query(StaffMember).filter_by(email="sam@x.com").one()
        assert me["staff"] == {
            "organization_id": org.id,
            "staff_member_id": staff.id,
            "venue_ids": [v.id],
            "has_pin": False,
        }

    def test_me_for_a_manager_an_admin_and_a_roster_match_by_email(
        self, client, db_session
    ):
        roles = _system_roles(db_session)
        org = _enabled_org(db_session)
        rich = _make_user(db_session, email="rich@x.com")
        _make_membership(db_session, rich, org).role_id = roles["manager"].id
        me = client.get("/api/auth/me", headers=auth_headers(rich)).json()
        assert me["access"] == {"agents": None, "apps": None, "staff": False}
        assert me["staff"] is None
        # on the roster under the same email, not yet claimed: still theirs
        row = _staff(db_session, org, "rich@x.com")
        row.pin_hash = "x"
        db_session.flush()
        me = client.get("/api/auth/me", headers=auth_headers(rich)).json()
        assert me["staff"]["staff_member_id"] == row.id and me["staff"]["has_pin"]

        admin = _make_user(db_session, role="admin")
        me = client.get("/api/auth/me", headers=auth_headers(admin)).json()
        assert me["access"] == {"agents": None, "apps": None, "staff": False}
        assert me["staff"] is None and me["is_device"] is False


# ---------------------------------------------------------------------------
# Device tokens
# ---------------------------------------------------------------------------


def _device(db, org, venue):
    user = _make_user(db, email=f"device-{uuid.uuid4().hex[:6]}@devices.norm.local")
    user.is_device = True
    user.password_set = False
    device = VenueDevice(
        id=str(uuid.uuid4()),
        organization_id=org.id,
        venue_id=venue.id,
        name="Kitchen tablet",
        user_id=user.id,
    )
    db.add(device)
    db.flush()
    return user, device


class TestDeviceTokens:
    def test_a_device_token_works_until_the_device_is_revoked(self, client, db_session):
        _system_roles(db_session)
        org = _enabled_org(db_session)
        v = make_venue(db_session, "Bar", org)
        user, device = _device(db_session, org, v)
        token = create_access_token(
            {"sub": user.id, "type": "device", "device": device.id},
            expires_delta=timedelta(days=365),
        )
        headers = {"Authorization": f"Bearer {token}"}
        me = client.get("/api/auth/me", headers=headers)
        assert me.status_code == 200 and me.json()["is_device"] is True

        device.revoked_at = datetime.now(timezone.utc)
        db_session.flush()
        assert client.get("/api/auth/me", headers=headers).status_code == 401

    def test_tablet_tokens_cannot_chat(self, client, db_session):
        """No chat on the tablet (plan section 5): a device token and a PIN'd
        person's token are both refused by the messages routes, whatever the
        shell shows."""
        _system_roles(db_session)
        org = _enabled_org(db_session)
        v = make_venue(db_session, "Bar", org)
        user, device = _device(db_session, org, v)
        device_token = create_access_token(
            {"sub": user.id, "type": "device", "device": device.id}
        )
        person = _make_user(db_session)
        pin_token = create_access_token(
            {"sub": person.id, "type": "pin", "device": device.id}
        )
        for token in (device_token, pin_token):
            for path in ("/api/messages", "/api/messages/stream"):
                r = client.post(
                    path,
                    json={"message": "hi"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert r.status_code == 403, (path, r.text)
                assert "tablet" in r.json()["detail"].lower()

    def test_a_device_token_must_name_its_own_device(self, client, db_session):
        _system_roles(db_session)
        org = _enabled_org(db_session)
        v = make_venue(db_session, "Bar", org)
        user, device = _device(db_session, org, v)
        other = _make_user(db_session)
        no_device = create_access_token({"sub": user.id, "type": "device"})
        wrong_user = create_access_token(
            {"sub": other.id, "type": "device", "device": device.id}
        )
        unknown = create_access_token(
            {"sub": user.id, "type": "device", "device": "nope"}
        )
        for token in (no_device, wrong_user, unknown):
            r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 401
        # an ordinary token, and a staff token, are not checked against devices
        for token in (
            create_access_token({"sub": other.id}),
            create_access_token({"sub": other.id, "type": "staff"}),
        ):
            r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 200


# ---------------------------------------------------------------------------
# The email
# ---------------------------------------------------------------------------


class TestCodeEmail:
    def test_the_code_email_renders_and_is_logged_without_its_body(self, db_session):
        from app.services.email_service import send_system_email
        from app.services.email_templates import render_template

        subject, html = render_template(
            "staff_login_code", {"code": "123456", "first_name": "Sam", "minutes": 10}
        )
        assert subject.strip() == "Your Norm sign-in code"
        assert "123456" in html and "Hi Sam," in html and "10 minutes" in html

        log_id = send_system_email(
            "staff_login_code",
            ["sam@x.com"],
            {"code": "654321", "first_name": "Sam", "minutes": 10},
            db_session,
            log_body=False,
        )
        log = db_session.query(EmailLog).filter_by(id=log_id).one()
        assert log.html_body == "" and log.subject.strip() == "Your Norm sign-in code"
        # the default still keeps the body
        kept = send_system_email(
            "staff_login_code",
            ["sam@x.com"],
            {"code": "654321", "first_name": "Sam", "minutes": 10},
            db_session,
        )
        assert "654321" in db_session.query(EmailLog).filter_by(id=kept).one().html_body

    def test_create_access_token_keeps_its_day_long_default(self):
        claims = jwt.decode(
            create_access_token({"sub": "u"}), JWT_SECRET, algorithms=[JWT_ALGORITHM]
        )
        life = datetime.fromtimestamp(claims["exp"], tz=timezone.utc) - datetime.now(
            timezone.utc
        )
        assert timedelta(hours=23) < life <= timedelta(hours=24)
