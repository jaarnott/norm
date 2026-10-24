"""Tests for connector endpoints."""

import uuid
from unittest.mock import patch, MagicMock

from app.db.models import Connection


class TestListConnectors:
    """GET /api/connectors"""

    def test_list_connectors(self, client, admin_headers):
        resp = client.get("/api/connectors", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "connectors" in data
        # Should at least have the platform connector (anthropic)
        names = [c["name"] for c in data["connectors"]]
        assert "anthropic" in names


class TestConnectorHealth:
    """The /connectors list exposes needs_reconnect (drives the Settings pill)."""

    def _broken_row(self, db_session, venue_id=None):
        from app.db.config_models import ConnectionSpec

        db_session.add(
            ConnectionSpec(
                id=str(uuid.uuid4()),
                connector_name="loadedhub",
                display_name="LoadedHub",
                category="pos",
                auth_type="oauth2",
                enabled=True,
            )
        )
        db_session.add(
            Connection(
                connector_name="loadedhub",
                venue_id=venue_id,
                config={},
                enabled="true",
                access_token="stale",
                refresh_token="dead",
                needs_reconnect=True,
                last_auth_error='Token refresh failed (400): "invalid"',
            )
        )
        db_session.flush()

    def test_list_exposes_needs_reconnect(self, client, db_session, admin_headers):
        from tests.conftest import _make_venue

        venue = _make_venue(db_session)
        self._broken_row(db_session, venue_id=venue.id)

        resp = client.get(f"/api/connectors?venue_id={venue.id}", headers=admin_headers)
        entry = next(c for c in resp.json()["connectors"] if c["name"] == "loadedhub")
        assert entry["needs_reconnect"] is True


class TestUpsertConnector:
    """PUT /api/connectors/{name}"""

    def test_update_existing_config_merges(self, client, db_session, admin_headers):
        """Also (was test_save_anthropic_config_as_admin): the first save
        succeeds and its response redacts the api_key."""
        # First save
        resp = client.put(
            "/api/connectors/anthropic",
            json={
                "config": {"api_key": "sk-real-key"},
                "enabled": True,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "anthropic"
        assert data["enabled"] is True
        # api_key should be redacted in response
        assert data["config"]["api_key"] != "sk-real-key"

        # Update with redacted key should keep original
        resp = client.put(
            "/api/connectors/anthropic",
            json={
                "config": {
                    "api_key": "••••••••",
                    "interpreter_model": "claude-sonnet-4-20250514",
                },
                "enabled": True,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200

        # Verify the key was preserved (still redacted in response)
        row = (
            db_session.query(Connection)
            .filter(Connection.connector_name == "anthropic")
            .first()
        )
        assert row.config["api_key"] == "sk-real-key"
        assert row.config["interpreter_model"] == "claude-sonnet-4-20250514"


class TestToggleConnector:
    """PATCH /api/connectors/{name}/toggle"""

    def test_toggle_connector(self, client, db_session, admin_headers):
        # First create a config
        db_session.add(
            Connection(
                id=str(uuid.uuid4()),
                connector_name="anthropic",
                config={"api_key": "sk-test"},
                enabled="true",
            )
        )
        db_session.flush()

        resp = client.patch("/api/connectors/anthropic/toggle", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False


class TestDeleteConnector:
    """DELETE /api/connectors/{name}"""

    def test_delete_connector_config(self, client, db_session, admin_headers):
        db_session.add(
            Connection(
                id=str(uuid.uuid4()),
                connector_name="anthropic",
                config={"api_key": "sk-test"},
                enabled="true",
            )
        )
        db_session.flush()

        resp = client.delete("/api/connectors/anthropic", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True


class TestTestConnector:
    """POST /api/connectors/{name}/test"""

    def test_test_anthropic_success(self, client, db_session, admin_headers):
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_client.models.list.return_value = MagicMock()

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            resp = client.post(
                "/api/connectors/anthropic/test",
                json={
                    "config": {"api_key": "sk-valid-key"},
                },
                headers=admin_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_test_anthropic_no_key_returns_400(self, client, db_session, admin_headers):
        # Ensure no saved config exists so merged credentials have no api_key
        from app.db.models import Connection

        db_session.query(Connection).filter(
            Connection.connector_name == "anthropic"
        ).delete()
        db_session.flush()

        resp = client.post(
            "/api/connectors/anthropic/test",
            json={
                "config": {},
            },
            headers=admin_headers,
        )
        assert resp.status_code == 400


class TestEveryWriteVerb:
    """The admin-only write routes, one check per kind across every verb:
    PUT /connectors/{name}, PATCH …/toggle, DELETE, POST …/test.

    Folds the per-verb *_as_manager_returns_403 and *_returns_404 tests."""

    def _verbs(self, name):
        return [
            ("put", f"/api/connectors/{name}", {"config": {}, "enabled": True}),
            ("patch", f"/api/connectors/{name}/toggle", None),
            ("delete", f"/api/connectors/{name}", None),
            ("post", f"/api/connectors/{name}/test", {"config": {}}),
        ]

    def test_a_manager_is_refused_on_every_verb(
        self, client, db_session, manager_headers
    ):
        # A real row, so a refusal can't be a not-found in disguise.
        db_session.add(
            Connection(
                id=str(uuid.uuid4()),
                connector_name="anthropic",
                config={},
                enabled="true",
            )
        )
        db_session.flush()

        for method, url, body in self._verbs("anthropic"):
            kwargs = {"json": body} if body is not None else {}
            resp = client.request(method, url, headers=manager_headers, **kwargs)
            assert resp.status_code == 403, (method, url)

    def test_an_unknown_connector_is_404_on_every_verb(self, client, admin_headers):
        for method, url, body in self._verbs("nonexistent"):
            kwargs = {"json": body} if body is not None else {}
            resp = client.request(method, url, headers=admin_headers, **kwargs)
            assert resp.status_code == 404, (method, url)


class TestConnectionsStayInTheirOrganisation:
    """Security ledger S25/S33 (24 Oct 2026): any signed-in user could read any
    venue's connection settings, and /oauth/status with no venue returned the
    first connection in the whole database, OAuth metadata included."""

    def _two_orgs(self, db_session):
        from app.db.models import OrganizationMembership
        from tests.org_helpers import (
            grant_venue,
            make_org,
            make_user,
            make_venue,
            seed_system_roles,
        )

        roles = seed_system_roles(db_session)
        mine, theirs = make_org(db_session, "Mine"), make_org(db_session, "Theirs")
        my_venue = make_venue(db_session, "Here", mine)
        their_venue = make_venue(db_session, "There", theirs)
        user = make_user(db_session)
        db_session.add(
            OrganizationMembership(
                id=str(uuid.uuid4()),
                user_id=user.id,
                organization_id=mine.id,
                role="owner",
                role_id=roles["owner"].id,
            )
        )
        grant_venue(db_session, user, my_venue)
        db_session.add(
            Connection(
                id=str(uuid.uuid4()),
                connector_name="loadedhub",
                venue_id=their_venue.id,
                access_token="their-token",
                oauth_metadata={"company": "theirs"},
                config={"x_loaded_company_id": "theirs"},
                enabled="true",
            )
        )
        db_session.flush()
        return user, my_venue, their_venue

    def test_another_organisations_venue_is_refused(self, client, db_session):
        from tests.org_helpers import auth_headers

        user, _, their_venue = self._two_orgs(db_session)
        h = auth_headers(user)
        assert (
            client.get(
                f"/api/connectors?venue_id={their_venue.id}", headers=h
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"/api/oauth/status/loadedhub?venue_id={their_venue.id}", headers=h
            ).status_code
            == 403
        )

    def test_status_without_a_venue_stays_in_my_organisation(self, client, db_session):
        from tests.org_helpers import auth_headers

        user, my_venue, _ = self._two_orgs(db_session)
        h = auth_headers(user)
        out = client.get("/api/oauth/status/loadedhub", headers=h).json()
        assert out == {"connected": False}  # theirs is not mine
        assert (
            client.get(f"/api/connectors?venue_id={my_venue.id}", headers=h).status_code
            == 200
        )

    def test_component_configs_are_for_platform_admins(self, client, db_session):
        from tests.org_helpers import auth_headers, make_user

        user = make_user(db_session)
        assert (
            client.get(
                "/api/component-api-configs", headers=auth_headers(user)
            ).status_code
            == 403
        )
