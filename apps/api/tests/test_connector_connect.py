"""Part 2: connect/reconnect a connector from the conversation.

Who may start a flow, that it's venue-scoped, what the card reads, and that a
rejected connector token both flags health and can be resurfaced in-chat.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.db.config_models import ConnectionSpec
from app.db.models import (
    Connection,
    OrganizationMembership,
    Role,
    UserVenueAccess,
)
from tests.conftest import _make_organization, _make_user, _make_venue


def _oauth_spec_row(db_session, connector="loadedhub"):
    db_session.add(
        ConnectionSpec(
            id=str(uuid.uuid4()),
            connector_name=connector,
            display_name="LoadedHub",
            category="pos",
            auth_type="oauth2",
            oauth_config={
                "authorize_url": "https://auth.example.com/authorize",
                "token_url": "https://auth.example.com/token",
                "client_id": "cid",
                "client_secret": "secret",
                "scopes": "read",
            },
            enabled=True,
        )
    )
    db_session.flush()


def _manager_with_connectors_perm(db_session, *, venue, with_access=True):
    """A non-admin who holds settings:connectors, optionally with venue access."""
    user = _make_user(db_session, role="manager")
    org = _make_organization(db_session)
    role = Role(
        id=str(uuid.uuid4()),
        name=f"mgr-{uuid.uuid4().hex[:6]}",
        display_name="Manager",
        description="",
        is_system=False,
        permissions=["settings:connectors"],
        organization_id=org.id,
    )
    db_session.add(role)
    db_session.flush()
    db_session.add(
        OrganizationMembership(
            id=str(uuid.uuid4()),
            user_id=user.id,
            organization_id=org.id,
            role="member",
            role_id=role.id,
        )
    )
    if with_access:
        db_session.add(
            UserVenueAccess(id=str(uuid.uuid4()), user_id=user.id, venue_id=venue.id)
        )
    db_session.flush()
    from app.auth.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}


class TestAuthorizeAuthorization:
    """/oauth/authorize is no longer admin-only — but stays venue-scoped."""

    def test_manager_with_permission_and_access_can_start(self, client, db_session):
        venue = _make_venue(db_session)
        _oauth_spec_row(db_session)
        headers = _manager_with_connectors_perm(db_session, venue=venue)

        resp = client.get(
            f"/api/oauth/authorize/loadedhub?venue_id={venue.id}", headers=headers
        )
        assert resp.status_code == 200
        assert "authorize_url" in resp.json()

    def test_manager_without_venue_access_is_refused(self, client, db_session):
        venue = _make_venue(db_session)
        _oauth_spec_row(db_session)
        headers = _manager_with_connectors_perm(
            db_session, venue=venue, with_access=False
        )

        resp = client.get(
            f"/api/oauth/authorize/loadedhub?venue_id={venue.id}", headers=headers
        )
        assert resp.status_code == 403

    def test_user_without_permission_is_refused(
        self, client, db_session, manager_headers
    ):
        venue = _make_venue(db_session)
        _oauth_spec_row(db_session)
        # manager_headers has role="manager" but no membership/role → no perm
        resp = client.get(
            f"/api/oauth/authorize/loadedhub?venue_id={venue.id}",
            headers=manager_headers,
        )
        assert resp.status_code == 403


class TestConnectInfo:
    """The single payload the card renders from."""

    def test_reports_per_venue_status_scoped_to_the_user(self, client, db_session):
        connected_venue = _make_venue(db_session, name="Connected Venue")
        broken_venue = _make_venue(db_session, name="Broken Venue")
        _make_venue(db_session, name="Unrelated Venue")  # no access → excluded
        _oauth_spec_row(db_session)
        db_session.add(
            Connection(
                connector_name="loadedhub",
                venue_id=connected_venue.id,
                config={},
                enabled="true",
                access_token="live",
            )
        )
        db_session.add(
            Connection(
                connector_name="loadedhub",
                venue_id=broken_venue.id,
                config={},
                enabled="true",
                access_token="stale",
                needs_reconnect=True,
            )
        )
        db_session.flush()

        headers = _manager_with_connectors_perm(db_session, venue=connected_venue)
        # give the same user access to the broken venue too
        db_session.add(
            UserVenueAccess(
                id=str(uuid.uuid4()),
                user_id=_user_id_from(headers, db_session),
                venue_id=broken_venue.id,
            )
        )
        db_session.flush()

        resp = client.get("/api/connectors/loadedhub/connect-info", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["auth_type"] == "oauth2"
        statuses = {v["venue_name"]: v["status"] for v in data["venues"]}
        assert statuses == {
            "Connected Venue": "connected",
            "Broken Venue": "needs_reconnect",
        }  # Unrelated Venue excluded — no access


def _user_id_from(headers, db_session):
    from app.auth.security import decode_access_token

    token = headers["Authorization"].split()[1]
    return decode_access_token(token)["sub"]


class TestAuthFailurePropagates:
    """A rejected token flows through as a structured flag, not just a string."""

    def test_execute_tool_call_marks_auth_failed(self, db_session):
        from app.agents.tool_loop import _execute_tool_call
        from app.connectors.base import ConnectorResult
        from app.db.models import ToolCall

        db_session.add(
            ConnectionSpec(
                id=str(uuid.uuid4()),
                connector_name="loadedhub",
                display_name="LoadedHub",
                category="pos",
                auth_type="oauth2",
                oauth_config={"token_url": "x"},
                tools=[
                    {
                        "action": "get_sales",
                        "method": "GET",
                        "path_template": "/sales",
                    }
                ],
                enabled=True,
            )
        )
        from tests.conftest import _make_thread

        user = _make_user(db_session)
        thread = _make_thread(db_session, user, domain="reports")
        tc = ToolCall(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            iteration=1,
            tool_name="loadedhub__get_sales",
            connector_name="loadedhub",
            action="get_sales",
            method="GET",
            status="pending",
            input_params={},
        )
        db_session.add(tc)
        db_session.flush()

        rendered = MagicMock()
        rendered.to_audit_dict.return_value = {}
        auth_fail = ConnectorResult(
            success=False,
            reference=None,
            response_payload={},
            error_message="loadedhub authorization failed. Reconnect loadedhub in Settings → Connectors.",
            auth_failed=True,
        )
        with patch(
            "app.connectors.spec_executor.execute_spec",
            return_value=(auth_fail, rendered),
        ):
            result = _execute_tool_call(tc, db_session, config_db=db_session)

        assert result["auth_failed"] is True
        assert result["success"] is False


class TestConnectIntentDetection:
    """'connect BambooHR' must show the card — it routes to meta (no tool loop),
    so the supervisor detects it deterministically."""

    def _specs(self, db_session):
        for name, display, cat in [
            ("loadedhub", "LoadedHub", "pos"),
            ("bamboohr", "BambooHR", "hr"),
            ("norm", "Norm", "_internal"),
        ]:
            db_session.add(
                ConnectionSpec(
                    id=str(uuid.uuid4()),
                    connector_name=name,
                    display_name=display,
                    category=cat,
                    auth_type="oauth2",
                    enabled=True,
                )
            )
        db_session.flush()

    def test_detects_connector_by_name(self, db_session):
        from app.services.supervisor import _detect_connect_intent

        self._specs(db_session)
        assert (
            _detect_connect_intent("can you connect to bamboohr please", db_session)
            == "bamboohr"
        )
        assert _detect_connect_intent("reconnect loadedhub", db_session) == "loadedhub"

    def test_detects_via_alias(self, db_session):
        from app.services.supervisor import _detect_connect_intent

        self._specs(db_session)
        assert (
            _detect_connect_intent("please connect loaded", db_session) == "loadedhub"
        )
        assert _detect_connect_intent("connect bamboo hr", db_session) == "bamboohr"

    def test_no_verb_no_match(self, db_session):
        from app.services.supervisor import _detect_connect_intent

        self._specs(db_session)
        assert (
            _detect_connect_intent("how were sales at loadedhub last week", db_session)
            is None
        )

    def test_verb_but_no_connector(self, db_session):
        from app.services.supervisor import _detect_connect_intent

        self._specs(db_session)
        assert (
            _detect_connect_intent("connect the dots on our sales", db_session) is None
        )

    def test_never_matches_norm_internal(self, db_session):
        from app.services.supervisor import _detect_connect_intent

        self._specs(db_session)
        assert _detect_connect_intent("connect to norm", db_session) is None


class TestMcpConnectLink:
    def test_connect_link_targets_the_web_connect_route(self):
        from app.mcp import links

        url = links.connect_link("loadedhub", venue_id="v1")
        assert "?connect=loadedhub" in url
        assert "venue=v1" in url

    def test_connect_link_without_venue(self):
        from app.mcp import links

        assert links.connect_link("loadedhub").endswith("?connect=loadedhub")


class TestStateTtl:
    def test_a_stale_oauth_state_is_rejected(self, db_session):
        from app.db.models import OAuthState
        from app.services import oauth_service

        spec = MagicMock()
        spec.connector_name = "loadedhub"
        spec.oauth_config = {"token_url": "https://auth.example.com/token"}
        old = OAuthState(
            connector_name="loadedhub",
            state="stale-state",
            venue_id=None,
            user_id=None,
        )
        db_session.add(old)
        db_session.flush()
        old.created_at = datetime.now(timezone.utc) - timedelta(
            minutes=oauth_service.OAUTH_STATE_TTL_MIN + 5
        )
        db_session.flush()

        import pytest

        with pytest.raises(ValueError, match="expired"):
            oauth_service.exchange_code(
                spec, "code", "stale-state", "https://cb", db_session
            )
