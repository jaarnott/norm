"""MCP (Model Context Protocol) executor.

Handles JSON-RPC 2.0 communication with remote MCP servers:
- Tool discovery via ``tools/list``
- Tool execution via ``tools/call``
- Response parsing (text content + resource embeds)
- Schema conversion to ConnectorSpec.tools format
"""

import json
import logging
import time

import httpx

from app.connectors.base import ConnectorResult

logger = logging.getLogger(__name__)

MCP_TIMEOUT = 30  # seconds


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

    if auth_type == "bearer":
        token_field = auth_config.get("token_field", "api_key")
        token = credentials.get(token_field, "")
        headers["Authorization"] = f"Bearer {token}"
    elif auth_type == "api_key_header":
        header_name = auth_config.get("header_name", "X-API-Key")
        key_field = auth_config.get("key_field", "api_key")
        headers[header_name] = credentials.get(key_field, "")

    return headers


def _jsonrpc_request(method: str, params: dict | None = None, rpc_id: int = 1) -> dict:
    """Build a JSON-RPC 2.0 request body."""
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": method,
        "params": params or {},
    }


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


def convert_mcp_tools_to_spec(mcp_tools: list[dict]) -> list[dict]:
    """Convert MCP tool definitions to ConnectorSpec.tools format.

    MCP tools have: name, description, inputSchema (JSON Schema object).
    ConnectorSpec tools need: action, method, description, required_fields,
    optional_fields, field_descriptions.
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

        spec_tools.append(
            {
                "action": name,
                "method": method,
                "description": description,
                "required_fields": required_fields,
                "optional_fields": optional_fields,
                "field_descriptions": field_descriptions,
            }
        )

    return spec_tools
