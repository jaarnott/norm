"""tool_executor dispatch and credential resolution.

Two defects this file pins:

1. execute_connector_tool only ever checked `consolidator_config`, so every
   @register'd internal tool fell through to execute_spec and failed. It now
   checks get_handler() first, mirroring tool_loop._execute_tool_call.
2. _resolve_credentials fell back to *any* enabled config when the requested
   venue had none — a cross-venue leak on any authenticated, venue-scoped
   path. `strict_venue=True` restricts the fallback to platform configs.
"""

import uuid

import pytest

from app.connectors.tool_executor import (
    _apply_transform,
    _resolve_credentials,
    execute_connector_tool,
)
from app.db.config_models import ConnectorSpec
from app.db.models import ConnectorConfig, Venue


def _spec(db, connector_name, tools, execution_mode="internal"):
    spec = ConnectorSpec(
        connector_name=connector_name,
        display_name=connector_name,
        execution_mode=execution_mode,
        auth_type="bearer",
        tools=tools,
    )
    db.add(spec)
    db.flush()
    return spec


def _venue(db, name):
    v = Venue(id=str(uuid.uuid4()), name=name, timezone="Pacific/Auckland")
    db.add(v)
    db.flush()
    return v


def _config(db, connector_name, venue_id, secret):
    c = ConnectorConfig(
        connector_name=connector_name,
        venue_id=venue_id,
        enabled="true",
        config={"api_key": secret},
    )
    db.add(c)
    db.flush()
    return c


@pytest.fixture()
def connector():
    """A connector name unique to this test.

    The suite runs against the same database as local dev (ci.yml points
    DATABASE_URL and CONFIG_DATABASE_URL at the same local postgres), which
    holds real ConnectorConfig rows. A fixed name like "loadedhub" collides
    with them, and `.first()` may return the real row instead of the fixture's.
    """
    return f"testconn_{uuid.uuid4().hex[:12]}"


class TestInternalHandlerDispatch:
    def test_registered_internal_handler_is_used(
        self, db_session, monkeypatch, connector
    ):
        """The bug: without a get_handler lookup this fell through to
        execute_spec and failed, making every internal tool unreachable."""
        _spec(db_session, connector, [{"action": "ping_tool", "method": "GET"}])

        called = {}

        def fake_handler(params, db, thread_id):
            called["params"] = params
            return {"success": True, "data": {"pong": True}}

        monkeypatch.setattr(
            "app.agents.internal_tools.get_handler",
            lambda c, a: fake_handler if (c, a) == (connector, "ping_tool") else None,
        )

        res = execute_connector_tool(
            connector, "ping_tool", {"x": 1}, db_session, db_session
        )
        assert res.success is True
        assert res.payload == {"pong": True}
        assert res.rendered_request["method"] == "INTERNAL"
        assert called["params"]["x"] == 1

    def test_handler_shadows_consolidator(self, db_session, monkeypatch, connector):
        """Ordering must match tool_loop: a registered handler wins."""
        _spec(
            db_session,
            connector,
            [
                {
                    "action": "both",
                    "method": "GET",
                    "consolidator_config": {"function_code": "def run(p,c,l): pass"},
                }
            ],
        )
        monkeypatch.setattr(
            "app.agents.internal_tools.get_handler",
            lambda c, a: lambda p, d, t: {"success": True, "data": "from_handler"},
        )
        res = execute_connector_tool(connector, "both", {}, db_session, db_session)
        assert res.payload == "from_handler"
        assert res.rendered_request["method"] == "INTERNAL"

    def test_consolidator_still_used_when_no_handler(
        self, db_session, monkeypatch, connector
    ):
        """Regression guard: the pre-existing consolidator path must survive."""
        _spec(
            db_session,
            connector,
            [
                {
                    "action": "consolidate",
                    "method": "GET",
                    "consolidator_config": {"function_code": "..."},
                }
            ],
        )
        monkeypatch.setattr("app.agents.internal_tools.get_handler", lambda c, a: None)
        monkeypatch.setattr(
            "app.agents.internal_tools.execute_consolidator",
            lambda cfg, p, d, t: {"success": True, "data": [1, 2, 3], "_logs": ["ok"]},
        )
        res = execute_connector_tool(
            connector, "consolidate", {}, db_session, db_session
        )
        assert res.success is True
        assert res.payload == [1, 2, 3]
        assert res.rendered_request["method"] == "CONSOLIDATOR"
        assert res.row_count == 3
        assert res.logs == ["ok"]

    def test_handler_exception_becomes_a_failed_result(
        self, db_session, monkeypatch, connector
    ):
        _spec(db_session, connector, [{"action": "boom", "method": "GET"}])

        def exploding(params, db, thread_id):
            raise RuntimeError("handler blew up")

        monkeypatch.setattr(
            "app.agents.internal_tools.get_handler", lambda c, a: exploding
        )
        res = execute_connector_tool(connector, "boom", {}, db_session, db_session)
        assert res.success is False
        assert "handler blew up" in res.error

    def test_venue_name_passed_to_handler(self, db_session, monkeypatch, connector):
        v = _venue(db_session, "La Zeppa")
        _spec(db_session, connector, [{"action": "t", "method": "GET"}])
        seen = {}
        monkeypatch.setattr(
            "app.agents.internal_tools.get_handler",
            lambda c, a: (
                lambda p, d, t: seen.update(p) or {"success": True, "data": None}
            ),
        )
        execute_connector_tool(
            connector, "t", {}, db_session, db_session, venue_id=v.id
        )
        assert seen["venue"] == "La Zeppa"

    def test_unknown_action_reports_available(self, db_session, connector):
        _spec(db_session, connector, [{"action": "known", "method": "GET"}])
        res = execute_connector_tool(connector, "nope", {}, db_session, db_session)
        assert res.success is False
        assert "known" in res.error


class TestStrictVenueCredentials:
    def test_exact_venue_match_wins(self, db_session, connector):
        a = _venue(db_session, "Venue A")
        b = _venue(db_session, "Venue B")
        _config(db_session, connector, a.id, "key-A")
        _config(db_session, connector, b.id, "key-B")
        row = _resolve_credentials(connector, b.id, db_session, strict_venue=True)
        assert row.config["api_key"] == "key-B"

    def test_strict_refuses_another_venues_credentials(self, db_session, connector):
        """The leak: venue B has no config, so the loose fallback would hand
        back venue A's key and answer B's question with A's data."""
        a = _venue(db_session, "Venue A")
        b = _venue(db_session, "Venue B")
        _config(db_session, connector, a.id, "key-A")

        assert (
            _resolve_credentials(connector, b.id, db_session, strict_venue=True) is None
        )

        loose = _resolve_credentials(connector, b.id, db_session)
        assert loose is not None and loose.config["api_key"] == "key-A"

    def test_strict_still_allows_platform_config(self, db_session, connector):
        """Platform (venue_id NULL) connectors must keep working under strict."""
        b = _venue(db_session, "Venue B")
        _config(db_session, connector, None, "platform-key")
        row = _resolve_credentials(connector, b.id, db_session, strict_venue=True)
        assert row.config["api_key"] == "platform-key"

    def test_strict_prefers_venue_over_platform(self, db_session, connector):
        b = _venue(db_session, "Venue B")
        _config(db_session, connector, None, "platform-key")
        _config(db_session, connector, b.id, "key-B")
        row = _resolve_credentials(connector, b.id, db_session, strict_venue=True)
        assert row.config["api_key"] == "key-B"

    def test_loose_is_the_default(self, db_session, connector):
        """Existing callers (dashboard refresh) keep their behaviour."""
        a = _venue(db_session, "Venue A")
        b = _venue(db_session, "Venue B")
        _config(db_session, connector, a.id, "key-A")
        assert _resolve_credentials(connector, b.id, db_session) is not None


class TestApplyTransform:
    def test_no_transform_returns_payload_unchanged(self):
        assert _apply_transform({}, {"a": 1}) == {"a": 1}

    def test_disabled_transform_is_a_no_op(self):
        assert _apply_transform(
            {"response_transform": {"enabled": False}}, {"a": 1}
        ) == {"a": 1}

    @pytest.mark.parametrize("empty", [None, {}, []])
    def test_empty_payload_short_circuits(self, empty):
        assert (
            _apply_transform({"response_transform": {"enabled": True}}, empty) == empty
        )
