"""The importer's two tick jobs (plan §5.2, §15): the nightly shadow reload
and the post-freeze watch.

* ``importer.nightly`` — 02:30 in the organisation's timezone, one run per
  org-day, for every module still in ``shadow``. Orbit stays the record
  until a module is live, and the morning's parity, snapshots and pages read
  what the night brought in. A module that went live is simply not asked
  for; one that is ``off`` has nothing to compare against.
* ``importer.post_freeze_watch`` — daily, between the Orbit freeze and the
  end of the fallback window (Tue 19 Jan → Wed 3 Feb 2027 in the plan): any
  Orbit payroll-input row created or changed after the freeze means someone
  keyed a week in Orbit after Norm became the record. The payroll contact
  gets a list — table, venue, week, when — never an amount or a name.

Both claim through ``finance_core.jobs`` (``fin_job_runs``) before working,
commit the claim, then run; a failure keeps its error on the row for the
weekly parity email. The Orbit credential comes from ``importer.creds_path``
(``ORBIT_SUPABASE_CREDS`` in production, D31); without one the run records
"no Orbit credential" and the parity email says so.

The freeze window is an organisation setting kept as a dated ``pay_rules``
row (``rule = post_freeze_watch``, ``params = {"freeze_at": …, "until": …}``),
the table every other dated payroll rule lives in; ``set_freeze_window``
writes it.
"""

from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.db.models import Organization, Venue
from app.services.finance_core import importer
from app.services.finance_core.jobs import ORG_SCOPE, claim, finish

logger = logging.getLogger(__name__)

JOB_NIGHTLY = "importer.nightly"
JOB_WATCH = "importer.post_freeze_watch"
NIGHTLY_AT = dt.time(2, 30)
FREEZE_RULE = "post_freeze_watch"
WATCH_TEMPLATE = "importer_post_freeze"


def _org_tz(org) -> ZoneInfo:
    try:
        return ZoneInfo(getattr(org, "default_timezone", None) or "Pacific/Auckland")
    except Exception:  # noqa: BLE001 — a bad timezone string must not stop the tick
        return ZoneInfo("Pacific/Auckland")


def freeze_window(db: Session, org_id: str) -> tuple[dt.datetime, dt.date] | None:
    """``(freeze_at, watch_until)`` for the organisation, or None when no
    window is set. The latest effective rule row wins."""
    from app.db.payroll_models import PayRule

    row = (
        db.query(PayRule)
        .filter(PayRule.organization_id == org_id, PayRule.rule == FREEZE_RULE)
        .order_by(PayRule.effective_from.desc())
        .first()
    )
    if row is None or not isinstance(row.params, dict):
        return None
    freeze_at = importer._ts(row.params.get("freeze_at"))
    until = importer._date(row.params.get("until"))
    if freeze_at is None or until is None:
        return None
    return freeze_at, until


def set_freeze_window(
    db: Session, org_id: str, *, freeze_at: dt.datetime, until: dt.date
) -> None:
    """Record the Orbit freeze (plan §15, 19 Jan row) so the watch runs
    daily until ``until``. One row per freeze date."""
    from app.db.payroll_models import PayRule

    if freeze_at.tzinfo is None:
        raise ValueError("freeze_at needs a timezone")
    row = (
        db.query(PayRule)
        .filter(
            PayRule.organization_id == org_id,
            PayRule.rule == FREEZE_RULE,
            PayRule.effective_from == freeze_at.date(),
        )
        .first()
    )
    params = {"freeze_at": freeze_at.isoformat(), "until": until.isoformat()}
    if row is None:
        db.add(
            PayRule(
                organization_id=org_id,
                rule=FREEZE_RULE,
                effective_from=freeze_at.date(),
                params=params,
                source="cut-over runbook",
            )
        )
    else:
        row.params = params
    db.flush()


def _source(source):
    """The Orbit client — the caller's (tests), else one from the credential."""
    if source is not None:
        return source
    return importer.orbit_client()


def nightly(
    db: Session, org, *, now: dt.datetime, source=None, log=logger.info
) -> dict:
    """One organisation's nightly reload of every shadow module."""
    modes = importer.org_modes(org)
    modules = [m for m in importer.MODULES if modes[m] == "shadow"]
    if not modules:
        return {"skipped": "no module in shadow"}
    result = importer.run_import(
        db,
        _source(source),
        org=org.id,
        modules=modules,
        dry_run=False,
        commit=False,
        now=now,
        log=lambda *a: None,
    )
    log("importer.nightly %s: %s", org.slug, result.report.outcome)
    if not result.ok:
        failed = [
            c["check"] + ": " + c["detail"]
            for c in result.report.gate["checks"]
            if not c["ok"]
        ]
        raise RuntimeError("sanity gate failed — " + "; ".join(failed)[:1500])
    return {
        "modules": modules,
        "written": {
            t: w.get("insert", 0) + w.get("update", 0)
            for t, w in result.report.written.items()
        },
        "collisions": len(result.report.collisions),
    }


def post_freeze_watch(
    db: Session, org, *, now: dt.datetime, source=None, send=None
) -> dict:
    """List Orbit payroll-input rows written since the freeze and email the
    payroll contact. ``send`` defaults to ``email_service.send_system_email``."""
    window = freeze_window(db, org.id)
    if window is None:
        return {"skipped": "no freeze window"}
    freeze_at, _until = window
    src = _source(source)
    venues = db.query(Venue).filter(Venue.organization_id == org.id).all()
    company_venues, _xero = importer._connections(db, venues)
    orbit_venues = {
        v["id"]: v
        for v in src.rows("venues", select="id,name,loadedhub_company_id", key=("id",))
        if str(v.get("loadedhub_company_id") or "") in company_venues
    }
    found = importer.post_freeze_rows(src, freeze_at, orbit_venues)
    rows = sum(len(v) for v in found.values())
    contact = (getattr(org, "payroll_contact_email", None) or "").strip()
    out = {"rows": rows, "tables": {t: len(v) for t, v in found.items()}}
    if not rows:
        return out
    if not contact:
        out["email"] = "suppressed: no payroll_contact_email on the organisation"
        logger.warning(
            "post-freeze watch for %s found %d rows but the org has no payroll contact",
            org.slug,
            rows,
        )
        return out
    names = {v.id: v.name for v in venues}
    venue_name = {
        oid: names.get(company_venues.get(str(v.get("loadedhub_company_id") or "")))
        or v.get("name")
        for oid, v in orbit_venues.items()
    }
    tables = [
        {
            "table": table,
            "rows": [
                {**r, "venue": venue_name.get(r.get("venue_id")) or "—"} for r in items
            ],
        }
        for table, items in found.items()
    ]
    if send is None:
        from app.services.email_service import send_system_email as send
    log_id = send(
        WATCH_TEMPLATE,
        [contact],
        {
            "org_name": org.name,
            "freeze_at": freeze_at.astimezone(_org_tz(org)).strftime(
                "%a %d %b %Y %H:%M"
            ),
            "today": now.astimezone(_org_tz(org)).strftime("%a %d %b %Y"),
            "total": rows,
            "tables": tables,
        },
        db,
        organization_id=org.id,
    )
    out["email"] = f"sent to the payroll contact (email log {log_id})"
    return out


def run_due(
    db: Session, now: dt.datetime | None = None, *, source=None, send=None
) -> dict:
    """Run both jobs for every organisation that is due. Called by the tick
    (``finance_core.tick``) in its own session; each run claims its
    (org, job, day) slot first, so overlapping ticks never repeat one."""
    now = now or dt.datetime.now(dt.timezone.utc)
    ran: list[dict] = []
    for org in db.query(Organization).all():
        modes = importer.org_modes(org)
        tz = _org_tz(org)
        local = now.astimezone(tz)
        slot = local.date().isoformat()
        shadow = [m for m in importer.MODULES if modes[m] == "shadow"]
        if shadow and local.time() >= NIGHTLY_AT:
            _run_one(
                db,
                org,
                JOB_NIGHTLY,
                slot,
                now,
                ran,
                lambda: nightly(db, org, now=now, source=source),
            )
        window = freeze_window(db, org.id)
        if window is not None:
            freeze_at, until = window
            if freeze_at.astimezone(tz).date() <= local.date() <= until:
                _run_one(
                    db,
                    org,
                    JOB_WATCH,
                    slot,
                    now,
                    ran,
                    lambda: post_freeze_watch(
                        db, org, now=now, source=source, send=send
                    ),
                )
    return {"ran": ran}


def _run_one(
    db: Session, org, job: str, slot: str, now: dt.datetime, ran: list, work
) -> None:
    run = claim(db, org_id=org.id, job=job, scope_id=ORG_SCOPE, slot=slot, now=now)
    if run is None:
        return
    # Claim, then commit, then work (jobs.py's rule): the slot row must be
    # visible to the other instances before the slow part starts.
    db.commit()
    entry = {"org": org.slug, "job": job, "slot": slot}
    try:
        # The work runs in a SAVEPOINT: a failure rolls back exactly what it
        # did and leaves the claim row (and the caller's session) intact.
        with db.begin_nested():
            entry["result"] = work()
        finish(db, run, now=now)
    except Exception as exc:  # noqa: BLE001 — recorded on the run row, never stops the tick
        logger.exception("%s failed for %s", job, org.slug)
        finish(db, run, error=str(exc), now=now)
        entry["error"] = str(exc)[:500]
    db.commit()
    ran.append(entry)
