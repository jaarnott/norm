"""Hygiene (plan §9 step 2): what payroll must fix IN LOADED before the close
— unapproved clock-ins, staff without a pay code, unfinished clock-ins that
were dropped, the salaried overview, staff with no remuneration timeline,
the week's public holidays — and the parity line against Loaded's own pay
summary.

The lists come from ``loadedhub.get_payrun view=hygiene`` read live (so the
Monday 20:00 email reflects the day's fixes, not the 07:30 snapshot); the
snapshot's own counts ride beside them. When Loaded cannot be read the lists
that can be rebuilt from the stored facts are, and the answer says it is not
live. Norm approves no clock-ins (Orbit never did either).
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy.orm import Session

from app.db.finance_models import FinJobRun
from app.db.models import EmailLog, Organization, Venue
from app.db.payroll_models import PayEntity, PayWeek
from app.services.finance_core.ctx import FinanceCtx
from app.services.finance_core.errors import FinanceError
from app.services.payroll import _email, snapshot as snapshot_svc, weeks as weeks_svc
from app.services.payroll import entities as entities_svc

logger = logging.getLogger(__name__)

TEMPLATE = "payroll_hygiene"
LIST_KEYS = (
    "unapproved_clockins",
    "staff_without_pay_code",
    "unfinished_clockins",
    "staff_without_timeline",
    "public_holiday_dates",
)


def from_facts(db: Session, snapshot) -> dict:
    """The lists a snapshot can answer on its own (no timeline list, no
    parity — those need Loaded)."""
    facts = snapshot_svc.facts_of(db, snapshot)
    worked = [f for f in facts if f.shift_type != "Leave"]
    return {
        "unapproved_clockins": [
            {
                "staff_name": f.staff_name,
                "clockin_at": f.clockin_at.isoformat() if f.clockin_at else None,
                "clockout_at": f.clockout_at.isoformat() if f.clockout_at else None,
            }
            for f in facts
            if f.shift_type == "Clockin" and f.is_approved is False
        ],
        "staff_without_pay_code": sorted(
            {f.staff_name or "?" for f in worked if not f.pay_code}
        ),
        "unfinished_clockins": [],
        "staff_without_timeline": [],
        "salaried_count": len(
            {f.staff_member_id for f in facts if f.remuneration_type == "Salary"}
        ),
        "public_holiday_dates": sorted(
            {
                f.trading_date.isoformat()
                for f in facts
                if f.ph_cost_cents > 0 and f.trading_date
            }
        ),
        "parity": None,
    }


def snapshot_report(db: Session, org_id: str, week_start: dt.date) -> dict:
    """ "snapshot taken for N of M entities; failed: …" — from the Monday job's
    claim rows for this week (one per entity)."""
    entities = db.query(PayEntity).filter(PayEntity.organization_id == org_id).all()
    on = [e for e in entities if e.payroll_on]
    names = snapshot_svc.venue_names(db, org_id)
    runs = {
        r.scope_id: r
        for r in db.query(FinJobRun)
        .filter(
            FinJobRun.organization_id == org_id,
            FinJobRun.job == "payroll.snapshot",
            FinJobRun.slot == week_start.isoformat(),
        )
        .all()
    }
    taken, failed = 0, []
    for e in on:
        run = runs.get(e.venue_id)
        if run is not None and run.finished_at is not None and not run.error:
            taken += 1
        else:
            failed.append(
                {
                    "venue_id": e.venue_id,
                    "venue_name": names.get(e.venue_id),
                    "error": (run.error if run is not None else "not run yet"),
                }
            )
    out = {
        "taken": taken,
        "of": len(on),
        "entities_total": len(entities),
        "failed": failed,
        "note": None,
    }
    if not on:
        # payroll_on starts false on every entity (D12 keeps AU off; the
        # owner decides whether a connected NZ venue should start on).
        out["note"] = (
            "No entity has payroll switched on yet — switch each venue that runs "
            "a pay run on under Payroll settings → Entities."
        )
    return out


def compute(db: Session, ctx: FinanceCtx, week: PayWeek, *, config_db=None) -> dict:
    """The hygiene picture for a week: live from Loaded when it answers, else
    what the snapshot knows."""
    ctx.require("payroll:read")
    venue = weeks_svc.venue_of(db, ctx, week.venue_id)
    current = weeks_svc.current_snapshot(db, week)
    out: dict = {
        "week_start": week.week_start.isoformat(),
        "venue_id": venue.id,
        "venue_name": venue.name,
        "snapshot": None,
        "snapshot_report": snapshot_report(db, ctx.org_id, week.week_start),
        "email": last_email(db, ctx.org_id, venue.id, week.week_start),
    }
    if current is not None:
        out["snapshot"] = {
            "id": current.id,
            "taken_at": current.taken_at.isoformat(),
            "source": current.source,
            "shift_count": current.shift_count,
            "dropped_deleted": current.dropped_deleted,
            "dropped_unfinished": current.dropped_unfinished,
        }
    try:
        result = snapshot_svc.payrun(
            db, ctx, venue, week.week_start, view="hygiene", config_db=config_db
        )
    except FinanceError as exc:
        out["live"] = False
        out["error"] = exc.message
        out.update(
            from_facts(db, current)
            if current is not None
            else {k: [] for k in LIST_KEYS}
        )
        return out
    section = (result.get("sections") or {}).get("hygiene") or {}
    out["live"] = True
    for key in LIST_KEYS:
        out[key] = section.get(key) or []
    out["salaried_count"] = section.get("salaried_count") or 0
    out["parity"] = section.get("parity") or result.get("parity")
    out["dropped"] = result.get("dropped") or {}
    out["notes"] = result.get("notes") or []
    return out


def last_email(db: Session, org_id: str, venue_id: str, week_start: dt.date) -> dict:
    log = (
        db.query(EmailLog)
        .filter(
            EmailLog.organization_id == org_id,
            EmailLog.venue_id == venue_id,
            EmailLog.template_name == TEMPLATE,
            EmailLog.subject.contains(week_start.isoformat()),
        )
        .order_by(EmailLog.created_at.desc())
        .first()
    )
    if log is None:
        return {"last_sent_at": None, "to": None, "status": None}
    return {
        "last_sent_at": (log.sent_at or log.created_at).isoformat()
        if (log.sent_at or log.created_at)
        else None,
        "to": (log.to_addresses or [None])[0],
        "status": log.status,
    }


def send_email(db: Session, ctx: FinanceCtx, week: PayWeek, *, config_db=None) -> dict:
    """The Monday 20:00 email (and the page's "send now"): the lists to the
    payroll contact, opening with the snapshot report. Nothing is sent when
    there is no contact (recorded as suppressed)."""
    ctx.require("payroll:read")
    venue = weeks_svc.venue_of(db, ctx, week.venue_id)
    org = db.query(Organization).filter(Organization.id == ctx.org_id).first()
    entity = entities_svc.get_or_create(db, ctx, venue.id)
    picture = compute(db, ctx, week, config_db=config_db)
    report = picture["snapshot_report"]
    context = {
        "org_name": org.name if org is not None else "",
        "venue_name": venue.name,
        "week_start": week.week_start.isoformat(),
        "week_ending": weeks_svc.week_ending(week.week_start).isoformat(),
        "week_label": f"week ending {weeks_svc.week_ending(week.week_start).strftime('%a %d %b %Y')}",
        "taken": report["taken"],
        "of": report["of"],
        "failed": report["failed"],
        "live": picture.get("live", False),
        "error": picture.get("error"),
        "unapproved": picture.get("unapproved_clockins") or [],
        "without_pay_code": picture.get("staff_without_pay_code") or [],
        "unfinished": picture.get("unfinished_clockins") or [],
        "without_timeline": picture.get("staff_without_timeline") or [],
        "salaried_count": picture.get("salaried_count") or 0,
        "public_holidays": picture.get("public_holiday_dates") or [],
        "parity": picture.get("parity"),
        "snapshot": picture.get("snapshot"),
        "subject": f"Payroll hygiene — {venue.name}, week {week.week_start.isoformat()}",
    }
    to = entities_svc.contact_email(org, entity)
    return _email.send(db, ctx, TEMPLATE, context, to=to, venue_id=venue.id)


def venue_for(db: Session, ctx: FinanceCtx, venue_id: str) -> Venue:
    return weeks_svc.venue_of(db, ctx, venue_id)
