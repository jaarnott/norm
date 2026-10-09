"""Settings → Email: who may use the email endpoints, and whose mail they see.

The email router gated on ``email:read`` / ``email:manage`` while neither
scope was in PERMISSION_SCOPES, so no role could hold them and every owner
got 403 on the Email tab (fixed Oct 2026). Owners and managers now hold both.
Granting them made scoping the log essential: ``email_logs`` is one table for
every organisation, and an invite or password reset carries a live sign-in
link in its body — unscoped, any owner could have read anyone's reset link.
"""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.permissions import PERMISSION_GROUPS, PERMISSION_SCOPES, STANDARD_ROLES
from app.db.engine import get_db
from app.db.models import EmailLog
from app.routers import email as email_router
from tests.conftest import (
    _make_membership,
    _make_organization,
    _make_thread,
    _make_user,
    _make_venue,
)
from tests.test_permissions import _auth, _seed_system_roles

# The shared test app doesn't mount the email router; this file's own does.
_app = FastAPI()
_app.include_router(email_router.router, prefix="/api")


@pytest.fixture()
def email_client(db_session):
    def _override_get_db():
        yield db_session

    _app.dependency_overrides[get_db] = _override_get_db
    with TestClient(_app, raise_server_exceptions=False) as c:
        yield c
    _app.dependency_overrides.clear()


def _member(db, org, role):
    user = _make_user(db, role="user")
    _make_membership(db, user, org).role_id = role.id
    db.flush()
    return user


def _log(db, **kw):
    log = EmailLog(
        id=str(uuid.uuid4()),
        sender_type=kw.pop("sender_type", "system"),
        sender_email="norm@example.com",
        to_addresses=["someone@example.com"],
        subject=kw.pop("subject", "Hello"),
        status=kw.pop("status", "sent"),
        **kw,
    )
    db.add(log)
    db.flush()
    return log


class TestScopes:
    def test_scopes_exist_and_are_grouped(self):
        assert {"email:read", "email:manage"} <= PERMISSION_SCOPES
        assert PERMISSION_GROUPS["Email"] == ["email:read", "email:manage"]

    def test_owner_and_manager_hold_them_team_member_does_not(self):
        email = {"email:read", "email:manage"}
        assert email <= set(STANDARD_ROLES["owner"]["permissions"])
        assert email <= set(STANDARD_ROLES["manager"]["permissions"])
        assert not email & set(STANDARD_ROLES["team_member"]["permissions"])
        assert not email & set(STANDARD_ROLES["payroll_admin"]["permissions"])


class TestEmailAccess:
    def test_owner_can_open_email_settings(self, email_client, db_session):
        roles = _seed_system_roles(db_session)
        owner = _member(db_session, _make_organization(db_session), roles["owner"])

        assert (
            email_client.get("/api/email/connections", headers=_auth(owner)).status_code
            == 200
        )
        assert (
            email_client.get("/api/email/logs", headers=_auth(owner)).status_code == 200
        )

    def test_team_member_cannot(self, email_client, db_session):
        roles = _seed_system_roles(db_session)
        member = _member(
            db_session, _make_organization(db_session), roles["team_member"]
        )

        assert (
            email_client.get(
                "/api/email/connections", headers=_auth(member)
            ).status_code
            == 403
        )
        assert (
            email_client.get("/api/email/logs", headers=_auth(member)).status_code
            == 403
        )

    def test_owner_cannot_touch_platform_email(self, email_client, db_session):
        """The templates are shared by every organisation, and a test send goes
        out from Norm's own address: platform admin only."""
        roles = _seed_system_roles(db_session)
        owner = _member(db_session, _make_organization(db_session), roles["owner"])

        put = email_client.put(
            "/api/email/templates/password_reset",
            json={"subject_template": "Click here"},
            headers=_auth(owner),
        )
        assert put.status_code == 403
        send = email_client.post(
            "/api/email/send-test", json={"to": "x@example.com"}, headers=_auth(owner)
        )
        assert send.status_code == 403


class TestLogScoping:
    @pytest.fixture()
    def two_orgs(self, db_session):
        roles = _seed_system_roles(db_session)
        org_a = _make_organization(db_session, name="Org A")
        org_b = _make_organization(db_session, name="Org B")
        owner_a = _member(db_session, org_a, roles["owner"])
        staff_a = _member(db_session, org_a, roles["team_member"])
        owner_b = _member(db_session, org_b, roles["owner"])
        venue_a = _make_venue(db_session, organization_id=org_a.id)
        venue_b = _make_venue(db_session, organization_id=org_b.id)
        logs = {
            # A report from a thread at one of A's venues.
            "a_thread": _log(
                db_session,
                template_name="report",
                thread_id=_make_thread(db_session, owner_a, venue_id=venue_a.id).id,
            ),
            # A failed send from an A member's own mailbox, no thread.
            "a_on_behalf": _log(
                db_session,
                sender_type="on_behalf",
                sender_user_id=staff_a.id,
                status="failed",
            ),
            # An all-venues thread started by an A member.
            "a_no_venue": _log(
                db_session, thread_id=_make_thread(db_session, staff_a).id
            ),
            "b_thread": _log(
                db_session,
                status="failed",
                thread_id=_make_thread(db_session, owner_b, venue_id=venue_b.id).id,
            ),
            # System mail with no org link at all — the body holds a reset link.
            "reset": _log(db_session, template_name="password_reset", subject="Reset"),
        }
        return owner_a, logs

    def test_owner_sees_only_their_organisations_mail(
        self, email_client, db_session, two_orgs
    ):
        owner_a, logs = two_orgs
        res = email_client.get("/api/email/logs?limit=500", headers=_auth(owner_a))
        assert res.status_code == 200
        seen = {row["id"] for row in res.json()["logs"]}
        assert seen == {
            logs["a_thread"].id,
            logs["a_on_behalf"].id,
            logs["a_no_venue"].id,
        }

    def test_another_organisations_log_and_a_reset_are_not_found(
        self, email_client, db_session, two_orgs
    ):
        owner_a, logs = two_orgs
        for key in ("b_thread", "reset"):
            res = email_client.get(
                f"/api/email/logs/{logs[key].id}", headers=_auth(owner_a)
            )
            assert res.status_code == 404, key
        own = email_client.get(
            f"/api/email/logs/{logs['a_thread'].id}", headers=_auth(owner_a)
        )
        assert own.status_code == 200

    def test_retry_is_scoped_too(self, email_client, db_session, two_orgs):
        owner_a, logs = two_orgs
        other = email_client.post(
            f"/api/email/retry/{logs['b_thread'].id}", headers=_auth(owner_a)
        )
        assert other.status_code == 404
        # Found (so not 404) — an on-behalf send just can't be retried here.
        own = email_client.post(
            f"/api/email/retry/{logs['a_on_behalf'].id}", headers=_auth(owner_a)
        )
        assert own.status_code == 400

    def test_platform_admin_sees_everything(self, email_client, db_session, two_orgs):
        _, logs = two_orgs
        admin = _make_user(db_session, role="admin")
        res = email_client.get("/api/email/logs?limit=500", headers=_auth(admin))
        assert res.status_code == 200
        seen = {row["id"] for row in res.json()["logs"]}
        assert {log.id for log in logs.values()} <= seen
