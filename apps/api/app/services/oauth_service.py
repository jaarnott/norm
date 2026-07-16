"""OAuth 2.0 Authorization Code Flow service.

Handles token exchange, refresh, and credential management for
connectors that use OAuth2 authentication.
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.db.models import ConnectorConfig, ConnectorSpec, OAuthState, Venue

logger = logging.getLogger(__name__)


def build_authorize_url(
    spec: ConnectorSpec,
    redirect_uri: str,
    db: Session,
    venue_id: str | None = None,
    user_id: str | None = None,
) -> str:
    """Build the authorization URL and persist the state parameter."""
    oauth = spec.oauth_config or {}
    authorize_url = oauth.get("authorize_url", "")
    client_id = oauth.get("client_id", "")
    scopes = oauth.get("scopes", "")

    if not authorize_url or not client_id:
        raise ValueError("OAuth config missing authorize_url or client_id")

    state = secrets.token_urlsafe(32)

    # Persist state for verification on callback
    oauth_state = OAuthState(
        connector_name=spec.connector_name,
        state=state,
        venue_id=venue_id,
        user_id=user_id,
    )
    db.add(oauth_state)
    db.commit()

    query: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
    }
    if scopes:
        query["scope"] = scopes

    # Google-specific: request offline access for refresh tokens
    if "accounts.google.com" in authorize_url:
        query["access_type"] = "offline"
        query["prompt"] = "consent"

    return authorize_url + "?" + urlencode(query)


def exchange_code(
    spec: ConnectorSpec,
    code: str,
    state: str,
    redirect_uri: str,
    db: Session,
) -> dict:
    """Exchange authorization code for tokens and store them.

    Returns the oauth_metadata (extra fields from the token response).
    """
    # Verify and consume state
    oauth_state = db.query(OAuthState).filter(OAuthState.state == state).first()
    if not oauth_state:
        raise ValueError("Invalid or expired OAuth state")
    if oauth_state.connector_name != spec.connector_name:
        raise ValueError("OAuth state connector mismatch")
    db.delete(oauth_state)

    oauth = spec.oauth_config or {}
    token_url = oauth.get("token_url", "")
    client_id = oauth.get("client_id", "")
    client_secret = oauth.get("client_secret", "")

    if not token_url:
        raise ValueError("OAuth config missing token_url")

    # Exchange code for tokens
    resp = httpx.post(
        token_url,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        timeout=15.0,
    )

    if resp.status_code != 200:
        logger.error(
            "OAuth token exchange failed: %s %s", resp.status_code, resp.text[:300]
        )
        raise ValueError(
            f"Token exchange failed ({resp.status_code}): {resp.text[:200]}"
        )

    token_data = resp.json()
    # Use venue_id/user_id from the OAuthState to store tokens correctly
    _store_tokens(
        db,
        spec.connector_name,
        token_data,
        venue_id=oauth_state.venue_id,
        user_id=oauth_state.user_id,
    )

    return token_data


def refresh_access_token(
    spec: ConnectorSpec,
    db: Session,
    venue_id: str | None = None,
    user_id: str | None = None,
) -> str:
    """Refresh an expired access token. Returns the new access_token.

    Providers such as LoadedHub *rotate* refresh tokens: each successful refresh
    returns a new refresh token and invalidates the previous one. Two concurrent
    refreshes would therefore race, and the loser's token would be dead — which
    eventually locks us out entirely.

    To prevent that we take a row lock on the ConnectorConfig for the duration of
    the exchange, so refreshes are serialised. After acquiring the lock we
    re-check expiry: if another caller already refreshed while we were blocked,
    we reuse their token instead of burning a second exchange. (Same shape as the
    ``with_for_update`` claim in ``task_scheduler._claim_due_tasks``; blocking
    rather than ``skip_locked`` so the loser waits and reuses the winner's token.)
    """
    query = db.query(ConnectorConfig).filter(
        ConnectorConfig.connector_name == spec.connector_name
    )
    if user_id:
        query = query.filter(ConnectorConfig.user_id == user_id)
    elif venue_id:
        query = query.filter(ConnectorConfig.venue_id == venue_id)
    config_row = query.with_for_update().first()
    if not config_row or not config_row.refresh_token:
        raise ValueError("No refresh token available")

    # Re-check under the lock — another caller may have just refreshed.
    if config_row.access_token and config_row.token_expires_at:
        if datetime.now(timezone.utc) < config_row.token_expires_at - timedelta(
            seconds=60
        ):
            return config_row.access_token

    previous_refresh_token = config_row.refresh_token

    oauth = spec.oauth_config or {}
    token_url = oauth.get("token_url", "")
    client_id = oauth.get("client_id", "")
    client_secret = oauth.get("client_secret", "")
    scopes = oauth.get("scopes", "")

    refresh_body = {
        "grant_type": "refresh_token",
        "refresh_token": config_row.refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    # LoadedHub expects scope on refresh requests; include if configured
    if scopes:
        refresh_body["scope"] = scopes

    resp = httpx.post(
        token_url,
        data=refresh_body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        timeout=15.0,
    )

    if resp.status_code != 200:
        logger.error(
            "OAuth token refresh failed: %s %s", resp.status_code, resp.text[:300]
        )
        raise ValueError(
            f"Token refresh failed ({resp.status_code}): {resp.text[:200]}"
        )

    token_data = resp.json()

    # A rotated refresh token resets the refresh-token lifetime. Log it so the
    # token's liveness is observable: if a connector goes long enough without a
    # refresh, the provider expires the refresh token and we get locked out
    # (which is exactly how LoadedHub broke — months of no runs, no rotation).
    new_refresh = token_data.get("refresh_token")
    if new_refresh and new_refresh != previous_refresh_token:
        logger.info(
            "%s issued a rotated refresh token (lifetime reset)", spec.connector_name
        )

    _store_tokens(
        db, spec.connector_name, token_data, venue_id=venue_id, user_id=user_id
    )

    return token_data["access_token"]


def get_valid_access_token(
    spec: ConnectorSpec,
    db: Session,
    venue_id: str | None = None,
    user_id: str | None = None,
) -> str:
    """Get a valid access token, refreshing if expired."""
    query = db.query(ConnectorConfig).filter(
        ConnectorConfig.connector_name == spec.connector_name
    )
    if user_id:
        query = query.filter(ConnectorConfig.user_id == user_id)
    elif venue_id:
        query = query.filter(ConnectorConfig.venue_id == venue_id)
    config_row = query.first()
    if not config_row or not config_row.access_token:
        raise ValueError(f"No OAuth tokens for connector {spec.connector_name}")

    # Check if token is expired (with 60s buffer)
    if config_row.token_expires_at:
        now = datetime.now(timezone.utc)
        if now >= config_row.token_expires_at - timedelta(seconds=60):
            return refresh_access_token(spec, db, venue_id=venue_id, user_id=user_id)

    return config_row.access_token


def refresh_all_tokens(
    db: Session | None = None, config_db: Session | None = None
) -> dict:
    """Proactively refresh every OAuth connector's tokens. Returns a summary.

    Providers such as LoadedHub *rotate* refresh tokens, and each rotation resets
    the refresh token's lifetime. A connector that goes unrefreshed for longer
    than that lifetime is permanently locked out and needs a manual
    re-authorization — which is exactly how LoadedHub broke here: months of
    failing task runs meant Loaded was never called, so the token was never
    rotated and quietly expired.

    Lazy refresh (``get_valid_access_token``) only fires when something actually
    uses the connector, so it cannot protect an idle one. This runs on a
    schedule instead, independent of task activity, and is what makes refresh
    reliable rather than a side effect of traffic.

    Normally a no-op for still-valid tokens: the short access-token lifetime
    (~1h) sets the real rotation cadence. Per-connector failures are logged and
    collected rather than aborting the run, so one dead connector can't stop the
    others from being kept alive.
    """
    from app.db.engine import SessionLocal, _ConfigSessionLocal

    owns_db = db is None
    owns_config_db = config_db is None
    if owns_db:
        db = SessionLocal()
    if owns_config_db:
        config_db = _ConfigSessionLocal()

    refreshed: list[str] = []
    failed: list[dict] = []
    skipped: list[str] = []

    try:
        rows = (
            db.query(ConnectorConfig)
            .filter(
                ConnectorConfig.refresh_token.isnot(None),
                ConnectorConfig.enabled == "true",
            )
            .all()
        )

        for row in rows:
            # Label by venue, not just connector name: the same connector is
            # configured per-venue, so "loadedhub" alone doesn't tell an operator
            # which venue to reconnect.
            label = row.connector_name
            if row.venue_id:
                venue = db.query(Venue).filter(Venue.id == row.venue_id).first()
                label = (
                    f"{row.connector_name} ({venue.name if venue else row.venue_id})"
                )

            spec = (
                config_db.query(ConnectorSpec)
                .filter(ConnectorSpec.connector_name == row.connector_name)
                .first()
            )
            if not spec or spec.auth_type != "oauth2" or not spec.oauth_config:
                skipped.append(label)
                continue

            try:
                if row.token_expires_at is None:
                    # With no expiry recorded, the lazy path never refreshes this
                    # connector (see get_valid_access_token), so its refresh token
                    # would rot. Force a rotation — which also establishes an
                    # expiry if the provider returns expires_in.
                    refresh_access_token(
                        spec, db, venue_id=row.venue_id, user_id=row.user_id
                    )
                else:
                    get_valid_access_token(
                        spec, db, venue_id=row.venue_id, user_id=row.user_id
                    )
                refreshed.append(label)
            except Exception as exc:
                logger.warning(
                    "Keep-alive refresh failed for %s (venue=%s): %s",
                    row.connector_name,
                    row.venue_id,
                    exc,
                )
                failed.append({"connector": label, "error": str(exc)[:200]})

        return {"refreshed": refreshed, "failed": failed, "skipped": skipped}
    finally:
        if owns_db:
            db.close()
        if owns_config_db:
            config_db.close()


def _store_tokens(
    db: Session,
    connector_name: str,
    token_data: dict,
    venue_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """Store token response in ConnectorConfig."""
    query = db.query(ConnectorConfig).filter(
        ConnectorConfig.connector_name == connector_name
    )
    if user_id:
        query = query.filter(ConnectorConfig.user_id == user_id)
    elif venue_id:
        query = query.filter(ConnectorConfig.venue_id == venue_id)
    else:
        query = query.filter(ConnectorConfig.venue_id.is_(None))
    config_row = query.first()

    if not config_row:
        config_row = ConnectorConfig(
            connector_name=connector_name,
            venue_id=venue_id,
            user_id=user_id,
            config={},
            enabled="true",
        )
        db.add(config_row)

    config_row.access_token = token_data.get("access_token")
    if token_data.get("refresh_token"):
        config_row.refresh_token = token_data["refresh_token"]

    expires_in = token_data.get("expires_in")
    if expires_in:
        config_row.token_expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=int(expires_in)
        )
    else:
        config_row.token_expires_at = None

    # Store extra metadata (venue_id, venue_name, etc.)
    known_keys = {"access_token", "refresh_token", "expires_in", "token_type", "scope"}
    extra = {k: v for k, v in token_data.items() if k not in known_keys}
    if extra:
        config_row.oauth_metadata = extra

    db.commit()
