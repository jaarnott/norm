"""Suggested prompts under the new-chat box, and the insights behind them.

``GET /suggestions`` is what Home shows one person (services/suggestions);
``POST /suggestions/events`` records what they saw, clicked or dismissed, so
Home learns and stops repeating itself. The two admin routes list a venue's
insights and run its insight detectors now (services/insights) — for
checking a venue without waiting for tomorrow morning.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_permission
from app.db.engine import get_config_db, get_db
from app.db.models import User

router = APIRouter()


@router.get("/suggestions")
def get_suggestions(
    member: str | None = Query(None),
    venue_id: str | None = Query(None),
    limit: int = Query(4, ge=1, le=4),
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    from app.services.suggestions.engine import suggestions_for

    return {
        "suggestions": suggestions_for(db, config_db, user, member, venue_id, limit)
    }


class SuggestionEventIn(BaseModel):
    suggestion_id: str
    event: Literal["shown", "clicked", "dismissed"]
    venue_id: str | None = None


class SuggestionEventsBody(BaseModel):
    events: list[SuggestionEventIn]


@router.post("/suggestions/events")
def post_suggestion_events(
    body: SuggestionEventsBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.services.suggestions.events import record_event

    for ev in body.events[:20]:
        record_event(db, user, ev.suggestion_id, ev.event, ev.venue_id)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ notes
VERDICT_WORDS = {
    "expected": "Expected",
    "acting": "Being fixed",
    "not_useful": "Not useful",
}


def _can_clear(note, user: User, permissions: set[str]) -> bool:
    return (
        note.created_by == user.id
        or "org:manage" in permissions
        or getattr(user, "role", None) == "admin"
    )


@router.get("/insight-notes")
def list_insight_notes(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("reports:read")),
):
    """What people have told Norm about its suggestions, newest first: Settings
    → Suggestions. A note quietens (or follows up) a suggestion for the whole
    organisation, so everyone who sees suggestions can see why."""
    from app.db.models import Insight, InsightNote, Venue
    from app.services.entitlements import org_id_for_user
    from app.services.suggestions.engine import permissions_of

    org_id = org_id_for_user(user.id, db)
    if not org_id:
        return {"notes": []}
    perms = permissions_of(db, user)
    notes = (
        db.query(InsightNote)
        .filter(InsightNote.organization_id == org_id)
        .order_by(InsightNote.created_at.desc())
        .limit(200)
        .all()
    )
    keys = {n.subject_key for n in notes}
    insights = (
        {
            i.subject_key: i
            for i in db.query(Insight).filter(Insight.subject_key.in_(keys)).all()
        }
        if keys
        else {}
    )
    venues = {
        v.id: v.name
        for v in db.query(Venue).filter(Venue.organization_id == org_id).all()
    }
    users = (
        {
            u.id: (u.full_name or u.email)
            for u in db.query(User)
            .filter(User.id.in_({n.created_by for n in notes if n.created_by}))
            .all()
        }
        if notes
        else {}
    )
    out = []
    for n in notes:
        ins = insights.get(n.subject_key)
        out.append(
            {
                "id": n.id,
                "about": ins.headline if ins else n.detector.replace("_", " "),
                "venue": venues.get(n.venue_id),
                "verdict": n.verdict,
                "verdict_label": VERDICT_WORDS.get(n.verdict, n.verdict),
                "reason": n.reason,
                "until": n.until,
                "by": users.get(n.created_by),
                "created_at": n.created_at.isoformat() if n.created_at else None,
                "thread_id": n.thread_id,
                "can_clear": _can_clear(n, user, perms),
            }
        )
    return {"notes": out}


@router.delete("/insight-notes/{note_id}")
def clear_insight_note(
    note_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("reports:read")),
):
    """Clear a note: the suggestion it quietened can come back. The person who
    made it, or an owner, may clear it."""
    from app.db.models import InsightNote
    from app.services.entitlements import org_id_for_user
    from app.services.suggestions.engine import permissions_of

    note = db.query(InsightNote).filter(InsightNote.id == note_id).first()
    if note is None or note.organization_id != org_id_for_user(user.id, db):
        raise HTTPException(404, "No such note")
    if not _can_clear(note, user, permissions_of(db, user)):
        raise HTTPException(
            403, "Only the person who made this note, or an owner, can clear it"
        )
    db.delete(note)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ admin
@router.get("/admin/insights")
def list_insights(
    venue_id: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("admin:system")),
):
    from app.db.models import Insight, InsightRun

    q = db.query(Insight)
    runs = db.query(InsightRun)
    if venue_id:
        q = q.filter(Insight.venue_id == venue_id)
        runs = runs.filter(InsightRun.venue_id == venue_id)
    return {
        "insights": [
            {
                "id": r.id,
                "venue_id": r.venue_id,
                "detector": r.detector,
                "subject": r.subject,
                "area": r.area,
                "audience": r.audience,
                "headline": r.headline,
                "prompt": r.prompt,
                "value": float(r.value or 0),
                "as_of": r.as_of,
                "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            }
            for r in q.order_by(Insight.value.desc()).limit(200).all()
        ],
        "runs": [
            {
                "venue_id": r.venue_id,
                "slot": r.slot,
                "cadences": r.cadences,
                "ok": r.ok,
                "error": r.error,
                "summary": {
                    k: v for k, v in (r.summary or {}).items() if k != "item_supplier"
                },
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in runs.order_by(InsightRun.started_at.desc()).limit(50).all()
        ],
    }


class RunInsightsBody(BaseModel):
    venue_ids: list[str]
    cadences: list[Literal["daily", "weekly", "roster"]] = ["daily", "weekly", "roster"]
    rerun: bool = False


@router.post("/admin/insights/run")
def run_insights_now(
    body: RunInsightsBody,
    user: User = Depends(require_permission("admin:system")),
):
    """Run the detectors for these venues now. Reads only (Norm's own tools
    against LoadedHub); takes a minute or two per venue for the weekly set."""
    from app.services.insights.runner import run_due_insights

    if not body.venue_ids:
        raise HTTPException(400, "Name at least one venue")
    return run_due_insights(
        venue_ids=body.venue_ids, force_cadences=list(body.cadences), rerun=body.rerun
    )
