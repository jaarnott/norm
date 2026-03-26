"""Tests for automated task endpoints."""

import sys
import uuid
from unittest.mock import patch, MagicMock


# Mock the task_scheduler module since apscheduler isn't installed in test env
_mock_scheduler = MagicMock()
sys.modules.setdefault("apscheduler", MagicMock())
sys.modules.setdefault("apscheduler.schedulers", MagicMock())
sys.modules.setdefault("apscheduler.schedulers.background", MagicMock())
sys.modules.setdefault("apscheduler.triggers", MagicMock())
sys.modules.setdefault("apscheduler.triggers.cron", MagicMock())
sys.modules.setdefault("apscheduler.triggers.interval", MagicMock())

from app.db.models import AutomatedTask, AutomatedTaskRun  # noqa: E402


class TestListAutomatedTasks:
    """GET /api/automated-tasks"""

    def test_list_automated_tasks(self, client, db_session, admin_user, admin_headers):
        task = AutomatedTask(
            id=str(uuid.uuid4()),
            title="Daily Stock Check",
            agent_slug="procurement",
            prompt="Check stock levels",
            schedule_type="daily",
            status="active",
            created_by=admin_user.id,
        )
        db_session.add(task)
        db_session.flush()

        resp = client.get("/api/automated-tasks", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks" in data
        assert len(data["tasks"]) >= 1
        titles = [t["title"] for t in data["tasks"]]
        assert "Daily Stock Check" in titles

    def test_list_filter_by_agent_slug(
        self, client, db_session, admin_user, admin_headers
    ):
        unique_title = f"Unique HR Task {uuid.uuid4().hex[:8]}"
        db_session.add(
            AutomatedTask(
                id=str(uuid.uuid4()),
                title=unique_title,
                agent_slug="hr",
                prompt="Do something",
                status="active",
                created_by=admin_user.id,
            )
        )
        db_session.flush()

        resp = client.get("/api/automated-tasks?agent_slug=hr", headers=admin_headers)
        assert resp.status_code == 200
        tasks = resp.json()["tasks"]
        assert all(t["agent_slug"] == "hr" for t in tasks)
        titles = [t["title"] for t in tasks]
        assert unique_title in titles

    def test_list_filter_by_status(self, client, db_session, admin_user, admin_headers):
        unique_title = f"Unique Paused {uuid.uuid4().hex[:8]}"
        db_session.add(
            AutomatedTask(
                id=str(uuid.uuid4()),
                title=unique_title,
                agent_slug="procurement",
                prompt="Do something",
                status="paused",
                created_by=admin_user.id,
            )
        )
        db_session.flush()

        resp = client.get("/api/automated-tasks?status=paused", headers=admin_headers)
        assert resp.status_code == 200
        tasks = resp.json()["tasks"]
        assert all(t["status"] == "paused" for t in tasks)
        titles = [t["title"] for t in tasks]
        assert unique_title in titles

    def test_list_without_auth_returns_401(self, client):
        resp = client.get("/api/automated-tasks")
        assert resp.status_code in (401, 403)


class TestGetAutomatedTask:
    """GET /api/automated-tasks/{task_id}"""

    def test_get_task_detail(self, client, db_session, admin_user, admin_headers):
        task = AutomatedTask(
            id=str(uuid.uuid4()),
            title="My Task",
            agent_slug="procurement",
            prompt="Do something",
            status="draft",
            created_by=admin_user.id,
        )
        db_session.add(task)
        db_session.flush()

        resp = client.get(f"/api/automated-tasks/{task.id}", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "My Task"
        assert "runs" in data

    def test_get_task_not_found_returns_404(self, client, admin_headers):
        resp = client.get(f"/api/automated-tasks/{uuid.uuid4()}", headers=admin_headers)
        assert resp.status_code == 404


class TestCreateAutomatedTask:
    """POST /api/automated-tasks"""

    def test_create_task(self, client, db_session, admin_user, admin_headers):
        resp = client.post(
            "/api/automated-tasks",
            json={
                "title": "Weekly Report",
                "description": "Generate weekly report",
                "agent_slug": "reports",
                "prompt": "Generate weekly sales report",
                "schedule_type": "weekly",
                "schedule_config": {"day_of_week": 1, "hour": 9},
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Weekly Report"
        assert data["agent_slug"] == "reports"
        assert data["status"] == "draft"
        assert data["created_by"] == admin_user.id

    def test_create_task_missing_required_fields_returns_422(
        self, client, admin_headers
    ):
        resp = client.post(
            "/api/automated-tasks",
            json={
                "title": "No prompt or agent",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_create_task_without_auth_returns_401(self, client):
        resp = client.post(
            "/api/automated-tasks",
            json={
                "title": "No Auth",
                "agent_slug": "procurement",
                "prompt": "Do something",
            },
        )
        assert resp.status_code in (401, 403)


class TestUpdateAutomatedTask:
    """PUT /api/automated-tasks/{task_id}"""

    @patch("app.services.task_scheduler.schedule_task")
    @patch("app.services.task_scheduler.unschedule_task")
    def test_update_task(
        self,
        mock_unsched,
        mock_sched,
        client,
        db_session,
        admin_user,
        admin_headers,
    ):
        task = AutomatedTask(
            id=str(uuid.uuid4()),
            title="Old Title",
            agent_slug="procurement",
            prompt="Old prompt",
            status="draft",
            created_by=admin_user.id,
        )
        db_session.add(task)
        db_session.flush()

        resp = client.put(
            f"/api/automated-tasks/{task.id}",
            json={
                "title": "New Title",
                "prompt": "New prompt",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "New Title"

    def test_update_nonexistent_returns_404(self, client, admin_headers):
        resp = client.put(
            f"/api/automated-tasks/{uuid.uuid4()}",
            json={
                "title": "Nope",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 404


class TestPauseResumeAutomatedTask:
    """POST /api/automated-tasks/{task_id}/pause and /resume"""

    @patch("app.services.task_scheduler.unschedule_task")
    def test_pause_task(
        self, mock_unsched, client, db_session, admin_user, admin_headers
    ):
        task = AutomatedTask(
            id=str(uuid.uuid4()),
            title="Active Task",
            agent_slug="procurement",
            prompt="Do something",
            status="active",
            created_by=admin_user.id,
        )
        db_session.add(task)
        db_session.flush()

        resp = client.post(
            f"/api/automated-tasks/{task.id}/pause", headers=admin_headers
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "paused"

    @patch("app.services.task_scheduler.schedule_task")
    def test_resume_task(
        self, mock_sched, client, db_session, admin_user, admin_headers
    ):
        task = AutomatedTask(
            id=str(uuid.uuid4()),
            title="Paused Task",
            agent_slug="procurement",
            prompt="Do something",
            status="paused",
            created_by=admin_user.id,
        )
        db_session.add(task)
        db_session.flush()

        resp = client.post(
            f"/api/automated-tasks/{task.id}/resume", headers=admin_headers
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

    def test_pause_nonexistent_returns_404(self, client, admin_headers):
        resp = client.post(
            f"/api/automated-tasks/{uuid.uuid4()}/pause", headers=admin_headers
        )
        assert resp.status_code == 404

    def test_resume_nonexistent_returns_404(self, client, admin_headers):
        resp = client.post(
            f"/api/automated-tasks/{uuid.uuid4()}/resume", headers=admin_headers
        )
        assert resp.status_code == 404


class TestDeleteAutomatedTask:
    """DELETE /api/automated-tasks/{task_id}"""

    @patch("app.services.task_scheduler.unschedule_task")
    def test_delete_task(
        self, mock_unsched, client, db_session, admin_user, admin_headers
    ):
        task = AutomatedTask(
            id=str(uuid.uuid4()),
            title="To Delete",
            agent_slug="procurement",
            prompt="Do something",
            status="draft",
            created_by=admin_user.id,
        )
        db_session.add(task)
        db_session.flush()

        resp = client.delete(f"/api/automated-tasks/{task.id}", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_delete_nonexistent_returns_404(self, client, admin_headers):
        resp = client.delete(
            f"/api/automated-tasks/{uuid.uuid4()}", headers=admin_headers
        )
        assert resp.status_code == 404


class TestListRuns:
    """GET /api/automated-tasks/{task_id}/runs"""

    def test_list_runs(self, client, db_session, admin_user, admin_headers):
        task = AutomatedTask(
            id=str(uuid.uuid4()),
            title="Task",
            agent_slug="procurement",
            prompt="Do something",
            status="active",
            created_by=admin_user.id,
        )
        db_session.add(task)
        db_session.flush()

        run = AutomatedTaskRun(
            id=str(uuid.uuid4()),
            automated_task_id=task.id,
            status="success",
            mode="live",
            result_summary="Completed",
        )
        db_session.add(run)
        db_session.flush()

        resp = client.get(f"/api/automated-tasks/{task.id}/runs", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "runs" in data
        assert len(data["runs"]) == 1
        assert data["runs"][0]["status"] == "success"
