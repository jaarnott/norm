"""MCP (Model Context Protocol) wire format — shared by client and server.

Norm sits on both ends of the MCP wire:

- As a **client**, ``mcp_executor`` calls out to remote MCP servers (Orbit
  Marketing, etc.) and parses their responses.
- As a **server**, ``routers.mcp`` answers JSON-RPC calls from external AI
  clients (Claude) on behalf of an authenticated Norm user.

The builders here are the exact inverse of ``mcp_executor._parse_mcp_response``.
Keeping them in one module is what lets a test assert the round trip::

    data, embeds, is_error = _parse_mcp_response(
        {"jsonrpc": "2.0", "id": 1, "result": tools_call_result(payload)}
    )
    assert data == payload

Deliberately NOT here:

- ``_parse_mcp_response`` itself. It encodes inbound conventions that are not
  ours to inherit — the embed URL arrives in ``resource.text`` rather than
  ``resource.uri``, and ``container_hint`` is scavenged from the data payload.
  Those are quirks of the servers Norm consumes; the server half must not
  adopt them by importing them.
- Anything httpx-bound, or any auth-header shaping. This module imports
  nothing from ``app``.
"""

from __future__ import annotations

import json
from typing import Any

# ── JSON-RPC 2.0 error codes (spec-defined) ──────────────────────────
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

JSONRPC_VERSION = "2.0"


# ── Envelope ─────────────────────────────────────────────────────────


def jsonrpc_request(
    method: str, params: dict | None = None, rpc_id: int = 1
) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 request body (outbound: Norm as client)."""
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": rpc_id,
        "method": method,
        "params": params or {},
    }


def jsonrpc_result(rpc_id: Any, result: Any) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 success response (inbound: Norm as server)."""
    return {"jsonrpc": JSONRPC_VERSION, "id": rpc_id, "result": result}


def jsonrpc_error(
    rpc_id: Any, code: int, message: str, data: Any = None
) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 error response.

    A JSON-RPC error means the *protocol* call failed — malformed request,
    unknown method, bad params. A tool that ran and failed is NOT this: that
    is a successful result carrying ``isError: true``. See tools_call_result.
    Conflating the two makes a recoverable tool failure look to the client
    like a broken server.
    """
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": rpc_id, "error": error}


# ── Content blocks ───────────────────────────────────────────────────


def text_content(payload: Any) -> dict[str, Any]:
    """A ``type: text`` content block carrying serialized JSON.

    Emitted as a single block: ``_parse_mcp_response`` merges every text item
    into one dict, so one block keeps the round trip exact.
    """
    if isinstance(payload, str):
        return {"type": "text", "text": payload}
    return {"type": "text", "text": json.dumps(payload, default=str)}


def resource_content(url: str, uri: str = "", mime_type: str = "text/html") -> dict:
    """A ``type: resource`` content block (an embeddable URL).

    The URL goes in ``resource.text`` rather than ``resource.uri`` — that is
    the convention Norm's own client reads, and the one the Orbit integration
    established. Documented here so both halves agree on one spelling.
    """
    return {
        "type": "resource",
        "resource": {"uri": uri or url, "mimeType": mime_type, "text": url},
    }


def tools_call_result(payload: Any, is_error: bool = False) -> dict[str, Any]:
    """Build a ``tools/call`` result.

    ``structuredContent`` is emitted for dict payloads without declaring an
    ``outputSchema``. The spec permits that; declaring one would oblige us to
    validate every payload against it, and connector responses are shaped by
    config (``response_transform``) — i.e. by rows no test can see. Don't sign
    a contract that config can break.
    """
    result: dict[str, Any] = {
        "content": [text_content(payload)],
        "isError": is_error,
    }
    if isinstance(payload, dict) and not is_error:
        result["structuredContent"] = payload
    return result


def error_result(message: str, code: str = "INTERNAL_ERROR") -> dict[str, Any]:
    """A ``tools/call`` result for a tool that ran and failed.

    Recoverable by the model — it can fix its arguments and retry. Error codes
    mirror the vocabulary Norm asks of the MCP servers it consumes.
    """
    return tools_call_result({"error": message, "code": code}, is_error=True)
