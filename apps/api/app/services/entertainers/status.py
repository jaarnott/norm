"""A performance's derived status — one Python function and its SQL twin.

Orbit's ``computeShiftStatus`` rule, kept (plan §6.2, 02 §5.2):

    paid › did_not_perform › mismatch | ready_for_payment ›
    awaiting_performer › awaiting_manager › scheduled | awaiting_approvals

``paid`` beats everything; either side reporting "did not perform" is
terminal; manager approved AND performer confirmed is ``mismatch`` when their
times disagree by more than the tolerance at either end, else
``ready_for_payment``; one side only is "awaiting" the other; neither is
``scheduled`` until the booked end has passed, then ``awaiting_approvals``.
Payment never *requires* the performer's confirmation — only a mismatch
blocks it. The tolerance is ``ent_settings.mismatch_tolerance_minutes`` (15
by default) — the ONE rule, server-side; Orbit's portal used exact equality
and told performers five minutes was a mismatch.

The SQL twin lets the roster and payments pages filter and count by status
in the database. ``tests/test_entertainers_status.py`` runs both over the
same rows and asserts they agree.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import Integer, and_, case, cast, func, literal, or_
from sqlalchemy.sql.expression import ColumnElement

from app.db.entertainers_models import EntPerformance
from app.db.models import Venue, _now
from app.services.entertainers._shared import DEFAULT_TZ, has_ended

STATUSES = (
    "scheduled",
    "awaiting_approvals",
    "awaiting_manager",
    "awaiting_performer",
    "mismatch",
    "ready_for_payment",
    "did_not_perform",
    "paid",
)


def _minutes(value: dt.time) -> int:
    return value.hour * 60 + value.minute


def times_match(perf: EntPerformance, tolerance: int) -> bool | None:
    """True when the manager's and the performer's times agree within the
    tolerance at both ends; None when one side has no times yet. The
    manager's side falls back to the booked times (approved as rostered)."""
    mgr_start = perf.actual_start_time or perf.start_time
    mgr_end = perf.actual_end_time or perf.end_time
    ent_start = perf.performer_actual_start_time
    ent_end = perf.performer_actual_end_time
    if not (mgr_start and mgr_end and ent_start and ent_end):
        return None
    return (
        abs(_minutes(mgr_start) - _minutes(ent_start)) <= tolerance
        and abs(_minutes(mgr_end) - _minutes(ent_end)) <= tolerance
    )


def derive(
    perf: EntPerformance,
    *,
    tz: ZoneInfo,
    tolerance: int,
    now: dt.datetime | None = None,
) -> str:
    if perf.payment_status == "paid":
        return "paid"
    approved = perf.performance_status == "approved"
    manager_dnp = perf.performance_status == "did_not_perform"
    confirmed = perf.performer_confirmed_at is not None
    if manager_dnp or perf.performer_dnp:
        return "did_not_perform"
    if approved and confirmed:
        return "ready_for_payment" if times_match(perf, tolerance) else "mismatch"
    if approved:
        return "awaiting_performer"
    if confirmed:
        return "awaiting_manager"
    return "awaiting_approvals" if has_ended(perf, tz, now) else "scheduled"


# ---------------------------------------------------------------------------
# The SQL twin
# ---------------------------------------------------------------------------


def _sql_minutes(column) -> ColumnElement:
    return cast(func.extract("hour", column), Integer) * 60 + cast(
        func.extract("minute", column), Integer
    )


def _sql_times_match(tolerance: int) -> ColumnElement:
    p = EntPerformance
    mgr_start = func.coalesce(p.actual_start_time, p.start_time)
    mgr_end = func.coalesce(p.actual_end_time, p.end_time)
    return and_(
        p.performer_actual_start_time.is_not(None),
        p.performer_actual_end_time.is_not(None),
        func.abs(_sql_minutes(mgr_start) - _sql_minutes(p.performer_actual_start_time))
        <= tolerance,
        func.abs(_sql_minutes(mgr_end) - _sql_minutes(p.performer_actual_end_time))
        <= tolerance,
    )


def sql_ended(now: dt.datetime | None = None) -> ColumnElement:
    """``TRUE`` when the booked end has passed, in the venue's zone (the
    query must join ``Venue`` on ``EntPerformance.venue_id``). Postgres's
    ``timezone(zone, timestamp)`` resolves a wall-clock time in the repeated
    hour to standard time and a missing one an hour on — the same answers as
    ``_shared.local_to_utc``."""
    p = EntPerformance
    now = now or _now()
    overnight = or_(
        p.ends_next_day.is_(True),
        _sql_minutes(p.end_time) <= _sql_minutes(p.start_time),
    )
    # date + integer → date, date + time → timestamp (Postgres arithmetic
    # SQLAlchemy's typed operators don't model, hence ``op``).
    end_day = p.performance_date.op("+")(
        case((overnight, literal(1)), else_=literal(0))
    )
    local_stamp = end_day.op("+")(p.end_time)
    zone = func.coalesce(Venue.timezone, literal(DEFAULT_TZ))
    return func.timezone(zone, local_stamp) <= literal(now)


def sql_status(*, tolerance: int, now: dt.datetime | None = None) -> ColumnElement:
    """The derived status as a SQL ``CASE`` — the twin of ``derive``."""
    p = EntPerformance
    approved = p.performance_status == "approved"
    confirmed = p.performer_confirmed_at.is_not(None)
    return case(
        (p.payment_status == "paid", literal("paid")),
        (
            or_(p.performance_status == "did_not_perform", p.performer_dnp.is_(True)),
            literal("did_not_perform"),
        ),
        (
            and_(approved, confirmed),
            case(
                (_sql_times_match(tolerance), literal("ready_for_payment")),
                else_=literal("mismatch"),
            ),
        ),
        (approved, literal("awaiting_performer")),
        (confirmed, literal("awaiting_manager")),
        (sql_ended(now), literal("awaiting_approvals")),
        else_=literal("scheduled"),
    )
