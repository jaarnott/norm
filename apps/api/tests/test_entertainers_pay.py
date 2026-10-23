"""The pay maths (DST-aware, Orbit's ``nzShiftTime`` ported), the one
15-minute mismatch rule, and the derived status — the Python function
against its SQL twin over the same rows.

The NZ clock changes in 2026: forward on Sunday 27 September (02:00 → 03:00,
an hour lost), back on Sunday 5 April (03:00 → 02:00, an hour gained).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.db.entertainers_models import EntPerformance
from app.db.models import Venue
from app.services.entertainers import status as status_svc
from app.services.entertainers._shared import (
    amount_cents,
    clock_minutes,
    dst_adjustment_minutes,
    hours_of,
    local_to_utc,
    real_minutes,
)
from tests.entertainers_world import UTC, add_performer, add_performance, make_world

NZ = ZoneInfo("Pacific/Auckland")
T = dt.time


class TestRealMinutes:
    def test_an_ordinary_night_is_its_clock_minutes(self):
        assert real_minutes(dt.date(2026, 10, 10), T(21, 0), T(1, 0), False, NZ) == 240
        assert clock_minutes(T(21, 0), T(1, 0)) == 240

    def test_spring_forward_loses_an_hour(self):
        # 26 Sep 22:00 NZST → 27 Sep 03:00 NZDT: the clock says 5 hours, 4 were played.
        assert clock_minutes(T(22, 0), T(3, 0)) == 300
        assert real_minutes(dt.date(2026, 9, 26), T(22, 0), T(3, 0), False, NZ) == 240
        assert (
            dst_adjustment_minutes(dt.date(2026, 9, 26), T(22, 0), T(3, 0), False, NZ)
            == -60
        )

    def test_fall_back_gains_an_hour(self):
        # 4 Apr 22:00 NZDT → 5 Apr 03:00 NZST: 5 on the clock, 6 played.
        assert real_minutes(dt.date(2026, 4, 4), T(22, 0), T(3, 0), False, NZ) == 360
        assert (
            dst_adjustment_minutes(dt.date(2026, 4, 4), T(22, 0), T(3, 0), False, NZ)
            == 60
        )

    def test_a_set_entirely_before_the_change_is_untouched(self):
        assert real_minutes(dt.date(2026, 9, 26), T(19, 0), T(23, 0), False, NZ) == 240

    def test_the_repeated_hour_resolves_as_orbit_did(self):
        # Orbit's two-pass localToUtc reads 02:30 on 5 April as the SECOND
        # 02:30 (standard time), so an end at 02:30 after a 22:00 start is
        # 5h30 played, not 4h30.
        assert real_minutes(dt.date(2026, 4, 4), T(22, 0), T(2, 30), False, NZ) == 330
        assert local_to_utc(dt.date(2026, 4, 5), T(2, 30), NZ) == dt.datetime(
            2026, 4, 4, 14, 30, tzinfo=UTC
        )

    def test_the_missing_hour_resolves_an_hour_on(self):
        # 02:30 on 27 September does not exist; it reads as 03:30 NZDT.
        assert local_to_utc(dt.date(2026, 9, 27), T(2, 30), NZ) == dt.datetime(
            2026, 9, 26, 14, 30, tzinfo=UTC
        )

    def test_ends_next_day_flag_rolls_over_even_when_end_is_later_on_the_clock(self):
        assert (
            real_minutes(dt.date(2026, 10, 10), T(10, 0), T(11, 0), True, NZ) == 25 * 60
        )

    def test_a_blank_zone_name_falls_back_to_auckland(self):
        from app.services.entertainers._shared import venue_tz

        assert str(venue_tz(Venue(name="x", timezone=None))) == "Pacific/Auckland"
        assert (
            str(venue_tz(Venue(name="x", timezone="Not/AZone"))) == "Pacific/Auckland"
        )


class TestAmount:
    def test_rate_times_real_hours_rounded_half_up(self):
        assert amount_cents(10_000, 240) == 40_000
        assert amount_cents(12_345, 90) == 18_518  # 185.175 → 185.18
        assert amount_cents(10_001, 30) == 5_001  # 50.005 → 50.01 (JS Math.round)
        assert hours_of(90) == Decimal("1.50") and hours_of(100) == Decimal("1.67")

    def test_dst_night_pays_for_hours_played(self):
        assert (
            amount_cents(
                10_000, real_minutes(dt.date(2026, 9, 26), T(22, 0), T(3, 0), False, NZ)
            )
            == 40_000
        )
        assert (
            amount_cents(
                10_000, real_minutes(dt.date(2026, 4, 4), T(22, 0), T(3, 0), False, NZ)
            )
            == 60_000
        )


def _perf(**kw) -> EntPerformance:
    base = dict(
        performance_date=dt.date(2026, 10, 10),
        start_time=T(21, 0),
        end_time=T(1, 0),
        ends_next_day=False,
        performance_status="scheduled",
        payment_status="pending",
        performer_dnp=False,
    )
    base.update(kw)
    return EntPerformance(**base)


class TestMismatchRule:
    def test_within_fifteen_minutes_at_both_ends_matches(self):
        p = _perf(
            actual_start_time=T(21, 0),
            actual_end_time=T(1, 0),
            performer_actual_start_time=T(21, 15),
            performer_actual_end_time=T(0, 45),
        )
        assert status_svc.times_match(p, 15) is True

    def test_sixteen_minutes_at_one_end_is_a_mismatch(self):
        p = _perf(
            actual_start_time=T(21, 0),
            actual_end_time=T(1, 0),
            performer_actual_start_time=T(21, 16),
            performer_actual_end_time=T(1, 0),
        )
        assert status_svc.times_match(p, 15) is False

    def test_manager_side_falls_back_to_the_booked_times(self):
        p = _perf(
            performer_actual_start_time=T(21, 5), performer_actual_end_time=T(1, 5)
        )
        assert status_svc.times_match(p, 15) is True

    def test_no_performer_times_is_undecided(self):
        assert status_svc.times_match(_perf(), 15) is None

    def test_the_tolerance_is_the_settings_value_not_exact_equality(self):
        p = _perf(
            actual_start_time=T(21, 0),
            actual_end_time=T(1, 0),
            performer_actual_start_time=T(21, 5),
            performer_actual_end_time=T(1, 0),
        )
        assert status_svc.times_match(p, 15) is True
        assert status_svc.times_match(p, 0) is False


NOW = dt.datetime(2026, 10, 12, 0, 0, tzinfo=UTC)  # Mon 12 Oct 13:00 NZDT


class TestDerivedStatus:
    @pytest.mark.parametrize(
        "fields, expected",
        [
            ({}, "awaiting_approvals"),
            ({"performance_date": dt.date(2026, 10, 20)}, "scheduled"),
            (
                {
                    "performance_status": "approved",
                    "actual_start_time": T(21, 0),
                    "actual_end_time": T(1, 0),
                },
                "awaiting_performer",
            ),
            (
                {
                    "performer_confirmed_at": NOW,
                    "performer_actual_start_time": T(21, 0),
                    "performer_actual_end_time": T(1, 0),
                },
                "awaiting_manager",
            ),
            (
                {
                    "performance_status": "approved",
                    "actual_start_time": T(21, 0),
                    "actual_end_time": T(1, 0),
                    "performer_confirmed_at": NOW,
                    "performer_actual_start_time": T(21, 10),
                    "performer_actual_end_time": T(1, 0),
                },
                "ready_for_payment",
            ),
            (
                {
                    "performance_status": "approved",
                    "actual_start_time": T(21, 0),
                    "actual_end_time": T(1, 0),
                    "performer_confirmed_at": NOW,
                    "performer_actual_start_time": T(21, 30),
                    "performer_actual_end_time": T(1, 0),
                },
                "mismatch",
            ),
            ({"performance_status": "did_not_perform"}, "did_not_perform"),
            ({"performer_dnp": True, "performer_confirmed_at": NOW}, "did_not_perform"),
            ({"payment_status": "paid", "performance_status": "approved"}, "paid"),
            (
                {"payment_status": "paid", "performance_status": "did_not_perform"},
                "paid",
            ),
        ],
    )
    def test_python_rule(self, fields, expected):
        assert (
            status_svc.derive(_perf(**fields), tz=NZ, tolerance=15, now=NOW) == expected
        )

    def test_a_performance_ending_later_today_is_still_scheduled(self):
        # Mon 12 Oct 13:00 NZDT: a set 20:00–23:00 tonight has not ended.
        p = _perf(
            performance_date=dt.date(2026, 10, 12),
            start_time=T(20, 0),
            end_time=T(23, 0),
        )
        assert status_svc.derive(p, tz=NZ, tolerance=15, now=NOW) == "scheduled"
        assert (
            status_svc.derive(
                p, tz=NZ, tolerance=15, now=NOW + dt.timedelta(hours=10, minutes=1)
            )
            == "awaiting_approvals"
        )

    def test_sql_twin_agrees_with_python_on_every_branch(self, db_session):
        w = make_world(db_session)
        performer = add_performer(db_session, w.org)
        cases = [
            {},
            {"date": dt.date(2026, 10, 20)},
            {
                "date": dt.date(2026, 10, 12),
                "start": T(20, 0),
                "end": T(23, 0),
            },  # ends tonight
            {
                "date": dt.date(2026, 10, 11),
                "start": T(20, 0),
                "end": T(2, 0),
            },  # ended this morning
            {
                "performance_status": "approved",
                "actual_start_time": T(21, 0),
                "actual_end_time": T(1, 0),
            },
            {
                "performer_confirmed_at": NOW,
                "performer_actual_start_time": T(21, 0),
                "performer_actual_end_time": T(1, 0),
            },
            {
                "performance_status": "approved",
                "actual_start_time": T(21, 0),
                "actual_end_time": T(1, 0),
                "performer_confirmed_at": NOW,
                "performer_actual_start_time": T(21, 14),
                "performer_actual_end_time": T(1, 15),
            },
            {
                "performance_status": "approved",
                "actual_start_time": T(21, 0),
                "actual_end_time": T(1, 0),
                "performer_confirmed_at": NOW,
                "performer_actual_start_time": T(21, 16),
                "performer_actual_end_time": T(1, 0),
            },
            {
                "performance_status": "approved",
                "performer_confirmed_at": NOW,
                "performer_actual_start_time": T(21, 0),
                "performer_actual_end_time": T(1, 0),
            },  # approved as rostered
            {"performance_status": "did_not_perform"},
            {"performer_dnp": True, "performer_confirmed_at": NOW},
            {"payment_status": "paid", "performance_status": "approved"},
            # the spring-forward night: ends 03:00 NZDT on 27 Sep
            {"date": dt.date(2026, 9, 26), "start": T(22, 0), "end": T(3, 0)},
        ]
        rows = []
        for case in cases:
            kw = dict(case)
            date = kw.pop("date", dt.date(2026, 10, 10))
            start = kw.pop("start", T(21, 0))
            end = kw.pop("end", T(1, 0))
            rows.append(
                add_performance(
                    db_session,
                    w.org,
                    w.venue,
                    performer,
                    date=date,
                    start=start,
                    end=end,
                    **kw,
                )
            )
        db_session.flush()
        twin = status_svc.sql_status(tolerance=15, now=NOW).label("status")
        got = dict(
            db_session.query(EntPerformance.id, twin)
            .join(Venue, Venue.id == EntPerformance.venue_id)
            .filter(EntPerformance.organization_id == w.org.id)
            .all()
        )
        for row in rows:
            python = status_svc.derive(row, tz=NZ, tolerance=15, now=NOW)
            assert got[row.id] == python, (
                f"{row.performance_date} {row.start_time}-{row.end_time}: sql {got[row.id]} != python {python}"
            )
        assert {got[r.id] for r in rows} >= {
            "scheduled",
            "awaiting_approvals",
            "awaiting_performer",
            "awaiting_manager",
            "ready_for_payment",
            "mismatch",
            "did_not_perform",
            "paid",
        }
