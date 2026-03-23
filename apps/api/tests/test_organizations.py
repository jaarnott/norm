"""Tests for organization endpoints."""

import uuid

import pytest

from app.db.models import Organization, OrganizationMembership, UserVenueAccess


class TestListOrganizations:
    """GET /api/organizations"""

    def test_list_organizations_returns_user_orgs(
        self, client, db_session, admin_user, admin_headers,
        organization, admin_org_membership,
    ):
        resp = client.get("/api/organizations", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "organizations" in data
        assert len(data["organizations"]) == 1
        assert data["organizations"][0]["id"] == organization.id

    def test_list_organizations_empty_for_no_membership(
        self, client, db_session, manager_user, manager_headers,
    ):
        resp = client.get("/api/organizations", headers=manager_headers)
        assert resp.status_code == 200
        assert len(resp.json()["organizations"]) == 0

    def test_list_organizations_without_auth_returns_401(self, client):
        resp = client.get("/api/organizations")
        assert resp.status_code in (401, 403)


class TestCreateOrganization:
    """POST /api/organizations"""

    def test_create_organization_as_admin(self, client, db_session, admin_user, admin_headers):
        resp = client.post("/api/organizations", json={
            "name": "New Org",
            "slug": "new-org",
            "billing_email": "billing@example.com",
            "plan": "pro",
        }, headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "New Org"
        assert data["slug"] == "new-org"
        assert data["plan"] == "pro"
        assert data["billing_email"] == "billing@example.com"

    def test_create_organization_as_manager_returns_403(
        self, client, manager_headers,
    ):
        resp = client.post("/api/organizations", json={
            "name": "Forbidden Org",
            "slug": "forbidden-org",
        }, headers=manager_headers)
        assert resp.status_code == 403

    def test_create_organization_duplicate_slug_returns_400(
        self, client, db_session, admin_user, admin_headers, organization,
    ):
        resp = client.post("/api/organizations", json={
            "name": "Duplicate",
            "slug": organization.slug,
        }, headers=admin_headers)
        assert resp.status_code == 400

    def test_create_organization_missing_fields_returns_422(self, client, admin_headers):
        resp = client.post("/api/organizations", json={
            "name": "No Slug",
        }, headers=admin_headers)
        assert resp.status_code == 422

    def test_create_organization_without_auth_returns_401(self, client):
        resp = client.post("/api/organizations", json={
            "name": "No Auth",
            "slug": "no-auth",
        })
        assert resp.status_code in (401, 403)


class TestGetOrganization:
    """GET /api/organizations/{org_id}"""

    def test_get_organization_as_member(
        self, client, db_session, admin_user, admin_headers,
        organization, admin_org_membership,
    ):
        resp = client.get(f"/api/organizations/{organization.id}", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == organization.id
        assert "venues" in data
        assert "members" in data

    def test_get_organization_not_member_and_not_admin_returns_403(
        self, client, db_session, manager_user, manager_headers, organization,
    ):
        resp = client.get(f"/api/organizations/{organization.id}", headers=manager_headers)
        assert resp.status_code == 403

    def test_get_organization_not_found_returns_404(self, client, admin_headers):
        resp = client.get(f"/api/organizations/{uuid.uuid4()}", headers=admin_headers)
        assert resp.status_code == 404


class TestUpdateOrganization:
    """PUT /api/organizations/{org_id}"""

    def test_update_organization_as_owner(
        self, client, db_session, admin_user, admin_headers,
        organization, admin_org_membership,
    ):
        resp = client.put(f"/api/organizations/{organization.id}", json={
            "name": "Updated Org",
            "plan": "enterprise",
        }, headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated Org"
        assert resp.json()["plan"] == "enterprise"

    def test_update_organization_as_member_returns_403(
        self, client, db_session, manager_user, manager_headers,
        organization, manager_org_membership,
    ):
        resp = client.put(f"/api/organizations/{organization.id}", json={
            "name": "Nope",
        }, headers=manager_headers)
        assert resp.status_code == 403


class TestAddMember:
    """POST /api/organizations/{org_id}/members"""

    def test_add_member(
        self, client, db_session, admin_user, admin_headers,
        manager_user, organization, admin_org_membership,
    ):
        resp = client.post(f"/api/organizations/{organization.id}/members", json={
            "user_id": manager_user.id,
            "role": "member",
        }, headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_add_member_user_not_found_returns_404(
        self, client, admin_headers, organization, admin_org_membership,
    ):
        resp = client.post(f"/api/organizations/{organization.id}/members", json={
            "user_id": str(uuid.uuid4()),
            "role": "member",
        }, headers=admin_headers)
        assert resp.status_code == 404

    def test_add_member_org_not_found_returns_404(self, client, admin_headers, admin_user):
        resp = client.post(f"/api/organizations/{uuid.uuid4()}/members", json={
            "user_id": admin_user.id,
            "role": "member",
        }, headers=admin_headers)
        assert resp.status_code == 404


class TestRemoveMember:
    """DELETE /api/organizations/{org_id}/members/{user_id}"""

    def test_remove_member(
        self, client, db_session, admin_user, admin_headers,
        manager_user, organization, admin_org_membership, manager_org_membership,
    ):
        resp = client.delete(
            f"/api/organizations/{organization.id}/members/{manager_user.id}",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_remove_member_not_found_returns_404(
        self, client, admin_headers, organization, admin_org_membership,
    ):
        resp = client.delete(
            f"/api/organizations/{organization.id}/members/{uuid.uuid4()}",
            headers=admin_headers,
        )
        assert resp.status_code == 404


class TestUserVenues:
    """GET/PUT /api/users/{user_id}/venues"""

    def test_list_user_venues(
        self, client, db_session, admin_user, admin_headers, venue, admin_venue_access,
    ):
        resp = client.get(f"/api/users/{admin_user.id}/venues", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "venues" in data
        venue_ids = [v["id"] for v in data["venues"]]
        assert venue.id in venue_ids

    def test_set_user_venues(
        self, client, db_session, admin_user, admin_headers, venue,
    ):
        resp = client.put(
            f"/api/users/{admin_user.id}/venues",
            json={"venue_ids": [venue.id]},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["venue_count"] == 1


class TestFindUserByEmail:
    """GET /api/users/by-email"""

    def test_find_user_by_email(self, client, admin_user, admin_headers):
        resp = client.get(
            f"/api/users/by-email?email={admin_user.email}",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == admin_user.id

    def test_find_user_not_found_returns_404(self, client, admin_headers):
        resp = client.get(
            "/api/users/by-email?email=nobody@example.com",
            headers=admin_headers,
        )
        assert resp.status_code == 404


class TestOrgUsage:
    """GET /api/organizations/{org_id}/usage"""

    def test_get_usage(
        self, client, db_session, admin_user, admin_headers,
        organization, admin_org_membership,
    ):
        resp = client.get(
            f"/api/organizations/{organization.id}/usage?month=2026-03",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["month"] == "2026-03"
        assert "total_tokens" in data

    def test_get_daily_usage(
        self, client, db_session, admin_user, admin_headers,
        organization, admin_org_membership,
    ):
        resp = client.get(
            f"/api/organizations/{organization.id}/usage/daily?month=2026-03",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["month"] == "2026-03"
        assert "days" in data
