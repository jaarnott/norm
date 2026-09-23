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
    kind: str  # "trading_day" | "trading_week" | "month" | "trading_range" | "custom"
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

    @property
    def is_incomplete(self) -> bool:
        """Whether the window extends beyond now — figures are partial.

        A Sunday-so-far presented as a finished day is how a partial week
        gets quoted as a result (thread b9bda2c1, 23 Aug 2026); every tool
        that states its window inherits this flag automatically.
        """
        return self.end > dt.datetime.now(self.end.tzinfo or dt.timezone.utc)

    def describe(self) -> str:
        """One line a human (or an LLM relaying to one) can check against."""
        fmt = "%a %d %b %H:%M"
        base = f"{self.start.strftime(fmt)} → {self.end.strftime(fmt)} {self.timezone}"
        suffix = (
            " (in progress — includes the current trading day, figures are partial)"
            if self.is_incomplete
            else ""
        )
        if self.kind == "custom" and not self.is_trading_aligned:
            return (
                f"Custom window — {base}. Not a trading day: the venue's day "
                f"starts at {self.day_start}, so this splits a trading "
                f"session.{suffix}"
            )
        return f"{self.label} — {base}{suffix}"

    def as_dict(self) -> dict:
        out = {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "kind": self.kind,
            "label": self.label,
            "timezone": self.timezone,
            "day_start": self.day_start,
            "trading_aligned": self.is_trading_aligned,
            "description": self.describe(),
        }
        if self.is_incomplete:
            out["incomplete"] = True
            out["through"] = dt.datetime.now(
                self.end.tzinfo or dt.timezone.utc
            ).isoformat(timespec="seconds")
        return out


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


# ── Calendar dates, months, quarters and ranges ──────────────────────────
#
# "April 2026", "1 April 2026 to 30 June 2026", "April to June", "Q2 2026".
# These used to fall through to the LLM resolver, whose prompt told it a month
# runs "1st 00:00:00 to last day 23:59:59" — midnight. Every such window then
# failed the trading-day check, the tool asked "did the user ask for these
# exact clock times?", and the agent re-ran each call with confirmed_by_user —
# doubling the calls on every multi-month question (threads fa1cfd1c and
# 01688f94, 23 Sep 2026; "April to June 2026" did not resolve at all for five
# of six venues). A date names a trading DAY here: 1 April starts at the
# venue's day start, 30 June ends one second before the next day starts.

_MONTHS: dict[str, int] = {}
for _i, _name in enumerate(
    (
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    ),
    start=1,
):
    _MONTHS[_name] = _i
    _MONTHS[_name[:3]] = _i
_MONTHS["sept"] = 9

_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))
_ORDINAL = r"(\d{1,2})(?:st|nd|rd|th)?"
# One side of a range: a day, or a whole month. Year optional throughout.
_POINT_PATTERNS = (
    ("iso", re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")),
    ("dmy_num", re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")),  # NZ: day first
    (
        "d_month",
        re.compile(rf"^{_ORDINAL}\s+(?:of\s+)?({_MONTH_RE})(?:\s+(\d{{4}}))?$"),
    ),
    ("month_d", re.compile(rf"^({_MONTH_RE})\s+{_ORDINAL}(?:\s+(\d{{4}}))?$")),
    ("month", re.compile(rf"^({_MONTH_RE})(?:\s+(\d{{4}}))?$")),
)
_RANGE_SPLIT = re.compile(
    r"\s+(?:to|through|thru|until|till)\s+|\s+[-–—]\s+|\s*[–—]\s*"
)
_QUARTER = re.compile(r"^(?:q|quarter\s+)([1-4])(?:\s+(\d{4}))?$")
_LEAD = re.compile(r"^(?:from|between|for|over|during|the\s+period(?:\s+of)?)\s+")


@dataclass(frozen=True)
class _Point:
    year: int | None
    month: int
    day: int | None  # None → the whole month


def _parse_point(text: str) -> _Point | None:
    text = text.strip().strip(",.")
    for kind, pattern in _POINT_PATTERNS:
        m = pattern.match(text)
        if not m:
            continue
        g = m.groups()
        if kind == "iso":
            return _Point(int(g[0]), int(g[1]), int(g[2]))
        if kind == "dmy_num":
            return _Point(int(g[2]), int(g[1]), int(g[0]))
        if kind == "d_month":
            return _Point(int(g[2]) if g[2] else None, _MONTHS[g[1]], int(g[0]))
        if kind == "month_d":
            return _Point(int(g[2]) if g[2] else None, _MONTHS[g[0]], int(g[1]))
        return _Point(int(g[1]) if g[1] else None, _MONTHS[g[0]], None)
    return None


def _month_list(text: str) -> tuple[_Point, _Point] | None:
    """ "April, May and June 2026" → (April, June), if the months are contiguous."""
    m = re.match(
        rf"^((?:(?:{_MONTH_RE})(?:\s*,\s*|\s+and\s+|\s*&\s*))+(?:{_MONTH_RE}))(?:\s+(\d{{4}}))?$",
        text,
    )
    if not m:
        return None
    months = [_MONTHS[w] for w in re.findall(_MONTH_RE, m.group(1))]
    if any(b - a != 1 for a, b in zip(months, months[1:])):
        return None  # "April and June" is not a range
    year = int(m.group(2)) if m.group(2) else None
    return _Point(year, months[0], None), _Point(year, months[-1], None)


def _fill_years(start: _Point, end: _Point, today: dt.date) -> tuple[_Point, _Point]:
    """Share a year stated on one side; otherwise take the most recent start."""
    sy, ey = start.year, end.year
    before = (start.month, start.day or 1) > (end.month, end.day or 31)
    if sy is None and ey is not None:
        sy = ey - 1 if before else ey
    elif ey is None and sy is not None:
        ey = sy + 1 if before else sy
    elif sy is None and ey is None:
        sy = today.year
        if (start.month, start.day or 1) > (today.month, today.day):
            sy -= 1  # "April" said in February means last April
        ey = sy + 1 if before else sy
    return _Point(sy, start.month, start.day), _Point(ey, end.month, end.day)


def _trading_bounds(
    venue, start: _Point, end: _Point
) -> tuple[dt.datetime, dt.datetime]:
    tz = timezone_for(venue)
    hh, mm = _parse_hhmm(day_start_for(venue))
    first = dt.date(start.year, start.month, start.day or 1)
    if end.day is None:
        last_excl = (
            dt.date(end.year + 1, 1, 1)
            if end.month == 12
            else dt.date(end.year, end.month + 1, 1)
        )
    else:
        last_excl = dt.date(end.year, end.month, end.day) + dt.timedelta(days=1)
    # Wall-clock construction: ZoneInfo derives each side's offset from its own
    # date, so a range spanning a daylight-saving change is right at both ends.
    s = dt.datetime(first.year, first.month, first.day, hh, mm, tzinfo=tz)
    e = dt.datetime(
        last_excl.year, last_excl.month, last_excl.day, hh, mm, tzinfo=tz
    ) - dt.timedelta(seconds=1)
    return s, e


def _range_label(start: _Point, end: _Point) -> str:
    def month(p):
        return dt.date(2000, p.month, 1).strftime("%B")

    if start.day is None and end.day is None:
        if (start.year, start.month) == (end.year, end.month):
            return f"{month(start)} {start.year}"
        if start.year == end.year:
            return f"{month(start)} – {month(end)} {end.year}"
        return f"{month(start)} {start.year} – {month(end)} {end.year}"
    s_day = start.day or 1
    e = dt.date(end.year, end.month, end.day) if end.day else None
    left = dt.date(start.year, start.month, s_day).strftime("%d %b")
    right = e.strftime("%d %b %Y") if e else f"{month(end)} {end.year}"
    if start.year != end.year:
        left += f" {start.year}"
    return f"{left} – {right}"


def calendar_range(
    venue, phrase: str, moment: dt.datetime | None = None
) -> Window | None:
    """A date, month, month list, quarter or "X to Y" range on the trading day.

    None when the phrase is none of those — the caller falls back to the LLM.
    """
    text = _LEAD.sub("", (phrase or "").strip().lower().rstrip(".,"))
    text = re.sub(r"\s+", " ", text)
    tz = timezone_for(venue)
    today = (moment.astimezone(tz) if moment else dt.datetime.now(tz)).date()

    pair: tuple[_Point, _Point] | None = None
    label: str | None = None
    q = _QUARTER.match(text)
    if q:
        n = int(q.group(1))
        year = int(q.group(2)) if q.group(2) else None
        pair = (_Point(year, 3 * n - 2, None), _Point(year, 3 * n, None))
        label = f"Q{n}"
    if pair is None:
        pair = _month_list(text)
    if pair is None:
        if (phrase or "").strip().lower().startswith("between "):
            text = text.replace(" and ", " to ", 1)  # "between X and Y"
        parts = [p for p in _RANGE_SPLIT.split(text) if p.strip()]
        if len(parts) == 1:
            point = _parse_point(parts[0])
            if point:
                pair = (point, point)
        elif len(parts) == 2:
            a, b = _parse_point(parts[0]), _parse_point(parts[1])
            if a and b:
                pair = (a, b)
    if pair is None:
        return None

    start, end = _fill_years(pair[0], pair[1], today)
    try:
        s, e = _trading_bounds(venue, start, end)
    except ValueError:  # 31 April, month 13 …
        return None
    if e <= s:
        return None

    if label:
        label = f"{label} {start.year} ({_range_label(start, end)})"
    elif start == end and start.day is not None:
        label = s.strftime("%A %d %b %Y")
    else:
        label = _range_label(start, end)
    if start.day is None and (start.year, start.month) == (end.year, end.month):
        kind = "month"
    elif start == end:
        kind = "trading_day"
    else:
        kind = "trading_range"
    return Window(s, e, kind, label, str(tz), day_start_for(venue))


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
        # Year-on-year anchors are DETERMINISTIC: the LLM resolver answered
        # "same week last year" two different ways within one conversation
        # (25 Aug vs 18 Aug 2025 — two YoY baselines in one thread,
        # b9bda2c1, 23 Aug 2026). 52 trading weeks back keeps the weekday
        # alignment a like-for-like comparison needs.
        "same week last year": lambda: trading_week(venue, moment, -52),
        "this week last year": lambda: trading_week(venue, moment, -52),
        "last week last year": lambda: trading_week(venue, moment, -53),
        "same day last year": lambda: trading_day(venue, moment, -364),
        "this day last year": lambda: trading_day(venue, moment, -364),
    }
    handler = handlers.get(key)
    if handler:
        return handler()

    # "week beginning 10 August 2026" — the shape a week-by-week navigator
    # needs, resolved here rather than by the LLM.
    match = _WEEK_OF.match(key)
    if match:
        return week_beginning(venue, match.group(1))
    return calendar_range(venue, key, moment)


__all__ = [
    "DETERMINISTIC_PHRASES",
    "Window",
    "calendar_range",
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
