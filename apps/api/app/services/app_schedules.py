"""Scheduled runs for apps built into Norm (Oct 2026).

A built-in app's spec can declare daily jobs:

    "schedules": [{"op": "nightly", "at": "19:00", "tz": "UTC"}]

Every tick of the existing 5-minute ``/internal/run-due-tasks`` job (and the
manual ``/internal/run-app-schedules``) calls :func:`run_app_schedules`, which
runs each job once a day, for every organization that has the app's catalog
App on — once ``at`` has passed in ``tz``. No new scheduler job is needed.

* **Once per slot.** The slot is the local date. A row in
  ``app_schedule_runs`` (unique on org, app, op, slot) is inserted and
  committed BEFORE the run, so a second API instance or the next tick finds it
  taken and moves on. A run that fails stays failed for that day (its error is
  on the row); it isn't retried in a loop.
* **Tenancy.** Each run binds the app to ONE org (``builtin_apps.bind``) and
  runs as the system actor (``app_runtime.SYSTEM``), which is honoured only for
  built-ins and only inside that binding — exactly the boundary interactive
  runs have.
* **Isolation.** Each org's run commits or rolls back on its own; one org's
  failure never touches another's.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)


def _schedules(spec: dict) -> list[dict]:
    out = []
    for s in (spec or {}).get("schedules") or []:
        if not isinstance(s, dict) or not s.get("op") or not s.get("at"):
            continue
        try:
            hh, mm = (int(x) for x in str(s["at"]).split(":", 1))
            tz = ZoneInfo(str(s.get("tz") or "UTC"))
        except Exception:  # noqa: BLE001 — a bad entry is skipped, not fatal
            logger.warning("bad schedule entry %r", s)
            continue
        out.append({"op": str(s["op"]), "hh": hh, "mm": mm, "tz": tz})
    return out


def due_slot(schedule: dict, now: datetime) -> str | None:
    """Today's slot (local date) if this schedule's time has passed, else None."""
    local = now.astimezone(schedule["tz"])
    if (local.hour, local.minute) >= (schedule["hh"], schedule["mm"]):
        return local.date().isoformat()
    return None


def run_app_schedules(now: datetime | None = None, db=None, config_db=None) -> dict:
    """Run every due built-in app schedule once. Returns what ran."""
    from app.db.engine import SessionLocal, _ConfigSessionLocal
    from app.db.models import AppScheduleRun, Organization, Venue
    from app.services.app_runtime import SYSTEM, run_logic
    from app.services.builtin_apps import bind, builtin_apps, is_on

    now = now or datetime.now(timezone.utc)
    own_db, own_cdb = db is None, config_db is None
    db = db or SessionLocal()
    config_db = config_db or _ConfigSessionLocal()
    ran: list[dict] = []
    try:
        jobs = [(b, s) for b in builtin_apps().values() for s in _schedules(b.spec)]
        if not jobs:
            return {"ran": []}
        org_ids = [o.id for o in db.query(Organization.id).all()]
        for b, sched in jobs:
            slot = due_slot(sched, now)
            if slot is None:
                continue
            for org_id in org_ids:
                if not is_on(b.slug, org_id, db, config_db):
                    continue
                claim = AppScheduleRun(
                    organization_id=org_id, app_slug=b.slug, op=sched["op"], slot=slot
                )
                try:
                    # A savepoint: losing the race must not roll back anything
                    # else in this session.
                    with db.begin_nested():
                        db.add(claim)
                        db.flush()
                except IntegrityError:
                    continue  # already claimed — another tick or instance
                db.commit()
                app, version = bind(b, org_id)
                entry = {"org": org_id, "app": b.slug, "op": sched["op"], "slot": slot}
                try:
                    out = run_logic(
                        db,
                        config_db,
                        app=app,
                        version=version,
                        user=SYSTEM,
                        venue_id=None,
                        # The page normally supplies the venue list; a scheduled
                        # run has no page, so the org's venues come from here.
                        params={
                            "op": sched["op"],
                            "_schedule": {"slot": slot},
                            "venues": [
                                {"id": v.id, "name": v.name}
                                for v in db.query(Venue)
                                .filter(Venue.organization_id == org_id)
                                .all()
                            ],
                        },
                    )
                    if out.get("success"):
                        db.commit()
                    else:
                        db.rollback()
                    claim = db.merge(claim)
                    claim.ok = bool(out.get("success"))
                    claim.error = (out.get("error") or None) and str(out.get("error"))[
                        :2000
                    ]
                    data = out.get("data")
                    claim.summary = data if isinstance(data, dict) else None
                except Exception as exc:  # noqa: BLE001 — one org never stops the rest
                    db.rollback()
                    claim = db.merge(claim)
                    claim.ok = False
                    claim.error = str(exc)[:2000]
                    logger.exception("scheduled run failed: %s", entry)
                claim.finished_at = datetime.now(timezone.utc)
                db.commit()
                entry["ok"] = claim.ok
                ran.append(entry)
        return {"ran": ran}
    finally:
        if own_db:
            db.close()
        if own_cdb:
            config_db.close()
