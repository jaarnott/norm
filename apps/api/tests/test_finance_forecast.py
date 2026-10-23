"""Forecasting v1 (plan §8): the engine's samples, exclusions, deflation and
uplifts; the closed-day and holiday rules; the override that survives
regeneration; and the trading-day boundaries the inputs are read on,
across the NZ clock change.
"""

from __future__ import annotations

import datetime as dt
import statistics
import uuid
from zoneinfo import ZoneInfo

import pytest

from app.db.finance_models import FinForecastDay, FinForecastVintage
from app.services.finance import actuals as actuals_svc
from app.services.finance import drivers as drivers_svc
from app.services.finance import forecast as f
from app.services.finance import jobs
from tests.finance_f6_world import MONDAY, make_world, weekday_series

NZ = ZoneInfo("Pacific/Auckland")
START = dt.date(2026, 1, 5)  # a Monday
AS_OF = START + dt.timedelta(days=300)  # Sun 1 Nov 2026


def _saturday_after(day: dt.date) -> dt.date:
    while day.isoweekday() != 6:
        day += dt.timedelta(days=1)
    return day


def _saturdays_before(as_of: dt.date, n: int) -> list[dt.date]:
    out, cursor = [], as_of - dt.timedelta(days=1)
    while len(out) < n:
        if cursor.isoweekday() == 6:
            out.append(cursor)
        cursor -= dt.timedelta(days=1)
    return out


@pytest.fixture
def series():
    return weekday_series(START, 300)


# ===========================================================================
# The pure engine
# ===========================================================================


class TestBaseline:
    def test_median_of_the_last_eight_same_weekdays_not_the_mean(self, series):
        target = _saturday_after(AS_OF)
        samples = [series[d] for d in _saturdays_before(AS_OF, 8)]
        # One outlier makes mean and median differ.
        series[_saturdays_before(AS_OF, 8)[0]] = 900_000
        samples[0] = 900_000
        out = f.forecast_day(target, f.Inputs(actuals=series), AS_OF)
        assert out.baseline_cents == int(statistics.median(samples))
        assert out.baseline_cents != int(statistics.mean(samples))
        assert out.components["median_of"] == 8
        assert [s["date"] for s in out.components["samples"]] == [
            d.isoformat() for d in _saturdays_before(AS_OF, 8)
        ]

    def test_fewer_than_four_samples_is_flagged_thin_and_none_is_no_history(self):
        target = _saturday_after(AS_OF)
        thin = {d: 100_000 for d in _saturdays_before(AS_OF, 3)}
        out = f.forecast_day(target, f.Inputs(actuals=thin), AS_OF)
        assert (
            out.baseline_cents == 100_000 and "thin_history" in out.components["flags"]
        )
        out = f.forecast_day(target, f.Inputs(actuals={}), AS_OF)
        assert out.baseline_cents == 0 and "no_history" in out.components["flags"]

    def test_holiday_driver_and_zero_days_are_left_out_with_their_reason(self, series):
        target = _saturday_after(AS_OF)
        sats = _saturdays_before(AS_OF, 12)
        series[sats[0]] = 0
        inputs = f.Inputs(
            actuals=series,
            holidays={sats[1]: "Labour Day"},
            drivers={sats[2]: (f.Driver("manual", "Concert"),)},
        )
        out = f.forecast_day(target, inputs, AS_OF)
        reasons = {s["date"]: s["reason"] for s in out.components["samples"]}
        assert reasons[sats[0].isoformat()] == "closed"
        assert reasons[sats[1].isoformat()] == "holiday"
        assert reasons[sats[2].isoformat()] == "driver"
        used = [s for s in out.components["samples"] if s["used_cents"] is not None]
        assert len(used) == 8 and sats[3].isoformat() == used[0]["date"]
        assert out.baseline_cents == int(
            statistics.median(series[d] for d in sats[3:11])
        )

    def test_a_sample_with_functions_is_deflated_by_their_expected_spend(self, series):
        target = _saturday_after(AS_OF)
        sats = _saturdays_before(AS_OF, 8)
        plain = int(statistics.median(series[d] for d in sats))
        spend = {sats[0]: 50_000, sats[1]: 50_000, sats[2]: 50_000, sats[3]: 50_000}
        out = f.forecast_day(target, f.Inputs(actuals=series, event_spend=spend), AS_OF)
        deflated = [s for s in out.components["samples"] if s["reason"] == "deflated"]
        assert len(deflated) == 4 and all(
            s["used_cents"] == s["actual_cents"] - 50_000 for s in deflated
        )
        assert out.baseline_cents < plain

    def test_a_closed_weekday_forecasts_zero_whether_set_or_detected(self, series):
        monday = AS_OF + dt.timedelta(days=1)
        assert monday.isoweekday() == 1
        # Detected: the series has Monday at $0 every week.
        assert f.detect_closed_weekdays(series, AS_OF) == {1}
        out = f.run(f.Inputs(actuals=series), AS_OF, horizon=3)[1]
        assert (
            out.date == monday
            and out.forecast_cents == 0
            and "closed" in out.components["flags"]
        )
        # Set: a Tuesday the venue says it is closed.
        out = f.forecast_day(
            monday + dt.timedelta(days=1),
            f.Inputs(actuals=series, closed_weekdays=frozenset({2})),
            AS_OF,
        )
        assert out.forecast_cents == 0 and "closed" in out.components["flags"]

    def test_detection_needs_three_quarters_of_eight_weeks(self, series):
        # Three of the last eight Tuesdays at $0 is a bad run, not a closed day.
        tuesdays = [d for d in sorted(series) if d.isoweekday() == 2 and d < AS_OF][-8:]
        for d in tuesdays[:3]:
            series[d] = 0
        assert 2 not in f.detect_closed_weekdays(series, AS_OF)
        for d in tuesdays[3:6]:
            series[d] = 0
        assert 2 in f.detect_closed_weekdays(series, AS_OF)


class TestUplifts:
    def test_functions_and_manual_drivers_add_with_labels(self, series):
        target = _saturday_after(AS_OF)
        inputs = f.Inputs(
            actuals=series,
            event_spend={target: 34_500},
            drivers={
                target: (
                    f.Driver("manual", "Round the Bays", uplift_cents=20_000),
                    f.Driver("event", "Concert", uplift_bp=1000),
                )
            },
        )
        out = f.forecast_day(target, inputs, AS_OF)
        labels = [u["label"] for u in out.components["uplifts"]]
        assert labels == ["Confirmed functions", "Round the Bays", "Concert"]
        pct = int(round(out.baseline_cents * 0.10))
        assert out.uplift_cents == 34_500 + 20_000 + pct
        assert out.forecast_cents == out.baseline_cents + out.uplift_cents

    def test_a_public_holiday_takes_last_years_same_holiday_when_traded(self, series):
        target = _saturday_after(AS_OF)
        last_year = target - dt.timedelta(days=364)
        series[last_year] = 777_000
        inputs = f.Inputs(
            actuals=series, holidays={target: "Labour Day", last_year: "Labour Day"}
        )
        out = f.forecast_day(target, inputs, AS_OF)
        assert out.forecast_cents == 777_000
        assert "holiday" in out.components["flags"]
        assert out.components["uplifts"][0]["kind"] == "holiday"
        assert last_year.isoformat() in out.components["uplifts"][0]["label"]

    def test_a_public_holiday_with_no_last_year_keeps_the_baseline_and_says_so(
        self, series
    ):
        target = _saturday_after(AS_OF)
        out = f.forecast_day(
            target, f.Inputs(actuals=series, holidays={target: "Matariki"}), AS_OF
        )
        assert out.forecast_cents == out.baseline_cents
        assert "holiday_no_history" in out.components["flags"]

    def test_an_override_replaces_the_final_number_and_keeps_the_forecast(self, series):
        target = _saturday_after(AS_OF)
        inputs = f.Inputs(
            actuals=series, overrides={target: f.Override(123_400, "big night", "u1")}
        )
        out = f.forecast_day(target, inputs, AS_OF)
        assert out.final_cents == 123_400 and out.forecast_cents == out.baseline_cents
        assert out.components["override"]["reason"] == "big night"
        assert "Overridden to $1,234" in f.why_text(out.components, "Saturday")

    def test_the_last_year_column_is_a_reference_never_blended(self, series):
        target = _saturday_after(AS_OF)
        series[target - dt.timedelta(days=364)] = 5_000_000
        out = f.forecast_day(target, f.Inputs(actuals=series), AS_OF)
        assert out.components["last_year"]["same_weekday"]["actual_cents"] == 5_000_000
        assert out.baseline_cents < 1_000_000


class TestLevelCorrection:
    def test_off_by_default_on_as_a_switch_damped_and_clipped(self, series):
        target = _saturday_after(AS_OF)
        # Halve the last four weeks: ratio 0.5 → damped 0.75 → clipped to 0.85.
        for d in list(series):
            if AS_OF - dt.timedelta(days=28) <= d < AS_OF:
                series[d] //= 2
        off = f.run(f.Inputs(actuals=series), AS_OF, horizon=7)
        on = f.run(f.Inputs(actuals=series, level_correction=True), AS_OF, horizon=7)
        sat_off = next(r for r in off if r.date == target)
        sat_on = next(r for r in on if r.date == target)
        assert sat_off.components["level_correction"] is None
        assert sat_on.components["level_correction"]["factor"] == 0.85
        assert sat_on.baseline_cents == int(round(sat_off.baseline_cents * 0.85))


class TestWhy:
    def test_the_sentence_names_the_samples_exclusions_and_uplifts(self, series):
        target = _saturday_after(AS_OF)
        sats = _saturdays_before(AS_OF, 9)
        inputs = f.Inputs(
            actuals=series,
            holidays={sats[1]: "Labour Day"},
            event_spend={target: 50_000},
        )
        out = f.forecast_day(target, inputs, AS_OF)
        why = f.why_text(out.components, "Saturday")
        assert why.startswith("Median of the last 8 Saturdays")
        assert "1 holiday" in why and "Confirmed functions +$500" in why


# ===========================================================================
# Trading-day boundaries
# ===========================================================================


class TestTradingDayBoundaries:
    def test_the_actuals_window_carries_each_ends_own_offset_across_the_clock_change(
        self, db_session
    ):
        w = make_world(db_session)
        # NZ went to NZDT on Sun 27 Sep 2026.
        window = actuals_svc.window_for(
            w.laz, dt.date(2026, 9, 26), dt.date(2026, 9, 28)
        )
        assert window["start"] == "2026-09-26T07:00:00+12:00"
        assert window["end"] == "2026-09-29T07:00:00+13:00"
        assert window["confirmed_by_user"] is True
        window = actuals_svc.window_for(
            w.dsc, dt.date(2026, 9, 27), dt.date(2026, 9, 27)
        )
        assert window["start"] == "2026-09-27T06:00:00+13:00"

    def test_the_night_slot_opens_thirty_minutes_after_the_day_start(self, db_session):
        w = make_world(db_session)

        def at(h, m, day=dt.date(2026, 9, 28)):
            return dt.datetime(day.year, day.month, day.day, h, m, tzinfo=NZ)

        assert jobs.closed_trading_day(w.laz, at(7, 29)) is None
        assert jobs.closed_trading_day(w.laz, at(7, 31)) == dt.date(2026, 9, 27)
        assert jobs.closed_trading_day(w.dsc, at(6, 31)) == dt.date(2026, 9, 27)
        assert jobs.closed_trading_day(w.dsc, at(6, 29)) is None
        # 02:00 the next morning: the 28th's trade is still open (its day runs
        # to 06:59), and the 27th's slot was already due the morning before.
        assert jobs.closed_trading_day(w.laz, at(2, 0, dt.date(2026, 9, 29))) is None
        assert jobs.closed_trading_day(
            w.laz, at(7, 31, dt.date(2026, 9, 29))
        ) == dt.date(2026, 9, 28)


# ===========================================================================
# Vintages in the database
# ===========================================================================


def _seed_actuals(w, venue, first: dt.date, days: int):
    series = weekday_series(first, days)
    actuals_svc.upsert_days(
        w.db,
        w.org.id,
        venue.id,
        [
            {
                "date": d,
                "sales_incl_cents": c,
                "sales_ex_cents": int(c / 1.15),
                "orders": 10,
            }
            for d, c in series.items()
        ],
    )
    return series


class TestVintages:
    def test_a_vintage_stores_seventy_days_with_components(self, db_session):
        w = make_world(db_session)
        _seed_actuals(w, w.laz, START, 300)
        vintage, results = f.build_vintage(db_session, w.org.id, w.laz.id, AS_OF)
        assert (
            len(results) == 70
            and vintage.method == "median8"
            and vintage.horizon_days == 70
        )
        rows = engine_days(db_session, vintage.id)
        assert len(rows) == 70
        sat = next(r for r in rows if r.date == _saturday_after(AS_OF))
        assert (
            sat.components["median_of"] == 8
            and sat.forecast_cents == sat.baseline_cents
        )
        assert (
            vintage.params["closed_weekdays"] == []
            and vintage.params["level_correction"] is False
        )
        assert f.latest_vintage(db_session, w.org.id, w.laz.id).id == vintage.id

    def test_an_override_survives_regeneration_and_an_archived_driver_does_not(
        self, db_session
    ):
        w = make_world(db_session)
        _seed_actuals(w, w.laz, START, 300)
        ctx = w.ctx()
        target = _saturday_after(AS_OF)
        drivers_svc.set_override(
            db_session, ctx, w.laz.id, target, 999_900, reason="owner's call"
        )
        driver = drivers_svc.add_driver(
            db_session,
            ctx,
            w.laz.id,
            target,
            kind="manual",
            label="Fireworks",
            uplift_cents=10_000,
        )
        _, first = f.build_vintage(db_session, w.org.id, w.laz.id, AS_OF)
        day = next(r for r in first if r.date == target)
        assert day.final_cents == 999_900 and day.uplift_cents == 10_000
        drivers_svc.archive_driver(db_session, ctx, driver.id)
        _, second = f.build_vintage(
            db_session, w.org.id, w.laz.id, AS_OF + dt.timedelta(days=1)
        )
        day = next(r for r in second if r.date == target)
        assert (
            day.final_cents == 999_900
            and day.components["override"]["reason"] == "owner's call"
        )
        assert day.uplift_cents == 0

    def test_the_venues_settings_drive_closed_days_and_the_level_switch(
        self, db_session
    ):
        w = make_world(db_session)
        _seed_actuals(w, w.laz, START, 300)
        from app.services.finance import budgets

        budgets.set_venue_settings(
            db_session, w.ctx(), w.laz.id, closed_weekdays=[2], level_correction_on=True
        )
        vintage, results = f.build_vintage(db_session, w.org.id, w.laz.id, AS_OF)
        tuesday = next(r for r in results if r.date.isoweekday() == 2)
        assert tuesday.forecast_cents == 0 and "closed" in tuesday.components["flags"]
        assert vintage.params["level_correction"] is True
        saturday = next(r for r in results if r.date.isoweekday() == 6)
        assert saturday.components["level_correction"] is not None

    def test_confirmed_functions_events_deflate_samples_and_lift_the_day(
        self, db_session
    ):
        """A real Functions event (three confirmed sections, a bar tab) on a
        past Saturday is taken off that sample; one on a future Saturday is
        added as an uplift. Functions in shadow still counts (real bookings)."""
        from app.db.functions_models import (
            FnContact,
            FnEvent,
            FnEventSectionConfirmation,
        )

        w = make_world(db_session)
        w.org.functions_mode = "shadow"
        _seed_actuals(w, w.laz, START, 300)
        contact = FnContact(organization_id=w.org.id, name="Casey Client")
        db_session.add(contact)
        db_session.flush()
        past, future = _saturdays_before(AS_OF, 1)[0], _saturday_after(AS_OF)
        for day in (past, future):
            event = FnEvent(
                organization_id=w.org.id,
                contact_id=contact.id,
                venue_id=w.laz.id,
                name="Birthday",
                event_date=day,
                bar_tab_cents=40_000,
            )
            db_session.add(event)
            db_session.flush()
            for section in ("details", "beverages", "food"):
                db_session.add(
                    FnEventSectionConfirmation(
                        organization_id=w.org.id,
                        event_id=event.id,
                        section=section,
                        kind="confirmed",
                        actor_kind="staff",
                    )
                )
        db_session.flush()
        spend = f.functions_event_spend(db_session, w.org.id, w.laz.id, past, future)
        assert spend == {past: 40_000, future: 40_000}
        _, results = f.build_vintage(db_session, w.org.id, w.laz.id, AS_OF)
        day = next(r for r in results if r.date == future)
        assert day.components["uplifts"][0] == {
            "kind": "function",
            "label": "Confirmed functions",
            "cents": 40_000,
        }
        sample = next(
            s for s in day.components["samples"] if s["date"] == past.isoformat()
        )
        assert sample["reason"] == "deflated" and sample["event_spend_cents"] == 40_000


def engine_days(db, vintage_id) -> list[FinForecastDay]:
    return (
        db.query(FinForecastDay)
        .filter(FinForecastDay.vintage_id == vintage_id)
        .order_by(FinForecastDay.date)
        .all()
    )


def test_vintage_rows_are_org_scoped(db_session):
    w = make_world(db_session)
    v = FinForecastVintage(
        organization_id=w.org.id,
        venue_id=w.laz.id,
        as_of=MONDAY,
        horizon_days=1,
        method="median8",
    )
    db_session.add(v)
    db_session.flush()
    with pytest.raises(Exception):
        f.get_vintage(db_session, w.other_org.id, v.id)
    assert f.get_vintage(db_session, w.org.id, v.id).id == v.id
    assert isinstance(uuid.UUID(v.id), uuid.UUID)
