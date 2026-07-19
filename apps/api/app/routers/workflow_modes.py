"""Per-user workflow run modes — view and change your own modes.

A user-scoped preference (not admin/org). Mirrors the dashboard-preferences
pattern: read/write the ``User.workflow_modes`` JSON with ``flag_modified``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.auth.dependencies import get_current_user
from app.db.engine import get_db
from app.db.models import User
from app.services.workflow_modes import (
    MODE_IDS,
    WORKFLOW_KEYS,
    catalog,
)

router = APIRouter()


class SetModeRequest(BaseModel):
    workflow: str
    mode: str


@router.get("/workflow-modes")
async def get_workflow_modes(user: User = Depends(get_current_user)):
    """The caller's selections plus the catalog for rendering the settings UI."""
    return {"selected": user.workflow_modes or {}, **catalog()}


@router.post("/workflow-modes")
async def set_workflow_mode(
    body: SetModeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if body.workflow not in WORKFLOW_KEYS:
        raise HTTPException(400, f"unknown workflow: {body.workflow}")
    if body.mode not in MODE_IDS:
        raise HTTPException(400, f"unknown mode: {body.mode}")
    modes = dict(user.workflow_modes or {})
    modes[body.workflow] = body.mode
    user.workflow_modes = modes
    flag_modified(user, "workflow_modes")
    db.commit()
    return {"modes": modes}
