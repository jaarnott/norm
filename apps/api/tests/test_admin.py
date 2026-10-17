"""Tests for admin/deployment endpoints."""

from app.db.models import Deployment


class TestListDeployments:
    """GET /api/admin/deployments"""

    def test_returns_empty_list_initially(self, client, admin_headers):
        resp = client.get("/api/admin/deployments", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["deployments"] == []

    def test_admin_only(self, client, manager_headers):
        """Non-admin users should receive 403."""
        resp = client.get("/api/admin/deployments", headers=manager_headers)
        assert resp.status_code == 403

    def test_returns_deployments_after_webhook(self, client, admin_headers):
        """After posting a deploy webhook, the deployment appears in the list.

        The webhook endpoint does not require auth (secured by webhook secret
        later) — it is posted here without headers."""
        resp = client.post(
            "/api/admin/deploy-webhook",
            json={
                "environment": "testing",
                "image_tag": "v1.0.0",
                "git_sha": "abc123",
                "status": "success",
                "commit_message": "initial deploy",
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        resp = client.get("/api/admin/deployments", headers=admin_headers)
        assert resp.status_code == 200
        deployments = resp.json()["deployments"]
        assert len(deployments) == 1
        assert deployments[0]["environment"] == "testing"
        assert deployments[0]["image_tag"] == "v1.0.0"
        assert deployments[0]["git_sha"] == "abc123"
        assert deployments[0]["status"] == "success"

    def test_filter_by_environment(self, client, admin_headers):
        """The environment query parameter filters results."""
        client.post(
            "/api/admin/deploy-webhook",
            json={
                "environment": "testing",
                "image_tag": "v1.0.0",
                "git_sha": "abc111",
                "status": "success",
            },
        )
        client.post(
            "/api/admin/deploy-webhook",
            json={
                "environment": "staging",
                "image_tag": "v1.0.1",
                "git_sha": "abc222",
                "status": "running",
            },
        )

        resp = client.get(
            "/api/admin/deployments",
            params={"environment": "staging"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        deployments = resp.json()["deployments"]
        assert len(deployments) == 1
        assert deployments[0]["environment"] == "staging"


class TestDeployWebhook:
    """POST /api/admin/deploy-webhook"""

    def test_updates_existing_deployment(self, client, db_session):
        """Posting the same git_sha + environment updates the existing record —
        one row, not two — and a finished status stamps completed_at."""
        client.post(
            "/api/admin/deploy-webhook",
            json={
                "environment": "testing",
                "image_tag": "v1.0.0",
                "git_sha": "sha999",
                "status": "pending",
            },
        )
        resp = client.post(
            "/api/admin/deploy-webhook",
            json={
                "environment": "testing",
                "image_tag": "v1.0.0",
                "git_sha": "sha999",
                "status": "success",
                "logs_url": "https://logs.example.com/done",
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        rows = (
            db_session.query(Deployment)
            .filter(Deployment.git_sha == "sha999", Deployment.environment == "testing")
            .all()
        )
        assert len(rows) == 1
        assert rows[0].status == "success"
        assert rows[0].logs_url == "https://logs.example.com/done"
        assert rows[0].completed_at is not None

    def test_missing_required_field_returns_422(self, client):
        resp = client.post(
            "/api/admin/deploy-webhook",
            json={
                "environment": "testing",
                # missing image_tag, git_sha, status
            },
        )
        assert resp.status_code == 422


class TestListEnvironments:
    """GET /api/admin/environments"""

    def test_returns_three_environments(self, client, admin_headers):
        resp = client.get("/api/admin/environments", headers=admin_headers)
        assert resp.status_code == 200
        envs = resp.json()["environments"]
        assert len(envs) == 3
        names = [e["name"] for e in envs]
        assert names == ["testing", "staging", "production"]

    def test_non_admin_gets_403(self, client, manager_headers):
        resp = client.get("/api/admin/environments", headers=manager_headers)
        assert resp.status_code == 403

    def test_shows_latest_deploy_after_webhook(self, client, admin_headers):
        """After a webhook, the corresponding environment shows latest_deploy."""
        client.post(
            "/api/admin/deploy-webhook",
            json={
                "environment": "staging",
                "image_tag": "v3.0.0",
                "git_sha": "xyz789",
                "status": "success",
                "commit_message": "staged release",
            },
        )
        resp = client.get("/api/admin/environments", headers=admin_headers)
        envs = resp.json()["environments"]
        staging = next(e for e in envs if e["name"] == "staging")
        assert staging["latest_deploy"] is not None
        assert staging["latest_deploy"]["image_tag"] == "v3.0.0"
        assert staging["latest_deploy"]["status"] == "success"
