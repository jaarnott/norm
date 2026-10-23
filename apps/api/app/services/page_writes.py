"""A page button that writes to Loaded or Xero — shown first, then confirmed.

Norm asks before IT writes (services/approvals.py); a page's own button —
"Push journal", "Sync month to Loaded", "Mark paid and bill" — is a person
making the change themselves, and pressing it is the approval. Two things
have to hold, the same two ``card_actions`` holds for the embedded cards:

- **The person saw what lands.** ``preview`` runs the write consolidator in
  preview mode (the executor options ``services/previews.py`` uses) and
  returns the card and its fingerprint; ``confirm`` re-runs it live with
  ``expect_fingerprint``, so the write refuses if Loaded or Xero moved since
  they looked — an approval never lands on values nobody looked at.
- **It acts only where and as whom it may.** The venue is picked fail-closed
  (``card_actions.venue_for``: the one named if they may open it, else their
  only venue, never "whichever login comes first"), and the run is under
  ``caller_scope.use(for_user(...))``, so the executor's ``requires`` check
  sees the clicker and the ledger built-ins take ``pushed_by`` from the
  scope — set only here, never from params.

Mounted once, on the connectors router (``POST /api/connectors/{name}/
execute/{action}/preview`` and ``/confirm``), as the write half of the
component-API door; the generic execute route stays GET-only. Services never
call Loaded or Xero writes directly — they call these two.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.connectors import spec_rows
from app.services import caller_scope, card_actions, previews


@dataclass
class _Target:
    connector: str
    action: str
    tool_def: dict
    venue_id: str
    venue_name: str | None


def _split(tool: str) -> tuple[str, str]:
    connector, _, action = str(tool or "").partition(".")
    if not connector or not action:
        raise HTTPException(400, "tool must be 'connector.action'")
    return connector, action


def _target(db: Session, config_db: Session, *, tool: str, user, venue_id) -> _Target:
    """The tool row and the venue this click may act at, or an HTTP error.

    Only a write tool comes through here: a read belongs on the GET execute
    route, which has no card and no fingerprint to check.
    """
    from app.db.config_models import ConnectionSpec
    from app.db.models import Venue

    connector, action = _split(tool)
    spec = (
        config_db.query(ConnectionSpec)
        .filter(ConnectionSpec.connector_name == connector)
        .first()
    )
    tool_def = spec_rows.find_tool(spec, action) if spec else None
    if tool_def is None:
        raise HTTPException(404, f"Tool not found: {tool}")
    writes = (
        str(tool_def.get("method", "GET")).upper() != "GET"
        or spec_rows.effect(tool_def) in ("write", "draft")
        or bool(
            (tool_def.get("consolidator_config") or {}).get("allowed_write_actions")
        )
    )
    if not writes:
        raise HTTPException(
            400, f"{tool} doesn't write anything — call the execute route instead"
        )
    chosen = card_actions.venue_for(db, user, venue_id)
    venue = db.query(Venue).filter(Venue.id == chosen).first()
    return _Target(connector, action, tool_def, chosen, venue.name if venue else None)


def _params(target: _Target, params: dict | None) -> dict:
    """The call's params with the chosen venue pinned — inside params, which
    is where the engine reads it (a venue outside params is discarded)."""
    out = dict(params or {})
    out["venue_id"] = target.venue_id
    if target.venue_name and not out.get("venue"):
        out["venue"] = target.venue_name
    return out


def _refuse_if_lacking(db: Session, tool_def: dict) -> None:
    refusal = caller_scope.missing_scopes(db, tool_def)
    if refusal:
        raise HTTPException(403, refusal)


def preview(
    db: Session,
    *,
    tool: str,
    params: dict | None,
    user,
    venue_id: str | None,
    config_db: Session | None = None,
) -> dict:
    """What this write would change, from live data, without writing.

    Returns ``{"card": …, "fingerprint": …}``. A write that would change
    nothing comes back as ``{"card": None, "done": <result>}``; one that would
    fail is a 400 with the tool's own reason.
    """
    config_db = config_db or db
    target = _target(db, config_db, tool=tool, user=user, venue_id=venue_id)
    with caller_scope.use(caller_scope.for_user(db, user)):
        _refuse_if_lacking(db, target.tool_def)
        outcome = previews.preview_call(
            target.connector,
            target.action,
            _params(target, params),
            target.tool_def,
            db,
            None,
        )
    if outcome.kind == "refused":
        raise HTTPException(
            400, (outcome.result or {}).get("error") or "the tool refused this"
        )
    if outcome.kind == "done":
        return {"card": None, "fingerprint": None, "done": outcome.result}
    card = outcome.card or {}
    return {
        "card": card,
        "fingerprint": card.get("fingerprint"),
        "run_params": outcome.run_params,
    }


def confirm(
    db: Session,
    *,
    tool: str,
    params: dict | None,
    user,
    venue_id: str | None,
    fingerprint: str | None,
    config_db: Session | None = None,
) -> dict:
    """Run the write the person just previewed and approved.

    ``fingerprint`` is what their card carried; the run checks live data
    still gives it just before writing and stops otherwise (409, with the
    card as it is now). Records who pressed the button (an Approval row via
    ``card_actions.record``). Flushes; the route commits.
    """
    from app.agents.internal_tools import execute_consolidator, get_handler

    config_db = config_db or db
    target = _target(db, config_db, tool=tool, user=user, venue_id=venue_id)
    call_params = _params(target, params)
    title = previews.title_for(target.action, target.tool_def)
    with caller_scope.use(caller_scope.for_user(db, user)):
        _refuse_if_lacking(db, target.tool_def)
        handler = get_handler(target.connector, target.action)
        if handler is not None:
            # A built-in: its previewer says whether what the person saw
            # still holds (the consolidator case checks inside the sandbox).
            shown = {"source": "tool", "fingerprint": fingerprint}
            now = previews.changed_since(_Shown(shown, target, call_params), db)
            if now is not None:
                raise HTTPException(
                    409, {"error": "changed since it was approved", "changed": now}
                )
            run = handler(call_params, db, None)
        else:
            cfg = {
                **(target.tool_def.get("consolidator_config") or {}),
                "action": target.action,
            }
            if fingerprint:
                cfg["expect_fingerprint"] = fingerprint
            run = execute_consolidator(cfg, call_params, db, None)
    if run.get("changed"):
        # Loaded or Xero moved since the person looked: nothing was written.
        # Hand back a fresh card with its own fingerprint, so the page can
        # show what it is now and let them confirm THAT.
        fresh = preview(
            db,
            tool=tool,
            params=params,
            user=user,
            venue_id=venue_id,
            config_db=config_db,
        )
        raise HTTPException(
            409,
            {
                "error": run.get("error") or "changed since it was approved",
                "changed": fresh.get("card"),
                "fingerprint": fresh.get("fingerprint"),
            },
        )
    data = run.get("data")
    error = run.get("error") or (data.get("error") if isinstance(data, dict) else None)
    if not run.get("success", True) or error:
        raise HTTPException(400, error or f"{tool} failed")
    card_actions.record(db, user, target.venue_id, title, detail=tool)
    return {"success": True, "data": data}


class _Shown:
    """The shape ``previews.changed_since`` reads off a ToolCall, for a page
    click that has no ToolCall row."""

    def __init__(self, preview: dict, target: _Target, params: dict):
        self.preview = preview
        self.connector_name = target.connector
        self.action = target.action
        self.input_params = params
        self.thread_id = None
