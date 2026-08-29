"""Supplier tenders — the web page's bridge to the Cook Brothers App + Loaded.

Loaded's tenders API is unreachable with Norm's own OAuth client (the
``stock:tenders`` scopes aren't grantable on it), so tender documents ride the
Cook Brothers App connection — its consolidated ``stock_loadedhub_tender`` tool
(action: list | get | update), venue resolved from the authenticated
connection, reusing the recipe-write ``_cb_context``/``execute_spec`` plumbing.

The review comes straight through the same tool (``action: review`` — Loaded's
own tender-review report, passed through unchanged), so page and agent see
exactly what Loaded's own screen would show.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.engine import get_config_db, get_db
from app.db.models import User
from app.services.venue_service import user_can_access_venue

logger = logging.getLogger(__name__)

router = APIRouter()

CB_TOOL = "stock_loadedhub_tender"


class TendersRequest(BaseModel):
    venue_id: str


class TenderReviewRequest(BaseModel):
    venue_id: str
    tender_id: str
    start_time: str
    end_time: str


def _call_cb(fields: dict, venue_id: str, db: Session, config_db: Session):
    from app.connectors.spec_executor import execute_spec
    from app.services.recipe_save import RecipeSaveError, _cb_context, _op

    try:
        spec, cfg = _cb_context(venue_id, db, config_db)
    except RecipeSaveError as exc:
        # Venue isn't CB-connected — same actionable message the recipe save gives.
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        op = _op(spec, CB_TOOL)
    except RecipeSaveError:
        raise HTTPException(
            status_code=501,
            detail=(
                "The Cook Brothers App doesn't expose supplier tenders yet — "
                f"its '{CB_TOOL}' tool hasn't been added/discovered."
            ),
        )
    result, _ = execute_spec(
        spec, {**op, "required_fields": []}, fields, cfg.config, db, venue_id=venue_id
    )
    if not result.success:
        raise HTTPException(
            status_code=502, detail=result.error_message or "Cook Brothers App error"
        )
    payload = result.response_payload or {}
    data = (
        payload.get("data")
        if isinstance(payload, dict) and "data" in payload
        else payload
    )
    # The CB tool nests once more per action: list -> {tenders: [...]},
    # get -> {tender: {...}}. Unwrap either; pass anything else through.
    if isinstance(data, dict):
        for key in ("tender", "tenders", "review"):
            if key in data:
                return data[key]
    return data


@router.post("/supplier-tenders/list")
def list_tenders(
    req: TendersRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    if not user_can_access_venue(db, user.id, req.venue_id):
        raise HTTPException(
            status_code=403, detail="You don't have access to that venue."
        )
    return {"data": _call_cb({"action": "list"}, req.venue_id, db, config_db)}


@router.post("/supplier-tenders/review")
def tender_review(
    req: TenderReviewRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    if not user_can_access_venue(db, user.id, req.venue_id):
        raise HTTPException(
            status_code=403, detail="You don't have access to that venue."
        )
    fields = {
        "action": "review",
        "tender_id": req.tender_id,
        "start_time": req.start_time,
        "end_time": req.end_time,
    }
    return {"data": _call_cb(fields, req.venue_id, db, config_db)}
