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

Also here (folded in 17 Oct 2026, docstrings kept as section comments): PKCE and
dynamic client registration for public clients, and the per-venue LoadedHub
wrong-company callback check.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.connectors.spec_executor import (
    ConnectorAuthError,
    _apply_auth,
    _should_retry_after_refresh,
    execute_spec,
    render_request,
)
from app.connectors.base import ConnectorResult
from app.db.config_models import ConnectionSpec
from app.db.models import Connection, OAuthState, Venue
from app.services import oauth_service
from urllib.parse import parse_qs, urlparse
import base64
import hashlib
import uuid


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
            side_effect=ValueError(
                'Token refresh failed (400): "Refresh token is invalid or expired."'
            ),
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


class TestPreviewRenderDoesNotNeedALiveToken:
    """The dry-run regression introduced alongside ConnectorAuthError.

    Requiring a token to *execute* is right; requiring one to *preview* is not.
    A dry-run never reaches the wire, and to_audit_dict redacts the auth header
    anyway — so demanding a live OAuth token only broke the preview button.
    """

    def _operation(self):
        return {"method": "GET", "path_template": "/stock", "required_fields": []}

    def test_preview_renders_a_placeholder_instead_of_raising(self):
        headers, _ = _apply_auth(
            {}, "oauth2", {}, {}, spec=None, db=None, require_token=False
        )
        assert headers["Authorization"].startswith("Bearer ")
        assert headers["Authorization"] != "Bearer "

    def test_render_request_without_db_still_previews(self):
        """Exactly what the dry-run router does: no db, empty credentials."""
        rendered = render_request(
            _oauth_spec(), self._operation(), {}, {}, require_token=False
        )
        assert rendered.url == "https://api.example.com/stock"

    def test_preview_never_reveals_the_token(self):
        with patch(
            "app.services.oauth_service.get_valid_access_token",
            return_value="secret-tok",
        ):
            rendered = render_request(
                _oauth_spec(),
                self._operation(),
                {},
                {},
                db=MagicMock(),
                venue_id="v1",
            )
        assert rendered.headers["Authorization"] == "Bearer secret-tok"
        assert "secret-tok" not in str(rendered.to_audit_dict())

    def test_execution_still_requires_a_real_token(self):
        """The placeholder must never leak into a request that goes on the wire."""
        with pytest.raises(ConnectorAuthError):
            render_request(_oauth_spec(), self._operation(), {}, {})


class TestVenueScopedTokenSelection:
    """LoadedHub scopes data by the token itself — it ignores x-loaded-company-id.

    So an unfiltered token lookup doesn't just pick a random row, it silently
    authenticates as the wrong venue and returns that venue's data. venue_id
    must reach get_valid_access_token on every executing path.
    """

    def test_execute_spec_passes_venue_id_through_to_token_lookup(self):
        spec = _oauth_spec()
        operation = {"method": "GET", "path_template": "/stock", "required_fields": []}

        with patch(
            "app.services.oauth_service.get_valid_access_token", return_value="tok"
        ) as get_token:
            execute_spec(spec, operation, {}, {}, MagicMock(), venue_id="venue-abc")

        assert get_token.call_args.kwargs.get("venue_id") == "venue-abc"

    def test_lookup_returns_the_requested_venues_token(self, db_session):
        """The half the mocked test above can't reach.

        Patching get_valid_access_token proves the argument was passed; it
        cannot prove the query honours it, because the mock has no rows to
        filter. With two real venues holding different tokens, picking the
        wrong one is exactly the silent cross-venue leak this guards.
        """
        import uuid

        from app.db.config_models import ConnectionSpec
        from app.db.models import Connection, Venue
        from app.services.oauth_service import get_valid_access_token

        spec = ConnectionSpec(
            connector_name="loadedhub",
            display_name="LoadedHub",
            execution_mode="template",
            auth_type="oauth2",
            auth_config={},
            tools=[],
        )
        db_session.add(spec)

        tokens = {}
        for name in ("Venue A", "Venue B"):
            venue = Venue(id=str(uuid.uuid4()), name=name)
            db_session.add(venue)
            token = f"token-for-{name.replace(' ', '-').lower()}"
            tokens[name] = (venue.id, token)
            db_session.add(
                Connection(
                    connector_name="loadedhub",
                    venue_id=venue.id,
                    enabled="true",
                    config={},
                    access_token=token,
                )
            )
        db_session.flush()

        for name, (venue_id, expected) in tokens.items():
            got = get_valid_access_token(spec, db_session, venue_id=venue_id)
            assert got == expected, f"{name} got another venue's token"

    def test_unscoped_lookup_never_returns_a_venues_token(self, db_session):
        """A None/None lookup must mean the GLOBAL (venue IS NULL) row.

        Before the fix, no filter was applied at all: a None/None call took
        whichever row came back first — and in refresh_access_token that SPENT
        the victim venue's rotating refresh token while _store_tokens filed the
        new one on the global row, silently killing that venue's connection on
        its next refresh (a "reconnect X" prompt with no visible cause).
        """
        import uuid

        import pytest

        from app.db.config_models import ConnectionSpec
        from app.db.models import Connection, Venue
        from app.services.oauth_service import (
            get_valid_access_token,
            refresh_access_token,
        )

        spec = ConnectionSpec(
            connector_name="loadedhub",
            display_name="LoadedHub",
            execution_mode="template",
            auth_type="oauth2",
            auth_config={},
            tools=[],
        )
        db_session.add(spec)
        venue = Venue(id=str(uuid.uuid4()), name="Only Venue")
        db_session.add(venue)
        db_session.add(
            Connection(
                connector_name="loadedhub",
                venue_id=venue.id,
                enabled="true",
                config={},
                access_token="venue-token",
                refresh_token="venue-refresh",
            )
        )
        db_session.flush()

        # Only venue rows exist: an unscoped lookup must fail loudly, not
        # borrow (or burn) a venue's tokens.
        with pytest.raises(ValueError):
            get_valid_access_token(spec, db_session)
        with pytest.raises(ValueError):
            refresh_access_token(spec, db_session)

        # With a global row present, the unscoped lookup returns exactly it.
        db_session.add(
            Connection(
                connector_name="loadedhub",
                venue_id=None,
                enabled="true",
                config={},
                access_token="global-token",
            )
        )
        db_session.flush()
        assert get_valid_access_token(spec, db_session) == "global-token"


class TestKeepAliveActuallyRedeems:
    """The keep-alive must perform a REAL redemption on every run.

    LoadedHub's access tokens live ~14 days but its refresh tokens live only
    ~24 HOURS (proven Aug-2026: a fresh, never-revoked grant was "invalid or
    expired" 26.4h after mint), so a lazy is-the-access-token-still-valid check
    redeems the refresh token ~13 days after it died. That was the Aug-2026
    failure: the keep-alive called get_valid_access_token (a no-op while the
    access token was valid), production made ONE real token call per 14 days —
    always a 400 — and every venue's connection collapsed on a 14-day cycle.
    force=True is the fix; each real redemption makes Loaded mint a fresh
    full-lifetime refresh token, so the chain survives while the scheduler
    cadence beats the refresh lifetime.
    """

    @pytest.fixture(autouse=True)
    def _isolate(self, db_session):
        # refresh_all_tokens scans every connector row; clear leftover rows from
        # the shared dev database so they can't bleed into the call counts.
        from app.db.models import Connection

        db_session.query(Connection).delete()
        db_session.flush()

    def _setup(self, db_session, expires_in_days=10):
        import uuid

        from app.db.config_models import ConnectionSpec
        from app.db.models import Connection, Venue

        spec = ConnectionSpec(
            connector_name="loadedhub",
            display_name="LoadedHub",
            execution_mode="template",
            auth_type="oauth2",
            auth_config={},
            oauth_config={
                "token_url": "https://loadedhub.test/api/token",
                "client_id": "cookbrothers",
                "client_secret": "s3cret",
            },
            tools=[],
        )
        db_session.add(spec)
        venue = Venue(id=str(uuid.uuid4()), name="Venue A")
        db_session.add(venue)
        db_session.flush()
        cfg = Connection(
            connector_name="loadedhub",
            venue_id=venue.id,
            enabled="true",
            config={},
            access_token="old-access",
            refresh_token="old-refresh",
            token_expires_at=datetime.now(timezone.utc)
            + timedelta(days=expires_in_days),
        )
        db_session.add(cfg)
        db_session.flush()
        return spec, cfg

    def _token_response(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 1209600,
        }
        return resp

    def test_force_redeems_even_with_valid_access_token(self, db_session):
        from app.services.oauth_service import refresh_access_token

        spec, cfg = self._setup(db_session)
        with patch(
            "app.services.oauth_service.httpx.post",
            return_value=self._token_response(),
        ) as post:
            got = refresh_access_token(
                spec, db_session, venue_id=cfg.venue_id, force=True
            )
        post.assert_called_once()
        assert got == "new-access"
        db_session.refresh(cfg)
        assert cfg.access_token == "new-access"
        assert cfg.refresh_token == "new-refresh"  # rotation stored

    def test_unforced_keeps_the_early_return(self, db_session):
        from app.services.oauth_service import refresh_access_token

        spec, cfg = self._setup(db_session)
        with patch("app.services.oauth_service.httpx.post") as post:
            got = refresh_access_token(spec, db_session, venue_id=cfg.venue_id)
        post.assert_not_called()
        assert got == "old-access"

    def test_refresh_all_tokens_forces_real_redemption(self, db_session):
        from app.services.oauth_service import refresh_all_tokens

        spec, cfg = self._setup(db_session)
        with patch(
            "app.services.oauth_service.httpx.post",
            return_value=self._token_response(),
        ) as post:
            summary = refresh_all_tokens(db=db_session, config_db=db_session)
        # The access token was 10 days from expiry — a lazy check would have
        # skipped it. The keep-alive must have redeemed anyway.
        post.assert_called_once()
        assert len(summary["refreshed"]) == 1
        assert summary["failed"] == []
        db_session.refresh(cfg)
        assert cfg.refresh_token == "new-refresh"

    def test_refresh_merges_metadata_instead_of_replacing(self, db_session):
        """A redemption must not wipe the stored venue binding.

        LoadedHub's code-exchange response carries VenueId/VenueName (stored in
        oauth_metadata and used by the connect card's "connected as X / wrong
        company" display), but its refresh-grant response does NOT — it carries
        only bookkeeping fields like ``.issued``/``.expires``. Replacing the
        metadata wholesale on refresh would blank the venue binding on the
        first keep-alive run after a connect.
        """
        from app.services.oauth_service import refresh_access_token

        spec, cfg = self._setup(db_session)
        cfg.oauth_metadata = {"venue_id": "company-123", "venue_name": "Venue A"}
        db_session.flush()

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 1209600,
            ".issued": "08/15/2026 01:00:00 +00:00",
            ".expires": "08/29/2026 01:00:00 +00:00",
        }
        with patch("app.services.oauth_service.httpx.post", return_value=resp):
            refresh_access_token(spec, db_session, venue_id=cfg.venue_id, force=True)

        db_session.refresh(cfg)
        assert cfg.oauth_metadata["venue_id"] == "company-123"  # preserved
        assert cfg.oauth_metadata["venue_name"] == "Venue A"  # preserved
        assert cfg.oauth_metadata[".issued"] == "08/15/2026 01:00:00 +00:00"  # updated


class TestRetryOn401:
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


class TestKeepAliveRefresh:
    """The scheduled keep-alive — what stops idle tokens from rotting.

    LoadedHub only resets a refresh token's lifetime when a rotation happens, so
    a connector nobody calls eventually expires and locks us out. Lazy refresh
    can't prevent that; this job is what makes refresh reliable.
    """

    @pytest.fixture(autouse=True)
    def _isolate(self, db_session):
        """Clear pre-existing connector rows.

        refresh_all_tokens scans every connector, so leftover rows in a shared
        dev database would otherwise bleed into these assertions. Safe: the
        db_session fixture rolls back, and the refresh calls are mocked so
        nothing commits.
        """
        from app.db.models import Connection

        db_session.query(Connection).delete()
        db_session.flush()

    def _spec_row(self, db_session, connector="loadedhub", expires_at=None):
        from app.db.models import Connection

        row = Connection(
            connector_name=connector,
            venue_id=None,
            config={},
            enabled="true",
            access_token="access",
            refresh_token="refresh",
            token_expires_at=expires_at,
        )
        db_session.add(row)
        db_session.flush()
        return row

    def test_live_token_is_still_force_redeemed(self, db_session):
        """A live access token must NOT skip the redemption.

        The lazy path was the Aug-2026 outage: Loaded's refresh tokens live
        ~24h while access tokens live ~14 days, so waiting for access-token
        expiry redeems the refresh token ~13 days after it died. Every
        keep-alive run redeems for real, forced.

        Also (was test_null_expiry_forces_a_refresh): token_expires_at IS NULL
        ⇒ lazy refresh never fires ⇒ token would rot. Both rows are redeemed,
        forced, and the lazy get_valid_access_token is never consulted.
        """
        from app.db.models import Connection
        from app.services.oauth_service import refresh_all_tokens

        config_db = MagicMock()
        config_db.query.return_value.filter.return_value.first.return_value = (
            _oauth_spec()
        )

        for expires_at in (None, datetime.now(timezone.utc) + timedelta(hours=1)):
            db_session.query(Connection).delete()
            self._spec_row(db_session, expires_at=expires_at)
            with (
                patch(
                    "app.services.oauth_service.refresh_access_token"
                ) as mock_refresh,
                patch("app.services.oauth_service.get_valid_access_token") as mock_lazy,
            ):
                result = refresh_all_tokens(db=db_session, config_db=config_db)

            mock_refresh.assert_called_once()
            assert mock_refresh.call_args.kwargs.get("force") is True, expires_at
            mock_lazy.assert_not_called()
            assert "loadedhub" in result["refreshed"]

    def test_one_dead_connector_does_not_stop_the_others(self, db_session):
        """A connector needing re-auth must not block keeping the rest alive."""
        from app.services.oauth_service import refresh_all_tokens

        self._spec_row(
            db_session,
            connector="loadedhub",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        self._spec_row(
            db_session,
            connector="gmail",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        config_db = MagicMock()
        config_db.query.return_value.filter.return_value.first.return_value = (
            _oauth_spec()
        )

        def _fail_loadedhub(spec, db, venue_id=None, user_id=None, force=False):
            raise ValueError("Refresh token is invalid or expired.")

        with patch(
            "app.services.oauth_service.refresh_access_token",
            side_effect=_fail_loadedhub,
        ):
            result = refresh_all_tokens(db=db_session, config_db=config_db)

        assert len(result["failed"]) == 2
        assert all("invalid or expired" in f["error"] for f in result["failed"])

    def test_non_oauth_connector_is_skipped(self, db_session):
        from app.services.oauth_service import refresh_all_tokens

        self._spec_row(db_session, connector="bamboohr")
        spec = _oauth_spec(connector_name="bamboohr")
        spec.auth_type = "api_key_header"
        config_db = MagicMock()
        config_db.query.return_value.filter.return_value.first.return_value = spec

        with patch("app.services.oauth_service.refresh_access_token") as mock_refresh:
            result = refresh_all_tokens(db=db_session, config_db=config_db)

        mock_refresh.assert_not_called()
        assert "bamboohr" in result["skipped"]


class TestRefreshTokenRotation:
    """LoadedHub rotates refresh tokens; a rotated token must be persisted."""

    def test_rotated_refresh_token_is_stored(self, db_session):
        from app.db.models import Connection
        from app.services.oauth_service import _store_tokens

        db_session.add(
            Connection(
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
            db_session.query(Connection)
            .filter(
                Connection.connector_name == "loadedhub",
                Connection.venue_id.is_(None),
            )
            .first()
        )
        assert row.access_token == "new-access"
        assert row.refresh_token == "rotated-refresh"
        assert row.token_expires_at > datetime.now(timezone.utc) + timedelta(minutes=50)

    def test_omitted_refresh_token_preserves_existing(self, db_session):
        """Providers that omit refresh_token on refresh must not blank ours."""
        from app.db.models import Connection
        from app.services.oauth_service import _store_tokens

        db_session.add(
            Connection(
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
            db_session.query(Connection)
            .filter(
                Connection.connector_name == "loadedhub",
                Connection.venue_id.is_(None),
            )
            .first()
        )
        assert row.refresh_token == "keep-me"


class TestConnectionHealthIsPersisted:
    """A dead refresh token must be visible before anyone attempts a fetch.

    bool(access_token) stays true after the refresh token dies, so the UI read
    "Connected" through the whole LoadedHub outage. These lock down the durable
    signal the connectors list and the in-conversation reconnect card key off.
    """

    def _venue_row(self, db_session, *, needs_reconnect=False):
        import uuid

        from app.db.models import Connection, Venue

        venue = Venue(id=str(uuid.uuid4()), name="Health Venue", location="AKL")
        db_session.add(venue)
        row = Connection(
            connector_name="loadedhub",
            venue_id=venue.id,
            config={},
            enabled="true",
            access_token="old-access",
            refresh_token="rt",
            token_expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
            needs_reconnect=needs_reconnect,
        )
        db_session.add(row)
        db_session.flush()
        return venue, row

    def test_a_refused_refresh_flags_needs_reconnect(self, db_session):
        from app.services.oauth_service import refresh_access_token

        venue, row = self._venue_row(db_session)
        resp = MagicMock(status_code=400, text='"Refresh token is invalid or expired."')
        with patch("app.services.oauth_service.httpx.post", return_value=resp):
            with pytest.raises(ValueError):
                refresh_access_token(_oauth_spec(), db_session, venue_id=venue.id)

        db_session.refresh(row)
        assert row.needs_reconnect is True
        assert "invalid or expired" in (row.last_auth_error or "")
        assert row.last_auth_checked_at is not None

    def test_a_transient_network_error_does_not_flag(self, db_session):
        """A blip is not "reconnect me" — only a provider rejection is."""
        import httpx

        from app.services.oauth_service import refresh_access_token

        venue, row = self._venue_row(db_session)
        with patch(
            "app.services.oauth_service.httpx.post",
            side_effect=httpx.ConnectError("boom"),
        ):
            with pytest.raises(httpx.ConnectError):
                refresh_access_token(_oauth_spec(), db_session, venue_id=venue.id)

        db_session.refresh(row)
        assert row.needs_reconnect is False

    def test_a_successful_store_clears_the_flag(self, db_session):
        from app.services.oauth_service import _store_tokens

        venue, row = self._venue_row(db_session, needs_reconnect=True)
        row.last_auth_error = "old error"
        db_session.flush()

        _store_tokens(
            db_session,
            "loadedhub",
            {"access_token": "fresh", "refresh_token": "rt2", "expires_in": 3600},
            venue_id=venue.id,
        )

        db_session.refresh(row)
        assert row.needs_reconnect is False
        assert row.last_auth_error is None


class TestVenueMailboxNeedsAVenue:
    """A venue mailbox (Hiring's interview invites) belongs to one venue;
    authorising one without a venue would store the token org-wide."""

    def test_authorize_without_a_venue_is_refused(
        self, client, db_session, admin_headers
    ):
        import uuid as _uuid

        from app.db.config_models import ConnectionSpec

        name = f"vm_{_uuid.uuid4().hex[:6]}"
        db_session.add(
            ConnectionSpec(
                id=str(_uuid.uuid4()),
                connector_name=name,
                display_name="Venue mailbox",
                category="calendar",
                auth_type="oauth2",
                execution_mode="template",
                oauth_config={
                    "authorize_url": "https://accounts.example.com/o/oauth2/auth",
                    "token_url": "https://oauth.example.com/token",
                    "client_id": "x",
                    "scopes": "calendar",
                },
                tools=[],
                enabled=True,
            )
        )
        db_session.flush()
        r = client.get(f"/api/oauth/authorize/{name}", headers=admin_headers)
        assert r.status_code == 400 and "venue" in r.json()["detail"].lower()


class TestCredentialHeaderSanitization:
    """User-entered credential values must never reach the wire with stray
    whitespace — a leading space on a stored x-loaded-company-id made httpx
    reject the request as an illegal header value (prod 500, which the
    invoices dashboard rendered as 'No outstanding invoices')."""

    def test_bearer_token_is_stripped(self):
        from app.connectors.spec_executor import _apply_auth

        headers, _ = _apply_auth(
            {}, "bearer", {"token_field": "api_key"}, {"api_key": " tok-123\n"}
        )
        assert headers["Authorization"] == "Bearer tok-123"

    def test_api_key_header_is_stripped(self):
        from app.connectors.spec_executor import _apply_auth

        headers, _ = _apply_auth(
            {},
            "api_key_header",
            {"header_name": "X-API-Key", "key_field": "api_key"},
            {"api_key": "  key-9  "},
        )
        assert headers["X-API-Key"] == "key-9"


# ──── PKCE + dynamic client registration ────────────────────────────────────
#
# PKCE + dynamic client registration for public OAuth 2.1 clients.
#
# These lock down the Cook Brothers App MCP connector's auth: a public client
# (token_endpoint_auth_method=none) must use PKCE (S256) and self-register (RFC
# 7591), and the MCP executor must authenticate with the resulting access token.
# Confidential clients (LoadedHub) must be unaffected.


def _expected_challenge(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )


def _public_spec(connector_name="cook_brothers_app"):
    spec = MagicMock()
    spec.connector_name = connector_name
    spec.auth_type = "oauth2"
    spec.auth_config = {}
    spec.oauth_config = {
        "authorize_url": "https://cb.example.com/oauth/authorize",
        "token_url": "https://cb.example.com/oauth/token",
        "registration_url": "https://cb.example.com/oauth/register",
        "scopes": "mcp",
        "token_endpoint_auth_method": "none",
        "pkce": True,
        "client_id": "public-cid",
    }
    return spec


class TestPkceGeneration:
    def test_challenge_is_s256_of_verifier(self):
        verifier, challenge = oauth_service._generate_pkce()
        assert 43 <= len(verifier) <= 128
        assert challenge == _expected_challenge(verifier)
        assert "=" not in challenge  # base64url, unpadded

    def test_uses_pkce_true_for_public_client(self):
        assert oauth_service._uses_pkce({"token_endpoint_auth_method": "none"}) is True
        assert oauth_service._uses_pkce({"pkce": True}) is True

    def test_uses_pkce_false_for_confidential_client(self):
        assert oauth_service._uses_pkce({"client_secret": "s"}) is False
        assert oauth_service._uses_pkce({}) is False


class TestAuthorizeUrlPkce:
    def test_public_client_authorize_url_has_pkce_and_persists_verifier(
        self, db_session
    ):
        from app.db.models import OAuthState

        url = oauth_service.build_authorize_url(
            _public_spec(), "https://norm/api/oauth/callback", db_session
        )
        q = parse_qs(urlparse(url).query)
        assert q["code_challenge_method"] == ["S256"]
        challenge = q["code_challenge"][0]
        state = q["state"][0]

        row = db_session.query(OAuthState).filter(OAuthState.state == state).first()
        assert row is not None
        assert row.code_verifier  # stored for the token exchange
        assert challenge == _expected_challenge(row.code_verifier)

    def test_confidential_client_authorize_url_has_no_pkce(self, db_session):
        spec = MagicMock()
        spec.connector_name = "loadedhub"
        spec.oauth_config = {
            "authorize_url": "https://auth.example.com/authorize",
            "client_id": "cid",
            "client_secret": "secret",
            "scopes": "read",
        }
        url = oauth_service.build_authorize_url(
            spec, "https://norm/api/oauth/callback", db_session
        )
        q = parse_qs(urlparse(url).query)
        assert "code_challenge" not in q
        assert "code_challenge_method" not in q


class TestDynamicRegistration:
    def test_register_client_posts_and_stores_client_id(self, db_session):
        from app.db.config_models import ConnectionSpec

        spec = ConnectionSpec(
            connector_name="cb_reg_test",
            display_name="CB",
            execution_mode="mcp",
            auth_type="oauth2",
            auth_config={},
            oauth_config={
                "registration_url": "https://cb.example.com/oauth/register",
                "token_endpoint_auth_method": "none",
                "scopes": "mcp",
            },
        )
        db_session.add(spec)
        db_session.flush()

        resp = MagicMock(status_code=201)
        resp.json.return_value = {"client_id": "registered-123"}
        with patch(
            "app.services.oauth_service.httpx.post", return_value=resp
        ) as mock_post:
            client_id = oauth_service.register_client(
                spec, ["https://norm/api/oauth/callback"], db_session
            )

        assert client_id == "registered-123"
        assert spec.oauth_config["client_id"] == "registered-123"
        body = mock_post.call_args.kwargs["json"]
        assert body["token_endpoint_auth_method"] == "none"
        assert body["redirect_uris"] == ["https://norm/api/oauth/callback"]
        assert "authorization_code" in body["grant_types"]

    def test_register_client_is_idempotent_when_client_id_present(self, db_session):
        spec = MagicMock()
        spec.connector_name = "cb"
        spec.oauth_config = {"client_id": "already", "registration_url": "https://x"}
        with patch("app.services.oauth_service.httpx.post") as mock_post:
            client_id = oauth_service.register_client(
                spec, ["https://n/cb"], db_session
            )
        assert client_id == "already"
        mock_post.assert_not_called()


class TestTokenExchangePublicClient:
    def test_exchange_sends_verifier_and_omits_secret(self, db_session):
        from app.db.models import OAuthState

        state = "st-pkce-1"
        db_session.add(
            OAuthState(
                connector_name="cook_brothers_app",
                state=state,
                venue_id=None,
                user_id=None,
                code_verifier="the-verifier",
            )
        )
        db_session.flush()

        resp = MagicMock(status_code=200)
        resp.json.return_value = {"access_token": "at", "refresh_token": "rt"}
        with patch(
            "app.services.oauth_service.httpx.post", return_value=resp
        ) as mock_post:
            oauth_service.exchange_code(
                _public_spec(),
                "the-code",
                state,
                "https://norm/api/oauth/callback",
                db_session,
            )

        body = mock_post.call_args.kwargs["data"]
        assert body["code_verifier"] == "the-verifier"
        assert body["grant_type"] == "authorization_code"
        assert "client_secret" not in body  # public client


class TestMcpExecutorOauthHeader:
    def test_oauth2_uses_access_token_as_bearer(self):
        from app.connectors.mcp_executor import _build_auth_headers

        headers = _build_auth_headers({"access_token": "tok-123"}, "oauth2", {})
        assert headers["Authorization"] == "Bearer tok-123"

    def test_bearer_still_reads_token_field(self):
        from app.connectors.mcp_executor import _build_auth_headers

        headers = _build_auth_headers(
            {"api_key": "k"}, "bearer", {"token_field": "api_key"}
        )
        assert headers["Authorization"] == "Bearer k"


# ──── Per-venue connect refuses a wrong-company token ───────────────────────
#
# Per-venue LoadedHub connect must refuse a wrong-company token.
#
# The 14 Aug incident: reconnect clicks made while the Loaded session had Bessie
# active minted Bessie tokens for four other venues, and the callback stored each
# one and said "Connected successfully" — every venue then served Bessie's data
# (Loaded scopes data by the token, not the x-loaded-company-id header). These
# tests pin the callback validation: a token whose company doesn't match the
# venue's stored ``x_loaded_company_id`` is rejected loudly (tokens wiped,
# ``needs_reconnect`` set, failure page); a matching token succeeds; a venue with
# no stored company id adopts the token's.


COMPANY_A = "aaaaaaaa-1111-2222-3333-444444444444"
COMPANY_B = "bbbbbbbb-5555-6666-7777-888888888888"


def _setup(db_session, *, stored_company, state_venue=True):
    """A loadedhub spec + one venue whose row already holds freshly-exchanged
    tokens (exchange_code stores before the callback validates), + the state."""
    spec = ConnectionSpec(
        connector_name="loadedhub",
        display_name="LoadedHub",
        execution_mode="template",
        auth_type="oauth2",
        auth_config={},
        oauth_config={"token_url": "https://example.test/token", "client_id": "x"},
        tools=[],
    )
    db_session.add(spec)
    venue = Venue(id=str(uuid.uuid4()), name="La Zeppa")
    db_session.add(venue)
    db_session.flush()  # venue row must exist before FK'd rows below
    cfg = Connection(
        connector_name="loadedhub",
        venue_id=venue.id,
        enabled="true",
        config={"x_loaded_company_id": stored_company} if stored_company else {},
        access_token="freshly-exchanged-token",
        refresh_token="freshly-exchanged-refresh",
    )
    db_session.add(cfg)
    state = OAuthState(
        connector_name="loadedhub",
        venue_id=venue.id if state_venue else None,
        state=f"state-{uuid.uuid4().hex}",
    )
    db_session.add(state)
    db_session.flush()
    return venue, cfg, state


def _callback(client, state, token_data):
    with patch("app.routers.oauth.exchange_code", return_value=token_data):
        return client.get(
            f"/api/oauth/callback?code=abc&state={state.state}",
            follow_redirects=False,
        )


class TestWrongCompanyRejected:
    def test_mismatch_wipes_tokens_and_fails_loudly(self, client, db_session):
        venue, cfg, state = _setup(db_session, stored_company=COMPANY_A)
        res = _callback(
            client,
            state,
            {
                "access_token": "tok",
                "venue_id": COMPANY_B,
                "venue_name": "Bessie & Engineers",
            },
        )
        assert res.status_code == 400
        assert "Bessie &amp; Engineers" in res.text or "Bessie & Engineers" in res.text
        assert "La Zeppa" in res.text
        db_session.refresh(cfg)
        assert cfg.access_token is None
        assert cfg.refresh_token is None
        assert cfg.needs_reconnect is True
        assert "wrong Loaded company" in (cfg.last_auth_error or "")
        # The configured company id is untouched — only the tokens were bad.
        assert cfg.config["x_loaded_company_id"] == COMPANY_A

    def test_whitespace_in_stored_id_still_matches(self, client, db_session):
        # La Zeppa's real stored id carries a leading space — must not reject.
        venue, cfg, state = _setup(db_session, stored_company=f" {COMPANY_A}")
        res = _callback(client, state, {"access_token": "tok", "venue_id": COMPANY_A})
        assert res.status_code == 200
        db_session.refresh(cfg)
        assert cfg.access_token == "freshly-exchanged-token"
        assert not cfg.needs_reconnect


class TestAcceptedPaths:
    def test_matching_company_succeeds(self, client, db_session):
        venue, cfg, state = _setup(db_session, stored_company=COMPANY_A)
        res = _callback(client, state, {"access_token": "tok", "venue_id": COMPANY_A})
        assert res.status_code == 200
        db_session.refresh(cfg)
        assert cfg.access_token == "freshly-exchanged-token"
        assert not cfg.needs_reconnect

    def test_first_connect_adopts_company_id(self, client, db_session):
        venue, cfg, state = _setup(db_session, stored_company=None)
        res = _callback(client, state, {"access_token": "tok", "venue_id": COMPANY_B})
        assert res.status_code == 200
        db_session.refresh(cfg)
        assert cfg.config["x_loaded_company_id"] == COMPANY_B
        assert cfg.access_token == "freshly-exchanged-token"

    def test_no_company_in_token_response_keeps_legacy_behavior(
        self, client, db_session
    ):
        venue, cfg, state = _setup(db_session, stored_company=COMPANY_A)
        res = _callback(client, state, {"access_token": "tok"})
        assert res.status_code == 200
        db_session.refresh(cfg)
        assert cfg.access_token == "freshly-exchanged-token"
