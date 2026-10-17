"""Tests for billing endpoints."""

from unittest.mock import patch

import pytest


class TestGetBilling:
    """GET /api/billing/{org_id}"""

    @patch("app.services.billing_service.get_billing_info")
    def test_get_billing_as_member(
        self,
        mock_billing,
        client,
        db_session,
        admin_user,
        admin_headers,
        organization,
        admin_org_membership,
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


class TestSetupBilling:
    """POST /api/billing/{org_id}/setup"""

    @patch("app.services.billing_service.create_setup_intent")
    def test_setup_billing_as_owner(
        self,
        mock_setup,
        client,
        db_session,
        admin_user,
        admin_headers,
        organization,
        admin_org_membership,
    ):
        mock_setup.return_value = "seti_test_client_secret"

        resp = client.post(
            f"/api/billing/{organization.id}/setup",
            json={"token_plan": "basic"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["client_secret"] == "seti_test_client_secret"


class TestSubscribe:
    """POST /api/billing/{org_id}/subscribe"""

    @patch("app.services.billing_service.create_subscription")
    def test_subscribe_as_owner(
        self,
        mock_sub,
        client,
        db_session,
        admin_user,
        admin_headers,
        organization,
        admin_org_membership,
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
        self,
        mock_change,
        client,
        db_session,
        admin_user,
        admin_headers,
        organization,
        admin_org_membership,
    ):
        mock_change.return_value = {"status": "active", "plan": "standard"}

        resp = client.put(
            f"/api/billing/{organization.id}/plan",
            json={"token_plan": "standard"},
            headers=admin_headers,
        )
        assert resp.status_code == 200


class TestAgentEntitlementBilling:
    """The Organization.*_agent_enabled booleans are gone (q2r3s4t5u6v7):
    PUT /agents writes org_app_entitlements, and billing prices agent bundles
    from the marketplace catalog — so billing and access ride ONE switch."""

    def _seed_agent_app(self, db, slug, key, price, bundled):
        from app.db.config_models import MarketplaceApp

        db.add(
            MarketplaceApp(
                slug=slug,
                name=slug,
                tier="agent",
                status="active",
                bundled=bundled,
                price_cents=price,
                stripe_price_key=key,
                composition={"owns_agents": [key]},
            )
        )
        db.flush()

    def test_put_agents_writes_an_entitlement_row_and_prices_it(
        self,
        client,
        db_session,
        admin_user,
        admin_headers,
        organization,
        admin_org_membership,
    ):
        self._seed_agent_app(db_session, "hr-agent", "hr", 1000, bundled=False)
        resp = client.put(
            f"/api/billing/{organization.id}/agents",
            json={"hr": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["agents"]["hr"] is True
        assert data["cost_breakdown"]["agents"] == 1000

        from app.db.models import OrgAppEntitlement

        row = (
            db_session.query(OrgAppEntitlement)
            .filter_by(organization_id=organization.id, app_slug="hr-agent")
            .one()
        )
        assert row.enabled is True

    def test_disabling_stops_the_charge_and_the_agent_together(
        self,
        client,
        db_session,
        admin_user,
        admin_headers,
        organization,
        admin_org_membership,
    ):
        # Bundled = on by default: the org pays and has access with no row.
        self._seed_agent_app(db_session, "hr-agent", "hr", 1000, bundled=True)

        from app.services.entitlements import agent_entitled

        assert agent_entitled("hr", organization.id, db_session, db_session)

        resp = client.put(
            f"/api/billing/{organization.id}/agents",
            json={"hr": False},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["agents"]["hr"] is False
        assert data["cost_breakdown"]["agents"] == 0
        # The same row the marketplace reads: access follows the money.
        assert not agent_entitled("hr", organization.id, db_session, db_session)

    def test_unknown_key_is_ignored(
        self,
        client,
        db_session,
        admin_user,
        admin_headers,
        organization,
        admin_org_membership,
    ):
        # No catalog rows at all — the legacy body keys map to nothing and the
        # endpoint stays a harmless no-op (dark-launch friendly).
        resp = client.put(
            f"/api/billing/{organization.id}/agents",
            json={"hr": True, "procurement": False},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        from app.db.models import OrgAppEntitlement

        assert (
            db_session.query(OrgAppEntitlement)
            .filter_by(organization_id=organization.id)
            .count()
            == 0
        )


class TestTopUp:
    """POST /api/billing/{org_id}/topup"""

    @patch("app.services.billing_service.purchase_top_up")
    def test_top_up_as_owner(
        self,
        mock_topup,
        client,
        db_session,
        admin_user,
        admin_headers,
        organization,
        admin_org_membership,
    ):
        mock_topup.return_value = {"tokens": 500_000, "status": "completed"}

        resp = client.post(
            f"/api/billing/{organization.id}/topup",
            json={"units": 1},
            headers=admin_headers,
        )
        assert resp.status_code == 200


class TestListInvoices:
    """GET /api/billing/{org_id}/invoices"""

    def test_list_invoices_no_subscription(
        self,
        client,
        db_session,
        admin_user,
        admin_headers,
        organization,
        admin_org_membership,
    ):
        resp = client.get(
            f"/api/billing/{organization.id}/invoices", headers=admin_headers
        )
        assert resp.status_code == 200
        assert resp.json()["invoices"] == []


class TestCancelSubscription:
    """DELETE /api/billing/{org_id}/subscription"""

    def test_cancel_no_subscription_returns_400(
        self,
        client,
        db_session,
        admin_user,
        admin_headers,
        organization,
        admin_org_membership,
    ):
        resp = client.delete(
            f"/api/billing/{organization.id}/subscription",
            headers=admin_headers,
        )
        assert resp.status_code == 400


class TestNonOwnersAreRefused:
    """The billing routes refuse someone who isn't the org's owner: a plain
    member (no billing permission), or a user outside the org entirely."""

    @pytest.mark.parametrize(
        "verb,path,body,is_member",
        [
            ("get", "", None, False),
            ("post", "/setup", {"token_plan": "basic"}, True),
            ("put", "/plan", {"token_plan": "standard"}, True),
            ("post", "/topup", {"units": 1}, True),
            ("get", "/invoices", None, False),
            ("delete", "/subscription", None, True),
        ],
        ids=["get", "setup", "plan", "topup", "invoices", "cancel"],
    )
    def test_returns_403(
        self,
        request,
        client,
        db_session,
        manager_user,
        manager_headers,
        organization,
        verb,
        path,
        body,
        is_member,
    ):
        if is_member:
            request.getfixturevalue("manager_org_membership")
        kwargs = {"headers": manager_headers}
        if body is not None:
            kwargs["json"] = body
        resp = getattr(client, verb)(f"/api/billing/{organization.id}{path}", **kwargs)
        assert resp.status_code == 403
