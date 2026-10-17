"""The daily insight run (services/insights/runner.py).

Once per venue per local day, after its trading day closes; the weekly set on
Mondays and the roster check Thursday to Sunday; one row per insight subject
(the next run updates it, not adds another); a sister venue's prices compared
across the group; and one venue's failure never stopping the rest. Tool reads
are faked here — the detectors' own rules are in test_insight_detectors.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.db.models import Connection, Insight, InsightNote, InsightRun
from app.services.insights import runner
from tests.conftest import _make_organization, _make_venue

# Monday 19 Oct 2026, 08:00 in Auckland (19:00 UTC Sunday).
MONDAY_8AM = datetime(2026, 10, 18, 19, 0, tzinfo=timezone.utc)
AS_OF = date(2026, 10, 19)


def _label(d):
    return f"{d:%a} {d.day} {d:%b}"


def fake_rows(prices: dict):
    """Tool results by what is asked: Tuesdays staffed like Saturdays; and the
    venue's olive oil price (``prices`` by venue name)."""

    def rows(self, action, params):
        venue = self.venue.name
        if action == "get_sales" and params.get("breakdown") == "daily":
            out = []
            for i in range(63, 0, -1):
                d = AS_OF - timedelta(days=i)
                out.append(
                    {
                        "date": d.isoformat(),
                        "day": f"{d:%A}",
                        "actual": 500.0 if d.weekday() == 1 else 1000.0,
                        "budget": 1000.0,
                        "last_year": 900.0,
                    }
                )
            return out
        if action == "get_labour" and params.get("group_by") == "day":
            out = []
            for i in range(63, 0, -1):
                d = AS_OF - timedelta(days=i)
                cost = 450.0 if d.weekday() == 1 else 300.0
                out.append(
                    {
                        "date": _label(d),
                        "actual_cost": cost,
                        "rostered_cost": cost - 5,
                        "actual_hours": 10.0,
                        "rostered_hours": 10.0,
                    }
                )
            return out
        if (
            action == "get_received_items_for_period"
            and params.get("group_by") == "item_supplier"
        ):
            return [
                {
                    "item_name": "OIL OLIVE EXTRA VIRGIN",
                    "base_unit": "L",
                    "supplier_name": "Bidfood",
                    "unit_cost_last": prices[venue],
                    "unit_cost_min": prices[venue],
                    "unit_cost_max": prices[venue],
                    "invoice_count": 5,
                    "quantity_base": 300.0,
                    "item_id": "oil",
                }
            ]
        return []

    return rows


@pytest.fixture()
def group(db_session):
    org = _make_organization(db_session, name="Cook Brothers")
    venues = []
    for name in ("La Zeppa", "Dunedin Social Club"):
        v = _make_venue(db_session, name=name, organization_id=org.id)
        v.timezone = "Pacific/Auckland"
        db_session.add(
            Connection(
                connector_name="loadedhub", venue_id=v.id, enabled="true", config={}
            )
        )
        venues.append(v)
    db_session.flush()
    return org, venues


def test_what_runs_on_which_day():
    assert runner.cadences_for(date(2026, 10, 19)) == ["daily", "weekly"]  # Monday
    assert runner.cadences_for(date(2026, 10, 20)) == ["daily"]
    assert runner.cadences_for(date(2026, 10, 22)) == ["daily", "roster"]  # Thursday


def test_a_venue_is_due_after_its_own_trading_day_closes(group):
    _, (lz, _) = group
    clock = runner.VenueClock(lz)
    assert (
        clock.due(datetime(2026, 10, 18, 18, 20, tzinfo=timezone.utc)) is None
    )  # 07:20 NZ
    assert (
        clock.due(datetime(2026, 10, 18, 18, 31, tzinfo=timezone.utc)) == AS_OF
    )  # 07:31 NZ
    assert (
        clock.window(AS_OF, AS_OF + timedelta(days=1))["start"]
        == "2026-10-19T07:00:00+13:00"
    )


def test_a_monday_run_saves_insights_and_compares_sister_venues(
    db_session, group, monkeypatch
):
    org, (lz, dsc) = group
    monkeypatch.setattr(
        runner.Fetcher,
        "rows",
        fake_rows({"La Zeppa": 26.27, "Dunedin Social Club": 20.16}),
    )
    out = runner.run_due_insights(
        now=MONDAY_8AM, db=db_session, config_db=db_session, venue_ids=[lz.id, dsc.id]
    )
    assert {r["venue"] for r in out["ran"]} == {"La Zeppa", "Dunedin Social Club"}
    assert all(r["ok"] for r in out["ran"])

    rows = {
        (i.venue_id, i.detector, i.subject) for i in db_session.query(Insight).all()
    }
    assert (lz.id, "quiet_day_wages", "Tuesday") in rows
    assert (lz.id, "group_price_gap", "OIL OLIVE EXTRA VIRGIN") in rows
    assert not any(v == dsc.id and d == "group_price_gap" for v, d, _ in rows)
    gap = (
        db_session.query(Insight)
        .filter_by(venue_id=lz.id, detector="group_price_gap")
        .one()
    )
    assert (
        gap.headline
        == "La Zeppa pays 30% more for Oil Olive Extra Virgin than Dunedin Social Club"
    )
    assert gap.organization_id == org.id and gap.expires_at > MONDAY_8AM


def test_each_venue_runs_once_a_day(db_session, group, monkeypatch):
    _, venues = group
    vids = [v.id for v in venues]
    monkeypatch.setattr(
        runner.Fetcher,
        "rows",
        fake_rows({"La Zeppa": 26.27, "Dunedin Social Club": 20.16}),
    )
    runner.run_due_insights(
        now=MONDAY_8AM, db=db_session, config_db=db_session, venue_ids=vids
    )
    again = runner.run_due_insights(
        now=MONDAY_8AM + timedelta(minutes=5),
        db=db_session,
        config_db=db_session,
        venue_ids=vids,
    )
    assert again["ran"] == []
    assert (
        db_session.query(InsightRun).filter(InsightRun.venue_id.in_(vids)).count() == 2
    )


def test_the_next_run_updates_an_insight_instead_of_adding_one(
    db_session, group, monkeypatch
):
    _, venues = group
    vids = [v.id for v in venues]
    monkeypatch.setattr(
        runner.Fetcher,
        "rows",
        fake_rows({"La Zeppa": 26.27, "Dunedin Social Club": 20.16}),
    )
    runner.run_due_insights(
        now=MONDAY_8AM, db=db_session, config_db=db_session, venue_ids=vids
    )
    q = db_session.query(Insight).filter(
        Insight.venue_id.in_(vids), Insight.detector == "quiet_day_wages"
    )
    first = q.count()
    assert first
    runner.run_due_insights(
        now=MONDAY_8AM + timedelta(days=7),
        db=db_session,
        config_db=db_session,
        venue_ids=vids,
    )
    assert q.count() == first


def test_one_venue_failing_never_stops_the_others(db_session, group, monkeypatch):
    _, (lz, dsc) = group
    good = fake_rows({"La Zeppa": 26.27, "Dunedin Social Club": 20.16})

    def rows(self, action, params):
        if self.venue.name == "La Zeppa":
            raise RuntimeError("LoadedHub fell over")
        return good(self, action, params)

    monkeypatch.setattr(runner.Fetcher, "rows", rows)
    out = runner.run_due_insights(
        now=MONDAY_8AM, db=db_session, config_db=db_session, venue_ids=[lz.id, dsc.id]
    )
    by = {r["venue"]: r for r in out["ran"]}
    assert by["La Zeppa"]["ok"] is False and "fell over" in by["La Zeppa"]["error"]
    assert by["Dunedin Social Club"]["ok"] is True
    run = db_session.query(InsightRun).filter_by(venue_id=lz.id).one()
    assert run.ok is False and run.finished_at is not None


def test_a_venue_that_needs_reconnecting_is_skipped(db_session, group, monkeypatch):
    _, (lz, dsc) = group
    conn = db_session.query(Connection).filter_by(venue_id=lz.id).one()
    conn.needs_reconnect = True
    db_session.flush()
    monkeypatch.setattr(
        runner.Fetcher, "rows", fake_rows({"La Zeppa": 1.0, "Dunedin Social Club": 1.0})
    )
    out = runner.run_due_insights(
        now=MONDAY_8AM, db=db_session, config_db=db_session, venue_ids=[lz.id, dsc.id]
    )
    assert [r["venue"] for r in out["ran"]] == ["Dunedin Social Club"]


def test_a_failed_read_is_noted_and_the_rest_still_run(db_session, group, monkeypatch):
    """A tool that errors leaves its data empty; the other detectors still
    speak (the run is ok, the error is on its summary)."""
    from app.connectors.tool_executor import ToolResult

    _, (lz, _) = group

    def execute(**kw):
        if kw["action"] == "get_labour":
            return ToolResult(success=False, payload=None, error="Loaded said no")
        return ToolResult(success=True, payload={"rows": []})

    monkeypatch.setattr("app.connectors.tool_executor.execute_connector_tool", execute)
    out = runner.run_due_insights(
        now=MONDAY_8AM, db=db_session, config_db=db_session, venue_ids=[lz.id]
    )
    assert out["ran"][0]["ok"] is True
    assert any("Loaded said no" in e for e in out["ran"][0]["errors"])


def test_three_weeks_after_we_ll_fix_it_norm_says_whether_it_worked(
    db_session, group, monkeypatch
):
    org, (lz, dsc) = group
    db_session.add(
        InsightNote(
            organization_id=org.id,
            venue_id=lz.id,
            subject_key=f"{lz.id}:quiet_day_wages:Tuesday",
            detector="quiet_day_wages",
            verdict="acting",
            reason="cutting the Tuesday roster to three",
            baseline=1171.0,
            created_at=MONDAY_8AM - timedelta(days=22),
        )
    )
    db_session.add(
        Insight(
            organization_id=org.id,
            venue_id=lz.id,
            detector="quiet_day_wages",
            subject="Tuesday",
            subject_key=f"{lz.id}:quiet_day_wages:Tuesday",
            area="wages",
            audience="reports:read",
            headline="Tuesdays at La Zeppa run wages at 104% of sales",
            prompt="…",
            value=1171.0,
            as_of="2026-09-27",
            expires_at=MONDAY_8AM - timedelta(days=14),
        )
    )
    db_session.flush()
    fixed = fake_rows({"La Zeppa": 26.27, "Dunedin Social Club": 20.16})

    def rows(self, action, params):
        out = fixed(self, action, params)
        if action == "get_labour" and params.get("group_by") == "day":
            for r in out:  # Tuesdays now staffed like the rest of the week
                r["actual_cost"] = r["rostered_cost"] = (
                    150.0 if r["date"].startswith("Tue") else 300.0
                )
        return out

    monkeypatch.setattr(runner.Fetcher, "rows", rows)
    runner.run_due_insights(
        now=MONDAY_8AM, db=db_session, config_db=db_session, venue_ids=[lz.id]
    )
    f = db_session.query(Insight).filter_by(venue_id=lz.id, detector="follow_up").one()
    assert f.headline.startswith(
        "It worked: Tuesdays at La Zeppa run wages at 104% of sales"
    )
    assert "now $0" in f.headline
    assert f.prompt.startswith(
        "Has cutting the Tuesday roster to three worked at La Zeppa?"
    )


def test_the_weekly_run_reads_the_last_four_weeks_hour_by_hour(
    db_session, group, monkeypatch
):
    _, (lz, _) = group
    asked = []
    good = fake_rows({"La Zeppa": 26.27, "Dunedin Social Club": 20.16})

    def rows(self, action, params):
        asked.append((action, params))
        return good(self, action, params)

    monkeypatch.setattr(runner.Fetcher, "rows", rows)
    runner.run_due_insights(
        now=MONDAY_8AM, db=db_session, config_db=db_session, venue_ids=[lz.id]
    )
    detail = [p for a, p in asked if a == "get_labour" and p["group_by"] == "detail"]
    hourly = [p for a, p in asked if a == "get_sales" and p.get("interval")]
    assert len(detail) == 1 and len(hourly) == 1
    for p in detail + hourly:
        assert p["start"] == "2026-09-21T07:00:00+12:00"
        assert p["end"] == "2026-10-19T07:00:00+13:00"
    assert hourly[0]["interval"] == "01:00:00" and hourly[0]["tax"] == "exclude"


def test_a_detail_payload_gives_the_clock_ins_or_else_the_roster():
    f = runner.Fetcher(None, None, None)
    clocked = {"rostered": [{"name": "r"}], "clockins": [{"name": "c"}], "leave": []}
    f.payload = lambda action, params: clocked
    assert f.rows("get_labour", {}) == [{"name": "c"}]
    f.payload = lambda action, params: {**clocked, "clockins": []}
    assert f.rows("get_labour", {}) == [{"name": "r"}]
