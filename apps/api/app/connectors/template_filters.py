"""Custom Jinja2 filters for connector spec templates."""

from datetime import datetime


def split_name(name: str, part: str) -> str:
    """Split a full name into first or last part.

    >>> split_name("Sarah Johnson", "first")
    'Sarah'
    >>> split_name("Sarah Johnson", "last")
    'Johnson'
    """
    if not name:
        return ""
    parts = name.rsplit(" ", 1)
    if part == "first":
        return parts[0]
    if part == "last":
        return parts[1] if len(parts) > 1 else ""
    raise ValueError(f"split_name part must be 'first' or 'last', got '{part}'")


def format_date(date_str: str, fmt: str = "%Y-%m-%d") -> str:
    """Parse a date string and reformat it.

    Tries common input formats before giving up.
    """
    if not date_str:
        return ""
    input_formats = [
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B %Y",
        "%d %b %Y",
    ]
    for ifmt in input_formats:
        try:
            dt = datetime.strptime(date_str, ifmt)
            return dt.strftime(fmt)
        except ValueError:
            continue
    return date_str  # return as-is if no format matched


def shift_days(date_str: str, days) -> str:
    """Shift a date by N days, returning YYYY-MM-DD (works on the date part only).

    Accepts a bare date ("2026-07-27") or an ISO datetime
    ("2026-07-27T07:00:00+12:00") and ignores any time/offset.

    Motivating use: LoadedHub's budget endpoint (`/api/budgets?from=&to=`) parses
    bare `from`/`to` as UTC, but budgets are stamped at the venue's local
    midnight — so the requested `from` day sits just outside the window and is
    dropped, understating every weekly budget by its first day (verified live,
    Aug 2026). Templating `from={{ from_date | shift_days(-1) }}` requests one
    day earlier; LoadedHub then drops *that* day and returns the intended window.
    Returns "" for empty input.
    """
    from datetime import date, timedelta

    if not date_str:
        return ""
    d = date.fromisoformat(str(date_str)[:10])
    return (d + timedelta(days=int(days))).isoformat()


def flatten_venue(venue: dict | str) -> str:
    """Extract venue name from a dict or return string as-is."""
    if isinstance(venue, dict):
        return venue.get("name", str(venue))
    return str(venue) if venue else ""


def default_if_none(value, default=""):
    """Return default if value is None."""
    if value is None:
        return default
    return value


# Registry of all custom filters for Jinja2 environment
TEMPLATE_FILTERS = {
    "split_name": split_name,
    "format_date": format_date,
    "shift_days": shift_days,
    "flatten_venue": flatten_venue,
    "default_if_none": default_if_none,
}
