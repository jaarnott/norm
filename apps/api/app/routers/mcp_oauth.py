"""OAuth 2.1 authorization server for MCP clients.

Endpoints (mounted under /api):
    POST /mcp/oauth/register          RFC 7591 dynamic client registration
    GET  /mcp/oauth/authorize         validate params, 302 -> Next.js consent
    GET  /mcp/oauth/consent-context   feeds the consent screen (Norm JWT)
    POST /mcp/oauth/consent           mint an authorization code (Norm JWT)
    POST /mcp/oauth/token             code->tokens, refresh->tokens
    POST /mcp/oauth/revoke            RFC 7009
    GET  /mcp/connections             list the user's grants (Norm JWT)
    DELETE /mcp/connections/{id}      revoke a grant (Norm JWT)

Paths are under /mcp/oauth/* to avoid colliding with the connector-OAuth client
at /api/oauth/* — different table, different router, no shared state.

The consent leg lands in the Next.js app, not here: the user's session lives in
localStorage (no auth cookie), so only the SPA can identify the browser user.
Server-rendering consent would mean inventing a cookie session, i.e. touching
shared auth — which this layer must not do.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.config import settings
from app.db.engine import get_db
from app.mcp import oauth_service as svc
from app.mcp import tokens as tok
from app.mcp.ratelimit import enforce_ip_limit
from app.mcp.scopes import MCP_SCOPES

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mcp-oauth"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _client_ip(request: Request) -> str:
    # Behind nginx/Cloud Run; X-Forwarded-For's first hop is the client.
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _oauth_error(error: str, description: str, status_code: int = 400) -> JSONResponse:
    """RFC 6749 §5.2 error shape — clients parse `error`, not FastAPI's detail."""
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "error_description": description},
        headers={"Cache-Control": "no-store"},
    )


# ── Dynamic client registration (RFC 7591) ───────────────────────────


class RegisterBody(BaseModel):
    client_name: str = Field(min_length=1, max_length=256)
    redirect_uris: list[str] = Field(min_length=1)
    grant_types: list[str] = ["authorization_code", "refresh_token"]
    response_types: list[str] = ["code"]
    token_endpoint_auth_method: str = "none"
    scope: str | None = None
    client_uri: str | None = None
    logo_uri: str | None = None


@router.post("/mcp/oauth/register")
def register_client(
    body: RegisterBody, request: Request, db: Session = Depends(get_db)
):
    """Register a client. Unauthenticated by spec — claude.ai has no prior
    credential. Abuse is bounded: registration alone grants zero access (a user
    must still consent), and the redirect allowlist prevents code exfiltration.
    """
    from app.db.mcp_models import McpClient

    # DCR is unauthenticated (no principal yet), so this must key by IP — but
    # claude.ai registers from Anthropic's shared egress, so ALL customers
    # connecting Claude share this bucket. Keep it generous: registration alone
    # grants zero access (a user must still consent) and redirect_uris are
    # allowlisted, so the blast radius of a junk registration is one DB row.
    # A tight cap here would throttle legitimate multi-tenant connector setups.
    enforce_ip_limit(db, _client_ip(request), "register", 300, 3600)

    for uri in body.redirect_uris:
        if not svc.redirect_uri_allowed(uri):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"redirect_uri not permitted: {uri}",
            )

    client_id = "mcpc_" + secrets.token_urlsafe(24)
    client_secret = None
    secret_hash = None
    if body.token_endpoint_auth_method != "none":
        client_secret = secrets.token_urlsafe(32)
        secret_hash = tok.hash_client_secret(client_secret)

    client = McpClient(
        client_id=client_id,
        client_secret_hash=secret_hash,
        client_name=body.client_name,
        client_uri=body.client_uri,
        logo_uri=body.logo_uri,
        redirect_uris=body.redirect_uris,
        grant_types=body.grant_types,
        response_types=body.response_types,
        token_endpoint_auth_method=body.token_endpoint_auth_method,
        scope=body.scope,
    )
    db.add(client)
    db.commit()

    logger.info(
        "mcp_client_registered",
        extra={"client_id": client_id, "client_name": body.client_name},
    )

    resp = {
        "client_id": client_id,
        "client_name": body.client_name,
        "redirect_uris": body.redirect_uris,
        "grant_types": body.grant_types,
        "response_types": body.response_types,
        "token_endpoint_auth_method": body.token_endpoint_auth_method,
    }
    if client_secret:
        resp["client_secret"] = client_secret
    return JSONResponse(status_code=201, content=resp)


# ── Authorization (RFC 6749 §4.1.1 + PKCE) ───────────────────────────


@router.get("/mcp/oauth/authorize")
def authorize(
    request: Request,
    response_type: str = "",
    client_id: str = "",
    redirect_uri: str = "",
    scope: str = "",
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "",
    resource: str = "",
    db: Session = Depends(get_db),
):
    """Validate the request, then 302 to the Next.js consent page.

    Validation order matters: nothing may redirect to `redirect_uri` until it
    has been proven to belong to the client. Before that, errors render as
    plain text (never a redirect — that's the open-redirect hole).
    """
    from fastapi.responses import PlainTextResponse, RedirectResponse

    from app.db.mcp_models import McpClient

    client = (
        db.query(McpClient)
        .filter(McpClient.client_id == client_id, McpClient.is_active == True)  # noqa: E712
        .first()
    )
    if not client:
        return PlainTextResponse("Unknown client_id", status_code=400)
    if not svc.client_redirect_matches(client, redirect_uri):
        # Never redirect to an unvalidated URI.
        return PlainTextResponse(
            "redirect_uri does not match registration", status_code=400
        )

    # From here, errors go back to the (validated) redirect_uri per §4.1.2.1.
    def _err(err: str):
        sep = "&" if "?" in redirect_uri else "?"
        loc = f"{redirect_uri}{sep}error={err}"
        if state:
            loc += f"&state={state}"
        return RedirectResponse(loc, status_code=302)

    if response_type != "code":
        return _err("unsupported_response_type")
    if not code_challenge or code_challenge_method != "S256":
        return _err("invalid_request")  # PKCE S256 mandatory; plain rejected
    requested = scope.split()
    if set(requested) - set(MCP_SCOPES):
        return _err("invalid_scope")
    if resource and resource != settings.mcp_resource_url:
        return _err("invalid_target")

    # Forward to the SPA consent page, which reads the user's session.
    from urllib.parse import urlencode

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
    }
    if resource:
        params["resource"] = resource
    return RedirectResponse(
        f"{settings.app_url}/mcp/authorize?{urlencode(params)}", status_code=302
    )


# ── Consent (browser-authenticated) ──────────────────────────────────


@router.get("/mcp/oauth/consent-context")
def consent_context(
    client_id: str,
    scope: str = "",
    redirect_uri: str = "",
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Everything the consent screen renders. Requires the user's Norm JWT."""
    from app.db.mcp_models import McpClient

    client = (
        db.query(McpClient)
        .filter(McpClient.client_id == client_id, McpClient.is_active == True)  # noqa: E712
        .first()
    )
    if not client:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown client")
    if not svc.client_redirect_matches(client, redirect_uri):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "redirect_uri mismatch")

    requested = [s for s in scope.split() if s in MCP_SCOPES]
    return {
        "client": {
            "client_id": client.client_id,
            "client_name": client.client_name,
            "client_uri": client.client_uri,
            "logo_uri": client.logo_uri,
        },
        "user": {"id": user.id, "email": user.email, "full_name": user.full_name},
        "organizations": svc.resolve_consent_context(user, db),
        "requested_scopes": [
            {
                "scope": s,
                "label": MCP_SCOPES[s].label,
                "description": MCP_SCOPES[s].description,
                "access_level": MCP_SCOPES[s].access_level,
            }
            for s in requested
        ],
    }


class ConsentBody(BaseModel):
    client_id: str
    redirect_uri: str
    scope: str
    state: str | None = None
    code_challenge: str
    code_challenge_method: str = "S256"
    resource: str | None = None
    organization_id: str
    venue_ids: list[str] = []
    approved_scopes: list[str] = []
    action: str  # "approve" | "deny"


@router.post("/mcp/oauth/consent")
def consent(
    body: ConsentBody,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Record consent and mint a single-use authorization code.

    Every field is attacker-controlled and re-validated server-side — the
    consent screen's client-side checks are UX, not security.
    """
    from app.db.mcp_models import McpAuthorizationCode, McpClient
    from app.mcp.ratelimit import check_rate_limit

    check_rate_limit(db, f"usr:{user.id}:consent", 10, 60)

    client = (
        db.query(McpClient)
        .filter(McpClient.client_id == body.client_id, McpClient.is_active == True)  # noqa: E712
        .first()
    )
    if not client or not svc.client_redirect_matches(client, body.redirect_uri):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Invalid client or redirect_uri"
        )

    sep = "&" if "?" in body.redirect_uri else "?"

    if body.action == "deny":
        loc = f"{body.redirect_uri}{sep}error=access_denied"
        if body.state:
            loc += f"&state={body.state}"
        return {"redirect_to": loc}

    if not set(body.approved_scopes) <= set(body.scope.split()):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Approved more than was requested"
        )

    try:
        venue_ids, scopes = svc.validate_and_downscope(
            user, body.organization_id, body.venue_ids, body.approved_scopes, db
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    grant = svc.upsert_grant(
        user, body.client_id, body.organization_id, venue_ids, scopes, db
    )

    code = tok.new_authorization_code()
    db.add(
        McpAuthorizationCode(
            code_hash=tok.hash_token(code),
            client_id=body.client_id,
            user_id=user.id,
            organization_id=body.organization_id,
            venue_ids=venue_ids,
            scopes=scopes,
            redirect_uri=body.redirect_uri,
            resource=body.resource,
            code_challenge=body.code_challenge,
            code_challenge_method="S256",
            expires_at=_now() + tok.AUTH_CODE_TTL,
        )
    )
    db.commit()

    _audit(
        db,
        user_id=user.id,
        client=client,
        organization_id=body.organization_id,
        capability="mcp.consent.granted",
        scopes=scopes,
        grant_id=grant.id,
    )

    loc = f"{body.redirect_uri}{sep}code={code}&iss={settings.mcp_issuer}"
    if body.state:
        loc += f"&state={body.state}"
    return {"redirect_to": loc}


# ── Token (RFC 6749 §4.1.3 + §6) ─────────────────────────────────────


@router.post("/mcp/oauth/token")
def token(
    request: Request,
    grant_type: str = Form(...),
    code: str = Form(None),
    redirect_uri: str = Form(None),
    client_id: str = Form(None),
    client_secret: str = Form(None),
    code_verifier: str = Form(None),
    refresh_token: str = Form(None),
    resource: str = Form(None),
    db: Session = Depends(get_db),
):
    # Also shared across tenants via claude.ai egress (see register). Generous
    # cap: brute-forcing a 256-bit code/token is infeasible regardless.
    enforce_ip_limit(db, _client_ip(request), "token", 600, 60)
    if grant_type == "authorization_code":
        return _token_from_code(
            db, code, redirect_uri, client_id, client_secret, code_verifier, resource
        )
    if grant_type == "refresh_token":
        return _token_from_refresh(db, refresh_token, client_id, client_secret)
    return _oauth_error(
        "unsupported_grant_type", f"Unsupported grant_type: {grant_type}"
    )


def _authenticate_client(db, client_id, client_secret):
    from app.db.mcp_models import McpClient

    client = (
        db.query(McpClient)
        .filter(McpClient.client_id == client_id, McpClient.is_active == True)  # noqa: E712
        .first()
    )
    if not client:
        return None
    if client.token_endpoint_auth_method == "none":
        return client  # public client; PKCE is the proof
    if not client_secret or not tok.verify_client_secret(
        client_secret, client.client_secret_hash or ""
    ):
        return None
    return client


def _token_from_code(
    db, code, redirect_uri, client_id, client_secret, code_verifier, resource
):
    from sqlalchemy import text

    from app.db.mcp_models import McpAuthorizationCode

    if not code or not client_id or not code_verifier:
        return _oauth_error(
            "invalid_request", "Missing code, client_id, or code_verifier"
        )

    client = _authenticate_client(db, client_id, client_secret)
    if not client:
        return _oauth_error("invalid_client", "Client authentication failed", 401)

    code_hash = tok.hash_token(code)

    # Atomic single-use claim — not read-then-write. On multi-instance Cloud
    # Run a check-then-set race lets one code mint two token pairs.
    claimed = db.execute(
        text(
            "UPDATE mcp_authorization_codes SET consumed_at = now() "
            "WHERE code_hash = :h AND consumed_at IS NULL AND expires_at > now() "
            "RETURNING id"
        ),
        {"h": code_hash},
    ).first()

    row = (
        db.query(McpAuthorizationCode)
        .filter(McpAuthorizationCode.code_hash == code_hash)
        .first()
    )

    if claimed is None:
        # Either it never existed, expired, or was already consumed. A consumed
        # code being replayed means it leaked — revoke the whole grant family.
        if row is not None and row.consumed_at is not None:
            _revoke_grant_family(db, row)
            db.commit()
        return _oauth_error("invalid_grant", "Authorization code is invalid or expired")

    if row.client_id != client_id:
        return _oauth_error("invalid_grant", "client_id mismatch")
    if row.redirect_uri != redirect_uri:
        return _oauth_error("invalid_grant", "redirect_uri mismatch")
    if not tok.verify_pkce(code_verifier, row.code_challenge):
        return _oauth_error("invalid_grant", "PKCE verification failed")
    if row.resource and resource and row.resource != resource:
        return _oauth_error("invalid_target", "resource mismatch")

    from app.db.mcp_models import McpGrant

    grant = (
        db.query(McpGrant)
        .filter(
            McpGrant.user_id == row.user_id,
            McpGrant.client_id == row.client_id,
            McpGrant.organization_id == row.organization_id,
        )
        .first()
    )
    if not grant or grant.revoked_at is not None:
        return _oauth_error("invalid_grant", "Grant no longer valid")

    audience = row.resource or settings.mcp_resource_url
    access, refresh = tok.mint_token_pair(
        db, grant=grant, client_id=client_id, audience=audience
    )
    db.commit()

    return _token_response(access, refresh, grant.scopes)


def _token_from_refresh(db, refresh_token, client_id, client_secret):
    from sqlalchemy import text

    from app.db.mcp_models import McpGrant

    if not refresh_token:
        return _oauth_error("invalid_request", "Missing refresh_token")

    client = _authenticate_client(db, client_id, client_secret)
    if not client:
        return _oauth_error("invalid_client", "Client authentication failed", 401)

    row = tok.resolve_refresh_token(db, refresh_token)
    if row is None or row.client_id != client_id:
        return _oauth_error("invalid_grant", "Invalid refresh token")

    # Reuse detection (RFC 9700 §4.14.2): an already-rotated token being
    # presented means it leaked — kill the entire family.
    if row.rotated_at is not None or row.revoked_at is not None:
        tok.revoke_grant_tokens(db, row.grant_id)
        db.commit()
        return _oauth_error("invalid_grant", "Refresh token reuse detected")
    if row.expires_at <= _now():
        return _oauth_error("invalid_grant", "Refresh token expired")

    grant = db.query(McpGrant).filter(McpGrant.id == row.grant_id).first()
    if not grant or grant.revoked_at is not None:
        return _oauth_error("invalid_grant", "Grant no longer valid")

    # Re-derive scopes/venues from the live grant, intersected with the old
    # token's and the role's current grant — never widen. This is where a
    # revoked venue or a demotion takes effect.
    from app.mcp.scopes import scopes_grantable_by
    from app.db.models import OrganizationMembership, Role

    membership = (
        db.query(OrganizationMembership)
        .filter(
            OrganizationMembership.user_id == grant.user_id,
            OrganizationMembership.organization_id == grant.organization_id,
        )
        .first()
    )
    if not membership:
        return _oauth_error("invalid_grant", "Membership revoked")
    role = db.query(Role).filter(Role.id == membership.role_id).first()
    role_perms = set(role.permissions or []) if role else set()
    grantable = scopes_grantable_by(role_perms)
    new_scopes = sorted(set(grant.scopes or []) & set(row.scopes or []) & grantable)
    if not new_scopes:
        return _oauth_error("invalid_grant", "Access revoked; re-consent required")
    grant.scopes = new_scopes

    # Atomic rotate with a claim check. The WHERE rotated_at IS NULL makes the
    # UPDATE itself race-safe, but we MUST check that it actually claimed the
    # row: two concurrent requests with the same refresh token both pass the
    # rotated_at-is-None read above, and without this check both would mint a
    # family (only one UPDATE matches, but both proceed). An empty RETURNING
    # means another request already rotated it — that's reuse, so kill the
    # family, exactly as the authorization-code path does.
    rotated = db.execute(
        text(
            "UPDATE mcp_tokens SET rotated_at = now(), revoked_at = now() "
            "WHERE id = :id AND rotated_at IS NULL RETURNING id"
        ),
        {"id": row.id},
    ).first()
    if rotated is None:
        tok.revoke_grant_tokens(db, row.grant_id)
        db.commit()
        return _oauth_error("invalid_grant", "Refresh token reuse detected")

    access, refresh = tok.mint_token_pair(
        db,
        grant=grant,
        client_id=client_id,
        audience=row.audience,
        parent_token_id=row.id,
    )
    db.commit()

    return _token_response(access, refresh, new_scopes)


def _token_response(access, refresh, scopes):
    return JSONResponse(
        content={
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": int(tok.ACCESS_TOKEN_TTL.total_seconds()),
            "refresh_token": refresh,
            "scope": " ".join(scopes),
        },
        headers={"Cache-Control": "no-store"},
    )


def _revoke_grant_family(db, code_row):
    tok.revoke_grant_tokens(
        db,
        _grant_id_for(
            db, code_row.user_id, code_row.client_id, code_row.organization_id
        ),
    )


def _grant_id_for(db, user_id, client_id, organization_id):
    from app.db.mcp_models import McpGrant

    g = (
        db.query(McpGrant)
        .filter(
            McpGrant.user_id == user_id,
            McpGrant.client_id == client_id,
            McpGrant.organization_id == organization_id,
        )
        .first()
    )
    return g.id if g else ""


# ── Revocation (RFC 7009) ────────────────────────────────────────────


@router.post("/mcp/oauth/revoke")
def revoke(
    token: str = Form(...),
    token_type_hint: str = Form(None),
    db: Session = Depends(get_db),
):
    """Revoke a token. Always 200 — a 404 would be a token-existence oracle."""
    row = tok_module_resolve(db, token)
    if row is not None:
        tok.revoke_grant_tokens(db, row.grant_id)
        db.commit()
    return JSONResponse(content={}, headers={"Cache-Control": "no-store"})


def tok_module_resolve(db, token: str):
    """Find a token row of either kind by hash, regardless of state."""
    from app.db.mcp_models import McpToken

    return (
        db.query(McpToken).filter(McpToken.token_hash == tok.hash_token(token)).first()
    )


# ── User-facing disconnect ───────────────────────────────────────────


@router.get("/mcp/connections")
def list_connections(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """The user's active MCP grants, for Settings → Connections."""
    from app.db.mcp_models import McpClient, McpGrant

    grants = (
        db.query(McpGrant)
        .filter(McpGrant.user_id == user.id, McpGrant.revoked_at.is_(None))
        .all()
    )
    out = []
    for g in grants:
        client = db.query(McpClient).filter(McpClient.client_id == g.client_id).first()
        out.append(
            {
                "grant_id": g.id,
                "client_name": client.client_name if client else g.client_id,
                "organization_id": g.organization_id,
                "venue_ids": g.venue_ids,
                "scopes": g.scopes,
                "created_at": g.created_at.isoformat() if g.created_at else None,
            }
        )
    return out


@router.delete("/mcp/connections/{grant_id}")
def revoke_connection(
    grant_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)
):
    """Disconnect a client. Immediate — opaque tokens mean the next request fails."""
    from app.db.mcp_models import McpGrant

    grant = (
        db.query(McpGrant)
        .filter(McpGrant.id == grant_id, McpGrant.user_id == user.id)
        .first()
    )
    if not grant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connection not found")

    grant.revoked_at = _now()
    tok.revoke_grant_tokens(db, grant.id)
    db.commit()
    return {"revoked": True}


# ── Audit helper (lifecycle events) ──────────────────────────────────


def _audit(db, *, user_id, client, organization_id, capability, scopes, grant_id=None):
    from app.db.mcp_models import McpAuditLog

    db.add(
        McpAuditLog(
            user_id=user_id,
            client_id=client.client_id,
            client_name=client.client_name,
            grant_id=grant_id,
            organization_id=organization_id,
            capability=capability,
            access_level="write",  # consent/revoke are state changes
            scopes_used=scopes,
            success=True,
        )
    )
    db.commit()
