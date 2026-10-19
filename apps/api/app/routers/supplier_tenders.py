"""Supplier tenders — the web page's bridge to Loaded's tenders.

Loaded's tenders API is unreachable with Norm's own OAuth client (the
``stock:tenders`` scopes aren't grantable on it), so it goes through Loaded
Legacy (app/connectors/loaded_legacy.py): ``list_tenders`` and
``review_tender`` — Loaded's own tender-review report, passed through
unchanged, so the page shows what Loaded's own screen would. Until 19 Oct 2026
this went through the Cook Brothers App's ``stock_loadedhub_tender``
(docs/plans/loaded-legacy-takeover.md).
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


class TendersRequest(BaseModel):
    venue_id: str


class TenderReviewRequest(BaseModel):
    venue_id: str
    tender_id: str
    start_time: str
    end_time: str


def _call(action: str, fields: dict, venue_id: str, db: Session, config_db: Session):
    from app.connectors import loaded_legacy

    if loaded_legacy.connection(db, venue_id) is None:
        raise HTTPException(
            status_code=400,
            detail="This venue isn't connected to Loaded Legacy — connect it in "
            "Settings → Venues to see its tenders.",
        )
    try:
        data = loaded_legacy.call(action, fields, venue_id, db, config_db)
    except loaded_legacy.LoadedLegacyError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    # Loaded answers the list as an array (older answers wrapped it).
    if isinstance(data, dict) and action == "list_tenders":
        for key in ("items", "data"):
            if isinstance(data.get(key), list):
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
    return {"data": _call("list_tenders", {}, req.venue_id, db, config_db)}


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
        "tender_id": req.tender_id,
        "start_time": req.start_time,
        "end_time": req.end_time,
    }
    return {"data": _call("review_tender", fields, req.venue_id, db, config_db)}
