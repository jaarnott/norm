"""MCP JSON-RPC dispatch.

Transport-agnostic: takes a parsed JSON-RPC body, returns a response body (or
None for notifications). The HTTP concerns — status codes, auth challenge
headers, raw-body parsing — live in ``routers/mcp.py``.

## Protocol surface

Implemented: ``initialize``, ``notifications/initialized``, ``ping``,
``tools/list``, ``tools/call``.

Not implemented, deliberately: ``resources/*``, ``prompts/*``, SSE streaming,
``Mcp-Session-Id``, outbound notifications. We advertise only ``tools``, so a
conforming client never calls the rest.

Statelessness is a design commitment, not an omission: Norm runs on Cloud Run
with autoscaling, and session affinity across instances is a bug factory.
Everything needed to serve a call is in the bearer token.

Playbooks look superficially like MCP *prompts*, but prompts are user-invoked
slash commands, not model-invoked — curated playbook **tools** give the same
value without a second surface to secure.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from app.connectors.mcp_protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    jsonrpc_error,
    jsonrpc_result,
)
from app.mcp.instructions import SERVER_INSTRUCTIONS

logger = logging.getLogger(__name__)

# Newest first. We echo the client's version when we know it, else propose
# our latest and let the client decide — the spec wants a proposal, not an error.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26")
LATEST_PROTOCOL_VERSION = "2025-06-18"

SERVER_NAME = "norm"
SERVER_TITLE = "Norm"
SERVER_VERSION = "0.1.0"


class McpDispatchError(Exception):
    """A protocol-level failure — becomes a JSON-RPC error, not a tool result."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)


# ── Method handlers ──────────────────────────────────────────────────


def _handle_initialize(params: dict, ctx: "McpContext") -> dict:
    requested = params.get("protocolVersion", "")
    version = (
        requested
        if requested in SUPPORTED_PROTOCOL_VERSIONS
        else LATEST_PROTOCOL_VERSION
    )
    return {
        "protocolVersion": version,
        # Advertise only what we implement. A client that sees `resources` here
        # will call resources/list and get a method-not-found for its trouble.
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {
            "name": SERVER_NAME,
            "title": SERVER_TITLE,
            "version": SERVER_VERSION,
        },
        "instructions": SERVER_INSTRUCTIONS,
    }


def _handle_ping(params: dict, ctx: "McpContext") -> dict:
    return {}


def _handle_tools_list(params: dict, ctx: "McpContext") -> dict:
    """List the tools this principal may call.

    No pagination: the curated surface is small by construction. We accept and
    ignore `cursor` and never return `nextCursor`.

    Tools the principal lacks scope for are omitted entirely rather than
    listed-and-refused — a tool list is not a place to enumerate what someone
    can't have.
    """
    return {"tools": ctx.list_tools()}


def _handle_tools_call(params: dict, ctx: "McpContext") -> dict:
    name = params.get("name")
    if not name or not isinstance(name, str):
        raise McpDispatchError(INVALID_PARAMS, "params.name is required")
    # Note: `or {}` would silently coerce a falsy non-dict (e.g. []) into {},
    # defeating the type check below. Compare against None explicitly.
    arguments = params.get("arguments")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise McpDispatchError(INVALID_PARAMS, "params.arguments must be an object")
    return ctx.call_tool(name, arguments)


_HANDLERS: dict[str, Callable[[dict, "McpContext"], dict]] = {
    "initialize": _handle_initialize,
    "ping": _handle_ping,
    "tools/list": _handle_tools_list,
    "tools/call": _handle_tools_call,
}

# Notifications get no response body — the client isn't listening for one.
_NOTIFICATIONS = {"notifications/initialized", "notifications/cancelled"}


class McpContext:
    """What dispatch needs to serve a call.

    Phase 0 ships the null implementation (no tools). Phases 1+ subclass or
    replace ``list_tools`` / ``call_tool`` with the real projection and
    execution paths, so the dispatch layer never grows tool knowledge.
    """

    def __init__(self, principal: Any = None, db: Any = None, config_db: Any = None):
        self.principal = principal
        self.db = db
        self.config_db = config_db

    def list_tools(self) -> list[dict]:
        return []

    def call_tool(self, name: str, arguments: dict) -> dict:
        # Unknown tool and not-authorized-for-this-tool must be indistinguishable.
        # Otherwise the error message becomes an oracle for what exists.
        raise McpDispatchError(INVALID_PARAMS, f"Unknown tool: {name}")


# ── Entry point ──────────────────────────────────────────────────────


def handle_jsonrpc(body: Any, ctx: McpContext) -> dict | None:
    """Dispatch one JSON-RPC request. Returns None for notifications.

    Never raises: every failure becomes a JSON-RPC error response. A raised
    exception here would surface as a 500 with an HTML body, which no MCP
    client can parse.
    """
    if not isinstance(body, dict):
        return jsonrpc_error(None, INVALID_REQUEST, "Request must be a JSON object")

    rpc_id = body.get("id")
    method = body.get("method")

    if body.get("jsonrpc") != "2.0":
        return jsonrpc_error(rpc_id, INVALID_REQUEST, "jsonrpc must be '2.0'")
    if not method or not isinstance(method, str):
        return jsonrpc_error(rpc_id, INVALID_REQUEST, "method is required")

    if method in _NOTIFICATIONS:
        return None

    handler = _HANDLERS.get(method)
    if handler is None:
        return jsonrpc_error(rpc_id, METHOD_NOT_FOUND, f"Unknown method: {method}")

    params = body.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return jsonrpc_error(rpc_id, INVALID_PARAMS, "params must be an object")

    try:
        return jsonrpc_result(rpc_id, handler(params, ctx))
    except McpDispatchError as exc:
        return jsonrpc_error(rpc_id, exc.code, exc.message, exc.data)
    except Exception:
        # Log the detail, return none of it — an internal error message is not
        # a debugging channel for a third party.
        logger.exception("MCP dispatch failed", extra={"mcp_method": method})
        return jsonrpc_error(rpc_id, INTERNAL_ERROR, "internal error")
