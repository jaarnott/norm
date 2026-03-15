import uuid
from datetime import datetime, timezone

from app.connectors.base import BaseConnector, ConnectorResult


class MockSupplierConnector(BaseConnector):
    name = "mock_supplier"

    def submit(self, payload: dict) -> ConnectorResult:
        now = datetime.now(timezone.utc)
        reference = f"ORD-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

        response = {
            "order_reference": reference,
            "supplier": payload.get("supplier", "Unknown"),
            "status": "confirmed",
            "estimated_delivery": "2-3 business days",
            "confirmed_at": now.isoformat(),
            "items": payload.get("items", []),
        }

        return ConnectorResult(
            success=True,
            reference=reference,
            response_payload=response,
        )
