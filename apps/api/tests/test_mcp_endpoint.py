"""MCP HTTP transport — POST /mcp.

Covers what the pure dispatch tests can't: raw-body parsing, status codes, the
MCP_ENABLED gate, the root mount, and the 401 challenge.

The 401 tests are load-bearing. `WWW-Authenticate: ... resource_metadata=...`
is how claude.ai discovers the authorization server; without it the connector
cannot be added at all, and nothing else would notice.
"""

import pytest

from app.config import settings
from app.connectors.mcp_protocol import PARSE_ERROR
from app.mcp.principal import McpPrincipal

AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture()
def mcp_on(monkeypatch):
    """Enable the MCP surface and stub auth for one test.

    settings is an lru_cache'd singleton, so mutate and restore rather than
    rebuilding it. Auth is stubbed at resolve_principal so these tests stay
    about transport; the real resolution is covered elsewhere.
    """
    previous = settings.MCP_ENABLED
    settings.MCP_ENABLED = True

    principal = McpPrincipal(
        user_id="u1",
        organization_id="org1",
        venue_ids=(),
        scopes=frozenset(),
        client_id="test",
    )
    monkeypatch.setattr(
        "app.routers.mcp.resolve_principal", lambda token, db: principal
    )
    # No tools — transport tests must not depend on config rows.
    monkeypatch.setattr("app.mcp.execution.project_tools", lambda *a, **k: [])
    yield principal
    settings.MCP_ENABLED = previous


@pytest.fixture()
def mcp_no_auth(monkeypatch):
    """MCP enabled, auth left real, so the 401 path is exercised."""
    previous = settings.MCP_ENABLED
    settings.MCP_ENABLED = True
    yield
    settings.MCP_ENABLED = previous


def _rpc(client, method, params=None, rpc_id=1):
    body = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=AUTH)


class TestEnabledGate:
    def test_disabled_by_default(self, client):
        """Off unless deliberately switched on — it must not appear by accident."""
        assert settings.MCP_ENABLED is False
        assert _rpc(client, "ping").status_code == 404

    def test_disabled_gate_precedes_auth(self, client):
        """404, not 401 — a disabled surface must not advertise that it exists."""
        assert (
            client.post("/mcp", json={"jsonrpc": "2.0", "method": "ping"}).status_code
            == 404
        )

    def test_enabled_responds(self, client, mcp_on):
        assert _rpc(client, "ping").status_code == 200


class TestAuthChallenge:
    def test_no_token_is_401(self, client, mcp_no_auth):
        r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert r.status_code == 401

    def test_401_carries_the_header_claude_needs_to_find_the_as(
        self, client, mcp_no_auth
    ):
        """Without resource_metadata, claude.ai cannot discover the
        authorization server and the connector cannot be added."""
        r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        challenge = r.headers["WWW-Authenticate"]
        assert challenge.startswith("Bearer ")
        assert "resource_metadata=" in challenge
        assert "/.well-known/oauth-protected-resource" in challenge

    def test_bad_token_is_401(self, client, mcp_no_auth):
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Authorization": "Bearer nope"},
        )
        assert r.status_code == 401

    def test_malformed_authorization_header_is_401(self, client, mcp_no_auth):
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Authorization": "Basic abc123"},
        )
        assert r.status_code == 401

    def test_auth_failure_is_http_not_jsonrpc(self, client, mcp_no_auth):
        """A transport-level auth failure must be a real 401 so the client
        re-authenticates. Wrapped in a 200 JSON-RPC error it would look like a
        tool problem and the client would never re-auth."""
        r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert r.status_code == 401
        assert "jsonrpc" not in r.json()


class TestTransport:
    def test_mounted_at_root_not_under_api(self, client, mcp_on):
        assert _rpc(client, "ping").status_code == 200
        assert client.post("/api/mcp", json={}).status_code == 404

    def test_malformed_body_is_a_jsonrpc_parse_error(self, client, mcp_on):
        """Not FastAPI's 422 — a client that can't parse the error can't recover."""
        r = client.post(
            "/mcp",
            content=b"{",
            headers={"Content-Type": "application/json", **AUTH},
        )
        assert r.status_code == 200
        assert r.json()["error"]["code"] == PARSE_ERROR

    def test_notification_gets_202_and_empty_body(self, client, mcp_on):
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=AUTH,
        )
        assert r.status_code == 202
        assert r.content == b""

    @pytest.mark.parametrize("verb", ["get", "delete"])
    def test_no_sse_or_sessions(self, client, mcp_on, verb):
        """405 so clients skip the SSE/session probes cleanly rather than
        interpreting a 404 as 'server missing'."""
        r = getattr(client, verb)("/mcp")
        assert r.status_code == 405
        assert r.headers["Allow"] == "POST"


class TestHandshake:
    def test_full_handshake(self, client, mcp_on):
        init = _rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        ).json()["result"]

        assert init["protocolVersion"] == "2025-06-18"
        assert init["capabilities"] == {"tools": {"listChanged": False}}
        assert init["serverInfo"]["name"] == "norm"
        assert "resolve_dates" in init["instructions"]

        assert (
            client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=AUTH,
            ).status_code
            == 202
        )

        tools = _rpc(client, "tools/list").json()["result"]
        assert tools["tools"] == []
        assert "nextCursor" not in tools
