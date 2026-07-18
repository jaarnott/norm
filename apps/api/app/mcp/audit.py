"""MCP audit trail.

The brief requires knowing, for every action a client takes on a user's behalf:
which user, which client, which capability, which business record, whether it
was read/draft/write, and when. This module writes that record.

Two design choices that matter:

- **Written at the dispatch boundary, not inside tools.** A decorator wraps the
  call, so a tool body never mentions auditing and can't forget to. The tool
  reports which records it touched via a contextvar; if it never does, the row
  is still written with a null record_id.

- **Its own DB session and transaction.** If the tool's transaction rolls back,
  the audit row must survive — a failed *attempt* is exactly what you want on
  record. Conversely an audit-write failure must never roll back a successful
  business write. For reads, an audit failure is swallowed; for draft/write it
  fails the call — if you can't record a mutation, don't do it.
"""

from __future__ import annotations

import contextvars
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Records a tool touched, reported by the tool without knowing audit exists.
# list[tuple[record_type, record_id]].
_touched_var: contextvars.ContextVar[list] = contextvars.ContextVar(
    "mcp_touched_records", default=[]
)

# Per-capability allowlist of argument keys safe to persist. ALLOWLIST, never
# blocklist — a blocklist leaks PII the day someone adds a field. Keys not
# listed (and all free-text) are dropped. Values are never stored regardless.
_ARG_ALLOWLIST: dict[str, set[str]] = {
    # date/venue selectors are safe; free-text queries are not
    "norm__resolve_dates": {"timezone"},
}
_DEFAULT_ALLOWED_ARG_KEYS: set[str] = {
    "venue",
    "start_date",
    "end_date",
    "start_datetime",
    "end_datetime",
    "interval",
    "period",
}


def reset_touched() -> None:
    _touched_var.set([])


def record_touched(record_type: str, record_id: str) -> None:
    """A tool calls this to note a business record it read or wrote.

    The tool doesn't know an audit log exists — it just declares what it acted
    on. If it never calls this, the audit row still gets written.
    """
    _touched_var.get().append((record_type, record_id))


def _redact_args(tool_name: str, arguments: dict) -> dict:
    allowed = _ARG_ALLOWLIST.get(tool_name, set()) | _DEFAULT_ALLOWED_ARG_KEYS
    return {k: v for k, v in (arguments or {}).items() if k in allowed}


def write_audit(
    *,
    principal,
    capability: str,
    access_level: str,
    arguments: dict,
    success: bool,
    error_code: str | None = None,
    error_message: str | None = None,
    duration_ms: int | None = None,
    venue_id: str | None = None,
    request_id: str | None = None,
) -> None:
    """Write one audit row on its own session/transaction.

    Never raises for reads. For draft/write, an audit failure is re-raised so
    the caller can abort — you must not perform a mutation you can't record.
    """
    from app.db.engine import SessionLocal
    from app.db.mcp_models import McpAuditLog

    touched = _touched_var.get()
    record_type = touched[0][0] if touched else None
    record_id = touched[0][1] if touched else None
    record_ids = [rid for (_t, rid) in touched] if len(touched) > 1 else None

    db = SessionLocal()
    try:
        db.add(
            McpAuditLog(
                user_id=principal.user_id,
                client_id=principal.client_id,
                client_name=principal.client_name,
                token_id=principal.token_id,
                grant_id=principal.grant_id,
                organization_id=principal.organization_id,
                venue_id=venue_id,
                capability=capability,
                access_level=access_level,
                scopes_used=sorted(principal.scopes),
                record_type=record_type,
                record_id=record_id,
                record_ids=record_ids,
                success=success,
                error_code=error_code,
                error_message=(error_message or "")[:2000] or None,
                duration_ms=duration_ms,
                request_id=request_id,
                arguments_redacted=_redact_args(capability, arguments),
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("mcp_audit_write_failed", extra={"mcp_tool": capability})
        # Log and degrade — never re-raise. Auditing here is *post-hoc*: the
        # draft or write has already been committed (a workflow's draft by
        # run_tool_loop, a direct call by the executor) before we get here.
        # Re-raising would turn a completed action into a client-visible error
        # and prompt a retry that duplicates the mutation, which is worse than a
        # missing audit row. Enforcing "don't act unless recorded" would require
        # writing the audit in the same transaction as the action; that is a
        # larger change and out of scope while v1 direct tools are read-only.
        # The failure is loud in logs/Sentry regardless.
    finally:
        db.close()
