"""Public holidays (``fin_holidays``) from Loaded's pay adjustments — owner,
23 Oct 2026: "public holidays should come from Loaded".

Loaded prices a public holiday with a pay adjustment of pay adjustment type
Public Holiday ("Labour Day", time and a half) in a pay adjustment package the
venue's company has activated: the managed "New Zealand Public Holidays", a
regional "Canterbury Anniversary Day", the company's own. Each adjustment's
dates are concrete, one per year. ``loadedhub.get_pay_adjustments`` reads them
(the ONE shaping, the same answer chat gets); this module keeps them per
venue for the forecast, which flags every holiday for a human look and takes
last year's same holiday where the venue traded.

Why per venue and no region setting: Loaded's packages are activated per
company, so the company's activations ARE its region — La Zeppa has the
national package only, Bessie & Royals added Canterbury's anniversary. Norm
takes Loaded's word rather than a second region list (the Nager.Date seed and
``holiday_region`` this replaced). Every dated holiday is kept, including one
Loaded would not price (before its package was activated, or with the
adjustment switched off): it is still a day that moves sales.

A sync takes every holiday Loaded returns in its window. Rows from today on
that Loaded no longer returns are removed (an activation taken away); past
rows are kept, so history the backtest leans on never vanishes with a
package. The nightly ``finance.nightly`` refreshes the next twelve months; the
Forecasting page's "Sync from Loaded" also fills the history.

Loaded's managed package says its dates run "until 31 December 2026": until
Loaded publishes the next year's, the forecast has no holidays past that, and
the sync says so (``dates_until``).
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy.orm import Session

from app.db.finance_models import FinHoliday
from app.db.models import Venue
from app.services.finance_core.ctx import FinanceCtx, require_venue
from app.services.finance_core.errors import Invalid, NotFound, flush

logger = logging.getLogger(__name__)

SOURCE = "loaded"
#: Days ahead each sync covers (the nightly window).
AHEAD_DAYS = 365
#: Years back the page's sync fills — the forecast's history reaches a year
#: and ten weeks back; the backtest further.
HISTORY_YEARS = 3


def read_loaded(
    db: Session,
    config_db,
    venue: Venue,
    first: dt.date,
    last: dt.date,
    *,
    user=None,
) -> dict:
    """``loadedhub.get_pay_adjustments`` for the venue on its own token. Raises
    ``Invalid`` when Loaded can't be read — a failed read must never look like
    a year with no holidays (it would delete the future rows)."""
    from app.connectors.tool_executor import execute_connector_tool

    res = execute_connector_tool(
        connector_name="loadedhub",
        action="get_pay_adjustments",
        params={
            "venue": venue.name,
            "start": first.isoformat(),
            "end": last.isoformat(),
        },
        db=db,
        config_db=config_db,
        venue_id=venue.id,
        strict_venue=True,
        user=user,
    )
    if not res.success:
        raise Invalid(f"Loaded's pay adjustments could not be read: {res.error}")
    data = res.payload
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        data = data["data"]
    if not isinstance(data, dict) or data.get("error"):
        detail = data.get("error") if isinstance(data, dict) else "no answer"
        raise Invalid(f"Loaded's pay adjustments could not be read: {detail}")
    if not isinstance(data.get("public_holidays"), list):
        raise Invalid("Loaded's pay adjustments came back without public holidays.")
    return data


def sync_venue(
    db: Session,
    org_id: str,
    venue: Venue,
    *,
    first: dt.date,
    last: dt.date,
    today: dt.date,
    config_db=None,
    user=None,
) -> dict:
    """Store the venue's public holidays from Loaded for ``first``..``last``.
    Adds what is new, updates a moved package name, removes rows from
    ``today`` on that Loaded no longer holds; never touches the past."""
    data = read_loaded(db, config_db, venue, first, last, user=user)
    wanted: dict[tuple[dt.date, str], str | None] = {}
    for h in data["public_holidays"]:
        if not isinstance(h, dict):
            continue
        try:
            date = dt.date.fromisoformat(str(h.get("date"))[:10])
        except (TypeError, ValueError):
            continue
        name = str(h.get("name") or "").strip()
        if not name or not (first <= date <= last):
            continue
        wanted.setdefault((date, name), (str(h.get("package") or "").strip() or None))
    rows = (
        db.query(FinHoliday)
        .filter(
            FinHoliday.organization_id == org_id,
            FinHoliday.venue_id == venue.id,
            FinHoliday.date >= first,
            FinHoliday.date <= last,
        )
        .all()
    )
    have = {(r.date, r.name): r for r in rows}
    inserted = updated = removed = 0
    for key, package in wanted.items():
        row = have.get(key)
        if row is None:
            db.add(
                FinHoliday(
                    organization_id=org_id,
                    venue_id=venue.id,
                    date=key[0],
                    name=key[1],
                    package=package,
                    source=SOURCE,
                )
            )
            inserted += 1
        elif row.package != package or row.source != SOURCE:
            row.package, row.source = package, SOURCE
            updated += 1
    for key, row in have.items():
        if key not in wanted and row.date >= today:
            db.delete(row)
            removed += 1
    flush(db)
    return {
        "venue_id": venue.id,
        "venue": venue.name,
        "first": first.isoformat(),
        "last": last.isoformat(),
        "inserted": inserted,
        "updated": updated,
        "removed": removed,
        "unchanged": len(wanted) - inserted - updated,
        "dates_until": data.get("dates_until"),
        "packages": sorted(
            {p.get("name") for p in data.get("packages") or [] if p.get("name")}
        ),
    }


def sync_from_page(
    db: Session, ctx: FinanceCtx, venue_id: str, *, today: dt.date, config_db=None
) -> dict:
    """The Forecasting page's "Sync from Loaded" (``budgets:write``): the
    history and the next twelve months, on the person's own call."""
    ctx.require("budgets:write")
    require_venue(ctx, venue_id)
    venue = (
        db.query(Venue)
        .filter(Venue.organization_id == ctx.org_id, Venue.id == venue_id)
        .first()
    )
    if venue is None:
        raise NotFound("Venue not found.")
    first = today.replace(year=today.year - HISTORY_YEARS, month=1, day=1)
    return sync_venue(
        db,
        ctx.org_id,
        venue,
        first=first,
        last=today + dt.timedelta(days=AHEAD_DAYS),
        today=today,
        config_db=config_db,
        user=ctx.user,
    )


def list_holidays(
    db: Session, ctx: FinanceCtx, venue_id: str, *, year: int | None = None
) -> list[FinHoliday]:
    ctx.require("finance:read")
    require_venue(ctx, venue_id)
    q = db.query(FinHoliday).filter(
        FinHoliday.organization_id == ctx.org_id, FinHoliday.venue_id == venue_id
    )
    if year is not None:
        q = q.filter(
            FinHoliday.date >= dt.date(int(year), 1, 1),
            FinHoliday.date <= dt.date(int(year), 12, 31),
        )
    return q.order_by(FinHoliday.date, FinHoliday.name).all()


def holiday_dict(row: FinHoliday) -> dict:
    return {
        "date": row.date.isoformat(),
        "name": row.name,
        "package": row.package,
        "source": row.source,
    }


def for_venue(
    db: Session,
    org_id: str,
    venue_id: str,
    first: dt.date,
    last: dt.date,
) -> dict[dt.date, str]:
    """``{date: name}`` — the venue's public holidays (two on one day joined)."""
    rows = (
        db.query(FinHoliday)
        .filter(
            FinHoliday.organization_id == org_id,
            FinHoliday.venue_id == venue_id,
            FinHoliday.date >= first,
            FinHoliday.date <= last,
        )
        .order_by(FinHoliday.date, FinHoliday.name)
        .all()
    )
    out: dict[dt.date, str] = {}
    for r in rows:
        out[r.date] = r.name if r.date not in out else f"{out[r.date]} / {r.name}"
    return out
