"""Public holidays (``fin_holidays``) from Nager.Date, with the regional
mapping Orbit's ``fetch-nz-holidays`` used (05 §7 item 2) — seeded per
organisation and year, read per venue through its ``holiday_region``.

Nager.Date marks a regional holiday with ``counties`` (``NZ-AUK`` …, or
``AU-NSW`` …); a national one has none. A national day is stored under the
country code as its region; a regional day under each region name its
county maps to. A venue sees the national rows plus its own region's
(``fin_venue_settings.holiday_region``). Orbit only ever seeded two venues,
by a button nobody pressed — here the seed is a command the owner runs each
January (``POST /api/finance/holidays/seed``), and the forecast flags every
holiday it knows for a human look.

HTTP is one small function (``fetch_year``) so tests fake it.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Callable

from sqlalchemy.orm import Session

from app.db.finance_models import FinHoliday
from app.db.models import Organization
from app.services.finance_core.ctx import FinanceCtx
from app.services.finance_core.errors import Invalid, flush

logger = logging.getLogger(__name__)

NAGER_URL = "https://date.nager.at/api/v3/PublicHolidays/{year}/{country}"
SOURCE = "nager"

#: Nager.Date county codes -> the region names a venue may be set to.
NZ_REGIONS: dict[str, tuple[str, ...]] = {
    "NZ-AUK": ("Auckland",),
    "NZ-BOP": ("Bay of Plenty",),
    "NZ-CAN": ("Canterbury",),
    "NZ-CIT": ("Chatham Islands",),
    "NZ-GIS": ("Gisborne",),
    "NZ-HKB": ("Hawke's Bay",),
    "NZ-MBH": ("Marlborough",),
    "NZ-MWT": ("Manawatū-Whanganui", "Manawatu-Wanganui", "Manawatu"),
    "NZ-NSN": ("Nelson",),
    "NZ-NTL": ("Northland",),
    "NZ-OTA": ("Otago",),
    "NZ-STL": ("Southland",),
    "NZ-TAS": ("Tasman",),
    "NZ-TKI": ("Taranaki",),
    "NZ-WGN": ("Wellington",),
    "NZ-WKO": ("Waikato",),
    "NZ-WTC": ("West Coast",),
}
AU_REGIONS: dict[str, tuple[str, ...]] = {
    "AU-ACT": ("Australian Capital Territory", "ACT"),
    "AU-NSW": ("New South Wales", "NSW"),
    "AU-NT": ("Northern Territory", "NT"),
    "AU-QLD": ("Queensland", "QLD"),
    "AU-SA": ("South Australia", "SA"),
    "AU-TAS": ("Tasmania", "TAS"),
    "AU-VIC": ("Victoria", "VIC"),
    "AU-WA": ("Western Australia", "WA"),
}
REGIONS = {"NZ": NZ_REGIONS, "AU": AU_REGIONS}
#: Organisation currency -> the country whose calendar applies.
COUNTRY_BY_CURRENCY = {"NZD": "NZ", "AUD": "AU"}


def country_for(db: Session, org_id: str) -> str:
    currency = (
        db.query(Organization.currency).filter(Organization.id == org_id).scalar()
    )
    return COUNTRY_BY_CURRENCY.get(str(currency or "NZD").upper(), "NZ")


def region_names(country: str) -> list[str]:
    """Every region name a venue of this country may be set to."""
    return sorted({n for names in REGIONS.get(country, {}).values() for n in names})


def fetch_year(year: int, country: str) -> list[dict]:
    """Nager.Date's list for a year: ``[{date, localName, name, counties}]``."""
    import httpx

    response = httpx.get(NAGER_URL.format(year=year, country=country), timeout=20)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise Invalid("Nager.Date returned something that is not a list of holidays.")
    return data


def rows_for(holidays: list[dict], country: str) -> list[tuple[str, dt.date, str]]:
    """``(region, date, name)`` rows from Nager's list: a national day under
    the country code, a regional day under each mapped region name."""
    mapping = REGIONS.get(country, {})
    out: list[tuple[str, dt.date, str]] = []
    for h in holidays:
        if not isinstance(h, dict):
            continue
        try:
            date = dt.date.fromisoformat(str(h.get("date"))[:10])
        except (TypeError, ValueError):
            continue
        name = str(h.get("localName") or h.get("name") or "").strip()
        if not name:
            continue
        counties = h.get("counties") or []
        if not counties:
            out.append((country, date, name))
            continue
        for county in counties:
            for region in mapping.get(str(county), ()):
                out.append((region, date, name))
    return out


def seed_year(
    db: Session,
    ctx: FinanceCtx,
    year: int,
    *,
    country: str | None = None,
    fetch: Callable[[int, str], list[dict]] = fetch_year,
) -> dict:
    """Store the year's holidays for the organisation. Idempotent: a row
    already there is left alone (the unique key is region + date + name)."""
    ctx.require("budgets:write")
    if not (2000 <= int(year) <= 2100):
        raise Invalid("year must be a four-digit year.")
    country = (country or country_for(db, ctx.org_id)).upper()
    if country not in REGIONS:
        raise Invalid(f"country is one of {', '.join(REGIONS)}.")
    wanted = rows_for(fetch(int(year), country), country)
    existing = {
        (r.region, r.date, r.name)
        for r in db.query(FinHoliday).filter(
            FinHoliday.organization_id == ctx.org_id,
            FinHoliday.date >= dt.date(int(year), 1, 1),
            FinHoliday.date <= dt.date(int(year), 12, 31),
        )
    }
    inserted = 0
    for region, date, name in wanted:
        if (region, date, name) in existing:
            continue
        db.add(
            FinHoliday(
                organization_id=ctx.org_id,
                region=region,
                date=date,
                name=name,
                source=SOURCE,
            )
        )
        existing.add((region, date, name))
        inserted += 1
    flush(db)
    return {
        "year": int(year),
        "country": country,
        "inserted": inserted,
        "unchanged": len(wanted) - inserted,
        "regions": sorted({r for r, _, _ in wanted}),
    }


def list_holidays(
    db: Session, ctx: FinanceCtx, *, year: int | None = None, region: str | None = None
) -> list[FinHoliday]:
    ctx.require("finance:read")
    q = db.query(FinHoliday).filter(FinHoliday.organization_id == ctx.org_id)
    if year is not None:
        q = q.filter(
            FinHoliday.date >= dt.date(int(year), 1, 1),
            FinHoliday.date <= dt.date(int(year), 12, 31),
        )
    if region:
        q = q.filter(FinHoliday.region == region)
    return q.order_by(FinHoliday.date, FinHoliday.region).all()


def for_venue(
    db: Session,
    org_id: str,
    venue_id: str,
    first: dt.date,
    last: dt.date,
    *,
    region: str | None = None,
) -> dict[dt.date, str]:
    """``{date: name}`` — the national rows plus the venue's region's. The
    region comes from the venue's settings unless given."""
    from app.services.finance import budgets

    if region is None:
        settings = budgets.venue_settings(db, org_id, venue_id)
        region = settings.holiday_region if settings else None
    regions = [country_for(db, org_id)]
    if region:
        regions.append(region)
    rows = (
        db.query(FinHoliday)
        .filter(
            FinHoliday.organization_id == org_id,
            FinHoliday.region.in_(regions),
            FinHoliday.date >= first,
            FinHoliday.date <= last,
        )
        .order_by(FinHoliday.date)
        .all()
    )
    out: dict[dt.date, str] = {}
    for r in rows:
        out[r.date] = r.name if r.date not in out else f"{out[r.date]} / {r.name}"
    return out
