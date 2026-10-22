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


class TestAdminThreadViewer:
    """GET /api/admin/threads and /api/admin/threads/users"""

    def _world(self, db_session):
        from datetime import datetime, timezone

        from app.db.models import Message, Thread
        from tests.conftest import _make_user

        def at(day, hour=9):
            return datetime(2026, 10, day, hour, tzinfo=timezone.utc)

        ann = _make_user(db_session, full_name="Ann")
        bob = _make_user(db_session, full_name="Bob")
        robot = _make_user(db_session, full_name="Only automated runs")
        # Ann's thread started first but was talked in last.
        old = Thread(user_id=ann.id, title="old start", created_at=at(1))
        new = Thread(user_id=bob.id, title="new start", created_at=at(5))
        empty = Thread(user_id=bob.id, title="no messages", created_at=at(3))
        run = Thread(user_id=robot.id, intent="x.automated_task", created_at=at(9))
        db_session.add_all([old, new, empty, run])
        db_session.flush()
        db_session.add_all(
            [
                Message(thread_id=old.id, role="user", content="hi", created_at=at(2)),
                Message(
                    thread_id=old.id, role="user", content="again", created_at=at(8)
                ),
                Message(thread_id=new.id, role="user", content="hi", created_at=at(6)),
            ]
        )
        db_session.flush()
        return ann, bob

    def test_newest_message_first_and_dated_by_it(
        self, client, admin_headers, db_session
    ):
        self._world(db_session)
        rows = client.get("/api/admin/threads", headers=admin_headers).json()["threads"]
        assert [r["title"] for r in rows] == ["old start", "new start", "no messages"]
        assert rows[0]["last_message_at"].startswith("2026-10-08")
        # A thread with no messages yet is dated by its start.
        assert rows[2]["last_message_at"].startswith("2026-10-03")

    def test_person_filter_uses_the_id_the_list_offers(
        self, client, admin_headers, db_session
    ):
        """The filter sent the person's email as their id and matched nothing
        (Oct 2026): the list carried no user_id."""
        ann, bob = self._world(db_session)
        people = client.get("/api/admin/threads/users", headers=admin_headers).json()[
            "users"
        ]
        assert [p["full_name"] for p in people] == [
            "Ann",
            "Bob",
        ]  # not the automated-only one
        rows = client.get(
            "/api/admin/threads",
            params={"user_id": people[1]["id"]},
            headers=admin_headers,
        ).json()
        assert rows["total"] == 2
        assert {r["user_id"] for r in rows["threads"]} == {bob.id}

    def test_dates_filter_on_the_last_message(self, client, admin_headers, db_session):
        self._world(db_session)
        rows = client.get(
            "/api/admin/threads",
            params={"date_from": "2026-10-07", "date_to": "2026-10-08"},
            headers=admin_headers,
        ).json()["threads"]
        assert [r["title"] for r in rows] == ["old start"]

    def test_people_list_is_admin_only(self, client, manager_headers):
        assert (
            client.get("/api/admin/threads/users", headers=manager_headers).status_code
            == 403
        )
