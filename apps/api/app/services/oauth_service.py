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

from app.db.models import ConnectorConfig, ConnectorSpec, OAuthState

logger = logging.getLogger(__name__)


def build_authorize_url(spec: ConnectorSpec, redirect_uri: str, db: Session, venue_id: str | None = None) -> str:
    """Build the authorization URL and persist the state parameter."""
    oauth = spec.oauth_config or {}
    authorize_url = oauth.get("authorize_url", "")
    client_id = oauth.get("client_id", "")
    scopes = oauth.get("scopes", "")

    if not authorize_url or not client_id:
        raise ValueError("OAuth config missing authorize_url or client_id")

    state = secrets.token_urlsafe(32)

    # Persist state for verification on callback (venue_id tracks which venue this is for)
    oauth_state = OAuthState(connector_name=spec.connector_name, state=state, venue_id=venue_id)
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
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15.0,
    )

    if resp.status_code != 200:
        logger.error("OAuth token exchange failed: %s %s", resp.status_code, resp.text[:300])
        raise ValueError(f"Token exchange failed ({resp.status_code}): {resp.text[:200]}")

    token_data = resp.json()
    # Use venue_id from the OAuthState to store tokens for the correct venue
    _store_tokens(db, spec.connector_name, token_data, venue_id=oauth_state.venue_id)

    return token_data


def refresh_access_token(spec: ConnectorSpec, db: Session, venue_id: str | None = None) -> str:
    """Refresh an expired access token. Returns the new access_token."""
    query = db.query(ConnectorConfig).filter(ConnectorConfig.connector_name == spec.connector_name)
    if venue_id:
        query = query.filter(ConnectorConfig.venue_id == venue_id)
    config_row = query.first()
    if not config_row or not config_row.refresh_token:
        raise ValueError("No refresh token available")

    oauth = spec.oauth_config or {}
    token_url = oauth.get("token_url", "")
    client_id = oauth.get("client_id", "")
    client_secret = oauth.get("client_secret", "")

    resp = httpx.post(
        token_url,
        data={
            "grant_type": "refresh_token",
            "refresh_token": config_row.refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15.0,
    )

    if resp.status_code != 200:
        logger.error("OAuth token refresh failed: %s %s", resp.status_code, resp.text[:300])
        raise ValueError(f"Token refresh failed ({resp.status_code}): {resp.text[:200]}")

    token_data = resp.json()
    _store_tokens(db, spec.connector_name, token_data, venue_id=venue_id)

    return token_data["access_token"]


def get_valid_access_token(spec: ConnectorSpec, db: Session, venue_id: str | None = None) -> str:
    """Get a valid access token, refreshing if expired."""
    query = db.query(ConnectorConfig).filter(ConnectorConfig.connector_name == spec.connector_name)
    if venue_id:
        query = query.filter(ConnectorConfig.venue_id == venue_id)
    config_row = query.first()
    if not config_row or not config_row.access_token:
        raise ValueError(f"No OAuth tokens for connector {spec.connector_name}")

    # Check if token is expired (with 60s buffer)
    if config_row.token_expires_at:
        now = datetime.now(timezone.utc)
        if now >= config_row.token_expires_at - timedelta(seconds=60):
            return refresh_access_token(spec, db, venue_id=venue_id)

    return config_row.access_token


def _store_tokens(db: Session, connector_name: str, token_data: dict, venue_id: str | None = None) -> None:
    """Store token response in ConnectorConfig."""
    query = db.query(ConnectorConfig).filter(ConnectorConfig.connector_name == connector_name)
    if venue_id:
        query = query.filter(ConnectorConfig.venue_id == venue_id)
    else:
        query = query.filter(ConnectorConfig.venue_id.is_(None))
    config_row = query.first()

    if not config_row:
        config_row = ConnectorConfig(
            connector_name=connector_name,
            venue_id=venue_id,
            config={},
            enabled="true",
        )
        db.add(config_row)

    config_row.access_token = token_data.get("access_token")
    if token_data.get("refresh_token"):
        config_row.refresh_token = token_data["refresh_token"]

    expires_in = token_data.get("expires_in")
    if expires_in:
        config_row.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
    else:
        config_row.token_expires_at = None

    # Store extra metadata (venue_id, venue_name, etc.)
    known_keys = {"access_token", "refresh_token", "expires_in", "token_type", "scope"}
    extra = {k: v for k, v in token_data.items() if k not in known_keys}
    if extra:
        config_row.oauth_metadata = extra

    db.commit()
