"""Tests for task endpoints: list, detail, delete."""

import uuid

import pytest

from app.db.models import Task, Message, Role, Organization, OrganizationMembership
from app.auth.permissions import STANDARD_ROLES


def _ensure_role(db_session, user):
    """Give a non-admin user an org + owner role so permission checks pass."""
    if user.role == "admin":
        return
    # Create owner role if needed
    role = db_session.query(Role).filter(Role.name == "owner", Role.is_system.is_(True)).first()
    if not role:
        defn = STANDARD_ROLES["owner"]
        role = Role(
            id=str(uuid.uuid4()), name="owner", display_name=defn["display_name"],
            is_system=True, permissions=defn["permissions"],
        )
        db_session.add(role)
        db_session.flush()
    # Create org + membership
    org = Organization(id=str(uuid.uuid4()), name="Test", slug=f"t-{uuid.uuid4().hex[:6]}")
    db_session.add(org)
    db_session.flush()
    mem = OrganizationMembership(
        id=str(uuid.uuid4()), user_id=user.id,
        organization_id=org.id, role="owner", role_id=role.id,
    )
    db_session.add(mem)
    db_session.flush()


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
        _ensure_role(db_session, manager_user)
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
        _ensure_role(db_session, manager_user)
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
