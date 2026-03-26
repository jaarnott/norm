"""Tests for order endpoints."""

import uuid
from unittest.mock import patch


from app.db.models import Thread, Order


class TestListOrders:
    """GET /api/orders"""

    def test_list_orders(self, client, db_session, admin_user, admin_headers):
        thread = Thread(
            id=str(uuid.uuid4()),
            user_id=admin_user.id,
            domain="procurement",
            status="awaiting_approval",
            intent="place_order",
            raw_prompt="Order milk",
            extracted_fields={
                "product": {"id": "p1", "name": "Milk"},
                "venue": {"id": "v1", "name": "HQ"},
            },
        )
        db_session.add(thread)
        db_session.flush()

        order = Order(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            status="draft",
        )
        db_session.add(order)
        db_session.flush()

        resp = client.get("/api/orders", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "orders" in data
        assert len(data["orders"]) >= 1

    def test_list_orders_without_auth_returns_401(self, client):
        resp = client.get("/api/orders")
        assert resp.status_code in (401, 403)


class TestGetOrder:
    """GET /api/orders/{order_id}"""

    @patch("app.routers.orders.get_order")
    def test_get_order_detail(self, mock_get, client, admin_headers):
        order_id = str(uuid.uuid4())
        mock_get.return_value = {
            "id": order_id,
            "status": "draft",
            "lines": [],
        }

        resp = client.get(f"/api/orders/{order_id}", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["id"] == order_id

    @patch("app.routers.orders.get_order")
    def test_get_order_not_found_returns_404(self, mock_get, client, admin_headers):
        mock_get.return_value = None

        resp = client.get(f"/api/orders/{uuid.uuid4()}", headers=admin_headers)
        assert resp.status_code == 404

    def test_get_order_without_auth_returns_401(self, client):
        resp = client.get(f"/api/orders/{uuid.uuid4()}")
        assert resp.status_code in (401, 403)


class TestApproveOrder:
    """POST /api/orders/{order_id}/approve"""

    @patch("app.routers.orders.approve_order")
    def test_approve_order(self, mock_approve, client, admin_headers):
        order_id = str(uuid.uuid4())
        mock_approve.return_value = {"id": order_id, "status": "approved"}

        resp = client.post(f"/api/orders/{order_id}/approve", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    @patch("app.routers.orders.approve_order")
    def test_approve_order_not_found_returns_404(
        self, mock_approve, client, admin_headers
    ):
        mock_approve.return_value = None

        resp = client.post(f"/api/orders/{uuid.uuid4()}/approve", headers=admin_headers)
        assert resp.status_code == 404

    def test_approve_order_without_auth_returns_401(self, client):
        resp = client.post(f"/api/orders/{uuid.uuid4()}/approve")
        assert resp.status_code in (401, 403)


class TestRejectOrder:
    """POST /api/orders/{order_id}/reject"""

    @patch("app.routers.orders.reject_order")
    def test_reject_order(self, mock_reject, client, admin_headers):
        order_id = str(uuid.uuid4())
        mock_reject.return_value = {"id": order_id, "status": "rejected"}

        resp = client.post(f"/api/orders/{order_id}/reject", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

    @patch("app.routers.orders.reject_order")
    def test_reject_order_not_found_returns_404(
        self, mock_reject, client, admin_headers
    ):
        mock_reject.return_value = None

        resp = client.post(f"/api/orders/{uuid.uuid4()}/reject", headers=admin_headers)
        assert resp.status_code == 404


class TestSubmitOrder:
    """POST /api/orders/{order_id}/submit"""

    @patch("app.routers.orders.submit_order")
    def test_submit_order(self, mock_submit, client, admin_headers):
        order_id = str(uuid.uuid4())
        mock_submit.return_value = {"id": order_id, "status": "submitted"}

        resp = client.post(f"/api/orders/{order_id}/submit", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "submitted"

    @patch("app.routers.orders.submit_order")
    def test_submit_order_not_found_or_not_approved_returns_404(
        self,
        mock_submit,
        client,
        admin_headers,
    ):
        mock_submit.return_value = None

        resp = client.post(f"/api/orders/{uuid.uuid4()}/submit", headers=admin_headers)
        assert resp.status_code == 404
