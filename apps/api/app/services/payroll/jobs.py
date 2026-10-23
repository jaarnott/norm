"""The payroll module's tick jobs (DESIGN-CONTRACT §6), each claimed once
per slot through ``finance_core.jobs.claim``:

* ``payroll.snapshot`` — Monday, 30 minutes after the venue's day start
  (the pay week has just ended), per entity-week: the Monday snapshot.
* ``payroll.close_snapshot`` — Tuesday 10:00 venue-local, per entity-week:
  the close snapshot, unless "Start close" already took one.
* ``payroll.hygiene_email`` — Monday 20:00 venue-local, per entity-week.
* ``payroll.gratuity_reminder`` — the 1st of the month, 09:00 venue-local,
  per entity-month: "enter last month's pool".
* ``payroll.retention`` — daily, per organisation.
* ``finance.parity_weekly`` — Wednesday 04:00 (the first entity's local
  time), per organisation-week, after that night's importer run finished.

Times are venue-local through the venue's timezone and ``day_start_time``;
only the most recent due slot is claimed (the first tick after a deploy
does not replay months of weeks). Claim, commit, then work: ``run_due``
flushes the claim and commits before the slow part, so a second instance
finds the slot taken.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy.orm import Session

from app.db.finance_models import FinJobRun
from app.db.models import Organization, Venue, _now
from app.db.payroll_models import PayEntity
from app.services.business_calendar import timezone_for
from app.services.finance_core import jobs as jobs_svc
from app.services.finance_core.ctx import for_org
from app.services.payroll import weeks as weeks_svc

logger = logging.getLogger(__name__)

JOB_SNAPSHOT = "payroll.snapshot"
JOB_CLOSE = "payroll.close_snapshot"
JOB_HYGIENE = "payroll.hygiene_email"
JOB_GRATUITY = "payroll.gratuity_reminder"
JOB_RETENTION = "payroll.retention"
JOB_PARITY = "finance.parity_weekly"
JOB_IMPORTER = "importer.nightly"

SNAPSHOT_DELAY = dt.timedelta(minutes=30)
CLOSE_TIME = dt.time(10, 0)  # Tuesday
HYGIENE_TIME = dt.time(20, 0)  # Monday
GRATUITY_TIME = dt.time(9, 0)  # the 1st
PARITY_TIME = dt.time(4, 0)  # Wednesday
PARITY_WAIT = dt.timedelta(minutes=30)


# ================================================================== when ==


def _local(venue, now: dt.datetime) -> dt.datetime:
    return now.astimezone(timezone_for(venue))


def _at(venue, day: dt.date, time: dt.time) -> dt.datetime:
    return dt.datetime.combine(day, time, tzinfo=timezone_for(venue))


def due_snapshot(venue, now: dt.datetime) -> dt.date | None:
    """The last completed week, once 30 minutes past its end."""
    last = weeks_svc.last_completed_week_start(venue, now)
    end = weeks_svc.week_window(venue, last).end + dt.timedelta(seconds=1)
    return last if now >= end + SNAPSHOT_DELAY else None


def due_close(venue, now: dt.datetime) -> dt.date | None:
    """The last completed week, from the Tuesday 10:00 after it ended."""
    last = weeks_svc.last_completed_week_start(venue, now)
    tuesday = last + dt.timedelta(days=8)
    return last if now >= _at(venue, tuesday, CLOSE_TIME) else None


def due_hygiene(venue, now: dt.datetime) -> dt.date | None:
    """The last completed week, from 20:00 on the Monday it ended."""
    last = weeks_svc.last_completed_week_start(venue, now)
    monday = last + dt.timedelta(days=7)
    return last if now >= _at(venue, monday, HYGIENE_TIME) else None


def due_gratuity(venue, now: dt.datetime) -> str | None:
    """The previous month, from 09:00 on the 1st."""
    local = _local(venue, now)
    first = local.date().replace(day=1)
    if now < _at(venue, first, GRATUITY_TIME):
        return None
    previous = (first - dt.timedelta(days=1)).replace(day=1)
    return previous.strftime("%Y-%m")


def due_parity(venue, now: dt.datetime) -> dt.date | None:
    """The last completed week, from the Wednesday 04:00 after it ended."""
    last = weeks_svc.last_completed_week_start(venue, now)
    wednesday = last + dt.timedelta(days=9)
    return last if now >= _at(venue, wednesday, PARITY_TIME) else None


def importer_settled(db: Session, org_id: str, now: dt.datetime) -> bool:
    """Has tonight's importer run finished (or never started)? An unfinished
    run younger than the wait holds the parity run back one tick."""
    running = (
        db.query(FinJobRun)
        .filter(
            FinJobRun.organization_id == org_id,
            FinJobRun.job == JOB_IMPORTER,
            FinJobRun.finished_at.is_(None),
            FinJobRun.started_at >= now - PARITY_WAIT,
        )
        .first()
    )
    return running is None


# ================================================================= running ==


def _entities(db: Session, org_id: str) -> list[tuple[PayEntity, Venue]]:
    out = []
    for entity in (
        db.query(PayEntity)
        .filter(PayEntity.organization_id == org_id, PayEntity.payroll_on.is_(True))
        .all()
    ):
        venue = db.query(Venue).filter(Venue.id == entity.venue_id).first()
        if venue is not None:
            out.append((entity, venue))
    return sorted(out, key=lambda ev: ev[1].name or "")


def _orgs(db: Session) -> list[Organization]:
    from app.services.finance_core import modes as modes_svc

    out = []
    for org in db.query(Organization).all():
        modes = modes_svc.modes_of(org)
        if modes["payroll"] != "off" or modes["entertainers"] != "off":
            out.append(org)
    return out


def _run(
    db: Session,
    *,
    org_id: str,
    job: str,
    scope_id: str,
    slot: str,
    now,
    work,
    report: dict,
) -> None:
    """Claim the slot, commit the claim, do the work, record the outcome."""
    run = jobs_svc.claim(
        db, org_id=org_id, job=job, scope_id=scope_id, slot=slot, now=now
    )
    if run is None:
        return
    db.commit()
    key = f"{job}:{scope_id}:{slot}"
    try:
        # The work runs in a savepoint: a failure half-way leaves nothing of
        # it behind and the session stays usable for the next job.
        with db.begin_nested():
            result = work()
        jobs_svc.finish(db, run, now=_now())
        db.commit()
        report[key] = result if isinstance(result, dict) else {"ok": True}
    except Exception as exc:  # noqa: BLE001 — recorded on the run, never stops the tick
        logger.exception("%s failed", key)
        jobs_svc.finish(db, run, error=str(exc), now=_now())
        db.commit()
        report[key] = {"error": str(exc)[:500]}


def run_due(db: Session, now: dt.datetime | None = None, *, config_db=None) -> dict:
    """Every payroll job that is due at ``now``, for every organisation with
    payroll (or entertainers) on."""
    from app.services.payroll import gratuities as gratuities_svc
    from app.services.payroll import hygiene as hygiene_svc
    from app.services.payroll import parity as parity_svc
    from app.services.payroll import retention as retention_svc
    from app.services.payroll import snapshot as snapshot_svc

    now = now or _now()
    report: dict = {}
    for org in _orgs(db):
        ctx = for_org(db, org.id, actor_label="tick")
        payroll_on = ctx.mode("payroll") != "off"
        entities = _entities(db, org.id) if payroll_on else []
        for entity, venue in entities:
            week = due_snapshot(venue, now)
            if week is not None:
                _run(
                    db,
                    org_id=org.id,
                    job=JOB_SNAPSHOT,
                    scope_id=venue.id,
                    slot=week.isoformat(),
                    now=now,
                    work=lambda v=venue, w=week: _snapshot_result(
                        snapshot_svc.take(
                            db, ctx, v.id, w, source="monday", config_db=config_db
                        )
                    ),
                    report=report,
                )
            week = due_hygiene(venue, now)
            if week is not None:
                _run(
                    db,
                    org_id=org.id,
                    job=JOB_HYGIENE,
                    scope_id=venue.id,
                    slot=week.isoformat(),
                    now=now,
                    work=lambda v=venue, w=week: hygiene_svc.send_email(
                        db,
                        ctx,
                        weeks_svc.get_or_create(db, ctx, v.id, w),
                        config_db=config_db,
                    ),
                    report=report,
                )
            week = due_close(venue, now)
            if week is not None:
                _run(
                    db,
                    org_id=org.id,
                    job=JOB_CLOSE,
                    scope_id=venue.id,
                    slot=week.isoformat(),
                    now=now,
                    work=lambda v=venue, w=week: _close(
                        db, ctx, snapshot_svc, v, w, config_db
                    ),
                    report=report,
                )
            period = due_gratuity(venue, now)
            if period is not None:
                _run(
                    db,
                    org_id=org.id,
                    job=JOB_GRATUITY,
                    scope_id=venue.id,
                    slot=period,
                    now=now,
                    work=lambda v=venue, p=period: gratuities_svc.send_reminder(
                        db, ctx, v, p
                    ),
                    report=report,
                )
        if entities:
            first_venue = entities[0][1]
            week = due_parity(first_venue, now)
            if week is not None and importer_settled(db, org.id, now):
                _run(
                    db,
                    org_id=org.id,
                    job=JOB_PARITY,
                    scope_id=jobs_svc.ORG_SCOPE,
                    slot=week.isoformat(),
                    now=now,
                    work=lambda w=week: _parity_result(
                        parity_svc.weekly(db, ctx, week_start=w, config_db=config_db)
                    ),
                    report=report,
                )
        _run(
            db,
            org_id=org.id,
            job=JOB_RETENTION,
            scope_id=jobs_svc.ORG_SCOPE,
            slot=now.astimezone(dt.timezone.utc).date().isoformat(),
            now=now,
            work=lambda o=org: retention_svc.purge(db, o.id, now=now),
            report=report,
        )
    # The weekly Xero status refresh (services/payroll/xero_status.py) rides
    # this runner too: it claims its own (org, week) slot, so it is safe to
    # call every tick and runs once a week per organisation.
    try:
        from app.services.payroll import xero_status as xero_status_svc

        xero = xero_status_svc.run_due(db, now, config_db=config_db)
        if xero:
            report["xero.status_refresh"] = xero
    except Exception:  # noqa: BLE001 — one job's failure never stops the tick
        logger.exception("xero status refresh failed")
    return report


def _snapshot_result(snapshot) -> dict:
    return {
        "snapshot_id": snapshot.id,
        "shifts": snapshot.shift_count,
        "dropped_deleted": snapshot.dropped_deleted,
        "dropped_unfinished": snapshot.dropped_unfinished,
    }


def _close(db, ctx, snapshot_svc, venue, week_start, config_db) -> dict:
    """The Tuesday close snapshot — unless Start close already took one."""
    week = weeks_svc.get_or_create(db, ctx, venue.id, week_start)
    if weeks_svc.close_snapshot(db, week) is not None:
        return {"skipped": "close snapshot already taken"}
    return _snapshot_result(
        snapshot_svc.take(
            db, ctx, venue.id, week_start, source="close", config_db=config_db
        )
    )


def _parity_result(out: dict) -> dict:
    return {
        "runs": len(out.get("runs", [])),
        "failed_jobs": len(out.get("failed_jobs", [])),
        "email": (out.get("email") or {}).get("status"),
    }
