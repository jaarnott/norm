"""Reusable connector tool execution — the same path the LLM tool loop uses.

Handles: spec lookup, field normalization, execute_spec. (Endpoints return raw
data since Sep 2026 — shaping lives in the consolidators' shapes.)
Used by both the LLM tool loop and the dashboard chart refresh.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session
from app.connectors import spec_rows

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    success: bool
    payload: Any  # transformed response data (list or dict)
    error: str | None = None
    rendered_request: dict | None = None  # {method, url, headers, body}
    row_count: int = 0
    logs: list[str] | None = None  # consolidator execution logs
    # True when the failure is a dead/rejected connector authorization the user
    # must fix by reconnecting — lets the MCP surface offer a connect link
    # instead of a bare error.
    auth_failed: bool = False


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
    """Run under the caller's organisation (caller_scope): the venue this call
    was authorised for — MCP, charts and apps check it before calling — or
    else its thread's user. See _execute_connector_tool for the rest."""
    from app.services import caller_scope

    scope = caller_scope.for_venue(db, venue_id) or caller_scope.for_thread(
        db, thread_id
    )
    with caller_scope.use(scope):
        return _execute_connector_tool(
            connector_name,
            action,
            params,
            db,
            config_db,
            venue_id=venue_id,
            thread_id=thread_id,
            strict_venue=strict_venue,
        )


def _execute_connector_tool(
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
    1. Look up ConnectionSpec and tool definition
    2. Dispatch to a registered internal handler, or a consolidator, if either
       applies (mirrors tool_loop._execute_tool_call)
    3. Resolve venue-aware credentials
    4. Call execute_spec (which normalizes fields via _normalize_fields)
    6. Return clean result

    ``strict_venue`` controls the credential fallback when no config exists for
    ``venue_id``. False (default) keeps the historical loose behaviour — fall
    back to any enabled config. True restricts the fallback to venue-agnostic
    (platform) configs only, matching tool_loop._resolve_venue_config. Callers
    serving an authenticated, venue-scoped request must pass True: the loose
    fallback would otherwise answer a question about venue A using venue B's
    credentials.
    """
    from app.db.config_models import ConnectionSpec
    from app.db.models import Venue
    from app.connectors.spec_executor import execute_spec

    # 1. Look up connector spec
    spec = (
        config_db.query(ConnectionSpec)
        .filter(ConnectionSpec.connector_name == connector_name)
        .first()
    )
    if not spec:
        available = [
            s.connector_name
            for s in config_db.query(ConnectionSpec.connector_name).all()
        ]
        return ToolResult(
            success=False,
            payload=None,
            error=f"Connector not found: {connector_name}. Available: {', '.join(available)}",
        )

    # 2. Find matching tool definition
    tool_def = None
    for t in spec_rows.rows(spec):
        if t.get("action") == action:
            tool_def = t
            break
    if not tool_def:
        available_actions = [t.get("action") for t in spec_rows.rows(spec)]
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
            # Carry the action, as the agent loop does: without it
            # execute_consolidator can't tell which workflow this is, so a run
            # mode passed by MCP, a chart or an app went straight through —
            # mode="autopilot" reconciled at autopilot for anyone
            # (consolidator review, 1 Oct 2026).
            cfg = {**consolidator_config, "action": action}
            return execute_consolidator(cfg, p, db_sess, tid)

    if handler:
        # Pass venue info through params so the handler can use it: the name for
        # display, and the id for anything needing the venue's own settings —
        # resolve_dates reads day_start_time/timezone off it, so without the id
        # it silently applies the org default instead of the venue's calendar.
        call_params = dict(params)
        if venue_id:
            call_params.setdefault("venue_id", venue_id)
            v = db.query(Venue).filter(Venue.id == venue_id).first()
            if v and not call_params.get("venue"):
                call_params["venue"] = v.name

        try:
            handler_result = handler(call_params, db, thread_id)
        except Exception as exc:
            return ToolResult(success=False, payload=None, error=str(exc))

        payload = handler_result.get("data")
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
            auth_failed=getattr(result, "auth_failed", False),
        )

    # Endpoints return raw data (Sep 2026): no transform here.
    payload = result.response_payload

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
    from app.db.config_models import ConnectionSpec

    spec = (
        config_db.query(ConnectionSpec)
        .filter(ConnectionSpec.connector_name == connector_name)
        .first()
    )
    if not spec:
        available = [
            s.connector_name
            for s in config_db.query(ConnectionSpec.connector_name).all()
        ]
        return {
            "error": f"Connector not found: {connector_name}",
            "available_connectors": available,
        }

    tool_def = None
    for t in spec_rows.rows(spec):
        if t.get("action") == action:
            tool_def = t
            break
    if not tool_def:
        return {
            "error": f"Action not found: {action}",
            "available_actions": [t.get("action") for t in spec_rows.rows(spec)],
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
    """The TOOLS a chart may call on a connector, with method, path and params.
    Endpoints are not listed: charts follow the same rule as the agent."""
    from app.db.config_models import ConnectionSpec

    spec = (
        config_db.query(ConnectionSpec)
        .filter(ConnectionSpec.connector_name == connector_name)
        .first()
    )
    if not spec:
        available = [
            s.connector_name
            for s in config_db.query(ConnectionSpec.connector_name).all()
        ]
        return {
            "error": f"Connector not found: {connector_name}",
            "available_connectors": available,
        }

    tools = []
    # Charts call TOOLS only (Sep 2026) — never an endpoint.
    for t in spec_rows.tools(spec):
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
    from app.db.models import Connection

    if venue_id:
        config = (
            db.query(Connection)
            .filter(
                Connection.connector_name == connector_name,
                Connection.venue_id == venue_id,
                Connection.enabled == "true",
            )
            .first()
        )
        if config:
            return config

    if strict_venue:
        # Platform-level configs only — never another venue's.
        return (
            db.query(Connection)
            .filter(
                Connection.connector_name == connector_name,
                Connection.venue_id.is_(None),
                Connection.enabled == "true",
            )
            .first()
        )

    # Fall back to first enabled config (platform or any venue)
    return (
        db.query(Connection)
        .filter(
            Connection.connector_name == connector_name,
            Connection.enabled == "true",
        )
        .first()
    )
