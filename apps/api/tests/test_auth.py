"""Tests for authentication endpoints: register, login, me."""

from unittest.mock import patch


from app.db.models import User, Role


class TestRegister:
    """POST /api/auth/register"""

    def test_register_creates_user(self, client, db_session):
        resp = client.post(
            "/api/auth/register",
            json={
                "email": "newuser@example.com",
                "password": "securepass123",
                "full_name": "New User",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["email"] == "newuser@example.com"
        assert data["user"]["full_name"] == "New User"
        assert data["user"]["id"]  # non-empty string

    def test_first_user_is_admin(self, client, db_session):
        """When the users table is empty the first registered user gets admin role.

        We patch the User count query to return 0 so the register endpoint
        treats this as the very first user, regardless of pre-existing data.
        """

        def _patched_count():
            """Return 0 only for the User count check inside register."""
            return 0

        with patch.object(
            type(db_session.query(User)), "count", side_effect=_patched_count
        ):
            resp = client.post(
                "/api/auth/register",
                json={
                    "email": "firstadmin@example.com",
                    "password": "securepass123",
                    "full_name": "First Admin",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["user"]["role"] == "admin"

    def test_second_user_is_user(self, client, db_session, admin_user):
        """When at least one user exists, new registrations get the user role."""
        resp = client.post(
            "/api/auth/register",
            json={
                "email": "second@example.com",
                "password": "securepass123",
                "full_name": "Second User",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["user"]["role"] == "user"

    def test_register_duplicate_email_returns_400(self, client, db_session, admin_user):
        """Registering with an already-taken email should fail."""
        resp = client.post(
            "/api/auth/register",
            json={
                "email": admin_user.email,
                "password": "securepass123",
                "full_name": "Duplicate",
            },
        )
        assert resp.status_code == 400
        assert "already registered" in resp.json()["detail"].lower()


class TestLogin:
    """POST /api/auth/login"""

    def test_login_returns_token(self, client, db_session, admin_user):
        resp = client.post(
            "/api/auth/login",
            json={
                "email": admin_user.email,
                "password": "testpass123",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["email"] == admin_user.email

    def test_login_wrong_password_returns_401(self, client, db_session, admin_user):
        resp = client.post(
            "/api/auth/login",
            json={
                "email": admin_user.email,
                "password": "wrongpassword",
            },
        )
        assert resp.status_code == 401
        assert "invalid credentials" in resp.json()["detail"].lower()

    def test_login_nonexistent_user_returns_401(self, client, db_session):
        resp = client.post(
            "/api/auth/login",
            json={
                "email": "nobody@example.com",
                "password": "whatever",
            },
        )
        assert resp.status_code == 401

    def test_login_inactive_user_returns_401(self, client, db_session, admin_user):
        """A deactivated user cannot log in."""
        admin_user.is_active = False
        db_session.flush()

        resp = client.post(
            "/api/auth/login",
            json={
                "email": admin_user.email,
                "password": "testpass123",
            },
        )
        assert resp.status_code == 401
        assert "disabled" in resp.json()["detail"].lower()


class TestMe:
    """GET /api/auth/me"""

    def test_me_returns_user_info(self, client, admin_headers, admin_user):
        resp = client.get("/api/auth/me", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == admin_user.email
        assert data["full_name"] == admin_user.full_name
        assert data["role"] == "admin"
        assert data["id"] == admin_user.id

    def test_me_without_token_returns_401(self, client):
        """Requests without an Authorization header should be rejected."""
        resp = client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_me_with_invalid_token_returns_401(self, client):
        """A garbage token should be rejected."""
        resp = client.get(
            "/api/auth/me",
            headers={
                "Authorization": "Bearer invalid.token.here",
            },
        )
        assert resp.status_code == 401


class TestInviteUser:
    """POST /api/auth/invite"""

    def test_invite_creates_inactive_user(
        self, client, db_session, admin_headers, admin_user
    ):
        from tests.conftest import _make_organization, _make_membership

        org = _make_organization(db_session)
        _make_membership(db_session, admin_user, org, role="owner")

        # Get a system role
        role = (
            db_session.query(Role)
            .filter(Role.name == "team_member", Role.organization_id.is_(None))
            .first()
        )
        if not role:
            role = Role(
                name="team_member",
                display_name="Team Member",
                is_system=True,
                permissions=["tasks:read"],
            )
            db_session.add(role)
            db_session.flush()

        resp = client.post(
            "/api/auth/invite",
            json={
                "email": "invited@example.com",
                "org_id": org.id,
                "role_id": role.id,
                "venue_ids": [],
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify user created but inactive
        user = (
            db_session.query(User).filter(User.email == "invited@example.com").first()
        )
        assert user is not None
        assert user.is_active is False

    def test_invite_without_permission_returns_403(
        self, client, db_session, manager_headers
    ):
        resp = client.post(
            "/api/auth/invite",
            json={
                "email": "test@test.com",
                "org_id": "fake",
                "role_id": "fake",
            },
            headers=manager_headers,
        )
        assert resp.status_code == 403


class TestAcceptInvite:
    """POST /api/auth/accept-invite"""

    def test_accept_invite_activates_user(self, client, db_session):
        from app.auth.security import create_invite_token, hash_password

        user = User(
            email="pending@example.com",
            hashed_password=hash_password("temp"),
            full_name="Pending",
            role="user",
            is_active=False,
        )
        db_session.add(user)
        db_session.flush()

        token = create_invite_token(user.id)
        resp = client.post(
            "/api/auth/accept-invite",
            json={
                "token": token,
                "full_name": "Accepted User",
                "password": "newpass123",
            },
        )
        assert resp.status_code == 200
        db_session.refresh(user)
        assert user.is_active is True
        assert user.full_name == "Accepted User"

    def test_accept_with_invalid_token(self, client):
        resp = client.post(
            "/api/auth/accept-invite",
            json={
                "token": "invalid.token.here",
                "full_name": "Test",
                "password": "test123",
            },
        )
        assert resp.status_code == 400


class TestForgotPassword:
    """POST /api/auth/forgot-password"""

    def test_returns_200_for_existing_email(self, client, db_session, admin_user):
        resp = client.post(
            "/api/auth/forgot-password",
            json={"email": admin_user.email},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_returns_200_for_nonexistent_email(self, client):
        resp = client.post(
            "/api/auth/forgot-password",
            json={"email": "nobody@example.com"},
        )
        assert resp.status_code == 200  # Don't leak email existence


class TestResetPassword:
    """POST /api/auth/reset-password"""

    def test_reset_changes_password(self, client, db_session, admin_user):
        from app.auth.security import create_reset_token, verify_password

        token = create_reset_token(admin_user.id)
        resp = client.post(
            "/api/auth/reset-password",
            json={"token": token, "password": "brandnewpassword"},
        )
        assert resp.status_code == 200
        db_session.refresh(admin_user)
        assert verify_password("brandnewpassword", admin_user.hashed_password)

    def test_reset_with_invalid_token(self, client):
        resp = client.post(
            "/api/auth/reset-password",
            json={"token": "garbage", "password": "whatever"},
        )
        assert resp.status_code == 400
