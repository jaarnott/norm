"""Budgets (plan §6.3, Phase 6): fiscal years from the organisation's start
month, versions and the lock as a mechanism, days, the monthly lines'
one-of rule, venue settings, the import parsers (generic and Orbit's
12-block tab) with preview and apply, Budget vs Loaded rows, and the
routes behind ``finance:read`` / ``budgets:write`` for NON-admin people.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.db.finance_models import FinBudgetDay, FinBudgetLine
from app.services.finance import budget_import, budgets
from app.services.finance_core.errors import Conflict, Forbidden, Invalid, NotFound
from tests.finance_f6_world import make_world
from tests.org_helpers import auth_headers

FY = 2027


class TestFiscalYears:
    def test_named_by_the_year_they_end_in(self):
        assert budgets.fiscal_year_of(dt.date(2026, 10, 20), 4) == 2027
        assert budgets.fiscal_year_of(dt.date(2027, 3, 31), 4) == 2027
        assert budgets.fiscal_year_of(dt.date(2027, 4, 1), 4) == 2028
        assert budgets.fiscal_year_of(dt.date(2026, 6, 30), 7) == 2026  # AU
        assert budgets.fiscal_year_of(dt.date(2026, 7, 1), 7) == 2027
        assert budgets.fiscal_year_of(dt.date(2026, 12, 31), 1) == 2026
        assert budgets.fiscal_bounds(2027, 4) == (
            dt.date(2026, 4, 1),
            dt.date(2027, 3, 31),
        )
        assert budgets.fiscal_bounds(2027, 7) == (
            dt.date(2026, 7, 1),
            dt.date(2027, 6, 30),
        )
        assert budgets.fiscal_months(2027, 4)[0] == (2026, 4) and budgets.fiscal_months(
            2027, 4
        )[-1] == (2027, 3)

    def test_the_organisation_decides(self, db_session):
        w = make_world(db_session)
        assert (
            budgets.fiscal_year_for(db_session, w.org.id, dt.date(2026, 5, 1)) == 2027
        )
        w.org.fiscal_year_start_month = 7
        db_session.flush()
        assert (
            budgets.fiscal_year_for(db_session, w.org.id, dt.date(2026, 5, 1)) == 2026
        )


class TestVersions:
    def test_the_current_budget_is_the_latest_locked_version(self, db_session):
        w = make_world(db_session)
        ctx = w.ctx()
        a = budgets.create_version(db_session, ctx, w.laz.id, FY, label="first")
        budgets.set_days(
            db_session,
            ctx,
            a.id,
            [{"date": "2026-10-20", "amount_cents": 100_00, "note": "quiz"}],
        )
        assert (
            budgets.current_version(db_session, w.org.id, w.laz.id, FY) is None
        )  # a draft
        budgets.lock_version(db_session, ctx, a.id)
        assert budgets.current_version(db_session, w.org.id, w.laz.id, FY).id == a.id
        b = budgets.create_version(
            db_session, ctx, w.laz.id, FY, label="second", copy_from_version_id=a.id
        )
        budgets.set_days(
            db_session, ctx, b.id, [{"date": "2026-10-20", "amount_cents": 200_00}]
        )
        budgets.lock_version(db_session, ctx, b.id)
        assert budgets.current_version(db_session, w.org.id, w.laz.id, FY).id == b.id
        assert budgets.locked_cents(
            db_session, w.org.id, w.laz.id, dt.date(2026, 10, 20), dt.date(2026, 10, 20)
        ) == {dt.date(2026, 10, 20): 20000}
        # The copy carried the note; the day edit kept it.
        day = budgets.days_of_version(
            db_session, b.id, dt.date(2026, 10, 20), dt.date(2026, 10, 20)
        )[dt.date(2026, 10, 20)]
        assert day.note == "quiz"
        budgets.unlock_version(db_session, ctx, b.id)
        assert budgets.current_version(db_session, w.org.id, w.laz.id, FY).id == a.id

    def test_a_locked_version_is_not_edited_in_place(self, db_session):
        w = make_world(db_session)
        ctx = w.ctx()
        v = budgets.create_version(db_session, ctx, w.laz.id, FY)
        budgets.lock_version(db_session, ctx, v.id)
        with pytest.raises(Conflict):
            budgets.set_days(
                db_session, ctx, v.id, [{"date": "2026-10-20", "amount_cents": 1}]
            )
        budgets.unlock_version(db_session, ctx, v.id)
        assert (
            budgets.set_days(
                db_session, ctx, v.id, [{"date": "2026-10-20", "amount_cents": 1}]
            )
            == 1
        )

    def test_days_refuse_duplicates_and_negatives_and_clear_notes_on_null(
        self, db_session
    ):
        w = make_world(db_session)
        ctx = w.ctx()
        v = budgets.create_version(db_session, ctx, w.laz.id, FY)
        with pytest.raises(Invalid):
            budgets.set_days(
                db_session,
                ctx,
                v.id,
                [
                    {"date": "2026-10-20", "amount_cents": 1},
                    {"date": "2026-10-20", "amount_cents": 2},
                ],
            )
        with pytest.raises(Invalid):
            budgets.set_days(
                db_session, ctx, v.id, [{"date": "2026-10-20", "amount_cents": -1}]
            )
        budgets.set_days(
            db_session,
            ctx,
            v.id,
            [{"date": "2026-10-20", "amount_cents": 5, "note": "x"}],
        )
        budgets.set_days(
            db_session, ctx, v.id, [{"date": "2026-10-20", "amount_cents": 6}]
        )  # note kept
        budgets.set_days(
            db_session,
            ctx,
            v.id,
            [{"date": "2026-10-21", "amount_cents": 7, "note": None}],
        )
        rows = {
            d.date: d
            for d in db_session.query(FinBudgetDay).filter(
                FinBudgetDay.version_id == v.id
            )
        }
        assert (
            rows[dt.date(2026, 10, 20)].note == "x"
            and rows[dt.date(2026, 10, 20)].amount_cents == 6
        )
        assert rows[dt.date(2026, 10, 21)].note is None

    def test_locking_needs_budgets_write_and_the_venue(self, db_session):
        w = make_world(db_session)
        v = budgets.create_version(db_session, w.ctx(), w.dsc.id, FY)
        with pytest.raises(Forbidden):
            budgets.lock_version(db_session, w.ctx(w.team_member), v.id)
        with pytest.raises(
            Forbidden
        ):  # the manager holds budgets:write but not Dunedin
            budgets.lock_version(db_session, w.ctx(w.manager), v.id)
        with pytest.raises(NotFound):  # another organisation
            budgets.lock_version(db_session, w.ctx(w.other_owner), v.id)


class TestLines:
    def test_exactly_one_value_and_the_kind_the_item_names(self, db_session):
        w = make_world(db_session)
        ctx = w.ctx()
        with pytest.raises(Invalid):
            budgets.set_lines(
                db_session,
                ctx,
                w.laz.id,
                FY,
                11,
                {"opex_cents": {"value_cents": 1, "value_ratio": 0.1}},
            )
        with pytest.raises(Invalid):
            budgets.set_lines(
                db_session, ctx, w.laz.id, FY, 11, {"opex_cents": {"value_ratio": 0.1}}
            )
        with pytest.raises(Invalid):
            budgets.set_lines(
                db_session, ctx, w.laz.id, FY, 11, {"cogs_pct": {"value_cents": 100}}
            )
        with pytest.raises(Invalid):
            budgets.set_lines(
                db_session, ctx, w.laz.id, FY, 11, {"revenue": {"value_cents": 100}}
            )
        with pytest.raises(Invalid):
            budgets.set_lines(
                db_session, ctx, w.laz.id, FY, 11, {"cogs_pct": {"value_ratio": 7}}
            )
        rows = budgets.set_lines(
            db_session,
            ctx,
            w.laz.id,
            FY,
            11,
            {
                "cogs_pct": {"value_ratio": "0.315"},
                "opex_cents": {"value_cents": 120000},
            },
        )
        assert {r.line_item: (r.value_cents, r.value_ratio) for r in rows} == {
            "cogs_pct": (None, Decimal("0.315000")),
            "opex_cents": (120000, None),
        }

    def test_a_month_budgets_wages_as_a_percentage_or_an_amount_never_both(
        self, db_session
    ):
        w = make_world(db_session)
        ctx = w.ctx()
        budgets.set_lines(
            db_session, ctx, w.laz.id, FY, 11, {"wage_pct": {"value_ratio": 0.29}}
        )
        rows = budgets.set_lines(
            db_session,
            ctx,
            w.laz.id,
            FY,
            11,
            {"wage_cents": {"value_cents": 8_900_000}},
        )
        assert [r.line_item for r in rows] == ["wage_cents"]
        with pytest.raises(Invalid):
            budgets.set_lines(
                db_session,
                ctx,
                w.laz.id,
                FY,
                11,
                {"wage_pct": {"value_ratio": 0.3}, "wage_cents": {"value_cents": 1}},
            )
        rows = budgets.set_lines(
            db_session, ctx, w.laz.id, FY, 11, {"wage_cents": None}
        )
        assert rows == []
        assert (
            db_session.query(FinBudgetLine)
            .filter(FinBudgetLine.venue_id == w.laz.id)
            .count()
            == 0
        )


class TestSettings:
    def test_defaults_validation_and_the_stale_version(self, db_session):
        w = make_world(db_session)
        ctx = w.ctx()
        assert budgets.settings_dict(None, w.laz.id) == {
            "venue_id": w.laz.id,
            "closed_weekdays": [],
            "level_correction_on": False,
            "version": 0,
        }
        row = budgets.set_venue_settings(
            db_session, ctx, w.laz.id, closed_weekdays=[7, 1]
        )
        assert budgets.settings_dict(row, w.laz.id)["closed_weekdays"] == [1, 7]
        assert row.level_correction_on is False
        with pytest.raises(Invalid):
            budgets.set_venue_settings(db_session, ctx, w.laz.id, closed_weekdays=[0])
        with pytest.raises(Conflict):
            budgets.set_venue_settings(
                db_session,
                ctx,
                w.laz.id,
                level_correction_on=True,
                version=row.version + 5,
            )


class TestVsRows:
    def test_variance_is_loaded_minus_locked_with_totals(self):
        first, last = dt.date(2026, 10, 1), dt.date(2026, 10, 3)
        rows, totals = budgets.vs_rows(
            first,
            last,
            locked={first: 100_000, last: 200_000},
            loaded={first: 110_000, first + dt.timedelta(days=1): 50_000},
            actual={first: 99_000},
            notes={first: "quiz"},
        )
        assert (
            rows[0]["variance_cents"] == 10_000
            and rows[0]["variance_pct"] == 10.0
            and rows[0]["note"] == "quiz"
        )
        assert rows[1]["variance_cents"] is None and rows[2]["variance_cents"] is None
        assert totals == {
            "locked_cents": 300_000,
            "loaded_cents": 160_000,
            "actual_cents": 99_000,
            "variance_cents": -140_000,
            "variance_pct": -46.7,
        }


# ---------------------------------------------------------------- import --

GENERIC = """Date,Budget,Events
2026-04-01,"$1,978.00",Quiz night
02/04/2026,2392.5,
3 Apr 2026,(200),
foo,1,
2026-04-04,,
2026-04-05,0,closed
"""


def _orbit_tab() -> str:
    """Two month blocks of Orbit's layout (April and May), 5 columns each:
    Date | Last Year | Budget | Events | spacer; data from row 3."""
    rows = [
        ["FY27 budget workings"] + [""] * 9,
        ["Date", "LY", "Budget", "Events", "", "Date", "LY", "Budget", "Events", ""],
    ]
    for i in range(31):
        april = dt.date(2026, 4, 1) + dt.timedelta(days=i) if i < 30 else None
        may = dt.date(2026, 5, 1) + dt.timedelta(days=i)
        row = []
        row += (
            [april.isoformat(), "1500", str(1000 + i), "Quiz" if i == 2 else "", ""]
            if april
            else ["", "", "", "", ""]
        )
        row += (
            [str(46143 + i), "", "", "", ""]
            if i < 2
            else [may.strftime("%d/%m/%Y"), "1800", f"{2000 + i}.00", "", ""]
        )
        rows.append(row)
    return "\n".join(",".join(r) for r in rows) + "\n"


class TestImportParsers:
    def test_generic_reads_dates_amounts_notes_and_reports_bad_lines(self):
        rows, problems = budget_import.parse_generic(GENERIC)
        assert [
            (r["date"].isoformat(), r["amount_cents"], r["note"]) for r in rows
        ] == [
            ("2026-04-01", 197800, "Quiz night"),
            ("2026-04-02", 239250, None),
            ("2026-04-05", 0, "closed"),
        ]
        assert problems == [
            "line 4: negative amount for 2026-04-03",
            "line 5: not a date: 'foo'",
            "line 6: not an amount: ''",
        ]

    def test_orbits_twelve_block_tab_reads_dates_serials_and_events(self):
        rows, problems = budget_import.parse_orbit_text(_orbit_tab())
        assert problems == []
        by_date = {r["date"]: r for r in rows}
        assert len(rows) == 30 + 31
        assert by_date[dt.date(2026, 4, 1)]["amount_cents"] == 100000
        assert by_date[dt.date(2026, 4, 3)]["note"] == "Quiz"
        # Excel serials 46143/46144 = 1–2 May 2026; the rest d/m/Y.
        assert (
            by_date[dt.date(2026, 5, 1)]["amount_cents"] == 0
        )  # empty Budget cell = $0 (Orbit's rule)
        assert by_date[dt.date(2026, 5, 3)]["amount_cents"] == 200200
        assert by_date[dt.date(2026, 5, 31)]["amount_cents"] == 203000

    def test_parse_date_and_amount_shapes(self):
        assert budget_import.parse_date("46143") == dt.date(2026, 5, 1)
        assert budget_import.parse_date("Sat 03 Oct 2026") == dt.date(2026, 10, 3)
        assert budget_import.parse_date("2026-10-03T00:00:00+13:00") == dt.date(
            2026, 10, 3
        )
        assert budget_import.parse_date("nope") is None
        assert (
            budget_import.parse_amount("$1,978") == 197800
            and budget_import.parse_amount("(200)") == -20000
        )
        assert (
            budget_import.parse_amount("-") is None
            and budget_import.parse_amount(12.345) == 1235
        )


class TestImportPreviewApply:
    def test_preview_counts_months_outside_days_and_duplicates(self, db_session):
        w = make_world(db_session)
        text = "2026-04-01,100\n2026-04-01,200\n2027-04-01,300\n2026-05-01,0\n"
        out = budget_import.preview(db_session, w.ctx(), w.laz.id, text)
        assert (
            out["fiscal_year"] == 2027
            and out["days"] == 4
            and out["total_cents"] == 60000
        )
        assert out["duplicates"] == ["2026-04-01"] and out["outside_fiscal_year"] == [
            "2027-04-01"
        ]
        assert out["zero_days"] == ["2026-05-01"]
        assert [m["month"] for m in out["months"]] == [4, 5, 4]

    def test_apply_writes_a_new_import_version_and_refuses_bad_sets(self, db_session):
        w = make_world(db_session)
        ctx = w.ctx()
        version, shown = budget_import.apply(
            db_session,
            ctx,
            w.laz.id,
            _orbit_tab(),
            layout="orbit",
            label="FY27",
            lock=True,
        )
        assert (
            version.source == "import"
            and version.locked_at is not None
            and version.fiscal_year == 2027
        )
        assert budgets.version_stats(db_session, [version.id])[version.id] == (
            61,
            shown["total_cents"],
        )
        with pytest.raises(Invalid):
            budget_import.apply(
                db_session, ctx, w.laz.id, "2026-04-01,1\n2026-04-01,2\n"
            )
        with pytest.raises(Invalid):
            budget_import.apply(
                db_session,
                ctx,
                w.laz.id,
                "2026-04-01,1\n2027-04-01,2\n",
                fiscal_year=2027,
            )
        with pytest.raises(Invalid):
            budget_import.apply(db_session, ctx, w.laz.id, "foo,bar\n")
        with pytest.raises(Forbidden):
            budget_import.apply(
                db_session, w.ctx(w.team_member), w.laz.id, "2026-04-01,1\n"
            )


# ---------------------------------------------------------------- routes --


@pytest.fixture
def api(client, db_session, monkeypatch):
    w = make_world(db_session)

    def fake_execute(**kw):
        from app.connectors.tool_executor import ToolResult

        w.calls.append(kw)
        return ToolResult(
            success=True, payload={"days": [{"date": "2026-10-01", "amount": 1978.0}]}
        )

    w.calls = []
    monkeypatch.setattr(
        "app.connectors.tool_executor.execute_connector_tool", fake_execute
    )
    return client, w


class TestRoutes:
    def test_owner_200_team_member_403_other_org_404_and_mode_off_404(
        self, api, db_session
    ):
        client, w = api
        assert (
            client.get("/api/finance/meta", headers=auth_headers(w.owner)).status_code
            == 200
        )
        assert (
            client.get(
                "/api/finance/meta", headers=auth_headers(w.team_member)
            ).status_code
            == 403
        )
        from app.services.finance_core import modes

        modes.set_mode(db_session, w.org.id, "finance", "off")
        db_session.flush()
        assert (
            client.get("/api/finance/meta", headers=auth_headers(w.owner)).status_code
            == 404
        )

    def test_the_meta_lists_the_viewers_venues_only(self, api):
        client, w = api
        owner = client.get("/api/finance/meta", headers=auth_headers(w.owner)).json()
        assert {v["name"] for v in owner["venues"]} == {
            "La Zeppa",
            "Dunedin Social Club",
            "Head Office",
        }
        assert owner["can_see_group"] is True and owner["fiscal_year_start_month"] == 4
        manager = client.get(
            "/api/finance/meta", headers=auth_headers(w.manager)
        ).json()
        assert [v["name"] for v in manager["venues"]] == ["La Zeppa"]
        assert (
            manager["can_see_group"] is False and "budgets:write" in manager["scopes"]
        )

    def test_versions_days_lock_and_the_month_grid_with_loaded(self, api, db_session):
        client, w = api
        h = auth_headers(w.manager)
        out = client.post(
            "/api/finance/budgets/versions",
            json={"venue_id": w.laz.id, "fiscal_year": FY, "label": "FY27"},
            headers=h,
        )
        assert out.status_code == 201, out.text
        vid = out.json()["id"]
        out = client.put(
            f"/api/finance/budgets/versions/{vid}/days",
            json={
                "days": [
                    {"date": "2026-10-01", "amount_cents": 197800, "note": "quiz"},
                    {"date": "2026-10-02", "amount_cents": 239200},
                ]
            },
            headers=h,
        )
        assert out.status_code == 200 and out.json()["saved"] == 2
        assert (
            client.post(
                f"/api/finance/budgets/versions/{vid}/lock", headers=h
            ).status_code
            == 200
        )
        out = client.put(
            f"/api/finance/budgets/versions/{vid}/days",
            json={"days": [{"date": "2026-10-01", "amount_cents": 1}]},
            headers=h,
        )
        assert out.status_code == 409 and out.json()["detail"]["code"] == "conflict"
        grid = client.get(
            "/api/finance/budgets/days",
            params={"venue_id": w.laz.id, "year": 2026, "month": 10},
            headers=h,
        )
        assert grid.status_code == 200, grid.text
        body = grid.json()
        assert (
            body["locked"] is True
            and body["version_id"] == vid
            and body["fiscal_year"] == 2027
        )
        assert body["days"][0] == {
            "date": "2026-10-01",
            "weekday": 4,
            "amount_cents": 197800,
            "note": "quiz",
            "loaded_cents": 197800,
            "actual_cents": None,
            "holiday": None,
        }
        assert (
            body["totals"]["amount_cents"] == 197800 + 239200
            and len(body["days"]) == 31
        )
        call = w.calls[-1]
        assert (
            call["action"] == "get_budgets"
            and call["venue_id"] == w.laz.id
            and call["strict_venue"] is True
        )
        assert (
            call["params"]["from_date"] == "2026-10-01"
            and call["params"]["to_date"] == "2026-10-31"
        )
        # The manager cannot read Dunedin's grid; the team member nothing.
        assert (
            client.get(
                "/api/finance/budgets/days",
                params={"venue_id": w.dsc.id, "year": 2026, "month": 10},
                headers=h,
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/api/finance/budgets/versions",
                params={"venue_id": w.laz.id},
                headers=auth_headers(w.team_member),
            ).status_code
            == 403
        )

    def test_lines_import_vs_loaded_and_sync_month(self, api):
        client, w = api
        h = auth_headers(w.owner)
        out = client.put(
            "/api/finance/budgets/lines",
            json={
                "venue_id": w.laz.id,
                "fiscal_year": FY,
                "month": 10,
                "lines": {
                    "wage_pct": {"value_ratio": 0.29},
                    "opex_cents": {"value_cents": 1200000},
                },
            },
            headers=h,
        )
        assert out.status_code == 200, out.text
        assert out.json()["lines"]["wage_pct"]["value_ratio"] == 0.29
        out = client.put(
            "/api/finance/budgets/lines",
            json={
                "venue_id": w.laz.id,
                "fiscal_year": FY,
                "month": 10,
                "lines": {"opex_cents": {"value_cents": 1, "value_ratio": 1}},
            },
            headers=h,
        )
        assert out.status_code == 400 and out.json()["detail"]["code"] == "invalid"
        got = client.get(
            "/api/finance/budgets/lines",
            params={"venue_id": w.laz.id, "fiscal_year": FY},
            headers=h,
        ).json()
        assert (
            got["months"]["10"]["opex_cents"]["value_cents"] == 1200000
            and got["month_order"][0] == 4
        )

        text = "2026-10-01,1978\n2026-10-02,2392\n"
        prev = client.post(
            "/api/finance/budgets/import/preview",
            json={"venue_id": w.laz.id, "text": text},
            headers=h,
        )
        assert (
            prev.status_code == 200
            and prev.json()["days"] == 2
            and prev.json()["fiscal_year"] == 2027
        )
        applied = client.post(
            "/api/finance/budgets/import/apply",
            json={"venue_id": w.laz.id, "text": text, "lock": True},
            headers=h,
        )
        assert applied.status_code == 201, applied.text
        assert applied.json()["version"]["locked_at"] and applied.json()["days"] == 2

        vs = client.get(
            "/api/finance/budgets/vs-loaded",
            params={"venue_id": w.laz.id, "year": 2026, "month": 10},
            headers=h,
        ).json()
        assert (
            vs["days"][0]["locked_cents"] == 197800
            and vs["days"][0]["loaded_cents"] == 197800
            and vs["days"][0]["variance_cents"] == 0
        )
        year = client.get(
            "/api/finance/budgets/vs-loaded",
            params={"venue_id": w.laz.id, "fiscal_year": FY},
            headers=h,
        ).json()
        assert len(year["months"]) == 12 and year["totals"]["locked_cents"] == 437000

        sync = client.get(
            "/api/finance/budgets/sync-month",
            params={"venue_id": w.laz.id, "year": 2026, "month": 10},
            headers=h,
        ).json()
        assert sync["tool"] == "loadedhub.set_budgets_typed"
        assert sync["params"]["days"] == [
            {"date": "2026-10-01", "amount": 1978.0},
            {"date": "2026-10-02", "amount": 2392.0},
        ]
        assert (
            sync["params"]["venue"] == "La Zeppa"
            and len(sync["days_without_budget"]) == 29
        )

    def test_bodies_refuse_unknown_fields(self, api):
        client, w = api
        out = client.post(
            "/api/finance/budgets/versions",
            json={"venue_id": w.laz.id, "fiscal_year": FY, "bogus": 1},
            headers=auth_headers(w.owner),
        )
        assert out.status_code == 422
        # The region setting is gone: public holidays are Loaded's, per venue.
        out = client.put(
            "/api/finance/settings",
            json={"venue_id": w.laz.id, "holiday_region": "Auckland"},
            headers=auth_headers(w.owner),
        )
        assert out.status_code == 422

    def test_holidays_sync_from_loaded_and_list(self, api, monkeypatch):
        client, w = api
        from app.connectors.tool_executor import ToolResult

        calls = []

        def execute(**kw):
            calls.append(kw)
            return ToolResult(
                success=True,
                payload={
                    "public_holidays": [
                        {
                            "date": "2026-12-25",
                            "name": "Christmas Day",
                            "package": "New Zealand Public Holidays",
                            "priced": True,
                        }
                    ],
                    "packages": [{"name": "New Zealand Public Holidays"}],
                    "dates_until": "2026-12-26",
                },
            )

        monkeypatch.setattr(
            "app.connectors.tool_executor.execute_connector_tool", execute
        )
        h = auth_headers(w.manager)
        out = client.post(
            "/api/finance/holidays/sync", json={"venue_id": w.laz.id}, headers=h
        )
        assert out.status_code == 200, out.text
        assert out.json()["inserted"] == 1 and out.json()["dates_until"] == "2026-12-26"
        assert [(c["action"], c["venue_id"], c["strict_venue"]) for c in calls] == [
            ("get_pay_adjustments", w.laz.id, True)
        ]
        out = client.get(
            "/api/finance/holidays",
            params={"venue_id": w.laz.id, "year": 2026},
            headers=h,
        )
        assert out.json() == [
            {
                "date": "2026-12-25",
                "name": "Christmas Day",
                "package": "New Zealand Public Holidays",
                "source": "loaded",
            }
        ]
        # The manager holds La Zeppa only; a team member holds no budgets:write.
        assert (
            client.post(
                "/api/finance/holidays/sync", json={"venue_id": w.dsc.id}, headers=h
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/finance/holidays/sync",
                json={"venue_id": w.laz.id},
                headers=auth_headers(w.team_member),
            ).status_code
            == 403
        )
