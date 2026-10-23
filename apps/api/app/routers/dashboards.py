"""An AI agent's Dashboard page: which dashboard it shows, and the choices.

A dashboard is a page of code — the person's organisation's own (an app of
kind ``dashboard``) or one of Norm's templates — run by the same AppRunner as
any app. The rules live in services/dashboards.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.engine import get_config_db, get_db
from app.db.models import User
from app.services import dashboards

router = APIRouter()


def _agent(agent: str) -> str:
    from app.services.app_runtime import DASHBOARD_AGENTS

    if agent not in DASHBOARD_AGENTS:
        raise HTTPException(404, "no Dashboard page for that AI agent")
    return agent


@router.get("/dashboards/{agent}")
def dashboard_shown(
    agent: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """What this person's Dashboard page for ``agent`` shows. ``tiles`` is
    true while the page has nothing of the new kind yet, so it keeps showing
    the old tile dashboard."""
    agent = _agent(agent)
    if not dashboards.has_any(db, config_db, user, agent):
        return {"dashboard": None, "tiles": True}
    return {
        "dashboard": dashboards.shown(db, config_db, user, agent),
        "tiles": False,
    }


@router.get("/dashboards/{agent}/available")
def dashboard_choices(
    agent: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """The person's own dashboards, those shared with them, and the templates."""
    return dashboards.available(db, config_db, user, _agent(agent))


class UseRequest(BaseModel):
    ref: str


@router.post("/dashboards/{agent}/use")
def use_dashboard(
    agent: str,
    body: UseRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Show this dashboard on the page — as it is, without copying it."""
    return dashboards.use(db, config_db, user, _agent(agent), body.ref)


class CopyRequest(BaseModel):
    ref: str
    name: str | None = None
    #: Show the copy on its Dashboard page straight away.
    use: bool = True


@router.post("/dashboards/copy")
def copy_dashboard(
    body: CopyRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """The person's own copy of a dashboard, to change."""
    return dashboards.copy(
        db, config_db, user, body.ref, name=body.name, choose=body.use
    )


class ShareRequest(BaseModel):
    slug: str
    shared: bool
    approve_writes: bool = False


@router.post("/dashboards/share")
def share_dashboard(
    body: ShareRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Share your dashboard with the organisation (to use as it is), or stop."""
    return dashboards.set_shared(
        db, user, body.slug, body.shared, approve_writes=body.approve_writes
    )


@router.delete("/dashboards/{slug}")
def remove_dashboard(
    slug: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Remove your own dashboard (it's archived, with its history)."""
    return dashboards.remove(db, user, slug)


class TemplateRequest(BaseModel):
    slug: str
    template: str


@router.post("/dashboards/make-template")
def make_template(
    body: TemplateRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Platform admins: publish your own dashboard as one of Norm's templates,
    or as its next version — everyone using the template gets it."""
    return dashboards.make_template(db, config_db, user, body.slug, body.template)
