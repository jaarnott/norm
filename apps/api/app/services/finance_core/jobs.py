"""The one claim table for every Finance tick job: ``fin_job_runs``.

A job is named (``payroll.snapshot``, ``ent.morning``, ``finance.nightly``,
…), runs for a scope (an entity, a venue, or ``-`` for the organisation) and
a slot (the week, the day, the month it covers). The unique row per
(org, job, scope, slot) is the once-per-slot guard, as ``app_schedule_runs``
is for built-in apps: a module's ``run_due`` claims before it works, so a
second API instance or the next five-minute tick finds the slot taken and
moves on.

Two rules on top of that:

* **A failed run keeps its error** on the row (``finish(error=...)``) and is
  not retried in a loop; the weekly parity email lists it.
* **A stale run is reclaimable.** A row started over thirty minutes ago with
  no ``finished_at`` is a run that died mid-way (an instance restarted, a
  Loaded call that never returned); the next tick takes it over. The reclaim
  is one conditional UPDATE, so two ticks cannot both take it.

Claim, then commit, then work: the claim row must be visible to the other
instances before the slow part starts. ``claim`` and ``finish`` only flush.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.finance_models import FinJobRun
from app.db.models import _now

#: A run with no ``finished_at`` this long after it started is dead.
STALE_AFTER = dt.timedelta(minutes=30)

#: ``scope_id`` for a job that runs once per organisation.
ORG_SCOPE = "-"


def claim(
    db: Session,
    *,
    org_id: str,
    job: str,
    scope_id: str,
    slot: str,
    now: dt.datetime | None = None,
) -> FinJobRun | None:
    """Take the (org, job, scope, slot) run. ``None`` means it is already
    claimed or done; a stale unfinished run is taken over instead."""
    now = now or _now()
    run = FinJobRun(
        organization_id=org_id,
        job=job,
        scope_id=scope_id,
        slot=slot,
        started_at=now,
    )
    try:
        # A savepoint: losing the race must not roll back anything else in
        # this session.
        with db.begin_nested():
            db.add(run)
            db.flush()
        return run
    except IntegrityError:
        pass
    key = (
        FinJobRun.organization_id == org_id,
        FinJobRun.job == job,
        FinJobRun.scope_id == scope_id,
        FinJobRun.slot == slot,
    )
    reclaimed = (
        db.query(FinJobRun)
        .filter(
            *key,
            FinJobRun.finished_at.is_(None),
            FinJobRun.started_at < now - STALE_AFTER,
        )
        .update(
            {FinJobRun.started_at: now, FinJobRun.error: None},
            synchronize_session=False,
        )
    )
    if not reclaimed:
        return None
    db.expire_all()
    return db.query(FinJobRun).filter(*key).one()


def finish(
    db: Session,
    run: FinJobRun,
    error: str | None = None,
    *,
    now: dt.datetime | None = None,
) -> FinJobRun:
    """Mark the run done — with the error that stopped it, if one did."""
    run.finished_at = now or _now()
    run.error = (error or None) and str(error)[:4000]
    db.flush()
    return run


def failed_since(db: Session, org_id: str, since: dt.datetime) -> list[FinJobRun]:
    """Runs that finished with an error, or never finished and went stale,
    since ``since`` — the parity email's list."""
    from sqlalchemy import or_

    stale_before = _now() - STALE_AFTER
    return (
        db.query(FinJobRun)
        .filter(
            FinJobRun.organization_id == org_id,
            FinJobRun.started_at >= since,
            or_(
                FinJobRun.error.is_not(None),
                (FinJobRun.finished_at.is_(None))
                & (FinJobRun.started_at < stale_before),
            ),
        )
        .order_by(FinJobRun.started_at)
        .all()
    )
