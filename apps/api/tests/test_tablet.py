"""The kitchen tablet (docs/plans/staff-access.md, sections 5 and 11.6): a
venue device's context and the PIN step — the right PIN is a short token for
that person, wrong PINs are counted on the server, and a PIN from another
venue's staff member is no good here.

``services/staff_access`` is stubbed (see test_staff_directory.access_calls);
the tablet only needs it for the membership of someone tapping in for the
first time. A PIN never resolves an account by email: the row's address was
typed by a manager, so the person gets a placeholder account until they sign
in with a code.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.auth.security import create_access_token, decode_access_token
from app.config import settings
from app.db.models import StaffMember, User, VenueDevice
from app.services import staff_directory
from tests.conftest import _make_membership, _make_user
from tests.org_helpers import auth_headers
from tests import test_staff_directory as _directory_tests

# The directory suite's fixtures (its own test app, the staff_access stub),
# bound here so pytest finds them in this module.
_world = _directory_tests._world
access_calls = _directory_tests.access_calls
staff_client = _directory_tests.staff_client


@pytest.fixture()
def tablet(db_session):
    """An org with two venues, a device at La Zeppa, and staff with PINs:
    Sam at La Zeppa (claimed account), Tess at Mr Murdochs, Pat at La Zeppa
    with no email yet."""
    w = _world(db_session)
    device_user = _make_user(db_session, full_name="La Zeppa kitchen")
    device_user.is_device = True
    device_user.password_set = False
    _make_membership(db_session, device_user, w.org).role_id = w.roles["team_member"].id
    device = VenueDevice(
        organization_id=w.org.id,
        venue_id=w.a.id,
        name="La Zeppa kitchen",
        user_id=device_user.id,
        pin_required=True,
    )
    db_session.add(device)
    db_session.flush()
    token = create_access_token(
        {"sub": device_user.id, "type": "device", "device": device.id},
        expires_delta=timedelta(days=1),
    )
    w.device = device
    w.headers = {"Authorization": f"Bearer {token}"}

    w.sam = staff_directory.add_manual(
        db_session, w.org.id, name="Sam Chef", email="sam@x.com", venue_ids=[w.a.id]
    )
    sam_user = _make_user(db_session, full_name="Sam Chef")
    _make_membership(db_session, sam_user, w.org).role_id = w.roles["team_member"].id
    w.sam.user_id = sam_user.id
    w.tess = staff_directory.add_manual(
        db_session, w.org.id, name="Tess Bar", email="tess@x.com", venue_ids=[w.b.id]
    )
    w.pat = staff_directory.add_manual(
        db_session, w.org.id, name="Pat Cook", venue_ids=[w.a.id]
    )
    w.pins = {
        "sam": staff_directory.set_pin(db_session, w.sam),
        "tess": staff_directory.set_pin(db_session, w.tess),
        "pat": staff_directory.set_pin(db_session, w.pat),
    }
    db_session.flush()
    return w


class TestContext:
    def test_device_token_gets_its_context(self, tablet, staff_client):
        r = staff_client.get("/api/tablet/context", headers=tablet.headers)
        assert r.status_code == 200, r.text
        assert r.json() == {
            "device": {
                "id": tablet.device.id,
                "name": "La Zeppa kitchen",
                "pin_required": True,
                "venue": {"id": tablet.a.id, "name": "La Zeppa"},
                "organization": {"id": tablet.org.id, "name": "Lott Group"},
            }
        }
        assert tablet.device.last_seen_at is not None

    def test_a_persons_token_is_refused(self, tablet, staff_client):
        r = staff_client.get("/api/tablet/context", headers=auth_headers(tablet.owner))
        assert r.status_code == 403
        r = staff_client.post(
            "/api/tablet/pin", json={"pin": "00000"}, headers=auth_headers(tablet.owner)
        )
        assert r.status_code == 403

    def test_a_revoked_device_is_signed_out(self, tablet, staff_client, db_session):
        tablet.device.revoked_at = datetime.now(timezone.utc)
        db_session.flush()
        assert (
            staff_client.get("/api/tablet/context", headers=tablet.headers).status_code
            == 401
        )


class TestPin:
    def test_right_pin_is_a_short_token_for_that_person(
        self, tablet, staff_client, access_calls
    ):
        r = staff_client.post(
            "/api/tablet/pin", json={"pin": tablet.pins["sam"]}, headers=tablet.headers
        )
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["user"] == {"id": tablet.sam.user_id, "full_name": "Sam Chef"}
        assert out["expires_in"] == settings.TABLET_PIN_TTL_MINUTES * 60
        claims = decode_access_token(out["access_token"])
        assert claims["sub"] == tablet.sam.user_id and claims["type"] == "pin"
        assert claims["device"] == tablet.device.id
        assert (
            claims["exp"] - datetime.now(timezone.utc).timestamp()
            <= settings.TABLET_PIN_TTL_MINUTES * 60 + 5
        )
        assert tablet.sam.last_seen_at is not None
        # Sam already had an account: nothing was claimed.
        assert access_calls["claim"] == []
        # The short token works as Sam.
        r = staff_client.post(
            "/api/me/pin", headers={"Authorization": f"Bearer {out['access_token']}"}
        )
        assert r.status_code == 200 and len(r.json()["pin"]) == 5

    def test_first_tap_makes_the_account(
        self, tablet, staff_client, access_calls, db_session
    ):
        # Tess has an email but no account yet — she is at the other venue,
        # so move her here first. Her email is NOT looked up: the account is
        # a placeholder until she signs in with a code.
        tablet.tess.venue_ids = [tablet.a.id]
        db_session.flush()
        r = staff_client.post(
            "/api/tablet/pin", json={"pin": tablet.pins["tess"]}, headers=tablet.headers
        )
        assert r.status_code == 200, r.text
        assert access_calls["claim"] == []
        assert tablet.tess.user_id and r.json()["user"]["id"] == tablet.tess.user_id
        tess_user = db_session.query(User).filter(User.id == tablet.tess.user_id).one()
        assert tess_user.email == f"staff-{tablet.tess.id}@staff.norm.local"
        assert not tess_user.password_set
        # Pat has no email: a placeholder account, membership made.
        r = staff_client.post(
            "/api/tablet/pin", json={"pin": tablet.pins["pat"]}, headers=tablet.headers
        )
        assert r.status_code == 200, r.text
        pat = (
            db_session.query(StaffMember).filter(StaffMember.id == tablet.pat.id).one()
        )
        user = db_session.query(User).filter(User.id == pat.user_id).one()
        assert user.email == f"staff-{pat.id}@staff.norm.local"
        assert (
            user.full_name == "Pat Cook"
            and not user.password_set
            and not user.is_device
        )
        assert (user.id, pat.id) in access_calls["ensure"]

    def test_a_pin_never_becomes_someone_elses_account(
        self, tablet, staff_client, access_calls, db_session
    ):
        """A manager adds a row with the Owner's email and sets its PIN. The
        PIN must not mint a session as the Owner (billing:manage, org:roles,
        every organisation they belong to) — nothing proved that address is
        theirs. The token is for a new placeholder account."""
        mole = staff_directory.add_manual(
            db_session,
            tablet.org.id,
            name="Totally The Owner",
            email=tablet.owner.email,
            venue_ids=[tablet.a.id],
        )
        pin = staff_directory.set_pin(db_session, mole)
        db_session.flush()
        r = staff_client.post(
            "/api/tablet/pin", json={"pin": pin}, headers=tablet.headers
        )
        assert r.status_code == 200, r.text
        sub = decode_access_token(r.json()["access_token"])["sub"]
        assert sub != tablet.owner.id
        user = db_session.query(User).filter(User.id == sub).one()
        assert user.email == f"staff-{mole.id}@staff.norm.local"
        assert not user.password_set and mole.user_id == user.id
        assert access_calls["claim"] == []

    def test_wrong_pins_are_counted_and_lock_after_five(
        self, tablet, staff_client, access_calls
    ):
        for _ in range(5):
            r = staff_client.post(
                "/api/tablet/pin", json={"pin": "99999"}, headers=tablet.headers
            )
            assert r.status_code == 400, r.text
        # The sixth try is locked out — even with the right PIN.
        r = staff_client.post(
            "/api/tablet/pin", json={"pin": tablet.pins["sam"]}, headers=tablet.headers
        )
        assert r.status_code == 429
        assert int(r.headers["Retry-After"]) >= 1
        assert "locked" in r.json()["detail"].lower()

    def test_another_venues_pin_is_refused_here(
        self, tablet, staff_client, access_calls
    ):
        r = staff_client.post(
            "/api/tablet/pin", json={"pin": tablet.pins["tess"]}, headers=tablet.headers
        )
        assert r.status_code == 400
        assert access_calls["claim"] == []
        # A leaver's PIN too.
        tablet.sam.is_active = False
        r = staff_client.post(
            "/api/tablet/pin", json={"pin": tablet.pins["sam"]}, headers=tablet.headers
        )
        assert r.status_code == 400
