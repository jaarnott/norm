"""OAuth 2.0 Authorization Code Flow service.

Handles token exchange, refresh, and credential management for
connectors that use OAuth2 authentication.
"""

import base64
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.db.models import Connection, ConnectionSpec, OAuthState, Venue

logger = logging.getLogger(__name__)

# How long an unconsumed OAuth state stays redeemable. A user has to complete
# the provider's login within this window; long enough for a real login, short
# enough that a leaked/abandoned state doesn't linger.
OAUTH_STATE_TTL_MIN = 15


def _uses_pkce(oauth: dict) -> bool:
    """Whether this connector's OAuth flow uses PKCE.

    Explicit ``pkce: true`` opts in; a public client
    (``token_endpoint_auth_method == "none"``) always does, since OAuth 2.1
    requires PKCE when there is no client secret to authenticate the exchange.
    """
    return bool(oauth.get("pkce")) or oauth.get("token_endpoint_auth_method") == "none"


def _generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for PKCE S256 (RFC 7636)."""
    verifier = secrets.token_urlsafe(64)  # ~86 chars, within the 43-128 range
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    return verifier, challenge


def register_client(
    spec: ConnectionSpec, redirect_uris: list[str], config_db: Session
) -> str:
    """Dynamically register Norm as an OAuth client (RFC 7591). Returns client_id.

    Public-client registration (``token_endpoint_auth_method: none``), so no
    secret is issued. Done once per connector — the returned ``client_id`` is
    persisted into ``spec.oauth_config`` (config DB), and subsequent calls
    short-circuit. All environments' callback URLs are registered together so the
    single stored ``client_id`` works across the shared config DB.
    """
    from sqlalchemy.orm.attributes import flag_modified

    oauth = dict(spec.oauth_config or {})
    if oauth.get("client_id"):
        return oauth["client_id"]

    registration_url = oauth.get("registration_url", "")
    if not registration_url:
        raise ValueError("OAuth config missing registration_url")

    body = {
        "client_name": oauth.get("client_name", "Norm"),
        "redirect_uris": redirect_uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": oauth.get("token_endpoint_auth_method", "none"),
        "application_type": "web",
    }
    if oauth.get("scopes"):
        body["scope"] = oauth["scopes"]

    resp = httpx.post(
        registration_url,
        json=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        # 30s, not 15: Loaded's token endpoint has slow evenings — 16 Aug 2026
        # keep-alives lost two venues' redemptions to 15s read timeouts.
        timeout=30.0,
    )
    if resp.status_code not in (200, 201):
        logger.error(
            "OAuth dynamic registration failed: %s %s",
            resp.status_code,
            resp.text[:300],
        )
        raise ValueError(
            f"Client registration failed ({resp.status_code}): {resp.text[:200]}"
        )

    data = resp.json()
    client_id = data.get("client_id")
    if not client_id:
        raise ValueError("Registration response missing client_id")

    oauth["client_id"] = client_id
    # Persist anything else the server returned (a secret/registration token even
    # for a "none" client, etc.) so we don't lose it.
    for k in (
        "client_secret",
        "registration_access_token",
        "registration_client_uri",
        "client_id_issued_at",
    ):
        if data.get(k):
            oauth[k] = data[k]
    spec.oauth_config = oauth
    flag_modified(spec, "oauth_config")
    config_db.commit()
    logger.info("Registered OAuth client for %s (client_id set)", spec.connector_name)
    return client_id


def build_authorize_url(
    spec: ConnectionSpec,
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

    # PKCE (OAuth 2.1 public clients): generate a verifier now, persist it with
    # the state so it survives the redirect, and send only the challenge.
    use_pkce = _uses_pkce(oauth)
    code_verifier = None
    code_challenge = None
    if use_pkce:
        code_verifier, code_challenge = _generate_pkce()

    # Persist state (+ verifier) for verification on callback
    oauth_state = OAuthState(
        connector_name=spec.connector_name,
        state=state,
        venue_id=venue_id,
        user_id=user_id,
        code_verifier=code_verifier,
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
    if use_pkce and code_challenge:
        query["code_challenge"] = code_challenge
        query["code_challenge_method"] = "S256"

    # Google-specific: request offline access for refresh tokens
    if "accounts.google.com" in authorize_url:
        query["access_type"] = "offline"
        query["prompt"] = "consent"

    return authorize_url + "?" + urlencode(query)


def exchange_code(
    spec: ConnectionSpec,
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
    # Expire stale states. The state is single-use and unguessable, but now that
    # any manager (not just a platform admin) can start a flow, a bounded
    # lifetime keeps a leaked or abandoned state from being redeemable forever.
    if oauth_state.created_at:
        created = oauth_state.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - created > timedelta(
            minutes=OAUTH_STATE_TTL_MIN
        ):
            db.delete(oauth_state)
            db.commit()
            raise ValueError(
                "OAuth state has expired — please start the connection again"
            )
    # Capture the PKCE verifier before the state row is deleted — it must be
    # replayed on the token exchange.
    code_verifier = oauth_state.code_verifier
    db.delete(oauth_state)

    oauth = spec.oauth_config or {}
    token_url = oauth.get("token_url", "")
    client_id = oauth.get("client_id", "")
    client_secret = oauth.get("client_secret", "")

    if not token_url:
        raise ValueError("OAuth config missing token_url")

    # Public clients (PKCE, no secret) omit client_secret and send code_verifier;
    # confidential clients (LoadedHub, Google) send the secret as before.
    token_body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
    }
    if client_secret:
        token_body["client_secret"] = client_secret
    if code_verifier:
        token_body["code_verifier"] = code_verifier

    # Exchange code for tokens
    resp = httpx.post(
        token_url,
        data=token_body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        # 30s, not 15: Loaded's token endpoint has slow evenings — 16 Aug 2026
        # keep-alives lost two venues' redemptions to 15s read timeouts.
        timeout=30.0,
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


def _mark_needs_reconnect(config_row: Connection, error: str) -> None:
    """Flag a connection as broken. Caller commits.

    Set only when the provider *rejects* a refresh (a 4xx on the token
    endpoint) — a dead or revoked refresh token that a human must re-authorize.
    Transient failures (network, timeout) raise before this point and leave the
    flag untouched, so a blip is not mistaken for "reconnect me".
    """
    config_row.needs_reconnect = True
    config_row.last_auth_error = (error or "")[:1000]
    config_row.last_auth_checked_at = datetime.now(timezone.utc)


def refresh_access_token(
    spec: ConnectionSpec,
    db: Session,
    venue_id: str | None = None,
    user_id: str | None = None,
    force: bool = False,
) -> str:
    """Redeem the refresh token for new tokens. Returns the new access_token.

    ``force=True`` performs the redemption even while the stored access token is
    still valid. This is what the scheduled keep-alive uses, and it is the whole
    point of the schedule: LoadedHub's access tokens live ~14 days but the
    REFRESH tokens for this client live only ~24 hours (proven Aug-2026: a
    fresh, never-revoked grant was already "invalid or expired" 26.4h after
    mint), so a lazy refresh that waits for access-token expiry is ~13 days too
    late — and production history held zero successful redemptions ever. Every
    successful redemption makes Loaded mint a brand-new full-lifetime refresh
    token (their near-expiry branch always fires for short-lived grants), so the
    chain stays alive as long as the redemption cadence beats the ~24h lifetime.

    Concurrency: providers such as LoadedHub *rotate* refresh tokens — each
    successful redemption returns a new refresh token and invalidates the
    previous one. Two concurrent redemptions would race, and the loser's token
    would be dead. So we take a row lock on the Connection for the duration
    of the exchange; non-forced callers re-check expiry under the lock and reuse
    a token another caller just refreshed. (Same shape as the ``with_for_update``
    claim in ``task_scheduler._claim_due_tasks``.)
    """
    query = db.query(Connection).filter(
        Connection.connector_name == spec.connector_name
    )
    if user_id:
        query = query.filter(Connection.user_id == user_id)
    elif venue_id:
        query = query.filter(Connection.venue_id == venue_id)
    else:
        # No scope given must mean the GLOBAL row — never an arbitrary venue's.
        # Without this filter, a None/None call locked whichever row came back
        # first and SPENT that venue's rotating refresh token, while
        # _store_tokens (which does filter venue IS NULL) filed the new tokens
        # on the global row — silently killing the victim venue's connection.
        query = query.filter(Connection.venue_id.is_(None))
    config_row = query.with_for_update().first()
    if not config_row or not config_row.refresh_token:
        raise ValueError("No refresh token available")

    # Re-check under the lock — another caller may have just refreshed. A forced
    # redemption (the keep-alive) skips this on purpose: its job is to redeem
    # while everything still looks valid, because waiting for expiry is too late.
    if not force and config_row.access_token and config_row.token_expires_at:
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
    }
    # Confidential clients authenticate the refresh with their secret; public
    # clients (token_endpoint_auth_method=none, e.g. the MCP connectors) have
    # none and rely on refresh-token rotation instead.
    if client_secret:
        refresh_body["client_secret"] = client_secret
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
        # 30s, not 15: Loaded's token endpoint has slow evenings — 16 Aug 2026
        # keep-alives lost two venues' redemptions to 15s read timeouts.
        timeout=30.0,
    )

    if resp.status_code != 200:
        logger.error(
            "OAuth token refresh failed: %s %s", resp.status_code, resp.text[:300]
        )
        # Record the broken state on the (locked) row so the UI and the
        # in-conversation reconnect card can see it without anyone having to
        # attempt a fetch first. Commit releases the with_for_update lock.
        _mark_needs_reconnect(
            config_row, f"Token refresh failed ({resp.status_code}): {resp.text[:200]}"
        )
        db.commit()
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
    spec: ConnectionSpec,
    db: Session,
    venue_id: str | None = None,
    user_id: str | None = None,
) -> str:
    """Get a valid access token, refreshing if expired."""
    query = db.query(Connection).filter(
        Connection.connector_name == spec.connector_name
    )
    if user_id:
        query = query.filter(Connection.user_id == user_id)
    elif venue_id:
        query = query.filter(Connection.venue_id == venue_id)
    else:
        # Unscoped lookup means the GLOBAL row, matching _store_tokens — never
        # an arbitrary venue's token (see refresh_access_token).
        query = query.filter(Connection.venue_id.is_(None))
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
    """Redeem every OAuth connector's refresh token for fresh tokens. Summary out.

    This must perform a REAL redemption on every run — never a lazy
    is-the-access-token-still-valid check. LoadedHub's access tokens live ~14
    days but its refresh tokens for this client live only ~24 HOURS, so any
    strategy that waits for access-token expiry redeems the refresh token ~13
    days after it died. That was the Aug-2026 failure mode: this function
    called ``get_valid_access_token`` (a no-op while the access token was
    valid), so production made one real token call per 14 days — always a 400 —
    and every venue's connection collapsed on a 14-day cycle behind
    ``refreshed=N`` no-op summaries. Each real redemption makes Loaded mint a
    fresh full-lifetime refresh token, so the chain survives indefinitely as
    long as the scheduler cadence stays comfortably under the ~24h refresh
    lifetime. The "issued a rotated refresh token" INFO log firing on each run
    is the operational proof of life.

    Per-connector failures are logged and collected rather than aborting the
    run, so one dead connector can't stop the others from being kept alive.
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
            db.query(Connection)
            .filter(
                Connection.refresh_token.isnot(None),
                Connection.enabled == "true",
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
                config_db.query(ConnectionSpec)
                .filter(ConnectionSpec.connector_name == row.connector_name)
                .first()
            )
            if not spec or spec.auth_type != "oauth2" or not spec.oauth_config:
                skipped.append(label)
                continue

            try:
                # Always a real redemption (force=True): a lazy check would skip
                # rows whose access token still looks valid — and with Loaded's
                # paired ~14-day lifetimes, "still valid" means "the refresh
                # token dies at the same moment you finally use it".
                refresh_access_token(
                    spec, db, venue_id=row.venue_id, user_id=row.user_id, force=True
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
    """Store token response in Connection."""
    query = db.query(Connection).filter(Connection.connector_name == connector_name)
    if user_id:
        query = query.filter(Connection.user_id == user_id)
    elif venue_id:
        query = query.filter(Connection.venue_id == venue_id)
    else:
        query = query.filter(Connection.venue_id.is_(None))
    config_row = query.first()

    if not config_row:
        config_row = Connection(
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

    # Store extra metadata (venue_id, venue_name, etc.). MERGE, don't replace:
    # refresh-grant responses are thinner than the original code exchange —
    # LoadedHub's refresh response has no VenueId/VenueName — and a replace
    # would wipe the stored venue binding that the connect card's
    # "connected as X / wrong company" display depends on (and Gmail/Outlook's
    # stored "email") on the first keep-alive redemption after a connect.
    known_keys = {"access_token", "refresh_token", "expires_in", "token_type", "scope"}
    extra = {k: v for k, v in token_data.items() if k not in known_keys}
    if extra:
        merged = dict(config_row.oauth_metadata or {})
        merged.update(extra)
        config_row.oauth_metadata = merged

    # A fresh token means the connection is healthy again — clear any prior
    # reconnect flag so the UI stops nagging.
    config_row.needs_reconnect = False
    config_row.last_auth_error = None
    config_row.last_auth_checked_at = datetime.now(timezone.utc)

    db.commit()
