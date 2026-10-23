"""Receiving rules in Settings: the organisation's rules and each venue's
overrides (services/receiving_rules). Anyone in the organisation can see
them; an owner or a manager (``receiving:rules``) changes them."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_permission
from app.db.engine import get_db
from app.db.models import User
from app.services import active_org
from app.services import receiving_rules as RR

router = APIRouter()


def _org(db: Session, user: User) -> str:
    org = active_org.org_id_for(db, user.id)
    if not org:
        raise HTTPException(400, "Choose an organisation first")
    return org


class RuleBody(BaseModel):
    kind: str
    params: dict
    #: None: the organisation's rule; a venue: that venue's override.
    venue_id: str | None = None
    note: str | None = None


@router.get("/receiving-rules")
def get_receiving_rules(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return RR.view(db, user, _org(db, user))


@router.put("/receiving-rules")
def put_receiving_rule(
    body: RuleBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(RR.PERMISSION)),
):
    org = _org(db, user)
    try:
        RR.set_rule(
            db,
            org_id=org,
            venue_id=body.venue_id,
            kind=body.kind,
            params=body.params,
            note=body.note,
            user=user,
        )
    except RR.RuleError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return RR.view(db, user, org)


@router.delete("/receiving-rules")
def delete_receiving_rule(
    kind: str,
    venue_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(RR.PERMISSION)),
):
    org = _org(db, user)
    RR.remove_rule(db, org_id=org, venue_id=venue_id, kind=kind)
    db.commit()
    return RR.view(db, user, org)


@router.get("/receiving-rules/examples")
def receiving_rule_examples(
    kind: str,
    basis: str,
    weekday: int | None = None,
    days: int | None = None,
    day: int | None = None,
    when: str | None = None,
    user: User = Depends(get_current_user),
):
    """What a rule would do, before it is saved."""
    try:
        params = RR.clean(
            kind,
            {
                "basis": basis,
                "weekday": weekday,
                "days": days,
                "day": day,
                "when": when,
            },
        )
    except RR.RuleError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "description": RR.describe(kind, params),
        "examples": RR.examples(kind, params),
    }
