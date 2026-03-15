import uuid
from datetime import datetime, timezone

from app.connectors.base import BaseConnector, ConnectorResult


class MockHrConnector(BaseConnector):
    name = "mock_hr"

    def submit(self, payload: dict) -> ConnectorResult:
        now = datetime.now(timezone.utc)
        employee_id = f"EMP-{uuid.uuid4().hex[:6].upper()}"

        response = {
            "employee_id": employee_id,
            "employee_name": payload.get("employee_name", "Unknown"),
            "status": "setup_complete",
            "confirmed_at": now.isoformat(),
            "venue": payload.get("venue"),
            "role": payload.get("role"),
            "start_date": payload.get("start_date"),
        }

        return ConnectorResult(
            success=True,
            reference=employee_id,
            response_payload=response,
        )
