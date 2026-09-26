"""MCP (Model Context Protocol) executor.

Handles JSON-RPC 2.0 communication with remote MCP servers:
- Tool discovery via ``tools/list``
- Tool execution via ``tools/call``
- Response parsing (text content + resource embeds)
- Schema conversion to ConnectionSpec.tools format
"""

import json
import logging
import time

import httpx

from app.connectors.base import ConnectorResult
from app.connectors.mcp_protocol import jsonrpc_request as _jsonrpc_request

logger = logging.getLogger(__name__)

MCP_TIMEOUT = 30  # seconds

__all__ = [
    "mcp_discover_tools",
    "mcp_call_tool",
    "convert_mcp_tools_to_spec",
]


def _build_auth_headers(
    credentials: dict,
    auth_type: str,
    auth_config: dict | None = None,
) -> dict:
    """Build auth headers for an MCP request."""
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    auth_config = auth_config or {}

    if auth_type == "oauth2":
        # OAuth 2.1 MCP connectors: the caller resolves a valid per-venue access
        # token (refreshing if needed) and passes it here as access_token.
        token = credentials.get("access_token", "")
        headers["Authorization"] = f"Bearer {token}"
    elif auth_type == "bearer":
        token_field = auth_config.get("token_field", "api_key")
        token = credentials.get(token_field, "")
        headers["Authorization"] = f"Bearer {token}"
    elif auth_type == "api_key_header":
        header_name = auth_config.get("header_name", "X-API-Key")
        key_field = auth_config.get("key_field", "api_key")
        headers[header_name] = credentials.get(key_field, "")

    return headers


def _parse_mcp_response(body: dict) -> tuple[dict, list[dict], bool]:
    """Parse an MCP JSON-RPC response.

    Returns (data_payload, embeds, is_error).
    - data_payload: merged JSON from all ``type: text`` content items
    - embeds: list of ``{url, uri, container_hint}`` from ``type: resource`` items
    - is_error: True if the MCP response signalled an error
    """
    # JSON-RPC error (protocol-level)
    if "error" in body:
        err = body["error"]
        return {"error": err.get("message", str(err))}, [], True

    result = body.get("result", body)
    content = result.get("content", [])
    is_error = bool(result.get("isError", False))

    data_payload: dict = {}
    embeds: list[dict] = []

    for item in content:
        item_type = item.get("type")

        if item_type == "text":
            text = item.get("text", "")
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    data_payload.update(parsed)
                else:
                    data_payload.setdefault("items", [])
                    if isinstance(parsed, list):
                        data_payload["items"].extend(parsed)
                    else:
                        data_payload["items"].append(parsed)
            except (json.JSONDecodeError, TypeError):
                data_payload["text"] = text

        elif item_type == "resource":
            resource = item.get("resource", {})
            embed_url = resource.get(
                "text", ""
            )  # URL is in the text field per Orbit spec
            if embed_url and embed_url.startswith("http"):
                # Extract container_hint from the data payload if available
                container_hint = data_payload.get("container_hint", "inline_card")
                embeds.append(
                    {
                        "url": embed_url,
                        "uri": resource.get("uri", ""),
                        "mime_type": resource.get("mimeType", "text/html"),
                        "container_hint": container_hint,
                    }
                )

    return data_payload, embeds, is_error


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def mcp_discover_tools(
    mcp_url: str,
    credentials: dict,
    auth_type: str = "bearer",
    auth_config: dict | None = None,
) -> list[dict]:
    """Call ``tools/list`` on an MCP server and return raw tool definitions."""
    headers = _build_auth_headers(credentials, auth_type, auth_config)
    body = _jsonrpc_request("tools/list")

    t0 = time.time()
    resp = httpx.post(mcp_url, json=body, headers=headers, timeout=MCP_TIMEOUT)
    duration_ms = int((time.time() - t0) * 1000)

    if resp.status_code == 401:
        raise ValueError("MCP authentication failed (401). Check the API key.")
    resp.raise_for_status()

    data = resp.json()
    result = data.get("result", data)
    tools = result.get("tools", [])

    logger.info("MCP tools/list %s → %d tools (%dms)", mcp_url, len(tools), duration_ms)
    return tools


def mcp_call_tool(
    mcp_url: str,
    tool_name: str,
    arguments: dict,
    credentials: dict,
    auth_type: str = "bearer",
    auth_config: dict | None = None,
) -> ConnectorResult:
    """Call ``tools/call`` on an MCP server and return a ConnectorResult."""
    headers = _build_auth_headers(credentials, auth_type, auth_config)
    body = _jsonrpc_request("tools/call", {"name": tool_name, "arguments": arguments})

    t0 = time.time()
    try:
        resp = httpx.post(mcp_url, json=body, headers=headers, timeout=MCP_TIMEOUT)
    except httpx.TimeoutException:
        return ConnectorResult(
            success=False,
            reference=None,
            response_payload={},
            error_message=f"MCP call to {tool_name} timed out after {MCP_TIMEOUT}s",
        )
    except httpx.RequestError as exc:
        return ConnectorResult(
            success=False,
            reference=None,
            response_payload={},
            error_message=f"MCP connection error: {exc}",
        )
    duration_ms = int((time.time() - t0) * 1000)

    if resp.status_code == 401:
        return ConnectorResult(
            success=False,
            reference=None,
            response_payload={},
            error_message="MCP authentication failed (401). Check the API key.",
        )

    if resp.status_code >= 400:
        return ConnectorResult(
            success=False,
            reference=None,
            response_payload={},
            error_message=f"MCP server returned HTTP {resp.status_code}",
        )

    try:
        resp_body = resp.json()
    except Exception:
        return ConnectorResult(
            success=False,
            reference=None,
            response_payload={},
            error_message="MCP response was not valid JSON",
        )

    data_payload, embeds, is_error = _parse_mcp_response(resp_body)

    # Attach embeds to payload so the tool loop can build display blocks
    if embeds:
        data_payload["_embed"] = embeds

    logger.info(
        "MCP tools/call %s.%s → %s (%dms, %d embeds)",
        mcp_url.split("/")[-1],
        tool_name,
        "error" if is_error else "ok",
        duration_ms,
        len(embeds),
    )

    error_msg = None
    if is_error:
        error_msg = data_payload.get("error", "MCP tool returned an error")
        if isinstance(error_msg, dict):
            error_msg = error_msg.get("message", str(error_msg))

    return ConnectorResult(
        success=not is_error,
        reference=None,
        response_payload=data_payload,
        error_message=str(error_msg) if error_msg else None,
    )


#: The parts of a property's JSON Schema that travel with the spec row.
_SCHEMA_KEYS = ("type", "enum", "items", "properties", "required", "minimum", "maximum")


def coerce_arguments_to_schema(fields: dict, operation: dict) -> dict:
    """Cast string arguments to the JSON type the row's field_schema declares.

    MCP servers validate argument TYPES: Orbit's zod schemas reject "true" for
    a boolean and "100" for a number (stock_find_stocktakes, 26 Sep 2026 —
    three failed calls before the agent gave up passing them). The model is
    shown `type: string` for any field without a field_schema, and older
    threads keep sending strings, so the cast happens here, on the way out.
    A value that cannot be cast is sent as-is; the server's own validation
    error says what is wrong.
    """
    schema = operation.get("field_schema") or {}
    out = dict(fields)
    for key, value in fields.items():
        prop = schema.get(key)
        if not isinstance(prop, dict) or not isinstance(value, str):
            continue
        typ = prop.get("type")
        text = value.strip()
        try:
            if typ == "boolean":
                if text.lower() in ("true", "1", "yes"):
                    out[key] = True
                elif text.lower() in ("false", "0", "no"):
                    out[key] = False
            elif typ == "integer":
                out[key] = int(float(text))
            elif typ == "number":
                number = float(text)
                out[key] = int(number) if number.is_integer() else number
            elif typ in ("array", "object"):
                if text.startswith(("[", "{")):
                    out[key] = json.loads(text)
                elif typ == "array":
                    out[key] = [s.strip() for s in text.split(",") if s.strip()]
        except (ValueError, json.JSONDecodeError):
            pass
    return out


def convert_mcp_tools_to_spec(mcp_tools: list[dict]) -> list[dict]:
    """Convert MCP tool definitions to ConnectionSpec.tools format.

    MCP tools have: name, description, inputSchema (JSON Schema object).
    ConnectionSpec tools need: action, method, description, required_fields,
    optional_fields, field_descriptions — and field_schema for any field that
    is not a plain string, so the agent is shown the real type (prompt_builder
    passes field_schema through verbatim) and coerce_arguments_to_schema can
    cast stragglers. Without it every field reads `type: string` and strict
    servers reject the call.
    """
    spec_tools: list[dict] = []

    for tool in mcp_tools:
        name = tool.get("name", "")
        description = tool.get("description", "")
        input_schema = tool.get("inputSchema", {})
        properties = input_schema.get("properties", {})
        required = input_schema.get("required", [])

        # Classify read vs write by name convention
        method = "GET" if name.startswith("get_") else "POST"

        required_fields = [f for f in required if f in properties]
        optional_fields = [f for f in properties if f not in required]

        field_descriptions: dict[str, str] = {}
        for field_name, prop in properties.items():
            desc_parts: list[str] = []
            if prop.get("description"):
                desc_parts.append(prop["description"])
            if prop.get("enum"):
                desc_parts.append(f"Options: {', '.join(str(e) for e in prop['enum'])}")
            field_descriptions[field_name] = " ".join(desc_parts) if desc_parts else ""

        field_schema: dict[str, dict] = {}
        for field_name, prop in properties.items():
            if not isinstance(prop, dict):
                continue
            typed = {k: prop[k] for k in _SCHEMA_KEYS if k in prop}
            if typed.get("type", "string") != "string" or "enum" in typed:
                field_schema[field_name] = typed

        row = {
            "action": name,
            "method": method,
            "description": description,
            "required_fields": required_fields,
            "optional_fields": optional_fields,
            "field_descriptions": field_descriptions,
        }
        if field_schema:
            row["field_schema"] = field_schema
        spec_tools.append(row)

    return spec_tools
