"""Norm's business calendar — the one place a trading day is defined.

A hospitality day is not a civil day. Trade after midnight belongs to the day
that started the evening before, so a venue's day runs from its
``day_start_time`` (e.g. 07:00) to one second before the same time tomorrow.
Get that wrong and a late-night venue reads $0 for a Saturday, which looks
exactly like a POS outage rather than a bad window.

Before this module the rule existed three times and disagreed with itself:

1. ``internal_tools.resolve_dates`` — "7:00am" as prose inside an LLM prompt,
   applied per call by a model, with no venue and no way to test it.
2. ``mcp/instructions.py`` — the same sentence, hand-copied, for MCP clients.
3. ``reports_crud._resolve_date_placeholders`` — the only real implementation,
   reading ``Venue.day_start_time`` but defaulting to **00:00**, so dashboards
   and the agent could answer the same question differently for the same venue
   on the same day.

Everything now resolves through here. The rule is Python, per venue, and
testable; prose about it is documentation, never the source of truth.

Two deliberate choices:

- **Fail to a configured default, never to another venue.** The reports path
  used to fall back to "the first venue in the table with a day_start_time",
  silently applying one venue's boundary to another. A missing value now means
  ``settings.BUSINESS_DAY_START``, which is the same answer everywhere.
- **The window is half-open internally, inclusive on the wire.** ``end`` is one
  second before the next day starts, because the connector APIs we call treat
  the range as inclusive.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import settings

# Vocabulary the resolver understands deterministically. Anything outside this
# is genuinely fuzzy ("the week before the long weekend") and still goes to the
# LLM resolver — but these, the phrases that actually get used, never do.
DETERMINISTIC_PHRASES = frozenset(
    {
        "today",
        "yesterday",
        "tomorrow",
        "this week",
        "last week",
        "next week",
        "this month",
        "last month",
    }
)


@dataclass(frozen=True)
class Window:
    """A resolved business window, and enough context to explain itself."""

    start: dt.datetime
    end: dt.datetime
    kind: str  # "trading_day" | "trading_week" | "month" | "custom"
    label: str
    timezone: str
    day_start: str  # HH:MM actually applied

    @property
    def is_trading_aligned(self) -> bool:
        """Whether this window respects the venue's trading boundary.

        A custom range that happens to align still counts — what matters to a
        caller is whether the numbers mean "a trading day", not how we got here.
        """
        hh, mm = _parse_hhmm(self.day_start)
        return (self.start.hour, self.start.minute) == (hh, mm)

    def describe(self) -> str:
        """One line a human (or an LLM relaying to one) can check against."""
        fmt = "%a %d %b %H:%M"
        base = f"{self.start.strftime(fmt)} → {self.end.strftime(fmt)} {self.timezone}"
        if self.kind == "custom" and not self.is_trading_aligned:
            return (
                f"Custom window — {base}. Not a trading day: the venue's day "
                f"starts at {self.day_start}, so this splits a trading session."
            )
        return f"{self.label} — {base}"

    def as_dict(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "kind": self.kind,
            "label": self.label,
            "timezone": self.timezone,
            "day_start": self.day_start,
            "trading_aligned": self.is_trading_aligned,
            "description": self.describe(),
        }


# ── Venue settings ───────────────────────────────────────────────────────


def _parse_hhmm(value: str) -> tuple[int, int]:
    """ "HH:MM" -> (hour, minute). Malformed values fall back to midnight.

    Deliberately lenient: a typo in a venue's config should not 500 a dashboard.
    """
    try:
        parts = str(value).split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (ValueError, IndexError, AttributeError):
        pass
    return 0, 0


def day_start_for(venue) -> str:
    """The venue's business day start as "HH:MM".

    Falls back to the configured default — never to another venue's value.
    """
    value = getattr(venue, "day_start_time", None) if venue is not None else None
    if value:
        hour, minute = _parse_hhmm(value)
        return f"{hour:02d}:{minute:02d}"
    hour, minute = _parse_hhmm(settings.BUSINESS_DAY_START)
    return f"{hour:02d}:{minute:02d}"


def timezone_for(venue) -> ZoneInfo:
    """The venue's timezone, falling back to the configured default."""
    name = getattr(venue, "timezone", None) if venue is not None else None
    for candidate in (name, settings.SCHEDULER_TIMEZONE, "UTC"):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return ZoneInfo("UTC")


def timezone_name(venue) -> str:
    return str(timezone_for(venue))


def humanize_hhmm(hhmm: str) -> str:
    """ "07:00" -> "7:00am". For prose that quotes the boundary.

    Lives here so text describing the rule is generated from the rule. The MCP
    server instructions used to hand-write "7:00am", which meant changing the
    configured start silently made the guidance we send external clients false.
    """
    hour, minute = _parse_hhmm(hhmm)
    suffix = "am" if hour < 12 else "pm"
    display_hour = hour % 12 or 12
    return f"{display_hour}:{minute:02d}{suffix}"


def day_end_label(hhmm: str) -> str:
    """The inclusive end of a day starting at ``hhmm`` — "07:00" -> "6:59am"."""
    hour, minute = _parse_hhmm(hhmm)
    total = (hour * 60 + minute - 1) % (24 * 60)
    return humanize_hhmm(f"{total // 60:02d}:{total % 60:02d}")


# ── Boundaries ───────────────────────────────────────────────────────────


def _start_of_trading_day(moment: dt.datetime, day_start: str) -> dt.datetime:
    """The start of the trading day that ``moment`` falls inside.

    This is the rollover: at 02:00 with a 07:00 start, the trading day began at
    07:00 *yesterday* — which is exactly why post-midnight trade is not lost.
    """
    hour, minute = _parse_hhmm(day_start)
    start = moment.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if moment < start:
        start -= dt.timedelta(days=1)
    return start


def trading_day(
    venue, moment: dt.datetime | None = None, offset_days: int = 0
) -> Window:
    """The trading day containing ``moment`` (default: now), shifted by offset."""
    tz = timezone_for(venue)
    ds = day_start_for(venue)
    now = moment.astimezone(tz) if moment else dt.datetime.now(tz)
    start = _start_of_trading_day(now, ds) + dt.timedelta(days=offset_days)
    end = start + dt.timedelta(days=1) - dt.timedelta(seconds=1)
    labels = {0: "Today", -1: "Yesterday", 1: "Tomorrow"}
    label = labels.get(offset_days) or start.strftime("%A %d %b")
    return Window(start, end, "trading_day", label, str(tz), ds)


def trading_week(
    venue, moment: dt.datetime | None = None, offset_weeks: int = 0
) -> Window:
    """The trading week (Monday day-start → next Monday day-start) for ``moment``."""
    tz = timezone_for(venue)
    ds = day_start_for(venue)
    now = moment.astimezone(tz) if moment else dt.datetime.now(tz)
    day = _start_of_trading_day(now, ds)
    start = day - dt.timedelta(days=day.weekday()) + dt.timedelta(weeks=offset_weeks)
    end = start + dt.timedelta(days=7) - dt.timedelta(seconds=1)
    labels = {0: "This week", -1: "Last week", 1: "Next week"}
    label = labels.get(offset_weeks) or f"Week of {start.strftime('%d %b')}"
    return Window(start, end, "trading_week", label, str(tz), ds)


def trading_month(
    venue, moment: dt.datetime | None = None, offset_months: int = 0
) -> Window:
    """The month, bounded by the venue's day start rather than midnight."""
    tz = timezone_for(venue)
    ds = day_start_for(venue)
    now = moment.astimezone(tz) if moment else dt.datetime.now(tz)
    day = _start_of_trading_day(now, ds)
    first = day.replace(day=1)
    if offset_months:
        month_index = first.month - 1 + offset_months
        first = first.replace(
            year=first.year + month_index // 12, month=month_index % 12 + 1
        )
    nxt = (
        first.replace(year=first.year + 1, month=1)
        if first.month == 12
        else first.replace(month=first.month + 1)
    )
    end = nxt - dt.timedelta(seconds=1)
    labels = {0: "This month", -1: "Last month"}
    label = labels.get(offset_months) or first.strftime("%B %Y")
    return Window(first, end, "month", label, str(tz), ds)


def custom_window(venue, start: dt.datetime, end: dt.datetime) -> Window:
    """A caller-supplied range, honoured verbatim.

    Never snapped. Someone reconciling against a bank statement legitimately
    wants midnight-to-midnight; overriding that would just be a different way
    of returning the wrong number. ``describe()`` says whether it lines up with
    the trading day, so the difference is visible rather than silent.
    """
    tz = timezone_for(venue)
    ds = day_start_for(venue)
    return Window(
        start.astimezone(tz), end.astimezone(tz), "custom", "Custom window", str(tz), ds
    )


_WEEK_OF = re.compile(
    r"^(?:the\s+)?week\s+(?:beginning|starting|commencing|of|beg)\s+"
    r"(?:mon(?:day)?\s+)?(.+?)\s*$",
    re.IGNORECASE,
)


def week_beginning(venue, date_text: str) -> Window | None:
    """The trading week containing a given calendar date, or None.

    Exists so a caller can page through weeks without either computing
    timestamps itself or falling through to the LLM resolver. Both are traps:
    naive `+7 days` arithmetic breaks across a daylight-saving boundary, and the
    LLM path used to stamp today's UTC offset on every date it produced.

    Takes a *calendar date* and returns the trading week that contains it, so
    the caller only ever does date arithmetic — which has no clock, and so no
    DST hazard. The instant, the day start and the offset stay here.
    """
    text = (date_text or "").strip().rstrip(".,")
    if not text:
        return None
    parsed: dt.date | None = None
    for fmt in (
        "%Y-%m-%d",
        "%d %B %Y",
        "%d %b %Y",
        "%d %B",
        "%d %b",
        "%B %d %Y",
        "%b %d %Y",
    ):
        try:
            got = dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
        # Formats without a year default to 1900; assume the current year.
        parsed = got.date()
        if "%Y" not in fmt:
            parsed = parsed.replace(year=dt.date.today().year)
        break
    if parsed is None:
        return None

    tz = timezone_for(venue)
    # Midday avoids any transition hour; the week is derived from the date.
    anchor = dt.datetime(parsed.year, parsed.month, parsed.day, 12, 0, tzinfo=tz)
    week = trading_week(venue, anchor, 0)
    return Window(
        week.start,
        week.end,
        "trading_week",
        f"Week of {week.start.strftime('%d %b')}",
        str(tz),
        week.day_start,
    )


def resolve_phrase(
    venue, phrase: str, moment: dt.datetime | None = None
) -> Window | None:
    """Resolve a common phrase deterministically, or None if it isn't one.

    None means "ask the LLM resolver" — it is not an error.
    """
    key = (phrase or "").strip().lower()
    handlers = {
        "today": lambda: trading_day(venue, moment, 0),
        "yesterday": lambda: trading_day(venue, moment, -1),
        "tomorrow": lambda: trading_day(venue, moment, 1),
        "this week": lambda: trading_week(venue, moment, 0),
        "last week": lambda: trading_week(venue, moment, -1),
        "next week": lambda: trading_week(venue, moment, 1),
        "this month": lambda: trading_month(venue, moment, 0),
        "last month": lambda: trading_month(venue, moment, -1),
    }
    handler = handlers.get(key)
    if handler:
        return handler()

    # "week beginning 10 August 2026" — the shape a week-by-week navigator
    # needs, resolved here rather than by the LLM.
    match = _WEEK_OF.match(key)
    if match:
        return week_beginning(venue, match.group(1))
    return None


__all__ = [
    "DETERMINISTIC_PHRASES",
    "Window",
    "custom_window",
    "day_start_for",
    "resolve_phrase",
    "timezone_for",
    "timezone_name",
    "trading_day",
    "trading_month",
    "trading_week",
    "week_beginning",
]
