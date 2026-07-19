"""The real McpContext — projects the tool surface and executes calls.

Sits between the transport-agnostic dispatch (``server.py``) and Norm's
existing execution path (``tool_executor.execute_connector_tool``), which is
the same path the LLM tool loop and the dashboard refresh use.

Two things this layer is responsible for that the executor is not:

- **Venue resolution.** A caller-supplied venue is input to be checked, never
  an assertion. See ``resolve_mcp_venue``.
- **Result size.** MCP is stateless: unlike the in-app loop, there is no
  ``search_tool_result`` escape hatch for a truncated payload, because that
  needs a prior ToolCall row and a thread. So truncation here is lossy, and
  ``summary_fields`` on the spec row is the only thing that makes a large
  result usable.
"""

from __future__ import annotations

import logging
import time

from sqlalchemy.orm import Session

from app.connectors.mcp_protocol import INVALID_PARAMS, error_result, tools_call_result
from app.mcp.principal import McpPrincipal
from app.mcp.projection import McpTool, project_tools, to_mcp_tool_dict
from app.mcp.audit import reset_touched, write_audit
from app.mcp.results import shape_result, ui_payload
from app.mcp.server import McpContext, McpDispatchError

logger = logging.getLogger(__name__)


class VenueResolutionError(Exception):
    """Venue missing, ambiguous, or not permitted. Recoverable by the model."""


def resolve_mcp_venue(
    principal: McpPrincipal,
    venue_name: str | None,
    db: Session,
) -> str | None:
    """Resolve and authorize a venue for this principal.

    The caller-supplied value is **input to be checked**, never an assertion.
    This is deliberately the inverse of two existing behaviours:

    - ``POST /api/messages`` takes ``req.venue_id`` straight into the agent with
      no authorization check at all.
    - ``venue_service.get_user_venues`` **fails open**: a user with no
      UserVenueAccess rows is handed every venue on the platform.

    Neither is defensible for a third-party AI client, so this fails closed:
    no consented venues means no venues, full stop.
    """
    from app.db.models import UserVenueAccess, Venue

    if not principal.venue_ids:
        raise VenueResolutionError(
            "You do not have access to any venues in this organization."
        )

    permitted = (
        db.query(Venue)
        .filter(Venue.id.in_(principal.venue_ids))
        .order_by(Venue.name)
        .all()
    )
    by_name = {v.name.lower(): v for v in permitted}

    if venue_name is None:
        if len(permitted) == 1:
            return permitted[0].id
        raise VenueResolutionError(
            "Which venue? Specify one of: " + ", ".join(v.name for v in permitted)
        )

    venue = by_name.get(venue_name.strip().lower())
    if venue is None:
        # Do not reveal whether the venue exists elsewhere — only what this
        # principal may see.
        raise VenueResolutionError(
            f"No access to venue '{venue_name}'. Available: "
            + ", ".join(v.name for v in permitted)
        )

    # The token froze venue_ids at consent time; access may have been revoked
    # since. Re-check against live state rather than trusting the token.
    still_has_access = (
        db.query(UserVenueAccess)
        .filter(
            UserVenueAccess.user_id == principal.user_id,
            UserVenueAccess.venue_id == venue.id,
        )
        .first()
    )
    if not still_has_access:
        raise VenueResolutionError(f"No access to venue '{venue_name}'.")

    # Venue.organization_id is nullable; a NULL-org venue must be refused
    # rather than defaulted, or it becomes a cross-org bridge.
    if venue.organization_id != principal.organization_id:
        raise VenueResolutionError(f"No access to venue '{venue_name}'.")

    return venue.id


class NormMcpContext(McpContext):
    """Serves tools/list and tools/call for one authenticated principal."""

    def __init__(self, principal: McpPrincipal, db: Session, config_db: Session):
        super().__init__(principal=principal, db=db, config_db=config_db)
        self._tools: dict[str, McpTool] | None = None

    # ── tools/list ───────────────────────────────────────────────────

    def _venue_names(self) -> list[str]:
        """Names of the principal's *consented* venues.

        Sourced from the token, never from venue_service.get_user_venues —
        that fails open (a user with no access rows is handed every venue on
        the platform).
        """
        from app.db.models import Venue

        if not self.principal.venue_ids:
            return []
        return [
            v.name
            for v in self.db.query(Venue)
            .filter(Venue.id.in_(self.principal.venue_ids))
            .order_by(Venue.name)
            .all()
        ]

    def _tool_map(self) -> dict[str, McpTool]:
        if self._tools is None:
            self._tools = {
                t.name: t
                for t in project_tools(
                    self.db,
                    self.config_db,
                    user_id=self.principal.user_id,
                    granted_scopes=self.principal.scopes,
                    venue_names=self._venue_names(),
                )
            }
        return self._tools

    def list_tools(self) -> list[dict]:
        return [to_mcp_tool_dict(t) for t in self._tool_map().values()]

    # ── tools/call ───────────────────────────────────────────────────

    def call_tool(self, name: str, arguments: dict) -> dict:
        reset_touched()
        tool = self._tool_map().get(name)
        if tool is None:
            # Identical to the unknown-tool error by design: a tool the caller
            # lacks scope for must not be distinguishable from one that doesn't
            # exist, or the error becomes an enumeration oracle. Still audited —
            # "who tried what they couldn't have" is a signal worth keeping.
            self._audit(name, "read", arguments, False, error_code="not_authorized")
            raise McpDispatchError(INVALID_PARAMS, f"Unknown tool: {name}")

        params = dict(arguments)
        venue_name = params.pop("venue", None)

        try:
            venue_id = self._resolve_venue_for(tool, venue_name)
        except VenueResolutionError as exc:
            self._audit(
                tool.name, tool.access, arguments, False, error_code="venue_denied"
            )
            # Recoverable: the model can retry with a valid venue.
            return error_result(str(exc), code="VALIDATION_ERROR")

        # Playbook workflow tools run Norm's own agent + tool loop, which owns
        # drafts and the approval gate. They return a final payload directly.
        if tool.kind == "playbook":
            return self._call_playbook(tool, params, venue_id)

        # A tool that isn't venue-scoped still gets no venue_id above, but the
        # date resolver needs the venue's calendar to apply the right trading
        # day. Hand it one only when it's unambiguous.
        if venue_id is None and not tool.venue_scoped:
            params.setdefault("venue_id", self._calendar_venue())
            if params.get("venue_id") is None:
                params.pop("venue_id", None)

        t0 = time.time()
        result = self._execute(tool, params, venue_id)
        duration_ms = int((time.time() - t0) * 1000)

        payload, truncated = (
            shape_result(
                result.payload,
                summary_fields=list(tool.summary_fields)
                if tool.summary_fields
                else None,
            )
            if result.success
            else (None, False)
        )

        logger.info(
            "mcp_tool_call",
            extra={
                "mcp_tool": tool.name,
                "connector": tool.connector,
                "action": tool.action,
                "venue_id": venue_id,
                "duration_ms": duration_ms,
                "status": "ok" if result.success else "error",
                "truncated": truncated,
                # Keys only — never values. Arguments carry venue and staff data.
                "arg_keys": sorted(params),
            },
        )

        self._audit(
            tool.name,
            tool.access,
            arguments,
            result.success,
            error_message=None if result.success else result.error,
            duration_ms=duration_ms,
            venue_id=venue_id,
        )

        if not result.success:
            return error_result(result.error or "Tool execution failed")

        # A tool with an embedded UI renders from `structuredContent`, so give
        # the app the FULL payload — the shaping above exists to protect the
        # model's context, and applying it to the app's copy is what left a
        # week-long roster with nothing to draw. Falls back to the shaped
        # payload if even the UI budget can't hold it.
        structured = ui_payload(result.payload) if tool.ui_resource else None
        if structured is not None:
            structured = self._as_display_block(tool, structured)
        return tools_call_result(payload, structured=structured)

    def _as_display_block(self, tool: McpTool, data: dict) -> dict:
        """Wrap a payload for the display-block app, or pass it through.

        Norm's components take the raw payload untouched (that is what
        internal_tools._show_component hands them), so this only names the
        component — it never reshapes the data.
        """
        from app.mcp.ui_apps import DISPLAY_BLOCK_URI, component_for

        if tool.ui_resource != DISPLAY_BLOCK_URI:
            return data
        component = component_for(tool.connector, tool.action)
        if not component:
            return data
        # `embedded` tells the component it is running outside the Norm app —
        # no session, no route back to the API — so it skips lookups it would
        # normally fetch and renders from the data it was handed.
        return {
            "component": component,
            "data": data,
            "props": {"embedded": True, "connector_name": tool.connector},
        }

    def _audit(
        self,
        capability,
        access_level,
        arguments,
        success,
        *,
        error_code=None,
        error_message=None,
        duration_ms=None,
        venue_id=None,
    ):
        write_audit(
            principal=self.principal,
            capability=capability,
            access_level=access_level,
            arguments=arguments,
            success=success,
            error_code=error_code,
            error_message=error_message,
            duration_ms=duration_ms,
            venue_id=venue_id,
        )

    def _calendar_venue(self) -> str | None:
        """A venue to read business-calendar settings from, or None.

        ``resolve_dates`` is deliberately not venue-scoped: it returns no venue
        data, so it needs no venue *authorization*. But it does need venue
        *settings* — `day_start_time` and `timezone` — or it silently applies
        the org default instead of the venue's own trading day.

        With exactly one consented venue the choice is unambiguous, so use it.
        With several they may disagree, and picking one silently would be its
        own wrong answer — fall back to the configured default instead.
        """
        venue_ids = self.principal.venue_ids if self.principal else ()
        return venue_ids[0] if len(venue_ids) == 1 else None

    def _resolve_venue_for(self, tool: McpTool, venue_name: str | None) -> str | None:
        """Resolve and authorize the venue for a tool call.

        Driven by `tool.venue_scoped`, NOT by whether the schema carries a
        `venue` property. The property is only injected when the principal has
        more than one venue, so a schema-driven rule would skip authorization
        entirely for single-venue users — and would also leave `venue_id` unset,
        so `strict_venue` could find no venue credentials.
        """
        if not tool.venue_scoped:
            return None
        return resolve_mcp_venue(self.principal, venue_name, self.db)

    def _execute(self, tool: McpTool, params: dict, venue_id: str | None):
        from app.connectors.tool_executor import execute_connector_tool

        return execute_connector_tool(
            tool.connector,
            tool.action,
            params,
            self.db,
            self.config_db,
            venue_id=venue_id,
            # Never fall back to another venue's credentials on an
            # authenticated, venue-scoped request.
            strict_venue=True,
        )

    def _call_playbook(self, tool: McpTool, params: dict, venue_id: str | None) -> dict:
        from app.mcp.workflows import execute_playbook_tool

        request = (params.get("request") or "").strip()
        if not request:
            self._audit(tool.name, tool.access, params, False, error_code="validation")
            return error_result(
                "The 'request' field is required.", code="VALIDATION_ERROR"
            )

        t0 = time.time()
        payload = execute_playbook_tool(
            tool.playbook_slug,
            request,
            venue_id,
            self.principal,
            self.db,
            self.config_db,
        )
        duration_ms = int((time.time() - t0) * 1000)

        is_error = isinstance(payload, dict) and "error" in payload

        # Record the touched business record for the audit row. The workflow
        # runs on a worker thread, so its record_touched contextvar isn't
        # visible here — read the id back off the returned payload instead.
        if not is_error:
            from app.mcp.audit import record_touched

            if payload.get("working_document_id"):
                record_touched("working_document", payload["working_document_id"])
            elif payload.get("thread_id"):
                record_touched("thread", payload["thread_id"])

        self._audit(
            tool.name,
            tool.access,
            params,
            not is_error,
            error_message=payload.get("error") if is_error else None,
            duration_ms=duration_ms,
            venue_id=venue_id,
        )
        if is_error:
            return error_result(
                payload["error"], code=payload.get("code", "INTERNAL_ERROR")
            )
        return tools_call_result(payload)
