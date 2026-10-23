"""Plumbing every entertainers service shares: load-if-visible, the per-org
settings row, venue-local time, and the field-level audit row.

Performers are organisation-wide (Orbit's register ignored venue links and
the owner kept it that way), so a performer loads on membership alone; a
performance is venue-scoped and loads only at one of the caller's venues. A
performer on a public link (``ctx.is_performer``) loads only their own rows.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import settings
from app.db.entertainers_models import (
    EntPerformance,
    EntPerformanceChange,
    EntPerformer,
    EntSettings,
)
from app.db.models import Venue, _now
from app.services.finance_core.ctx import FinanceCtx
from app.services.finance_core.errors import (
    Conflict,
    Forbidden,
    Invalid,
    NotFound,
    flush,
)

#: Every entity in this build runs NZ payroll (D12); a venue with no timezone
#: on its row is read as Auckland rather than refused.
DEFAULT_TZ = "Pacific/Auckland"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load(
    db: Session,
    ctx: FinanceCtx,
    model,
    row_id: str | None,
    *,
    label: str,
    venue_scoped: bool = False,
    for_update: bool = False,
):
    """The row if it is in the caller's organisation (and, when
    ``venue_scoped``, at one of their venues) — otherwise ``NotFound``, never
    "exists but not yours"."""
    if not row_id:
        raise NotFound(f"{label} not found.")
    q = db.query(model).filter(model.organization_id == ctx.org_id, model.id == row_id)
    if for_update:
        q = q.with_for_update().populate_existing()
    row = q.first()
    if row is None:
        raise NotFound(f"{label} not found.")
    if venue_scoped and not ctx.is_performer:
        if getattr(row, "venue_id", None) not in ctx.venue_ids:
            raise NotFound(f"{label} not found.")
    return row


def load_performer(
    db: Session, ctx: FinanceCtx, performer_id: str | None, *, for_update: bool = False
) -> EntPerformer:
    """A performer of the organisation. A performer on a public link may
    load only themself."""
    if ctx.is_performer and performer_id != ctx.performer_id:
        raise NotFound("Performer not found.")
    return load(
        db, ctx, EntPerformer, performer_id, label="Performer", for_update=for_update
    )


def load_performance(
    db: Session,
    ctx: FinanceCtx,
    performance_id: str | None,
    *,
    for_update: bool = False,
) -> EntPerformance:
    """A performance at one of the caller's venues — or, for a performer on
    a public link, one of their own."""
    row = load(
        db,
        ctx,
        EntPerformance,
        performance_id,
        label="Performance",
        venue_scoped=True,
        for_update=for_update,
    )
    if ctx.is_performer and row.performer_id != ctx.performer_id:
        raise NotFound("Performance not found.")
    return row


def require_any_venue(ctx: FinanceCtx) -> None:
    """Booking, inviting and approving need venue access — a member with no
    venue rows (fail closed) may read nothing and write nothing."""
    if ctx.is_performer:
        raise Forbidden("Only venue staff can do that.")
    if not ctx.venue_ids:
        raise Forbidden("You do not have access to any venue.")


def check_version(row, expected: int | None) -> None:
    """Refuse an edit made against an older copy (``None`` skips: the agent
    never saw a version; the mapper's version column still catches races)."""
    if expected is not None and int(expected) != row.version:
        raise Conflict(
            "This was changed by someone else since you opened it — reload and try again."
        )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def settings_for(db: Session, org_id: str) -> EntSettings:
    """The organisation's ``ent_settings`` row, made with the defaults the
    first time it is asked for."""
    row = db.query(EntSettings).filter(EntSettings.organization_id == org_id).first()
    if row is None:
        row = EntSettings(organization_id=org_id)
        db.add(row)
        flush(db)
    return row


# ---------------------------------------------------------------------------
# Venue-local time
# ---------------------------------------------------------------------------


def venue_tz(venue: Venue | None) -> ZoneInfo:
    name = (getattr(venue, "timezone", None) or DEFAULT_TZ).strip() or DEFAULT_TZ
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 — an unknown name on the row, not the caller's fault
        return ZoneInfo(DEFAULT_TZ)


def venue_of(db: Session, venue_id: str) -> Venue | None:
    return db.query(Venue).filter(Venue.id == venue_id).first()


def _offset_minutes(utc_ms: int, tz: ZoneInfo) -> int:
    """The zone's offset at a UTC instant, in minutes (Orbit's
    ``offsetMinutes``)."""
    instant = dt.datetime.fromtimestamp(utc_ms / 1000, tz=dt.timezone.utc).astimezone(
        tz
    )
    return int(instant.utcoffset().total_seconds() // 60)


def local_to_utc(day: dt.date, time: dt.time, tz: ZoneInfo) -> dt.datetime:
    """A venue-local wall-clock time as a UTC instant — Orbit's two-pass
    ``localToUtc`` (``nzShiftTime.ts``), ported exactly so a time in the
    missing or the repeated hour of a daylight-saving change resolves the
    way Orbit's stored amounts did: the repeated hour in April reads as its
    SECOND occurrence (standard time), the missing hour in September as the
    instant an hour on."""
    naive_ms = int(
        dt.datetime(
            day.year, day.month, day.day, time.hour, time.minute, tzinfo=dt.timezone.utc
        ).timestamp()
        * 1000
    )
    utc_ms = naive_ms - _offset_minutes(naive_ms, tz) * 60_000
    utc_ms = naive_ms - _offset_minutes(utc_ms, tz) * 60_000
    return dt.datetime.fromtimestamp(utc_ms / 1000, tz=dt.timezone.utc)


def clock_minutes(start: dt.time, end: dt.time, ends_next_day: bool = False) -> int:
    """Clock-face minutes, daylight saving ignored. An end at or before the
    start, or ``ends_next_day``, rolls into the next day."""
    diff = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
    if ends_next_day or diff <= 0:
        diff += 24 * 60
    return diff


def real_minutes(
    day: dt.date,
    start: dt.time,
    end: dt.time,
    ends_next_day: bool,
    tz: ZoneInfo,
) -> int:
    """Minutes actually elapsed in the venue's zone between the booked start
    and end — the same as clock minutes except across a daylight-saving
    change (an hour fewer in September, an hour more in April). Falls back
    to clock minutes if the zone maths gives nothing positive."""
    overnight = ends_next_day or (
        end.hour * 60 + end.minute <= start.hour * 60 + start.minute
    )
    start_utc = local_to_utc(day, start, tz)
    end_day = day + dt.timedelta(days=1 if overnight else 0)
    end_utc = local_to_utc(end_day, end, tz)
    real = round((end_utc - start_utc).total_seconds() / 60)
    return real if real > 0 else clock_minutes(start, end, ends_next_day)


def dst_adjustment_minutes(
    day: dt.date, start: dt.time, end: dt.time, ends_next_day: bool, tz: ZoneInfo
) -> int:
    """Minutes gained (+) or lost (−) to a daylight-saving change."""
    return real_minutes(day, start, end, ends_next_day, tz) - clock_minutes(
        start, end, ends_next_day
    )


def amount_cents(rate_cents: int, minutes: int) -> int:
    """``rate × hours`` to the cent, half up (JS ``Math.round`` on a positive
    number, which Orbit's pay maths used)."""
    cents = Decimal(rate_cents) * Decimal(minutes) / Decimal(60)
    return int(cents.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def hours_of(minutes: int) -> Decimal:
    return (Decimal(minutes) / Decimal(60)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def ends_at(perf: EntPerformance, tz: ZoneInfo) -> dt.datetime:
    """When the booked performance ends, as an instant."""
    overnight = perf.ends_next_day or (
        perf.end_time.hour * 60 + perf.end_time.minute
        <= perf.start_time.hour * 60 + perf.start_time.minute
    )
    end_day = perf.performance_date + dt.timedelta(days=1 if overnight else 0)
    return local_to_utc(end_day, perf.end_time, tz)


def has_ended(
    perf: EntPerformance, tz: ZoneInfo, now: dt.datetime | None = None
) -> bool:
    return ends_at(perf, tz) <= (now or _now())


def parse_time(value, what: str = "time") -> dt.time:
    """``HH:MM`` (seconds tolerated) → a time; anything else is ``Invalid``."""
    if isinstance(value, dt.time):
        return value.replace(second=0, microsecond=0)
    text = str(value or "").strip()
    try:
        parsed = dt.time.fromisoformat(text[:5] if len(text) >= 5 else text)
    except ValueError as exc:
        raise Invalid(f"{what} must be HH:MM.") from exc
    return parsed.replace(second=0, microsecond=0)


def parse_date(value, what: str = "date") -> dt.date:
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value or "").strip()[:10])
    except ValueError as exc:
        raise Invalid(f"{what} must be YYYY-MM-DD.") from exc


def hm(value: dt.time | None) -> str | None:
    return value.strftime("%H:%M") if value is not None else None


def iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def day(value: dt.date | None) -> str | None:
    return value.isoformat() if value is not None else None


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def ip_hash(ip: str | None) -> str | None:
    """A keyed hash of a visitor's IP — enough to spot one source behind
    many submissions, never the address itself."""
    if not ip:
        return None
    return hashlib.sha256(f"{settings.JWT_SECRET}:{ip}".encode()).hexdigest()[:32]


def record_change(
    db: Session,
    perf: EntPerformance,
    *,
    source: str,
    field: str,
    old,
    new,
    user_id: str | None = None,
    name: str | None = None,
    note: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> EntPerformanceChange | None:
    """One audit row per field that actually changed (``old == new`` writes
    nothing). Values are stored as text."""
    old_text = None if old is None else str(old)
    new_text = None if new is None else str(new)
    if old_text == new_text:
        return None
    row = EntPerformanceChange(
        organization_id=perf.organization_id,
        performance_id=perf.id,
        changed_by_source=source,
        changed_by_user_id=user_id,
        changed_by_name=name,
        field=field,
        old_value=old_text,
        new_value=new_text,
        note=note,
        ip_hash=ip_hash(ip),
        user_agent=(user_agent or None) and str(user_agent)[:300],
    )
    db.add(row)
    return row


def actor_name(db: Session, ctx: FinanceCtx) -> str | None:
    if ctx.user is not None:
        return getattr(ctx.user, "full_name", None) or getattr(ctx.user, "email", None)
    if ctx.user_id:
        from app.db.models import User

        user = db.query(User).filter(User.id == ctx.user_id).first()
        return user.full_name if user else None
    return ctx.actor_label


def change_source(ctx: FinanceCtx) -> str:
    return {"staff": "manager", "performer": "performer"}.get(ctx.actor_kind, "system")
