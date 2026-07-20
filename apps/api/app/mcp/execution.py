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
from app.mcp.results import shape_result, ui_content_summary, ui_payload
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

        # App-support tools (embedded MCP Apps' callback surface) validate
        # their own venue_id argument against the principal — they carry ids,
        # not display names, so name-based venue resolution below doesn't
        # apply to them.
        if tool.kind == "app":
            return self._call_app_tool(tool, dict(arguments))

        params = dict(arguments)
        venue_name = params.pop("venue", None)

        # Group-wide question, group-wide answer. Handled before venue
        # resolution because "all" is not a venue to authorize — it means every
        # venue this principal already consented to.
        if (
            tool.multi_venue
            and isinstance(venue_name, str)
            and venue_name.strip().lower() == "all"
        ):
            return self._call_all_venues(tool, params, arguments)

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

        # A tool bound to a display-block UI renders the full payload in the
        # card. If its model-facing content would be truncated, hand the model
        # a compact "it's on screen" summary instead of the "too many, narrow
        # the request" envelope — the envelope reads as a failure and sends the
        # model re-fetching a roster the user is already looking at. Only when
        # the card actually holds the data (ui_payload not None); if the payload
        # is too big even for the card, the honest envelope stands.
        structured = ui_payload(result.payload) if tool.ui_resource else None
        if structured is not None and truncated and result.success:
            payload = ui_content_summary(result.payload)
            truncated = False

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

        # The app renders from `structuredContent` — the FULL payload (computed
        # above as `structured`; the content shaping exists to protect the
        # model's context, and applying it to the app's copy is what once left
        # a week-long roster with nothing to draw). Wrap it as a display block.
        if structured is not None:
            structured = self._as_display_block(tool, structured)
        return tools_call_result(payload, structured=structured)

    def _call_app_tool(self, tool: McpTool, params: dict) -> dict:
        """Run an app-support tool (app_tools.py) with audit and error mapping.

        No result reshaping: the caller is an app that parses the payload, so
        a `_slimmed` envelope would break it. app_tools bounds its own result
        sizes (paging) instead.
        """
        from app.mcp.app_tools import AppToolError, execute_app_tool

        t0 = time.time()
        try:
            payload = execute_app_tool(
                tool.name, params, self.principal, self.db, self.config_db
            )
            success, error = True, None
        except AppToolError as exc:
            payload, success, error = None, False, str(exc)
        except Exception:
            logger.exception("mcp_app_tool_failed", extra={"mcp_tool": tool.name})
            payload, success, error = None, False, "The request could not be completed."

        duration_ms = int((time.time() - t0) * 1000)
        logger.info(
            "mcp_tool_call",
            extra={
                "mcp_tool": tool.name,
                "connector": None,
                "action": None,
                "venue_id": params.get("venue_id"),
                "duration_ms": duration_ms,
                "status": "ok" if success else "error",
                "truncated": False,
                "arg_keys": sorted(params),
            },
        )
        self._audit(
            tool.name,
            tool.access,
            params,
            success,
            error_message=error,
            duration_ms=duration_ms,
            venue_id=params.get("venue_id"),
        )
        if not success:
            # AppToolError refusals are recoverable (wrong venue, missing doc,
            # bad params) — mark them so, matching the venue errors above.
            return error_result(
                error or "Tool execution failed", code="VALIDATION_ERROR"
            )
        return tools_call_result(payload)

    def _consented_venues(self) -> list[tuple[str, str]]:
        """(id, name) for the principal's consented venues, ordered by name.

        From the token, never from venue_service.get_user_venues — that fails
        open, and a fan-out over "every venue on the platform" would be a
        cross-tenant leak rather than a slow query.
        """
        from app.db.models import Venue

        if not self.principal.venue_ids:
            return []
        return [
            (v.id, v.name)
            for v in self.db.query(Venue)
            .filter(Venue.id.in_(self.principal.venue_ids))
            .order_by(Venue.name)
            .all()
        ]

    def _call_all_venues(self, tool: McpTool, params: dict, arguments: dict) -> dict:
        """Run a `*_for_period` tool once per consented venue.

        Each venue resolves its own window, so a group with mixed day starts
        gets each venue measured on its own trading day instead of one venue's
        boundary imposed on the rest.

        One venue failing does not fail the call: its error is reported in its
        own row. A partial answer that says which venue is missing is more
        useful than a total failure, and it is what makes a stale POS feed
        visible as a stale feed rather than as a zero.
        """
        venues = self._consented_venues()
        if not venues:
            return error_result(
                "You do not have access to any venues in this organization.",
                code="VALIDATION_ERROR",
            )

        t0 = time.time()
        rows: list[dict] = []
        any_ok = False
        for venue_id, venue_name in venues:
            result = self._execute(tool, dict(params), venue_id)
            row: dict = {"venue": venue_name}
            if result.success:
                any_ok = True
                payload = result.payload
                # The consolidator's {window, data} envelope — lift the window
                # to the row so each venue states the basis of its own numbers.
                if isinstance(payload, dict) and {"window", "data"} <= payload.keys():
                    row["window"] = payload["window"]
                    row["data"] = payload["data"]
                else:
                    row["data"] = payload
            else:
                row["error"] = result.error or "Tool execution failed"
            rows.append(row)

        duration_ms = int((time.time() - t0) * 1000)
        logger.info(
            "mcp_tool_call_all_venues",
            extra={
                "mcp_tool": tool.name,
                "connector": tool.connector,
                "action": tool.action,
                "venue_count": len(venues),
                "failed_venues": sum(1 for r in rows if "error" in r),
                "duration_ms": duration_ms,
                "arg_keys": sorted(params),
            },
        )
        self._audit(
            tool.name,
            tool.access,
            arguments,
            any_ok,
            error_message=None if any_ok else "all venues failed",
            duration_ms=duration_ms,
        )

        if not any_ok:
            return error_result(
                "No venue returned data. "
                + "; ".join(f"{r['venue']}: {r['error']}" for r in rows if "error" in r)
            )

        payload, _ = shape_result({"venues": rows})
        # No structuredContent: the UI components render one venue's payload,
        # and handing them a list would render nothing. A per-venue call still
        # gets its component.
        return tools_call_result(payload)

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

        # A *_for_period consolidator wraps its result as {window, data} so the
        # window it applied is visible. Components parse the raw connector shape
        # and only look one level deep, so hand them the inner payload —
        # otherwise a roster arrives with its shifts one level too far down and
        # renders empty. The window still travels, as a prop.
        body, window = data, None
        if isinstance(data, dict) and "data" in data and "window" in data:
            body, window = data["data"], data["window"]

        # `embedded` tells the component it is running outside the Norm app —
        # no session, no route back to the API — so it skips lookups it would
        # normally fetch and renders from the data it was handed.
        props = {"embedded": True, "connector_name": tool.connector}
        if window:
            props["window"] = window
        return {"component": component, "data": body, "props": props}

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

        # A playbook bound to the display-block app renders through a real
        # Norm component — for create_stock_order, the purchase-order editor
        # itself, with its lines pre-resolved server-side. Failure to build
        # the block must not fail the workflow; the app falls back to the
        # plain payload (workflow card).
        structured = None
        if tool.ui_resource:
            from app.mcp.po_display import playbook_display_block

            block = playbook_display_block(
                payload, venue_id, self.principal, self.db, self.config_db
            )
            structured = ui_payload(block) if block else None
        return tools_call_result(payload, structured=structured)
