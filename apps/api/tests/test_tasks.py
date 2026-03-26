"""Tests for thread endpoints: list, detail, delete."""

import uuid


from app.db.models import Thread, Role, Organization, OrganizationMembership
from app.auth.permissions import STANDARD_ROLES


def _ensure_role(db_session, user):
    """Give a non-admin user an org + owner role so permission checks pass."""
    if user.role == "admin":
        return
    # Create owner role if needed
    role = (
        db_session.query(Role)
        .filter(Role.name == "owner", Role.is_system.is_(True))
        .first()
    )
    if not role:
        defn = STANDARD_ROLES["owner"]
        role = Role(
            id=str(uuid.uuid4()),
            name="owner",
            display_name=defn["display_name"],
            is_system=True,
            permissions=defn["permissions"],
        )
        db_session.add(role)
        db_session.flush()
    # Create org + membership
    org = Organization(
        id=str(uuid.uuid4()), name="Test", slug=f"t-{uuid.uuid4().hex[:6]}"
    )
    db_session.add(org)
    db_session.flush()
    mem = OrganizationMembership(
        id=str(uuid.uuid4()),
        user_id=user.id,
        organization_id=org.id,
        role="owner",
        role_id=role.id,
    )
    db_session.add(mem)
    db_session.flush()


class TestListThreads:
    """GET /api/threads"""

    def test_list_threads_returns_user_threads(
        self, client, db_session, admin_user, admin_headers
    ):
        # Create threads for admin user
        t1 = Thread(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            domain="procurement",
            status="awaiting_user_input",
            intent="place_order",
            raw_prompt="Order milk",
        )
        t2 = Thread(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            domain="hr",
            status="awaiting_approval",
            intent="new_employee",
            raw_prompt="Hire someone",
        )
        db_session.add_all([t1, t2])
        db_session.flush()

        resp = client.get("/api/threads", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "threads" in data
        assert len(data["threads"]) == 2

    def test_list_threads_does_not_return_other_users_threads(
        self,
        client,
        db_session,
        admin_user,
        manager_user,
        manager_headers,
    ):
        _ensure_role(db_session, manager_user)
        # Create a thread for admin
        thread = Thread(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            domain="procurement",
            status="awaiting_user_input",
            intent="place_order",
            raw_prompt="Order milk",
        )
        db_session.add(thread)
        db_session.flush()

        # Manager should not see admin's threads
        resp = client.get("/api/threads", headers=manager_headers)
        assert resp.status_code == 200
        assert len(resp.json()["threads"]) == 0

    def test_list_threads_without_auth_returns_401(self, client):
        resp = client.get("/api/threads")
        assert resp.status_code in (401, 403)


class TestGetThreadDetail:
    """GET /api/threads/{thread_id}"""

    def test_get_thread_detail_returns_thread(
        self, client, db_session, admin_user, admin_headers
    ):
        thread = Thread(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            domain="reports",
            status="awaiting_user_input",
            intent="generate_report.tool_use",
            raw_prompt="Show me sales data",
            extracted_fields={},
            missing_fields=[],
        )
        db_session.add(thread)
        db_session.flush()

        resp = client.get(f"/api/threads/{thread.id}", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == thread.id

    def test_get_thread_not_found_returns_404(self, client, admin_headers):
        resp = client.get(f"/api/threads/{uuid.uuid4()}", headers=admin_headers)
        assert resp.status_code == 404

    def test_get_thread_without_auth_returns_401(self, client):
        resp = client.get(f"/api/threads/{uuid.uuid4()}")
        assert resp.status_code in (401, 403)


class TestDeleteThread:
    """DELETE /api/threads/{thread_id}"""

    def test_delete_own_thread(self, client, db_session, admin_user, admin_headers):
        thread = Thread(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            domain="procurement",
            status="awaiting_user_input",
            intent="place_order",
            raw_prompt="Order milk",
        )
        db_session.add(thread)
        db_session.flush()

        resp = client.delete(f"/api/threads/{thread.id}", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_delete_other_users_thread_returns_404(
        self,
        client,
        db_session,
        admin_user,
        manager_user,
        manager_headers,
    ):
        _ensure_role(db_session, manager_user)
        thread = Thread(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            domain="procurement",
            status="awaiting_user_input",
            intent="place_order",
            raw_prompt="Order milk",
        )
        db_session.add(thread)
        db_session.flush()

        resp = client.delete(f"/api/threads/{thread.id}", headers=manager_headers)
        assert resp.status_code == 404

    def test_delete_nonexistent_thread_returns_404(self, client, admin_headers):
        resp = client.delete(f"/api/threads/{uuid.uuid4()}", headers=admin_headers)
        assert resp.status_code == 404

    def test_delete_without_auth_returns_401(self, client):
        resp = client.delete(f"/api/threads/{uuid.uuid4()}")
        assert resp.status_code in (401, 403)
