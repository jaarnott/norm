"""The hourly copy of staff from Loaded (docs/plans/staff-access.md, 11.6).

Every tick of the 5-minute ``/internal/run-due-tasks`` job calls
:func:`run_due` on a thread of its own. For each organisation with staff
access switched on it claims this hour's slot (``staff_sync_runs`` is unique
on organisation and ``YYYY-MM-DDTHH``), so the next tick and the other API
instances find the hour taken and move on; then it runs
``staff_directory.sync_org`` and keeps the summary on the row. A failure is
logged on that row and never raised: one organisation's Loaded outage must
not stop the others, and nothing here may take the tick down.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)


def slot_for(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")


def run_due(now: datetime | None = None, db=None, config_db=None) -> int:
    """Run this hour's staff copy for every organisation that hasn't had it
    yet. Returns how many organisations ran."""
    from app.db.engine import SessionLocal, _ConfigSessionLocal
    from app.db.models import Organization, StaffSyncRun
    from app.services import staff_directory

    now = now or datetime.now(timezone.utc)
    slot = slot_for(now)
    own_db, own_cdb = db is None, config_db is None
    db = db or SessionLocal()
    config_db = config_db or _ConfigSessionLocal()
    ran = 0
    try:
        org_ids = [
            o.id
            for o in db.query(Organization.id)
            .filter(Organization.staff_access_enabled.is_(True))
            .order_by(Organization.created_at)
            .all()
        ]
        for org_id in org_ids:
            claim = StaffSyncRun(organization_id=org_id, slot=slot, started_at=now)
            try:
                # A savepoint: losing the race must not roll back anything
                # else in this session.
                with db.begin_nested():
                    db.add(claim)
                    db.flush()
            except IntegrityError:
                continue  # this hour is taken — another tick or instance
            db.commit()
            try:
                summary = staff_directory.sync_org(db, config_db, org_id, now=now)
                db.commit()
                claim = db.merge(claim)
                claim.summary = summary.as_dict()
            except Exception as exc:  # noqa: BLE001 — one org never stops the rest
                db.rollback()
                claim = db.merge(claim)
                claim.summary = {"error": str(exc)[:2000]}
                logger.exception("staff sync failed for organisation %s", org_id)
            claim.finished_at = datetime.now(timezone.utc)
            db.commit()
            ran += 1
        return ran
    finally:
        if own_db:
            db.close()
        if own_cdb:
            config_db.close()
