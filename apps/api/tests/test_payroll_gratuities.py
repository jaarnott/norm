"""Gratuities (plan §9 step 4): shares summing to the pool with the residual
by largest remainder, leave and security excluded, hours only at the paying
venue across the overlapping pay weeks, the payroll entity on the shift
from its snapshot, inactive staff kept but unpayable, excluded staff
redistributed.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.services.finance_core.errors import Invalid
from app.services.payroll import gratuities as gratuities_svc
from app.services.payroll import snapshot as snapshot_svc
from tests.payroll_world import (
    DSC_LOADED,
    LAZ_LOADED,
    FakeLoaded,
    install,
    make_world,
    payrun_result,
    shift,
)

SEP_29 = dt.date(2026, 9, 28)  # the pay week straddling September / October
OCT_5 = dt.date(2026, 10, 5)
OCT_12 = dt.date(2026, 10, 12)
NOV_2 = dt.date(2026, 11, 2)  # the batch is paid in this week


@pytest.fixture
def w(db_session):
    return make_world(db_session)


def _row(sid, staff, name, monday, hours, **kw):
    start = (
        dt.datetime.combine(monday + dt.timedelta(days=2), dt.time(17, 0)).isoformat()
        + "+13:00"
    )
    return shift(
        sid,
        staff,
        name,
        start=start,
        end=start,
        hours=hours,
        cost_cents=int(hours * 2500),
        **kw,
    )


class TestSplit:
    def test_shares_sum_to_the_pool_by_largest_remainder(self):
        rows = [{"hours": 1.0}, {"hours": 1.0}, {"hours": 1.0}]
        assert gratuities_svc.split(rows, 1000) == [334, 333, 333]
        assert sum(gratuities_svc.split(rows, 1000)) == 1000
        rows = [{"hours": 10.0}, {"hours": 5.0}, {"hours": 2.5}]
        assert gratuities_svc.split(rows, 100001) == [57143, 28572, 14286]

    def test_excluded_rows_are_redistributed(self):
        rows = [{"hours": 10.0}, {"hours": 10.0, "excluded": True}, {"hours": 10.0}]
        assert gratuities_svc.split(rows, 1001) == [501, 0, 500]

    def test_weeks_overlapping_a_month(self):
        assert gratuities_svc.weeks_for_period("2026-09") == [
            dt.date(2026, 8, 31),
            dt.date(2026, 9, 7),
            dt.date(2026, 9, 14),
            dt.date(2026, 9, 21),
            dt.date(2026, 9, 28),
        ]
        with pytest.raises(Invalid):
            gratuities_svc.weeks_for_period("Sept 2026")


def _snapshots(w, monkeypatch):
    """La Zeppa's and DSC's snapshots for the weeks overlapping October."""
    laz_rows = {
        SEP_29: [
            _row("a1", "a", "Ana Lee", SEP_29, 10.0),  # Wed 30 Sep: before the period
            shift(
                "a2",
                "a",
                "Ana Lee",
                start="2026-10-02T17:00:00+13:00",
                end="2026-10-02T23:00:00+13:00",
                hours=6.0,
                cost_cents=15000,
            ),
        ],
        OCT_5: [
            _row("a3", "a", "Ana Lee", OCT_5, 10.0),
            _row("b1", "b", "Ben Roy", OCT_5, 10.0, code="LZ002"),
            _row("s1", "s", "Sam Guard", OCT_5, 8.0, code="SECURITY"),
            _row(
                "l1",
                "a",
                "Ana Lee",
                OCT_5,
                8.0,
                shift_type="Leave",
                leave_request_id="lr1",
                leave_type="NZ Annual",
            ),
            _row(
                "x1",
                "x",
                "Xan Away",
                OCT_5,
                4.0,
                code="LZ099",
                worked=DSC_LOADED,
                other=True,
            ),  # worked at DSC: not this pool
        ],
        OCT_12: [_row("g1", "g", "Gone Person", OCT_12, 4.0, code="LZ050")],
    }
    dsc_rows = {
        OCT_5: [
            _row(
                "d1",
                "d",
                "Dee Fox",
                OCT_5,
                10.0,
                code="DS001",
                worked=LAZ_LOADED,
                other=True,
            )
        ]
    }

    def answer(venue, params):
        monday = dt.date.fromisoformat(params["week_ending"]) - dt.timedelta(days=6)
        rows = (laz_rows if venue.id == w.laz.id else dsc_rows).get(monday, [])
        return payrun_result(venue, monday, rows)

    install(
        monkeypatch,
        FakeLoaded(
            get_payrun=answer,
            get_staff_members=[
                {"id": "a"},
                {"id": "b"},
                {"id": "d"},
                {"id": "g", "datestampDeleted": "2026-10-20T00:00:00"},
            ],
        ),
    )
    owner = w.ctx(w.owner)
    for monday in (SEP_29, OCT_5, OCT_12):
        snapshot_svc.take(w.db, owner, w.laz.id, monday, source="monday")
    snapshot_svc.take(w.db, owner, w.dsc.id, OCT_5, source="monday")


class TestPreview:
    def test_hours_at_the_venue_in_the_period_tagged_with_the_paying_entity(
        self, w, monkeypatch
    ):
        _snapshots(w, monkeypatch)
        out = gratuities_svc.preview(
            w.db,
            w.ctx(w.owner),
            venue_id=w.laz.id,
            period="2026-10",
            week_start=NOV_2,
            total_cents=40000,
        )
        rows = {r["staff_name"]: r for r in out["rows"]}
        # Ana: 6 h (2 Oct) + 10 h = 16; Ben 10; Dee 10 (paid by DSC); Gone 4 (inactive). Leave, security and Xan's DSC shift are out.
        assert {n: r["hours"] for n, r in rows.items()} == {
            "Ana Lee": 16.0,
            "Ben Roy": 10.0,
            "Dee Fox": 10.0,
            "Gone Person": 4.0,
        }
        assert (
            rows["Dee Fox"]["payroll_venue_id"] == w.dsc.id
            and rows["Ana Lee"]["payroll_venue_id"] == w.laz.id
        )
        assert rows["Gone Person"]["is_payable"] is False
        assert sum(r["amount_cents"] for r in out["rows"]) == 40000
        assert (
            rows["Ana Lee"]["amount_cents"] == 16000
            and rows["Gone Person"]["amount_cents"] == 4000
        )
        assert (
            out["payable_cents"] == 36000
            and out["forfeited_cents"] == 4000
            and out["intercompany_cents"] == 10000
        )
        assert out["weeks_without_snapshot"] == ["2026-10-19", "2026-10-26"]
        assert any("No snapshot" in x for x in out["warnings"])

    def test_staff_list_failure_treats_everyone_as_payable_with_a_warning(
        self, w, monkeypatch
    ):
        _snapshots(w, monkeypatch)
        install(
            monkeypatch, FakeLoaded(get_payrun=lambda v, p: payrun_result(v, OCT_5, []))
        )
        out = gratuities_svc.preview(
            w.db,
            w.ctx(w.owner),
            venue_id=w.laz.id,
            period="2026-10",
            week_start=NOV_2,
            total_cents=40000,
        )
        assert all(r["is_payable"] for r in out["rows"])
        assert any("could not confirm staff activity" in x for x in out["warnings"])


class TestBatches:
    def test_create_exclude_and_payable_by(self, w, monkeypatch):
        _snapshots(w, monkeypatch)
        owner = w.ctx(w.owner)
        batch = gratuities_svc.create_batch(
            w.db,
            owner,
            venue_id=w.laz.id,
            period="2026-10",
            week_start=NOV_2,
            total_cents=40000,
            gl_code="4100",
        )
        d = gratuities_svc.batch_dict(w.db, batch)
        assert (
            d["payable_cents"] == 36000
            and d["forfeited_cents"] == 4000
            and len(d["allocations"]) == 4
        )
        ben = next(a for a in d["allocations"] if a["staff_name"] == "Ben Roy")
        gratuities_svc.exclude(w.db, owner, batch.id, ben["id"])
        d = gratuities_svc.batch_dict(w.db, batch)
        amounts = {a["staff_name"]: a["amount_cents"] for a in d["allocations"]}
        assert amounts["Ben Roy"] == 0 and sum(amounts.values()) == 40000
        assert (
            amounts["Ana Lee"] == 21334
            and amounts["Dee Fox"] == 13333
            and amounts["Gone Person"] == 5333
        )
        laz_shares = gratuities_svc.payable_by(w.db, owner, w.laz.id, NOV_2)
        assert {a.staff_name for _, a in laz_shares} == {
            "Ana Lee"
        }  # Gone is unpayable, Ben excluded, Dee paid by DSC
        dsc_shares = gratuities_svc.payable_by(w.db, owner, w.dsc.id, NOV_2)
        assert [(b.venue_id, a.staff_name, a.amount_cents) for b, a in dsc_shares] == [
            (w.laz.id, "Dee Fox", 13333)
        ]

    def test_nobody_worked_refuses(self, w, monkeypatch):
        install(
            monkeypatch,
            FakeLoaded(
                get_payrun=lambda v, p: payrun_result(v, OCT_5, []),
                get_staff_members=[],
            ),
        )
        with pytest.raises(Invalid, match="Nobody worked"):
            gratuities_svc.create_batch(
                w.db,
                w.ctx(w.owner),
                venue_id=w.laz.id,
                period="2026-10",
                week_start=NOV_2,
                total_cents=100,
            )

    def test_shadow_needs_rehearsal(self, db_session, monkeypatch):
        from app.services.finance_core.errors import NotLive

        w = make_world(db_session, payroll="shadow")
        _snapshots(w, monkeypatch)
        with pytest.raises(NotLive):
            gratuities_svc.create_batch(
                w.db,
                w.ctx(w.owner),
                venue_id=w.laz.id,
                period="2026-10",
                week_start=NOV_2,
                total_cents=100,
            )
        batch = gratuities_svc.create_batch(
            w.db,
            w.ctx(w.owner),
            venue_id=w.laz.id,
            period="2026-10",
            week_start=NOV_2,
            total_cents=100,
            rehearsal=True,
        )
        assert batch.rehearsal is True
        assert gratuities_svc.payable_by(w.db, w.ctx(w.owner), w.laz.id, NOV_2) == []
