"""OAuth 2.0 endpoints for connector authentication.

Provides:
- GET  /oauth/authorize/{connector} — start the OAuth flow (returns redirect URL)
- GET  /oauth/callback               — handle provider redirect, exchange code for tokens
- GET  /oauth/status/{connector}     — check if connector has valid OAuth tokens
- POST /oauth/disconnect/{connector} — remove stored OAuth tokens
"""

import logging

from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.engine import get_db, get_config_db
from app.db.models import ConnectorSpec, ConnectorConfig, User
from app.auth.dependencies import get_current_user, require_role
from app.services.oauth_service import build_authorize_url, exchange_code

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_redirect_uri(request: Request) -> str:
    """Build the OAuth callback URL based on the current request or env config."""
    from app.config import settings

    configured = settings.OAUTH_REDIRECT_URI or None
    if configured:
        return configured
    # Derive from request
    return str(request.base_url).rstrip("/") + "/api/oauth/callback"


@router.get("/oauth/authorize/{connector}")
async def oauth_authorize(
    connector: str,
    request: Request,
    venue_id: str | None = None,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(require_role("admin")),
):
    """Start the OAuth flow. Returns the authorization URL to redirect the user to."""
    spec = (
        config_db.query(ConnectorSpec)
        .filter(
            ConnectorSpec.connector_name == connector,
            ConnectorSpec.auth_type == "oauth2",
        )
        .first()
    )
    if not spec:
        raise HTTPException(404, f"No OAuth2 connector spec found: {connector}")

    if not spec.oauth_config:
        raise HTTPException(400, f"Connector {connector} has no OAuth configuration")

    redirect_uri = _get_redirect_uri(request)
    # For email connectors, scope tokens to the current user
    user_id = user.id if spec.category == "email" else None
    try:
        authorize_url = build_authorize_url(
            spec,
            redirect_uri,
            db,
            venue_id=venue_id,
            user_id=user_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"authorize_url": authorize_url}


@router.get("/oauth/callback")
async def oauth_callback(
    code: str,
    state: str,
    request: Request,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    """Handle the OAuth provider redirect. Exchanges code for tokens.

    This endpoint is called by the OAuth provider, not directly by the user.
    No auth required since the provider can't send our JWT.
    We validate via the state parameter instead.
    """
    from app.db.models import OAuthState

    # Look up the state to find which connector this is for
    oauth_state = db.query(OAuthState).filter(OAuthState.state == state).first()
    if not oauth_state:
        raise HTTPException(400, "Invalid or expired OAuth state")

    connector_name = oauth_state.connector_name
    oauth_state_user_id = oauth_state.user_id
    spec = (
        config_db.query(ConnectorSpec)
        .filter(ConnectorSpec.connector_name == connector_name)
        .first()
    )
    if not spec:
        raise HTTPException(400, f"Connector spec not found: {connector_name}")

    redirect_uri = _get_redirect_uri(request)

    try:
        token_data = exchange_code(spec, code, state, redirect_uri, db)
    except ValueError as exc:
        return HTMLResponse(
            content=_result_page(
                success=False, connector=connector_name, message=str(exc)
            ),
            status_code=400,
        )

    # For LoadedHub, the OAuth response identifies which venue/company the
    # tokens are for. Move tokens from the null-venue config (where exchange_code
    # stored them) to the matching Norm venue based on x_loaded_company_id.
    if connector_name == "loadedhub" and token_data.get("access_token"):
        try:
            # LoadedHub returns the venue identifier in the token response as
            # ``venue_id`` (and a display name as ``venue_name``). We also try
            # a few alternate keys as defensive fallbacks.
            company_id = None
            for key in (
                "venue_id",
                "company_id",
                "companyId",
                "x-loaded-company-id",
                "tenant_id",
                "tenantId",
            ):
                if key in token_data and token_data[key]:
                    company_id = str(token_data[key])
                    break
            # Also check oauth_metadata of the just-stored (null-venue) config row
            if not company_id:
                null_cfg = (
                    db.query(ConnectorConfig)
                    .filter(
                        ConnectorConfig.connector_name == connector_name,
                        ConnectorConfig.venue_id.is_(None),
                        ConnectorConfig.user_id.is_(None),
                    )
                    .first()
                )
                if null_cfg and null_cfg.oauth_metadata:
                    for key in (
                        "venue_id",
                        "company_id",
                        "companyId",
                        "x-loaded-company-id",
                        "tenant_id",
                        "tenantId",
                    ):
                        if (
                            key in null_cfg.oauth_metadata
                            and null_cfg.oauth_metadata[key]
                        ):
                            company_id = str(null_cfg.oauth_metadata[key])
                            break

            if company_id:
                # Find the Norm venue whose ConnectorConfig has this x_loaded_company_id
                target_cfg = None
                for cfg in (
                    db.query(ConnectorConfig)
                    .filter(
                        ConnectorConfig.connector_name == connector_name,
                        ConnectorConfig.venue_id.isnot(None),
                    )
                    .all()
                ):
                    cfg_company = (cfg.config or {}).get("x_loaded_company_id")
                    if str(cfg_company) == company_id:
                        target_cfg = cfg
                        break

                # Pull the just-stored tokens from the null-venue row
                src_cfg = (
                    db.query(ConnectorConfig)
                    .filter(
                        ConnectorConfig.connector_name == connector_name,
                        ConnectorConfig.venue_id.is_(None),
                        ConnectorConfig.user_id.is_(None),
                    )
                    .first()
                )

                if target_cfg and src_cfg and src_cfg.access_token:
                    target_cfg.access_token = src_cfg.access_token
                    target_cfg.refresh_token = src_cfg.refresh_token
                    target_cfg.token_expires_at = src_cfg.token_expires_at
                    target_cfg.oauth_metadata = src_cfg.oauth_metadata
                    # Clear the null-venue row to avoid stale duplicates
                    src_cfg.access_token = None
                    src_cfg.refresh_token = None
                    src_cfg.token_expires_at = None
                    src_cfg.oauth_metadata = None
                    db.commit()
                    token_data["venue_id"] = company_id
                    token_data["matched_venue_id"] = target_cfg.venue_id
                else:
                    token_data["venue_id"] = company_id
                    token_data["match_warning"] = (
                        f"No Norm venue found with x_loaded_company_id={company_id}"
                    )
            else:
                token_data["match_warning"] = (
                    "LoadedHub did not return a company identifier in the token response"
                )
        except Exception as exc:
            logger.warning("LoadedHub venue mapping failed: %s", exc)

    # For Google connectors, fetch user email from userinfo endpoint
    if connector_name == "gmail" and token_data.get("access_token"):
        try:
            import httpx

            resp = httpx.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
                timeout=10.0,
            )
            if resp.status_code == 200:
                userinfo = resp.json()
                email = userinfo.get("email")
                if email:
                    # Update the ConnectorConfig oauth_metadata with the email
                    from sqlalchemy.orm.attributes import flag_modified

                    config_row = (
                        db.query(ConnectorConfig)
                        .filter(
                            ConnectorConfig.connector_name == connector_name,
                            ConnectorConfig.user_id == oauth_state_user_id,
                        )
                        .first()
                    )
                    if config_row:
                        meta = config_row.oauth_metadata or {}
                        meta["email"] = email
                        config_row.oauth_metadata = meta
                        flag_modified(config_row, "oauth_metadata")
                        db.commit()
                    token_data["email"] = email
        except Exception:
            pass  # Non-critical — email display is nice-to-have

    # Build a success message including any extra metadata
    meta_parts = []
    known_keys = {"access_token", "refresh_token", "expires_in", "token_type", "scope"}
    for k, v in token_data.items():
        if k not in known_keys and v:
            meta_parts.append(f"{k}: {v}")
    meta_msg = (" | " + ", ".join(meta_parts)) if meta_parts else ""

    return HTMLResponse(
        content=_result_page(
            success=True,
            connector=spec.display_name,
            message=f"Connected successfully{meta_msg}",
        ),
    )


@router.get("/oauth/status/{connector}")
async def oauth_status(
    connector: str,
    venue_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Check whether a connector has valid OAuth tokens.

    If venue_id is provided, returns status for that specific venue.
    Otherwise returns status for the first matching config (legacy behaviour).
    """
    query = db.query(ConnectorConfig).filter(
        ConnectorConfig.connector_name == connector
    )
    if venue_id:
        query = query.filter(ConnectorConfig.venue_id == venue_id)
    config_row = query.first()

    if not config_row or not config_row.access_token:
        return {"connected": False}

    from datetime import datetime, timezone

    expired = False
    if config_row.token_expires_at:
        expired = datetime.now(timezone.utc) >= config_row.token_expires_at

    return {
        "connected": True,
        "expired": expired,
        "has_refresh_token": bool(config_row.refresh_token),
        "expires_at": config_row.token_expires_at.isoformat()
        if config_row.token_expires_at
        else None,
        "metadata": config_row.oauth_metadata,
    }


@router.get("/oauth/venues/{connector}")
async def oauth_venues_status(
    connector: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(require_role("admin")),
):
    """List all venues with their OAuth connection status for this connector."""
    from app.db.models import Venue

    spec = (
        config_db.query(ConnectorSpec)
        .filter(ConnectorSpec.connector_name == connector)
        .first()
    )
    if not spec:
        raise HTTPException(404, f"Connector spec not found: {connector}")

    venues = db.query(Venue).order_by(Venue.name).all()
    configs = (
        db.query(ConnectorConfig)
        .filter(ConnectorConfig.connector_name == connector)
        .all()
    )
    config_by_venue = {c.venue_id: c for c in configs if c.venue_id}

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    rows = []
    for v in venues:
        c = config_by_venue.get(v.id)
        connected = bool(c and c.access_token)
        expired = bool(
            c and c.access_token and c.token_expires_at and now >= c.token_expires_at
        )
        rows.append(
            {
                "venue_id": v.id,
                "venue_name": v.name,
                "connected": connected,
                "expired": expired,
                "has_refresh_token": bool(c and c.refresh_token),
                "expires_at": c.token_expires_at.isoformat()
                if c and c.token_expires_at
                else None,
            }
        )

    return {"venues": rows}


@router.post("/oauth/disconnect/{connector}")
async def oauth_disconnect(
    connector: str,
    venue_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    """Remove stored OAuth tokens for a connector (optionally scoped to a venue)."""
    query = db.query(ConnectorConfig).filter(
        ConnectorConfig.connector_name == connector
    )
    if venue_id:
        query = query.filter(ConnectorConfig.venue_id == venue_id)
    config_row = query.first()
    if not config_row:
        raise HTTPException(404, f"No config for connector: {connector}")

    config_row.access_token = None
    config_row.refresh_token = None
    config_row.token_expires_at = None
    config_row.oauth_metadata = None
    db.commit()
    return {"disconnected": True}


def _result_page(success: bool, connector: str, message: str) -> str:
    """Generate a simple HTML page shown after the OAuth callback."""
    color = "#38a169" if success else "#e53e3e"
    icon = "&#10003;" if success else "&#10007;"
    return f"""<!DOCTYPE html>
<html>
<head><title>OAuth - {connector}</title></head>
<body style="font-family: -apple-system, system-ui, sans-serif; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; background: #f7f7f7;">
  <div style="text-align: center; padding: 2rem; background: white; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); max-width: 400px;">
    <div style="font-size: 3rem; color: {color}; margin-bottom: 1rem;">{icon}</div>
    <h2 style="margin: 0 0 0.5rem; font-size: 1.1rem;">{connector}</h2>
    <p style="color: #555; font-size: 0.9rem; margin: 0 0 1.5rem;">{message}</p>
    <p style="color: #999; font-size: 0.8rem;">You can close this window and return to Norm.</p>
    <script>
      // Notify the opener window that OAuth is complete
      if (window.opener) {{
        window.opener.postMessage({{ type: 'oauth-complete', connector: '{connector}', success: {"true" if success else "false"} }}, '*');
      }}
    </script>
  </div>
</body>
</html>"""
