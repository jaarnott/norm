"""``finance.nightly`` on the tick (plan §8, DESIGN-CONTRACT §6), the
holiday seed with the regional mapping (HTTP faked), and the actuals cache
read from ``get_sales`` on the trading calendar. The job never writes
Loaded: every tool call it makes is a read, asserted by name.
"""

from __future__ import annotations

import datetime as dt
import uuid
from zoneinfo import ZoneInfo

import pytest

from app.db.finance_models import (
    FinActualsDaily,
    FinForecastVintage,
    FinHoliday,
    FinJobRun,
    FinLoadedBudgetLog,
)
from app.db.models import Connection
from app.services.finance import actuals as actuals_svc
from app.services.finance import holidays, jobs
from app.services.finance_core.errors import Forbidden, Invalid
from tests.finance_f6_world import make_world, weekday_series

NZ = ZoneInfo("Pacific/Auckland")
START = dt.date(2026, 1, 5)
SLOT = dt.date(2026, 11, 16)  # the Monday that closed
NOW = dt.datetime(2026, 11, 17, 7, 31, tzinfo=NZ)


# ------------------------------------------------------------- holidays --

NAGER = [
    {
        "date": "2027-01-01",
        "localName": "New Year's Day",
        "name": "New Year's Day",
        "counties": None,
    },
    {
        "date": "2027-01-25",
        "localName": "Wellington Anniversary Day",
        "name": "Wellington Anniversary Day",
        "counties": ["NZ-WGN"],
    },
    {
        "date": "2027-02-01",
        "localName": "Auckland Anniversary Day",
        "name": "Auckland Anniversary Day",
        "counties": ["NZ-AUK", "NZ-NTL"],
    },
    {
        "date": "2027-03-22",
        "localName": "Otago Anniversary Day",
        "name": "Otago Anniversary Day",
        "counties": ["NZ-OTA"],
    },
    {"date": "bad", "localName": "Broken", "counties": None},
]


class TestHolidays:
    def test_national_rows_under_the_country_regional_under_each_mapped_region(self):
        rows = holidays.rows_for(NAGER, "NZ")
        assert ("NZ", dt.date(2027, 1, 1), "New Year's Day") in rows
        assert ("Auckland", dt.date(2027, 2, 1), "Auckland Anniversary Day") in rows
        assert ("Northland", dt.date(2027, 2, 1), "Auckland Anniversary Day") in rows
        assert ("Otago", dt.date(2027, 3, 22), "Otago Anniversary Day") in rows
        assert len(rows) == 5
        assert "New South Wales" in holidays.region_names(
            "AU"
        ) and "Otago" in holidays.region_names("NZ")

    def test_seed_is_idempotent_and_each_venue_reads_its_own_region(self, db_session):
        w = make_world(db_session)
        ctx = w.ctx()
        calls = []

        def fetch(year, country):
            calls.append((year, country))
            return NAGER

        out = holidays.seed_year(db_session, ctx, 2027, fetch=fetch)
        assert out == {
            "year": 2027,
            "country": "NZ",
            "inserted": 5,
            "unchanged": 0,
            "regions": ["Auckland", "NZ", "Northland", "Otago", "Wellington"],
        }
        assert holidays.seed_year(db_session, ctx, 2027, fetch=fetch)["inserted"] == 0
        assert calls == [(2027, "NZ"), (2027, "NZ")]
        assert (
            db_session.query(FinHoliday)
            .filter(FinHoliday.organization_id == w.org.id)
            .count()
            == 5
        )
        from app.services.finance import budgets

        budgets.set_venue_settings(db_session, ctx, w.laz.id, holiday_region="Auckland")
        budgets.set_venue_settings(db_session, ctx, w.dsc.id, holiday_region="Otago")
        lo, hi = dt.date(2027, 1, 1), dt.date(2027, 12, 31)
        assert holidays.for_venue(db_session, w.org.id, w.laz.id, lo, hi) == {
            dt.date(2027, 1, 1): "New Year's Day",
            dt.date(2027, 2, 1): "Auckland Anniversary Day",
        }
        assert holidays.for_venue(db_session, w.org.id, w.dsc.id, lo, hi) == {
            dt.date(2027, 1, 1): "New Year's Day",
            dt.date(2027, 3, 22): "Otago Anniversary Day",
        }
        assert holidays.for_venue(db_session, w.org.id, w.cbi.id, lo, hi) == {
            dt.date(2027, 1, 1): "New Year's Day"
        }
        # Another organisation sees none of it.
        assert (
            holidays.for_venue(db_session, w.other_org.id, w.other_venue.id, lo, hi)
            == {}
        )
        with pytest.raises(Forbidden):
            holidays.seed_year(db_session, w.ctx(w.team_member), 2027, fetch=fetch)
        with pytest.raises(Invalid):
            holidays.seed_year(db_session, ctx, 2027, country="XX", fetch=fetch)


# -------------------------------------------------------------- actuals --


def _sales_payload(window, tax, measure):
    first = dt.date.fromisoformat(window["start"][:10])
    last = dt.date.fromisoformat(window["end"][:10]) - dt.timedelta(days=1)
    rows = []
    cursor = first
    while cursor <= last:
        n = (cursor - START).days
        if measure == "orders":
            rows.append(
                {"venue": "La Zeppa", "date": cursor.isoformat(), "orders": 10 + n}
            )
        else:
            incl = 1000.0 + n
            rows.append(
                {
                    "venue": "La Zeppa",
                    "date": cursor.isoformat(),
                    "actual": incl if tax == "include" else round(incl / 1.15, 2),
                }
            )
        cursor += dt.timedelta(days=1)
    return {"window": window, "rows": rows}


def _budgets_payload(first: dt.date, last_excl: dt.date):
    rows = []
    cursor = first
    while cursor < last_excl:
        # Loaded files the budget FOR day D at D+1 00:00 local.
        stored = cursor + dt.timedelta(days=1)
        rows.append(
            {
                "id": f"b-{cursor.isoformat()}",
                "date": f"{stored.isoformat()}T00:00:00+13:00",
                "amount": 2000 + cursor.day,
                "salesTax": 0.15,
            }
        )
        cursor += dt.timedelta(days=1)
    return rows


@pytest.fixture
def loaded(monkeypatch):
    """A fake executor answering get_sales / get_budgets_raw and recording
    every call; any other action (a write) fails the test."""
    from app.connectors.tool_executor import ToolResult

    state = {"calls": [], "fail": set()}

    def execute(**kw):
        state["calls"].append(kw)
        action, params = kw["action"], kw["params"]
        if action in state["fail"]:
            return ToolResult(success=False, payload=None, error=f"{action} is down")
        if action == "get_sales":
            return ToolResult(
                success=True,
                payload=_sales_payload(
                    {"start": params["start"], "end": params["end"]},
                    params.get("tax", "include"),
                    params.get("measure", "sales"),
                ),
            )
        if action == "get_budgets_raw":
            return ToolResult(
                success=True,
                payload=_budgets_payload(
                    dt.date.fromisoformat(params["from_date"]),
                    dt.date.fromisoformat(params["to_date"]),
                ),
            )
        raise AssertionError(f"the nightly job must never call {action}")

    monkeypatch.setattr("app.connectors.tool_executor.execute_connector_tool", execute)
    return state


class TestActuals:
    def test_read_days_parses_the_three_feeds_on_the_trading_window(
        self, db_session, loaded
    ):
        w = make_world(db_session)
        rows = actuals_svc.read_days(
            db_session, db_session, w.laz, dt.date(2026, 11, 14), dt.date(2026, 11, 16)
        )
        assert [r["date"] for r in rows] == [
            dt.date(2026, 11, 14),
            dt.date(2026, 11, 15),
            dt.date(2026, 11, 16),
        ]
        n = (dt.date(2026, 11, 14) - START).days
        assert rows[0]["sales_incl_cents"] == int(round((1000 + n) * 100))
        assert rows[0]["sales_ex_cents"] == int(
            round(round((1000 + n) / 1.15, 2) * 100)
        )
        assert rows[0]["orders"] == 10 + n
        assert [c["params"].get("measure") for c in loaded["calls"]] == [
            "sales",
            "sales",
            "orders",
        ]
        assert all(
            c["params"]["start"] == "2026-11-14T07:00:00+13:00"
            and c["params"]["end"] == "2026-11-17T07:00:00+13:00"
            for c in loaded["calls"]
        )
        assert all(
            c["strict_venue"] and c["venue_id"] == w.laz.id for c in loaded["calls"]
        )

    def test_a_failed_sales_read_caches_nothing(self, db_session, loaded):
        w = make_world(db_session)
        loaded["fail"].add("get_sales")
        assert actuals_svc.refresh(db_session, db_session, w.laz, SLOT) is None
        assert db_session.query(FinActualsDaily).count() == 0

    def test_refresh_upserts_fourteen_days_and_history_loads_in_chunks(
        self, db_session, loaded
    ):
        w = make_world(db_session)
        assert actuals_svc.refresh(db_session, db_session, w.laz, SLOT) == 14
        assert actuals_svc.refresh(db_session, db_session, w.laz, SLOT) == 14
        assert db_session.query(FinActualsDaily).count() == 14
        series = actuals_svc.series(db_session, w.org.id, w.laz.id, SLOT, SLOT)
        assert series == {SLOT: int(round((1000 + (SLOT - START).days) * 100))}
        out = actuals_svc.load_history(
            db_session, db_session, w.laz, months=6, through=SLOT
        )
        assert out["failed_chunks"] == [] and out["written"] >= 180
        assert actuals_svc.coverage(db_session, w.org.id, w.laz.id)["days"] >= 180


# ------------------------------------------------------------------ job --


def _connect(db, venue):
    db.add(
        Connection(
            id=str(uuid.uuid4()),
            connector_name="loadedhub",
            venue_id=venue.id,
            config={"k": "x"},
            enabled="true",
        )
    )
    db.flush()


class TestNightly:
    def test_due_venues_are_trading_connected_and_in_a_shadow_or_live_org(
        self, db_session
    ):
        w = make_world(db_session, finance="shadow")
        _connect(db_session, w.laz)
        _connect(db_session, w.cbi)
        _connect(
            db_session, w.other_venue
        )  # its org is live but has no claim here? it is live: it IS due
        due = {(o.slug, v.name, s) for o, v, s in jobs.due_venues(db_session, NOW)}
        assert (w.org.slug, "La Zeppa", SLOT) in due
        assert not any(name == "Head Office" for _, name, _ in due)
        assert not any(
            name == "Dunedin Social Club" for _, name, _ in due
        )  # not connected
        from app.services.finance_core import modes

        modes.set_mode(db_session, w.org.id, "finance", "off")
        db_session.flush()
        assert not any(
            slug == w.org.slug for slug, _, _ in jobs.due_venues(db_session, NOW)
        )

    def test_the_night_refreshes_logs_forecasts_scores_and_claims_once(
        self, db_session, loaded
    ):
        w = make_world(db_session, finance="shadow")
        _connect(db_session, w.laz)
        series = weekday_series(START, (SLOT - START).days + 1)
        actuals_svc.upsert_days(
            db_session,
            w.org.id,
            w.laz.id,
            [
                {"date": d, "sales_incl_cents": c, "sales_ex_cents": c, "orders": 1}
                for d, c in series.items()
            ],
        )
        db_session.commit()

        out = jobs.run_due(db_session, NOW, config_db=db_session)
        assert len(out["ran"]) == 1, out
        result = out["ran"][0]
        assert (
            result["errors"] == []
            and result["actuals"] == 14
            and result["forecast_days"] == 70
        )
        assert result["loaded_log"] > 0 and result["scored"]["date"] == SLOT.isoformat()
        actions = {c["action"] for c in loaded["calls"]}
        assert actions == {"get_sales", "get_budgets_raw"}
        run = (
            db_session.query(FinJobRun).filter(FinJobRun.job == "finance.nightly").one()
        )
        assert (run.scope_id, run.slot, run.error) == (
            w.laz.id,
            SLOT.isoformat(),
            None,
        ) and run.finished_at
        vintage = db_session.query(FinForecastVintage).one()
        assert (
            vintage.as_of == SLOT + dt.timedelta(days=1) and vintage.method == "median8"
        )
        log = (
            db_session.query(FinLoadedBudgetLog)
            .filter(FinLoadedBudgetLog.venue_id == w.laz.id)
            .all()
        )
        assert all(r.captured_on == SLOT + dt.timedelta(days=1) for r in log)
        assert {r.date for r in log} >= {
            SLOT,
            SLOT + dt.timedelta(days=jobs.LOG_AHEAD_DAYS),
        }
        # The benchmark captured for the closed day is the budget FOR that day.
        assert (
            next(r for r in log if r.date == SLOT).amount_cents
            == (2000 + SLOT.day) * 100
        )
        # A second tick finds the slot taken.
        before = len(loaded["calls"])
        assert jobs.run_due(db_session, NOW, config_db=db_session)["ran"] == []
        assert len(loaded["calls"]) == before
        # The log only grows when Loaded's figure changes.
        assert (
            jobs.run_due(db_session, NOW + dt.timedelta(days=1), config_db=db_session)[
                "ran"
            ][0]["loaded_log"]
            == 1
        )

    def test_a_failed_step_lands_on_the_run_row_and_the_others_still_run(
        self, db_session, loaded
    ):
        w = make_world(db_session, finance="live")
        _connect(db_session, w.laz)
        series = weekday_series(START, (SLOT - START).days + 1)
        actuals_svc.upsert_days(
            db_session,
            w.org.id,
            w.laz.id,
            [
                {"date": d, "sales_incl_cents": c, "sales_ex_cents": c, "orders": 1}
                for d, c in series.items()
            ],
        )
        db_session.commit()
        loaded["fail"].add("get_budgets_raw")
        out = jobs.run_due(db_session, NOW, config_db=db_session)
        result = out["ran"][0]
        assert result["errors"] == ["loaded budget log: get_budgets_raw is down"]
        assert result["forecast_days"] == 70 and result["actuals"] == 14
        run = db_session.query(FinJobRun).one()
        assert (
            run.error == "loaded budget log: get_budgets_raw is down"
            and run.finished_at
        )
