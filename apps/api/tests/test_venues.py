"""Tests for venue endpoints."""

import uuid

import pytest

from app.db.models import Venue, UserVenueAccess, Organization, OrganizationMembership


class TestListVenues:
    """GET /api/venues"""

    def test_list_venues_returns_accessible_venues(
        self, client, db_session, admin_user, admin_headers, venue, admin_venue_access,
    ):
        resp = client.get("/api/venues", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "venues" in data
        # Should include at least the venue we have access to
        assert len(data["venues"]) >= 1
        venue_ids = [v["id"] for v in data["venues"]]
        assert venue.id in venue_ids

    def test_list_venues_without_auth_returns_401(self, client):
        resp = client.get("/api/venues")
        assert resp.status_code in (401, 403)


class TestCreateVenue:
    """POST /api/organizations/{org_id}/venues"""

    def test_create_venue_in_org(
        self, client, db_session, admin_user, admin_headers,
        organization, admin_org_membership,
    ):
        resp = client.post(
            f"/api/organizations/{organization.id}/venues",
            json={"name": "New Venue", "location": "Wellington", "timezone": "Pacific/Auckland"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "New Venue"
        assert data["location"] == "Wellington"
        assert data["timezone"] == "Pacific/Auckland"
        assert data["organization_id"] == organization.id

    def test_create_venue_org_not_found_returns_404(self, client, admin_headers):
        resp = client.post(
            f"/api/organizations/{uuid.uuid4()}/venues",
            json={"name": "Venue"},
            headers=admin_headers,
        )
        assert resp.status_code == 404

    def test_create_venue_without_auth_returns_401(self, client, organization):
        resp = client.post(
            f"/api/organizations/{organization.id}/venues",
            json={"name": "Venue"},
        )
        assert resp.status_code in (401, 403)

    def test_create_venue_missing_name_returns_422(
        self, client, admin_headers, organization, admin_org_membership,
    ):
        resp = client.post(
            f"/api/organizations/{organization.id}/venues",
            json={},
            headers=admin_headers,
        )
        assert resp.status_code == 422


class TestUpdateVenue:
    """PUT /api/venues/{venue_id}"""

    def test_update_venue(self, client, db_session, admin_headers, venue):
        resp = client.put(
            f"/api/venues/{venue.id}",
            json={"name": "Updated Venue", "location": "Christchurch"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Updated Venue"
        assert data["location"] == "Christchurch"

    def test_update_venue_not_found_returns_404(self, client, admin_headers):
        resp = client.put(
            f"/api/venues/{uuid.uuid4()}",
            json={"name": "Nope"},
            headers=admin_headers,
        )
        assert resp.status_code == 404


class TestDeleteVenue:
    """DELETE /api/venues/{venue_id}"""

    def test_delete_venue_as_admin(self, client, db_session, admin_headers, venue):
        resp = client.delete(f"/api/venues/{venue.id}", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_delete_venue_as_manager_returns_403(self, client, manager_headers, venue):
        resp = client.delete(f"/api/venues/{venue.id}", headers=manager_headers)
        assert resp.status_code == 403

    def test_delete_venue_not_found_returns_404(self, client, admin_headers):
        resp = client.delete(f"/api/venues/{uuid.uuid4()}", headers=admin_headers)
        assert resp.status_code == 404

    def test_delete_venue_without_auth_returns_401(self, client, venue):
        resp = client.delete(f"/api/venues/{venue.id}")
        assert resp.status_code in (401, 403)


class TestVenueConnectors:
    """GET /api/venues/{venue_id}/connectors"""

    def test_list_venue_connectors(self, client, db_session, admin_headers, venue):
        resp = client.get(f"/api/venues/{venue.id}/connectors", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["venue_id"] == venue.id
        assert "connectors" in data

    def test_list_venue_connectors_not_found_returns_404(self, client, admin_headers):
        resp = client.get(f"/api/venues/{uuid.uuid4()}/connectors", headers=admin_headers)
        assert resp.status_code == 404
