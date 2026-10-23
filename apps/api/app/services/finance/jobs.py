"""``finance.nightly`` — the one Norm Finance job on the tick (plan §8,
DESIGN-CONTRACT §6): per venue, 30 minutes after its trading day closes,
refresh the actuals, log Loaded's budget, build the night's vintage and
score the day that just closed. **Never a Loaded write.**

The slot is the venue-local trading date that just closed (the insights
runner's rule: the day start + 30 minutes), claimed through
``finance_core.jobs.claim`` before any work and committed, so a second API
instance or the next tick finds it taken. Each step is its own try: a dead
Loaded token stops the actuals refresh but the vintage still builds from
the cache, and the error lands on the run row for the weekly parity email.

Runs for every trading venue with a Loaded connection in an organisation
whose ``finance_mode`` is ``shadow`` or ``live`` — silent vintages in shadow
are the point (four weeks of scores before anyone may push a forecast).
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy.orm import Session

from app.db.models import Connection, Organization, Venue
from app.services.business_calendar import day_start_for, timezone_for
from app.services.finance import actuals as actuals_svc
from app.services.finance import forecast as engine
from app.services.finance import pushes, scoring
from app.services.finance_core import jobs as claims

logger = logging.getLogger(__name__)

JOB = "finance.nightly"
#: Minutes after the day start before the night's run.
SETTLE_MINUTES = 30
#: Days ahead the Loaded budget log captures each night.
LOG_AHEAD_DAYS = 77


def _hhmm(value: str) -> tuple[int, int]:
    try:
        h, m = (int(x) for x in str(value).split(":")[:2])
        return h, m
    except ValueError:
        return 7, 0


def closed_trading_day(venue: Venue, now: dt.datetime) -> dt.date | None:
    """The trading date that closed at least SETTLE_MINUTES ago — the slot —
    or None while the venue's day has not rolled over yet."""
    tz = timezone_for(venue)
    local = now.astimezone(tz)
    h, m = _hhmm(day_start_for(venue))
    ready = dt.datetime.combine(local.date(), dt.time(h, m), tzinfo=tz) + dt.timedelta(
        minutes=SETTLE_MINUTES
    )
    if local < ready:
        return None
    return local.date() - dt.timedelta(days=1)


def due_venues(
    db: Session, now: dt.datetime
) -> list[tuple[Organization, Venue, dt.date]]:
    orgs = (
        db.query(Organization)
        .filter(Organization.finance_mode.in_(("shadow", "live")))
        .all()
    )
    out = []
    for org in orgs:
        venues = (
            db.query(Venue)
            .join(Connection, Connection.venue_id == Venue.id)
            .filter(
                Venue.organization_id == org.id,
                Venue.is_trading.is_(True),
                Connection.connector_name == "loadedhub",
                Connection.enabled == "true",
            )
            .distinct()
            .all()
        )
        for venue in venues:
            slot = closed_trading_day(venue, now)
            if slot is not None:
                out.append((org, venue, slot))
    return out


def _loaded_budget_days(
    db: Session, config_db, venue: Venue, first: dt.date, last: dt.date
):
    """``{date: (amount_cents, loaded_id)}`` from ``get_budgets_raw`` with
    Loaded's one-day shift undone (a row dated D+1 is the budget FOR D).
    None when the read fails."""
    from app.connectors.tool_executor import execute_connector_tool

    res = execute_connector_tool(
        connector_name="loadedhub",
        action="get_budgets_raw",
        params={
            "venue": venue.name,
            "from_date": first.isoformat(),
            "to_date": (last + dt.timedelta(days=1)).isoformat(),
        },
        db=db,
        config_db=config_db,
        venue_id=venue.id,
        strict_venue=True,
    )
    if not res.success:
        raise RuntimeError(res.error or "get_budgets_raw failed")
    rows = res.payload
    if isinstance(rows, dict):
        if rows.get("error"):
            raise RuntimeError(str(rows["error"]))
        rows = rows.get("data") or rows.get("items") or []
    out: dict[dt.date, tuple[int, str | None]] = {}
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        try:
            stored = dt.date.fromisoformat(str(r.get("date") or "")[:10])
            amount = float(r.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        day = stored - dt.timedelta(days=1)
        if first <= day <= last:
            out[day] = (int(round(amount * 100)), str(r["id"]) if r.get("id") else None)
    return out


def run_venue(
    db: Session, config_db, org: Organization, venue: Venue, slot: dt.date
) -> dict:
    """The night's four steps for one venue; each failure is noted and the
    next step still runs."""
    out: dict = {"venue": venue.name, "slot": slot.isoformat(), "errors": []}
    try:
        written = actuals_svc.refresh(db, config_db, venue, slot)
        out["actuals"] = written
        if written is None:
            out["errors"].append("actuals: Loaded could not be read")
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"actuals: {exc}"[:300])
    try:
        today = slot + dt.timedelta(days=1)
        days = _loaded_budget_days(
            db, config_db, venue, slot, today + dt.timedelta(days=LOG_AHEAD_DAYS)
        )
        out["loaded_log"] = pushes.append_loaded_log(db, org.id, venue.id, today, days)
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"loaded budget log: {exc}"[:300])
    try:
        vintage, results = engine.build_vintage(
            db, org.id, venue.id, slot + dt.timedelta(days=1)
        )
        out["vintage"] = vintage.id
        out["forecast_days"] = len(results)
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"vintage: {exc}"[:300])
    try:
        actual = actuals_svc.series(db, org.id, venue.id, slot, slot).get(slot)
        if actual is not None:
            out["scored"] = scoring.score_day(db, org.id, venue.id, slot, actual)
        else:
            out["scored"] = None
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(f"scoring: {exc}"[:300])
    return out


def run_due(db: Session, now: dt.datetime | None = None, *, config_db=None) -> dict:
    """The tick's hook: claim and run every due venue-night."""
    from app.db.models import _now

    now = now or _now()
    close_config = False
    if config_db is None:
        from app.db.engine import _ConfigSessionLocal

        config_db = _ConfigSessionLocal()
        close_config = True
    ran = []
    try:
        for org, venue, slot in due_venues(db, now):
            run = claims.claim(
                db,
                org_id=org.id,
                job=JOB,
                scope_id=venue.id,
                slot=slot.isoformat(),
                now=now,
            )
            if run is None:
                continue
            db.commit()  # the claim is visible before the slow part
            try:
                result = run_venue(db, config_db, org, venue, slot)
                claims.finish(
                    db, run, error="; ".join(result["errors"]) or None, now=_now()
                )
                db.commit()
                ran.append(result)
            except Exception as exc:  # noqa: BLE001 — recorded on the run row
                logger.exception("finance.nightly failed for %s", venue.name)
                db.rollback()
                run = db.merge(run)
                claims.finish(db, run, error=str(exc)[:4000], now=_now())
                db.commit()
                ran.append(
                    {
                        "venue": venue.name,
                        "slot": slot.isoformat(),
                        "errors": [str(exc)[:300]],
                    }
                )
    finally:
        if close_config:
            config_db.close()
    return {"job": JOB, "ran": ran}
