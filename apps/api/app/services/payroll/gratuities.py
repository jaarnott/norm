"""Gratuities (plan §9 step 4, Orbit's rule kept): a monthly pool earned at a
venue, paid in one pay week, split pro rata on the hours people PHYSICALLY
WORKED at that venue across every pay week overlapping the period.

* Leave and security shifts are excluded; a shift counts by its trading
  date inside the period.
* Each share is tagged with the entity whose payroll pays it: the entity
  whose snapshot held the shift — Loaded's own attribution on the shift's
  date (``filterByPayrollVenue``), so a share paid by another entity is an
  intercompany recharge.
* Staff no longer active in Loaded keep their share but it is not payable
  (and not redistributed); staff a person excludes get nothing and their
  share IS redistributed.
* Cents by largest remainder, so the shares always add to the pool (Orbit
  rounded each share and let the batch drift by a few cents — parity class
  ``gratuity_residual``).

The hours come from each entity's CURRENT snapshot of each overlapping week,
so a batch keyed on the 1st of the month reads the same facts the payrun did.
"""

from __future__ import annotations

import calendar
import datetime as dt
import logging

from sqlalchemy.orm import Session

from app.db.models import Organization, Venue, _now
from app.db.payroll_models import (
    PayEntity,
    PayGratuityAllocation,
    PayGratuityBatch,
    PayShiftFact,
    PaySnapshot,
    PayWeek,
)
from app.services.finance_core.ctx import FinanceCtx, require_venue
from app.services.finance_core.errors import FinanceError, Invalid, NotFound, flush
from app.services.functions.money import largest_remainder
from app.services.payroll import _email, snapshot as snapshot_svc, weeks as weeks_svc
from app.services.payroll import entities as entities_svc

logger = logging.getLogger(__name__)

REMINDER_TEMPLATE = "payroll_gratuity_reminder"


def period_bounds(period: str) -> tuple[dt.date, dt.date]:
    try:
        year, month = (int(p) for p in str(period).split("-", 1))
        first = dt.date(year, month, 1)
    except (TypeError, ValueError):
        raise Invalid("period must be a month as YYYY-MM.")
    last = dt.date(year, month, calendar.monthrange(year, month)[1])
    return first, last


def weeks_for_period(period: str) -> list[dt.date]:
    """Every pay week (Monday) overlapping the calendar month."""
    first, last = period_bounds(period)
    monday = weeks_svc.monday_of(first)
    out = []
    while monday <= last:
        out.append(monday)
        monday += dt.timedelta(days=7)
    return out


def _cents(value, what: str) -> int:
    if isinstance(value, bool):
        raise Invalid(f"{what} must be a whole number of cents.")
    try:
        out = int(value)
    except (TypeError, ValueError):
        raise Invalid(f"{what} must be a whole number of cents.")
    if out < 0:
        raise Invalid(f"{what} cannot be negative.")
    return out


# ================================================================== split ==


def split(rows: list[dict], total_cents: int) -> list[int]:
    """Amounts per row: hours pro rata over the non-excluded rows, cents by
    largest remainder; an excluded row gets 0. Inactive rows keep theirs."""
    included = [i for i, r in enumerate(rows) if not r.get("excluded")]
    amounts = [0] * len(rows)
    if not included or total_cents <= 0:
        return amounts
    weights = [int(round(float(rows[i]["hours"]) * 100)) for i in included]
    if sum(weights) <= 0:
        return amounts
    for i, amount in zip(included, largest_remainder(weights, total_cents)):
        amounts[i] = amount
    return amounts


# ================================================================ preview ==


def _inactive_staff(db, ctx, venue, config_db) -> tuple[set[str], str | None]:
    """Loaded staff ids marked deleted at the paying venue; a failed read
    means everyone is treated as payable (with a warning — Orbit's rule)."""
    try:
        payload = snapshot_svc.loaded(
            db,
            ctx,
            venue,
            "get_staff_members",
            {"include_deleted": "true", "include_last_clocks": "false"},
            config_db=config_db,
        )
    except FinanceError as exc:
        return set(), (
            "Loaded could not confirm staff activity for this venue — everyone is "
            f"treated as payable ({exc.message})."
        )
    rows = payload if isinstance(payload, list) else []
    if isinstance(payload, dict):
        rows = payload.get("staff") or payload.get("rows") or payload.get("data") or []
        if not rows:
            for value in payload.values():
                if isinstance(value, list):
                    rows = value
                    break
    inactive = {
        str(r.get("id"))
        for r in rows
        if isinstance(r, dict) and r.get("id") is not None and r.get("datestampDeleted")
    }
    return inactive, None


def preview(
    db: Session,
    ctx: FinanceCtx,
    *,
    venue_id: str,
    period: str,
    week_start,
    total_cents: int,
    excluded_staff_ids=(),
    config_db=None,
) -> dict:
    """The split as it would be stored."""
    ctx.require("payroll:read")
    venue = weeks_svc.venue_of(db, ctx, venue_id)
    week_start = weeks_svc.validate_week_start(week_start)
    total = _cents(total_cents, "total_cents")
    first, last = period_bounds(period)
    excluded = {str(s) for s in (excluded_staff_ids or []) if str(s).strip()}
    names = snapshot_svc.venue_names(db, ctx.org_id)
    entity = entities_svc.get_or_create(db, ctx, venue.id)
    security_code = entity.security_pay_code or "SECURITY"

    weeks = weeks_for_period(period)
    missing: list[str] = []
    by_staff: dict[str, dict] = {}
    for monday in weeks:
        week_rows = (
            db.query(PayWeek, PaySnapshot)
            .join(
                PaySnapshot,
                (PaySnapshot.week_id == PayWeek.id)
                & (PaySnapshot.organization_id == PayWeek.organization_id),
            )
            .filter(
                PayWeek.organization_id == ctx.org_id,
                PayWeek.week_start == monday,
                PaySnapshot.superseded_at.is_(None),
            )
            .all()
        )
        if not week_rows:
            missing.append(monday.isoformat())
        for week, snap in week_rows:
            facts = (
                db.query(PayShiftFact)
                .filter(
                    PayShiftFact.organization_id == ctx.org_id,
                    PayShiftFact.snapshot_id == snap.id,
                    PayShiftFact.shift_type != "Leave",
                    PayShiftFact.trading_date >= first,
                    PayShiftFact.trading_date <= last,
                )
                .all()
            )
            for f in facts:
                if (f.pay_code or "") == security_code:
                    continue
                worked_here = f.worked_venue_id == venue.id or (
                    f.worked_venue_id is None
                    and entity.loaded_company_id
                    and f.worked_venue_loaded_id == entity.loaded_company_id
                )
                if not worked_here:
                    continue
                hours = float(f.hours or 0)
                if hours <= 0:
                    continue
                key = f.staff_member_id or f.staff_name or "unknown"
                row = by_staff.get(key)
                if row is None:
                    row = by_staff[key] = {
                        "staff_member_id": f.staff_member_id,
                        "staff_name": f.staff_name or "Unknown",
                        "pay_code": f.pay_code,
                        "hours": 0.0,
                        "payroll_venue_id": week.venue_id,
                        "payroll_venue_name": names.get(week.venue_id),
                        "is_payable": True,
                        "excluded": key in excluded,
                    }
                row["hours"] = round(row["hours"] + hours, 2)
                if not row["pay_code"] and f.pay_code:
                    row["pay_code"] = f.pay_code

    rows = sorted(by_staff.values(), key=lambda r: (r["staff_name"] or "").casefold())
    warnings = []
    if missing:
        warnings.append(
            "No snapshot for pay week(s) " + ", ".join(missing) + " — hours worked "
            "in them are not in this split; take those snapshots first."
        )
    inactive, warning = _inactive_staff(db, ctx, venue, config_db)
    if warning:
        warnings.append(warning)
    for r in rows:
        r["is_payable"] = not (
            r["staff_member_id"] and r["staff_member_id"] in inactive
        )
    for r, amount in zip(rows, split(rows, total)):
        r["amount_cents"] = amount
    return _summary(rows, total, venue.id, names, warnings, weeks, missing)


def _summary(rows, total, venue_id, names, warnings, weeks, missing) -> dict:
    payable = [r for r in rows if r["is_payable"] and not r.get("excluded")]
    by_venue: dict[str, dict] = {}
    for r in payable:
        acc = by_venue.setdefault(
            r["payroll_venue_id"] or "",
            {
                "venue_id": r["payroll_venue_id"],
                "venue_name": names.get(r["payroll_venue_id"] or ""),
                "amount_cents": 0,
                "hours": 0.0,
            },
        )
        acc["amount_cents"] += r["amount_cents"]
        acc["hours"] = round(acc["hours"] + r["hours"], 2)
    return {
        "rows": rows,
        "total_cents": total,
        "payable_cents": sum(r["amount_cents"] for r in payable),
        "forfeited_cents": sum(
            r["amount_cents"]
            for r in rows
            if not r["is_payable"] and not r.get("excluded")
        ),
        "intercompany_cents": sum(
            r["amount_cents"] for r in payable if r["payroll_venue_id"] != venue_id
        ),
        "by_payroll_venue": sorted(
            by_venue.values(), key=lambda v: v["venue_name"] or ""
        ),
        "weeks": [w.isoformat() for w in weeks],
        "weeks_without_snapshot": missing,
        "warnings": warnings,
    }


# ================================================================ batches ==


def create_batch(
    db: Session,
    ctx: FinanceCtx,
    *,
    venue_id: str,
    period: str,
    week_start,
    total_cents: int,
    gl_code: str | None = None,
    notes: str | None = None,
    excluded_staff_ids=(),
    rehearsal: bool = False,
    config_db=None,
) -> PayGratuityBatch:
    ctx.require("payroll:write")
    weeks_svc.require_writable(ctx, rehearsal=rehearsal)
    picture = preview(
        db,
        ctx,
        venue_id=venue_id,
        period=period,
        week_start=week_start,
        total_cents=total_cents,
        excluded_staff_ids=excluded_staff_ids,
        config_db=config_db,
    )
    if not picture["rows"]:
        raise Invalid(
            "Nobody worked at this venue in that period according to the snapshots "
            "— nothing to split."
        )
    batch = PayGratuityBatch(
        organization_id=ctx.org_id,
        venue_id=venue_id,
        week_start=weeks_svc.validate_week_start(week_start),
        period=str(period),
        total_cents=_cents(total_cents, "total_cents"),
        gl_code=(gl_code or "").strip() or None,
        notes=(notes or "").strip() or None,
        created_by=ctx.user_id,
        source="norm",
        rehearsal=bool(rehearsal),
    )
    db.add(batch)
    flush(db)
    now = _now()
    db.add_all(
        PayGratuityAllocation(
            organization_id=ctx.org_id,
            batch_id=batch.id,
            staff_member_id=r["staff_member_id"] or r["staff_name"],
            staff_name=r["staff_name"],
            pay_code=r["pay_code"],
            hours=r["hours"],
            amount_cents=r["amount_cents"],
            is_payable=r["is_payable"],
            payroll_venue_id=r["payroll_venue_id"],
            excluded_at=now if r.get("excluded") else None,
            excluded_by=ctx.user_id if r.get("excluded") else None,
        )
        for r in picture["rows"]
    )
    flush(db)
    return batch


def get_batch(db: Session, ctx: FinanceCtx, batch_id: str) -> PayGratuityBatch:
    batch = (
        db.query(PayGratuityBatch)
        .filter(
            PayGratuityBatch.organization_id == ctx.org_id,
            PayGratuityBatch.id == batch_id,
        )
        .first()
    )
    if batch is None or not ctx.can_see_venue(batch.venue_id):
        raise NotFound("Gratuity batch not found.")
    return batch


def allocations_of(db: Session, batch: PayGratuityBatch) -> list[PayGratuityAllocation]:
    return (
        db.query(PayGratuityAllocation)
        .filter(
            PayGratuityAllocation.organization_id == batch.organization_id,
            PayGratuityAllocation.batch_id == batch.id,
        )
        .order_by(PayGratuityAllocation.staff_name)
        .all()
    )


def list_batches(
    db: Session, ctx: FinanceCtx, *, venue_id: str | None = None, week_start=None
) -> list[PayGratuityBatch]:
    q = db.query(PayGratuityBatch).filter(
        PayGratuityBatch.organization_id == ctx.org_id
    )
    if venue_id:
        require_venue(ctx, venue_id)
        q = q.filter(PayGratuityBatch.venue_id == venue_id)
    else:
        q = q.filter(ctx.venue_clause(PayGratuityBatch.venue_id))
    if week_start is not None:
        q = q.filter(
            PayGratuityBatch.week_start == weeks_svc.validate_week_start(week_start)
        )
    return q.order_by(
        PayGratuityBatch.week_start.desc(), PayGratuityBatch.period.desc()
    ).all()


def exclude(
    db: Session, ctx: FinanceCtx, batch_id: str, allocation_id: str
) -> PayGratuityBatch:
    """Exclude one person's share and redistribute it."""
    ctx.require("payroll:write")
    batch = get_batch(db, ctx, batch_id)
    weeks_svc.require_writable(ctx, rehearsal=bool(batch.rehearsal))
    rows = allocations_of(db, batch)
    target = next((a for a in rows if a.id == allocation_id), None)
    if target is None:
        raise NotFound("Allocation not found.")
    if target.excluded_at is None:
        target.excluded_at = _now()
        target.excluded_by = ctx.user_id
    shaped = [
        {"hours": float(a.hours or 0), "excluded": a.excluded_at is not None}
        for a in rows
    ]
    for a, amount in zip(rows, split(shaped, batch.total_cents)):
        a.amount_cents = amount
    flush(db)
    return batch


def delete_batch(db: Session, ctx: FinanceCtx, batch_id: str) -> None:
    ctx.require("payroll:write")
    batch = get_batch(db, ctx, batch_id)
    weeks_svc.require_writable(ctx, rehearsal=bool(batch.rehearsal))
    db.delete(batch)
    flush(db)


def payable_by(
    db: Session,
    ctx: FinanceCtx,
    payroll_venue_id: str,
    week_start,
    *,
    include_rehearsal: bool = False,
) -> list[tuple[PayGratuityBatch, PayGratuityAllocation]]:
    """The shares this entity's payroll pays in the week — earned here or at
    another venue (an intercompany recharge). Rehearsal batches never feed an
    export unless asked for."""
    week_start = weeks_svc.validate_week_start(week_start)
    q = (
        db.query(PayGratuityBatch, PayGratuityAllocation)
        .join(
            PayGratuityAllocation,
            (PayGratuityAllocation.batch_id == PayGratuityBatch.id)
            & (
                PayGratuityAllocation.organization_id
                == PayGratuityBatch.organization_id
            ),
        )
        .filter(
            PayGratuityBatch.organization_id == ctx.org_id,
            PayGratuityBatch.week_start == week_start,
            PayGratuityAllocation.payroll_venue_id == payroll_venue_id,
            PayGratuityAllocation.is_payable.is_(True),
            PayGratuityAllocation.excluded_at.is_(None),
        )
    )
    if not include_rehearsal:
        q = q.filter(PayGratuityBatch.rehearsal.is_(False))
    return q.order_by(PayGratuityAllocation.staff_name).all()


def batch_dict(
    db: Session, batch: PayGratuityBatch, names: dict[str, str] | None = None
) -> dict:
    names = names or snapshot_svc.venue_names(db, batch.organization_id)
    rows = allocations_of(db, batch)
    payable = [a for a in rows if a.is_payable and a.excluded_at is None]
    return {
        "id": batch.id,
        "venue_id": batch.venue_id,
        "venue_name": names.get(batch.venue_id),
        "week_start": batch.week_start.isoformat(),
        "period": batch.period,
        "total_cents": batch.total_cents,
        "gl_code": batch.gl_code,
        "notes": batch.notes,
        "created_by": batch.created_by,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
        "source": batch.source,
        "rehearsal": batch.rehearsal,
        "allocations": [
            {
                "id": a.id,
                "staff_member_id": a.staff_member_id,
                "staff_name": a.staff_name,
                "pay_code": a.pay_code,
                "hours": float(a.hours or 0),
                "amount_cents": a.amount_cents,
                "is_payable": a.is_payable,
                "payroll_venue_id": a.payroll_venue_id,
                "payroll_venue_name": names.get(a.payroll_venue_id or ""),
                "excluded_at": a.excluded_at.isoformat() if a.excluded_at else None,
                "excluded_by": a.excluded_by,
            }
            for a in rows
        ],
        "payable_cents": sum(a.amount_cents for a in payable),
        "forfeited_cents": sum(
            a.amount_cents for a in rows if not a.is_payable and a.excluded_at is None
        ),
        "intercompany_cents": sum(
            a.amount_cents for a in payable if a.payroll_venue_id != batch.venue_id
        ),
        # Σ shares and what the pool is short or over by: zero for a batch Norm
        # split; three Orbit batches arrive off their pool (F7) and show it here.
        "shares_cents": sum(a.amount_cents for a in rows),
        "pool_residual_cents": batch.total_cents - sum(a.amount_cents for a in rows),
    }


# =============================================================== reminder ==


def send_reminder(db: Session, ctx: FinanceCtx, venue: Venue, period: str) -> dict:
    """The 1st-of-the-month note: enter last month's pool for this venue."""
    org = db.query(Organization).filter(Organization.id == ctx.org_id).first()
    entity = (
        db.query(PayEntity)
        .filter(PayEntity.organization_id == ctx.org_id, PayEntity.venue_id == venue.id)
        .first()
    )
    first, _ = period_bounds(period)
    context = {
        "org_name": org.name if org else "",
        "venue_name": venue.name,
        "period": period,
        "period_label": first.strftime("%B %Y"),
        "allowance_code": getattr(entity, "ace_gratuity_allowance_code", None),
        "subject": f"Gratuities for {first.strftime('%B %Y')} — {venue.name}",
    }
    to = entities_svc.contact_email(org, entity)
    return _email.send(db, ctx, REMINDER_TEMPLATE, context, to=to, venue_id=venue.id)
