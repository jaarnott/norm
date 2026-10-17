"""MCP wire format, JSON-RPC dispatch, and the HTTP transport (POST /mcp).

The wire-format and dispatch tests are pure — no DB, no HTTP. The round-trip
tests are the load-bearing ones: Norm is about to sit on both ends of the MCP
wire, and the single most valuable guarantee is that what our server emits,
our own client can parse.

The transport tests at the end cover what the pure dispatch tests can't:
raw-body parsing, status codes, the MCP_ENABLED gate, the root mount, and the
401 challenge. The 401 tests are load-bearing. `WWW-Authenticate: ...
resource_metadata=...` is how claude.ai discovers the authorization server;
without it the connector cannot be added at all, and nothing else would
notice.
"""

import pytest

from app.config import settings
from app.connectors.mcp_executor import _parse_mcp_response
from app.connectors.mcp_protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    error_result,
    jsonrpc_error,
    jsonrpc_request,
    jsonrpc_result,
    resource_content,
    text_content,
    tools_call_result,
)
from app.mcp.principal import McpPrincipal
from app.mcp.server import (
    LATEST_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    McpContext,
    McpDispatchError,
    handle_jsonrpc,
)


class TestRoundTrip:
    """Our server's output must parse through our own client."""

    def test_dict_payload_round_trips(self):
        payload = {"total_sales": 1234.5, "items": [{"sku": "a"}, {"sku": "b"}]}
        body = jsonrpc_result(1, tools_call_result(payload))
        data, embeds, is_error = _parse_mcp_response(body)
        assert data == payload
        assert embeds == []
        assert is_error is False

    def test_error_result_round_trips_as_tool_error(self):
        body = jsonrpc_result(1, error_result("supplier API returned 500"))
        data, _embeds, is_error = _parse_mcp_response(body)
        assert is_error is True
        assert "supplier API returned 500" in str(data.get("error"))

    def test_jsonrpc_error_is_distinguishable_from_tool_error(self):
        """A protocol error and a tool error must not look alike to the client.

        _parse_mcp_response handles them on separate paths; if a tool failure
        were emitted as a JSON-RPC error, the client would treat a recoverable
        situation as a broken server.
        """
        body = jsonrpc_error(1, METHOD_NOT_FOUND, "Unknown method: nope")
        data, _embeds, is_error = _parse_mcp_response(body)
        assert is_error is True
        assert data["error"] == "Unknown method: nope"

    def test_resource_block_round_trips_as_embed(self):
        result = tools_call_result({"container_hint": "full_page"})
        result["content"].append(
            resource_content("https://example.com/x", uri="ui://x")
        )
        data, embeds, is_error = _parse_mcp_response(jsonrpc_result(1, result))
        assert is_error is False
        assert len(embeds) == 1
        assert embeds[0]["url"] == "https://example.com/x"
        assert embeds[0]["container_hint"] == "full_page"


class TestContentBlocks:
    def test_text_content_serializes_dicts(self):
        assert text_content({"a": 1}) == {"type": "text", "text": '{"a": 1}'}

    def test_text_content_passes_strings_through(self):
        assert text_content("hello") == {"type": "text", "text": "hello"}

    def test_structured_content_present_for_dicts(self):
        assert tools_call_result({"a": 1})["structuredContent"] == {"a": 1}

    def test_structured_content_absent_for_lists(self):
        assert "structuredContent" not in tools_call_result([1, 2])

    def test_structured_content_absent_on_error(self):
        assert "structuredContent" not in tools_call_result({"a": 1}, is_error=True)

    def test_single_text_block(self):
        """One block, because _parse_mcp_response merges many into one dict."""
        assert len(tools_call_result({"a": 1})["content"]) == 1


class TestEnvelope:
    def test_jsonrpc_request_shape(self):
        assert jsonrpc_request("tools/list", {"x": 1}, rpc_id=7) == {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/list",
            "params": {"x": 1},
        }

    def test_jsonrpc_error_omits_data_when_absent(self):
        assert "data" not in jsonrpc_error(1, INVALID_PARAMS, "bad")["error"]

    def test_jsonrpc_error_includes_data_when_given(self):
        assert jsonrpc_error(1, INVALID_PARAMS, "bad", {"f": "venue"})["error"][
            "data"
        ] == {"f": "venue"}


class TestDispatchInitialize:
    def test_echoes_supported_client_version(self):
        r = handle_jsonrpc(
            jsonrpc_request("initialize", {"protocolVersion": "2025-03-26"}),
            McpContext(),
        )
        assert r["result"]["protocolVersion"] == "2025-03-26"

    def test_proposes_latest_for_unknown_version(self):
        """Do not error — the spec wants the server to propose."""
        r = handle_jsonrpc(
            jsonrpc_request("initialize", {"protocolVersion": "1999-01-01"}),
            McpContext(),
        )
        assert r["result"]["protocolVersion"] == LATEST_PROTOCOL_VERSION

    def test_advertises_tools_and_ui(self):
        """Advertise exactly what we implement: tools, plus (with MCP Apps on)
        resources and the UI extension. Never advertise prompts, which we don't."""
        caps = handle_jsonrpc(jsonrpc_request("initialize"), McpContext())["result"][
            "capabilities"
        ]
        assert caps["tools"] == {"listChanged": False}
        # MCP_UI_ENABLED defaults on, so the UI extension is advertised.
        assert caps["resources"] == {"listChanged": False}
        assert "io.modelcontextprotocol/ui" in caps["extensions"]
        assert "prompts" not in caps

    def test_instructions_name_the_tool_as_it_actually_projects(self):
        """It used to say `resolve_dates`; the tool is `norm__resolve_dates`.
        Guidance pointing at a name absent from tools/list is guidance the
        client cannot follow."""
        from app.mcp.projection import default_tool_name

        r = handle_jsonrpc(jsonrpc_request("initialize"), McpContext())
        assert (
            default_tool_name("connector", "norm", "resolve_dates")
            in r["result"]["instructions"]
        )

    def test_instructions_follow_the_configured_day_start(self, monkeypatch):
        """The instructions carry the date authority, derived from config — and
        follow it when it moves: the regression the literal pin could not
        catch."""
        from app.config import settings
        from app.services.business_calendar import humanize_hhmm

        instructions = handle_jsonrpc(jsonrpc_request("initialize"), McpContext())[
            "result"
        ]["instructions"]
        # Derived from config, not pinned to a literal — the old test asserted
        # "7:00am Monday", which would keep passing while the configured start
        # moved and the text quietly lied to the client.
        assert humanize_hhmm(settings.BUSINESS_DAY_START) in instructions
        assert "Monday" in instructions

        monkeypatch.setattr(settings, "BUSINESS_DAY_START", "05:30")
        instructions = handle_jsonrpc(jsonrpc_request("initialize"), McpContext())[
            "result"
        ]["instructions"]
        assert "5:30am" in instructions and "5:29am" in instructions
        assert "7:00am" not in instructions

    def test_latest_version_is_first_supported(self):
        assert SUPPORTED_PROTOCOL_VERSIONS[0] == LATEST_PROTOCOL_VERSION


class TestDispatchErrors:
    def test_non_object_body(self):
        assert (
            handle_jsonrpc(["not", "an", "object"], McpContext())["error"]["code"]
            == INVALID_REQUEST
        )

    def test_wrong_jsonrpc_version(self):
        r = handle_jsonrpc({"jsonrpc": "1.0", "id": 1, "method": "ping"}, McpContext())
        assert r["error"]["code"] == INVALID_REQUEST

    def test_missing_method(self):
        r = handle_jsonrpc({"jsonrpc": "2.0", "id": 1}, McpContext())
        assert r["error"]["code"] == INVALID_REQUEST

    def test_unknown_method(self):
        # prompts/* is deliberately not implemented.
        r = handle_jsonrpc(jsonrpc_request("prompts/list"), McpContext())
        assert r["error"]["code"] == METHOD_NOT_FOUND

    def test_non_object_params(self):
        r = handle_jsonrpc(
            {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": []}, McpContext()
        )
        assert r["error"]["code"] == INVALID_PARAMS

    def test_error_id_is_echoed(self):
        r = handle_jsonrpc(jsonrpc_request("nope", rpc_id=42), McpContext())
        assert r["id"] == 42

    def test_unhandled_exception_leaks_nothing(self):
        """Dispatch never raises, and the error it returns leaks nothing.

        An exception escaping here would surface as a 500 with an HTML body,
        which no MCP client can parse."""

        class Exploding(McpContext):
            def list_tools(self):
                raise RuntimeError("connection string: postgres://user:hunter2@host")

        r = handle_jsonrpc(jsonrpc_request("tools/list"), Exploding())
        assert r["error"]["code"] == INTERNAL_ERROR
        assert "hunter2" not in str(r)
        assert "postgres" not in str(r)


class TestDispatchNotifications:
    @pytest.mark.parametrize(
        "method", ["notifications/initialized", "notifications/cancelled"]
    )
    def test_notifications_get_no_response(self, method):
        assert (
            handle_jsonrpc({"jsonrpc": "2.0", "method": method}, McpContext()) is None
        )


class TestDispatchTools:
    def test_tools_list_empty_by_default(self):
        r = handle_jsonrpc(jsonrpc_request("tools/list"), McpContext())
        assert r["result"]["tools"] == []

    def test_tools_list_never_paginates(self):
        r = handle_jsonrpc(jsonrpc_request("tools/list", {"cursor": "x"}), McpContext())
        assert "nextCursor" not in r["result"]

    def test_tools_call_requires_name(self):
        r = handle_jsonrpc(jsonrpc_request("tools/call", {}), McpContext())
        assert r["error"]["code"] == INVALID_PARAMS

    @pytest.mark.parametrize("bad", ["nope", [], 0, ["a"]])
    def test_tools_call_rejects_non_object_arguments(self, bad):
        """Includes falsy non-dicts ([], 0): `x or {}` would coerce those to {}
        and skip the type check entirely."""
        r = handle_jsonrpc(
            jsonrpc_request("tools/call", {"name": "x", "arguments": bad}),
            McpContext(),
        )
        assert r["error"]["code"] == INVALID_PARAMS

    def test_tools_call_allows_omitted_arguments(self):
        class Echo(McpContext):
            def call_tool(self, name, arguments):
                return tools_call_result({"got": arguments})

        r = handle_jsonrpc(jsonrpc_request("tools/call", {"name": "x"}), Echo())
        assert r["result"]["structuredContent"] == {"got": {}}

    def test_unknown_tool_is_invalid_params_not_a_tool_error(self):
        r = handle_jsonrpc(
            jsonrpc_request("tools/call", {"name": "nope"}), McpContext()
        )
        assert r["error"]["code"] == INVALID_PARAMS

    def test_tool_failure_is_a_result_not_a_jsonrpc_error(self):
        """The distinction that must never regress: a tool that ran and failed
        is a successful JSON-RPC result carrying isError, so the model can
        retry. A JSON-RPC error means the server is broken."""

        class Failing(McpContext):
            def call_tool(self, name, arguments):
                return error_result("supplier API returned 500")

        r = handle_jsonrpc(jsonrpc_request("tools/call", {"name": "x"}), Failing())
        assert "error" not in r
        assert r["result"]["isError"] is True

    def test_dispatch_error_from_context_becomes_jsonrpc_error(self):
        class Refusing(McpContext):
            def call_tool(self, name, arguments):
                raise McpDispatchError(INVALID_PARAMS, "start_date is required")

        r = handle_jsonrpc(jsonrpc_request("tools/call", {"name": "x"}), Refusing())
        assert r["error"]["code"] == INVALID_PARAMS
        assert "start_date" in r["error"]["message"]


class TestErrorCodes:
    def test_codes_match_the_jsonrpc_spec(self):
        assert (PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND) == (
            -32700,
            -32600,
            -32601,
        )
        assert (INVALID_PARAMS, INTERNAL_ERROR) == (-32602, -32603)


# ── HTTP transport — POST /mcp ───────────────────────────────────────────

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


class TestAuthChallenge:
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
        """No token: a transport-level auth failure must be a real 401 so the
        client re-authenticates. Wrapped in a 200 JSON-RPC error it would look like a
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
        assert init["capabilities"]["tools"] == {"listChanged": False}
        assert init["capabilities"]["resources"] == {"listChanged": False}
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
