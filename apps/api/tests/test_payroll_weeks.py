"""Pay weeks and snapshots (plan §9 step 1): the Monday-at-day-start
boundary at a 06:00 and a 07:00 venue across the NZ clock change, the
snapshot store (facts, hashes, dropped counts, supersession), the diff by
Loaded's shift id, drift since close, and the close that needs a close
snapshot.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.services.finance_core.errors import Conflict, Invalid, NotFound
from app.services.payroll import snapshot as snapshot_svc
from app.services.payroll import weeks as weeks_svc
from tests.payroll_world import (
    DSC_LOADED,
    MONDAY,
    NZ,
    FakeLoaded,
    install,
    make_world,
    payrun_result,
    shift,
)

#: NZ clocks go forward on Sun 27 Sep 2026 (02:00 → 03:00): this week has 167 hours.
DST_WEEK = dt.date(2026, 9, 21)


@pytest.fixture
def w(db_session):
    return make_world(db_session)


class TestPayWeekBoundary:
    @pytest.mark.parametrize(
        "venue_attr,start_hhmm", [("laz", "07:00"), ("dsc", "06:00")]
    )
    def test_the_week_runs_day_start_to_day_start_across_the_clock_change(
        self, w, venue_attr, start_hhmm
    ):
        venue = getattr(w, venue_attr)
        window = weeks_svc.week_window(venue, DST_WEEK)
        h, m = (int(x) for x in start_hhmm.split(":"))
        assert window.start == dt.datetime(2026, 9, 21, h, m, tzinfo=NZ)
        assert window.start.utcoffset() == dt.timedelta(hours=12)
        assert window.end == dt.datetime(2026, 9, 28, h, m, tzinfo=NZ) - dt.timedelta(
            seconds=1
        )
        assert window.end.utcoffset() == dt.timedelta(hours=13)
        # 7 days less the hour the clocks skipped — never "+7 days" on a timestamp.
        # (Compared in UTC: Python subtracts two datetimes sharing one tzinfo as wall time.)
        utc = dt.timezone.utc
        elapsed = (
            window.end.astimezone(utc)
            + dt.timedelta(seconds=1)
            - window.start.astimezone(utc)
        )
        assert elapsed.total_seconds() == 167 * 3600

    def test_a_clock_in_before_day_start_belongs_to_the_week_before(self, w):
        moment = dt.datetime(2026, 9, 28, 6, 30, tzinfo=NZ)
        assert (
            weeks_svc.week_start_for(w.laz, moment) == DST_WEEK
        )  # 07:00 venue: still last week
        assert weeks_svc.week_start_for(w.dsc, moment) == dt.date(
            2026, 9, 28
        )  # 06:00 venue: this week

    def test_week_start_must_be_a_monday(self):
        with pytest.raises(Invalid, match="Monday"):
            weeks_svc.validate_week_start("2026-10-13")
        assert weeks_svc.validate_week_start("2026-10-12") == MONDAY

    def test_last_completed_week(self, w):
        now = dt.datetime(
            2026, 10, 19, 7, 30, tzinfo=NZ
        )  # Monday 07:30: last week just ended
        assert weeks_svc.last_completed_week_start(w.laz, now) == MONDAY
        earlier = dt.datetime(
            2026, 10, 19, 6, 30, tzinfo=NZ
        )  # still inside WE 18 Oct at a 07:00 venue
        assert weeks_svc.last_completed_week_start(
            w.laz, earlier
        ) == MONDAY - dt.timedelta(days=7)


ROWS = [
    shift("s1", "a", "Ana Lee", hours=6.0, cost_cents=18000),
    shift(
        "s2",
        "a",
        "Ana Lee",
        start="2026-10-14T17:00:00+13:00",
        end="2026-10-15T01:00:00+13:00",
        hours=8.0,
        cost_cents=24000,
    ),
    shift(
        "s3",
        "b",
        "Ben Roy",
        code="LZ002",
        hours=5.0,
        cost_cents=12500,
        worked=DSC_LOADED,
        other=True,
    ),
    shift("s4", "c", "Cal Wu", code="SECURITY", hours=4.0, cost_cents=16000),
    shift(
        "s5",
        "a",
        "Ana Lee",
        shift_type="Leave",
        hours=8.0,
        cost_cents=24000,
        leave_request_id="lr1",
        leave_type="NZ Annual",
    ),
]


class TestSnapshot:
    def test_store_facts_counts_and_hashes(self, w, monkeypatch):
        fake = install(
            monkeypatch,
            FakeLoaded(
                get_payrun=lambda v, p: payrun_result(
                    v, MONDAY, ROWS, dropped={"deleted": 2, "unfinished": 3}
                )
            ),
        )
        snap = snapshot_svc.take(w.db, w.ctx(), w.laz.id, MONDAY, source="monday")
        assert fake.calls[0][1:] == (
            "get_payrun",
            {
                "week_ending": "2026-10-18",
                "view": "all",
                "security_pay_code": "SECURITY",
            },
        )
        assert (snap.shift_count, snap.dropped_deleted, snap.dropped_unfinished) == (
            5,
            2,
            3,
        )
        facts = {f.source_shift_id: f for f in snapshot_svc.facts_of(w.db, snap)}
        assert facts["s2"].trading_date == dt.date(
            2026, 10, 14
        )  # a 01:00 clock-out stays on the 14th
        assert facts["s2"].clockin_at == dt.datetime(2026, 10, 14, 17, 0, tzinfo=NZ)
        assert float(facts["s1"].hours) == 6.0 and facts["s1"].cost_cents == 18000
        assert (
            facts["s3"].worked_venue_id == w.dsc.id
        )  # Loaded's venueId → the Norm venue, by company id
        assert facts["s1"].worked_venue_id == w.laz.id
        assert len(facts["s1"].payload_sha256) == 64 and len(snap.content_sha256) == 64
        totals = snapshot_svc.totals(facts.values())
        assert totals == {
            "shifts": 4,
            "hours": 23.0,
            "cost_cents": 70500,
            "ph_hours": 0.0,
            "ph_cost_cents": 0,
            "leave_hours": 8.0,
            "leave_cost_cents": 24000,
        }

    def test_a_retake_supersedes_and_keeps_the_old_one(self, w, monkeypatch):
        install(
            monkeypatch,
            FakeLoaded(get_payrun=lambda v, p: payrun_result(v, MONDAY, ROWS)),
        )
        first = snapshot_svc.take(w.db, w.ctx(), w.laz.id, MONDAY, source="monday")
        second = snapshot_svc.take(w.db, w.ctx(), w.laz.id, MONDAY, source="manual")
        w.db.refresh(first)
        assert first.superseded_at is not None and second.superseded_at is None
        assert (
            first.content_sha256 == second.content_sha256
        )  # unchanged Loaded → recognisable
        week = weeks_svc.get_or_create(w.db, w.ctx(), w.laz.id, MONDAY)
        assert weeks_svc.current_snapshot(w.db, week).id == second.id
        assert len(snapshot_svc.list_for_week(w.db, week)) == 2

    def test_loaded_failure_is_the_services_error_and_stores_nothing(
        self, w, monkeypatch
    ):
        install(monkeypatch, FakeLoaded(get_payrun={"error": "token expired"}))
        with pytest.raises(Invalid, match="token expired"):
            snapshot_svc.take(w.db, w.ctx(), w.laz.id, MONDAY, source="monday")
        week = weeks_svc.get_or_create(w.db, w.ctx(), w.laz.id, MONDAY)
        assert weeks_svc.current_snapshot(w.db, week) is None

    def test_diff_by_shift_id_and_drift_since_close(self, w, monkeypatch):
        install(
            monkeypatch,
            FakeLoaded(get_payrun=lambda v, p: payrun_result(v, MONDAY, ROWS)),
        )
        close = snapshot_svc.take(w.db, w.ctx(), w.laz.id, MONDAY, source="close")
        week = weeks_svc.get_or_create(w.db, w.ctx(), w.laz.id, MONDAY)
        assert snapshot_svc.drift(w.db, week) is None
        changed = [dict(r) for r in ROWS if r["source_shift_id"] != "s4"]
        changed[0] = {**changed[0], "hours": 6.5, "cost_cents": 19500}
        changed.append(
            shift("s9", "d", "Dee Fox", code="LZ009", hours=3.0, cost_cents=9000)
        )
        install(
            monkeypatch,
            FakeLoaded(get_payrun=lambda v, p: payrun_result(v, MONDAY, changed)),
        )
        later = snapshot_svc.take(w.db, w.ctx(), w.laz.id, MONDAY, source="manual")
        d = snapshot_svc.diff(w.db, close, later)
        assert d["same"] is False
        assert [r["source_shift_id"] for r in d["added"]] == ["s9"]
        assert [r["source_shift_id"] for r in d["removed"]] == ["s4"]
        assert d["changed"] == [
            {
                "source_shift_id": "s1",
                "staff_name": "Ana Lee",
                "fields": {"hours": [6.0, 6.5], "cost_cents": [18000, 19500]},
            }
        ]
        assert d["totals"]["delta"]["cost_cents"] == 19500 - 18000 + 9000 - 16000
        drift = snapshot_svc.drift(w.db, week)
        assert drift is not None and drift["a"] == close.id and drift["b"] == later.id
        # The close still builds from the close snapshot, not the later one.
        assert weeks_svc.close_snapshot(w.db, week).id == close.id

    def test_a_snapshot_needs_payroll_read(self, w, monkeypatch):
        install(
            monkeypatch,
            FakeLoaded(get_payrun=lambda v, p: payrun_result(v, MONDAY, ROWS)),
        )
        from app.services.finance_core.errors import Forbidden

        with pytest.raises(Forbidden):
            snapshot_svc.take(w.db, w.ctx(w.manager), w.laz.id, MONDAY, source="manual")


class TestLoadedSeam:
    def test_a_raw_endpoints_top_level_array_is_usable(self):
        """get_leave_types / get_staff_members answer with a JSON array; the
        seam must pass it on (the leave-style seed failed on every venue
        while it refused anything but a dict — F8)."""
        from types import SimpleNamespace

        rows = [{"id": "M:1", "name": "NZ Annual"}]
        ok = SimpleNamespace(success=True, payload=rows, error=None)
        assert snapshot_svc._payload(ok) == rows
        assert snapshot_svc._payload({"success": True, "data": {"rows": []}}) == {
            "rows": []
        }
        with pytest.raises(Invalid, match="token expired"):
            snapshot_svc._payload(
                SimpleNamespace(success=False, payload=None, error="token expired")
            )
        with pytest.raises(Invalid, match="nothing usable"):
            snapshot_svc._payload(
                SimpleNamespace(success=True, payload="text", error=None)
            )


class TestWeekStatus:
    def test_status_follows_the_facts_and_close_needs_a_close_snapshot(
        self, w, monkeypatch
    ):
        ctx = w.ctx()
        week = weeks_svc.get_or_create(w.db, ctx, w.laz.id, MONDAY)
        assert weeks_svc.status_of(w.db, week) == "open"
        with pytest.raises(Conflict, match="Start close"):
            weeks_svc.close(w.db, ctx, week, version=week.version)
        install(
            monkeypatch,
            FakeLoaded(get_payrun=lambda v, p: payrun_result(v, MONDAY, ROWS)),
        )
        snapshot_svc.take(w.db, ctx, w.laz.id, MONDAY, source="monday")
        assert weeks_svc.status_of(w.db, week) == "snapshotted"
        snapshot_svc.take(w.db, ctx, w.laz.id, MONDAY, source="close")
        assert weeks_svc.status_of(w.db, week) == "close_ready"
        weeks_svc.close(w.db, ctx, week, version=week.version)
        assert week.closed_at is not None and week.closed_by == w.payroll_admin.id
        assert weeks_svc.status_of(w.db, week) == "closed"
        with pytest.raises(Conflict, match="already closed"):
            weeks_svc.close(w.db, ctx, week, version=week.version)

    def test_another_organisations_week_is_not_found(self, w):
        other = weeks_svc.get_or_create(
            w.db, w.ctx(w.other_owner), w.other_venue.id, MONDAY
        )
        with pytest.raises(NotFound):
            weeks_svc.get(w.db, w.ctx(), other.id)
        from app.services.finance_core.errors import Forbidden

        with pytest.raises((NotFound, Forbidden)):
            weeks_svc.get_or_create(w.db, w.ctx(), w.other_venue.id, MONDAY)

    def test_a_person_sees_only_their_venues_weeks(self, w):
        weeks_svc.get_or_create(w.db, w.ctx(w.owner), w.dsc.id, MONDAY)
        weeks_svc.get_or_create(w.db, w.ctx(w.owner), w.laz.id, MONDAY)
        mine = weeks_svc.list_weeks(w.db, w.ctx())  # payroll_admin holds La Zeppa only
        assert {x.venue_id for x in mine} == {w.laz.id}
        assert {x.venue_id for x in weeks_svc.list_weeks(w.db, w.ctx(w.owner))} == {
            w.laz.id,
            w.dsc.id,
        }
