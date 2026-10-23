"""Forecasting v1 — a defensible daily budget with reasons (plan §8, 05 §8).

Per venue, per trading day T over the next ten weeks:

* **baseline** = the median of the last 8 same-weekday trading days (at
  least 4), walking back up to 16 weeks for them. A sample is left out when
  the venue was closed (a closed weekday, a $0 day), when the day was a
  public holiday, or when a driver flagged it; a sample that carried
  Functions events is **deflated** by those events' expected spend
  (``functions/money.py``), so a wedding does not become next Saturday's
  normal trade. Median, not mean: on Orbit's own data that one change took
  the daily error from 55% to 46% (05 §6.3).
* **uplifts**, additive and each with a label the day shows: the confirmed
  Functions events on T (no 50% cliff); a public holiday → last year's same
  holiday if the venue traded, else the baseline, always flagged; manual
  drivers in dollars or as a percentage of the baseline.
* an **override** replaces the number and is kept with who and when.
* **last year** is a reference column, never blended in — the blend tested
  worse, and the mirror's last year is partly empty.
* a damped **level correction** (the last 4 weeks over the previous 4,
  halved, clipped ±15%) is a per-venue switch, off by default, scored on its
  own: it did not help overall (05 §6.3 reading iii).

Every nightly run is a **vintage** (``fin_forecast_vintages`` +
``fin_forecast_days``) whose ``components`` hold the samples, each exclusion
reason, the uplifts and the override — the day drawer explains a number
without recomputing anything, and the scorer measures what was actually
shown. Pure ``statistics``; no new dependency. The engine is the pure part
(``forecast_day`` / ``run``); ``build_vintage`` loads its inputs and stores
the result.

A closed weekday is one the venue's settings name, or one with $0 on at
least 75% of its last 8 occurrences (Mr Murdochs' spread of closed days);
such a day forecasts $0 unless overridden.
"""

from __future__ import annotations

import datetime as dt
import logging
import statistics
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from sqlalchemy.orm import Session

from app.db.finance_models import FinForecastDay, FinForecastVintage
from app.services.finance_core.errors import Invalid, NotFound

logger = logging.getLogger(__name__)

METHOD = "median8"
HORIZON_DAYS = 70
SAMPLES = 8
MIN_SAMPLES = 4
LOOKBACK_WEEKS = 16
#: A weekday with $0 on this share of its last CLOSED_WINDOW_WEEKS occurrences is closed.
CLOSED_SHARE = 0.75
CLOSED_WINDOW_WEEKS = 8
#: The level correction's clip (±15%) and its damping (halved).
LEVEL_CLIP = 0.15
LEVEL_DAMPING = 0.5
#: Where last year's same holiday may fall relative to T.
_LY_WINDOW = (330, 400)


@dataclass(frozen=True)
class Driver:
    kind: str
    label: str
    uplift_cents: int | None = None
    uplift_bp: int | None = None
    id: str | None = None


@dataclass(frozen=True)
class Override:
    amount_cents: int
    reason: str | None = None
    created_by: str | None = None
    created_at: str | None = None
    id: str | None = None


@dataclass
class Inputs:
    """Everything the engine reads, as plain mappings by trading date."""

    actuals: Mapping[dt.date, int]
    event_spend: Mapping[dt.date, int] = field(default_factory=dict)
    holidays: Mapping[dt.date, str] = field(default_factory=dict)
    drivers: Mapping[dt.date, Sequence[Driver]] = field(default_factory=dict)
    overrides: Mapping[dt.date, Override] = field(default_factory=dict)
    closed_weekdays: frozenset[int] = frozenset()
    level_correction: bool = False


@dataclass
class DayResult:
    date: dt.date
    baseline_cents: int
    uplift_cents: int
    forecast_cents: int
    final_cents: int
    components: dict


# ---------------------------------------------------------------------------
# The pure engine
# ---------------------------------------------------------------------------


def detect_closed_weekdays(actuals: Mapping[dt.date, int], as_of: dt.date) -> set[int]:
    """ISO weekdays with $0 on at least ``CLOSED_SHARE`` of their last
    ``CLOSED_WINDOW_WEEKS`` occurrences with data (at least 4 of them)."""
    closed = set()
    for weekday in range(1, 8):
        seen, zero = 0, 0
        cursor = as_of - dt.timedelta(days=1)
        while cursor.isoweekday() != weekday:
            cursor -= dt.timedelta(days=1)
        for _ in range(CLOSED_WINDOW_WEEKS):
            value = actuals.get(cursor)
            if value is not None:
                seen += 1
                if value == 0:
                    zero += 1
            cursor -= dt.timedelta(days=7)
        if seen >= MIN_SAMPLES and zero / seen >= CLOSED_SHARE:
            closed.add(weekday)
    return closed


def level_factor(
    actuals: Mapping[dt.date, int], as_of: dt.date, closed: set[int]
) -> dict | None:
    """The damped, clipped ratio of the last 4 weeks to the previous 4, or
    None when either period has no trade on record."""

    def total(first: dt.date, last: dt.date) -> int:
        out = 0
        cursor = first
        while cursor <= last:
            if cursor.isoweekday() not in closed:
                out += actuals.get(cursor) or 0
            cursor += dt.timedelta(days=1)
        return out

    recent = total(as_of - dt.timedelta(days=28), as_of - dt.timedelta(days=1))
    previous = total(as_of - dt.timedelta(days=56), as_of - dt.timedelta(days=29))
    if recent <= 0 or previous <= 0:
        return None
    ratio = recent / previous
    factor = 1 + (ratio - 1) * LEVEL_DAMPING
    factor = max(1 - LEVEL_CLIP, min(1 + LEVEL_CLIP, factor))
    return {"ratio": round(ratio, 4), "factor": round(factor, 4)}


def _same_weekday_before(target: dt.date, as_of: dt.date) -> dt.date:
    """The latest date strictly before ``as_of`` on ``target``'s weekday."""
    cursor = as_of - dt.timedelta(days=1)
    while cursor.isoweekday() != target.isoweekday():
        cursor -= dt.timedelta(days=1)
    return cursor


def _samples(
    target: dt.date, inputs: Inputs, as_of: dt.date, closed: set[int]
) -> list[dict]:
    """Up to LOOKBACK_WEEKS same-weekday candidates, each with its reason,
    stopping once SAMPLES usable ones are in hand."""
    out, usable = [], 0
    cursor = _same_weekday_before(target, as_of)
    for _ in range(LOOKBACK_WEEKS):
        actual = inputs.actuals.get(cursor)
        entry = {
            "date": cursor.isoformat(),
            "actual_cents": actual,
            "event_spend_cents": 0,
            "used_cents": None,
            "reason": "ok",
        }
        if actual is None:
            entry["reason"] = "no_data"
        elif cursor in inputs.holidays:
            entry["reason"] = "holiday"
            entry["label"] = inputs.holidays[cursor]
        elif inputs.drivers.get(cursor):
            entry["reason"] = "driver"
            entry["label"] = "; ".join(d.label for d in inputs.drivers[cursor])
        elif actual == 0 or cursor.isoweekday() in closed:
            entry["reason"] = "closed"
        else:
            spend = int(inputs.event_spend.get(cursor, 0) or 0)
            entry["event_spend_cents"] = spend
            entry["used_cents"] = max(actual - spend, 0)
            if spend > 0:
                entry["reason"] = "deflated"
            usable += 1
        out.append(entry)
        if usable >= SAMPLES:
            break
        cursor -= dt.timedelta(days=7)
    return out


def _last_year(target: dt.date, actuals: Mapping[dt.date, int]) -> dict:
    same_weekday = target - dt.timedelta(days=364)
    try:
        same_date = target.replace(year=target.year - 1)
    except ValueError:  # 29 February
        same_date = target - dt.timedelta(days=365)
    return {
        "same_weekday": {
            "date": same_weekday.isoformat(),
            "actual_cents": actuals.get(same_weekday),
        },
        "same_date": {
            "date": same_date.isoformat(),
            "actual_cents": actuals.get(same_date),
        },
    }


def _holiday_last_year(
    target: dt.date, name: str, inputs: Inputs
) -> tuple[dt.date, int] | None:
    """Last year's same-named holiday and what the venue took, if it traded."""
    lo, hi = (
        target - dt.timedelta(days=_LY_WINDOW[1]),
        target - dt.timedelta(days=_LY_WINDOW[0]),
    )
    for date, label in inputs.holidays.items():
        if lo <= date <= hi and label == name:
            actual = inputs.actuals.get(date)
            if actual:
                return date, actual
    return None


def forecast_day(
    target: dt.date,
    inputs: Inputs,
    as_of: dt.date,
    *,
    closed: set[int] | None = None,
    level: dict | None = None,
) -> DayResult:
    """One day's number and the components that made it."""
    closed = set(inputs.closed_weekdays) if closed is None else closed
    flags: list[str] = []
    components: dict = {
        "as_of": as_of.isoformat(),
        "samples": [],
        "median_of": 0,
        "level_correction": None,
        "uplifts": [],
        "override": None,
        "last_year": _last_year(target, inputs.actuals),
        "flags": flags,
    }
    baseline = 0
    if target.isoweekday() in closed:
        flags.append("closed")
    else:
        samples = _samples(target, inputs, as_of, closed)
        components["samples"] = samples
        used = [s["used_cents"] for s in samples if s["used_cents"] is not None]
        components["median_of"] = len(used)
        if not used:
            flags.append("no_history")
        else:
            if len(used) < MIN_SAMPLES:
                flags.append("thin_history")
            baseline = int(round(statistics.median(used)))
        if inputs.level_correction and level:
            components["level_correction"] = level
            baseline = int(round(baseline * level["factor"]))

    uplifts: list[dict] = []
    if target.isoweekday() not in closed:
        spend = int(inputs.event_spend.get(target, 0) or 0)
        if spend > 0:
            uplifts.append(
                {"kind": "function", "label": "Confirmed functions", "cents": spend}
            )
        holiday = inputs.holidays.get(target)
        if holiday:
            flags.append("holiday")
            ly = _holiday_last_year(target, holiday, inputs)
            if ly is not None:
                uplifts.append(
                    {
                        "kind": "holiday",
                        "label": f"{holiday}: last year's {ly[0].isoformat()}",
                        "cents": ly[1] - baseline,
                    }
                )
            else:
                flags.append("holiday_no_history")
                uplifts.append(
                    {
                        "kind": "holiday",
                        "label": f"{holiday}: no last-year trade, baseline kept",
                        "cents": 0,
                    }
                )
        for d in inputs.drivers.get(target, ()):
            if d.uplift_cents is not None:
                cents = int(d.uplift_cents)
            else:
                cents = int(round(baseline * int(d.uplift_bp or 0) / 10_000))
            uplifts.append(
                {
                    "kind": d.kind if d.kind != "holiday" else "manual",
                    "label": d.label,
                    "cents": cents,
                    "driver_id": d.id,
                }
            )
    components["uplifts"] = uplifts
    uplift = sum(u["cents"] for u in uplifts)
    forecast = max(baseline + uplift, 0)

    final = forecast
    override = inputs.overrides.get(target)
    if override is not None:
        final = int(override.amount_cents)
        components["override"] = {
            "id": override.id,
            "amount_cents": override.amount_cents,
            "reason": override.reason,
            "created_by": override.created_by,
            "created_at": override.created_at,
        }
        flags.append("override")
    components["baseline_cents"] = baseline
    components["forecast_cents"] = forecast
    components["final_cents"] = final
    return DayResult(target, baseline, uplift, forecast, final, components)


def run(
    inputs: Inputs, as_of: dt.date, *, horizon: int = HORIZON_DAYS
) -> list[DayResult]:
    """Every day from ``as_of`` for ``horizon`` days."""
    closed = set(inputs.closed_weekdays) | detect_closed_weekdays(inputs.actuals, as_of)
    level = (
        level_factor(inputs.actuals, as_of, closed) if inputs.level_correction else None
    )
    return [
        forecast_day(
            as_of + dt.timedelta(days=i), inputs, as_of, closed=closed, level=level
        )
        for i in range(horizon)
    ]


def _money(cents: int | None) -> str:
    if cents is None:
        return "—"
    return f"${cents / 100:,.0f}"


def why_text(components: dict, weekday_name: str) -> str:
    """One templated sentence a person can check against the drawer."""
    samples = components.get("samples") or []
    used = [s for s in samples if s.get("used_cents") is not None]
    flags = components.get("flags") or []
    parts = []
    if "closed" in flags:
        parts.append(f"The venue is closed on {weekday_name}s, so the forecast is $0")
    elif not used:
        parts.append(
            f"No {weekday_name}s with trade on record yet, so the baseline is $0"
        )
    else:
        lo = min(s["used_cents"] for s in used)
        hi = max(s["used_cents"] for s in used)
        excluded = [
            s
            for s in samples
            if s.get("used_cents") is None and s.get("reason") != "no_data"
        ]
        text = f"Median of the last {len(used)} {weekday_name}s ({_money(lo)}–{_money(hi)})"
        if excluded:
            reasons = {}
            for s in excluded:
                reasons[s["reason"]] = reasons.get(s["reason"], 0) + 1
            text += " — left out: " + ", ".join(
                f"{n} {r.replace('_', ' ')}" for r, n in reasons.items()
            )
        deflated = [s for s in used if s.get("event_spend_cents")]
        if deflated:
            text += f"; {len(deflated)} sample(s) had functions taken off"
        parts.append(text)
        lc = components.get("level_correction")
        if lc:
            parts.append(
                f"level correction ×{lc['factor']:.3f} (last 4 weeks vs the 4 before)"
            )
    for u in components.get("uplifts") or []:
        sign = "+" if u["cents"] >= 0 else "−"
        parts.append(f"{u['label']} {sign}{_money(abs(u['cents']))}")
    sentence = "; ".join(parts) + f" → {_money(components.get('forecast_cents'))}."
    override = components.get("override")
    if override:
        sentence += f" Overridden to {_money(override['amount_cents'])}"
        if override.get("reason"):
            sentence += f" ({override['reason']})"
        sentence += "."
    return sentence


# ---------------------------------------------------------------------------
# Inputs from the database
# ---------------------------------------------------------------------------


def functions_event_spend(
    db: Session, org_id: str, venue_id: str, first: dt.date, last: dt.date
) -> dict[dt.date, int]:
    """Expected spend of the venue's CONFIRMED Functions events by day —
    zero everywhere when Functions is off, has no events, or isn't installed."""
    try:
        from app.db.functions_models import FnEvent
        from app.db.models import Organization
        from app.services.functions import money as money_svc
        from app.services.functions import status as status_svc
    except ImportError:  # pragma: no cover — Functions always ships with Norm
        return {}
    mode = (
        db.query(Organization.functions_mode).filter(Organization.id == org_id).scalar()
    )
    if (mode or "off") == "off":
        return {}
    events = (
        db.query(FnEvent.id, FnEvent.event_date)
        .filter(
            FnEvent.organization_id == org_id,
            FnEvent.venue_id == venue_id,
            FnEvent.event_date >= first,
            FnEvent.event_date <= last,
            FnEvent.cancelled_at.is_(None),
        )
        .all()
    )
    if not events:
        return {}
    ids = [e.id for e in events]
    facts = status_svc.load_event_facts(db, org_id, ids)
    confirmed = {
        eid
        for eid, f in facts.items()
        if status_svc.event_status(f).status == "confirmed"
    }
    if not confirmed:
        return {}
    by_event = {e.id: e.event_date for e in events if e.id in confirmed}
    out: dict[dt.date, int] = {}
    for row in db.execute(
        money_svc.event_money_select(org_id, event_ids=list(confirmed))
    ):
        date = by_event.get(row.event_id)
        if date is None:
            continue
        out[date] = out.get(date, 0) + int(row.expected_spend_cents or 0)
    return out


def load_inputs(
    db: Session,
    org_id: str,
    venue_id: str,
    as_of: dt.date,
    *,
    horizon: int = HORIZON_DAYS,
) -> Inputs:
    from app.services.finance import actuals as actuals_svc
    from app.services.finance import budgets, drivers as drivers_svc, holidays

    history_from = as_of - dt.timedelta(days=_LY_WINDOW[1] + horizon)
    ahead = as_of + dt.timedelta(days=horizon)
    settings = budgets.venue_settings(db, org_id, venue_id)
    drivers = {
        date: tuple(
            Driver(r.kind, r.label, r.uplift_cents, r.uplift_bp, r.id) for r in rows
        )
        for date, rows in drivers_svc.drivers_by_date(
            db, org_id, venue_id, history_from, ahead
        ).items()
    }
    overrides = {
        date: Override(
            r.amount_cents,
            r.reason,
            r.created_by,
            r.created_at.isoformat() if r.created_at else None,
            r.id,
        )
        for date, r in drivers_svc.overrides_by_date(
            db, org_id, venue_id, as_of, ahead
        ).items()
    }
    try:
        event_spend = functions_event_spend(
            db,
            org_id,
            venue_id,
            as_of - dt.timedelta(days=LOOKBACK_WEEKS * 7 + 7),
            ahead,
        )
    except Exception as exc:  # noqa: BLE001 — a Functions hiccup never stops the forecast
        logger.warning(
            "forecast: Functions spend unavailable for %s: %s", venue_id, exc
        )
        event_spend = {}
    return Inputs(
        actuals=actuals_svc.series(
            db, org_id, venue_id, history_from, as_of - dt.timedelta(days=1)
        ),
        event_spend=event_spend,
        holidays=holidays.for_venue(db, org_id, venue_id, history_from, ahead),
        drivers=drivers,
        overrides=overrides,
        closed_weekdays=frozenset(int(d) for d in (settings.closed_weekdays or []))
        if settings
        else frozenset(),
        level_correction=bool(settings.level_correction_on) if settings else False,
    )


# ---------------------------------------------------------------------------
# Vintages
# ---------------------------------------------------------------------------


def store_vintage(
    db: Session,
    org_id: str,
    venue_id: str,
    as_of: dt.date,
    results: Sequence[DayResult],
    *,
    method: str = METHOD,
    horizon: int = HORIZON_DAYS,
    params: dict | None = None,
) -> FinForecastVintage:
    vintage = FinForecastVintage(
        organization_id=org_id,
        venue_id=venue_id,
        as_of=as_of,
        horizon_days=horizon,
        method=method,
        params=params or {},
    )
    db.add(vintage)
    db.flush()
    for r in results:
        db.add(
            FinForecastDay(
                organization_id=org_id,
                vintage_id=vintage.id,
                date=r.date,
                baseline_cents=r.baseline_cents,
                uplift_cents=r.uplift_cents,
                forecast_cents=r.forecast_cents,
                components=r.components,
            )
        )
    db.flush()
    return vintage


def build_vintage(
    db: Session,
    org_id: str,
    venue_id: str,
    as_of: dt.date,
    *,
    horizon: int = HORIZON_DAYS,
) -> tuple[FinForecastVintage, list[DayResult]]:
    """Load the inputs, run the engine from ``as_of``, store the vintage."""
    inputs = load_inputs(db, org_id, venue_id, as_of, horizon=horizon)
    results = run(inputs, as_of, horizon=horizon)
    vintage = store_vintage(
        db,
        org_id,
        venue_id,
        as_of,
        results,
        horizon=horizon,
        params={
            "samples": SAMPLES,
            "min_samples": MIN_SAMPLES,
            "lookback_weeks": LOOKBACK_WEEKS,
            "level_correction": inputs.level_correction,
            "closed_weekdays": sorted(inputs.closed_weekdays),
            "history_days": len(inputs.actuals),
        },
    )
    return vintage, results


def latest_vintage(
    db: Session,
    org_id: str,
    venue_id: str,
    *,
    as_of: dt.date | None = None,
    method: str = METHOD,
) -> FinForecastVintage | None:
    """The newest vintage (on or before ``as_of`` when given)."""
    q = db.query(FinForecastVintage).filter(
        FinForecastVintage.organization_id == org_id,
        FinForecastVintage.venue_id == venue_id,
        FinForecastVintage.method == method,
    )
    if as_of is not None:
        q = q.filter(FinForecastVintage.as_of <= as_of)
    return q.order_by(
        FinForecastVintage.as_of.desc(), FinForecastVintage.created_at.desc()
    ).first()


def vintage_at(
    db: Session, org_id: str, venue_id: str, as_of: dt.date, *, slack_days: int = 3
) -> FinForecastVintage | None:
    """The vintage made on ``as_of`` — or the nearest earlier one within
    ``slack_days`` (a night the job missed)."""
    return (
        db.query(FinForecastVintage)
        .filter(
            FinForecastVintage.organization_id == org_id,
            FinForecastVintage.venue_id == venue_id,
            FinForecastVintage.as_of <= as_of,
            FinForecastVintage.as_of >= as_of - dt.timedelta(days=slack_days),
        )
        .order_by(FinForecastVintage.as_of.desc(), FinForecastVintage.created_at.desc())
        .first()
    )


def vintage_days(
    db: Session, vintage_id: str, first: dt.date, last: dt.date
) -> dict[dt.date, FinForecastDay]:
    return {
        r.date: r
        for r in db.query(FinForecastDay).filter(
            FinForecastDay.vintage_id == vintage_id,
            FinForecastDay.date >= first,
            FinForecastDay.date <= last,
        )
    }


def vintage_dict(v: FinForecastVintage | None) -> dict | None:
    if v is None:
        return None
    return {
        "id": v.id,
        "as_of": v.as_of.isoformat(),
        "horizon_days": v.horizon_days,
        "method": v.method,
        "params": v.params,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }


def get_vintage(db: Session, org_id: str, vintage_id: str) -> FinForecastVintage:
    row = (
        db.query(FinForecastVintage)
        .filter(
            FinForecastVintage.organization_id == org_id,
            FinForecastVintage.id == vintage_id,
        )
        .first()
    )
    if row is None:
        raise NotFound("Forecast vintage not found.")
    return row


def parse_date(value, what: str = "date") -> dt.date:
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError) as exc:
        raise Invalid(f"{what} must be YYYY-MM-DD.") from exc
