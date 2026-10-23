"""Sales Analysis — the Reports agent's Sales Analysis page
(docs/plans/agents-and-apps-restructure.md §5).

The page reads through ``POST /connectors/loadedhub/execute/get_sales``. This
router is only for its one change: putting a POS item in another revenue
group, through the SAME tool Norm uses in chat (``loadedhub.set_revenue_group``),
the way routers/stock_catalogue.py makes the catalogue's changes:

- ``/preview`` runs the tool in preview mode: it reads the POS items, works
  out the change and stops at its ``preview(...)`` without writing. The page
  shows that card — before → after for every POS item with that name.
- ``/run`` runs it for real with the card's fingerprint, so the tool refuses
  (``changed``) when the items moved since the person looked. Pressing
  Confirm is the approval (services/card_actions.py), and it is recorded.

Checks: the venue (one the person may open) and the App (Sales Analysis on
for the venue's organisation).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.engine import get_config_db, get_db
from app.db.models import User, Venue
from app.services import card_actions

router = APIRouter()

#: The App the page belongs to.
APP = "sales-analysis"
TOOL = "set_revenue_group"
GROUPS = {"Food", "Beverage", "Other", "Unassigned"}


class GroupChange(BaseModel):
    venue_id: str
    revenue_group: str
    #: Item names exactly as get_sales shows them (every live POS item with
    #: that name moves together).
    items: list[str] = Field(default_factory=list)
    #: The card's fingerprint from /preview; /run refuses if the items moved.
    fingerprint: str | None = None


def _venue(db: Session, config_db: Session, user: User, venue_id: str) -> Venue:
    from app.services.entitlements import apps_on

    venue_id = card_actions.venue_for(db, user, venue_id)
    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    if venue is None:
        raise HTTPException(404, "Venue not found")
    on = apps_on(venue.organization_id, db, config_db)
    if on is not None and APP not in on:
        raise HTTPException(403, "Sales Analysis isn't on for this organisation")
    return venue


def _tool_def(config_db: Session) -> dict:
    from app.connectors import spec_rows
    from app.db.config_models import ConnectionSpec

    spec = (
        config_db.query(ConnectionSpec)
        .filter(ConnectionSpec.connector_name == "loadedhub")
        .first()
    )
    for row in spec_rows.tools(spec) if spec else []:
        if row.get("action") == TOOL and row.get("consolidator_config"):
            return row
    raise HTTPException(503, f"loadedhub.{TOOL} isn't installed")


def _params(body: GroupChange, venue: Venue) -> dict:
    group = body.revenue_group.strip().capitalize()
    items = [i for i in (s.strip() for s in body.items) if i]
    if group not in GROUPS:
        raise HTTPException(400, "Choose Food, Beverage, Other or Unassigned")
    if not items:
        raise HTTPException(400, "Name the item to move")
    # Only these three: the page can never become a door to pos_category
    # sweeps or multi-group passes. The venue the page chose wins.
    return {"venue": venue.name, "revenue_group": group, "items": items}


@router.post("/sales-analysis/revenue-group/preview")
def preview(
    body: GroupChange,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    """The tool's own approval card for the change. Writes nothing. ``done``
    instead of a card means there is nothing to change."""
    from app.services import caller_scope, previews

    venue = _venue(db, config_db, user, body.venue_id)
    params = _params(body, venue)
    tool_def = _tool_def(config_db)
    with caller_scope.use(caller_scope.for_venue(db, venue.id)):
        out = previews.preview_call("loadedhub", TOOL, params, tool_def, db, None)
    if out.kind == "card":
        return {"card": out.card}
    if out.kind == "done":
        return {"done": (out.result or {}).get("data")}
    if out.kind == "refused":
        raise HTTPException(
            400, (out.result or {}).get("error") or "Loaded wouldn't take that change"
        )
    raise HTTPException(500, f"Unexpected preview outcome {out.kind!r}")


@router.post("/sales-analysis/revenue-group/run")
def run(
    body: GroupChange,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
):
    """Make the change the person confirmed. ``changed`` (the card as it is
    now) when the items moved since the preview; nothing is written then."""
    from app.agents.internal_tools import execute_consolidator
    from app.services import caller_scope, previews

    venue = _venue(db, config_db, user, body.venue_id)
    params = _params(body, venue)
    tool_def = _tool_def(config_db)
    cfg = {**tool_def["consolidator_config"], "action": TOOL}
    if body.fingerprint:
        cfg["expect_fingerprint"] = body.fingerprint
    with caller_scope.use(caller_scope.for_venue(db, venue.id)):
        result = execute_consolidator(cfg, params, db, None)
    if "changed" in result:
        return {
            "changed": previews.normalise(
                result["changed"], title=TOOL, venue=venue.name, source="tool", fp=None
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
        f"Sales Analysis: {params['items'][0]} to {params['revenue_group']}",
        detail=(f"failed — {error}" if error else None),
    )
    db.commit()
    if not result.get("success", True) or error:
        raise HTTPException(400, str(error or "Loaded refused the change"))
    return {"data": data}
