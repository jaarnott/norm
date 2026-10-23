"""Bookings, approvals and the performer's confirmation (plan §11.1, D15).

* **Book** with an hourly rate: ``agreed_cents = rate × booked clock
  hours``, fixed when booked (and re-fixed on an edit).
* **Approve** after the performance has ended, with the actual times:
  ``actual_cents = rate × real venue-local minutes`` — daylight-saving aware
  (``_shared.real_minutes``, Orbit's ``nzShiftTime`` maths), so a set that
  crosses the clock change pays for the hours actually played. Did not
  perform takes a reason; reset puts a performance back to scheduled
  (never once paid). Every changed field is an ``ent_performance_changes``
  row with who and where from.
* **Shares.** A ``members_split`` band's approved amount is split by
  ``share_bp`` into ``ent_performance_shares`` on approval, the leftover cent
  to the first member (plan §11.3).
* **The performer confirms** on their link — their own times, or "I didn't
  perform". Manager and performer times must agree within the tolerance
  at both ends (``status.times_match``, the ONE rule); a mismatch emails the
  performer and shows on the roster, where it is resolved with "use the
  performer's times" or a re-approval.
* **Emails** on approval: the confirmation request if the performer has
  not confirmed, the approved amount for the invoice and exemption lanes
  (figures by email, never on the link — D14f), the mismatch note when
  times already disagree.

Who may: anyone with access to the venue books and approves (D15), through
the fail-closed ctx — a venue the person cannot see is "not found".
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.db.entertainers_models import (
    EntFile,
    EntPerformance,
    EntPerformanceChange,
    EntPerformanceShare,
    EntPerformer,
)
from app.db.models import User, Venue, _now
from app.services.entertainers import emails, status as status_svc, tokens
from app.services.entertainers import series as series_svc
from app.services.entertainers._shared import (
    actor_name,
    amount_cents,
    change_source,
    check_version,
    clock_minutes,
    day,
    dst_adjustment_minutes,
    has_ended,
    hm,
    iso,
    load,
    load_performance,
    parse_date,
    parse_time,
    real_minutes,
    record_change,
    require_any_venue,
    settings_for,
    venue_of,
    venue_tz,
)
from app.services.entertainers.performers import members_of
from app.services.finance_core.ctx import FinanceCtx, require_staff, require_venue
from app.services.finance_core.errors import Conflict, Invalid, flush

#: Reminders the morning job sends for an unconfirmed performance.
CONFIRM_KINDS = ("ent_confirm_request", "ent_confirm_reminder")
MANAGER_KINDS = ("ent_manager_approval",)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _booked_cents(perf: EntPerformance) -> int:
    return amount_cents(
        perf.rate_cents,
        clock_minutes(perf.start_time, perf.end_time, perf.ends_next_day),
    )


def _names(db: Session, org_id: str, rows: list[EntPerformance]) -> dict:
    performer_ids = {r.performer_id for r in rows}
    venue_ids = {r.venue_id for r in rows}
    user_ids = {r.approved_by for r in rows if r.approved_by} | {
        r.paid_by for r in rows if r.paid_by
    }
    out: dict = {"performer": {}, "venue": {}, "user": {}}
    if performer_ids:
        for row in db.query(EntPerformer).filter(
            EntPerformer.organization_id == org_id, EntPerformer.id.in_(performer_ids)
        ):
            out["performer"][row.id] = row
    if venue_ids:
        for row in db.query(Venue).filter(Venue.id.in_(venue_ids)):
            out["venue"][row.id] = row
    if user_ids:
        for uid, name in db.query(User.id, User.full_name).filter(
            User.id.in_(user_ids)
        ):
            out["user"][uid] = name
    return out


def _email_counts(db: Session, org_id: str, perf_id: str) -> dict:
    from app.services.entertainers import email_links

    return {
        "confirm_requests": email_links.send_count(
            db, org_id, CONFIRM_KINDS, target_id=perf_id
        ),
        "last_confirm_request_at": iso(
            email_links.last_sent_at(db, org_id, CONFIRM_KINDS, target_id=perf_id)
        ),
        "manager_requests": email_links.send_count(
            db, org_id, MANAGER_KINDS, target_id=perf_id
        ),
        "last_manager_request_at": iso(
            email_links.last_sent_at(db, org_id, MANAGER_KINDS, target_id=perf_id)
        ),
    }


def performance_dict(
    db: Session,
    ctx: FinanceCtx,
    perf: EntPerformance,
    *,
    names: dict | None = None,
    tolerance: int | None = None,
    now: dt.datetime | None = None,
    with_emails: bool = False,
) -> dict:
    names = names or _names(db, ctx.org_id, [perf])
    performer = names["performer"].get(perf.performer_id)
    venue = names["venue"].get(perf.venue_id)
    tz = venue_tz(venue)
    tol = (
        tolerance
        if tolerance is not None
        else settings_for(db, ctx.org_id).mismatch_tolerance_minutes
    )
    booked = clock_minutes(perf.start_time, perf.end_time, perf.ends_next_day)
    actual = (
        real_minutes(
            perf.performance_date,
            perf.actual_start_time,
            perf.actual_end_time,
            perf.actual_ends_next_day,
            tz,
        )
        if perf.actual_start_time and perf.actual_end_time
        else None
    )
    dst = (
        dst_adjustment_minutes(
            perf.performance_date,
            perf.actual_start_time,
            perf.actual_end_time,
            perf.actual_ends_next_day,
            tz,
        )
        if actual is not None
        else 0
    )
    match = status_svc.times_match(perf, tol)
    invoice_status = None
    if perf.invoice_file_id:
        invoice_status = (
            db.query(EntFile.check_status)
            .filter(EntFile.id == perf.invoice_file_id)
            .scalar()
        )
    out = {
        "id": perf.id,
        "venue_id": perf.venue_id,
        "venue_name": venue.name if venue else None,
        "performer_id": perf.performer_id,
        "performer_name": performer.name if performer else None,
        "lane": performer.lane if performer else None,
        "band_mode": performer.band_mode if performer else None,
        "series_id": perf.series_id,
        "performance_date": day(perf.performance_date),
        "start_time": hm(perf.start_time),
        "end_time": hm(perf.end_time),
        "ends_next_day": perf.ends_next_day,
        "rate_cents": perf.rate_cents,
        "agreed_cents": perf.agreed_cents,
        "actual_start_time": hm(perf.actual_start_time),
        "actual_end_time": hm(perf.actual_end_time),
        "actual_ends_next_day": perf.actual_ends_next_day,
        "actual_cents": perf.actual_cents,
        "minutes": {"booked": booked, "actual": actual, "dst_adjustment": dst},
        "performance_status": perf.performance_status,
        "dnp_reason": perf.dnp_reason,
        "approved_at": iso(perf.approved_at),
        "approved_by": perf.approved_by,
        "approved_by_name": names["user"].get(perf.approved_by),
        "performer_actual_start_time": hm(perf.performer_actual_start_time),
        "performer_actual_end_time": hm(perf.performer_actual_end_time),
        "performer_actual_ends_next_day": perf.performer_actual_ends_next_day,
        "performer_confirmed_at": iso(perf.performer_confirmed_at),
        "performer_dnp": perf.performer_dnp,
        "performer_dnp_reason": perf.performer_dnp_reason,
        "mismatch": (not match) if match is not None else None,
        "closing_manager": (
            {
                "name": perf.closing_manager_name,
                "staff_id": perf.closing_manager_staff_id,
                "email": perf.closing_manager_email,
                "user_id": perf.closing_manager_user_id,
                "source": perf.closing_manager_source,
                "resolved_at": iso(perf.closing_manager_resolved_at),
            }
            if perf.closing_manager_resolved_at or perf.closing_manager_source
            else None
        ),
        "payment_status": perf.payment_status,
        "paid_at": iso(perf.paid_at),
        "paid_by": perf.paid_by,
        "paid_by_name": names["user"].get(perf.paid_by),
        "paid_reference": perf.paid_reference,
        "payroll_week_start": day(perf.payroll_week_start),
        "invoice_file_id": perf.invoice_file_id,
        "invoice_check_status": invoice_status,
        "status": status_svc.derive(perf, tz=tz, tolerance=tol, now=now),
        "has_ended": has_ended(perf, tz, now),
        "notes": perf.notes,
        "created_at": iso(perf.created_at),
        "version": perf.version,
    }
    if with_emails:
        out["emails"] = _email_counts(db, ctx.org_id, perf.id)
    return out


def change_dict(row: EntPerformanceChange) -> dict:
    return {
        "id": row.id,
        "changed_at": iso(row.changed_at),
        "changed_by_source": row.changed_by_source,
        "changed_by_name": row.changed_by_name,
        "field": row.field,
        "old_value": row.old_value,
        "new_value": row.new_value,
        "note": row.note,
    }


def share_dict(db: Session, row: EntPerformanceShare) -> dict:
    from app.db.entertainers_models import EntBandMember

    member = (
        db.query(EntBandMember.name)
        .filter(EntBandMember.id == row.band_member_id)
        .scalar()
    )
    return {
        "id": row.id,
        "band_member_id": row.band_member_id,
        "member_name": member,
        "member_performer_id": row.member_performer_id,
        "amount_cents": row.amount_cents,
        "paid_at": iso(row.paid_at),
        "paid_by": row.paid_by,
        "paid_reference": row.paid_reference,
    }


def list_roster(
    db: Session,
    ctx: FinanceCtx,
    *,
    venue_id: str | None = None,
    since: dt.date | None = None,
    until: dt.date | None = None,
    performer_id: str | None = None,
    now: dt.datetime | None = None,
) -> dict:
    """The caller's venues' performances in a date window, with derived
    status. A venue the caller cannot see is refused; no venues → empty."""
    require_staff(ctx)
    if venue_id is not None:
        require_venue(ctx, venue_id)
    q = db.query(EntPerformance).filter(
        EntPerformance.organization_id == ctx.org_id,
        EntPerformance.venue_id.in_(sorted(ctx.venue_ids))
        if venue_id is None
        else EntPerformance.venue_id == venue_id,
    )
    if since:
        q = q.filter(EntPerformance.performance_date >= since)
    if until:
        q = q.filter(EntPerformance.performance_date <= until)
    if performer_id:
        q = q.filter(EntPerformance.performer_id == performer_id)
    rows = q.order_by(EntPerformance.performance_date, EntPerformance.start_time).all()
    names = _names(db, ctx.org_id, rows)
    tol = settings_for(db, ctx.org_id).mismatch_tolerance_minutes
    items = [
        performance_dict(db, ctx, r, names=names, tolerance=tol, now=now) for r in rows
    ]
    return {
        "performances": items,
        "totals": {
            "count": len(items),
            "agreed_cents": sum(r.agreed_cents for r in rows),
            "actual_cents": sum(r.actual_cents or 0 for r in rows),
        },
    }


def get(db: Session, ctx: FinanceCtx, performance_id: str, *, now=None) -> dict:
    require_staff(ctx)
    perf = load_performance(db, ctx, performance_id)
    changes = (
        db.query(EntPerformanceChange)
        .filter(
            EntPerformanceChange.organization_id == ctx.org_id,
            EntPerformanceChange.performance_id == perf.id,
        )
        .order_by(EntPerformanceChange.changed_at)
        .all()
    )
    shares = (
        db.query(EntPerformanceShare)
        .filter(
            EntPerformanceShare.organization_id == ctx.org_id,
            EntPerformanceShare.performance_id == perf.id,
        )
        .all()
    )
    from app.services.entertainers import email_links

    sent, _total = email_links.list_links(db, ctx, performance_id=perf.id, limit=50)
    invoice = None
    if perf.invoice_file_id:
        from app.services.entertainers.invoice_check import file_dict

        file = db.query(EntFile).filter(EntFile.id == perf.invoice_file_id).first()
        invoice = file_dict(db, ctx, file) if file else None
    return {
        "performance": performance_dict(db, ctx, perf, now=now, with_emails=True),
        "changes": [change_dict(c) for c in changes],
        "emails": sent,
        "shares": [share_dict(db, s) for s in shares] or None,
        "invoice": invoice,
    }


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------


def book(
    db: Session,
    ctx: FinanceCtx,
    *,
    venue_id: str,
    performer_id: str,
    performance_date,
    start_time,
    end_time,
    ends_next_day: bool = False,
    rate_cents: int,
    notes: str | None = None,
    repeat: dict | None = None,
) -> tuple[EntPerformance, list]:
    """One performance — or, with ``repeat``, a series per weekday
    materialised a year ahead (the first occurrence is the one returned)."""
    require_any_venue(ctx)
    require_venue(ctx, venue_id)
    performer = load(db, ctx, EntPerformer, performer_id, label="Performer")
    if not performer.is_active:
        raise Invalid("That performer is inactive.")
    date = parse_date(performance_date, "Date")
    start, end = parse_time(start_time, "Start time"), parse_time(end_time, "End time")
    rate = int(rate_cents)
    if rate < 0:
        raise Invalid("The hourly rate can't be negative.")
    if repeat:
        weekdays = repeat.get("weekdays") or [date.isoweekday()]
        series_rows, made = series_svc.create(
            db,
            ctx,
            venue_id=venue_id,
            performer_id=performer_id,
            weekdays=weekdays,
            start_time=start,
            end_time=end,
            ends_next_day=ends_next_day,
            rate_cents=rate,
            starts_on=date,
            ends_on=repeat.get("ends_on"),
            notes=notes,
        )
        first = min(made, key=lambda p: p.performance_date) if made else None
        if first is None:
            raise Invalid("That repeat makes no performances.")
        return first, series_rows
    perf = EntPerformance(
        organization_id=ctx.org_id,
        venue_id=venue_id,
        performer_id=performer_id,
        performance_date=date,
        start_time=start,
        end_time=end,
        ends_next_day=bool(ends_next_day),
        rate_cents=rate,
        agreed_cents=amount_cents(rate, clock_minutes(start, end, bool(ends_next_day))),
        notes=notes,
        created_by=ctx.user_id,
    )
    db.add(perf)
    flush(db)
    return perf, []


def update(
    db: Session,
    ctx: FinanceCtx,
    performance_id: str,
    fields: dict,
    *,
    version: int | None,
    scope: str = "this",
) -> list[EntPerformance]:
    require_any_venue(ctx)
    perf = load_performance(db, ctx, performance_id, for_update=True)
    clean: dict = {}
    for key, value in fields.items():
        if key == "performer_id":
            performer = load(db, ctx, EntPerformer, value, label="Performer")
            clean[key] = performer.id
        elif key == "performance_date":
            clean[key] = parse_date(value, "Date")
        elif key in ("start_time", "end_time"):
            clean[key] = parse_time(value, key.replace("_", " ").capitalize())
        elif key == "ends_next_day":
            clean[key] = bool(value)
        elif key == "rate_cents":
            clean[key] = int(value)
            if clean[key] < 0:
                raise Invalid("The hourly rate can't be negative.")
        elif key == "notes":
            clean[key] = (value or None) and str(value)[:2000]
        else:
            raise Invalid(f"Cannot set: {key}.")
    before = {k: getattr(perf, k) for k in clean}
    changed = series_svc.edit_scope(db, ctx, perf, clean, scope=scope, version=version)
    who = actor_name(db, ctx)
    for key, old in before.items():
        record_change(
            db,
            perf,
            source=change_source(ctx),
            field=key,
            old=old,
            new=getattr(perf, key),
            user_id=ctx.user_id,
            name=who,
        )
    flush(db)
    return changed


def delete(
    db: Session,
    ctx: FinanceCtx,
    performance_id: str,
    *,
    version: int | None,
    scope: str = "this",
) -> int:
    require_any_venue(ctx)
    perf = load_performance(db, ctx, performance_id, for_update=True)
    return series_svc.delete_scope(db, ctx, perf, scope=scope, version=version)


# ---------------------------------------------------------------------------
# The manager's approval
# ---------------------------------------------------------------------------


def _split_shares(db: Session, perf: EntPerformance, performer: EntPerformer) -> None:
    """A members_split band's approved amount by share, the residual cent
    to the first member (replacing any earlier split)."""
    db.query(EntPerformanceShare).filter(
        EntPerformanceShare.organization_id == perf.organization_id,
        EntPerformanceShare.performance_id == perf.id,
    ).delete(synchronize_session=False)
    members = members_of(db, performer)
    if not members or perf.actual_cents is None:
        return
    total = perf.actual_cents
    amounts = [total * m.share_bp // 10_000 for m in members]
    amounts[0] += total - sum(amounts)
    for member, cents in zip(members, amounts):
        db.add(
            EntPerformanceShare(
                organization_id=perf.organization_id,
                performance_id=perf.id,
                band_member_id=member.id,
                member_performer_id=member.member_performer_id,
                amount_cents=cents,
            )
        )
    flush(db)


def _approve_at(
    db: Session,
    ctx: FinanceCtx,
    perf: EntPerformance,
    *,
    start: dt.time,
    end: dt.time,
    ends_next_day: bool,
    note: str | None,
    now: dt.datetime,
) -> EntPerformance:
    venue = venue_of(db, perf.venue_id)
    tz = venue_tz(venue)
    if not has_ended(perf, tz, now):
        raise Conflict("The performance hasn't ended yet — approve it afterwards.")
    if perf.payment_status == "paid":
        raise Conflict("This performance is paid — undo the payment first.")
    performer = load(db, ctx, EntPerformer, perf.performer_id, label="Performer")
    minutes = real_minutes(perf.performance_date, start, end, ends_next_day, tz)
    pay = amount_cents(perf.rate_cents, minutes)
    who = actor_name(db, ctx)
    source = change_source(ctx)
    for field, old, new in (
        ("actual_start_time", hm(perf.actual_start_time), hm(start)),
        ("actual_end_time", hm(perf.actual_end_time), hm(end)),
        ("actual_ends_next_day", perf.actual_ends_next_day, ends_next_day),
        ("actual_cents", perf.actual_cents, pay),
        ("performance_status", perf.performance_status, "approved"),
    ):
        record_change(
            db,
            perf,
            source=source,
            field=field,
            old=old,
            new=new,
            user_id=ctx.user_id,
            name=who,
            note=note,
        )
    perf.actual_start_time, perf.actual_end_time = start, end
    perf.actual_ends_next_day = ends_next_day
    perf.actual_cents = pay
    perf.performance_status = "approved"
    perf.dnp_reason = None
    perf.approved_at = now
    perf.approved_by = ctx.user_id
    flush(db)
    if performer.band_mode == "members_split":
        _split_shares(db, perf, performer)
    tol = settings_for(db, ctx.org_id).mismatch_tolerance_minutes
    if perf.performer_confirmed_at is None and not perf.performer_dnp:
        _queue_confirm_request(db, perf, performer, venue, reminder=False)
    elif status_svc.times_match(perf, tol) is False:
        _queue_mismatch(db, perf, performer, venue, who)
    if performer.lane in ("invoice", "exemption"):
        _queue_approved_amount(db, perf, performer, venue)
    return perf


def approve(
    db: Session,
    ctx: FinanceCtx,
    performance_id: str,
    *,
    actual_start_time,
    actual_end_time,
    actual_ends_next_day: bool = False,
    note: str | None = None,
    version: int | None,
    now: dt.datetime | None = None,
) -> EntPerformance:
    require_any_venue(ctx)
    perf = load_performance(db, ctx, performance_id, for_update=True)
    check_version(perf, version)
    return _approve_at(
        db,
        ctx,
        perf,
        start=parse_time(actual_start_time, "Actual start"),
        end=parse_time(actual_end_time, "Actual end"),
        ends_next_day=bool(actual_ends_next_day),
        note=note,
        now=now or _now(),
    )


def use_performer_times(
    db: Session, ctx: FinanceCtx, performance_id: str, *, version: int | None, now=None
) -> EntPerformance:
    """Resolve a mismatch by approving at the times the performer gave."""
    require_any_venue(ctx)
    perf = load_performance(db, ctx, performance_id, for_update=True)
    check_version(perf, version)
    if not (perf.performer_actual_start_time and perf.performer_actual_end_time):
        raise Conflict("The performer hasn't given their times yet.")
    return _approve_at(
        db,
        ctx,
        perf,
        start=perf.performer_actual_start_time,
        end=perf.performer_actual_end_time,
        ends_next_day=perf.performer_actual_ends_next_day,
        note="Approved at the performer's times",
        now=now or _now(),
    )


def did_not_perform(
    db: Session,
    ctx: FinanceCtx,
    performance_id: str,
    *,
    reason: str,
    version: int | None,
) -> EntPerformance:
    require_any_venue(ctx)
    perf = load_performance(db, ctx, performance_id, for_update=True)
    check_version(perf, version)
    if perf.payment_status == "paid":
        raise Conflict("This performance is paid — undo the payment first.")
    reason = " ".join(str(reason or "").split())
    if not reason:
        raise Invalid("Say why the performance didn't happen.")
    who = actor_name(db, ctx)
    record_change(
        db,
        perf,
        source=change_source(ctx),
        field="performance_status",
        old=perf.performance_status,
        new="did_not_perform",
        user_id=ctx.user_id,
        name=who,
    )
    record_change(
        db,
        perf,
        source=change_source(ctx),
        field="dnp_reason",
        old=perf.dnp_reason,
        new=reason,
        user_id=ctx.user_id,
        name=who,
    )
    perf.performance_status = "did_not_perform"
    perf.dnp_reason = reason[:2000]
    perf.actual_cents = 0
    perf.approved_at = _now()
    perf.approved_by = ctx.user_id
    db.query(EntPerformanceShare).filter(
        EntPerformanceShare.organization_id == ctx.org_id,
        EntPerformanceShare.performance_id == perf.id,
    ).delete(synchronize_session=False)
    flush(db)
    return perf


def reset(
    db: Session, ctx: FinanceCtx, performance_id: str, *, version: int | None
) -> EntPerformance:
    """Back to scheduled — the manager's decision undone (the performer's
    confirmation stays). Never once paid."""
    require_any_venue(ctx)
    perf = load_performance(db, ctx, performance_id, for_update=True)
    check_version(perf, version)
    if perf.payment_status == "paid":
        raise Conflict("This performance is paid — undo the payment first.")
    who = actor_name(db, ctx)
    for field in (
        "performance_status",
        "actual_start_time",
        "actual_end_time",
        "actual_cents",
        "dnp_reason",
    ):
        record_change(
            db,
            perf,
            source=change_source(ctx),
            field=field,
            old=getattr(perf, field),
            new="scheduled" if field == "performance_status" else None,
            user_id=ctx.user_id,
            name=who,
        )
    perf.performance_status = "scheduled"
    perf.actual_start_time = perf.actual_end_time = None
    perf.actual_ends_next_day = False
    perf.actual_cents = None
    perf.dnp_reason = None
    perf.approved_at = None
    perf.approved_by = None
    db.query(EntPerformanceShare).filter(
        EntPerformanceShare.organization_id == ctx.org_id,
        EntPerformanceShare.performance_id == perf.id,
    ).delete(synchronize_session=False)
    flush(db)
    return perf


# ---------------------------------------------------------------------------
# The performer's confirmation (from the portal)
# ---------------------------------------------------------------------------


def performer_confirm(
    db: Session,
    ctx: FinanceCtx,
    performance_id: str,
    *,
    actual_start_time=None,
    actual_end_time=None,
    actual_ends_next_day: bool = False,
    did_not_perform: bool = False,
    reason: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    now: dt.datetime | None = None,
) -> EntPerformance:
    """The performer's own account of the night. Only their own performance
    (the ctx is ``for_performer``), only after it has ended."""
    if not ctx.is_performer:
        raise Invalid("Confirmation comes from the performer's link.")
    now = now or _now()
    perf = load_performance(db, ctx, performance_id, for_update=True)
    venue = venue_of(db, perf.venue_id)
    if not has_ended(perf, venue_tz(venue), now):
        raise Conflict("You can confirm once the performance has finished.")
    if perf.payment_status == "paid":
        raise Conflict("This performance has been paid.")
    performer = load(db, ctx, EntPerformer, perf.performer_id, label="Performer")
    old = {
        "performer_actual_start_time": hm(perf.performer_actual_start_time),
        "performer_actual_end_time": hm(perf.performer_actual_end_time),
        "performer_dnp": perf.performer_dnp,
        "performer_dnp_reason": perf.performer_dnp_reason,
    }
    if did_not_perform:
        perf.performer_dnp = True
        perf.performer_dnp_reason = (
            " ".join(str(reason or "").split()) or None
        ) and str(reason)[:2000]
        perf.performer_actual_start_time = perf.performer_actual_end_time = None
        perf.performer_actual_ends_next_day = False
    else:
        perf.performer_dnp = False
        perf.performer_dnp_reason = None
        perf.performer_actual_start_time = parse_time(actual_start_time, "Start time")
        perf.performer_actual_end_time = parse_time(actual_end_time, "End time")
        perf.performer_actual_ends_next_day = bool(actual_ends_next_day)
    perf.performer_confirmed_at = now
    for field, before in old.items():
        value = getattr(perf, field)
        record_change(
            db,
            perf,
            source="performer",
            field=field,
            old=before,
            new=hm(value) if isinstance(value, dt.time) else value,
            name=performer.name,
            ip=ip,
            user_agent=user_agent,
        )
    flush(db)
    tol = settings_for(db, ctx.org_id).mismatch_tolerance_minutes
    if (
        perf.performance_status == "approved"
        and not did_not_perform
        and status_svc.times_match(perf, tol) is False
    ):
        _queue_mismatch(db, perf, performer, venue, None)
    return perf


# ---------------------------------------------------------------------------
# Emails this module queues
# ---------------------------------------------------------------------------


def _portal_url(db: Session, perf: EntPerformance, performer: EntPerformer) -> str:
    _row, _token, url = tokens.mint(
        db, org_id=perf.organization_id, purpose="portal", performer_id=performer.id
    )
    return f"{url}?performance={perf.id}"


def _queue_confirm_request(
    db, perf, performer, venue, *, reminder: bool, now: dt.datetime | None = None
) -> bool:
    if not performer.email:
        return False
    url = _portal_url(db, perf, performer)
    org_id, perf_id, performer_id, venue_id = (
        perf.organization_id,
        perf.id,
        performer.id,
        perf.venue_id,
    )
    context = {
        "first_name": emails.first_name(performer.name),
        "venue_name": venue.name if venue else "the venue",
        "date_text": emails.date_text(perf.performance_date),
        "time_text": emails.time_text(perf.start_time, perf.end_time),
        "url": url,
        "expires_days": tokens.TTL.days,
    }
    kind = "ent_confirm_reminder" if reminder else "ent_confirm_request"
    address = performer.email

    def _send(session: Session) -> None:
        emails.deliver(
            session,
            org_id=org_id,
            kind=kind,
            to=address,
            context=context,
            performer_id=performer_id,
            venue_id=venue_id,
            performance_ids=[perf_id],
            target_id=perf_id,
            now=now,
        )

    emails.later(db, _send)
    return True


def _queue_mismatch(db, perf, performer, venue, manager_name) -> None:
    if not performer.email:
        return
    url = _portal_url(db, perf, performer)
    org_id, perf_id, performer_id, venue_id = (
        perf.organization_id,
        perf.id,
        performer.id,
        perf.venue_id,
    )
    context = {
        "first_name": emails.first_name(performer.name),
        "venue_name": venue.name if venue else "the venue",
        "date_text": emails.date_text(perf.performance_date),
        "manager_times": emails.time_text(
            perf.actual_start_time or perf.start_time,
            perf.actual_end_time or perf.end_time,
        ),
        "your_times": emails.time_text(
            perf.performer_actual_start_time, perf.performer_actual_end_time
        ),
        "manager_name": perf.closing_manager_name
        or manager_name
        or "the venue manager",
        "manager_email": perf.closing_manager_email or "",
        "url": url,
    }
    address = performer.email

    def _send(session: Session) -> None:
        emails.deliver(
            session,
            org_id=org_id,
            kind="ent_mismatch",
            to=address,
            context=context,
            performer_id=performer_id,
            venue_id=venue_id,
            performance_ids=[perf_id],
            target_id=perf_id,
        )

    emails.later(db, _send)


def _queue_approved_amount(db, perf, performer, venue) -> None:
    """The figure goes by email to the registered address, never on the link
    (D14f): amount, GST basis and the reference to put on their invoice."""
    if not performer.email:
        return
    from app.services.entertainers.invoice_check import gst_rate_bp

    org_id, perf_id, performer_id, venue_id = (
        perf.organization_id,
        perf.id,
        performer.id,
        perf.venue_id,
    )
    rate_bp = gst_rate_bp(db, org_id)
    amount = perf.actual_cents or 0
    if performer.lane == "exemption":
        gst_text = "No GST or withholding tax applies — your exemption certificate covers this payment."
    elif performer.gst_registered:
        gst_text = f"This amount excludes GST — add GST at {rate_bp / 100:g}% on your invoice (${amount * rate_bp / 10_000 / 100:,.2f})."
    else:
        gst_text = "Our records show you are not GST registered, so no GST should appear on your invoice."
    context = {
        "first_name": emails.first_name(performer.name),
        "venue_name": venue.name if venue else "the venue",
        "date_text": emails.date_text(perf.performance_date),
        "times": emails.time_text(perf.actual_start_time, perf.actual_end_time),
        "amount_text": emails.money_text(amount),
        "gst_text": gst_text,
        "reference": f"NORM-{perf_id[:8].upper()}",
        "needs_invoice": True,
    }
    address = performer.email

    def _send(session: Session) -> None:
        emails.deliver(
            session,
            org_id=org_id,
            kind="ent_approved_amount",
            to=address,
            context=context,
            performer_id=performer_id,
            venue_id=venue_id,
            performance_ids=[perf_id],
            target_id=perf_id,
        )

    emails.later(db, _send)


def send_confirm_request(
    db: Session, ctx: FinanceCtx, performance_id: str, *, resend: bool = False
) -> dict:
    """Staff: (re)send the performer's confirmation request."""
    require_any_venue(ctx)
    perf = load_performance(db, ctx, performance_id)
    if perf.performer_confirmed_at is not None:
        raise Conflict("The performer has already confirmed.")
    performer = load(db, ctx, EntPerformer, perf.performer_id, label="Performer")
    venue = venue_of(db, perf.venue_id)
    sent = _queue_confirm_request(db, perf, performer, venue, reminder=resend)
    return {"sent": sent}


def staff_portal_link(db: Session, ctx: FinanceCtx, performance_id: str) -> dict:
    """Staff opening the performer's view (Orbit's "Open entertainer's link")."""
    require_any_venue(ctx)
    perf = load_performance(db, ctx, performance_id)
    performer = load(db, ctx, EntPerformer, perf.performer_id, label="Performer")
    return {"url": _portal_url(db, perf, performer)}


# ---------------------------------------------------------------------------
# Reminders (the morning job)
# ---------------------------------------------------------------------------


def send_confirm_reminders(
    db: Session, org_id: str, *, now: dt.datetime | None = None
) -> int:
    """Approved performances the performer has not confirmed: a reminder
    every ``reminder_interval_days``, at most ``reminder_max`` sends in all,
    for performances in the last 30 days (Orbit's sweep rule)."""
    from app.services.entertainers import email_links

    now = now or _now()
    settings_row = settings_for(db, org_id)
    since = now.date() - dt.timedelta(days=30)
    rows = (
        db.query(EntPerformance)
        .filter(
            EntPerformance.organization_id == org_id,
            EntPerformance.performance_status == "approved",
            EntPerformance.performer_confirmed_at.is_(None),
            EntPerformance.payment_status == "pending",
            EntPerformance.performance_date >= since,
        )
        .all()
    )
    sent = 0
    for perf in rows:
        count = email_links.send_count(db, org_id, CONFIRM_KINDS, target_id=perf.id)
        if count >= settings_row.reminder_max:
            continue
        last = email_links.last_sent_at(db, org_id, CONFIRM_KINDS, target_id=perf.id)
        if last is not None and now - last < dt.timedelta(
            days=settings_row.reminder_interval_days
        ):
            continue
        performer = (
            db.query(EntPerformer).filter(EntPerformer.id == perf.performer_id).first()
        )
        if performer is None or not performer.is_active:
            continue
        venue = venue_of(db, perf.venue_id)
        if _queue_confirm_request(
            db, perf, performer, venue, reminder=count > 0, now=now
        ):
            sent += 1
    return sent


def unconfirmed_for_venue(
    db: Session, org_id: str, venue_id: str, *, now: dt.datetime
) -> list[EntPerformance]:
    """Approved, unpaid, unconfirmed performances past their reminders — the
    digest's "unconfirmed performer times"."""
    from app.services.entertainers import email_links

    settings_row = settings_for(db, org_id)
    rows = (
        db.query(EntPerformance)
        .filter(
            EntPerformance.organization_id == org_id,
            EntPerformance.venue_id == venue_id,
            EntPerformance.performance_status == "approved",
            EntPerformance.performer_confirmed_at.is_(None),
            EntPerformance.payment_status == "pending",
            EntPerformance.performance_date >= now.date() - dt.timedelta(days=60),
        )
        .order_by(EntPerformance.performance_date)
        .all()
    )
    return [
        r
        for r in rows
        if email_links.send_count(db, org_id, CONFIRM_KINDS, target_id=r.id)
        >= settings_row.reminder_max
    ]
