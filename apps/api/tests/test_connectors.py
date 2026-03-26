"""Tests for connector endpoints."""

import uuid
from unittest.mock import patch, MagicMock


from app.db.models import ConnectorConfig


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

    def test_list_connectors_without_auth_returns_401(self, client):
        resp = client.get("/api/connectors")
        assert resp.status_code in (401, 403)


class TestUpsertConnector:
    """PUT /api/connectors/{name}"""

    def test_save_anthropic_config_as_admin(self, client, db_session, admin_headers):
        resp = client.put(
            "/api/connectors/anthropic",
            json={
                "config": {"api_key": "sk-test-key"},
                "enabled": True,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "anthropic"
        assert data["enabled"] is True
        # api_key should be redacted in response
        assert data["config"]["api_key"] != "sk-test-key"

    def test_save_connector_as_manager_returns_403(self, client, manager_headers):
        resp = client.put(
            "/api/connectors/anthropic",
            json={
                "config": {"api_key": "sk-test-key"},
                "enabled": True,
            },
            headers=manager_headers,
        )
        assert resp.status_code == 403

    def test_save_unknown_connector_returns_404(self, client, admin_headers):
        resp = client.put(
            "/api/connectors/nonexistent",
            json={
                "config": {},
                "enabled": True,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 404

    def test_save_connector_without_auth_returns_401(self, client):
        resp = client.put(
            "/api/connectors/anthropic",
            json={
                "config": {"api_key": "sk-test"},
            },
        )
        assert resp.status_code in (401, 403)

    def test_update_existing_config_merges(self, client, db_session, admin_headers):
        # First save
        client.put(
            "/api/connectors/anthropic",
            json={
                "config": {"api_key": "sk-real-key"},
                "enabled": True,
            },
            headers=admin_headers,
        )

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
            db_session.query(ConnectorConfig)
            .filter(ConnectorConfig.connector_name == "anthropic")
            .first()
        )
        assert row.config["api_key"] == "sk-real-key"
        assert row.config["interpreter_model"] == "claude-sonnet-4-20250514"


class TestToggleConnector:
    """PATCH /api/connectors/{name}/toggle"""

    def test_toggle_connector(self, client, db_session, admin_headers):
        # First create a config
        db_session.add(
            ConnectorConfig(
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

    def test_toggle_nonexistent_connector_returns_404(self, client, admin_headers):
        resp = client.patch("/api/connectors/nonexistent/toggle", headers=admin_headers)
        assert resp.status_code == 404

    def test_toggle_connector_as_manager_returns_403(
        self, client, db_session, manager_headers
    ):
        db_session.add(
            ConnectorConfig(
                id=str(uuid.uuid4()),
                connector_name="anthropic",
                config={},
                enabled="true",
            )
        )
        db_session.flush()

        resp = client.patch("/api/connectors/anthropic/toggle", headers=manager_headers)
        assert resp.status_code == 403


class TestDeleteConnector:
    """DELETE /api/connectors/{name}"""

    def test_delete_connector_config(self, client, db_session, admin_headers):
        db_session.add(
            ConnectorConfig(
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

    def test_delete_nonexistent_returns_404(self, client, admin_headers):
        resp = client.delete("/api/connectors/nonexistent", headers=admin_headers)
        assert resp.status_code == 404

    def test_delete_as_manager_returns_403(self, client, db_session, manager_headers):
        db_session.add(
            ConnectorConfig(
                id=str(uuid.uuid4()),
                connector_name="anthropic",
                config={},
                enabled="true",
            )
        )
        db_session.flush()

        resp = client.delete("/api/connectors/anthropic", headers=manager_headers)
        assert resp.status_code == 403


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
        from app.db.models import ConnectorConfig

        db_session.query(ConnectorConfig).filter(
            ConnectorConfig.connector_name == "anthropic"
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

    def test_test_connector_as_manager_returns_403(self, client, manager_headers):
        resp = client.post(
            "/api/connectors/anthropic/test",
            json={
                "config": {"api_key": "sk-test"},
            },
            headers=manager_headers,
        )
        assert resp.status_code == 403

    def test_test_unknown_connector_returns_404(self, client, admin_headers):
        resp = client.post(
            "/api/connectors/nonexistent/test",
            json={
                "config": {},
            },
            headers=admin_headers,
        )
        assert resp.status_code == 404
