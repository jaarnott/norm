"""Stock catalogue — the Loaded Stock App's Catalogue page
(docs/plans/stock-catalogue-page.md, 23 Oct 2026).

A venue's stock items, units and suppliers, read and changed through the
SAME tools Norm uses in chat: ``loadedhub.get_stock`` (view ``catalogue``)
reads; ``loadedhub.manage_stock_item`` and ``loadedhub.manage_supplier``
write. Nothing here talks to Loaded itself.

A change is two calls, the way an approval works in chat:

- ``/preview`` runs the tool in preview mode: it reads, works out every
  change, and stops at its ``preview(...)`` without writing. The page shows
  that card — the same before → after rows the chat approval shows.
- ``/run`` runs it for real with the card's fingerprint, so the tool refuses
  (``changed``) when the records moved since the person looked. Pressing
  Confirm is the approval (services/card_actions.py), and it is recorded.

Owner, 23 Oct 2026: anyone who can open Loaded Stock may make these changes.
So the checks are the venue (one the person may open) and the App (Loaded
Stock on for the venue's organisation) — no role scope.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.engine import get_config_db, get_db
from app.db.models import User, Venue
from app.services import card_actions

logger = logging.getLogger(__name__)

router = APIRouter()

#: The App the page belongs to.
APP = "ordering"

#: What the page may ask each tool to do. Anything else is refused here, so
#: the page can never become a door to the rest of a tool.
WRITES: dict[str, set[str]] = {
    "manage_stock_item": {
        "update",
        "retire",
        "restore",
        "merge",
        "update_unit",
        "delete_unit",
        "restore_unit",
        "merge_units",
    },
    "manage_supplier": {"update", "retire", "restore", "merge"},
}


class WriteRequest(BaseModel):
    venue_id: str
    tool: str
    params: dict = Field(default_factory=dict)
    #: The card's fingerprint from /preview: the run refuses if the records
    #: no longer match it. None on a continuation of a long merge.
    fingerprint: str | None = None


def _venue(db: Session, config_db: Session, user: User, venue_id: str) -> Venue:
    """The venue, if this person may open it and Ordering is on there."""
    from app.services.entitlements import apps_on

    venue_id = card_actions.venue_for(db, user, venue_id)
    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    if venue is None:
        raise HTTPException(404, "Venue not found")
    on = apps_on(venue.organization_id, db, config_db)
    if on is not None and APP not in on:
        raise HTTPException(
            403, "Ordering isn't on for this organisation — turn it on first"
        )
    return venue


def _tool_def(config_db: Session, action: str) -> dict:
    from app.connectors import spec_rows
    from app.db.config_models import ConnectionSpec

    spec = (
        config_db.query(ConnectionSpec)
        .filter(ConnectionSpec.connector_name == "loadedhub")
        .first()
    )
    for row in spec_rows.tools(spec) if spec else []:
        if row.get("action") == action and row.get("consolidator_config"):
            return row
    raise HTTPException(503, f"loadedhub.{action} isn't installed")


def _checked(body: WriteRequest) -> str:
    op = str((body.params or {}).get("op") or "").strip().lower()
    if body.tool not in WRITES or op not in WRITES[body.tool]:
        raise HTTPException(400, f"The catalogue page can't run {body.tool} {op!r}")
    return op


def _params(body: WriteRequest, venue: Venue) -> dict:
    # The venue the page chose wins over anything in the params.
    params = {k: v for k, v in (body.params or {}).items() if k != "venue_id"}
    params["venue"] = venue.name
    return params


@router.get("/stock-catalogue")
def catalogue(
    venue_id: str,
    include_deleted: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    """Every item, unit and supplier at the venue (get_stock view
    'catalogue'); with include_deleted, the deactivated ones too."""
    from app.connectors.tool_executor import execute_connector_tool

    venue = _venue(db, config_db, user, venue_id)
    result = execute_connector_tool(
        "loadedhub",
        "get_stock",
        {"view": "catalogue", "include_deleted": include_deleted, "venue": venue.name},
        db,
        config_db,
        venue_id=venue.id,
        strict_venue=True,
        user=user,
    )
    if not result.success:
        raise HTTPException(502, result.error or "Couldn't read the catalogue")
    data = result.payload
    if isinstance(data, dict) and data.get("error"):
        raise HTTPException(502, str(data["error"]))
    return data


@router.post("/stock-catalogue/preview")
def preview(
    body: WriteRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    """What the change would do, as the tool's own approval card. Writes
    nothing. ``done`` instead of a card means there is nothing to change."""
    from app.services import caller_scope, previews

    _checked(body)
    venue = _venue(db, config_db, user, body.venue_id)
    tool_def = _tool_def(config_db, body.tool)
    with caller_scope.use(caller_scope.for_venue(db, venue.id)):
        out = previews.preview_call(
            "loadedhub", body.tool, _params(body, venue), tool_def, db, None
        )
    if out.kind == "card":
        return {"card": out.card}
    if out.kind == "done":
        return {"done": (out.result or {}).get("data")}
    if out.kind == "refused":
        raise HTTPException(
            400, (out.result or {}).get("error") or "Loaded wouldn't take that change"
        )
    raise HTTPException(500, f"Unexpected preview outcome {out.kind!r}")


@router.post("/stock-catalogue/run")
def run(
    body: WriteRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    """Make the change the person confirmed. ``changed`` (the card as it is
    now) when the records moved since the preview; nothing is written then."""
    from app.agents.internal_tools import execute_consolidator
    from app.services import caller_scope, previews

    op = _checked(body)
    venue = _venue(db, config_db, user, body.venue_id)
    tool_def = _tool_def(config_db, body.tool)
    cfg = {**tool_def["consolidator_config"], "action": body.tool}
    if body.fingerprint:
        cfg["expect_fingerprint"] = body.fingerprint
    params = _params(body, venue)
    with caller_scope.use(caller_scope.for_venue(db, venue.id)):
        result = execute_consolidator(cfg, params, db, None)
    if "changed" in result:
        return {
            "changed": previews.normalise(
                result["changed"],
                title=body.tool,
                venue=venue.name,
                source="tool",
                fp=None,
            )
        }
    data = result.get("data")
    error = result.get("error") or (
        data.get("error") if isinstance(data, dict) else None
    )
    card_actions.record(
        db,
        user,
        venue.id,
        f"Catalogue: {body.tool} {op}",
        detail=(f"failed — {error}" if error else None),
    )
    db.commit()
    if not result.get("success", True) or error:
        raise HTTPException(400, str(error or "Loaded refused the change"))
    return {"data": data}
