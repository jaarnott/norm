"""Tests for the role-based permission system."""

import uuid


from app.auth.permissions import STANDARD_ROLES
from app.auth.security import create_access_token
from app.db.models import (
    Role,
)

from tests.conftest import (
    _make_user,
    _make_organization,
    _make_membership,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seed_system_roles(db):
    """Insert the 4 system roles into the DB. Returns dict[name, Role]."""
    roles = {}
    for name, defn in STANDARD_ROLES.items():
        role = Role(
            id=str(uuid.uuid4()),
            name=name,
            display_name=defn["display_name"],
            description=defn["description"],
            is_system=True,
            permissions=defn["permissions"],
            organization_id=None,
        )
        db.add(role)
        roles[name] = role
    db.flush()
    return roles


def _auth(user):
    token = create_access_token({"sub": user.id})
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Tests: require_permission
# ---------------------------------------------------------------------------


class TestRequirePermission:
    """Test the require_permission dependency via the roles endpoints."""

    def test_owner_has_all_org_perms(self, client, db_session):
        """Owner role should have all org permissions."""
        roles = _seed_system_roles(db_session)
        user = _make_user(db_session, role="user")
        org = _make_organization(db_session)
        mem = _make_membership(db_session, user, org)
        mem.role_id = roles["owner"].id
        db_session.flush()

        # tasks:read should pass
        resp = client.get("/api/tasks", headers=_auth(user))
        assert resp.status_code == 200

    def test_team_member_limited_perms(self, client, db_session):
        """team_member should not have write perms like orders:approve."""
        roles = _seed_system_roles(db_session)
        user = _make_user(db_session, role="user")
        org = _make_organization(db_session)
        mem = _make_membership(db_session, user, org)
        mem.role_id = roles["team_member"].id
        db_session.flush()

        # orders:read should pass
        resp = client.get("/api/orders", headers=_auth(user))
        assert resp.status_code == 200

        # orders:approve should fail
        resp = client.post("/api/orders/fake-id/approve", headers=_auth(user))
        assert resp.status_code == 403

    def test_platform_admin_bypasses_org_checks(self, client, db_session):
        """Platform admin (User.role == 'admin') should bypass org permission checks."""
        user = _make_user(db_session, role="admin")
        # No org membership at all
        resp = client.get("/api/tasks", headers=_auth(user))
        assert resp.status_code == 200

    def test_missing_permission_returns_403(self, client, db_session):
        """User without required permission should get 403."""
        roles = _seed_system_roles(db_session)
        user = _make_user(db_session, role="user")
        org = _make_organization(db_session)
        mem = _make_membership(db_session, user, org)
        mem.role_id = roles["team_member"].id
        db_session.flush()

        # billing:manage is not in team_member
        resp = client.post(
            "/api/billing/fake-org/setup",
            json={"token_plan": "basic"},
            headers=_auth(user),
        )
        assert resp.status_code == 403

    def test_no_role_assigned_returns_403(self, client, db_session):
        """User with membership but no role_id should get 403."""
        user = _make_user(db_session, role="user")
        org = _make_organization(db_session)
        _make_membership(db_session, user, org)
        # No role_id set

        resp = client.get("/api/tasks", headers=_auth(user))
        assert resp.status_code == 403

    def test_admin_scopes_require_platform_admin(self, client, db_session):
        """admin:* scopes should require User.role == 'admin'."""
        roles = _seed_system_roles(db_session)
        user = _make_user(db_session, role="user")
        org = _make_organization(db_session)
        mem = _make_membership(db_session, user, org)
        mem.role_id = roles["owner"].id
        db_session.flush()

        resp = client.get("/api/admin/deployments", headers=_auth(user))
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Tests: Role CRUD
# ---------------------------------------------------------------------------


class TestRoleCRUD:
    def test_list_roles(self, client, db_session):
        roles = _seed_system_roles(db_session)
        user = _make_user(db_session, role="user")
        org = _make_organization(db_session)
        mem = _make_membership(db_session, user, org)
        mem.role_id = roles["owner"].id
        db_session.flush()

        resp = client.get(f"/api/organizations/{org.id}/roles", headers=_auth(user))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["roles"]) >= 4  # at least the 4 system roles

    def test_create_custom_role(self, client, db_session):
        roles = _seed_system_roles(db_session)
        user = _make_user(db_session, role="user")
        org = _make_organization(db_session)
        mem = _make_membership(db_session, user, org)
        mem.role_id = roles["owner"].id
        db_session.flush()

        resp = client.post(
            f"/api/organizations/{org.id}/roles",
            json={
                "name": "custom_viewer",
                "display_name": "Custom Viewer",
                "permissions": ["tasks:read", "orders:read"],
            },
            headers=_auth(user),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "custom_viewer"
        assert data["is_system"] is False

    def test_create_role_invalid_permission(self, client, db_session):
        roles = _seed_system_roles(db_session)
        user = _make_user(db_session, role="user")
        org = _make_organization(db_session)
        mem = _make_membership(db_session, user, org)
        mem.role_id = roles["owner"].id
        db_session.flush()

        resp = client.post(
            f"/api/organizations/{org.id}/roles",
            json={
                "name": "bad",
                "display_name": "Bad",
                "permissions": ["nonexistent:perm"],
            },
            headers=_auth(user),
        )
        assert resp.status_code == 400

    def test_update_custom_role(self, client, db_session):
        roles = _seed_system_roles(db_session)
        user = _make_user(db_session, role="user")
        org = _make_organization(db_session)
        mem = _make_membership(db_session, user, org)
        mem.role_id = roles["owner"].id
        db_session.flush()

        # Create custom role
        custom = Role(
            id=str(uuid.uuid4()),
            organization_id=org.id,
            name="test_role",
            display_name="Test",
            is_system=False,
            permissions=["tasks:read"],
        )
        db_session.add(custom)
        db_session.flush()

        resp = client.put(
            f"/api/organizations/{org.id}/roles/{custom.id}",
            json={
                "display_name": "Updated Test",
                "permissions": ["tasks:read", "tasks:write"],
            },
            headers=_auth(user),
        )
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "Updated Test"

    def test_cannot_modify_system_role(self, client, db_session):
        roles = _seed_system_roles(db_session)
        user = _make_user(db_session, role="user")
        org = _make_organization(db_session)
        mem = _make_membership(db_session, user, org)
        mem.role_id = roles["owner"].id
        db_session.flush()

        resp = client.put(
            f"/api/organizations/{org.id}/roles/{roles['owner'].id}",
            json={"display_name": "Hacked"},
            headers=_auth(user),
        )
        assert resp.status_code == 400

    def test_cannot_delete_system_role(self, client, db_session):
        roles = _seed_system_roles(db_session)
        user = _make_user(db_session, role="user")
        org = _make_organization(db_session)
        mem = _make_membership(db_session, user, org)
        mem.role_id = roles["owner"].id
        db_session.flush()

        resp = client.delete(
            f"/api/organizations/{org.id}/roles/{roles['owner'].id}",
            headers=_auth(user),
        )
        assert resp.status_code == 400

    def test_delete_custom_role(self, client, db_session):
        roles = _seed_system_roles(db_session)
        user = _make_user(db_session, role="user")
        org = _make_organization(db_session)
        mem = _make_membership(db_session, user, org)
        mem.role_id = roles["owner"].id
        db_session.flush()

        custom = Role(
            id=str(uuid.uuid4()),
            organization_id=org.id,
            name="deleteme",
            display_name="Delete Me",
            is_system=False,
            permissions=[],
        )
        db_session.add(custom)
        db_session.flush()

        resp = client.delete(
            f"/api/organizations/{org.id}/roles/{custom.id}",
            headers=_auth(user),
        )
        assert resp.status_code == 200

    def test_cannot_delete_role_in_use(self, client, db_session):
        roles = _seed_system_roles(db_session)
        user = _make_user(db_session, role="user")
        org = _make_organization(db_session)
        mem = _make_membership(db_session, user, org)
        mem.role_id = roles["owner"].id
        db_session.flush()

        custom = Role(
            id=str(uuid.uuid4()),
            organization_id=org.id,
            name="in_use",
            display_name="In Use",
            is_system=False,
            permissions=["tasks:read"],
        )
        db_session.add(custom)
        db_session.flush()

        # Assign to another member
        user2 = _make_user(db_session, role="user")
        mem2 = _make_membership(db_session, user2, org)
        mem2.role_id = custom.id
        db_session.flush()

        resp = client.delete(
            f"/api/organizations/{org.id}/roles/{custom.id}",
            headers=_auth(user),
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Tests: Member role assignment
# ---------------------------------------------------------------------------


class TestMemberRoleAssignment:
    def test_assign_role_to_member(self, client, db_session):
        roles = _seed_system_roles(db_session)
        admin = _make_user(db_session, role="user")
        org = _make_organization(db_session)
        mem = _make_membership(db_session, admin, org)
        mem.role_id = roles["owner"].id
        db_session.flush()

        target = _make_user(db_session, role="user")
        _make_membership(db_session, target, org)

        resp = client.put(
            f"/api/organizations/{org.id}/members/{target.id}/role",
            json={"role_id": roles["team_member"].id},
            headers=_auth(admin),
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests: /auth/me includes permissions
# ---------------------------------------------------------------------------


class TestAuthMePermissions:
    def test_me_includes_permissions_for_org_user(self, client, db_session):
        roles = _seed_system_roles(db_session)
        user = _make_user(db_session, role="user")
        org = _make_organization(db_session)
        mem = _make_membership(db_session, user, org)
        mem.role_id = roles["team_member"].id
        db_session.flush()

        resp = client.get("/api/auth/me", headers=_auth(user))
        assert resp.status_code == 200
        data = resp.json()
        assert "permissions" in data
        assert "tasks:read" in data["permissions"]
        assert data["org_role"]["name"] == "team_member"

    def test_me_admin_gets_all_perms(self, client, db_session):
        user = _make_user(db_session, role="admin")
        resp = client.get("/api/auth/me", headers=_auth(user))
        assert resp.status_code == 200
        data = resp.json()
        assert "permissions" in data
        assert len(data["permissions"]) > 0

    def test_me_no_membership_empty_perms(self, client, db_session):
        user = _make_user(db_session, role="user")
        resp = client.get("/api/auth/me", headers=_auth(user))
        assert resp.status_code == 200
        data = resp.json()
        assert data["permissions"] == []
        assert data["org_role"] is None


# ---------------------------------------------------------------------------
# Tests: Registration default role
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_non_first_user_gets_user_role(self, client, db_session):
        """When users already exist, new registrations get role='user'."""
        # Ensure at least one user exists
        _make_user(db_session, role="admin")

        resp = client.post(
            "/api/auth/register",
            json={
                "email": "new@test.com",
                "password": "testpass123",
                "full_name": "New User",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["user"]["role"] == "user"
