"""The daily actuals cache (``fin_actuals_daily``) — a venue's sales per
trading day as ``loadedhub.get_sales`` reports them, kept so the forecast
has samples and the scorer has answers without a Loaded call per day.

A cache, never the record: ``get_sales`` is. The nightly job refreshes the
last 14 days (a late cash-up or a corrected invoice moves a day's figure);
``load_history`` fills 24 months once for the backtest. Everything comes
through ``execute_connector_tool`` on the venue's own token with explicit
trading-day windows (the insights runner's way), so no model decides what
"yesterday" means and no venue's figure is read with another venue's login.

Three reads per window: sales with GST, sales without (Loaded carries the
tax inside the figure, and ``get_sales`` takes it off per bucket), and the
order count — the same three figures the Summary shows.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy.orm import Session

from app.db.finance_models import FinActualsDaily
from app.db.models import Venue, _now
from app.services.business_calendar import day_start_for, timezone_for
from app.services.finance_core.errors import Invalid

logger = logging.getLogger(__name__)

#: Days the nightly refresh re-reads.
REFRESH_DAYS = 14
#: Days per ``get_sales`` call when loading history (its call budget is 40;
#: a daily read costs a call per month chunk and a venue list).
HISTORY_CHUNK_DAYS = 92


def window_for(venue: Venue, first: dt.date, last: dt.date) -> dict:
    """The trading window covering ``first``..``last`` inclusive, at the
    venue's day start, with the offset each end has on its own date."""
    tz = timezone_for(venue)
    h, m = (int(x) for x in day_start_for(venue).split(":")[:2])
    start = dt.datetime(first.year, first.month, first.day, h, m, tzinfo=tz)
    after = last + dt.timedelta(days=1)
    end = dt.datetime(after.year, after.month, after.day, h, m, tzinfo=tz)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "confirmed_by_user": True,
    }


def _read(db: Session, config_db, venue: Venue, params: dict, user=None):
    """One ``get_sales`` call on the venue's token; ``None`` (and a log line)
    when it fails — a failed read must never look like a $0 day."""
    from app.connectors.tool_executor import execute_connector_tool

    try:
        res = execute_connector_tool(
            connector_name="loadedhub",
            action="get_sales",
            params={"venues": [venue.name], "breakdown": "daily", **params},
            db=db,
            config_db=config_db,
            venue_id=venue.id,
            strict_venue=True,
            user=user,
        )
    except Exception as exc:  # noqa: BLE001 — recorded, the caller decides
        logger.warning("actuals: get_sales failed for %s: %s", venue.name, exc)
        return None
    if not res.success:
        logger.warning("actuals: get_sales failed for %s: %s", venue.name, res.error)
        return None
    data = res.payload
    if isinstance(data, dict) and data.get("error"):
        logger.warning("actuals: get_sales error for %s: %s", venue.name, data["error"])
        return None
    return data


def _rows(payload) -> dict[dt.date, dict]:
    out: dict[dt.date, dict] = {}
    rows = payload.get("rows") if isinstance(payload, dict) else None
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        try:
            date = dt.date.fromisoformat(str(r.get("date"))[:10])
        except (TypeError, ValueError):
            continue
        out[date] = r
    return out


def read_days(
    db: Session, config_db, venue: Venue, first: dt.date, last: dt.date, user=None
) -> list[dict] | None:
    """``[{date, sales_incl_cents, sales_ex_cents, orders}]`` for the range,
    or None when the sales read failed (nothing is cached from a failure)."""
    window = window_for(venue, first, last)
    incl = _read(
        db, config_db, venue, {**window, "measure": "sales", "tax": "include"}, user
    )
    if incl is None:
        return None
    excl = _read(
        db, config_db, venue, {**window, "measure": "sales", "tax": "exclude"}, user
    )
    orders = _read(db, config_db, venue, {**window, "measure": "orders"}, user)
    incl_rows, ex_rows = _rows(incl), _rows(excl) if excl else {}
    order_rows = _rows(orders) if orders else {}
    out = []
    for date, row in sorted(incl_rows.items()):
        actual = row.get("actual")
        if not isinstance(actual, (int, float)) or isinstance(actual, bool):
            continue
        ex = (ex_rows.get(date) or {}).get("actual")
        ex_cents = (
            int(round(float(ex) * 100))
            if isinstance(ex, (int, float)) and not isinstance(ex, bool)
            else int(round(float(actual) * 100))
        )
        count = (order_rows.get(date) or {}).get("orders")
        out.append(
            {
                "date": date,
                "sales_incl_cents": int(round(float(actual) * 100)),
                "sales_ex_cents": ex_cents,
                "orders": int(count) if isinstance(count, (int, float)) else None,
            }
        )
    return out


def upsert_days(db: Session, org_id: str, venue_id: str, days: list[dict]) -> int:
    if not days:
        return 0
    dates = [d["date"] for d in days]
    existing = {
        r.date: r
        for r in db.query(FinActualsDaily).filter(
            FinActualsDaily.organization_id == org_id,
            FinActualsDaily.venue_id == venue_id,
            FinActualsDaily.date.in_(dates),
        )
    }
    now = _now()
    for d in days:
        row = existing.get(d["date"])
        if row is None:
            row = FinActualsDaily(
                organization_id=org_id, venue_id=venue_id, date=d["date"]
            )
            db.add(row)
        row.sales_incl_cents = int(d["sales_incl_cents"])
        row.sales_ex_cents = int(d["sales_ex_cents"])
        row.orders = d.get("orders")
        row.fetched_at = now
    db.flush()
    return len(days)


def refresh(
    db: Session,
    config_db,
    venue: Venue,
    through: dt.date,
    *,
    days: int = REFRESH_DAYS,
    user=None,
) -> int | None:
    """Re-read the ``days`` trading days ending ``through`` (inclusive).
    Returns the rows written, or None when Loaded could not be read."""
    first = through - dt.timedelta(days=days - 1)
    rows = read_days(db, config_db, venue, first, through, user=user)
    if rows is None:
        return None
    return upsert_days(db, venue.organization_id, venue.id, rows)


def load_history(
    db: Session,
    config_db,
    venue: Venue,
    *,
    months: int = 24,
    through: dt.date | None = None,
    user=None,
) -> dict:
    """Fill the cache back ``months`` months, in chunks. A chunk that fails
    is listed and the rest still load."""
    if months < 1 or months > 60:
        raise Invalid("months must be 1–60.")
    through = through or (
        dt.datetime.now(timezone_for(venue)).date() - dt.timedelta(days=1)
    )
    first = through - dt.timedelta(days=int(months * 30.44))
    written, failed = 0, []
    cursor = first
    while cursor <= through:
        last = min(cursor + dt.timedelta(days=HISTORY_CHUNK_DAYS - 1), through)
        rows = read_days(db, config_db, venue, cursor, last, user=user)
        if rows is None:
            failed.append(f"{cursor.isoformat()}..{last.isoformat()}")
        else:
            written += upsert_days(db, venue.organization_id, venue.id, rows)
            db.commit()
        cursor = last + dt.timedelta(days=1)
    return {
        "venue": venue.name,
        "from": first.isoformat(),
        "to": through.isoformat(),
        "written": written,
        "failed_chunks": failed,
    }


def series(
    db: Session, org_id: str, venue_id: str, first: dt.date, last: dt.date
) -> dict[dt.date, int]:
    """``{date: sales_incl_cents}`` over a range — the forecast's and scorer's
    input. GST-inclusive, as Loaded's budget is."""
    rows = db.query(FinActualsDaily.date, FinActualsDaily.sales_incl_cents).filter(
        FinActualsDaily.organization_id == org_id,
        FinActualsDaily.venue_id == venue_id,
        FinActualsDaily.date >= first,
        FinActualsDaily.date <= last,
    )
    return {d: int(c) for d, c in rows}


def coverage(db: Session, org_id: str, venue_id: str) -> dict:
    """How much history the cache holds for a venue."""
    from sqlalchemy import func

    first, last, n = (
        db.query(
            func.min(FinActualsDaily.date),
            func.max(FinActualsDaily.date),
            func.count(FinActualsDaily.id),
        )
        .filter(
            FinActualsDaily.organization_id == org_id,
            FinActualsDaily.venue_id == venue_id,
        )
        .one()
    )
    return {
        "first": first.isoformat() if first else None,
        "last": last.isoformat() if last else None,
        "days": int(n or 0),
    }
