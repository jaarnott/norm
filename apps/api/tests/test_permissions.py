"""Tests for the role-based permission system."""

import uuid


from app.auth.permissions import (
    PERMISSION_GROUPS,
    PERMISSION_SCOPES,
    PLATFORM_ADMIN_SCOPES,
)
from app.db.models import (
    Role,
)

from tests.conftest import (
    _make_user,
    _make_organization,
    _make_membership,
)
from tests.org_helpers import auth_headers, seed_system_roles

# ---------------------------------------------------------------------------
# Tests: require_permission
# ---------------------------------------------------------------------------


class TestRequirePermission:
    """Test the require_permission dependency via the roles endpoints."""

    def test_owner_has_all_org_perms(self, client, db_session):
        """Owner role should have all org permissions."""
        roles = seed_system_roles(db_session)
        user = _make_user(db_session, role="user")
        org = _make_organization(db_session)
        mem = _make_membership(db_session, user, org)
        mem.role_id = roles["owner"].id
        db_session.flush()

        # tasks:read should pass
        resp = client.get("/api/threads", headers=auth_headers(user))
        assert resp.status_code == 200

    def test_team_member_limited_perms(self, client, db_session):
        """team_member should not have write perms like orders:approve."""
        roles = seed_system_roles(db_session)
        user = _make_user(db_session, role="user")
        org = _make_organization(db_session)
        mem = _make_membership(db_session, user, org)
        mem.role_id = roles["team_member"].id
        db_session.flush()

        # orders:read should pass
        resp = client.get("/api/orders", headers=auth_headers(user))
        assert resp.status_code == 200

        # orders:approve should fail
        resp = client.post("/api/orders/fake-id/approve", headers=auth_headers(user))
        assert resp.status_code == 403

    def test_platform_admin_bypasses_org_checks(self, client, db_session):
        """Platform admin (User.role == 'admin') should bypass org permission checks."""
        user = _make_user(db_session, role="admin")
        # No org membership at all
        resp = client.get("/api/threads", headers=auth_headers(user))
        assert resp.status_code == 200

    def test_missing_permission_returns_403(self, client, db_session):
        """User without required permission should get 403."""
        roles = seed_system_roles(db_session)
        user = _make_user(db_session, role="user")
        org = _make_organization(db_session)
        mem = _make_membership(db_session, user, org)
        mem.role_id = roles["team_member"].id
        db_session.flush()

        # billing:manage is not in team_member
        resp = client.post(
            "/api/billing/fake-org/setup",
            json={"token_plan": "basic"},
            headers=auth_headers(user),
        )
        assert resp.status_code == 403

    def test_no_role_assigned_returns_403(self, client, db_session):
        """User with membership but no role_id should get 403."""
        user = _make_user(db_session, role="user")
        org = _make_organization(db_session)
        _make_membership(db_session, user, org)
        # No role_id set

        resp = client.get("/api/threads", headers=auth_headers(user))
        assert resp.status_code == 403

    def test_admin_scopes_require_platform_admin(self, client, db_session):
        """admin:* scopes should require User.role == 'admin'."""
        roles = seed_system_roles(db_session)
        user = _make_user(db_session, role="user")
        org = _make_organization(db_session)
        mem = _make_membership(db_session, user, org)
        mem.role_id = roles["owner"].id
        db_session.flush()

        resp = client.get("/api/admin/deployments", headers=auth_headers(user))
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Tests: Role CRUD
# ---------------------------------------------------------------------------


class TestRoleCRUD:
    @staticmethod
    def _owner(db_session):
        roles = seed_system_roles(db_session)
        user = _make_user(db_session, role="user")
        org = _make_organization(db_session)
        mem = _make_membership(db_session, user, org)
        mem.role_id = roles["owner"].id
        db_session.flush()
        return roles, user, org

    def test_custom_role_lifecycle(self, client, db_session):
        """List, create, refuse an unknown scope, update and delete a custom
        role (folds the old list/create/invalid/update/delete tests)."""
        _, user, org = self._owner(db_session)
        url = f"/api/organizations/{org.id}/roles"

        resp = client.get(url, headers=auth_headers(user))
        assert resp.status_code == 200
        assert len(resp.json()["roles"]) >= 4  # at least the 4 system roles

        resp = client.post(
            url,
            json={
                "name": "custom_viewer",
                "display_name": "Custom Viewer",
                "permissions": ["tasks:read", "orders:read"],
            },
            headers=auth_headers(user),
        )
        assert resp.status_code == 200
        created = resp.json()
        assert created["name"] == "custom_viewer"
        assert created["is_system"] is False

        resp = client.post(
            url,
            json={
                "name": "bad",
                "display_name": "Bad",
                "permissions": ["nonexistent:perm"],
            },
            headers=auth_headers(user),
        )
        assert resp.status_code == 400

        resp = client.put(
            f"{url}/{created['id']}",
            json={
                "display_name": "Updated Test",
                "permissions": ["tasks:read", "tasks:write"],
            },
            headers=auth_headers(user),
        )
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "Updated Test"

        resp = client.delete(f"{url}/{created['id']}", headers=auth_headers(user))
        assert resp.status_code == 200

    def test_system_roles_cannot_be_modified_or_deleted(self, client, db_session):
        roles, user, org = self._owner(db_session)
        url = f"/api/organizations/{org.id}/roles/{roles['owner'].id}"

        resp = client.put(
            url, json={"display_name": "Hacked"}, headers=auth_headers(user)
        )
        assert resp.status_code == 400

        resp = client.delete(url, headers=auth_headers(user))
        assert resp.status_code == 400

    def test_cannot_delete_role_in_use(self, client, db_session):
        _, user, org = self._owner(db_session)

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
            headers=auth_headers(user),
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Tests: Member role assignment
# ---------------------------------------------------------------------------


class TestMemberRoleAssignment:
    def test_assign_role_to_member(self, client, db_session):
        roles = seed_system_roles(db_session)
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
            headers=auth_headers(admin),
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests: the permission catalogue the custom-role editor reads
# ---------------------------------------------------------------------------


class TestPermissionCatalogue:
    def test_every_org_scope_is_in_exactly_one_group(self):
        """The editor offers only grouped scopes: apps:build / apps:share were
        in no group, so no custom role could ever be given them (Oct 2026)."""
        grouped = [s for scopes in PERMISSION_GROUPS.values() for s in scopes]
        assert len(grouped) == len(set(grouped))
        assert set(grouped) == PERMISSION_SCOPES - PLATFORM_ADMIN_SCOPES

    def test_shape_is_scope_keys_and_named_groups(self, client, db_session):
        """Keys, not objects: the editor read `group.permissions` off a list
        of strings and crashed the page when a group was opened (Oct 2026)."""
        user = _make_user(db_session, role="user")
        resp = client.get("/api/permissions", headers=auth_headers(user))
        assert resp.status_code == 200
        data = resp.json()
        assert data["permissions"] == sorted(PERMISSION_SCOPES)
        assert data["groups"] == PERMISSION_GROUPS
        assert all(isinstance(s, str) for s in data["groups"]["Apps"])


# ---------------------------------------------------------------------------
# Tests: /auth/me includes permissions
# ---------------------------------------------------------------------------


class TestAuthMePermissions:
    def test_me_includes_permissions_for_org_user(self, client, db_session):
        roles = seed_system_roles(db_session)
        user = _make_user(db_session, role="user")
        org = _make_organization(db_session)
        mem = _make_membership(db_session, user, org)
        mem.role_id = roles["team_member"].id
        db_session.flush()

        resp = client.get("/api/auth/me", headers=auth_headers(user))
        assert resp.status_code == 200
        data = resp.json()
        assert "permissions" in data
        assert "tasks:read" in data["permissions"]
        assert data["org_role"]["name"] == "team_member"

    def test_me_admin_gets_all_perms(self, client, db_session):
        """A platform admin holds every org scope (and no org role), so the
        UI shows them every page; the admin:* scopes are not in the list."""
        user = _make_user(db_session, role="admin")
        resp = client.get("/api/auth/me", headers=auth_headers(user))
        assert resp.status_code == 200
        data = resp.json()
        assert sorted(data["permissions"]) == sorted(
            PERMISSION_SCOPES - PLATFORM_ADMIN_SCOPES
        )
        assert data["org_role"] is None

    def test_me_no_membership_empty_perms(self, client, db_session):
        user = _make_user(db_session, role="user")
        resp = client.get("/api/auth/me", headers=auth_headers(user))
        assert resp.status_code == 200
        data = resp.json()
        assert data["permissions"] == []
        assert data["org_role"] is None
