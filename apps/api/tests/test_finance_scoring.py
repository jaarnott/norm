"""Scoring from day one (plan §8, 05 §8.4): the maths on a fixture, a
closed day scored against the vintages made 1/7/28 days earlier and the
Loaded budget as it stood 28 days earlier, the month strip, the push gate,
and the backtest on a synthetic series.
"""

from __future__ import annotations

import datetime as dt

from app.db.finance_models import FinForecastScore, FinForecastVintage
from app.services.finance import actuals as actuals_svc
from app.services.finance import budgets
from app.services.finance import forecast as engine
from app.services.finance import pushes, scoring
from tests.finance_f6_world import make_world, weekday_series

START = dt.date(2026, 1, 5)
DAY = dt.date(2026, 11, 14)  # a Saturday


class TestMaths:
    def test_ape_bias_within_and_monthly_sum_on_a_fixture(self):
        items = [
            scoring.Scored(DAY, 100_000, 110_000),
            scoring.Scored(DAY + dt.timedelta(days=1), 200_000, 150_000),
            scoring.Scored(DAY + dt.timedelta(days=2), 400_000, 400_000),
            scoring.Scored(
                DAY + dt.timedelta(days=3), 0, 50_000
            ),  # a $0 day scores nothing
        ]
        out = scoring.summarise(items)
        assert out["n"] == 3
        assert out["mape"] == 11.7 and out["median_ape"] == 10.0
        assert out["bias_pct"] == -5.0
        assert out["within_10"] == 0.67 and out["within_20"] == 0.67
        assert out["monthly_sum_error_pct"] == 5.7
        assert scoring.ape(0, 10) is None and scoring.ape(200, 150) == 0.25
        assert scoring.summarise([]) == {"n": 0}


def _vintage_with(db, w, as_of: dt.date, day: dt.date, forecast: int, method="median8"):
    result = engine.DayResult(day, forecast, 0, forecast, forecast, {"flags": []})
    return engine.store_vintage(
        db, w.org.id, w.laz.id, as_of, [result], method=method, horizon=28
    )


class TestScoreDay:
    def test_a_closed_day_is_scored_against_three_vintages_and_both_benchmarks(
        self, db_session
    ):
        w = make_world(db_session)
        ctx = w.ctx()
        v1 = _vintage_with(db_session, w, DAY - dt.timedelta(days=1), DAY, 110_000)
        v7 = _vintage_with(db_session, w, DAY - dt.timedelta(days=7), DAY, 120_000)
        v28 = _vintage_with(db_session, w, DAY - dt.timedelta(days=28), DAY, 130_000)
        # A vintage 3 days earlier must not be mistaken for horizon 1 or 7.
        _vintage_with(db_session, w, DAY - dt.timedelta(days=3), DAY, 999_000)
        pushes.append_loaded_log(
            db_session,
            w.org.id,
            w.laz.id,
            DAY - dt.timedelta(days=40),
            {DAY: (90_000, "b1")},
        )
        pushes.append_loaded_log(
            db_session,
            w.org.id,
            w.laz.id,
            DAY - dt.timedelta(days=10),
            {DAY: (105_000, "b1")},
        )
        version = budgets.create_version(db_session, ctx, w.laz.id, 2027)
        budgets.set_days(
            db_session,
            ctx,
            version.id,
            [{"date": DAY.isoformat(), "amount_cents": 95_000}],
        )
        budgets.lock_version(db_session, ctx, version.id)

        out = scoring.score_day(db_session, w.org.id, w.laz.id, DAY, 100_000)
        assert {k: v["vintage_id"] for k, v in out["vintages"].items()} == {
            "1": v1.id,
            "7": v7.id,
            "28": v28.id,
        }
        # The benchmark is the budget as it stood 28 days before — the 90,000
        # capture, not the later 105,000 one.
        assert out["benchmarks"] == {
            "loaded_budget_28": 90_000,
            "locked_budget": 95_000,
        }
        rows = (
            db_session.query(FinForecastScore)
            .filter(FinForecastScore.venue_id == w.laz.id)
            .all()
        )
        assert len(rows) == 5
        by_subject = {(r.vintage_id, r.benchmark): r for r in rows}
        assert by_subject[(v7.id, None)].signed_error_cents == 20_000
        assert float(by_subject[(None, "loaded_budget_28")].ape) == 0.1
        # Scoring again replaces, never duplicates.
        scoring.score_day(db_session, w.org.id, w.laz.id, DAY, 100_000)
        assert (
            db_session.query(FinForecastScore)
            .filter(FinForecastScore.venue_id == w.laz.id)
            .count()
            == 5
        )

        strip = scoring.month_strip(db_session, w.org.id, w.laz.id, DAY.year, DAY.month)
        assert strip["horizons"]["1"]["mape"] == 10.0
        assert strip["horizons"]["28"]["bias_pct"] == 30.0
        assert strip["benchmarks"]["locked_budget"]["mape"] == 5.0

    def test_a_missed_night_is_bridged_by_the_nearest_earlier_vintage_within_three_days(
        self, db_session
    ):
        w = make_world(db_session)
        v = _vintage_with(db_session, w, DAY - dt.timedelta(days=9), DAY, 120_000)
        out = scoring.score_day(db_session, w.org.id, w.laz.id, DAY, 100_000)
        assert out["vintages"]["7"]["vintage_id"] == v.id
        assert "1" not in out["vintages"] and "28" not in out["vintages"]


class TestPushGate:
    def test_four_weeks_of_nightly_scores_open_the_forecast_push_backtest_vintages_do_not(
        self, db_session
    ):
        w = make_world(db_session)
        assert scoring.push_ready(db_session, w.org.id, w.laz.id) == (False, 0)
        monday = dt.date(2026, 10, 5)
        for i in range(21):  # three weeks, daily
            day = monday + dt.timedelta(days=i)
            v = _vintage_with(db_session, w, day - dt.timedelta(days=1), day, 100_000)
            scoring._upsert(
                db_session, w.org.id, w.laz.id, day, 100_000, 100_000, vintage_id=v.id
            )
        for i in range(21, 60):  # backtest vintages are not the live engine
            day = monday + dt.timedelta(days=i)
            v = _vintage_with(
                db_session,
                w,
                day - dt.timedelta(days=1),
                day,
                100_000,
                method="backtest",
            )
            scoring._upsert(
                db_session, w.org.id, w.laz.id, day, 100_000, 100_000, vintage_id=v.id
            )
        db_session.flush()
        assert scoring.push_ready(db_session, w.org.id, w.laz.id) == (False, 3)
        for i in range(21, 28):
            day = monday + dt.timedelta(days=i)
            v = _vintage_with(db_session, w, day - dt.timedelta(days=1), day, 100_000)
            scoring._upsert(
                db_session, w.org.id, w.laz.id, day, 100_000, 100_000, vintage_id=v.id
            )
        db_session.flush()
        assert scoring.push_ready(db_session, w.org.id, w.laz.id) == (True, 4)


class TestBacktest:
    def _seed(self, w, days=420):
        series = weekday_series(START, days)
        actuals_svc.upsert_days(
            w.db,
            w.org.id,
            w.laz.id,
            [
                {
                    "date": d,
                    "sales_incl_cents": c,
                    "sales_ex_cents": int(c / 1.15),
                    "orders": 1,
                }
                for d, c in series.items()
            ],
        )
        return series

    def test_pseudo_vintages_over_a_synthetic_series_score_every_horizon(
        self, db_session
    ):
        w = make_world(db_session)
        series = self._seed(w)
        last = max(series)
        # A human benchmark: the actual + 10% everywhere → MAPE 10, bias +10.
        benchmark = {d: int(c * 1.1) for d, c in series.items() if c}
        out = scoring.backtest(
            db_session, w.org.id, w.laz.id, months=2, through=last, benchmark=benchmark
        )
        assert out["from"] < out["to"] == last.isoformat()
        for h in ("1", "7", "28"):
            s = out["horizons"][h]
            assert s["n"] > 40 and 0 <= s["mape"] < 20, (h, s)
            assert s["within_20"] > 0.8
        assert out["benchmark"]["mape"] == 10.0 and out["benchmark"]["bias_pct"] == 10.0
        assert out["by_month"] and all("1" in m for m in out["by_month"])
        assert out["stored_vintages"] == 0
        assert db_session.query(FinForecastVintage).count() == 0

    def test_storing_the_backtest_populates_the_strip_without_opening_the_push(
        self, db_session
    ):
        w = make_world(db_session)
        series = self._seed(w)
        last = max(series)
        out = scoring.backtest(
            db_session, w.org.id, w.laz.id, months=1, through=last, store=True
        )
        assert out["stored_vintages"] > 0
        assert (
            db_session.query(FinForecastVintage)
            .filter(FinForecastVintage.method == "backtest")
            .count()
            == out["stored_vintages"]
        )
        strip = scoring.month_strip(
            db_session, w.org.id, w.laz.id, last.year, last.month
        )
        assert strip["horizons"]["1"]["n"] > 0
        assert scoring.push_ready(db_session, w.org.id, w.laz.id)[0] is False

    def test_a_falling_series_is_over_forecast_unless_the_level_correction_is_on(
        self, db_session
    ):
        """The +17–30% bias the reader measured on falling trade: a series
        that halves over the window is over-forecast by every trailing
        method; the switch reduces the bias."""
        w = make_world(db_session)
        series = weekday_series(START, 420)
        total = len(series)
        for i, d in enumerate(sorted(series)):
            series[d] = int(series[d] * (1 - 0.5 * i / total))
        actuals_svc.upsert_days(
            db_session,
            w.org.id,
            w.laz.id,
            [
                {"date": d, "sales_incl_cents": c, "sales_ex_cents": c, "orders": 1}
                for d, c in series.items()
            ],
        )
        last = max(series)
        off = scoring.backtest(
            db_session,
            w.org.id,
            w.laz.id,
            months=3,
            through=last,
            level_correction=False,
        )
        on = scoring.backtest(
            db_session,
            w.org.id,
            w.laz.id,
            months=3,
            through=last,
            level_correction=True,
        )
        assert off["horizons"]["7"]["bias_pct"] > 0
        assert on["horizons"]["7"]["bias_pct"] < off["horizons"]["7"]["bias_pct"]
