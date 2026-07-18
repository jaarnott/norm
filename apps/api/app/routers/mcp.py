"""MCP server endpoint — ``POST /mcp``.

Mounted at the **domain root**, not under ``/api``, joining ``health`` and
``internal`` as the exceptions. Two reasons:

1. RFC 9728 requires the protected-resource metadata document to live at the
   host root under ``/.well-known/``. claude.ai takes one server URL and
   derives OAuth discovery from it; keeping the endpoint at ``/mcp`` makes the
   canonical resource identifier ``https://<host>/mcp`` — same root, one
   mental model.
2. This is not a Norm-app API. It is a separate protocol with a separate auth
   scheme (OAuth bearer, not the session JWT ``get_current_user`` validates).
   ``internal`` already set the precedent: different auth scheme, no prefix.

NOTE: nginx proxies ``/api/`` to the API and everything else to the web app,
so this endpoint needs its own ``location /mcp`` block or it will hit Next.js
and 404. See nginx/nginx.conf.
"""

from __future__ import annotations

import json
import logging

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.connectors.mcp_protocol import PARSE_ERROR, jsonrpc_error
from app.db.engine import get_config_db, get_db
from app.mcp.auth import McpAuthError, extract_bearer, resolve_principal
from app.mcp.execution import NormMcpContext
from app.mcp.ratelimit import enforce_call_limits
from app.mcp.server import handle_jsonrpc

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mcp"])

# Deliberately under mcp_executor.MCP_TIMEOUT (30s): when Norm is the client it
# allows 30s, so as a server it must give up first and return a usable error
# rather than have the caller time out on us.
MCP_SERVER_TOOL_TIMEOUT = 25


def _disabled() -> Response:
    return Response(status_code=404)


@router.post("/mcp")
async def mcp_endpoint(
    request: Request,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
) -> Response:
    """Handle one MCP JSON-RPC call."""
    if not settings.MCP_ENABLED:
        return _disabled()

    # Parse the body by hand rather than via a Pydantic model: FastAPI's 422 on
    # a malformed body is not a JSON-RPC envelope, and a client that can't parse
    # the error can't recover from it.
    raw = await request.body()
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _json_response(jsonrpc_error(None, PARSE_ERROR, "Parse error"))

    method = body.get("method") if isinstance(body, dict) else None
    params = body.get("params") if isinstance(body, dict) else None

    # Every MCP call is POST /mcp, so RequestTracingMiddleware's path-based
    # logging is useless for them without this. Bound here, the middleware's own
    # request_complete line inherits it for free.
    structlog.contextvars.bind_contextvars(
        mcp_method=method,
        mcp_tool=(params or {}).get("name") if method == "tools/call" else None,
    )

    try:
        principal = resolve_principal(
            extract_bearer(request.headers.get("authorization")), db
        )
    except McpAuthError as exc:
        # 401 with a challenge, NOT a JSON-RPC error: this is a transport-level
        # authentication failure, and the WWW-Authenticate header is how the
        # client discovers where to authenticate.
        return Response(
            status_code=401,
            headers={"WWW-Authenticate": exc.www_authenticate()},
            media_type="application/json",
            content=json.dumps({"error": exc.error, "error_description": exc.message}),
        )

    structlog.contextvars.bind_contextvars(
        mcp_client=principal.client_id,
        mcp_user_id=principal.user_id,
    )

    # Rate-limit the expensive path (a tools/call drives a connector or the LLM).
    # initialize/ping/tools-list are cheap and must stay responsive for the
    # handshake, so they're not counted.
    if method == "tools/call":
        try:
            enforce_call_limits(db, principal)
        except HTTPException as exc:
            return Response(
                status_code=exc.status_code,
                headers=exc.headers or {},
                media_type="application/json",
                content=json.dumps(
                    {"error": "rate_limited", "error_description": exc.detail}
                ),
            )

    ctx = NormMcpContext(principal, db, config_db)
    result = handle_jsonrpc(body, ctx)

    # Notifications get 202 and an empty body — there is no response to send.
    if result is None:
        return Response(status_code=202)

    return _json_response(result)


@router.get("/mcp")
@router.delete("/mcp")
async def mcp_unsupported() -> Response:
    """No SSE stream and no sessions in v1 — 405 so clients skip both cleanly."""
    if not settings.MCP_ENABLED:
        return _disabled()
    return Response(status_code=405, headers={"Allow": "POST"})


def _json_response(body: dict) -> Response:
    return Response(
        content=json.dumps(body, default=str),
        media_type="application/json",
        status_code=200,
    )
