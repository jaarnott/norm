"""The four Ace files against golden text built from Orbit's exact formats
(``PayrunReport.tsx``; report 01 §3.2) — byte-equal, CRLF — and the
refusals: no close snapshot, a missing pay code, a leave type with no Ace
style, no allowance code. Plus the JavaScript rounding the files were
written with. The leave types are Loaded's, as ``get_leave_types`` returned
them live (23 Oct 2026).
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from app.services.finance_core.errors import Conflict, Invalid, NotFound
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


ANNUAL = "M:8298b9d2-98df-4b30-b735-1d604809d1c8"
UNPAID = "M:67dfdc54-be51-432d-9fec-70866117bbdb"
PPH = "C:49fa8896-4f17-483c-6501-08dee13fbf1d"
#: get_leave_types as Loaded answered for Bessie & Royals (trimmed): its own
#: managed types — the same ids in every company — and one custom type.
LEAVE_TYPES = [
    {
        "id": ANNUAL,
        "name": "New Zealand Annual Leave",
        "isPaid": True,
        "type": "Managed",
        "activation": {"activatedAt": "2025-10-01T00:00:00+00:00"},
    },
    {
        "id": "M:e55019cd-5b2f-4fc5-98fb-6f9d89fc8481",
        "name": "New Zealand Sick Leave",
        "isPaid": True,
        "type": "Managed",
        "activation": {"activatedAt": "2025-10-01T00:00:00+00:00"},
    },
    {
        "id": "M:5afee836-7aa8-4cc1-b0ac-640a015bddbe",
        "name": "New Zealand Bereavement Leave",
        "isPaid": True,
        "type": "Managed",
        "activation": {"activatedAt": "2025-10-01T00:00:00+00:00"},
    },
    {
        "id": UNPAID,
        "name": "Unpaid Leave",
        "isPaid": False,
        "percentage": None,
        "type": "Managed",
        "activation": {"activatedAt": "2025-10-01T00:00:00+00:00"},
    },
    {
        "id": PPH,
        "name": "Paid Public Holiday",
        "isPaid": True,
        "type": "Custom",
        "activation": {"activatedAt": "2026-01-01T00:00:00+00:00"},
    },
    {
        "id": "M:29d1ae2a-d458-420e-94f1-1cf75e3a8440",
        "name": "Australian Sick and Carer's Leave",
        "isPaid": True,
        "type": "Managed",
        "activation": None,
    },
]


def _close(w, monkeypatch, rows, venue=None, leave_types=LEAVE_TYPES):
    venue = venue or w.laz
    fake = install(
        monkeypatch,
        FakeLoaded(
            get_payrun=lambda v, p: payrun_result(v, MONDAY, rows),
            get_leave_types=leave_types,
        ),
    )
    snap = snapshot_svc.take(w.db, w.ctx(), venue.id, MONDAY, source="close")
    week = weeks_svc.get_or_create(w.db, w.ctx(), venue.id, MONDAY)
    week.fake = fake
    return week, snap


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
        leave_type="New Zealand Annual Leave",
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
        leave_type="New Zealand Annual Leave",
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
        leave_type="New Zealand Annual Leave",
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
        leave_type="New Zealand Annual Leave",
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
        leave_type="New Zealand Sick Leave",
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
        leave_type="New Zealand Bereavement Leave",
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
        leave_type="New Zealand Bereavement Leave",
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
        leave_type="New Zealand Sick Leave",
        start="2026-10-17T09:00:00+13:00",
        end="2026-10-17T09:00:00+13:00",
    ),
]


class TestImportLeaveTxt:
    def test_golden(self, w, monkeypatch):
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
        assert built["totals"]["unpaid_skipped"] == []
        assert built["totals"]["styles_set_in_norm"] == []
        assert built["refusals"] == []
        # Loaded's leave types were read once, for this venue.
        assert [c[1] for c in week.fake.calls].count("get_leave_types") == 1

    def test_unpaid_in_loaded_is_left_out_and_a_custom_type_refuses_by_name(
        self, w, monkeypatch
    ):
        rows = LEAVE_ROWS + [
            # Loaded marks Unpaid Leave isPaid=false: nothing to pay, no line —
            # Orbit sent it as #70.
            shift(
                "l8",
                "a",
                "Ana Lee",
                code="LZ001",
                shift_type="Leave",
                hours=8.0,
                cost_cents=0,
                leave_request_id="lr6",
                leave_type="Unpaid Leave",
            ),
            # A company's own type: Loaded gives it no payroll meaning.
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
            # Another company's type this one's Loaded doesn't list.
            shift(
                "l10",
                "b",
                "Ben Roy",
                code="LZ002",
                shift_type="Leave",
                hours=8.0,
                cost_cents=20000,
                leave_request_id="lr8",
                leave_type="Family Violence Leave",
            ),
        ]
        week, _ = _close(w, monkeypatch, rows)
        built = exports_svc.build(w.db, w.ctx(), week, "leave")
        assert built["refusals"] == [
            "Leave types with no Ace style — Loaded gives these no payroll meaning; "
            "set one under Payroll settings → Leave types: Family Violence Leave, "
            "Paid Public Holiday"
        ]
        assert built["totals"]["unpaid_skipped"] == ["Unpaid Leave"]
        with pytest.raises(Invalid, match="Paid Public Holiday"):
            exports_svc.generate(w.db, w.ctx(), week, kinds=["leave"])

    def test_a_style_set_for_a_custom_type_is_used_loadeds_own_are_not_overridable(
        self, w, monkeypatch
    ):
        rows = [
            LEAVE_ROWS[2],
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
        ctx = w.ctx()
        entry = exports_svc.set_leave_style(w.db, ctx, w.laz.id, PPH, ace_style="#62")
        assert (entry["ace_style"], entry["ace_style_from"], entry["kind"]) == (
            "62",
            "override",
            "custom",
        )
        assert entry["override"]["set_by"] == ctx.user_id
        built = exports_svc.build(w.db, ctx, week, "leave")
        # Any style but #70/#60/#63 is hours × rate.
        assert built["content"] == (
            "#62,LZ001,0,8.00,25.00" + CRLF + "#70,LZ005,0,1.000,240.00,,,," + CRLF
        )
        assert built["totals"]["styles_set_in_norm"] == ["Paid Public Holiday"]
        with pytest.raises(Invalid, match="comes from Loaded"):
            exports_svc.set_leave_style(w.db, ctx, w.laz.id, ANNUAL, ace_style="75")
        with pytest.raises(Invalid, match="unpaid"):
            exports_svc.set_leave_style(w.db, ctx, w.laz.id, UNPAID, ace_style="70")
        with pytest.raises(NotFound):
            exports_svc.set_leave_style(w.db, ctx, w.laz.id, "C:nope", ace_style="62")
        with pytest.raises(Invalid, match="style number"):
            exports_svc.set_leave_style(w.db, ctx, w.laz.id, PPH, ace_style="sixty")
        with pytest.raises(Conflict):
            exports_svc.set_leave_style(
                w.db, ctx, w.laz.id, PPH, ace_style="61", version=99
            )
        # Cleared: the type refuses again.
        exports_svc.set_leave_style(w.db, ctx, w.laz.id, PPH, ace_style=None)
        assert exports_svc.build(w.db, ctx, week, "leave")["refusals"]

    def test_the_settings_view_lists_loadeds_types_with_their_source(
        self, w, monkeypatch
    ):
        _close(w, monkeypatch, LEAVE_ROWS)
        types = exports_svc.leave_types(w.db, w.ctx(), w.laz.id)
        view = [
            (t["name"], t["ace_style"], t["ace_style_from"], t["is_paid"])
            for t in types
        ]
        assert view == [
            ("New Zealand Annual Leave", "70", "loaded", True),
            ("New Zealand Bereavement Leave", "63", "loaded", True),
            ("New Zealand Sick Leave", "60", "loaded", True),
            ("Unpaid Leave", None, None, False),
            ("Paid Public Holiday", None, None, True),
            # Loaded lists Australia's managed types to an NZ company too.
            ("Australian Sick and Carer's Leave", None, None, True),
        ]
        assert types[-1]["active"] is False and types[0]["active"] is True

    def test_a_failed_read_refuses_and_a_week_without_leave_never_reads(
        self, w, monkeypatch
    ):
        week, _ = _close(
            w,
            monkeypatch,
            LEAVE_ROWS,
            leave_types=Invalid("Loaded could not be read: 401"),
        )
        built = exports_svc.build(w.db, w.ctx(), week, "leave")
        assert built["refusals"] == [
            "Could not read the leave types from Loaded: Loaded could not be read: 401"
        ]
        quiet, _ = _close(w, monkeypatch, ROWS[:2])
        assert exports_svc.build(w.db, w.ctx(), quiet, "leave")["content"] == ""
        assert "get_leave_types" not in [c[1] for c in quiet.fake.calls]


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
            "No gratuity allowance code for this entity — set it in Payroll settings (the gear on the Payroll page)."
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
            leave_type="New Zealand Annual Leave",
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
            leave_type="New Zealand Annual Leave",
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
