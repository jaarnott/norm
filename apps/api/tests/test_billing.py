"""Tests for billing endpoints."""

import uuid
from unittest.mock import patch, MagicMock

import pytest

from app.db.models import (
    Organization, OrganizationMembership, Subscription, TokenUsage,
)


class TestGetBilling:
    """GET /api/billing/{org_id}"""

    @patch("app.services.billing_service.get_billing_info")
    def test_get_billing_as_member(
        self, mock_billing, client, db_session, admin_user, admin_headers,
        organization, admin_org_membership,
    ):
        mock_billing.return_value = {
            "organization_id": organization.id,
            "plan": "starter",
            "status": "trialing",
            "token_quota": 1_000_000,
            "tokens_used": 0,
        }

        resp = client.get(f"/api/billing/{organization.id}", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["organization_id"] == organization.id

    def test_get_billing_not_member_returns_403(
        self, client, db_session, manager_user, manager_headers, organization,
    ):
        resp = client.get(f"/api/billing/{organization.id}", headers=manager_headers)
        assert resp.status_code == 403

    def test_get_billing_without_auth_returns_401(self, client, organization):
        resp = client.get(f"/api/billing/{organization.id}")
        assert resp.status_code in (401, 403)


class TestSetupBilling:
    """POST /api/billing/{org_id}/setup"""

    @patch("app.services.billing_service.create_setup_intent")
    def test_setup_billing_as_owner(
        self, mock_setup, client, db_session, admin_user, admin_headers,
        organization, admin_org_membership,
    ):
        mock_setup.return_value = "seti_test_client_secret"

        resp = client.post(
            f"/api/billing/{organization.id}/setup",
            json={"token_plan": "basic"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["client_secret"] == "seti_test_client_secret"

    def test_setup_billing_as_member_returns_403(
        self, client, db_session, manager_user, manager_headers,
        organization, manager_org_membership,
    ):
        resp = client.post(
            f"/api/billing/{organization.id}/setup",
            json={"token_plan": "basic"},
            headers=manager_headers,
        )
        assert resp.status_code == 403


class TestSubscribe:
    """POST /api/billing/{org_id}/subscribe"""

    @patch("app.services.billing_service.create_subscription")
    def test_subscribe_as_owner(
        self, mock_sub, client, db_session, admin_user, admin_headers,
        organization, admin_org_membership,
    ):
        mock_sub.return_value = {"status": "active", "plan": "basic"}

        resp = client.post(
            f"/api/billing/{organization.id}/subscribe",
            json={"token_plan": "basic"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"


class TestChangePlan:
    """PUT /api/billing/{org_id}/plan"""

    @patch("app.services.billing_service.change_plan")
    def test_change_plan_as_owner(
        self, mock_change, client, db_session, admin_user, admin_headers,
        organization, admin_org_membership,
    ):
        mock_change.return_value = {"status": "active", "plan": "standard"}

        resp = client.put(
            f"/api/billing/{organization.id}/plan",
            json={"token_plan": "standard"},
            headers=admin_headers,
        )
        assert resp.status_code == 200

    def test_change_plan_as_member_returns_403(
        self, client, db_session, manager_user, manager_headers,
        organization, manager_org_membership,
    ):
        resp = client.put(
            f"/api/billing/{organization.id}/plan",
            json={"token_plan": "standard"},
            headers=manager_headers,
        )
        assert resp.status_code == 403


class TestUpdateAgents:
    """PUT /api/billing/{org_id}/agents"""

    def test_update_agents_as_owner(
        self, client, db_session, admin_user, admin_headers,
        organization, admin_org_membership,
    ):
        with patch("app.services.billing_service.get_billing_info") as mock_info:
            mock_info.return_value = {
                "organization_id": organization.id,
                "hr_agent_enabled": True,
                "procurement_agent_enabled": False,
            }
            resp = client.put(
                f"/api/billing/{organization.id}/agents",
                json={"hr": True, "procurement": False},
                headers=admin_headers,
            )
            assert resp.status_code == 200


class TestTopUp:
    """POST /api/billing/{org_id}/topup"""

    @patch("app.services.billing_service.purchase_top_up")
    def test_top_up_as_owner(
        self, mock_topup, client, db_session, admin_user, admin_headers,
        organization, admin_org_membership,
    ):
        mock_topup.return_value = {"tokens": 500_000, "status": "completed"}

        resp = client.post(
            f"/api/billing/{organization.id}/topup",
            json={"units": 1},
            headers=admin_headers,
        )
        assert resp.status_code == 200

    def test_top_up_as_member_returns_403(
        self, client, db_session, manager_user, manager_headers,
        organization, manager_org_membership,
    ):
        resp = client.post(
            f"/api/billing/{organization.id}/topup",
            json={"units": 1},
            headers=manager_headers,
        )
        assert resp.status_code == 403


class TestListInvoices:
    """GET /api/billing/{org_id}/invoices"""

    def test_list_invoices_no_subscription(
        self, client, db_session, admin_user, admin_headers,
        organization, admin_org_membership,
    ):
        resp = client.get(f"/api/billing/{organization.id}/invoices", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["invoices"] == []

    def test_list_invoices_not_member_returns_403(
        self, client, db_session, manager_user, manager_headers, organization,
    ):
        resp = client.get(f"/api/billing/{organization.id}/invoices", headers=manager_headers)
        assert resp.status_code == 403


class TestCancelSubscription:
    """DELETE /api/billing/{org_id}/subscription"""

    def test_cancel_no_subscription_returns_400(
        self, client, db_session, admin_user, admin_headers,
        organization, admin_org_membership,
    ):
        resp = client.delete(
            f"/api/billing/{organization.id}/subscription",
            headers=admin_headers,
        )
        assert resp.status_code == 400

    def test_cancel_as_member_returns_403(
        self, client, db_session, manager_user, manager_headers,
        organization, manager_org_membership,
    ):
        resp = client.delete(
            f"/api/billing/{organization.id}/subscription",
            headers=manager_headers,
        )
        assert resp.status_code == 403
