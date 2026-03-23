"""OAuth 2.0 endpoints for connector authentication.

Provides:
- GET  /oauth/authorize/{connector} — start the OAuth flow (returns redirect URL)
- GET  /oauth/callback               — handle provider redirect, exchange code for tokens
- GET  /oauth/status/{connector}     — check if connector has valid OAuth tokens
- POST /oauth/disconnect/{connector} — remove stored OAuth tokens
"""


from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models import ConnectorSpec, ConnectorConfig, User
from app.auth.dependencies import get_current_user, require_role
from app.services.oauth_service import build_authorize_url, exchange_code

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
    user: User = Depends(require_role("admin")),
):
    """Start the OAuth flow. Returns the authorization URL to redirect the user to."""
    spec = db.query(ConnectorSpec).filter(
        ConnectorSpec.connector_name == connector,
        ConnectorSpec.auth_type == "oauth2",
    ).first()
    if not spec:
        raise HTTPException(404, f"No OAuth2 connector spec found: {connector}")

    if not spec.oauth_config:
        raise HTTPException(400, f"Connector {connector} has no OAuth configuration")

    redirect_uri = _get_redirect_uri(request)
    try:
        authorize_url = build_authorize_url(spec, redirect_uri, db, venue_id=venue_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"authorize_url": authorize_url}


@router.get("/oauth/callback")
async def oauth_callback(
    code: str,
    state: str,
    request: Request,
    db: Session = Depends(get_db),
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
    spec = db.query(ConnectorSpec).filter(
        ConnectorSpec.connector_name == connector_name
    ).first()
    if not spec:
        raise HTTPException(400, f"Connector spec not found: {connector_name}")

    redirect_uri = _get_redirect_uri(request)

    try:
        token_data = exchange_code(spec, code, state, redirect_uri, db)
    except ValueError as exc:
        return HTMLResponse(
            content=_result_page(success=False, connector=connector_name, message=str(exc)),
            status_code=400,
        )

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
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Check whether a connector has valid OAuth tokens."""
    config_row = db.query(ConnectorConfig).filter(
        ConnectorConfig.connector_name == connector
    ).first()

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
        "metadata": config_row.oauth_metadata,
    }


@router.post("/oauth/disconnect/{connector}")
async def oauth_disconnect(
    connector: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    """Remove stored OAuth tokens for a connector."""
    config_row = db.query(ConnectorConfig).filter(
        ConnectorConfig.connector_name == connector
    ).first()
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
        window.opener.postMessage({{ type: 'oauth-complete', connector: '{connector}', success: {'true' if success else 'false'} }}, '*');
      }}
    </script>
  </div>
</body>
</html>"""
