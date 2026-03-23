import os

import httpx

from app.connectors.base import BaseConnector, ConnectorResult


class BambooHrConnector(BaseConnector):
    name = "bamboohr"

    def __init__(self, config: dict | None = None) -> None:
        if config:
            subdomain = config["subdomain"]
            self.api_key = config["api_key"]
        else:
            subdomain = os.environ["BAMBOOHR_SUBDOMAIN"]
            self.api_key = os.environ["BAMBOOHR_API_KEY"]
        self.base_url = (
            f"https://{subdomain}.bamboohr.com/api/gateway.php/{subdomain}/v1"
        )

    def submit(self, payload: dict) -> ConnectorResult:
        bamboo_payload = self._map_fields(payload)
        try:
            resp = httpx.post(
                f"{self.base_url}/employees/",
                json=bamboo_payload,
                auth=(self.api_key, "x"),
                headers={"Accept": "application/json"},
                timeout=30.0,
            )
        except httpx.TimeoutException:
            return ConnectorResult(
                success=False,
                reference=None,
                response_payload={},
                error_message="BambooHR request timed out",
            )
        except httpx.HTTPError as exc:
            return ConnectorResult(
                success=False,
                reference=None,
                response_payload={},
                error_message=f"BambooHR network error: {exc}",
            )

        if resp.status_code == 201:
            location = resp.headers.get("Location", "")
            employee_id = location.rstrip("/").rsplit("/", 1)[-1] if location else None
            return ConnectorResult(
                success=True,
                reference=employee_id,
                response_payload={
                    "employee_id": employee_id,
                    "status": "created",
                    "location": location,
                },
            )

        body = resp.text
        return ConnectorResult(
            success=False,
            reference=None,
            response_payload={"status_code": resp.status_code, "body": body[:200]},
            error_message=f"BambooHR API error {resp.status_code}: {body[:200]}",
        )

    @staticmethod
    def _map_fields(payload: dict) -> dict:
        result: dict[str, str] = {}

        name = payload.get("employee_name")
        if name:
            parts = name.rsplit(" ", 1)
            if len(parts) == 2:
                result["firstName"] = parts[0]
                result["lastName"] = parts[1]
            else:
                result["firstName"] = parts[0]
                result["lastName"] = ""

        mapping = {
            "role": "jobTitle",
            "start_date": "hireDate",
            "email": "workEmail",
            "phone": "mobilePhone",
            "venue": "location",
            "employment_type": "employmentHistoryStatus",
        }
        for src, dst in mapping.items():
            val = payload.get(src)
            if val:
                result[dst] = val

        return result
