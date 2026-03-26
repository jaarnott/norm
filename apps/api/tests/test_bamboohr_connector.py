"""Tests for BambooHR connector."""

import os
from unittest.mock import patch, MagicMock

import httpx


BAMBOO_ENV = {"BAMBOOHR_SUBDOMAIN": "test", "BAMBOOHR_API_KEY": "fake-key"}


class TestMapFields:
    """Test BambooHrConnector._map_fields static method."""

    @patch.dict(os.environ, BAMBOO_ENV)
    def test_map_fields_full(self):
        from app.connectors.bamboohr import BambooHrConnector

        result = BambooHrConnector._map_fields(
            {
                "employee_name": "Sarah Jones",
                "role": "Bartender",
                "start_date": "2026-03-20",
                "email": "sarah@example.com",
                "phone": "021-555-1234",
                "venue": "La Zeppa",
                "employment_type": "Full-time",
            }
        )
        assert result == {
            "firstName": "Sarah",
            "lastName": "Jones",
            "jobTitle": "Bartender",
            "hireDate": "2026-03-20",
            "workEmail": "sarah@example.com",
            "mobilePhone": "021-555-1234",
            "location": "La Zeppa",
            "employmentHistoryStatus": "Full-time",
        }

    @patch.dict(os.environ, BAMBOO_ENV)
    def test_map_fields_single_name(self):
        from app.connectors.bamboohr import BambooHrConnector

        result = BambooHrConnector._map_fields({"employee_name": "Madonna"})
        assert result["firstName"] == "Madonna"
        assert result["lastName"] == ""

    @patch.dict(os.environ, BAMBOO_ENV)
    def test_map_fields_multi_word_name(self):
        from app.connectors.bamboohr import BambooHrConnector

        result = BambooHrConnector._map_fields({"employee_name": "Mary Jane Watson"})
        assert result["firstName"] == "Mary Jane"
        assert result["lastName"] == "Watson"


class TestSubmit:
    """Test BambooHrConnector.submit with mocked httpx."""

    @patch.dict(os.environ, BAMBOO_ENV)
    @patch("app.connectors.bamboohr.httpx.post")
    def test_submit_success(self, mock_post):
        from app.connectors.bamboohr import BambooHrConnector

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.headers = {
            "Location": "https://api.bamboohr.com/api/gateway.php/test/v1/employees/123"
        }
        mock_post.return_value = mock_resp

        connector = BambooHrConnector()
        result = connector.submit({"employee_name": "Sarah Jones", "role": "Bartender"})

        assert result.success is True
        assert result.reference == "123"
        assert result.response_payload["employee_id"] == "123"

    @patch.dict(os.environ, BAMBOO_ENV)
    @patch("app.connectors.bamboohr.httpx.post")
    def test_submit_auth_error(self, mock_post):
        from app.connectors.bamboohr import BambooHrConnector

        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_post.return_value = mock_resp

        connector = BambooHrConnector()
        result = connector.submit({"employee_name": "Sarah Jones"})

        assert result.success is False
        assert "401" in result.error_message

    @patch.dict(os.environ, BAMBOO_ENV)
    @patch("app.connectors.bamboohr.httpx.post")
    def test_submit_validation_error(self, mock_post):
        from app.connectors.bamboohr import BambooHrConnector

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "Invalid field: firstName is required"
        mock_post.return_value = mock_resp

        connector = BambooHrConnector()
        result = connector.submit({})

        assert result.success is False
        assert "400" in result.error_message

    @patch.dict(os.environ, BAMBOO_ENV)
    @patch("app.connectors.bamboohr.httpx.post")
    def test_submit_timeout(self, mock_post):
        from app.connectors.bamboohr import BambooHrConnector

        mock_post.side_effect = httpx.TimeoutException("timed out")

        connector = BambooHrConnector()
        result = connector.submit({"employee_name": "Sarah Jones"})

        assert result.success is False
        assert "timed out" in result.error_message

    @patch.dict(os.environ, BAMBOO_ENV)
    @patch("app.connectors.bamboohr.httpx.post")
    def test_submit_network_error(self, mock_post):
        from app.connectors.bamboohr import BambooHrConnector

        mock_post.side_effect = httpx.ConnectError("Connection refused")

        connector = BambooHrConnector()
        result = connector.submit({"employee_name": "Sarah Jones"})

        assert result.success is False
        assert "network error" in result.error_message.lower()
