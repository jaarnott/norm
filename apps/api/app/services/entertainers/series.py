"""Weekly recurrences — the stored rule Orbit lacked (plan §11.1; 02 §9 item 11).

A series is one weekday at one venue for one performer, from ``starts_on``
until ``ends_on`` (or open-ended), at a rate. Performances are materialised
twelve months ahead — on creation and by the morning job — so "extend the
residency" is editing the rule, not a new series. Edits and deletes take a
scope: *this* performance only, *this and following* (the rule is split at
the anchor date: the old series ends the day before, a new one carries the
change) or *all* (the rule and every not-yet-approved performance).

A series edit never touches a performance that has been approved, marked
did-not-perform or paid: those are facts.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.db.entertainers_models import EntPerformance, EntPerformer, EntSeries
from app.db.models import _now
from app.services.entertainers._shared import (
    amount_cents,
    check_version,
    clock_minutes,
    day,
    hm,
    iso,
    load,
    parse_date,
    parse_time,
    require_any_venue,
)
from app.services.finance_core.ctx import FinanceCtx, require_venue
from app.services.finance_core.errors import Invalid, flush

HORIZON = dt.timedelta(days=365)
SCOPES = ("this", "following", "all")


def series_dict(row: EntSeries) -> dict:
    return {
        "id": row.id,
        "venue_id": row.venue_id,
        "performer_id": row.performer_id,
        "weekday": row.weekday,
        "start_time": hm(row.start_time),
        "end_time": hm(row.end_time),
        "ends_next_day": row.ends_next_day,
        "rate_cents": row.rate_cents,
        "starts_on": day(row.starts_on),
        "ends_on": day(row.ends_on),
        "materialised_through": day(row.materialised_through),
        "notes": row.notes,
        "archived_at": iso(row.archived_at),
        "version": row.version,
    }


def _booked_cents(
    rate_cents: int, start: dt.time, end: dt.time, ends_next_day: bool
) -> int:
    return amount_cents(rate_cents, clock_minutes(start, end, ends_next_day))


def occurrences(series: EntSeries, *, through: dt.date) -> list[dt.date]:
    """The dates the rule names between the last materialised date and
    ``through`` (bounded by ``ends_on``)."""
    first = series.starts_on
    if series.materialised_through is not None:
        first = max(first, series.materialised_through + dt.timedelta(days=1))
    last = min(through, series.ends_on) if series.ends_on else through
    if last < first:
        return []
    # ISO weekday 1..7 on the first date on/after ``first``.
    offset = (series.weekday - first.isoweekday()) % 7
    out = []
    d = first + dt.timedelta(days=offset)
    while d <= last:
        out.append(d)
        d += dt.timedelta(days=7)
    return out


def materialise(
    db: Session, series: EntSeries, *, through: dt.date, created_by: str | None = None
) -> list[EntPerformance]:
    """Create the performances the rule names up to ``through`` that do not
    exist yet, and move ``materialised_through`` on."""
    if series.archived_at is not None:
        return []
    dates = occurrences(series, through=through)
    existing = {
        d
        for (d,) in db.query(EntPerformance.performance_date).filter(
            EntPerformance.organization_id == series.organization_id,
            EntPerformance.series_id == series.id,
        )
    }
    made = []
    for d in dates:
        if d in existing:
            continue
        perf = EntPerformance(
            organization_id=series.organization_id,
            venue_id=series.venue_id,
            performer_id=series.performer_id,
            series_id=series.id,
            performance_date=d,
            start_time=series.start_time,
            end_time=series.end_time,
            ends_next_day=series.ends_next_day,
            rate_cents=series.rate_cents,
            agreed_cents=_booked_cents(
                series.rate_cents,
                series.start_time,
                series.end_time,
                series.ends_next_day,
            ),
            notes=series.notes,
            created_by=created_by,
        )
        db.add(perf)
        made.append(perf)
    last = min(through, series.ends_on) if series.ends_on else through
    if series.materialised_through is None or last > series.materialised_through:
        series.materialised_through = last
    flush(db)
    return made


def create(
    db: Session,
    ctx: FinanceCtx,
    *,
    venue_id: str,
    performer_id: str,
    weekdays: list[int],
    start_time,
    end_time,
    ends_next_day: bool,
    rate_cents: int,
    starts_on,
    ends_on=None,
    notes: str | None = None,
    today: dt.date | None = None,
) -> tuple[list[EntSeries], list[EntPerformance]]:
    """One series per weekday, materialised twelve months ahead."""
    require_any_venue(ctx)
    require_venue(ctx, venue_id)
    performer = load(db, ctx, EntPerformer, performer_id, label="Performer")
    if not performer.is_active:
        raise Invalid("That performer is inactive.")
    start, end = parse_time(start_time, "Start time"), parse_time(end_time, "End time")
    first = parse_date(starts_on, "Start date")
    last = parse_date(ends_on, "End date") if ends_on else None
    if last is not None and last < first:
        raise Invalid("The end date is before the start date.")
    days = sorted({int(w) for w in weekdays})
    if not days or any(w < 1 or w > 7 for w in days):
        raise Invalid("Pick the weekdays (1 = Monday … 7 = Sunday).")
    if int(rate_cents) < 0:
        raise Invalid("The hourly rate can't be negative.")
    today = today or _now().date()
    through = max(today, first) + HORIZON
    series_rows, made = [], []
    for weekday in days:
        row = EntSeries(
            organization_id=ctx.org_id,
            venue_id=venue_id,
            performer_id=performer_id,
            weekday=weekday,
            start_time=start,
            end_time=end,
            ends_next_day=bool(ends_next_day),
            rate_cents=int(rate_cents),
            starts_on=first,
            ends_on=last,
            notes=notes,
            created_by=ctx.user_id,
        )
        db.add(row)
        flush(db)
        series_rows.append(row)
        made.extend(materialise(db, row, through=through, created_by=ctx.user_id))
    return series_rows, made


def extend_all(db: Session, org_id: str, *, today: dt.date | None = None) -> int:
    """The morning job: keep every live series materialised a year ahead."""
    today = today or _now().date()
    rows = (
        db.query(EntSeries)
        .filter(EntSeries.organization_id == org_id, EntSeries.archived_at.is_(None))
        .all()
    )
    count = 0
    for row in rows:
        count += len(materialise(db, row, through=today + HORIZON))
    return count


def _editable(perf: EntPerformance) -> bool:
    return perf.performance_status == "scheduled" and perf.payment_status == "pending"


def _apply(perf: EntPerformance, fields: dict) -> None:
    for key in (
        "performer_id",
        "start_time",
        "end_time",
        "ends_next_day",
        "rate_cents",
        "notes",
    ):
        if key in fields:
            setattr(perf, key, fields[key])
    if "performance_date" in fields:
        perf.performance_date = fields["performance_date"]
    perf.agreed_cents = _booked_cents(
        perf.rate_cents, perf.start_time, perf.end_time, perf.ends_next_day
    )


def _series_of(db: Session, ctx: FinanceCtx, perf: EntPerformance) -> EntSeries | None:
    if not perf.series_id:
        return None
    return load(db, ctx, EntSeries, perf.series_id, label="Series", for_update=True)


def edit_scope(
    db: Session,
    ctx: FinanceCtx,
    perf: EntPerformance,
    fields: dict,
    *,
    scope: str,
    version: int | None,
) -> list[EntPerformance]:
    """Apply ``fields`` to this performance, or to it and the series'
    following / every not-yet-approved performance, updating the rule."""
    if scope not in SCOPES:
        raise Invalid("scope must be this, following or all.")
    check_version(perf, version)
    if not _editable(perf):
        raise Invalid(
            "Only a scheduled, unpaid performance can be edited — reset it first."
        )
    series = _series_of(db, ctx, perf) if scope != "this" else None
    if series is None:
        _apply(perf, fields)
        flush(db)
        return [perf]
    changed = [perf]
    siblings = (
        db.query(EntPerformance)
        .filter(
            EntPerformance.organization_id == ctx.org_id,
            EntPerformance.series_id == series.id,
            EntPerformance.id != perf.id,
        )
        .all()
    )
    anchor = perf.performance_date
    if scope == "following":
        siblings = [s for s in siblings if s.performance_date >= anchor]
        # Split the rule at the anchor: the old series ends the day before,
        # a new one carries the change from here.
        if series.starts_on < anchor:
            new = EntSeries(
                organization_id=series.organization_id,
                venue_id=series.venue_id,
                performer_id=fields.get("performer_id", series.performer_id),
                weekday=series.weekday,
                start_time=fields.get("start_time", series.start_time),
                end_time=fields.get("end_time", series.end_time),
                ends_next_day=fields.get("ends_next_day", series.ends_next_day),
                rate_cents=fields.get("rate_cents", series.rate_cents),
                starts_on=anchor,
                ends_on=series.ends_on,
                materialised_through=series.materialised_through,
                notes=fields.get("notes", series.notes),
                created_by=ctx.user_id,
            )
            db.add(new)
            series.ends_on = anchor - dt.timedelta(days=1)
            flush(db)
            for s in [perf, *siblings]:
                s.series_id = new.id
            series = new
    for key in (
        "performer_id",
        "start_time",
        "end_time",
        "ends_next_day",
        "rate_cents",
        "notes",
    ):
        if key in fields:
            setattr(series, key, fields[key])
    for s in siblings:
        if _editable(s):
            _apply(s, {k: v for k, v in fields.items() if k != "performance_date"})
            changed.append(s)
    _apply(perf, fields)
    flush(db)
    return changed


def delete_scope(
    db: Session,
    ctx: FinanceCtx,
    perf: EntPerformance,
    *,
    scope: str,
    version: int | None,
) -> int:
    """Delete this scheduled performance, or it and the series' following /
    every scheduled one (ending or archiving the rule). Approved and paid
    performances stay."""
    if scope not in SCOPES:
        raise Invalid("scope must be this, following or all.")
    check_version(perf, version)
    if not _editable(perf):
        raise Invalid("Only a scheduled, unpaid performance can be deleted.")
    series = _series_of(db, ctx, perf) if scope != "this" else None
    if series is None:
        db.delete(perf)
        flush(db)
        return 1
    anchor = perf.performance_date
    q = db.query(EntPerformance).filter(
        EntPerformance.organization_id == ctx.org_id,
        EntPerformance.series_id == series.id,
        EntPerformance.performance_status == "scheduled",
        EntPerformance.payment_status == "pending",
    )
    if scope == "following":
        q = q.filter(EntPerformance.performance_date >= anchor)
        series.ends_on = anchor - dt.timedelta(days=1)
        if series.ends_on < series.starts_on:
            series.archived_at = _now()
    else:
        series.archived_at = _now()
    rows = q.all()
    for row in rows:
        db.delete(row)
    flush(db)
    return len(rows)
