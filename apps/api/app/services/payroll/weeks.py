"""Pay weeks: Monday at the venue's day start to the next Monday at day start
(plan §9), one ``pay_weeks`` row per entity-week.

The boundary comes from ``business_calendar`` — never ``+7 days`` on a
timestamp, which drifts an hour across the daylight-saving change. A week is
named by its Monday date (``week_start``); everything else about it (the
instants, the Sunday it ends) is derived here from the venue's own timezone
and ``day_start_time``.

Status is derived from the facts (a current snapshot, a close snapshot,
exports, a push); ``closed_at`` is the one stored sign-off.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.db.models import Venue, _now
from app.db.payroll_models import (
    PayExport,
    PaySnapshot,
    PayWeek,
    PayXeroPush,
)
from app.services.business_calendar import Window, timezone_for, trading_week
from app.services.finance_core.ctx import FinanceCtx, require_venue
from app.services.finance_core.errors import Conflict, Invalid, NotFound, flush

STATUSES = ("open", "snapshotted", "close_ready", "exported", "closed")


def require_writable(ctx: FinanceCtx, *, rehearsal: bool = False) -> None:
    """The shadow rule for a Norm-side payroll input (a payout, a gratuity
    batch, ACE inputs, an adjustment, an accrual): allowed when the module is
    live, or in shadow when the row is flagged ``rehearsal`` (the rehearsal
    weeks — such a row never feeds an export or a draft). Anything else is
    refused with the module's own message."""
    if ctx.is_live("payroll"):
        return
    if rehearsal and ctx.mode("payroll") == "shadow":
        return
    ctx.require_live("payroll")


# ---------------------------------------------------------------- the week --


def monday_of(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def validate_week_start(value) -> dt.date:
    """A ``week_start`` as a page or model sends it: a Monday, as a date."""
    if isinstance(value, dt.datetime):
        value = value.date()
    if not isinstance(value, dt.date):
        try:
            value = dt.date.fromisoformat(str(value).strip()[:10])
        except ValueError:
            raise Invalid("week_start must be a date as YYYY-MM-DD.")
    if value.weekday() != 0:
        raise Invalid(
            f"week_start must be a Monday — {value.isoformat()} is a "
            f"{value.strftime('%A')} (the week of {monday_of(value).isoformat()})."
        )
    return value


def week_window(venue, week_start: dt.date) -> Window:
    """The pay week's instants for a venue: Monday at day start to the next
    Monday at day start, less one second, with the offsets the venue's zone
    gives each end."""
    week_start = validate_week_start(week_start)
    tz = timezone_for(venue)
    # Midday on the Monday is inside the trading day whatever the day start.
    anchor = dt.datetime(
        week_start.year, week_start.month, week_start.day, 12, 0, tzinfo=tz
    )
    return trading_week(venue, anchor, 0)


def week_start_for(venue, moment: dt.datetime) -> dt.date:
    """The Monday of the pay week containing ``moment`` at this venue."""
    return trading_week(venue, moment, 0).start.date()


def last_completed_week_start(venue, now: dt.datetime | None = None) -> dt.date:
    """The most recent pay week that has ended at this venue."""
    now = now or _now()
    return trading_week(venue, now, -1).start.date()


def week_ending(week_start: dt.date) -> dt.date:
    return week_start + dt.timedelta(days=6)


def window_dict(window: Window) -> dict:
    return {
        "start": window.start.isoformat(),
        "end": window.end.isoformat(),
        "day_start": window.day_start,
        "timezone": window.timezone,
    }


# ---------------------------------------------------------------- the rows --


def venue_of(db: Session, ctx: FinanceCtx, venue_id: str) -> Venue:
    require_venue(ctx, venue_id)
    venue = (
        db.query(Venue)
        .filter(Venue.id == venue_id, Venue.organization_id == ctx.org_id)
        .first()
    )
    if venue is None:
        raise NotFound("Venue not found.")
    return venue


def get_or_create(db: Session, ctx: FinanceCtx, venue_id: str, week_start) -> PayWeek:
    """The week row for (venue, Monday), made on first use — a read-only
    person may open a week; the row itself changes nothing outside Norm."""
    venue = venue_of(db, ctx, venue_id)
    week_start = validate_week_start(week_start)
    week = (
        db.query(PayWeek)
        .filter(
            PayWeek.organization_id == ctx.org_id,
            PayWeek.venue_id == venue.id,
            PayWeek.week_start == week_start,
        )
        .first()
    )
    if week is None:
        week = PayWeek(
            organization_id=ctx.org_id, venue_id=venue.id, week_start=week_start
        )
        db.add(week)
        flush(db)
    return week


def get(db: Session, ctx: FinanceCtx, week_id: str) -> PayWeek:
    """A week the caller may see; another organisation's or venue's is not
    found."""
    week = (
        db.query(PayWeek)
        .filter(PayWeek.organization_id == ctx.org_id, PayWeek.id == week_id)
        .first()
    )
    if week is None or not ctx.can_see_venue(week.venue_id):
        raise NotFound("Pay week not found.")
    return week


def list_weeks(
    db: Session, ctx: FinanceCtx, *, venue_id: str | None = None, limit: int = 12
) -> list[PayWeek]:
    q = db.query(PayWeek).filter(PayWeek.organization_id == ctx.org_id)
    if venue_id:
        require_venue(ctx, venue_id)
        q = q.filter(PayWeek.venue_id == venue_id)
    else:
        q = q.filter(ctx.venue_clause(PayWeek.venue_id))
    return (
        q.order_by(PayWeek.week_start.desc(), PayWeek.venue_id)
        .limit(max(1, min(int(limit), 200)))
        .all()
    )


def update_notes(
    db: Session, ctx: FinanceCtx, week: PayWeek, *, notes: str | None, version: int
) -> PayWeek:
    ctx.require("payroll:write")
    if week.version != version:
        raise Conflict("This week was changed by someone else — reload and try again.")
    week.notes = notes
    week.version = version + 1
    flush(db)
    return week


# ---------------------------------------------------------------- status --


def current_snapshot(db: Session, week: PayWeek) -> PaySnapshot | None:
    return (
        db.query(PaySnapshot)
        .filter(
            PaySnapshot.organization_id == week.organization_id,
            PaySnapshot.week_id == week.id,
            PaySnapshot.superseded_at.is_(None),
        )
        .order_by(PaySnapshot.taken_at.desc())
        .first()
    )


def close_snapshot(db: Session, week: PayWeek) -> PaySnapshot | None:
    """The latest close snapshot — what exports, reconciliation and the
    journal build from, superseded or not (a later manual retake shows as
    drift; it does not move the close)."""
    return (
        db.query(PaySnapshot)
        .filter(
            PaySnapshot.organization_id == week.organization_id,
            PaySnapshot.week_id == week.id,
            PaySnapshot.source == "close",
        )
        .order_by(PaySnapshot.taken_at.desc())
        .first()
    )


def exports_of(db: Session, week: PayWeek) -> list[PayExport]:
    return (
        db.query(PayExport)
        .filter(
            PayExport.organization_id == week.organization_id,
            PayExport.week_id == week.id,
        )
        .order_by(PayExport.generated_at.desc(), PayExport.kind)
        .all()
    )


def pushes_of(db: Session, week: PayWeek) -> list[PayXeroPush]:
    return (
        db.query(PayXeroPush)
        .filter(
            PayXeroPush.organization_id == week.organization_id,
            PayXeroPush.week_id == week.id,
        )
        .order_by(PayXeroPush.pushed_at.desc())
        .all()
    )


def status_of(db: Session, week: PayWeek) -> str:
    if week.closed_at is not None:
        return "closed"
    if exports_of(db, week):
        return "exported"
    if close_snapshot(db, week) is not None:
        return "close_ready"
    if current_snapshot(db, week) is not None:
        return "snapshotted"
    return "open"


def close(db: Session, ctx: FinanceCtx, week: PayWeek, *, version: int) -> PayWeek:
    """Record the sign-off (``closed_at/by``). Needs a close snapshot — the
    close runs on a snapshot (D24), never on a live read."""
    ctx.require("payroll:write")
    if week.version != version:
        raise Conflict("This week was changed by someone else — reload and try again.")
    if week.closed_at is not None:
        raise Conflict("This week is already closed.")
    if close_snapshot(db, week) is None:
        raise Conflict(
            "Take the close snapshot first (Start close) — the close runs on a "
            "snapshot, not on a live read."
        )
    week.closed_at = _now()
    week.closed_by = ctx.user_id
    week.version = version + 1
    flush(db)
    return week


def as_dict(db: Session, week: PayWeek, venue: Venue | None = None) -> dict:
    venue = venue or db.query(Venue).filter(Venue.id == week.venue_id).first()
    window = week_window(venue, week.week_start)
    return {
        "id": week.id,
        "venue_id": week.venue_id,
        "venue_name": venue.name if venue is not None else None,
        "week_start": week.week_start.isoformat(),
        "week_ending": week_ending(week.week_start).isoformat(),
        "window": window_dict(window),
        "status": status_of(db, week),
        "notes": week.notes,
        "closed_at": week.closed_at.isoformat() if week.closed_at else None,
        "closed_by": week.closed_by,
        "version": week.version,
    }
