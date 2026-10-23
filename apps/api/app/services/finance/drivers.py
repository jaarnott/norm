"""Drivers and overrides — what a person tells the forecast (plan §8).

A **driver** moves a day: a public holiday, an event, a function, or a
manual factor ("Round the Bays"), as a dollar amount or a percentage of the
baseline (basis points), exactly one of the two. A driver is retired with
``archived_at`` — never deleted — so a vintage that used it still explains
itself. A day that carries a driver is also left out of the baseline's
samples, because its trade was not a normal day's.

An **override** replaces the day's number outright. It records who and when,
and it survives every nightly regeneration: Orbit's overrides lived only
until they were pushed, then were deleted (05 §7 item 4). A new override for
the same day supersedes the old one (``superseded_at``), so the history of
what people said stays readable.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.db.finance_models import DRIVER_KINDS, FinForecastDriver, FinForecastOverride
from app.db.models import _now
from app.services.finance_core.ctx import FinanceCtx, require_venue
from app.services.finance_core.errors import Invalid, NotFound, flush

#: A percentage uplift beyond this is a typo (300%).
_MAX_BP = 30_000


def _date(value, what: str = "date") -> dt.date:
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError) as exc:
        raise Invalid(f"{what} must be YYYY-MM-DD.") from exc


# ---------------------------------------------------------------------------
# Drivers
# ---------------------------------------------------------------------------


def add_driver(
    db: Session,
    ctx: FinanceCtx,
    venue_id: str,
    date,
    *,
    kind: str,
    label: str,
    uplift_cents: int | None = None,
    uplift_bp: int | None = None,
) -> FinForecastDriver:
    ctx.require("budgets:write")
    require_venue(ctx, venue_id)
    if kind not in DRIVER_KINDS:
        raise Invalid(f"kind is one of {', '.join(DRIVER_KINDS)}.")
    label = str(label or "").strip()
    if not label:
        raise Invalid("A driver needs a label — what is happening that day.")
    if (uplift_cents is None) == (uplift_bp is None):
        raise Invalid(
            "Give exactly one of uplift_cents (an amount) or uplift_bp (a percentage)."
        )
    if uplift_bp is not None and not (-10_000 <= int(uplift_bp) <= _MAX_BP):
        raise Invalid("uplift_bp is basis points: 1000 = +10%, -2500 = -25%.")
    row = FinForecastDriver(
        organization_id=ctx.org_id,
        venue_id=venue_id,
        date=_date(date),
        kind=kind,
        label=label[:120],
        uplift_cents=int(uplift_cents) if uplift_cents is not None else None,
        uplift_bp=int(uplift_bp) if uplift_bp is not None else None,
        created_by=ctx.user_id,
    )
    db.add(row)
    flush(db)
    return row


def archive_driver(db: Session, ctx: FinanceCtx, driver_id: str) -> FinForecastDriver:
    ctx.require("budgets:write")
    row = (
        db.query(FinForecastDriver)
        .filter(
            FinForecastDriver.organization_id == ctx.org_id,
            FinForecastDriver.id == driver_id,
        )
        .first()
    )
    if row is None:
        raise NotFound("Driver not found.")
    require_venue(ctx, row.venue_id)
    if row.archived_at is None:
        row.archived_at = _now()
        flush(db)
    return row


def list_drivers(
    db: Session,
    ctx: FinanceCtx,
    venue_id: str,
    first: dt.date,
    last: dt.date,
    *,
    include_archived: bool = False,
) -> list[FinForecastDriver]:
    ctx.require("finance:read")
    require_venue(ctx, venue_id)
    q = db.query(FinForecastDriver).filter(
        FinForecastDriver.organization_id == ctx.org_id,
        FinForecastDriver.venue_id == venue_id,
        FinForecastDriver.date >= first,
        FinForecastDriver.date <= last,
    )
    if not include_archived:
        q = q.filter(FinForecastDriver.archived_at.is_(None))
    return q.order_by(FinForecastDriver.date, FinForecastDriver.created_at).all()


def drivers_by_date(
    db: Session, org_id: str, venue_id: str, first: dt.date, last: dt.date
) -> dict[dt.date, list[FinForecastDriver]]:
    """Live drivers over a range, grouped by day — the engine's input."""
    out: dict[dt.date, list[FinForecastDriver]] = {}
    for r in (
        db.query(FinForecastDriver)
        .filter(
            FinForecastDriver.organization_id == org_id,
            FinForecastDriver.venue_id == venue_id,
            FinForecastDriver.date >= first,
            FinForecastDriver.date <= last,
            FinForecastDriver.archived_at.is_(None),
        )
        .order_by(FinForecastDriver.date, FinForecastDriver.created_at)
    ):
        out.setdefault(r.date, []).append(r)
    return out


def driver_dict(r: FinForecastDriver) -> dict:
    return {
        "id": r.id,
        "venue_id": r.venue_id,
        "date": r.date.isoformat(),
        "kind": r.kind,
        "label": r.label,
        "uplift_cents": r.uplift_cents,
        "uplift_bp": r.uplift_bp,
        "created_by": r.created_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "archived_at": r.archived_at.isoformat() if r.archived_at else None,
    }


# ---------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------


def set_override(
    db: Session,
    ctx: FinanceCtx,
    venue_id: str,
    date,
    amount_cents: int,
    *,
    reason: str | None = None,
) -> FinForecastOverride:
    """A person's number for the day. The previous live override for that
    day is superseded, not edited."""
    ctx.require("budgets:write")
    require_venue(ctx, venue_id)
    if isinstance(amount_cents, bool):
        raise Invalid("amount_cents must be a whole number of cents.")
    try:
        cents = int(amount_cents)
    except (TypeError, ValueError) as exc:
        raise Invalid("amount_cents must be a whole number of cents.") from exc
    if cents < 0:
        raise Invalid("An override cannot be negative.")
    day = _date(date)
    now = _now()
    for old in _live_overrides(db, ctx.org_id, venue_id, day, day):
        old.superseded_at = now
    row = FinForecastOverride(
        organization_id=ctx.org_id,
        venue_id=venue_id,
        date=day,
        amount_cents=cents,
        reason=(str(reason).strip() or None) if reason else None,
        created_by=ctx.user_id,
    )
    db.add(row)
    flush(db)
    return row


def clear_override(db: Session, ctx: FinanceCtx, venue_id: str, date) -> int:
    ctx.require("budgets:write")
    require_venue(ctx, venue_id)
    day = _date(date)
    now = _now()
    rows = _live_overrides(db, ctx.org_id, venue_id, day, day)
    for old in rows:
        old.superseded_at = now
    flush(db)
    return len(rows)


def _live_overrides(db, org_id, venue_id, first, last) -> list[FinForecastOverride]:
    return (
        db.query(FinForecastOverride)
        .filter(
            FinForecastOverride.organization_id == org_id,
            FinForecastOverride.venue_id == venue_id,
            FinForecastOverride.date >= first,
            FinForecastOverride.date <= last,
            FinForecastOverride.superseded_at.is_(None),
        )
        .order_by(FinForecastOverride.date, FinForecastOverride.created_at)
        .all()
    )


def overrides_by_date(
    db: Session, org_id: str, venue_id: str, first: dt.date, last: dt.date
) -> dict[dt.date, FinForecastOverride]:
    """The live override per day over a range — the engine's input."""
    out: dict[dt.date, FinForecastOverride] = {}
    for r in _live_overrides(db, org_id, venue_id, first, last):
        out[r.date] = r  # the latest wins (ordered by created_at)
    return out


def override_dict(r: FinForecastOverride | None) -> dict | None:
    if r is None:
        return None
    return {
        "id": r.id,
        "date": r.date.isoformat(),
        "amount_cents": r.amount_cents,
        "reason": r.reason,
        "created_by": r.created_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
