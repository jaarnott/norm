"""The entertainers' tick jobs (DESIGN-CONTRACT §6), claimed through
``finance_core.jobs``:

* ``ent.morning`` — 08:00 organisation-local, once per org-day: materialise
  the series a year ahead; the closing-manager lookup per venue; the
  approval emails and their reminders; the performer confirmation
  reminders; band-member and onboarding-invite reminders; then the venue
  digest. One step failing never stops the next; the run records the
  errors and the digest still goes.
* ``ent.invoice_checks`` — every tick, for invoices uploaded and not yet
  checked, at most 100 per organisation per day.

Both run in shadow as well as live: Norm's own reads and rows are not
customer-facing writes, and every email in shadow goes to the payroll
contact (``emails.deliver``).
"""

from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.db.entertainers_models import EntBandMember, EntPerformer
from app.db.finance_models import FinJobRun
from app.db.models import Organization, _now
from app.services.entertainers import (
    closing_manager,
    email_links,
    emails,
    invoice_check,
)
from app.services.entertainers import performances as performances_svc
from app.services.entertainers import series as series_svc
from app.services.entertainers import tokens
from app.services.entertainers._shared import DEFAULT_TZ, settings_for
from app.services.finance_core import jobs as core_jobs

logger = logging.getLogger(__name__)

MORNING_HOUR = 8
INVITE_KINDS = ("ent_invite", "ent_invite_reminder")
BAND_KINDS = ("ent_band_invite", "ent_band_reminder")


def _orgs(db: Session) -> list[Organization]:
    return (
        db.query(Organization)
        .filter(Organization.entertainers_mode.in_(("shadow", "live")))
        .all()
    )


def _local(org: Organization, now: dt.datetime) -> dt.datetime:
    name = getattr(org, "default_timezone", None) or DEFAULT_TZ
    try:
        return now.astimezone(ZoneInfo(name))
    except Exception:  # noqa: BLE001 — a bad zone name on the row
        return now.astimezone(ZoneInfo(DEFAULT_TZ))


def run_due(db: Session, now: dt.datetime | None = None) -> dict:
    now = now or _now()
    out: dict = {"morning": {}, "invoice_checks": {}}
    for org in _orgs(db):
        local = _local(org, now)
        if local.hour >= MORNING_HOUR:
            run = core_jobs.claim(
                db,
                org_id=org.id,
                job="ent.morning",
                scope_id=core_jobs.ORG_SCOPE,
                slot=local.date().isoformat(),
                now=now,
            )
            if run is not None:
                db.commit()  # the claim must be visible before the slow part
                out["morning"][org.id] = _morning(db, org, run, now)
        out["invoice_checks"][org.id] = _invoice_checks(db, org, now)
    return out


def _morning(db: Session, org: Organization, run: FinJobRun, now: dt.datetime) -> dict:
    report: dict = {}
    errors: list[str] = []

    def step(name, fn):
        # A savepoint per step: a failure undoes only that step (a session
        # rollback would also discard what the caller has not committed),
        # and the next step still runs.
        try:
            with db.begin_nested():
                report[name] = fn()
            db.commit()
        except Exception as exc:  # noqa: BLE001 — recorded; the next step runs
            logger.exception("ent.morning %s failed for %s", name, org.id)
            errors.append(f"{name}: {str(exc)[:200]}")

    step("series", lambda: series_svc.extend_all(db, org.id, today=now.date()))
    step(
        "closing_manager",
        lambda: _compact(closing_manager.resolve_for_org(db, org.id, now=now)),
    )
    buckets: dict = {}

    def _approvals():
        buckets.update(closing_manager.send_approval_requests(db, org.id, now=now))
        return {k: (v if isinstance(v, int) else len(v)) for k, v in buckets.items()}

    step("approval_emails", _approvals)
    step(
        "confirm_reminders",
        lambda: performances_svc.send_confirm_reminders(db, org.id, now=now),
    )
    step("band_reminders", lambda: _band_reminders(db, org.id, now))
    step("invite_reminders", lambda: _invite_reminders(db, org.id, now))
    step("digest", lambda: closing_manager.send_digest(db, org.id, buckets, now=now))
    core_jobs.finish(db, run, error="; ".join(errors) if errors else None, now=_now())
    db.commit()
    report["errors"] = errors
    return report


def _compact(report: dict) -> dict:
    return {k: (len(v) if isinstance(v, list) else v) for k, v in report.items()}


def _invite_reminders(db: Session, org_id: str, now: dt.datetime) -> int:
    """Invited performers who have not submitted: a reminder every
    ``reminder_interval_days``, ``reminder_max`` in all, carrying the link
    they already hold (minting a new one when none can be shown again)."""
    settings_row = settings_for(db, org_id)
    rows = (
        db.query(EntPerformer)
        .filter(
            EntPerformer.organization_id == org_id,
            EntPerformer.onboarding_status == "invited",
            EntPerformer.is_active.is_(True),
            EntPerformer.invite_sent_at.is_not(None),
            EntPerformer.email.is_not(None),
        )
        .all()
    )
    sent = 0
    for performer in rows:
        count = email_links.send_count(db, org_id, INVITE_KINDS, target_id=performer.id)
        if count >= settings_row.reminder_max:
            continue
        last = email_links.last_sent_at(
            db, org_id, INVITE_KINDS, target_id=performer.id
        )
        if last is not None and now - last < dt.timedelta(
            days=settings_row.reminder_interval_days
        ):
            continue
        url = tokens.current_url(
            db, org_id=org_id, purpose="onboarding", performer_id=performer.id, now=now
        )
        if url is None:
            _row, _token, url = tokens.mint(
                db,
                org_id=org_id,
                purpose="onboarding",
                performer_id=performer.id,
                now=now,
            )
        performer.invite_reminders_sent = (performer.invite_reminders_sent or 0) + 1
        performer.last_reminder_at = now
        db.flush()
        emails.deliver(
            db,
            org_id=org_id,
            kind="ent_invite_reminder",
            to=performer.email,
            context={
                "name": performer.name,
                "first_name": emails.first_name(performer.name),
                "url": url,
                "expires_days": tokens.TTL.days,
                "update": False,
            },
            performer_id=performer.id,
            target_id=performer.id,
            now=now,
        )
        sent += 1
    return sent


def _band_reminders(db: Session, org_id: str, now: dt.datetime) -> int:
    settings_row = settings_for(db, org_id)
    rows = (
        db.query(EntBandMember)
        .filter(
            EntBandMember.organization_id == org_id,
            EntBandMember.member_performer_id.is_(None),
            EntBandMember.removed_at.is_(None),
            EntBandMember.invite_sent_at.is_not(None),
            EntBandMember.email.is_not(None),
        )
        .all()
    )
    sent = 0
    for member in rows:
        count = email_links.send_count(db, org_id, BAND_KINDS, target_id=member.id)
        if count >= settings_row.reminder_max:
            continue
        last = email_links.last_sent_at(db, org_id, BAND_KINDS, target_id=member.id)
        if last is not None and now - last < dt.timedelta(
            days=settings_row.reminder_interval_days
        ):
            continue
        band = (
            db.query(EntPerformer)
            .filter(EntPerformer.id == member.band_performer_id)
            .first()
        )
        if band is None or not band.is_active:
            continue
        url = tokens.current_url(
            db,
            org_id=org_id,
            purpose="band_member",
            performer_id=band.id,
            band_member_id=member.id,
            now=now,
        )
        if url is None:
            _row, _token, url = tokens.mint(
                db,
                org_id=org_id,
                purpose="band_member",
                performer_id=band.id,
                band_member_id=member.id,
                now=now,
            )
        member.reminders_sent = (member.reminders_sent or 0) + 1
        member.last_reminder_at = now
        db.flush()
        emails.deliver(
            db,
            org_id=org_id,
            kind="ent_band_reminder",
            to=member.email,
            context={
                "name": member.name,
                "first_name": emails.first_name(member.name),
                "band_name": band.name,
                "url": url,
                "expires_days": tokens.TTL.days,
            },
            performer_id=band.id,
            band_member_id=member.id,
            target_id=member.id,
            now=now,
        )
        sent += 1
    return sent


def _checks_today(db: Session, org_id: str, now: dt.datetime) -> int:
    start = dt.datetime.combine(
        now.astimezone(dt.timezone.utc).date(), dt.time.min, dt.timezone.utc
    )
    return (
        db.query(FinJobRun)
        .filter(
            FinJobRun.organization_id == org_id,
            FinJobRun.job == "ent.invoice_checks",
            FinJobRun.started_at >= start,
        )
        .count()
    )


def _invoice_checks(
    db: Session, org: Organization, now: dt.datetime, *, reader=None
) -> dict:
    """Pending invoice files, at most 100 claims per org per day. The claim
    is per file and day, so a file left pending (the reader was down) is
    tried again tomorrow and "check now" any time."""
    done = failed = 0
    budget = invoice_check.CHECKS_PER_ORG_PER_DAY - _checks_today(db, org.id, now)
    if budget <= 0:
        return {"checked": 0, "skipped": "daily cap"}
    for file in invoice_check.pending_files(db, org.id, limit=budget):
        run = core_jobs.claim(
            db,
            org_id=org.id,
            job="ent.invoice_checks",
            scope_id=file.id,
            slot=now.date().isoformat(),
            now=now,
        )
        if run is None:
            continue
        db.commit()
        try:
            with db.begin_nested():
                invoice_check.run_check(db, file, now=now, reader=reader)
            core_jobs.finish(db, run, now=_now())
            db.commit()
            done += 1
        except Exception as exc:  # noqa: BLE001 — recorded on the run
            logger.exception("ent.invoice_checks failed for file %s", file.id)
            core_jobs.finish(db, run, error=str(exc)[:500], now=_now())
            db.commit()
            failed += 1
    return {"checked": done, "failed": failed}
