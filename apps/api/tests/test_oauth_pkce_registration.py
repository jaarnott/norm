"""PKCE + dynamic client registration for public OAuth 2.1 clients.

These lock down the Cook Brothers App MCP connector's auth: a public client
(token_endpoint_auth_method=none) must use PKCE (S256) and self-register (RFC
7591), and the MCP executor must authenticate with the resulting access token.
Confidential clients (LoadedHub) must be unaffected.
"""

import base64
import hashlib
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

from app.services import oauth_service


def _expected_challenge(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )


def _public_spec(connector_name="cook_brothers_app"):
    spec = MagicMock()
    spec.connector_name = connector_name
    spec.auth_type = "oauth2"
    spec.auth_config = {}
    spec.oauth_config = {
        "authorize_url": "https://cb.example.com/oauth/authorize",
        "token_url": "https://cb.example.com/oauth/token",
        "registration_url": "https://cb.example.com/oauth/register",
        "scopes": "mcp",
        "token_endpoint_auth_method": "none",
        "pkce": True,
        "client_id": "public-cid",
    }
    return spec


class TestPkceGeneration:
    def test_challenge_is_s256_of_verifier(self):
        verifier, challenge = oauth_service._generate_pkce()
        assert 43 <= len(verifier) <= 128
        assert challenge == _expected_challenge(verifier)
        assert "=" not in challenge  # base64url, unpadded

    def test_uses_pkce_true_for_public_client(self):
        assert oauth_service._uses_pkce({"token_endpoint_auth_method": "none"}) is True
        assert oauth_service._uses_pkce({"pkce": True}) is True

    def test_uses_pkce_false_for_confidential_client(self):
        assert oauth_service._uses_pkce({"client_secret": "s"}) is False
        assert oauth_service._uses_pkce({}) is False


class TestAuthorizeUrlPkce:
    def test_public_client_authorize_url_has_pkce_and_persists_verifier(
        self, db_session
    ):
        from app.db.models import OAuthState

        url = oauth_service.build_authorize_url(
            _public_spec(), "https://norm/api/oauth/callback", db_session
        )
        q = parse_qs(urlparse(url).query)
        assert q["code_challenge_method"] == ["S256"]
        challenge = q["code_challenge"][0]
        state = q["state"][0]

        row = db_session.query(OAuthState).filter(OAuthState.state == state).first()
        assert row is not None
        assert row.code_verifier  # stored for the token exchange
        assert challenge == _expected_challenge(row.code_verifier)

    def test_confidential_client_authorize_url_has_no_pkce(self, db_session):
        spec = MagicMock()
        spec.connector_name = "loadedhub"
        spec.oauth_config = {
            "authorize_url": "https://auth.example.com/authorize",
            "client_id": "cid",
            "client_secret": "secret",
            "scopes": "read",
        }
        url = oauth_service.build_authorize_url(
            spec, "https://norm/api/oauth/callback", db_session
        )
        q = parse_qs(urlparse(url).query)
        assert "code_challenge" not in q
        assert "code_challenge_method" not in q


class TestDynamicRegistration:
    def test_register_client_posts_and_stores_client_id(self, db_session):
        from app.db.config_models import ConnectionSpec

        spec = ConnectionSpec(
            connector_name="cb_reg_test",
            display_name="CB",
            execution_mode="mcp",
            auth_type="oauth2",
            auth_config={},
            oauth_config={
                "registration_url": "https://cb.example.com/oauth/register",
                "token_endpoint_auth_method": "none",
                "scopes": "mcp",
            },
        )
        db_session.add(spec)
        db_session.flush()

        resp = MagicMock(status_code=201)
        resp.json.return_value = {"client_id": "registered-123"}
        with patch(
            "app.services.oauth_service.httpx.post", return_value=resp
        ) as mock_post:
            client_id = oauth_service.register_client(
                spec, ["https://norm/api/oauth/callback"], db_session
            )

        assert client_id == "registered-123"
        assert spec.oauth_config["client_id"] == "registered-123"
        body = mock_post.call_args.kwargs["json"]
        assert body["token_endpoint_auth_method"] == "none"
        assert body["redirect_uris"] == ["https://norm/api/oauth/callback"]
        assert "authorization_code" in body["grant_types"]

    def test_register_client_is_idempotent_when_client_id_present(self, db_session):
        spec = MagicMock()
        spec.connector_name = "cb"
        spec.oauth_config = {"client_id": "already", "registration_url": "https://x"}
        with patch("app.services.oauth_service.httpx.post") as mock_post:
            client_id = oauth_service.register_client(
                spec, ["https://n/cb"], db_session
            )
        assert client_id == "already"
        mock_post.assert_not_called()


class TestTokenExchangePublicClient:
    def test_exchange_sends_verifier_and_omits_secret(self, db_session):
        from app.db.models import OAuthState

        state = "st-pkce-1"
        db_session.add(
            OAuthState(
                connector_name="cook_brothers_app",
                state=state,
                venue_id=None,
                user_id=None,
                code_verifier="the-verifier",
            )
        )
        db_session.flush()

        resp = MagicMock(status_code=200)
        resp.json.return_value = {"access_token": "at", "refresh_token": "rt"}
        with patch(
            "app.services.oauth_service.httpx.post", return_value=resp
        ) as mock_post:
            oauth_service.exchange_code(
                _public_spec(),
                "the-code",
                state,
                "https://norm/api/oauth/callback",
                db_session,
            )

        body = mock_post.call_args.kwargs["data"]
        assert body["code_verifier"] == "the-verifier"
        assert body["grant_type"] == "authorization_code"
        assert "client_secret" not in body  # public client


class TestMcpExecutorOauthHeader:
    def test_oauth2_uses_access_token_as_bearer(self):
        from app.connectors.mcp_executor import _build_auth_headers

        headers = _build_auth_headers({"access_token": "tok-123"}, "oauth2", {})
        assert headers["Authorization"] == "Bearer tok-123"

    def test_bearer_still_reads_token_field(self):
        from app.connectors.mcp_executor import _build_auth_headers

        headers = _build_auth_headers(
            {"api_key": "k"}, "bearer", {"token_field": "api_key"}
        )
        assert headers["Authorization"] == "Bearer k"
