"""Reusable connector tool execution — the same path the LLM tool loop uses.

Handles: spec lookup, field normalization, execute_spec, response_transform.
Used by both the LLM tool loop and the dashboard chart refresh.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    success: bool
    payload: Any  # transformed response data (list or dict)
    error: str | None = None
    rendered_request: dict | None = None  # {method, url, headers, body}
    row_count: int = 0
    logs: list[str] | None = None  # consolidator execution logs


def execute_connector_tool(
    connector_name: str,
    action: str,
    params: dict,
    db: Session,
    config_db: Session,
    venue_id: str | None = None,
    thread_id: str | None = None,
    strict_venue: bool = False,
) -> ToolResult:
    """Execute a connector tool end-to-end, matching the LLM tool loop path.

    Steps:
    1. Look up ConnectorSpec and tool definition
    2. Dispatch to a registered internal handler, or a consolidator, if either
       applies (mirrors tool_loop._execute_tool_call)
    3. Resolve venue-aware credentials
    4. Call execute_spec (which normalizes fields via _normalize_fields)
    5. Apply response_transform if configured
    6. Return clean result

    ``strict_venue`` controls the credential fallback when no config exists for
    ``venue_id``. False (default) keeps the historical loose behaviour — fall
    back to any enabled config. True restricts the fallback to venue-agnostic
    (platform) configs only, matching tool_loop._resolve_venue_config. Callers
    serving an authenticated, venue-scoped request must pass True: the loose
    fallback would otherwise answer a question about venue A using venue B's
    credentials.
    """
    from app.db.config_models import ConnectorSpec
    from app.db.models import Venue
    from app.connectors.spec_executor import execute_spec

    # 1. Look up connector spec
    spec = (
        config_db.query(ConnectorSpec)
        .filter(ConnectorSpec.connector_name == connector_name)
        .first()
    )
    if not spec:
        available = [
            s.connector_name
            for s in config_db.query(ConnectorSpec.connector_name).all()
        ]
        return ToolResult(
            success=False,
            payload=None,
            error=f"Connector not found: {connector_name}. Available: {', '.join(available)}",
        )

    # 2. Find matching tool definition
    tool_def = None
    for t in spec.tools or []:
        if t.get("action") == action:
            tool_def = t
            break
    if not tool_def:
        available_actions = [t.get("action") for t in (spec.tools or [])]
        return ToolResult(
            success=False,
            payload=None,
            error=f"Action not found: {action}. Available: {', '.join(str(a) for a in available_actions)}",
        )

    # 3. Dispatch to an in-process handler if one applies. Order mirrors
    #    tool_loop._execute_tool_call: a registered internal handler shadows a
    #    consolidator, and both shadow execute_spec. Without the get_handler
    #    lookup, every @register'd internal tool (resolve_dates,
    #    create_purchase_order, list_automated_tasks, ...) falls through to
    #    execute_spec and fails — this path only ever reached consolidators.
    from app.agents.internal_tools import get_handler

    handler = get_handler(connector_name, action)
    handler_kind = "INTERNAL"

    consolidator_config = tool_def.get("consolidator_config")
    if not handler and consolidator_config:
        from app.agents.internal_tools import execute_consolidator

        handler_kind = "CONSOLIDATOR"

        def handler(p, db_sess, tid):
            return execute_consolidator(consolidator_config, p, db_sess, tid)

    if handler:
        # Pass venue info through params so the handler can use it
        call_params = dict(params)
        if venue_id:
            v = db.query(Venue).filter(Venue.id == venue_id).first()
            if v and not call_params.get("venue"):
                call_params["venue"] = v.name

        try:
            handler_result = handler(call_params, db, thread_id)
        except Exception as exc:
            return ToolResult(success=False, payload=None, error=str(exc))

        payload = handler_result.get("data")
        payload = _apply_transform(tool_def, payload)
        logs = handler_result.get("_logs", [])
        return ToolResult(
            success=handler_result.get("success", True),
            payload=payload,
            error=handler_result.get("error"),
            rendered_request={
                "method": handler_kind,
                "url": f"{connector_name}/{action}",
            },
            row_count=(
                len(payload) if isinstance(payload, list) else (1 if payload else 0)
            ),
            logs=logs if logs else None,
        )

    # 3b. Resolve credentials for standard spec execution
    # Strip venue params (not API fields)
    clean_params = dict(params)
    clean_params.pop("venue", None)
    clean_params.pop("venue_name", None)
    clean_params.pop("venue_id", None)
    clean_params.pop("_all_venues", None)

    config_row = _resolve_credentials(
        connector_name, venue_id, db, strict_venue=strict_venue
    )
    credentials = config_row.config if config_row else {}
    resolved_venue_id = config_row.venue_id if config_row else venue_id

    # 4. Execute spec (includes _normalize_fields internally)
    try:
        result, rendered = execute_spec(
            spec,
            tool_def,
            clean_params,
            credentials,
            db,
            thread_id,
            venue_id=resolved_venue_id,
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            payload=None,
            error=str(exc),
        )

    rendered_dict = {
        "method": rendered.method,
        "url": rendered.url,
        "headers": {
            k: ("***" if k.lower() in ("authorization", "x-api-key") else v)
            for k, v in (rendered.headers or {}).items()
        },
        "body": rendered.body,
    }

    if not result.success:
        return ToolResult(
            success=False,
            payload=result.response_payload,
            error=result.error_message,
            rendered_request=rendered_dict,
        )

    # 5. Apply response_transform if configured
    # Resolve venue timezone for datetime field options (|tz, |dow)
    venue_tz_name = None
    if resolved_venue_id:
        venue_obj = db.query(Venue).filter(Venue.id == resolved_venue_id).first()
        if venue_obj and venue_obj.timezone:
            venue_tz_name = venue_obj.timezone

    payload = _apply_transform(tool_def, result.response_payload, venue_tz_name)

    row_count = len(payload) if isinstance(payload, list) else (1 if payload else 0)

    return ToolResult(
        success=True,
        payload=payload,
        error=None,
        rendered_request=rendered_dict,
        row_count=row_count,
    )


def get_tool_info(
    connector_name: str,
    action: str,
    config_db: Session,
) -> dict:
    """Return metadata about a tool: accepted params, field descriptions, etc."""
    from app.db.config_models import ConnectorSpec

    spec = (
        config_db.query(ConnectorSpec)
        .filter(ConnectorSpec.connector_name == connector_name)
        .first()
    )
    if not spec:
        available = [
            s.connector_name
            for s in config_db.query(ConnectorSpec.connector_name).all()
        ]
        return {
            "error": f"Connector not found: {connector_name}",
            "available_connectors": available,
        }

    tool_def = None
    for t in spec.tools or []:
        if t.get("action") == action:
            tool_def = t
            break
    if not tool_def:
        return {
            "error": f"Action not found: {action}",
            "available_actions": [t.get("action") for t in (spec.tools or [])],
        }

    accepted_params = []
    for field in tool_def.get("required_fields", []):
        accepted_params.append(
            {
                "name": field,
                "required": True,
                "description": (tool_def.get("field_descriptions") or {}).get(
                    field, ""
                ),
            }
        )
    for field, desc in (tool_def.get("field_descriptions") or {}).items():
        if field not in [p["name"] for p in accepted_params]:
            accepted_params.append(
                {"name": field, "required": False, "description": desc}
            )

    return {
        "accepted_params": accepted_params,
        "is_consolidator": bool(tool_def.get("consolidator_config")),
    }


def list_connector_tools(connector_name: str, config_db: Session) -> dict:
    """Return all available tools for a connector with method, path, and param info."""
    from app.db.config_models import ConnectorSpec

    spec = (
        config_db.query(ConnectorSpec)
        .filter(ConnectorSpec.connector_name == connector_name)
        .first()
    )
    if not spec:
        available = [
            s.connector_name
            for s in config_db.query(ConnectorSpec.connector_name).all()
        ]
        return {
            "error": f"Connector not found: {connector_name}",
            "available_connectors": available,
        }

    tools = []
    for t in spec.tools or []:
        field_descs = t.get("field_descriptions") or {}
        required = t.get("required_fields") or []
        tools.append(
            {
                "action": t.get("action", ""),
                "method": t.get("method", "GET"),
                "path": t.get("path_template", ""),
                "description": t.get("description", ""),
                "required_fields": required,
                "field_descriptions": field_descs,
            }
        )

    return {"tools": tools}


def _apply_transform(
    tool_def: dict, payload: Any, venue_timezone: str | None = None
) -> Any:
    """Apply a tool's response_transform, if it has one enabled.

    Shared by the handler and spec paths so a transform behaves the same
    however the tool was dispatched.
    """
    transform_config = (tool_def or {}).get("response_transform")
    if not (transform_config and transform_config.get("enabled") and payload):
        return payload

    from app.connectors.response_transform import apply_response_transform

    wrapped = (
        {"data": payload}
        if isinstance(payload, list)
        else (payload if isinstance(payload, dict) else {"data": payload})
    )
    transformed = apply_response_transform(
        wrapped, transform_config, venue_timezone=venue_timezone
    )
    return (
        transformed.get("data", transformed)
        if isinstance(transformed, dict)
        else transformed
    )


def _resolve_credentials(
    connector_name: str,
    venue_id: str | None,
    db: Session,
    *,
    strict_venue: bool = False,
):
    """Venue-aware credential lookup.

    With ``strict_venue=True`` the fallback is restricted to venue-agnostic
    (platform) configs, matching tool_loop._resolve_venue_config. The loose
    default — fall back to *any* enabled config — is retained for existing
    callers (dashboard chart refresh), but is a cross-venue leak on any
    authenticated, venue-scoped path: a request scoped to venue A would be
    answered with venue B's credentials, and the caller would never know.
    """
    from app.db.models import ConnectorConfig

    if venue_id:
        config = (
            db.query(ConnectorConfig)
            .filter(
                ConnectorConfig.connector_name == connector_name,
                ConnectorConfig.venue_id == venue_id,
                ConnectorConfig.enabled == "true",
            )
            .first()
        )
        if config:
            return config

    if strict_venue:
        # Platform-level configs only — never another venue's.
        return (
            db.query(ConnectorConfig)
            .filter(
                ConnectorConfig.connector_name == connector_name,
                ConnectorConfig.venue_id.is_(None),
                ConnectorConfig.enabled == "true",
            )
            .first()
        )

    # Fall back to first enabled config (platform or any venue)
    return (
        db.query(ConnectorConfig)
        .filter(
            ConnectorConfig.connector_name == connector_name,
            ConnectorConfig.enabled == "true",
        )
        .first()
    )
