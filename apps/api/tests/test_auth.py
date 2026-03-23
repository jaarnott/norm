"""Tests for authentication endpoints: register, login, me."""

from unittest.mock import patch, MagicMock

import pytest

from app.db.models import User


class TestRegister:
    """POST /api/auth/register"""

    def test_register_creates_user(self, client, db_session):
        resp = client.post("/api/auth/register", json={
            "email": "newuser@example.com",
            "password": "securepass123",
            "full_name": "New User",
        })
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
        original_count = db_session.query(User).count

        def _patched_count():
            """Return 0 only for the User count check inside register."""
            return 0

        with patch.object(
            type(db_session.query(User)), "count", side_effect=_patched_count
        ):
            resp = client.post("/api/auth/register", json={
                "email": "firstadmin@example.com",
                "password": "securepass123",
                "full_name": "First Admin",
            })
        assert resp.status_code == 200
        assert resp.json()["user"]["role"] == "admin"

    def test_second_user_is_manager(self, client, db_session, admin_user):
        """When at least one user exists, new registrations get the manager role."""
        resp = client.post("/api/auth/register", json={
            "email": "second@example.com",
            "password": "securepass123",
            "full_name": "Second User",
        })
        assert resp.status_code == 200
        assert resp.json()["user"]["role"] == "manager"

    def test_register_duplicate_email_returns_400(self, client, db_session, admin_user):
        """Registering with an already-taken email should fail."""
        resp = client.post("/api/auth/register", json={
            "email": admin_user.email,
            "password": "securepass123",
            "full_name": "Duplicate",
        })
        assert resp.status_code == 400
        assert "already registered" in resp.json()["detail"].lower()


class TestLogin:
    """POST /api/auth/login"""

    def test_login_returns_token(self, client, db_session, admin_user):
        resp = client.post("/api/auth/login", json={
            "email": admin_user.email,
            "password": "testpass123",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["email"] == admin_user.email

    def test_login_wrong_password_returns_401(self, client, db_session, admin_user):
        resp = client.post("/api/auth/login", json={
            "email": admin_user.email,
            "password": "wrongpassword",
        })
        assert resp.status_code == 401
        assert "invalid credentials" in resp.json()["detail"].lower()

    def test_login_nonexistent_user_returns_401(self, client, db_session):
        resp = client.post("/api/auth/login", json={
            "email": "nobody@example.com",
            "password": "whatever",
        })
        assert resp.status_code == 401

    def test_login_inactive_user_returns_401(self, client, db_session, admin_user):
        """A deactivated user cannot log in."""
        admin_user.is_active = False
        db_session.flush()

        resp = client.post("/api/auth/login", json={
            "email": admin_user.email,
            "password": "testpass123",
        })
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
        resp = client.get("/api/auth/me", headers={
            "Authorization": "Bearer invalid.token.here",
        })
        assert resp.status_code == 401
