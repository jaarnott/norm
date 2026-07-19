"""End-to-end MCP exercise of a `*_for_period` tool, run locally.

Why this file exists — three bugs shipped to production in a row, each one
hidden by the previous, and none catchable by the unit tests:

1. The consolidator's `call_api("norm", "resolve_dates", ...)` had no internal
   handler dispatch, so it rendered an HTTP request against a spec with no
   base_url: "Request URL is missing an 'http://' or 'https://' protocol".
2. Fixed that, and the window resolved correctly — then every call died with
   "Missing required fields: interval", because the wrapper declared
   `required_fields: []` and gave the caller no way to supply what
   get_sales_data requires.
3. Both were only visible by asking Claude to call the real production server.

Each layer works in isolation; the failures were all at the seams. So this
drives the whole path — project_tools -> venue authorization -> consolidator
sandbox -> internal handler -> param forwarding -> the executor's required-field
validation — against spec rows built by the sync script itself, with only the
outbound HTTP call stubbed. A regression in any seam fails here, locally,
before a commit.
"""

import importlib.util
import pathlib
import uuid

import pytest

from app.db.config_models import AgentConnectorBinding, ConnectorSpec, McpCapability
from app.db.models import ConnectorConfig, Organization, User, UserVenueAccess, Venue
from app.mcp.principal import McpPrincipal

SYNC_SCRIPT = (
    pathlib.Path(__file__).resolve().parent.parent
    / "scripts"
    / "sync_for_period_config.py"
)

# Shaped like the real loadedhub row: a required non-date argument is the whole
# point — that is the field the wrapper used to hide.
GET_SALES_DATA = {
    "action": "get_sales_data",
    "method": "GET",
    "path_template": "/sales",
    "required_fields": ["interval", "start_datetime", "end_datetime"],
    "optional_fields": ["posIdentifier"],
    "field_descriptions": {"interval": "Bucket size, e.g. 1.00:00:00"},
}

RESOLVE_DATES = {
    "action": "resolve_dates",
    "method": "GET",
    "required_fields": [],
    "optional_fields": ["query", "start", "end", "venue_id"],
}


def _sync_module():
    spec = importlib.util.spec_from_file_location("sync_for_period", SYNC_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def wired(db_session, monkeypatch):
    """A venue, a principal, and the connector rows the MCP surface reads."""
    # write_audit opens its own SessionLocal, so it cannot see this test's
    # uncommitted transaction. Auditing has its own coverage in
    # test_mcp_audit.py; silencing it here keeps this test on the seam it
    # exists to guard.
    import app.mcp.execution as execution_mod

    monkeypatch.setattr(execution_mod, "write_audit", lambda **kw: None)

    # The consolidator sandbox fetches connector specs on its own config-DB
    # session, which cannot see this test's uncommitted transaction. Point it
    # at the test session so the "norm" spec (and so resolve_dates) resolves.
    import app.db.engine as engine_mod

    class _SessionProxy:
        def __init__(self, s):
            self._s = s

        def query(self, *a, **k):
            return self._s.query(*a, **k)

        def expunge(self, obj):
            pass  # detaching would break the caller's session

        def close(self):
            pass

    monkeypatch.setattr(
        engine_mod, "_ConfigSessionLocal", lambda: _SessionProxy(db_session)
    )

    m = _sync_module()
    function_code = m.FUNCTION_CODE_PATH.read_text(encoding="utf-8")
    tool = m.tool_for(
        "get_sales_for_period",
        "get_sales_data",
        "start_datetime",
        "end_datetime",
        "Sales totals",
        function_code,
        wrapped=GET_SALES_DATA,
    )

    org = Organization(id=str(uuid.uuid4()), name="Cook", slug=f"o{uuid.uuid4().hex[:6]}")
    db_session.add(org)
    db_session.flush()
    venue = Venue(
        id=str(uuid.uuid4()),
        name="La Zeppa",
        timezone="Pacific/Auckland",
        organization_id=org.id,
        day_start_time="07:00",
    )
    db_session.add(venue)
    user = User(
        id=str(uuid.uuid4()),
        email=f"u{uuid.uuid4().hex[:6]}@x.com",
        hashed_password="x",
        full_name="U",
        role="user",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(UserVenueAccess(user_id=user.id, venue_id=venue.id))
    db_session.add(
        ConnectorConfig(
            id=str(uuid.uuid4()),
            connector_name="loadedhub",
            enabled="true",
            config={"api_key": "x"},
            venue_id=venue.id,
        )
    )
    db_session.add_all(
        [
            ConnectorSpec(
                connector_name="loadedhub",
                display_name="LoadedHub",
                execution_mode="template",
                auth_type="api_key",
                base_url_template="https://api.example.com",
                tools=[GET_SALES_DATA, tool],
                enabled=True,
            ),
            ConnectorSpec(
                connector_name="norm",
                display_name="Norm",
                execution_mode="internal",
                auth_type="none",
                tools=[RESOLVE_DATES],
                enabled=True,
            ),
            AgentConnectorBinding(
                agent_slug="ops",
                connector_name="loadedhub",
                capabilities=[
                    {"action": "get_sales_data", "enabled": True},
                    {"action": "get_sales_for_period", "enabled": True},
                ],
                enabled=True,
            ),
            AgentConnectorBinding(
                agent_slug="ops",
                connector_name="norm",
                capabilities=[{"action": "resolve_dates", "enabled": True}],
                enabled=True,
            ),
            McpCapability(
                kind="connector",
                target="loadedhub",
                action="get_sales_for_period",
                scopes=["mcp:reports:read"],
                enabled=True,
            ),
        ]
    )
    db_session.flush()

    principal = McpPrincipal(
        user_id=user.id,
        organization_id=org.id,
        venue_ids=(venue.id,),
        scopes=frozenset({"mcp:reports:read", "mcp:venues:read"}),
    )
    return {"principal": principal, "venue": venue, "db": db_session}


def _stub_upstream(monkeypatch, sink):
    """Stub only the outbound HTTP hop. execute_spec still runs, so its
    required-field validation — the thing that produced the production error —
    is exercised for real."""
    import app.connectors.spec_executor as se

    real = se.execute_spec

    def wrapper(spec, tool_def, params, credentials, db, thread_id, venue_id=None):
        if tool_def.get("action") == "get_sales_data":
            sink.append(dict(params))
        return real(spec, tool_def, params, credentials, db, thread_id, venue_id=venue_id)

    monkeypatch.setattr(se, "execute_spec", wrapper)

    import httpx

    def fake_request(**kw):
        return httpx.Response(
            200,
            json={"total": 15945},
            request=httpx.Request("GET", kw.get("url", "https://api.example.com")),
        )

    monkeypatch.setattr(httpx, "request", lambda **kw: fake_request(**kw))


def _ctx(wired):
    from app.mcp.execution import NormMcpContext

    return NormMcpContext(
        principal=wired["principal"], db=wired["db"], config_db=wired["db"]
    )


class TestForPeriodEndToEnd:
    def test_the_tool_is_published(self, wired):
        names = {t["name"] for t in _ctx(wired).list_tools()}
        assert "loadedhub__get_sales_for_period" in names

    def test_the_schema_lets_the_caller_supply_what_the_wrapped_action_needs(
        self, wired
    ):
        """Bug 2: the window resolved, then the call died on a field the caller
        was never offered."""
        tool = next(
            t
            for t in _ctx(wired).list_tools()
            if t["name"] == "loadedhub__get_sales_for_period"
        )
        props = tool["inputSchema"]["properties"]
        assert "interval" in props
        assert "interval" in tool["inputSchema"].get("required", [])
        # The date params it replaces stay hidden.
        assert "start_datetime" not in props and "end_datetime" not in props

    def test_a_period_call_resolves_the_trading_day_and_returns_data(
        self, wired, monkeypatch
    ):
        """The whole seam, in one assertion: internal-handler dispatch (bug 1),
        param forwarding (bug 2), and the trading-day window itself."""
        sent: list[dict] = []
        _stub_upstream(monkeypatch, sent)
        ctx = _ctx(wired)

        # Build the call from the PUBLISHED schema only — a caller cannot pass
        # a field the tool never offered. If the wrapper hides `interval`, this
        # call omits it and fails exactly as production did.
        tool = next(
            t
            for t in ctx.list_tools()
            if t["name"] == "loadedhub__get_sales_for_period"
        )
        args = {"period": "yesterday"}
        for field in tool["inputSchema"].get("required", []):
            args[field] = "1.00:00:00"

        out = ctx.call_tool("loadedhub__get_sales_for_period", args)
        assert not out.get("isError"), out

        assert sent, "the wrapped action was never reached"
        forwarded = sent[0]
        assert forwarded["interval"] == "1.00:00:00"
        # 07:00 boundary, not midnight — the rule this whole family exists for.
        assert "T07:00" in forwarded["start_datetime"]
        assert "period" not in forwarded

    def test_the_result_states_the_window_it_used(self, wired, monkeypatch):
        import json

        _stub_upstream(monkeypatch, [])
        out = _ctx(wired).call_tool(
            "loadedhub__get_sales_for_period",
            {"period": "yesterday", "interval": "1.00:00:00"},
        )
        body = json.loads(out["content"][0]["text"])
        assert "window" in body
        assert body["window"]["day_start"] == "07:00"
