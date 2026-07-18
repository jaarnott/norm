"""OAuth 2.1 authorization server.

The security-critical surface: DCR, PKCE, single-use codes, refresh rotation
with reuse detection, redirect-URI validation, and the opaque-token isolation
that is the whole reason for not using JWTs. These tests are the safety
argument for exposing Norm to third-party AI clients.
"""

import base64
import hashlib
import secrets
import uuid

import pytest

from app.config import settings
from app.db.models import (
    OrganizationMembership,
    Role,
    User,
    UserVenueAccess,
    Venue,
)
from app.auth.security import create_access_token, hash_password

REDIRECT = "https://claude.ai/api/mcp/auth_callback"


@pytest.fixture(autouse=True)
def _mcp_on():
    prev = settings.MCP_ENABLED
    settings.MCP_ENABLED = True
    yield
    settings.MCP_ENABLED = prev


@pytest.fixture()
def owner_role(db_session):
    """A system 'owner' role with all org scopes.

    The test DB is built with create_all, not migrations, so the seeded system
    roles aren't present — create one.
    """
    from app.auth.permissions import ALL_ORG_PERMISSIONS

    role = Role(
        id=str(uuid.uuid4()),
        organization_id=None,
        name="owner",
        display_name="Owner",
        is_system=True,
        permissions=list(ALL_ORG_PERMISSIONS),
    )
    db_session.add(role)
    db_session.flush()
    return role


@pytest.fixture()
def user_with_org(db_session, owner_role):
    """A user who owns one org with one venue they can access."""
    from app.db.models import Organization

    user = User(
        id=str(uuid.uuid4()),
        email=f"owner-{uuid.uuid4().hex[:8]}@x.com",
        hashed_password=hash_password("pw"),
        full_name="Owner",
        role="user",
        is_active=True,
    )
    org = Organization(
        id=str(uuid.uuid4()), name="Cook Bros", slug=f"cb-{uuid.uuid4().hex[:6]}"
    )
    db_session.add_all([user, org])
    db_session.flush()
    db_session.add(
        OrganizationMembership(
            id=str(uuid.uuid4()),
            user_id=user.id,
            organization_id=org.id,
            role="owner",
            role_id=owner_role.id,
        )
    )
    venue = Venue(
        id=str(uuid.uuid4()),
        name="La Zeppa",
        timezone="Pacific/Auckland",
        organization_id=org.id,
    )
    db_session.add(venue)
    db_session.flush()
    db_session.add(
        UserVenueAccess(id=str(uuid.uuid4()), user_id=user.id, venue_id=venue.id)
    )
    db_session.flush()
    return user, org, venue


@pytest.fixture()
def jwt(user_with_org):
    user, _org, _venue = user_with_org
    return create_access_token({"sub": user.id})


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def _register(client, **overrides):
    body = {"client_name": "Claude", "redirect_uris": [REDIRECT]}
    body.update(overrides)
    return client.post("/api/mcp/oauth/register", json=body)


def _consent_to_code(client, jwt, user_with_org, client_id, challenge, scope):
    _user, org, venue = user_with_org
    r = client.post(
        "/api/mcp/oauth/consent",
        json={
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "scope": scope,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "organization_id": org.id,
            "venue_ids": [venue.id],
            "approved_scopes": scope.split(),
            "action": "approve",
        },
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert r.status_code == 200, r.text
    return r.json()["redirect_to"].split("code=")[1].split("&")[0]


def _full_token(client, jwt, user_with_org, scope="mcp:venues:read"):
    cid = _register(client).json()["client_id"]
    verifier, challenge = _pkce()
    code = _consent_to_code(client, jwt, user_with_org, cid, challenge, scope)
    resp = client.post(
        "/api/mcp/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "client_id": cid,
            "code_verifier": verifier,
        },
    )
    return cid, verifier, resp


class TestDiscovery:
    def test_protected_resource_metadata(self, client):
        m = client.get("/.well-known/oauth-protected-resource/mcp").json()
        assert m["resource"].endswith("/mcp")
        assert "mcp:reports:read" in m["scopes_supported"]

    def test_authorization_server_metadata(self, client):
        m = client.get("/.well-known/oauth-authorization-server").json()
        assert m["code_challenge_methods_supported"] == ["S256"]  # no plain
        assert "authorization_code" in m["grant_types_supported"]
        assert "/mcp/authorize" in m["authorization_endpoint"]


class TestRegistration:
    def test_public_client_gets_no_secret(self, client):
        r = _register(client)
        assert r.status_code == 201
        assert "client_secret" not in r.json()
        assert r.json()["client_id"].startswith("mcpc_")

    def test_confidential_client_gets_a_secret(self, client):
        r = _register(client, token_endpoint_auth_method="client_secret_post")
        assert "client_secret" in r.json()

    def test_disallowed_redirect_host_is_rejected(self, client):
        r = _register(client, redirect_uris=["https://evil.example.com/cb"])
        assert r.status_code == 400

    def test_http_redirect_rejected_except_loopback(self, client):
        assert (
            _register(client, redirect_uris=["http://claude.ai/cb"]).status_code == 400
        )
        assert (
            _register(client, redirect_uris=["http://localhost:9999/cb"]).status_code
            == 201
        )


class TestAuthorize:
    def _params(self, cid, challenge, **kw):
        p = {
            "response_type": "code",
            "client_id": cid,
            "redirect_uri": REDIRECT,
            "scope": "mcp:venues:read",
            "state": "xyz",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        p.update(kw)
        return p

    def test_valid_request_redirects_to_consent(self, client):
        cid = _register(client).json()["client_id"]
        _v, ch = _pkce()
        r = client.get(
            "/api/mcp/oauth/authorize",
            params=self._params(cid, ch),
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert "/mcp/authorize" in r.headers["location"]

    def test_unknown_client_does_not_redirect(self, client):
        _v, ch = _pkce()
        r = client.get(
            "/api/mcp/oauth/authorize",
            params=self._params("mcpc_nope", ch),
            follow_redirects=False,
        )
        assert r.status_code == 400
        assert "location" not in r.headers

    def test_unregistered_redirect_uri_does_not_redirect(self, client):
        cid = _register(client).json()["client_id"]
        _v, ch = _pkce()
        r = client.get(
            "/api/mcp/oauth/authorize",
            params=self._params(cid, ch, redirect_uri="https://claude.ai/other"),
            follow_redirects=False,
        )
        assert r.status_code == 400
        assert "location" not in r.headers

    def test_plain_pkce_is_rejected(self, client):
        cid = _register(client).json()["client_id"]
        _v, ch = _pkce()
        r = client.get(
            "/api/mcp/oauth/authorize",
            params=self._params(cid, ch, code_challenge_method="plain"),
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert "error=invalid_request" in r.headers["location"]

    def test_unknown_scope_is_rejected(self, client):
        cid = _register(client).json()["client_id"]
        _v, ch = _pkce()
        r = client.get(
            "/api/mcp/oauth/authorize",
            params=self._params(cid, ch, scope="mcp:everything"),
            follow_redirects=False,
        )
        assert "error=invalid_scope" in r.headers["location"]


class TestConsentContext:
    def test_lists_org_venues_and_grantable_scopes(self, client, jwt, user_with_org):
        _user, org, venue = user_with_org
        cid = _register(client).json()["client_id"]
        ctx = client.get(
            "/api/mcp/oauth/consent-context",
            params={
                "client_id": cid,
                "scope": "mcp:reports:read",
                "redirect_uri": REDIRECT,
            },
            headers={"Authorization": f"Bearer {jwt}"},
        ).json()
        assert ctx["organizations"][0]["organization_id"] == org.id
        assert [v["name"] for v in ctx["organizations"][0]["venues"]] == ["La Zeppa"]
        assert "mcp:reports:read" in ctx["organizations"][0]["grantable_scopes"]

    def test_requires_auth(self, client):
        cid = _register(client).json()["client_id"]
        r = client.get(
            "/api/mcp/oauth/consent-context",
            params={"client_id": cid, "scope": "", "redirect_uri": REDIRECT},
        )
        assert r.status_code in (401, 403)


class TestTokenExchange:
    def test_happy_path(self, client, jwt, user_with_org):
        _cid, _v, resp = _full_token(client, jwt, user_with_org)
        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"].startswith("norm_mcp_at_")
        assert body["refresh_token"].startswith("norm_mcp_rt_")
        assert body["token_type"] == "Bearer"

    def test_wrong_pkce_verifier_rejected(self, client, jwt, user_with_org):
        cid = _register(client).json()["client_id"]
        _verifier, challenge = _pkce()
        code = _consent_to_code(
            client, jwt, user_with_org, cid, challenge, "mcp:venues:read"
        )
        r = client.post(
            "/api/mcp/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT,
                "client_id": cid,
                "code_verifier": "wrong-" + secrets.token_urlsafe(48),
            },
        )
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_grant"

    def test_code_is_single_use(self, client, jwt, user_with_org):
        cid = _register(client).json()["client_id"]
        verifier, challenge = _pkce()
        code = _consent_to_code(
            client, jwt, user_with_org, cid, challenge, "mcp:venues:read"
        )
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "client_id": cid,
            "code_verifier": verifier,
        }
        assert client.post("/api/mcp/oauth/token", data=data).status_code == 200
        assert client.post("/api/mcp/oauth/token", data=data).status_code == 400

    def test_redirect_uri_must_match(self, client, jwt, user_with_org):
        cid = _register(client).json()["client_id"]
        verifier, challenge = _pkce()
        code = _consent_to_code(
            client, jwt, user_with_org, cid, challenge, "mcp:venues:read"
        )
        r = client.post(
            "/api/mcp/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "https://claude.ai/different",
                "client_id": cid,
                "code_verifier": verifier,
            },
        )
        assert r.json()["error"] == "invalid_grant"


class TestRefreshRotation:
    def test_refresh_issues_new_pair(self, client, jwt, user_with_org):
        cid, _v, resp = _full_token(client, jwt, user_with_org)
        rt = resp.json()["refresh_token"]
        r = client.post(
            "/api/mcp/oauth/token",
            data={"grant_type": "refresh_token", "refresh_token": rt, "client_id": cid},
        )
        assert r.status_code == 200
        assert r.json()["refresh_token"] != rt

    def test_reuse_of_old_refresh_kills_the_family(self, client, jwt, user_with_org):
        cid, _v, resp = _full_token(client, jwt, user_with_org)
        rt = resp.json()["refresh_token"]
        first = client.post(
            "/api/mcp/oauth/token",
            data={"grant_type": "refresh_token", "refresh_token": rt, "client_id": cid},
        ).json()
        reuse = client.post(
            "/api/mcp/oauth/token",
            data={"grant_type": "refresh_token", "refresh_token": rt, "client_id": cid},
        )
        assert reuse.status_code == 400
        after = client.post(
            "/api/mcp/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": first["refresh_token"],
                "client_id": cid,
            },
        )
        assert after.status_code == 400


class TestTokenIsolation:
    """The whole reason for opaque tokens over JWTs."""

    def test_mcp_token_is_rejected_by_api_routes(self, client, jwt, user_with_org):
        _cid, _v, resp = _full_token(client, jwt, user_with_org)
        access = resp.json()["access_token"]
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {access}"})
        assert r.status_code == 401

    def test_refresh_token_is_not_a_bearer(self, client, jwt, user_with_org):
        _cid, _v, resp = _full_token(client, jwt, user_with_org)
        refresh = resp.json()["refresh_token"]
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Authorization": f"Bearer {refresh}"},
        )
        assert r.status_code == 401


class TestRevocation:
    def test_revoke_is_immediate(self, client, jwt, user_with_org):
        _cid, _v, resp = _full_token(client, jwt, user_with_org)
        access = resp.json()["access_token"]
        assert (
            client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={"Authorization": f"Bearer {access}"},
            ).status_code
            == 200
        )
        client.post("/api/mcp/oauth/revoke", data={"token": access})
        assert (
            client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={"Authorization": f"Bearer {access}"},
            ).status_code
            == 401
        )

    def test_revoke_unknown_token_is_still_200(self, client):
        """A 404 would be a token-existence oracle (RFC 7009 §2.2)."""
        r = client.post("/api/mcp/oauth/revoke", data={"token": "norm_mcp_at_nope"})
        assert r.status_code == 200

    def test_disconnect_revokes_the_grant(self, client, jwt, user_with_org):
        _cid, _v, resp = _full_token(client, jwt, user_with_org)
        access = resp.json()["access_token"]
        conns = client.get(
            "/api/mcp/connections", headers={"Authorization": f"Bearer {jwt}"}
        ).json()
        assert len(conns) == 1
        client.delete(
            f"/api/mcp/connections/{conns[0]['grant_id']}",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Authorization": f"Bearer {access}"},
        )
        assert r.status_code == 401


class TestConsentDownscoping:
    def test_cannot_approve_more_than_requested(self, client, jwt, user_with_org):
        _user, org, venue = user_with_org
        cid = _register(client).json()["client_id"]
        _v, challenge = _pkce()
        r = client.post(
            "/api/mcp/oauth/consent",
            json={
                "client_id": cid,
                "redirect_uri": REDIRECT,
                "scope": "mcp:venues:read",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "organization_id": org.id,
                "venue_ids": [venue.id],
                "approved_scopes": ["mcp:venues:read", "mcp:orders:draft"],
                "action": "approve",
            },
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert r.status_code == 400

    def test_deny_returns_access_denied(self, client, jwt, user_with_org):
        _user, org, venue = user_with_org
        cid = _register(client).json()["client_id"]
        _v, challenge = _pkce()
        r = client.post(
            "/api/mcp/oauth/consent",
            json={
                "client_id": cid,
                "redirect_uri": REDIRECT,
                "scope": "mcp:venues:read",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "organization_id": org.id,
                "venue_ids": [venue.id],
                "approved_scopes": [],
                "action": "deny",
            },
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert "error=access_denied" in r.json()["redirect_to"]
