"""Tests for OAuth token handling in the connector execution path.

Regression cover for a production outage: LoadedHub's refresh token expired, the
refresh 400'd, and the failure was silently downgraded to an empty ``Bearer``
header. httpx rejected that as a malformed header, so the agent reported
"Network error: Illegal header value b'Bearer '" — completely hiding the real
cause (an expired token needing re-authorization).

Two invariants are locked down here:
  1. An auth failure NEVER produces an empty bearer token — it surfaces as an
     actionable authorization error.
  2. A 401 triggers exactly one refresh + retry (never a loop), which covers the
     case where token_expires_at was never stored so proactive refresh never fires.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.connectors.spec_executor import (
    ConnectorAuthError,
    _apply_auth,
    _should_retry_after_refresh,
    execute_spec,
)
from app.connectors.base import ConnectorResult


def _oauth_spec(connector_name="loadedhub", execution_mode="template"):
    spec = MagicMock()
    spec.connector_name = connector_name
    spec.auth_type = "oauth2"
    spec.auth_config = {}
    spec.oauth_config = {
        "token_url": "https://auth.example.com/token",
        "client_id": "cid",
        "client_secret": "secret",
        "scopes": "read",
    }
    spec.execution_mode = execution_mode
    spec.base_url_template = "https://api.example.com"
    return spec


class TestApplyAuthNeverSendsEmptyBearer:
    """The core regression: no empty `Bearer ` on the wire, ever."""

    def test_refresh_failure_raises_instead_of_empty_bearer(self):
        spec = _oauth_spec()
        db = MagicMock()

        with patch(
            "app.services.oauth_service.get_valid_access_token",
            side_effect=ValueError('Token refresh failed (400): "Refresh token is invalid or expired."'),
        ):
            with pytest.raises(ConnectorAuthError) as exc:
                _apply_auth({}, "oauth2", {}, {}, spec=spec, db=db)

        # Actionable, and names the connector + the remedy.
        assert "loadedhub" in str(exc.value)
        assert "Reconnect" in str(exc.value)

    def test_missing_token_raises_rather_than_blank_header(self):
        """No spec/db (preview path) + empty config JSON must not yield `Bearer `."""
        with pytest.raises(ConnectorAuthError):
            _apply_auth({}, "oauth2", {}, {}, spec=None, db=None)

    def test_valid_token_sets_header(self):
        spec = _oauth_spec()
        db = MagicMock()
        with patch(
            "app.services.oauth_service.get_valid_access_token", return_value="tok-123"
        ):
            headers, _ = _apply_auth({}, "oauth2", {}, {}, spec=spec, db=db)
        assert headers["Authorization"] == "Bearer tok-123"

    def test_execute_spec_reports_auth_error_not_network_error(self):
        """End-to-end: the agent must see an auth error, not 'Network error'."""
        spec = _oauth_spec()
        db = MagicMock()
        operation = {"method": "GET", "path_template": "/stock", "required_fields": []}

        with patch(
            "app.services.oauth_service.get_valid_access_token",
            side_effect=ValueError("Refresh token is invalid or expired."),
        ):
            result, _ = execute_spec(spec, operation, {}, {}, db)

        assert result.success is False
        assert "Reconnect" in result.error_message
        assert "Illegal header value" not in (result.error_message or "")
        assert "Network error" not in (result.error_message or "")


class TestRetryOn401:
    def test_401_on_oauth2_is_retryable(self):
        result = ConnectorResult(
            success=False, reference=None, response_payload={"status_code": 401}
        )
        assert _should_retry_after_refresh(result, _oauth_spec(), MagicMock()) is True

    def test_403_is_not_retryable(self):
        result = ConnectorResult(
            success=False, reference=None, response_payload={"status_code": 403}
        )
        assert _should_retry_after_refresh(result, _oauth_spec(), MagicMock()) is False

    def test_success_is_not_retried(self):
        result = ConnectorResult(success=True, reference=None, response_payload={})
        assert _should_retry_after_refresh(result, _oauth_spec(), MagicMock()) is False

    def test_non_oauth_401_is_not_retried(self):
        """A bearer/api-key connector has no refresh token to use."""
        spec = _oauth_spec()
        spec.auth_type = "bearer"
        result = ConnectorResult(
            success=False, reference=None, response_payload={"status_code": 401}
        )
        assert _should_retry_after_refresh(result, spec, MagicMock()) is False

    def test_401_refreshes_and_retries_exactly_once(self):
        """401 → refresh → replay → success. And only ONE retry."""
        spec = _oauth_spec()
        db = MagicMock()
        operation = {"method": "GET", "path_template": "/stock", "required_fields": []}

        unauthorized = ConnectorResult(
            success=False,
            reference=None,
            response_payload={"status_code": 401},
            error_message="API error 401",
        )
        ok = ConnectorResult(success=True, reference="r1", response_payload={"data": 1})

        with (
            patch(
                "app.services.oauth_service.get_valid_access_token",
                return_value="tok-new",
            ),
            patch("app.services.oauth_service.refresh_access_token") as mock_refresh,
            patch(
                "app.connectors.spec_executor.execute_http",
                side_effect=[unauthorized, ok],
            ) as mock_http,
        ):
            result, _ = execute_spec(spec, operation, {}, {}, db)

        assert result.success is True
        mock_refresh.assert_called_once()
        assert mock_http.call_count == 2, "should retry exactly once, not loop"

    def test_401_twice_gives_clean_error_and_does_not_loop(self):
        spec = _oauth_spec()
        db = MagicMock()
        operation = {"method": "GET", "path_template": "/stock", "required_fields": []}

        unauthorized = ConnectorResult(
            success=False,
            reference=None,
            response_payload={"status_code": 401},
            error_message="API error 401: Unauthorized",
        )

        with (
            patch(
                "app.services.oauth_service.get_valid_access_token",
                return_value="tok-new",
            ),
            patch("app.services.oauth_service.refresh_access_token"),
            patch(
                "app.connectors.spec_executor.execute_http",
                side_effect=[unauthorized, unauthorized],
            ) as mock_http,
        ):
            result, _ = execute_spec(spec, operation, {}, {}, db)

        assert result.success is False
        assert mock_http.call_count == 2, "must not retry more than once"

    def test_401_then_failed_refresh_reports_reconnect(self):
        spec = _oauth_spec()
        db = MagicMock()
        operation = {"method": "GET", "path_template": "/stock", "required_fields": []}

        unauthorized = ConnectorResult(
            success=False, reference=None, response_payload={"status_code": 401}
        )

        with (
            patch(
                "app.services.oauth_service.get_valid_access_token",
                return_value="tok-old",
            ),
            patch(
                "app.services.oauth_service.refresh_access_token",
                side_effect=ValueError("Refresh token is invalid or expired."),
            ),
            patch(
                "app.connectors.spec_executor.execute_http",
                side_effect=[unauthorized],
            ),
        ):
            result, _ = execute_spec(spec, operation, {}, {}, db)

        assert result.success is False
        assert "Reconnect" in result.error_message


class TestRefreshTokenRotation:
    """LoadedHub rotates refresh tokens; a rotated token must be persisted."""

    def test_rotated_refresh_token_is_stored(self, db_session):
        from app.db.models import ConnectorConfig
        from app.services.oauth_service import _store_tokens

        db_session.add(
            ConnectorConfig(
                connector_name="loadedhub",
                venue_id=None,
                config={},
                enabled="true",
                access_token="old-access",
                refresh_token="old-refresh",
            )
        )
        db_session.flush()

        _store_tokens(
            db_session,
            "loadedhub",
            {
                "access_token": "new-access",
                "refresh_token": "rotated-refresh",
                "expires_in": 3600,
            },
        )

        # Filter on venue_id too: a bare .first() can return an unrelated
        # venue-scoped row for the same connector.
        row = (
            db_session.query(ConnectorConfig)
            .filter(
                ConnectorConfig.connector_name == "loadedhub",
                ConnectorConfig.venue_id.is_(None),
            )
            .first()
        )
        assert row.access_token == "new-access"
        assert row.refresh_token == "rotated-refresh"
        assert row.token_expires_at > datetime.now(timezone.utc) + timedelta(
            minutes=50
        )

    def test_omitted_refresh_token_preserves_existing(self, db_session):
        """Providers that omit refresh_token on refresh must not blank ours."""
        from app.db.models import ConnectorConfig
        from app.services.oauth_service import _store_tokens

        db_session.add(
            ConnectorConfig(
                connector_name="loadedhub",
                venue_id=None,
                config={},
                enabled="true",
                access_token="old-access",
                refresh_token="keep-me",
            )
        )
        db_session.flush()

        _store_tokens(
            db_session,
            "loadedhub",
            {"access_token": "new-access", "expires_in": 3600},
        )

        # Filter on venue_id too: a bare .first() can return an unrelated
        # venue-scoped row for the same connector.
        row = (
            db_session.query(ConnectorConfig)
            .filter(
                ConnectorConfig.connector_name == "loadedhub",
                ConnectorConfig.venue_id.is_(None),
            )
            .first()
        )
        assert row.refresh_token == "keep-me"
