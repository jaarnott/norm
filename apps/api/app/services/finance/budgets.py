"""The locked daily sales budget and its monthly lines (plan §6.3, 04 §12 A/E).

A venue's budget for a fiscal year is a **version** (``fin_budget_versions``)
holding one GST-inclusive amount and an optional event note per day
(``fin_budget_days``). "Locked at the start of the year" is a mechanism:
the current budget is the latest *locked* version; an unlocked version is a
draft that can still be edited. Locking and unlocking need ``budgets:write``;
a locked version's days are never edited in place — unlock it, or make a new
version — so the figure the Weekly Summary and the forecast strip read is the
one someone locked, not whatever was typed last.

Monthly lines (``fin_budget_lines``) carry the items D33 keeps: a wage
percentage *or* a wage amount, COGS percentages, and the dollar lines
(leave, opex, depreciation) plus a rebates percentage. Exactly one of
``value_cents`` / ``value_ratio`` is set per line (the table's CHECK), and a
month holds ``wage_pct`` or ``wage_cents``, never both — Orbit's rule, kept.

Fiscal years are named by the calendar year they END in (NZ April–March:
Apr 2026 – Mar 2027 is FY2027), from ``organizations.fiscal_year_start_month``
(AU: 7). A January start makes the fiscal year the calendar year.
"""

from __future__ import annotations

import calendar
import datetime as dt
from decimal import Decimal, InvalidOperation

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.finance_models import (
    BUDGET_LINE_ITEMS,
    BUDGET_SOURCES,
    FinBudgetDay,
    FinBudgetLine,
    FinBudgetVersion,
    FinVenueSettings,
)
from app.db.models import Organization, _now
from app.services.finance_core.ctx import FinanceCtx, require_venue
from app.services.finance_core.errors import Conflict, Invalid, NotFound, flush

#: Lines a month may hold only one of.
_WAGE_LINES = ("wage_pct", "wage_cents")
#: A ratio line is a fraction (0.29 = 29%); anything above this is a typo.
_MAX_RATIO = Decimal("5")


# ---------------------------------------------------------------------------
# Fiscal years
# ---------------------------------------------------------------------------


def fiscal_year_of(date: dt.date, start_month: int) -> int:
    """The fiscal year a date belongs to, named by the year it ends in."""
    if start_month <= 1:
        return date.year
    return date.year + 1 if date.month >= start_month else date.year


def fiscal_bounds(fiscal_year: int, start_month: int) -> tuple[dt.date, dt.date]:
    """First and last calendar date of a fiscal year."""
    if start_month <= 1:
        return dt.date(fiscal_year, 1, 1), dt.date(fiscal_year, 12, 31)
    first = dt.date(fiscal_year - 1, start_month, 1)
    last = dt.date(fiscal_year, start_month, 1) - dt.timedelta(days=1)
    return first, last


def fiscal_months(fiscal_year: int, start_month: int) -> list[tuple[int, int]]:
    """``(year, month)`` for each month of the fiscal year, in order."""
    first, _ = fiscal_bounds(fiscal_year, start_month)
    out = []
    y, m = first.year, first.month
    for _ in range(12):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def start_month(db: Session, org_id: str) -> int:
    value = (
        db.query(Organization.fiscal_year_start_month)
        .filter(Organization.id == org_id)
        .scalar()
    )
    return int(value or 4)


def fiscal_year_for(db: Session, org_id: str, date: dt.date) -> int:
    return fiscal_year_of(date, start_month(db, org_id))


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


def _version(db: Session, ctx: FinanceCtx, version_id: str) -> FinBudgetVersion:
    row = (
        db.query(FinBudgetVersion)
        .filter(
            FinBudgetVersion.organization_id == ctx.org_id,
            FinBudgetVersion.id == version_id,
        )
        .first()
    )
    if row is None:
        raise NotFound("Budget version not found.")
    require_venue(ctx, row.venue_id)
    return row


def get_version(db: Session, ctx: FinanceCtx, version_id: str) -> FinBudgetVersion:
    ctx.require("finance:read")
    return _version(db, ctx, version_id)


def list_versions(
    db: Session, ctx: FinanceCtx, venue_id: str, fiscal_year: int | None = None
) -> list[FinBudgetVersion]:
    ctx.require("finance:read")
    require_venue(ctx, venue_id)
    q = db.query(FinBudgetVersion).filter(
        FinBudgetVersion.organization_id == ctx.org_id,
        FinBudgetVersion.venue_id == venue_id,
    )
    if fiscal_year is not None:
        q = q.filter(FinBudgetVersion.fiscal_year == fiscal_year)
    return q.order_by(FinBudgetVersion.created_at.desc()).all()


def version_stats(db: Session, version_ids: list[str]) -> dict[str, tuple[int, int]]:
    """``{version_id: (days, total_cents)}`` for the list pages."""
    if not version_ids:
        return {}
    rows = (
        db.query(
            FinBudgetDay.version_id,
            func.count(FinBudgetDay.id),
            func.coalesce(func.sum(FinBudgetDay.amount_cents), 0),
        )
        .filter(FinBudgetDay.version_id.in_(version_ids))
        .group_by(FinBudgetDay.version_id)
        .all()
    )
    return {vid: (int(n), int(total)) for vid, n, total in rows}


def create_version(
    db: Session,
    ctx: FinanceCtx,
    venue_id: str,
    fiscal_year: int,
    *,
    source: str = "manual",
    label: str | None = None,
    notes: str | None = None,
    copy_from_version_id: str | None = None,
) -> FinBudgetVersion:
    """A new, unlocked version — empty, or a copy of another version's days."""
    ctx.require("budgets:write")
    require_venue(ctx, venue_id)
    if source not in BUDGET_SOURCES:
        raise Invalid(
            f"A budget version's source is one of {', '.join(BUDGET_SOURCES)}."
        )
    if not (2000 <= int(fiscal_year) <= 2100):
        raise Invalid("fiscal_year must be a four-digit year.")
    row = FinBudgetVersion(
        organization_id=ctx.org_id,
        venue_id=venue_id,
        fiscal_year=int(fiscal_year),
        label=(label or "").strip() or None,
        source=source,
        notes=(notes or "").strip() or None,
        created_by=ctx.user_id,
    )
    db.add(row)
    flush(db)
    if copy_from_version_id:
        src = _version(db, ctx, copy_from_version_id)
        if src.venue_id != venue_id:
            raise Invalid("A version can only be copied within the same venue.")
        for d in db.query(FinBudgetDay).filter(FinBudgetDay.version_id == src.id):
            db.add(
                FinBudgetDay(
                    organization_id=ctx.org_id,
                    version_id=row.id,
                    date=d.date,
                    amount_cents=d.amount_cents,
                    note=d.note,
                )
            )
        flush(db)
    return row


def lock_version(db: Session, ctx: FinanceCtx, version_id: str) -> FinBudgetVersion:
    """Lock: from now on this is the venue-year's current budget."""
    ctx.require("budgets:write")
    row = _version(db, ctx, version_id)
    if row.locked_at is None:
        row.locked_at = _now()
        row.locked_by = ctx.user_id
        flush(db)
    return row


def unlock_version(db: Session, ctx: FinanceCtx, version_id: str) -> FinBudgetVersion:
    ctx.require("budgets:write")
    row = _version(db, ctx, version_id)
    row.locked_at = None
    row.locked_by = None
    flush(db)
    return row


def update_version(
    db: Session, ctx: FinanceCtx, version_id: str, *, label=None, notes=None
) -> FinBudgetVersion:
    ctx.require("budgets:write")
    row = _version(db, ctx, version_id)
    if label is not None:
        row.label = str(label).strip() or None
    if notes is not None:
        row.notes = str(notes).strip() or None
    flush(db)
    return row


def current_version(
    db: Session, org_id: str, venue_id: str, fiscal_year: int
) -> FinBudgetVersion | None:
    """The latest LOCKED version for the venue-year — the budget everyone
    reads. No ctx: callers (the summary built-in, the nightly job, the grid)
    have already checked the venue."""
    return (
        db.query(FinBudgetVersion)
        .filter(
            FinBudgetVersion.organization_id == org_id,
            FinBudgetVersion.venue_id == venue_id,
            FinBudgetVersion.fiscal_year == fiscal_year,
            FinBudgetVersion.locked_at.is_not(None),
        )
        .order_by(FinBudgetVersion.locked_at.desc(), FinBudgetVersion.created_at.desc())
        .first()
    )


# ---------------------------------------------------------------------------
# Days
# ---------------------------------------------------------------------------


def _date(value, what: str = "date") -> dt.date:
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError) as exc:
        raise Invalid(f"{what} must be YYYY-MM-DD, got {value!r}.") from exc


def _cents(value, what: str) -> int:
    if isinstance(value, bool):
        raise Invalid(f"{what} must be a whole number of cents.")
    try:
        cents = int(value)
    except (TypeError, ValueError) as exc:
        raise Invalid(f"{what} must be a whole number of cents.") from exc
    if cents < 0:
        raise Invalid(f"{what} cannot be negative.")
    return cents


def set_days(db: Session, ctx: FinanceCtx, version_id: str, days: list[dict]) -> int:
    """Upsert days into an UNLOCKED version. ``days`` are
    ``{date, amount_cents, note?}``; a note given as ``None`` clears it, a
    note left out keeps it. Returns the number of days written."""
    ctx.require("budgets:write")
    version = _version(db, ctx, version_id)
    if version.locked_at is not None:
        raise Conflict(
            "This budget version is locked — unlock it or create a new version."
        )
    if not isinstance(days, list) or not days:
        raise Invalid("days must be a non-empty list.")
    wanted: dict[dt.date, dict] = {}
    for item in days:
        if not isinstance(item, dict):
            raise Invalid("Each day is {date, amount_cents, note}.")
        date = _date(item.get("date"))
        if date in wanted:
            raise Invalid(f"{date.isoformat()} is listed twice.")
        wanted[date] = {
            "amount_cents": _cents(item.get("amount_cents"), "amount_cents"),
            "note": item.get("note", ...),
        }
    existing = {
        d.date: d
        for d in db.query(FinBudgetDay).filter(
            FinBudgetDay.version_id == version.id,
            FinBudgetDay.date.in_(list(wanted)),
        )
    }
    for date, fields in wanted.items():
        row = existing.get(date)
        if row is None:
            row = FinBudgetDay(
                organization_id=ctx.org_id, version_id=version.id, date=date
            )
            db.add(row)
        row.amount_cents = fields["amount_cents"]
        if fields["note"] is not ...:
            note = fields["note"]
            row.note = (str(note).strip() or None) if note is not None else None
    flush(db)
    return len(wanted)


def days_of_version(
    db: Session, version_id: str, first: dt.date, last: dt.date
) -> dict[dt.date, FinBudgetDay]:
    return {
        d.date: d
        for d in db.query(FinBudgetDay).filter(
            FinBudgetDay.version_id == version_id,
            FinBudgetDay.date >= first,
            FinBudgetDay.date <= last,
        )
    }


def locked_days(
    db: Session, org_id: str, venue_id: str, first: dt.date, last: dt.date
) -> dict[dt.date, FinBudgetDay]:
    """The current (locked) budget's days over a range — across fiscal years
    when the range spans one. Days with no locked version are absent."""
    sm = start_month(db, org_id)
    out: dict[dt.date, FinBudgetDay] = {}
    fy = fiscal_year_of(first, sm)
    last_fy = fiscal_year_of(last, sm)
    while fy <= last_fy:
        version = current_version(db, org_id, venue_id, fy)
        if version is not None:
            lo, hi = fiscal_bounds(fy, sm)
            out.update(days_of_version(db, version.id, max(lo, first), min(hi, last)))
        fy += 1
    return out


def locked_cents(
    db: Session, org_id: str, venue_id: str, first: dt.date, last: dt.date
) -> dict[dt.date, int]:
    return {
        d: row.amount_cents
        for d, row in locked_days(db, org_id, venue_id, first, last).items()
    }


def month_bounds(year: int, month: int) -> tuple[dt.date, dt.date]:
    if not (1 <= int(month) <= 12):
        raise Invalid("month must be 1–12.")
    first = dt.date(int(year), int(month), 1)
    return first, first.replace(day=calendar.monthrange(first.year, first.month)[1])


# ---------------------------------------------------------------------------
# Monthly lines
# ---------------------------------------------------------------------------


def _ratio(value, what: str) -> Decimal:
    try:
        ratio = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise Invalid(f"{what} must be a decimal ratio (0.29 = 29%).") from exc
    if ratio < 0 or ratio > _MAX_RATIO:
        raise Invalid(f"{what} is out of range (a ratio, 0.29 = 29%).")
    return ratio.quantize(Decimal("0.000001"))


def _line_value(line_item: str, value: dict) -> dict:
    """``{"value_cents": …}`` or ``{"value_ratio": …}`` — exactly one, and the
    one the line item's name says."""
    if line_item not in BUDGET_LINE_ITEMS:
        raise Invalid(f"Unknown line item {line_item!r}.")
    if not isinstance(value, dict):
        raise Invalid(f"{line_item}: give {{value_cents}} or {{value_ratio}}.")
    has_cents = value.get("value_cents") is not None
    has_ratio = value.get("value_ratio") is not None
    if has_cents == has_ratio:
        raise Invalid(f"{line_item}: exactly one of value_cents / value_ratio.")
    if line_item.endswith("_cents"):
        if not has_cents:
            raise Invalid(f"{line_item} is a dollar line — give value_cents.")
        return {
            "value_cents": _cents(value["value_cents"], line_item),
            "value_ratio": None,
        }
    if not has_ratio:
        raise Invalid(f"{line_item} is a percentage line — give value_ratio.")
    return {"value_cents": None, "value_ratio": _ratio(value["value_ratio"], line_item)}


def lines_for_year(
    db: Session, ctx: FinanceCtx, venue_id: str, fiscal_year: int
) -> list[FinBudgetLine]:
    ctx.require("finance:read")
    require_venue(ctx, venue_id)
    return (
        db.query(FinBudgetLine)
        .filter(
            FinBudgetLine.organization_id == ctx.org_id,
            FinBudgetLine.venue_id == venue_id,
            FinBudgetLine.fiscal_year == int(fiscal_year),
        )
        .order_by(FinBudgetLine.month, FinBudgetLine.line_item)
        .all()
    )


def lines_map(
    db: Session, org_id: str, venue_id: str, fiscal_years: set[int]
) -> dict[tuple[int, int, str], FinBudgetLine]:
    """``{(fiscal_year, month, line_item): row}`` — the summary's lookup."""
    if not fiscal_years:
        return {}
    rows = db.query(FinBudgetLine).filter(
        FinBudgetLine.organization_id == org_id,
        FinBudgetLine.venue_id == venue_id,
        FinBudgetLine.fiscal_year.in_(sorted(fiscal_years)),
    )
    return {(r.fiscal_year, r.month, r.line_item): r for r in rows}


def set_lines(
    db: Session,
    ctx: FinanceCtx,
    venue_id: str,
    fiscal_year: int,
    month: int,
    lines: dict,
) -> list[FinBudgetLine]:
    """Set a month's lines. ``lines`` is ``{line_item: {value_cents | value_ratio} | None}``;
    ``None`` removes the line. Setting a wage line clears the other wage line
    (a month budgets wages as a percentage OR a dollar amount)."""
    ctx.require("budgets:write")
    require_venue(ctx, venue_id)
    if not (1 <= int(month) <= 12):
        raise Invalid("month must be 1–12.")
    if not isinstance(lines, dict) or not lines:
        raise Invalid("lines must be a non-empty object.")
    wanted: dict[str, dict | None] = {}
    for item, value in lines.items():
        wanted[str(item)] = None if value is None else _line_value(str(item), value)
    given_wage = [k for k in _WAGE_LINES if wanted.get(k) is not None]
    if len(given_wage) > 1:
        raise Invalid("A month budgets wages as a percentage or an amount, not both.")
    if given_wage:
        other = _WAGE_LINES[1 - _WAGE_LINES.index(given_wage[0])]
        wanted.setdefault(other, None)
    existing = {
        r.line_item: r
        for r in db.query(FinBudgetLine).filter(
            FinBudgetLine.organization_id == ctx.org_id,
            FinBudgetLine.venue_id == venue_id,
            FinBudgetLine.fiscal_year == int(fiscal_year),
            FinBudgetLine.month == int(month),
        )
    }
    for item, value in wanted.items():
        row = existing.get(item)
        if value is None:
            if row is not None:
                db.delete(row)
            continue
        if row is None:
            row = FinBudgetLine(
                organization_id=ctx.org_id,
                venue_id=venue_id,
                fiscal_year=int(fiscal_year),
                month=int(month),
                line_item=item,
            )
            db.add(row)
        row.value_cents = value["value_cents"]
        row.value_ratio = value["value_ratio"]
    flush(db)
    return (
        db.query(FinBudgetLine)
        .filter(
            FinBudgetLine.organization_id == ctx.org_id,
            FinBudgetLine.venue_id == venue_id,
            FinBudgetLine.fiscal_year == int(fiscal_year),
            FinBudgetLine.month == int(month),
        )
        .order_by(FinBudgetLine.line_item)
        .all()
    )


# ---------------------------------------------------------------------------
# Venue settings
# ---------------------------------------------------------------------------

#: What a venue without a settings row behaves like.
DEFAULT_SETTINGS = {
    "holiday_region": None,
    "closed_weekdays": [],
    "level_correction_on": False,
}


def venue_settings(db: Session, org_id: str, venue_id: str) -> FinVenueSettings | None:
    return (
        db.query(FinVenueSettings)
        .filter(
            FinVenueSettings.organization_id == org_id,
            FinVenueSettings.venue_id == venue_id,
        )
        .first()
    )


def settings_dict(row: FinVenueSettings | None, venue_id: str) -> dict:
    if row is None:
        return {"venue_id": venue_id, **DEFAULT_SETTINGS, "version": 0}
    return {
        "venue_id": venue_id,
        "holiday_region": row.holiday_region,
        "closed_weekdays": sorted(int(d) for d in (row.closed_weekdays or [])),
        "level_correction_on": bool(row.level_correction_on),
        "version": row.version,
    }


def set_venue_settings(
    db: Session,
    ctx: FinanceCtx,
    venue_id: str,
    *,
    holiday_region=...,
    closed_weekdays=...,
    level_correction_on=...,
    version: int | None = None,
) -> FinVenueSettings:
    ctx.require("budgets:write")
    require_venue(ctx, venue_id)
    row = venue_settings(db, ctx.org_id, venue_id)
    if row is None:
        row = FinVenueSettings(
            organization_id=ctx.org_id,
            venue_id=venue_id,
            closed_weekdays=[],
            level_correction_on=False,
        )
        db.add(row)
    elif version is not None and int(version) != row.version:
        raise Conflict(
            "These settings were changed by someone else since you opened them — reload."
        )
    if holiday_region is not ...:
        row.holiday_region = (
            (str(holiday_region).strip() or None) if holiday_region else None
        )
    if closed_weekdays is not ...:
        days = sorted({int(d) for d in (closed_weekdays or [])})
        if any(d < 1 or d > 7 for d in days):
            raise Invalid("closed_weekdays are ISO weekdays, 1 (Monday) to 7 (Sunday).")
        row.closed_weekdays = days
    if level_correction_on is not ...:
        row.level_correction_on = bool(level_correction_on)
    flush(db)
    return row


# ---------------------------------------------------------------------------
# Budget vs Loaded vs Actual
# ---------------------------------------------------------------------------


def vs_rows(
    first: dt.date,
    last: dt.date,
    *,
    locked: dict[dt.date, int],
    loaded: dict[dt.date, int],
    actual: dict[dt.date, int],
    notes: dict[dt.date, str | None] | None = None,
) -> tuple[list[dict], dict]:
    """One row per day with Orbit's variance convention (Loaded − locked;
    green above, red below) plus the actual, and the totals."""
    rows = []
    totals = {"locked_cents": 0, "loaded_cents": 0, "actual_cents": 0}
    cursor = first
    while cursor <= last:
        lk, ld, ac = locked.get(cursor), loaded.get(cursor), actual.get(cursor)
        row = {
            "date": cursor.isoformat(),
            "weekday": cursor.isoweekday(),
            "locked_cents": lk,
            "loaded_cents": ld,
            "actual_cents": ac,
            "variance_cents": (ld - lk) if lk is not None and ld is not None else None,
            "note": (notes or {}).get(cursor),
        }
        row["variance_pct"] = (
            round((ld - lk) / lk * 100, 1) if lk and ld is not None else None
        )
        rows.append(row)
        for key, value in (
            ("locked_cents", lk),
            ("loaded_cents", ld),
            ("actual_cents", ac),
        ):
            if value is not None:
                totals[key] += value
        cursor += dt.timedelta(days=1)
    lk, ld = totals["locked_cents"], totals["loaded_cents"]
    totals["variance_cents"] = ld - lk
    totals["variance_pct"] = round((ld - lk) / lk * 100, 1) if lk else None
    return rows, totals
