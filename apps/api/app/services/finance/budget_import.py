"""Importing a year's daily budget: a generic paste/CSV, and Orbit's FY27
spreadsheet layout as one optional parser (plan §2 item 9, 04 §12 F).

The generic layout is what anyone can produce from any spreadsheet: one line
per day, ``date, amount[, note]``, a header row allowed. Orbit's importer
only read one hard-coded workbook — a tab per venue, twelve month blocks
across, five columns each (``Date | Last Year | Budget | Events | spacer``),
data in rows 3–33 — so that layout is kept as a second parser for the one
venue tab pasted as CSV (the owner's budget workings keep that shape).

Both parsers return the same rows (``{date, amount_cents, note}``) plus the
lines they could not read, so the preview shows exactly what would land and
what was skipped. ``apply`` writes a new ``import`` version; it never
overwrites a locked one (Orbit's import silently overwrote the year).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.services.finance import budgets
from app.services.finance_core.ctx import FinanceCtx
from app.services.finance_core.errors import Invalid

#: Orbit's block layout: 12 blocks of 5 columns, data in spreadsheet rows 3–33.
ORBIT_BLOCK_WIDTH = 5
ORBIT_BLOCKS = 12
ORBIT_FIRST_ROW, ORBIT_LAST_ROW = 2, 32  # zero-indexed

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d/%m/%y",
    "%d-%m-%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%a %d %b %Y",
    "%A %d %B %Y",
    "%Y/%m/%d",
)
_EXCEL_EPOCH = dt.date(1899, 12, 30)


def parse_date(value) -> dt.date | None:
    """A cell as a date: ISO, day-first, '1 Apr 2026', an Excel serial, or
    an ISO datetime. None when it is none of those."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip().strip('"')
    if not text:
        return None
    if re.fullmatch(r"\d{4,6}(\.0+)?", text):
        serial = int(float(text))
        if 20000 <= serial <= 80000:  # 1954 – 2119: a spreadsheet date serial
            return _EXCEL_EPOCH + dt.timedelta(days=serial)
    head = text[:10]
    for fmt in _DATE_FORMATS:
        for candidate in (text, head, text.replace(",", "")):
            try:
                return dt.datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    try:
        return dt.datetime.fromisoformat(text).date()
    except ValueError:
        return None


def parse_amount(value) -> int | None:
    """A cell as GST-inclusive cents: '$1,978', '1978.5', '(200)' (negative),
    '-' / '' (none). None when it is not a number."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        # Through Decimal: 12.345 * 100 is 1234.4999… as a float.
        return int(
            (Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
    text = str(value).strip().strip('"')
    if text in ("", "-", "—"):
        return None
    negative = text.startswith("(") and text.endswith(")")
    cleaned = re.sub(r"[^\d.\-]", "", text)
    if cleaned in ("", "-", "."):
        return None
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    cents = int((amount * 100).quantize(Decimal("1")))
    return -cents if negative else cents


def _note(value) -> str | None:
    text = "" if value is None else str(value).strip().strip('"')
    return text or None


def _rows_of(text: str) -> list[list[str]]:
    """CSV, tab- or semicolon-separated — whatever the paste looks like."""
    sample = text[:2000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    return [row for row in csv.reader(io.StringIO(text), dialect)]


def parse_generic(text: str) -> tuple[list[dict], list[str]]:
    """``date, amount[, note]`` per line. Returns (rows, problems)."""
    rows, problems = [], []
    for n, cells in enumerate(_rows_of(text or ""), start=1):
        cells = [c.strip() for c in cells]
        if not any(cells):
            continue
        date = parse_date(cells[0])
        if date is None:
            if n == 1 and len(cells) >= 2:
                continue  # a header row
            problems.append(f"line {n}: not a date: {cells[0]!r}")
            continue
        if len(cells) < 2:
            problems.append(f"line {n}: no amount for {date.isoformat()}")
            continue
        cents = parse_amount(cells[1])
        if cents is None:
            problems.append(f"line {n}: not an amount: {cells[1]!r}")
            continue
        if cents < 0:
            problems.append(f"line {n}: negative amount for {date.isoformat()}")
            continue
        rows.append(
            {
                "date": date,
                "amount_cents": cents,
                "note": _note(cells[2]) if len(cells) > 2 else None,
            }
        )
    return rows, problems


def parse_orbit_blocks(grid: list[list]) -> tuple[list[dict], list[str]]:
    """Orbit's one-venue tab: 12 blocks of ``Date | Last Year | Budget |
    Events | spacer`` across, rows 3–33. ``grid`` is the tab as rows of
    cells (CSV text goes through ``parse_orbit_text``). The Last Year column
    is ignored, as Orbit's importer ignored it."""
    rows, problems = [], []
    for r in range(ORBIT_FIRST_ROW, min(ORBIT_LAST_ROW, len(grid) - 1) + 1):
        row = grid[r]
        for block in range(ORBIT_BLOCKS):
            col = block * ORBIT_BLOCK_WIDTH
            if col >= len(row):
                break
            date_cell = row[col]
            if date_cell in (None, ""):
                continue
            date = parse_date(date_cell)
            if date is None:
                problems.append(
                    f"row {r + 1}, block {block + 1}: not a date: {date_cell!r}"
                )
                continue
            amount_cell = row[col + 2] if col + 2 < len(row) else None
            cents = parse_amount(amount_cell)
            if cents is None:
                cents = 0  # Orbit: an empty Budget cell is a $0 day
            if cents < 0:
                problems.append(f"row {r + 1}, block {block + 1}: negative amount")
                continue
            note = _note(row[col + 3]) if col + 3 < len(row) else None
            rows.append({"date": date, "amount_cents": cents, "note": note})
    return rows, problems


def parse_orbit_text(text: str) -> tuple[list[dict], list[str]]:
    return parse_orbit_blocks(_rows_of(text or ""))


LAYOUTS = {"generic": parse_generic, "orbit": parse_orbit_text}


def parse(text: str, layout: str = "generic") -> tuple[list[dict], list[str]]:
    parser = LAYOUTS.get(str(layout or "generic"))
    if parser is None:
        raise Invalid(f"layout is one of {', '.join(LAYOUTS)}.")
    return parser(text)


# ---------------------------------------------------------------------------
# Preview and apply
# ---------------------------------------------------------------------------


def preview(
    db: Session,
    ctx: FinanceCtx,
    venue_id: str,
    text: str,
    *,
    layout: str = "generic",
    fiscal_year: int | None = None,
) -> dict:
    """What an import would write: the rows, their months, the days outside
    the fiscal year, duplicates and zero days, and the lines not read."""
    ctx.require("budgets:write")
    budgets.require_venue(ctx, venue_id)
    rows, problems = parse(text, layout)
    sm = budgets.start_month(db, ctx.org_id)
    if fiscal_year is None and rows:
        # The year most rows fall in.
        counts: dict[int, int] = {}
        for r in rows:
            fy = budgets.fiscal_year_of(r["date"], sm)
            counts[fy] = counts.get(fy, 0) + 1
        fiscal_year = max(counts, key=counts.get)
    seen: dict[dt.date, int] = {}
    duplicates = []
    for r in rows:
        seen[r["date"]] = seen.get(r["date"], 0) + 1
        if seen[r["date"]] == 2:
            duplicates.append(r["date"].isoformat())
    outside = []
    if fiscal_year is not None:
        lo, hi = budgets.fiscal_bounds(int(fiscal_year), sm)
        outside = sorted(
            {r["date"].isoformat() for r in rows if not lo <= r["date"] <= hi}
        )
    months: dict[tuple[int, int], dict] = {}
    for r in rows:
        key = (r["date"].year, r["date"].month)
        m = months.setdefault(
            key, {"year": key[0], "month": key[1], "days": 0, "total_cents": 0}
        )
        m["days"] += 1
        m["total_cents"] += r["amount_cents"]
    dates = sorted(r["date"] for r in rows)
    return {
        "layout": layout,
        "fiscal_year": fiscal_year,
        "days": len(rows),
        "total_cents": sum(r["amount_cents"] for r in rows),
        "first": dates[0].isoformat() if dates else None,
        "last": dates[-1].isoformat() if dates else None,
        "notes": sum(1 for r in rows if r["note"]),
        "outside_fiscal_year": outside,
        "duplicates": duplicates,
        "zero_days": [r["date"].isoformat() for r in rows if r["amount_cents"] == 0],
        "months": [months[k] for k in sorted(months)],
        "problems": problems,
        "rows": [
            {
                "date": r["date"].isoformat(),
                "amount_cents": r["amount_cents"],
                "note": r["note"],
            }
            for r in rows
        ],
    }


def apply(
    db: Session,
    ctx: FinanceCtx,
    venue_id: str,
    text: str,
    *,
    layout: str = "generic",
    fiscal_year: int | None = None,
    label: str | None = None,
    lock: bool = False,
):
    """Write the parsed days as a new ``import`` version (locked at once when
    asked). Refuses duplicates and days outside the fiscal year — the
    preview showed them, so an apply with them is a mistake, not a choice."""
    ctx.require("budgets:write")
    shown = preview(db, ctx, venue_id, text, layout=layout, fiscal_year=fiscal_year)
    if not shown["rows"]:
        raise Invalid(
            "Nothing to import"
            + (f": {shown['problems'][0]}" if shown["problems"] else ".")
        )
    if shown["duplicates"]:
        raise Invalid(f"Dates listed twice: {', '.join(shown['duplicates'][:5])}.")
    if shown["outside_fiscal_year"]:
        raise Invalid(
            f"{len(shown['outside_fiscal_year'])} day(s) fall outside FY{shown['fiscal_year']} "
            f"(first: {shown['outside_fiscal_year'][0]}) — import that year separately."
        )
    version = budgets.create_version(
        db,
        ctx,
        venue_id,
        int(shown["fiscal_year"]),
        source="import",
        label=label or f"Import {dt.date.today().isoformat()}",
        notes=f"{shown['days']} days, {shown['notes']} notes ({layout} layout)",
    )
    budgets.set_days(db, ctx, version.id, shown["rows"])
    if lock:
        budgets.lock_version(db, ctx, version.id)
    return version, shown
