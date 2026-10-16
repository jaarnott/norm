"""Plans are local, facts are instants — the conversions between them.

A plan (an event on 14 March from 19:00 to 01:00) is stored as the venue's
local date and times plus ``ends_next_day``. It becomes an instant only when
something needs one — a reminder, a calendar file, "is it on now" — and only
through ``to_instant``, with the VENUE's timezone. Orbit converted plans to
timestamps up front with whatever zone was assumed (New York by default), and
Auckland events drifted by a day.

Unlike ``business_calendar`` (which falls back to a configured zone for
reporting), Functions refuses a venue with no timezone: a booking at the
wrong instant is worse than an error the venue can fix in Settings.

Two DST rules, both zoneinfo's own:

* A local time that does not exist (the spring-forward gap, 02:00–03:00 in
  Pacific/Auckland) is read with the offset in force before the gap — 02:30
  becomes 03:30 daylight time.
* A local time that happens twice (the autumn fall-back hour) means the FIRST
  occurrence, still on daylight time (``fold=0``).
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.services.business_calendar import _start_of_trading_day, day_start_for
from app.services.functions.errors import Invalid

UTC = dt.timezone.utc


def validate_timezone(name: str) -> str:
    """The IANA name back if it is real, else ``Invalid``."""
    candidate = (name or "").strip()
    if not candidate or candidate.upper() == "LOCAL":
        raise Invalid("A timezone is required, e.g. Pacific/Auckland.")
    try:
        ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise Invalid(
            f"'{candidate}' is not a timezone (try Pacific/Auckland)."
        ) from exc
    return candidate


def venue_tz(venue) -> ZoneInfo:
    """The venue's timezone. Required for Functions — raises ``Invalid``
    naming the venue when it is missing or not a real zone."""
    name = getattr(venue, "timezone", None)
    try:
        return ZoneInfo(validate_timezone(name or ""))
    except Invalid as exc:
        label = getattr(venue, "name", None) or "This venue"
        raise Invalid(
            f"{label} has no valid timezone set — add one in Settings before "
            "using Functions there."
        ) from exc


def _zone(venue_or_tz) -> ZoneInfo:
    if isinstance(venue_or_tz, ZoneInfo):
        return venue_or_tz
    if isinstance(venue_or_tz, str):
        return ZoneInfo(validate_timezone(venue_or_tz))
    return venue_tz(venue_or_tz)


def to_instant(
    venue_or_tz, date: dt.date, time: dt.time | None = None, next_day: bool = False
) -> dt.datetime:
    """A venue-local plan as a UTC instant. ``time`` None means the start of
    the day; ``next_day`` moves it to the following date (an end time past
    midnight)."""
    tz = _zone(venue_or_tz)
    day = date + dt.timedelta(days=1) if next_day else date
    local = dt.datetime.combine(day, time or dt.time(0, 0), tzinfo=tz)
    return local.astimezone(UTC)


def plan_window(
    venue_or_tz,
    date: dt.date,
    start: dt.time | None,
    end: dt.time | None,
    ends_next_day: bool = False,
) -> tuple[dt.datetime, dt.datetime | None]:
    """``(start, end)`` instants for a local plan; end None when unknown."""
    start_at = to_instant(venue_or_tz, date, start)
    end_at = to_instant(venue_or_tz, date, end, ends_next_day) if end else None
    return start_at, end_at


def infer_ends_next_day(start: dt.time | None, end: dt.time | None) -> bool:
    """An end at or before the start can only be tomorrow (19:00–01:00)."""
    return bool(start and end and end <= start)


def validate_window(
    start: dt.time | None, end: dt.time | None, ends_next_day: bool
) -> None:
    """Refuse a window that ends before it starts on the same day."""
    if start and end and not ends_next_day and end <= start:
        raise Invalid(
            "The end time is before the start time — tick 'ends next day' if it "
            "finishes after midnight."
        )


def local_now(venue_or_tz, now: dt.datetime | None = None) -> dt.datetime:
    tz = _zone(venue_or_tz)
    return (now or dt.datetime.now(UTC)).astimezone(tz)


def local_today(venue_or_tz, now: dt.datetime | None = None) -> dt.date:
    """The civil date at the venue — what "today" means for a follow-up date
    or a calendar."""
    return local_now(venue_or_tz, now).date()


def business_date(venue, at: dt.datetime | None = None) -> dt.date:
    """The trading day an instant belongs to, by the venue's
    ``day_start_time`` (``business_calendar``'s rule): 01:30 on Sunday with a
    07:00 day start is Saturday's trade."""
    tz = venue_tz(venue)
    moment = (at or dt.datetime.now(UTC)).astimezone(tz)
    return _start_of_trading_day(moment, day_start_for(venue)).date()
