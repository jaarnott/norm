"""Background jobs, for the job's live card: read one, cancel one.

A job is visible to the person who started it, to anyone who may open its
venue (but venue staff, who see only their own), and to a platform admin
(services/background_jobs).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.engine import get_db
from app.db.models import BackgroundJob, User
from app.services import background_jobs

router = APIRouter()


def _job_for(db: Session, user: User, job_id: str) -> BackgroundJob:
    job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
    if job is None:
        raise HTTPException(404, "No such job")
    if getattr(user, "role", None) == "admin" or job.user_id == user.id:
        return job
    from app.services import screen_access

    # A colleague's job at the venue (an invoice batch, a catalogue merge)
    # is a manager's to watch or stop, not venue staff's.
    if job.venue_id and not screen_access.is_staff(db, user):
        from app.services.venue_service import user_can_access_venue

        if user_can_access_venue(db, user.id, job.venue_id):
            return job
    raise HTTPException(404, "No such job")


@router.get("/jobs/{job_id}")
def get_job(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return background_jobs.serialize(_job_for(db, user, job_id))


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Stop a job: a queued one never starts; a running one stops after the
    invoice it is on (everything done so far stays saved)."""
    job = _job_for(db, user, job_id)
    background_jobs.request_cancel(db, job.id)
    db.commit()
    db.refresh(job)
    return background_jobs.serialize(job)
