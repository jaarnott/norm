"""OAuth discovery metadata — RFC 8414 and RFC 9728.

Mounted at the **domain root** (no /api prefix), because the well-known
documents must resolve from the host root or clients won't find them. This is
the same prefix-less exception as health and internal.

claude.ai fetches these to discover where and how to authenticate, so the
`issuer` here must exactly match the URL prefix the document was fetched from
(RFC 8414 §3.3). We derive it from ``settings.mcp_issuer`` rather than
``request.base_url`` — behind nginx the latter reports the internal host, and a
mismatch makes clients reject the metadata outright.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.config import settings
from app.mcp.scopes import MCP_SCOPES

router = APIRouter(tags=["oauth-metadata"])

_CACHE_HEADERS = {"Cache-Control": "public, max-age=3600"}


def _issuer() -> str:
    return settings.mcp_issuer


def _protected_resource_metadata() -> dict:
    return {
        "resource": settings.mcp_resource_url,
        "authorization_servers": [_issuer()],
        "scopes_supported": sorted(MCP_SCOPES),
        "bearer_methods_supported": ["header"],
    }


def _authorization_server_metadata() -> dict:
    issuer = _issuer()
    # The authorization endpoint is the Next.js consent page, not an API route:
    # the user's session lives in localStorage, which only the SPA can read.
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{settings.app_url}/mcp/authorize",
        "token_endpoint": f"{issuer}/api/mcp/oauth/token",
        "registration_endpoint": f"{issuer}/api/mcp/oauth/register",
        "revocation_endpoint": f"{issuer}/api/mcp/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],  # PKCE mandatory; no plain
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "scopes_supported": sorted(MCP_SCOPES),
        "resource_indicators_supported": True,
        "authorization_response_iss_parameter_supported": True,
    }


def _json(body: dict) -> Response:
    import json

    return Response(
        content=json.dumps(body),
        media_type="application/json",
        headers=_CACHE_HEADERS,
    )


# RFC 9728 — Protected Resource Metadata. Path-inserted variant is canonical;
# the root variant is a fallback. claude.ai probes both.
@router.get("/.well-known/oauth-protected-resource/mcp")
@router.get("/.well-known/oauth-protected-resource")
def protected_resource_metadata() -> Response:
    return _json(_protected_resource_metadata())


# RFC 8414 — Authorization Server Metadata. openid-configuration is probed by
# some clients and returns the same body.
@router.get("/.well-known/oauth-authorization-server/mcp")
@router.get("/.well-known/oauth-authorization-server")
@router.get("/.well-known/openid-configuration")
def authorization_server_metadata() -> Response:
    return _json(_authorization_server_metadata())
