"""The four Ace files against golden text built from Orbit's exact formats
(``PayrunReport.tsx``; report 01 §3.2) — byte-equal, CRLF — and the
refusals: no close snapshot, a missing pay code, an unmapped leave type, no
allowance code. Plus the JavaScript rounding the files were written with.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from app.db.payroll_models import PayLeaveStyle
from app.services.finance_core.errors import Invalid
from app.services.payroll import exports as exports_svc
from app.services.payroll import gratuities as gratuities_svc
from app.services.payroll import snapshot as snapshot_svc
from app.services.payroll import weeks as weeks_svc
from tests.payroll_world import (
    MONDAY,
    FakeLoaded,
    install,
    make_world,
    payrun_result,
    shift,
)

CRLF = "\r\n"


@pytest.fixture
def w(db_session):
    return make_world(db_session)


def _styles(w, venue, mapping):
    rows = []
    for type_id, (name, style) in mapping.items():
        salaried, hourly = exports_svc.default_methods(style)
        rows.append(
            PayLeaveStyle(
                organization_id=w.org.id,
                venue_id=venue.id,
                loaded_leave_type_id=type_id,
                name=name,
                ace_style=style,
                salaried_method=salaried,
                hourly_method=hourly,
            )
        )
    w.db.add_all(rows)
    w.db.flush()
    return rows


def _close(w, monkeypatch, rows, venue=None):
    venue = venue or w.laz
    install(
        monkeypatch, FakeLoaded(get_payrun=lambda v, p: payrun_result(v, MONDAY, rows))
    )
    snap = snapshot_svc.take(w.db, w.ctx(), venue.id, MONDAY, source="close")
    return weeks_svc.get_or_create(w.db, w.ctx(), venue.id, MONDAY), snap


class TestToFixed:
    def test_rounds_half_up_on_the_binary_value_like_javascript(self):
        assert (
            exports_svc.to_fixed(0.125) == "0.13"
        )  # JS "0.13"; Python's format gives "0.12"
        assert (
            exports_svc.to_fixed(1.005) == "1.00"
        )  # 1.005 is 1.00499… in binary: JS "1.00"
        assert exports_svc.to_fixed(2.675) == "2.67"
        assert exports_svc.to_fixed(31.5) == "31.50"
        assert exports_svc.to_fixed(2, 3) == "2.000"


ROWS = [
    # Zoe sorts last despite the lower-case z; two shifts aggregate on her code.
    shift("s1", "z", "zoe Adams", code="LZ010", hours=6.0, cost_cents=15000),
    shift(
        "s2",
        "z",
        "zoe Adams",
        code="LZ010",
        start="2026-10-14T17:00:00+13:00",
        hours=4.0,
        cost_cents=11000,
    ),
    # Ana: a public-holiday premium is inside cost (10 h @ 25 + 50 PH premium) → rate 30.00.
    shift(
        "s3",
        "a",
        "Ana Lee",
        code="LZ001",
        hours=10.0,
        cost_cents=30000,
        ph_hours=10.0,
        ph_cost_cents=5000,
    ),
    # Ben: a cost that divides to a half-cent — 3 h for $100.005 does not exist in cents; use 7 h / $233.33 → 33.332857
    shift("s4", "b", "Ben Roy", code="LZ002", hours=7.0, cost_cents=23333),
    # Security is excluded from IMPORT.TXT.
    shift("s5", "s", "Sam Guard", code="SECURITY", hours=8.0, cost_cents=32000),
    # Leave rows never land in IMPORT.TXT.
    shift(
        "s6",
        "a",
        "Ana Lee",
        code="LZ001",
        shift_type="Leave",
        hours=8.0,
        cost_cents=24000,
        leave_request_id="lr1",
        leave_type="NZ Annual",
    ),
]


class TestImportTxt:
    def test_golden(self, w, monkeypatch):
        week, _ = _close(w, monkeypatch, ROWS)
        built = exports_svc.build(w.db, w.ctx(), week, "import")
        assert built["content"] == (
            "#52,LZ001,50001,10.00,30.00"
            + CRLF
            + "#52,LZ002,50001,7.00,33.33"
            + CRLF
            + "#52,LZ010,50001,10.00,26.00"
            + CRLF
        )
        assert built["totals"] == {
            "lines": 3,
            "pay_codes": 3,
            "hours": 27.0,
            "cost_cents": 79333,
        }
        assert built["refusals"] == []

    def test_the_entity_pay_type_code_is_a_setting(self, w, monkeypatch):
        from app.services.payroll import entities as entities_svc

        entity = entities_svc.get_or_create(w.db, w.ctx(), w.laz.id)
        entities_svc.update_settings(
            w.db, w.ctx(), w.laz.id, version=entity.version, ace_pay_type_code="50002"
        )
        week, _ = _close(w, monkeypatch, ROWS[:1])
        assert (
            exports_svc.build(w.db, w.ctx(), week, "import")["content"]
            == "#52,LZ010,50002,6.00,25.00" + CRLF
        )

    def test_a_missing_pay_code_refuses_with_the_name(self, w, monkeypatch):
        rows = ROWS + [
            shift("s7", "n", "No Code", code=None, hours=2.0, cost_cents=5000)
        ]
        week, _ = _close(w, monkeypatch, rows)
        built = exports_svc.build(w.db, w.ctx(), week, "import")
        assert built["refusals"] == ["Staff without a pay code in Loaded: No Code"]
        with pytest.raises(Invalid, match="No Code"):
            exports_svc.generate(w.db, w.ctx(), week, kinds=["import"])
        assert weeks_svc.exports_of(w.db, week) == []

    def test_no_close_snapshot_refuses(self, w, monkeypatch):
        install(
            monkeypatch,
            FakeLoaded(get_payrun=lambda v, p: payrun_result(v, MONDAY, ROWS)),
        )
        snapshot_svc.take(w.db, w.ctx(), w.laz.id, MONDAY, source="monday")
        week = weeks_svc.get_or_create(w.db, w.ctx(), w.laz.id, MONDAY)
        with pytest.raises(Invalid, match="close snapshot"):
            exports_svc.build(w.db, w.ctx(), week, "import")


LEAVE_ROWS = [
    # Hourly annual: two days (two local dates), 16 h, $400 → #70 Percent of Gross.
    shift(
        "l1",
        "a",
        "Ana Lee",
        code="LZ001",
        shift_type="Leave",
        hours=8.0,
        cost_cents=20000,
        leave_request_id="lr1",
        leave_type="NZ Annual",
        start="2026-10-13T09:00:00+13:00",
        end="2026-10-13T17:00:00+13:00",
    ),
    shift(
        "l2",
        "a",
        "Ana Lee",
        code="LZ001",
        shift_type="Leave",
        hours=8.0,
        cost_cents=20000,
        leave_request_id="lr1",
        leave_type="NZ Annual",
        start="2026-10-14T09:00:00+13:00",
        end="2026-10-14T17:00:00+13:00",
    ),
    # Salaried annual: one day, $240 → #70 Annual Leave method (daily rate).
    shift(
        "l3",
        "m",
        "Mia Salar",
        code="LZ005",
        shift_type="Leave",
        remuneration="Salary",
        hours=8.0,
        cost_cents=24000,
        leave_request_id="lr2",
        leave_type="NZ Annual",
        start="2026-10-15T09:00:00+13:00",
        end="2026-10-15T17:00:00+13:00",
    ),
    # Hourly sick: 6 h, $150 → #60 paid + entitlement reduction.
    shift(
        "l4",
        "b",
        "Ben Roy",
        code="LZ002",
        shift_type="Leave",
        hours=6.0,
        cost_cents=15000,
        leave_request_id="lr3",
        leave_type="NZ Sick",
        start="2026-10-16T09:00:00+13:00",
        end="2026-10-16T15:00:00+13:00",
    ),
    # Salaried bereavement: 2 days → #63 days recorded only.
    shift(
        "l5",
        "m",
        "Mia Salar",
        code="LZ005",
        shift_type="Leave",
        remuneration="Salary",
        hours=8.0,
        cost_cents=24000,
        leave_request_id="lr4",
        leave_type="NZ Bereavement",
        start="2026-10-16T09:00:00+13:00",
        end="2026-10-16T17:00:00+13:00",
    ),
    shift(
        "l6",
        "m",
        "Mia Salar",
        code="LZ005",
        shift_type="Leave",
        remuneration="Salary",
        hours=8.0,
        cost_cents=24000,
        leave_request_id="lr4",
        leave_type="NZ Bereavement",
        start="2026-10-17T09:00:00+13:00",
        end="2026-10-17T17:00:00+13:00",
    ),
    # A zero-hour leave row is skipped, not refused.
    shift(
        "l7",
        "b",
        "Ben Roy",
        code="LZ002",
        shift_type="Leave",
        hours=0.0,
        cost_cents=0,
        leave_request_id="lr5",
        leave_type="NZ Sick",
        start="2026-10-17T09:00:00+13:00",
        end="2026-10-17T09:00:00+13:00",
    ),
]
STYLE_MAP = {
    "M:1": ("NZ Annual", "70"),
    "M:2": ("NZ Sick", "60"),
    "M:3": ("NZ Bereavement", "63"),
    "M:4": ("Unpaid", None),
}


class TestImportLeaveTxt:
    def test_golden(self, w, monkeypatch):
        _styles(w, w.laz, STYLE_MAP)
        week, _ = _close(w, monkeypatch, LEAVE_ROWS)
        built = exports_svc.build(w.db, w.ctx(), week, "leave")
        assert built["content"] == (
            "#70,LZ001,0,2.000,,,,16.00,25.00"
            + CRLF
            + "#60,LZ002,0,6.00,25.00,,1.000,"
            + CRLF
            + "#63,LZ005,0,,,,2.000,"
            + CRLF
            + "#70,LZ005,0,1.000,240.00,,,,"
            + CRLF
        )
        assert built["totals"]["by_style"] == {"70": 2, "60": 1, "63": 1}
        assert built["totals"]["skipped_zero_hours"] == ["Ben Roy"]
        assert built["totals"]["unsigned_styles"] == [
            "NZ Annual",
            "NZ Sick",
            "NZ Bereavement",
        ]
        assert built["refusals"] == []

    def test_an_unmapped_type_refuses_and_lists_it(self, w, monkeypatch):
        _styles(w, w.laz, STYLE_MAP)
        rows = LEAVE_ROWS + [
            shift(
                "l8",
                "a",
                "Ana Lee",
                code="LZ001",
                shift_type="Leave",
                hours=8.0,
                cost_cents=0,
                leave_request_id="lr6",
                leave_type="Unpaid",
            ),
            shift(
                "l9",
                "a",
                "Ana Lee",
                code="LZ001",
                shift_type="Leave",
                hours=8.0,
                cost_cents=20000,
                leave_request_id="lr7",
                leave_type="Paid Public Holiday",
            ),
        ]
        week, _ = _close(w, monkeypatch, rows)
        built = exports_svc.build(w.db, w.ctx(), week, "leave")
        assert built["refusals"] == [
            "Leave types with no Ace style on the Payroll settings page: Paid Public Holiday, Unpaid"
        ]
        with pytest.raises(Invalid, match="Paid Public Holiday"):
            exports_svc.generate(w.db, w.ctx(), week, kinds=["leave"])

    def test_the_style_number_and_method_are_the_rows(self, w, monkeypatch):
        rows = _styles(w, w.laz, {"M:1": ("NZ Annual", "70")})
        ctx = w.ctx()
        # Payroll decides annual leave for salaried staff is written the hourly way.
        exports_svc.update_leave_style(
            w.db, ctx, rows[0].id, version=1, salaried_method="days_hours_rate"
        )
        week, _ = _close(w, monkeypatch, [LEAVE_ROWS[2]])
        assert (
            exports_svc.build(w.db, ctx, week, "leave")["content"]
            == "#70,LZ005,0,1.000,,,,8.00,30.00" + CRLF
        )
        exports_svc.update_leave_style(w.db, ctx, rows[0].id, version=2, ace_style="75")
        assert (
            exports_svc.build(w.db, ctx, week, "leave")["content"]
            == "#75,LZ005,0,8.00,30.00" + CRLF
        )

    def test_seed_from_loaded_prefills_orbits_mapping_unsigned(self, w, monkeypatch):
        install(
            monkeypatch,
            FakeLoaded(
                get_leave_types=[
                    {"id": "M:1", "name": "NZ Annual", "isPaid": True},
                    {"id": "M:2", "name": "NZ Sick", "isPaid": True},
                    {"id": "M:9", "name": "Family Violence", "isPaid": True},
                    {"id": "M:8", "name": "Gone", "deletedAt": "2025-01-01"},
                ]
            ),
        )
        out = exports_svc.seed_leave_styles(w.db, w.ctx(), w.laz.id)
        assert out["created"] == 3
        by_name = {s.name: s for s in out["styles"]}
        assert (
            by_name["NZ Annual"].ace_style == "70"
            and by_name["NZ Annual"].hourly_method == "days_hours_rate"
        )
        assert by_name["NZ Sick"].ace_style == "60"
        assert by_name["Family Violence"].ace_style is None  # never defaulted to #70
        assert all(s.signed_off_at is None for s in out["styles"])
        assert exports_svc.seed_leave_styles(w.db, w.ctx(), w.laz.id)["created"] == 0


class TestGratuitiesTxt:
    def _batch(self, w, monkeypatch, allowance="GRAT"):
        from app.services.payroll import entities as entities_svc

        entity = entities_svc.get_or_create(w.db, w.ctx(), w.laz.id)
        if allowance:
            entities_svc.update_settings(
                w.db,
                w.ctx(),
                w.laz.id,
                version=entity.version,
                ace_gratuity_allowance_code=allowance,
            )
        rows = [
            shift(
                "g1",
                "a",
                "Ana Lee",
                code="LZ001",
                hours=10.0,
                cost_cents=30000,
                start="2026-10-13T17:00:00+13:00",
            ),
            shift(
                "g2",
                "z",
                "zoe Adams",
                code="LZ010",
                hours=5.0,
                cost_cents=12000,
                start="2026-10-14T17:00:00+13:00",
            ),
            shift(
                "g3",
                "s",
                "Sam Guard",
                code="SECURITY",
                hours=8.0,
                cost_cents=32000,
                start="2026-10-15T17:00:00+13:00",
            ),
        ]
        week, _ = _close(w, monkeypatch, rows)
        install(
            monkeypatch,
            FakeLoaded(
                get_payrun=lambda v, p: payrun_result(v, MONDAY, rows),
                get_staff_members=[],
            ),
        )
        batch = gratuities_svc.create_batch(
            w.db,
            w.ctx(),
            venue_id=w.laz.id,
            period="2026-10",
            week_start=MONDAY,
            total_cents=30000,
        )
        return week, batch

    def test_golden(self, w, monkeypatch):
        week, batch = self._batch(w, monkeypatch)
        built = exports_svc.build(w.db, w.ctx(), week, "gratuities")
        # 300.00 split 10 h : 5 h → 200.00 / 100.00; security never shares.
        assert (
            built["content"]
            == "#51,LZ001,GRAT,200.00" + CRLF + "#51,LZ010,GRAT,100.00" + CRLF
        )
        assert built["totals"] == {"lines": 2, "amount_cents": 30000, "shares": 2}

    def test_no_allowance_code_refuses(self, w, monkeypatch):
        week, _ = self._batch(w, monkeypatch, allowance=None)
        built = exports_svc.build(w.db, w.ctx(), week, "gratuities")
        assert built["refusals"] == [
            "No gratuity allowance code for this entity — set it on the Payroll settings page."
        ]

    def test_a_rehearsal_batch_never_feeds_the_file(self, w, monkeypatch):
        week, batch = self._batch(w, monkeypatch)
        batch.rehearsal = True
        w.db.flush()
        assert exports_svc.build(w.db, w.ctx(), week, "gratuities")["content"] == ""


class TestEntertainersTxt:
    def test_golden_from_f5_lines_and_empty_when_absent(self, w, monkeypatch):
        week, _ = _close(w, monkeypatch, ROWS[:1])
        assert exports_svc.build(w.db, w.ctx(), week, "entertainers")["content"] == ""
        lines = [
            SimpleNamespace(
                performance_id="p1",
                pay_code="ENT01",
                hours=3.0,
                rate_cents=10000,
                amount_cents=30000,
            ),
            SimpleNamespace(
                performance_id="p2",
                pay_code="ENT01",
                hours=2.0,
                rate_cents=10000,
                amount_cents=20000,
            ),
            SimpleNamespace(
                performance_id="p3",
                pay_code="ENT02",
                hours=2.5,
                rate_cents=12000,
                amount_cents=None,
            ),
        ]
        monkeypatch.setattr(
            exports_svc,
            "entertainer_items",
            lambda db, ctx, venue_id, week_start: (lines, True),
        )
        built = exports_svc.build(w.db, w.ctx(), week, "entertainers")
        assert (
            built["content"]
            == "#52,ENT01,50001,5.00,100.00"
            + CRLF
            + "#52,ENT02,50001,2.50,120.00"
            + CRLF
        )
        assert built["totals"]["performance_ids"] == ["p1", "p2", "p3"]


class TestGenerateAndStore:
    def test_stores_content_and_a_re_export_diffs(self, w, monkeypatch):
        _styles(w, w.laz, STYLE_MAP)
        week, snap = _close(w, monkeypatch, ROWS + LEAVE_ROWS[:1])
        out = exports_svc.generate(
            w.db, w.ctx(), week, kinds=["import", "leave", "gratuities", "entertainers"]
        )
        kinds = [e.kind for e in out["exports"]]
        assert kinds == ["import", "leave", "gratuities", "entertainers"]
        assert all(
            e.snapshot_id == snap.id and len(e.content_sha256) == 64
            for e in out["exports"]
        )
        assert out["diffs"] == {
            "import": None,
            "leave": None,
            "gratuities": None,
            "entertainers": None,
        }
        # Loaded moved: a retaken close and a re-export show the change line by line.
        rows2 = (
            [{**ROWS[0], "hours": 7.0, "cost_cents": 17500}] + ROWS[1:] + LEAVE_ROWS[:1]
        )
        _close(w, monkeypatch, rows2)
        again = exports_svc.generate(w.db, w.ctx(), week, kinds=["import"])
        assert again["diffs"]["import"]["removed"] == ["#52,LZ010,50001,10.00,26.00"]
        assert again["diffs"]["import"]["added"] == ["#52,LZ010,50001,11.00,25.91"]
        assert weeks_svc.status_of(w.db, week) == "exported"

    def test_fallback_pack_has_the_four_files_and_the_rekey_sheet(self, w, monkeypatch):
        _styles(w, w.laz, STYLE_MAP)
        week, _ = _close(w, monkeypatch, ROWS[:1])
        from app.services.payroll import inputs as inputs_svc

        inputs_svc.set_ace_inputs(
            w.db,
            w.ctx(),
            week,
            gross_cents=100000,
            net_cents=80000,
            esct_cents=1000,
            kiwisaver_cents=3000,
        )
        inputs_svc.add_adjustment(
            w.db,
            w.ctx(),
            week,
            label="PH top-up",
            amount_cents=-2500,
            report_target="wages",
        )
        pack = exports_svc.fallback_pack(w.db, w.ctx(), week)
        assert set(pack["files"]) == {
            "IMPORT.TXT",
            "IMPORT_LEAVE.TXT",
            "GRATUITIES.TXT",
            "IMPORT_ENTERTAINERS.TXT",
            "REKEY.csv",
        }
        assert pack["files"]["IMPORT.TXT"] == "#52,LZ010,50001,6.00,25.00" + CRLF
        rekey = pack["files"]["REKEY.csv"].split(CRLF)
        assert rekey[0].startswith("kind,id,")
        assert any(line.startswith("ace_inputs,") for line in rekey)
        assert any(
            line.startswith("adjustment,") and "PH top-up" in line for line in rekey
        )

    def test_generate_needs_payroll_write(self, w, monkeypatch):
        from app.services.finance_core.errors import Forbidden

        week, _ = _close(w, monkeypatch, ROWS[:1])
        with pytest.raises(Forbidden):
            exports_svc.generate(
                w.db,
                w.ctx(w.owner).__class__(
                    **{**w.ctx(w.owner).__dict__, "scopes": frozenset({"payroll:read"})}
                ),
                week,
            )


def test_leave_days_count_local_dates_not_shifts(w, monkeypatch):
    """Two leave rows on the same local date count as one day (Orbit: no hours/8)."""
    _styles(w, w.laz, STYLE_MAP)
    rows = [
        shift(
            "l1",
            "a",
            "Ana Lee",
            code="LZ001",
            shift_type="Leave",
            hours=4.0,
            cost_cents=10000,
            leave_request_id="lr1",
            leave_type="NZ Annual",
            start="2026-10-13T09:00:00+13:00",
            end="2026-10-13T13:00:00+13:00",
        ),
        shift(
            "l2",
            "a",
            "Ana Lee",
            code="LZ001",
            shift_type="Leave",
            hours=4.0,
            cost_cents=10000,
            leave_request_id="lr1",
            leave_type="NZ Annual",
            start="2026-10-13T14:00:00+13:00",
            end="2026-10-13T18:00:00+13:00",
        ),
    ]
    week, _ = _close(w, monkeypatch, rows)
    assert (
        exports_svc.build(w.db, w.ctx(), week, "leave")["content"]
        == "#70,LZ001,0,1.000,,,,8.00,25.00" + CRLF
    )
    assert dt.date.fromisoformat(rows[0]["trading_date"]) == dt.date(2026, 10, 13)
