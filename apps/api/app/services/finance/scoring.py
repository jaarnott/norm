"""Scoring from day one (plan §8, 05 §8.4): every vintage measured against
what then happened, beside the human budget as the benchmark.

Each night, once a trading day's actual is known, the day is scored against
the vintages made 1, 7 and 28 days earlier (APE and signed error per
venue-day, ``fin_forecast_scores``) and against **the Loaded budget as it
stood 28 days earlier** (``fin_loaded_budget_log``) — the number a person
set in the budget-setting situation. Per venue-month the strip shows MAPE,
median APE, bias, the share within ±10/±20% and the monthly-sum error, per
horizon and per benchmark. Orbit's ``totalAcc`` cancelled over- and
under-forecasts (La Zeppa Feb 2026: "98.3% accurate" beside a daily MAPE of
79.7%); none of that survives here.

``backtest`` builds pseudo-vintages from the cached actuals for the last six
months so the strip is populated before anyone trusts a number — and so the
reader's figures (05 §6.3) can be reproduced on real data.
"""

from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.finance_models import FinForecastScore, FinForecastVintage
from app.services.finance import forecast as engine
from app.services.finance_core.errors import Invalid

HORIZONS = (1, 7, 28)
#: Benchmarks scored beside the vintages.
LOADED_28 = "loaded_budget_28"
LOCKED = "locked_budget"
BACKTEST_METHOD = "backtest"
#: "Set Loaded budget" shows once this many weeks of nightly scores exist.
PUSH_READY_WEEKS = 4


@dataclass(frozen=True)
class Scored:
    date: dt.date
    actual_cents: int
    forecast_cents: int

    @property
    def ape(self) -> float | None:
        return ape(self.actual_cents, self.forecast_cents)

    @property
    def signed_error_cents(self) -> int:
        return self.forecast_cents - self.actual_cents


def ape(actual: int, forecast: int) -> float | None:
    """Absolute percentage error, as a fraction; None on a $0 day."""
    if not actual:
        return None
    return abs(forecast - actual) / actual


def summarise(items: list[Scored]) -> dict:
    """The strip's numbers for a set of scored days (percentages as
    percents, shares as fractions). Days with no actual are left out."""
    scored = [s for s in items if s.actual_cents > 0]
    if not scored:
        return {"n": 0}
    apes = [s.ape for s in scored]
    actual = sum(s.actual_cents for s in scored)
    forecast = sum(s.forecast_cents for s in scored)
    signed = [s.signed_error_cents / s.actual_cents for s in scored]
    return {
        "n": len(scored),
        "mape": round(statistics.fmean(apes) * 100, 1),
        "median_ape": round(statistics.median(apes) * 100, 1),
        "bias_pct": round(statistics.fmean(signed) * 100, 1),
        "within_10": round(sum(1 for a in apes if a <= 0.10) / len(apes), 2),
        "within_20": round(sum(1 for a in apes if a <= 0.20) / len(apes), 2),
        "monthly_sum_error_pct": round(abs(forecast - actual) / actual * 100, 1),
        "actual_cents": actual,
        "forecast_cents": forecast,
    }


# ---------------------------------------------------------------------------
# Storing a day's scores
# ---------------------------------------------------------------------------


def _upsert(
    db: Session,
    org_id: str,
    venue_id: str,
    date: dt.date,
    actual: int,
    forecast: int,
    *,
    vintage_id: str | None = None,
    benchmark: str | None = None,
) -> FinForecastScore:
    row = (
        db.query(FinForecastScore)
        .filter(
            FinForecastScore.venue_id == venue_id,
            FinForecastScore.vintage_id == vintage_id,
            FinForecastScore.benchmark == benchmark,
            FinForecastScore.date == date,
        )
        .first()
    )
    if row is None:
        row = FinForecastScore(
            organization_id=org_id,
            venue_id=venue_id,
            vintage_id=vintage_id,
            benchmark=benchmark,
            date=date,
        )
        db.add(row)
    row.actual_cents = int(actual)
    row.forecast_cents = int(forecast)
    a = ape(actual, forecast)
    row.ape = round(a, 4) if a is not None else None
    row.signed_error_cents = int(forecast) - int(actual)
    return row


def score_day(
    db: Session,
    org_id: str,
    venue_id: str,
    day: dt.date,
    actual_cents: int,
    *,
    horizons=HORIZONS,
) -> dict:
    """Score one closed trading day: the vintages made 1/7/28 days earlier,
    the Loaded budget as at 28 days earlier, and the locked budget."""
    from app.services.finance import budgets, pushes

    out = {"date": day.isoformat(), "vintages": {}, "benchmarks": {}}
    for h in horizons:
        vintage = engine.vintage_at(db, org_id, venue_id, day - dt.timedelta(days=h))
        if vintage is None:
            continue
        row = engine.vintage_days(db, vintage.id, day, day).get(day)
        if row is None:
            continue
        _upsert(
            db,
            org_id,
            venue_id,
            day,
            actual_cents,
            row.forecast_cents,
            vintage_id=vintage.id,
        )
        out["vintages"][str(h)] = {
            "vintage_id": vintage.id,
            "forecast_cents": row.forecast_cents,
        }
    loaded = pushes.loaded_as_at(
        db, org_id, venue_id, day, day, as_of=day - dt.timedelta(days=28)
    ).get(day)
    if loaded is not None:
        _upsert(db, org_id, venue_id, day, actual_cents, loaded, benchmark=LOADED_28)
        out["benchmarks"][LOADED_28] = loaded
    locked = budgets.locked_cents(db, org_id, venue_id, day, day).get(day)
    if locked is not None:
        _upsert(db, org_id, venue_id, day, actual_cents, locked, benchmark=LOCKED)
        out["benchmarks"][LOCKED] = locked
    db.flush()
    return out


# ---------------------------------------------------------------------------
# Reading the strip
# ---------------------------------------------------------------------------


def month_strip(db: Session, org_id: str, venue_id: str, year: int, month: int) -> dict:
    """Per horizon and per benchmark, the summary of a venue-month's scores."""
    from app.services.finance import budgets

    first, last = budgets.month_bounds(year, month)
    rows = (
        db.query(FinForecastScore, FinForecastVintage.as_of, FinForecastVintage.method)
        .outerjoin(
            FinForecastVintage, FinForecastVintage.id == FinForecastScore.vintage_id
        )
        .filter(
            FinForecastScore.organization_id == org_id,
            FinForecastScore.venue_id == venue_id,
            FinForecastScore.date >= first,
            FinForecastScore.date <= last,
        )
        .all()
    )
    by_horizon: dict[str, list[Scored]] = {}
    by_benchmark: dict[str, list[Scored]] = {}
    for score, as_of, _method in rows:
        item = Scored(score.date, score.actual_cents, score.forecast_cents)
        if score.benchmark:
            by_benchmark.setdefault(score.benchmark, []).append(item)
        elif as_of is not None:
            h = (score.date - as_of).days
            if h in HORIZONS:
                by_horizon.setdefault(str(h), []).append(item)
    return {
        "venue_id": venue_id,
        "year": int(year),
        "month": int(month),
        "horizons": {
            h: summarise(v)
            for h, v in sorted(by_horizon.items(), key=lambda kv: int(kv[0]))
        },
        "benchmarks": {b: summarise(v) for b, v in sorted(by_benchmark.items())},
    }


def weeks_of_scores(db: Session, org_id: str, venue_id: str) -> int:
    """Distinct ISO weeks with a NIGHTLY vintage scored (backtest vintages
    don't count — the gate is about the live engine)."""
    rows = (
        db.query(FinForecastScore.date)
        .join(FinForecastVintage, FinForecastVintage.id == FinForecastScore.vintage_id)
        .filter(
            FinForecastScore.organization_id == org_id,
            FinForecastScore.venue_id == venue_id,
            FinForecastVintage.method == engine.METHOD,
        )
        .distinct()
        .all()
    )
    return len({d.isocalendar()[:2] for (d,) in rows})


def push_ready(db: Session, org_id: str, venue_id: str) -> tuple[bool, int]:
    weeks = weeks_of_scores(db, org_id, venue_id)
    return weeks >= PUSH_READY_WEEKS, weeks


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------


def backtest(
    db: Session,
    org_id: str,
    venue_id: str,
    *,
    months: int = 6,
    horizons=HORIZONS,
    through: dt.date | None = None,
    store: bool = False,
    benchmark: dict[dt.date, int] | None = None,
    benchmark_name: str = LOADED_28,
    level_correction: bool | None = None,
) -> dict:
    """Pseudo-vintages over the last ``months`` months from the cached
    actuals: for each day D and horizon h the engine runs as of D − h, with
    the inputs it would have had. Returns the summary per horizon (overall
    and per month) and, when a ``benchmark`` series is given (or the Loaded
    log holds one), the human budget's summary beside it. With ``store`` the
    vintages and scores are written so the strip is populated at once."""
    from app.services.finance import actuals as actuals_svc
    from app.services.finance import pushes

    if months < 1 or months > 36:
        raise Invalid("months must be 1–36.")
    inputs = engine.load_inputs(db, org_id, venue_id, through or dt.date.today())
    if level_correction is not None:
        inputs.level_correction = level_correction
    # The whole cached history, so an as_of a year back still has samples.
    first_actual = min(inputs.actuals) if inputs.actuals else None
    coverage = actuals_svc.coverage(db, org_id, venue_id)
    if coverage["last"] is None:
        return {"venue_id": venue_id, "error": "no actuals cached — load history first"}
    last = min(through or dt.date.max, dt.date.fromisoformat(coverage["last"]))
    full = actuals_svc.series(
        db, org_id, venue_id, dt.date.fromisoformat(coverage["first"]), last
    )
    inputs.actuals = full
    first_actual = min(full) if full else None
    first = max(last - dt.timedelta(days=int(months * 30.44)), first_actual or last)
    if benchmark is None:
        benchmark = pushes.loaded_as_at_offset(
            db, org_id, venue_id, first, last, days_before=28
        )

    closed_setting = set(inputs.closed_weekdays)
    per_h: dict[int, list[Scored]] = {h: [] for h in horizons}
    bench: list[Scored] = []
    monthly: dict[tuple[int, int], dict[int, list[Scored]]] = {}
    stored = 0
    cursor = first
    while cursor <= last:
        actual = full.get(cursor)
        if actual is None or actual <= 0:
            cursor += dt.timedelta(days=1)
            continue
        if benchmark.get(cursor):
            bench.append(Scored(cursor, actual, benchmark[cursor]))
        month_key = (cursor.year, cursor.month)
        for h in horizons:
            as_of = cursor - dt.timedelta(days=h)
            closed = closed_setting | engine.detect_closed_weekdays(
                _before(full, as_of), as_of
            )
            if cursor.isoweekday() in closed:
                continue
            level = (
                engine.level_factor(_before(full, as_of), as_of, closed)
                if inputs.level_correction
                else None
            )
            result = engine.forecast_day(
                cursor, _restricted(inputs, as_of), as_of, closed=closed, level=level
            )
            item = Scored(cursor, actual, result.forecast_cents)
            per_h[h].append(item)
            monthly.setdefault(month_key, {hh: [] for hh in horizons})[h].append(item)
            if store:
                vintage = engine.store_vintage(
                    db,
                    org_id,
                    venue_id,
                    as_of,
                    [result],
                    method=BACKTEST_METHOD,
                    horizon=h,
                    params={"backtest": True, "horizon": h},
                )
                _upsert(
                    db,
                    org_id,
                    venue_id,
                    cursor,
                    actual,
                    result.forecast_cents,
                    vintage_id=vintage.id,
                )
                stored += 1
        if store and benchmark.get(cursor):
            _upsert(
                db,
                org_id,
                venue_id,
                cursor,
                actual,
                benchmark[cursor],
                benchmark=benchmark_name,
            )
        cursor += dt.timedelta(days=1)
    if store:
        db.flush()
    return {
        "venue_id": venue_id,
        "from": first.isoformat(),
        "to": last.isoformat(),
        "months": months,
        "level_correction": inputs.level_correction,
        "closed_weekdays": sorted(closed_setting),
        "horizons": {str(h): summarise(v) for h, v in per_h.items()},
        "benchmark": {"name": benchmark_name, **summarise(bench)},
        "by_month": [
            {"year": y, "month": m, **{str(h): summarise(v) for h, v in hs.items()}}
            for (y, m), hs in sorted(monthly.items())
        ],
        "stored_vintages": stored,
    }


def _before(full: dict[dt.date, int], as_of: dt.date) -> dict[dt.date, int]:
    """The actuals as they stood the night of ``as_of``: nothing from that
    day on."""
    return {d: v for d, v in full.items() if d < as_of}


def _restricted(inputs: engine.Inputs, as_of: dt.date) -> engine.Inputs:
    """The inputs as they stood on ``as_of``: no actual from that day on,
    and no override (a backtest measures the engine, not the people)."""
    return engine.Inputs(
        actuals=_before(inputs.actuals, as_of),
        event_spend=inputs.event_spend,
        holidays=inputs.holidays,
        drivers=inputs.drivers,
        overrides={},
        closed_weekdays=inputs.closed_weekdays,
        level_correction=inputs.level_correction,
    )
