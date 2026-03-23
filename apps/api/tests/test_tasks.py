"""Tests for task endpoints: list, detail, delete."""

import uuid

import pytest

from app.db.models import Task, Message


class TestListTasks:
    """GET /api/tasks"""

    def test_list_tasks_returns_user_tasks(self, client, db_session, admin_user, admin_headers):
        # Create tasks for admin user
        t1 = Task(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            domain="procurement",
            status="awaiting_user_input",
            intent="place_order",
            raw_prompt="Order milk",
        )
        t2 = Task(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            domain="hr",
            status="awaiting_approval",
            intent="new_employee",
            raw_prompt="Hire someone",
        )
        db_session.add_all([t1, t2])
        db_session.flush()

        resp = client.get("/api/tasks", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks" in data
        assert len(data["tasks"]) == 2

    def test_list_tasks_does_not_return_other_users_tasks(
        self, client, db_session, admin_user, manager_user, manager_headers,
    ):
        # Create a task for admin
        task = Task(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            domain="procurement",
            status="awaiting_user_input",
            intent="place_order",
            raw_prompt="Order milk",
        )
        db_session.add(task)
        db_session.flush()

        # Manager should not see admin's tasks
        resp = client.get("/api/tasks", headers=manager_headers)
        assert resp.status_code == 200
        assert len(resp.json()["tasks"]) == 0

    def test_list_tasks_without_auth_returns_401(self, client):
        resp = client.get("/api/tasks")
        assert resp.status_code in (401, 403)


class TestGetTaskDetail:
    """GET /api/tasks/{task_id}"""

    def test_get_task_detail_returns_task(self, client, db_session, admin_user, admin_headers):
        task = Task(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            domain="reports",
            status="awaiting_user_input",
            intent="generate_report.tool_use",
            raw_prompt="Show me sales data",
            extracted_fields={},
            missing_fields=[],
        )
        db_session.add(task)
        db_session.flush()

        resp = client.get(f"/api/tasks/{task.id}", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == task.id

    def test_get_task_not_found_returns_404(self, client, admin_headers):
        resp = client.get(f"/api/tasks/{uuid.uuid4()}", headers=admin_headers)
        assert resp.status_code == 404

    def test_get_task_without_auth_returns_401(self, client):
        resp = client.get(f"/api/tasks/{uuid.uuid4()}")
        assert resp.status_code in (401, 403)


class TestDeleteTask:
    """DELETE /api/tasks/{task_id}"""

    def test_delete_own_task(self, client, db_session, admin_user, admin_headers):
        task = Task(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            domain="procurement",
            status="awaiting_user_input",
            intent="place_order",
            raw_prompt="Order milk",
        )
        db_session.add(task)
        db_session.flush()

        resp = client.delete(f"/api/tasks/{task.id}", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_delete_other_users_task_returns_404(
        self, client, db_session, admin_user, manager_user, manager_headers,
    ):
        task = Task(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            domain="procurement",
            status="awaiting_user_input",
            intent="place_order",
            raw_prompt="Order milk",
        )
        db_session.add(task)
        db_session.flush()

        resp = client.delete(f"/api/tasks/{task.id}", headers=manager_headers)
        assert resp.status_code == 404

    def test_delete_nonexistent_task_returns_404(self, client, admin_headers):
        resp = client.delete(f"/api/tasks/{uuid.uuid4()}", headers=admin_headers)
        assert resp.status_code == 404

    def test_delete_without_auth_returns_401(self, client):
        resp = client.delete(f"/api/tasks/{uuid.uuid4()}")
        assert resp.status_code in (401, 403)
