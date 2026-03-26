"""Tests for admin/deployment endpoints."""


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

    def test_unauthenticated_returns_401(self, client):
        resp = client.get("/api/admin/deployments")
        assert resp.status_code == 401

    def test_returns_deployments_after_webhook(self, client, admin_headers):
        """After posting a deploy webhook, the deployment appears in the list."""
        client.post(
            "/api/admin/deploy-webhook",
            json={
                "environment": "testing",
                "image_tag": "v1.0.0",
                "git_sha": "abc123",
                "status": "success",
                "commit_message": "initial deploy",
            },
        )
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

    def test_creates_deployment_record(self, client):
        """The webhook endpoint does not require auth (secured by webhook secret later)."""
        resp = client.post(
            "/api/admin/deploy-webhook",
            json={
                "environment": "production",
                "image_tag": "v2.0.0",
                "git_sha": "def456",
                "status": "pending",
                "commit_message": "big release",
                "logs_url": "https://logs.example.com/123",
                "triggered_by": "github-actions",
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_updates_existing_deployment(self, client):
        """Posting the same git_sha + environment updates the existing record."""
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

    def test_environments_have_null_latest_deploy_initially(
        self, client, admin_headers
    ):
        resp = client.get("/api/admin/environments", headers=admin_headers)
        envs = resp.json()["environments"]
        for env in envs:
            assert env["latest_deploy"] is None

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
