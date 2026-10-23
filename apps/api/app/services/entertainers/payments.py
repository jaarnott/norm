"""The payments ledger (plan §11.1, D15) and the payroll feed F3 reads.

Mark paid records who, when and the reference (Orbit never recorded
``paid_by``); undo reverses it. Both need ``payroll:write`` and a live
module — in shadow head office keeps paying from Orbit. Marking a
performance paid revokes the performer's portal links (plan §11.4).

``payroll_lines`` is the DESIGN-CONTRACT §5 interface: approved, unpaid
payroll-lane performances at a venue (carried forward until paid) plus the
ones paid in the given week, each as a ``Line`` with hours, rate and amount
— for ``IMPORT_ENTERTAINERS.TXT`` and the journal's entertainer term. A
performer who is not payable yields no line, and says so in ``skipped``;
a mismatch yields none either (the roster resolves it first).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db.entertainers_models import (
    EntBandMember,
    EntFile,
    EntPerformance,
    EntPerformanceShare,
    EntPerformer,
)
from app.db.models import Venue, _now
from app.services.entertainers import performances as performances_svc
from app.services.entertainers import status as status_svc
from app.services.entertainers import tokens
from app.services.entertainers._shared import (
    check_version,
    hours_of,
    load,
    load_performance,
    real_minutes,
    settings_for,
    venue_tz,
)
from app.services.entertainers.performers import members_of, payable
from app.services.finance_core.ctx import FinanceCtx, require_staff, require_venue
from app.services.finance_core.errors import Conflict, Invalid, NotFound, flush

PAYROLL_LANES = ("payroll_employee", "payroll_contractor")


@dataclass(frozen=True)
class Line:
    performance_id: str
    share_id: str | None
    performer_id: str
    performer_name: str
    lane: str
    payroll_code: str | None
    pay_code: str | None
    performance_date: dt.date
    venue_id: str
    minutes: int
    hours: Decimal
    rate_cents: int
    amount_cents: int
    payment_status: str
    paid_week_start: dt.date | None


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _bill_of(
    db: Session, org_id: str, performance_id: str, member_id: str | None = None
) -> dict | None:
    from app.services.entertainers.bills import live_push, push_dict

    push = live_push(db, org_id, performance_id, member_id)
    return push_dict(push) if push else None


def list_payments(
    db: Session,
    ctx: FinanceCtx,
    *,
    venue_id: str | None = None,
    status: str = "pending",
    since: dt.date | None = None,
    until: dt.date | None = None,
    lane: str | None = None,
    now: dt.datetime | None = None,
) -> dict:
    require_staff(ctx)
    ctx.require("payroll:write")
    if venue_id is not None:
        require_venue(ctx, venue_id)
    q = db.query(EntPerformance).filter(
        EntPerformance.organization_id == ctx.org_id,
        EntPerformance.performance_status == "approved",
        EntPerformance.venue_id.in_(sorted(ctx.venue_ids))
        if venue_id is None
        else EntPerformance.venue_id == venue_id,
    )
    if status in ("pending", "paid"):
        q = q.filter(EntPerformance.payment_status == status)
    elif status != "all":
        raise Invalid("status must be pending, paid or all.")
    if since:
        q = q.filter(EntPerformance.performance_date >= since)
    if until:
        q = q.filter(EntPerformance.performance_date <= until)
    rows = q.order_by(EntPerformance.performance_date, EntPerformance.start_time).all()
    names = performances_svc._names(db, ctx.org_id, rows)
    tol = settings_for(db, ctx.org_id).mismatch_tolerance_minutes
    today = _now().date()
    out = []
    for perf in rows:
        performer = names["performer"].get(perf.performer_id)
        if lane and (performer is None or performer.lane != lane):
            continue
        item = performances_svc.performance_dict(
            db, ctx, perf, names=names, tolerance=tol, now=now
        )
        ok, why = (
            payable(db, performer, today=today) if performer else (False, "missing")
        )
        item["performer"] = {
            "lane": performer.lane if performer else None,
            "band_mode": performer.band_mode if performer else None,
            "gst_registered": performer.gst_registered if performer else None,
            "payable": ok,
            "payable_reason": why,
        }
        invoice = None
        if perf.invoice_file_id:
            from app.services.entertainers.invoice_check import file_dict

            file = db.query(EntFile).filter(EntFile.id == perf.invoice_file_id).first()
            invoice = file_dict(db, ctx, file) if file else None
        item["invoice"] = invoice
        shares = (
            db.query(EntPerformanceShare)
            .filter(
                EntPerformanceShare.organization_id == ctx.org_id,
                EntPerformanceShare.performance_id == perf.id,
            )
            .all()
        )
        item["shares"] = [
            {
                **performances_svc.share_dict(db, s),
                "bill": _bill_of(db, ctx.org_id, perf.id, s.band_member_id),
            }
            for s in shares
        ] or None
        item["bill"] = _bill_of(db, ctx.org_id, perf.id)
        out.append(item)
    return {
        "rows": out,
        "totals": {
            "count": len(out),
            "pending_cents": sum(
                r["actual_cents"] or 0 for r in out if r["payment_status"] == "pending"
            ),
            "paid_cents": sum(
                r["actual_cents"] or 0 for r in out if r["payment_status"] == "paid"
            ),
        },
    }


# ---------------------------------------------------------------------------
# Mark paid
# ---------------------------------------------------------------------------


def _require_can_pay(ctx: FinanceCtx) -> None:
    require_staff(ctx)
    ctx.require("payroll:write")
    ctx.require_live("entertainers")


def _payable_now(db: Session, ctx: FinanceCtx, perf: EntPerformance) -> EntPerformer:
    if perf.performance_status != "approved":
        raise Conflict("Only an approved performance can be paid.")
    tol = settings_for(db, ctx.org_id).mismatch_tolerance_minutes
    if status_svc.times_match(perf, tol) is False:
        raise Conflict(
            "The performer's times differ from the approved ones — resolve the mismatch on the roster first."
        )
    performer = load(db, ctx, EntPerformer, perf.performer_id, label="Performer")
    ok, why = payable(db, performer)
    if not ok:
        raise Conflict(f"This performer isn't payable yet: {why}.")
    return performer


def mark_paid(
    db: Session,
    ctx: FinanceCtx,
    performance_id: str,
    *,
    reference: str | None = None,
    version: int | None,
    week_start: dt.date | None = None,
    now: dt.datetime | None = None,
) -> EntPerformance:
    _require_can_pay(ctx)
    now = now or _now()
    perf = load_performance(db, ctx, performance_id, for_update=True)
    check_version(perf, version)
    if perf.payment_status == "paid":
        raise Conflict("Already marked paid.")
    _payable_now(db, ctx, perf)
    perf.payment_status = "paid"
    perf.paid_at = now
    perf.paid_by = ctx.user_id
    perf.paid_reference = (" ".join(str(reference or "").split()) or None) and str(
        reference
    )[:200]
    perf.payroll_week_start = week_start
    for share in db.query(EntPerformanceShare).filter(
        EntPerformanceShare.organization_id == ctx.org_id,
        EntPerformanceShare.performance_id == perf.id,
        EntPerformanceShare.paid_at.is_(None),
    ):
        share.paid_at, share.paid_by, share.paid_reference = (
            now,
            ctx.user_id,
            perf.paid_reference,
        )
    tokens.revoke_for_performer(
        db, ctx.org_id, perf.performer_id, purposes=("portal",), now=now
    )
    flush(db)
    return perf


def undo_paid(
    db: Session, ctx: FinanceCtx, performance_id: str, *, version: int | None
) -> EntPerformance:
    _require_can_pay(ctx)
    perf = load_performance(db, ctx, performance_id, for_update=True)
    check_version(perf, version)
    if perf.payment_status != "paid":
        raise Conflict("This performance isn't marked paid.")
    perf.payment_status = "pending"
    perf.paid_at = perf.paid_by = perf.paid_reference = None
    perf.payroll_week_start = None
    for share in db.query(EntPerformanceShare).filter(
        EntPerformanceShare.organization_id == ctx.org_id,
        EntPerformanceShare.performance_id == perf.id,
    ):
        share.paid_at = share.paid_by = share.paid_reference = None
    flush(db)
    return perf


def _share(
    db: Session, ctx: FinanceCtx, perf: EntPerformance, member_id: str
) -> EntPerformanceShare:
    share = (
        db.query(EntPerformanceShare)
        .filter(
            EntPerformanceShare.organization_id == ctx.org_id,
            EntPerformanceShare.performance_id == perf.id,
            EntPerformanceShare.band_member_id == member_id,
        )
        .with_for_update()
        .first()
    )
    if share is None:
        raise NotFound("Share not found.")
    return share


def mark_share_paid(
    db: Session,
    ctx: FinanceCtx,
    performance_id: str,
    member_id: str,
    *,
    reference=None,
    now=None,
) -> EntPerformanceShare:
    _require_can_pay(ctx)
    now = now or _now()
    perf = load_performance(db, ctx, performance_id, for_update=True)
    share = _share(db, ctx, perf, member_id)
    if share.paid_at is not None:
        raise Conflict("Already marked paid.")
    if share.member_performer_id:
        member = load(
            db, ctx, EntPerformer, share.member_performer_id, label="Performer"
        )
        ok, why = payable(db, member)
        if not ok:
            raise Conflict(f"This member isn't payable yet: {why}.")
    share.paid_at, share.paid_by = now, ctx.user_id
    share.paid_reference = (" ".join(str(reference or "").split()) or None) and str(
        reference
    )[:200]
    flush(db)
    remaining = (
        db.query(EntPerformanceShare)
        .filter(
            EntPerformanceShare.performance_id == perf.id,
            EntPerformanceShare.paid_at.is_(None),
        )
        .count()
    )
    if remaining == 0 and perf.payment_status != "paid":
        perf.payment_status, perf.paid_at, perf.paid_by = "paid", now, ctx.user_id
        tokens.revoke_for_performer(
            db, ctx.org_id, perf.performer_id, purposes=("portal",), now=now
        )
        flush(db)
    return share


def undo_share_paid(
    db: Session, ctx: FinanceCtx, performance_id: str, member_id: str
) -> EntPerformanceShare:
    _require_can_pay(ctx)
    perf = load_performance(db, ctx, performance_id, for_update=True)
    share = _share(db, ctx, perf, member_id)
    share.paid_at = share.paid_by = share.paid_reference = None
    if perf.payment_status == "paid":
        perf.payment_status = "pending"
        perf.paid_at = perf.paid_by = perf.paid_reference = None
    flush(db)
    return share


# ---------------------------------------------------------------------------
# The payroll feed (F3)
# ---------------------------------------------------------------------------


def _line(
    perf: EntPerformance,
    performer: EntPerformer,
    venue: Venue | None,
    *,
    share: EntPerformanceShare | None = None,
    payee: EntPerformer | None = None,
) -> Line:
    payee = payee or performer
    tz = venue_tz(venue)
    minutes = real_minutes(
        perf.performance_date,
        perf.actual_start_time or perf.start_time,
        perf.actual_end_time or perf.end_time,
        perf.actual_ends_next_day if perf.actual_start_time else perf.ends_next_day,
        tz,
    )
    amount = share.amount_cents if share is not None else (perf.actual_cents or 0)
    rate = (
        perf.rate_cents
        if share is None
        else (round(amount * 60 / minutes) if minutes else 0)
    )
    return Line(
        performance_id=perf.id,
        share_id=share.id if share is not None else None,
        performer_id=payee.id,
        performer_name=payee.name,
        lane=payee.lane,
        payroll_code=payee.payroll_code,
        pay_code=payee.payroll_code,
        performance_date=perf.performance_date,
        venue_id=perf.venue_id,
        minutes=minutes,
        hours=hours_of(minutes),
        rate_cents=rate,
        amount_cents=amount,
        payment_status="paid"
        if (share.paid_at if share is not None else perf.payment_status == "paid")
        else "pending",
        paid_week_start=perf.payroll_week_start,
    )


def payroll_lines(
    db: Session,
    ctx: FinanceCtx,
    venue_id: str,
    week_start: dt.date,
    *,
    with_skipped: bool = False,
) -> list[Line] | tuple[list[Line], list[dict]]:
    """Approved, unpaid payroll-lane performances at the venue (carried
    forward) plus the ones paid in ``week_start``'s week, as pay lines. A
    members_split band's shares to payroll-lane members are their own lines."""
    require_staff(ctx)
    ctx.require("payroll:read")
    require_venue(ctx, venue_id)
    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    rows = (
        db.query(EntPerformance)
        .filter(
            EntPerformance.organization_id == ctx.org_id,
            EntPerformance.venue_id == venue_id,
            EntPerformance.performance_status == "approved",
            (EntPerformance.payment_status == "pending")
            | (EntPerformance.payroll_week_start == week_start),
        )
        .order_by(EntPerformance.performance_date)
        .all()
    )
    tol = settings_for(db, ctx.org_id).mismatch_tolerance_minutes
    today = _now().date()
    lines: list[Line] = []
    skipped: list[dict] = []
    for perf in rows:
        performer = (
            db.query(EntPerformer).filter(EntPerformer.id == perf.performer_id).first()
        )
        if performer is None:
            continue
        if (
            perf.payment_status == "pending"
            and status_svc.times_match(perf, tol) is False
        ):
            skipped.append(
                {
                    "performance_id": perf.id,
                    "performer": performer.name,
                    "reason": "times mismatch",
                }
            )
            continue
        if performer.band_mode == "members_split":
            by_member = {
                s.band_member_id: s
                for s in db.query(EntPerformanceShare).filter(
                    EntPerformanceShare.performance_id == perf.id
                )
            }
            shares = [
                by_member[m.id] for m in members_of(db, performer) if m.id in by_member
            ]
            for share in shares:
                member = (
                    db.query(EntPerformer)
                    .filter(EntPerformer.id == share.member_performer_id)
                    .first()
                    if share.member_performer_id
                    else None
                )
                if member is None or member.lane not in PAYROLL_LANES:
                    continue
                ok, why = payable(db, member, today=today)
                if not ok:
                    skipped.append(
                        {
                            "performance_id": perf.id,
                            "performer": member.name,
                            "reason": why,
                        }
                    )
                    continue
                lines.append(_line(perf, performer, venue, share=share, payee=member))
            continue
        if performer.lane not in PAYROLL_LANES:
            continue
        ok, why = payable(db, performer, today=today)
        if not ok:
            skipped.append(
                {"performance_id": perf.id, "performer": performer.name, "reason": why}
            )
            continue
        lines.append(_line(perf, performer, venue))
    return (lines, skipped) if with_skipped else lines


def mark_paid_for_week(
    db: Session,
    ctx: FinanceCtx,
    venue_id: str,
    week_start: dt.date,
    performance_ids: list[str],
    *,
    share_ids: tuple[str, ...] | list[str] = (),
    now: dt.datetime | None = None,
) -> dict:
    """F3's export marks the lines it wrote as paid in this pay week."""
    _require_can_pay(ctx)
    require_venue(ctx, venue_id)
    now = now or _now()
    paid = shares = 0
    for pid in performance_ids:
        perf = load_performance(db, ctx, pid, for_update=True)
        if perf.venue_id != venue_id:
            raise Invalid("That performance is at another venue.")
        if perf.payment_status == "paid":
            continue
        _payable_now(db, ctx, perf)
        perf.payment_status, perf.paid_at, perf.paid_by = "paid", now, ctx.user_id
        perf.payroll_week_start = week_start
        perf.paid_reference = f"payroll week {week_start.isoformat()}"
        tokens.revoke_for_performer(
            db, ctx.org_id, perf.performer_id, purposes=("portal",), now=now
        )
        paid += 1
    for sid in share_ids:
        share = (
            db.query(EntPerformanceShare)
            .filter(
                EntPerformanceShare.organization_id == ctx.org_id,
                EntPerformanceShare.id == sid,
            )
            .with_for_update()
            .first()
        )
        if share is None:
            raise NotFound("Share not found.")
        perf = load_performance(db, ctx, share.performance_id)
        if share.paid_at is None:
            share.paid_at, share.paid_by = now, ctx.user_id
            share.paid_reference = f"payroll week {week_start.isoformat()}"
            perf.payroll_week_start = perf.payroll_week_start or week_start
            shares += 1
    flush(db)
    return {"paid": paid, "shares": shares}


def member_name(db: Session, member_id: str) -> str | None:
    return db.query(EntBandMember.name).filter(EntBandMember.id == member_id).scalar()
