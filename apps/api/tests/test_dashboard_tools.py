"""Norm builds and changes dashboards (Oct 2026, docs/plans/norm-builds-dashboards.md).

Asked for a dashboard, Norm built an App: it had no way to make one (Rich, 20
Oct 2026). These pin the two tools: a tile is tried before it is saved and a
wrong field is named; only reading tools make tiles; tiles are placed in words
without overlapping; a change to a dashboard only its author sees runs at once
and one to a shared dashboard asks first; the sharer alone changes a shared
dashboard; an App tile on a shared dashboard must be one everyone can open.
"""

import uuid

import pytest

from app.agents.internal_tools import get_handler, get_previewer
from app.db.config_models import ConnectionSpec
from app.db.models import App, AppShare, AppVersion, Report, ReportVersion
from app.routers import reports_crud as rc
from tests.conftest import _make_membership, _make_thread
from tests.org_helpers import make_org, make_user
from tests.supplier_fakes import bind_sessions

ROWS = [
    {"date": "2026-10-19", "sales": 1200.0, "budget": 1000.0, "venue": "A"},
    {"date": "2026-10-20", "sales": 900.0, "budget": 1000.0, "venue": "A"},
]


@pytest.fixture()
def world(db_session, monkeypatch):
    bind_sessions(monkeypatch, db_session)
    db_session.add(
        ConnectionSpec(
            id=str(uuid.uuid4()),
            connector_name="testconn",
            display_name="Test",
            execution_mode="internal",
            auth_type="none",
            auth_config={},
            tools=[
                {"action": "read_sales", "method": "GET", "effect": "read"},
                {"action": "change_things", "method": "POST", "effect": "write"},
            ],
            endpoints=[],
            enabled=True,
        )
    )
    ran = []

    def fake_run(db, cdb, user, report, script, gf):
        ran.append((script["action"], gf.get("period")))
        return list(ROWS), [], None

    monkeypatch.setattr(rc, "run_tile", fake_run)
    org = make_org(db_session, "Org")
    owner, colleague = make_user(db_session), make_user(db_session)
    _make_membership(db_session, owner, org)
    _make_membership(db_session, colleague, org)
    db_session.flush()
    threads = {}
    for u in (owner, colleague):
        t = _make_thread(db_session, u, domain="norm")
        t.organization_id = org.id
        threads[u.id] = t.id
    db_session.flush()

    def manage(user, **params):
        return get_handler("norm_reports", "manage_dashboard")(
            params, db_session, threads[user.id]
        )

    def read(user, **params):
        return get_handler("norm_reports", "get_dashboards")(
            params, db_session, threads[user.id]
        )

    def preview(user, **params):
        return get_previewer("norm_reports", "manage_dashboard")(
            params, db_session, threads[user.id]
        )

    return type(
        "W",
        (),
        dict(
            org=org,
            owner=owner,
            colleague=colleague,
            manage=staticmethod(manage),
            read=staticmethod(read),
            preview=staticmethod(preview),
            ran=ran,
        ),
    )


SALES = {
    "title": "Sales",
    "kind": "number",
    "tool": "testconn.read_sales",
    "value": "sales",
}


def _new(world, **kw):
    out = world.manage(world.owner, op="create", agent="reports", title="Ops", **kw)
    assert out["success"], out
    return out["data"]["dashboard_id"]


class TestBuildingADashboard:
    def test_made_in_the_organisation_and_becomes_theirs(self, world, db_session):
        did = _new(world)
        report = db_session.get(Report, did)
        assert report.organization_id == world.org.id and report.is_dashboard
        listed = world.read(world.owner, agent="reports")["data"]
        mine = next(d for d in listed["dashboards"] if d["dashboard_id"] == did)
        assert mine["in_use"] and mine["yours"]
        assert listed["_no_display"]  # a list shows nothing in the conversation

    def test_a_tile_is_tried_before_it_is_saved(self, world, db_session):
        did = _new(world)
        out = world.manage(world.owner, op="add_tile", dashboard_id=did, tile=SALES)
        assert out["success"], out
        assert world.ran == [("read_sales", "this_month")]
        assert out["data"]["tile_tried"]["first_rows"][0]["sales"] == 1200.0
        shown = world.read(world.owner, dashboard_id=did)["data"]
        assert shown["report_id"] == did  # shows the dashboard in the conversation
        (tile,) = shown["tiles"]
        assert tile["tool"] == "testconn.read_sales" and tile["value"] == "sales"
        assert tile["place"] == "row 1, left, a quarter wide, short"

    def test_a_field_that_isnt_there_is_named_and_nothing_is_saved(
        self, world, db_session
    ):
        did = _new(world)
        before = db_session.query(ReportVersion).filter_by(report_id=did).count()
        out = world.manage(
            world.owner,
            op="add_tile",
            dashboard_id=did,
            tile={**SALES, "kind": "bar", "x": "day", "y": ["sales", "gross"]},
        )
        assert not out["success"]
        assert "day, gross" in out["error"] and "budget, date, sales" in out["error"]
        assert db_session.get(Report, did).charts == []
        assert (
            db_session.query(ReportVersion).filter_by(report_id=did).count() == before
        )

    def test_only_a_reading_tool_can_be_a_tile(self, world):
        did = _new(world)
        out = world.manage(
            world.owner,
            op="add_tile",
            dashboard_id=did,
            tile={**SALES, "tool": "testconn.change_things"},
        )
        assert not out["success"] and "only use a tool that reads" in out["error"]

    def test_three_thirds_sit_side_by_side_under_a_full_width_tile(
        self, world, db_session
    ):
        did = _new(world)
        world.manage(
            world.owner,
            op="add_tile",
            dashboard_id=did,
            tile={**SALES, "title": "Top", "width": "full"},
        )
        top = db_session.get(Report, did).charts[0].id
        after = top
        for name in ("Sales", "Wages", "Purchases"):
            out = world.manage(
                world.owner,
                op="add_tile",
                dashboard_id=did,
                after=after,
                tile={**SALES, "title": name, "width": "third", "height": "medium"},
            )
            assert out["success"], out
            report = db_session.get(Report, did)
            after = next(c.id for c in report.charts if c.title == name)
        layout = {i["chart_id"]: i for i in db_session.get(Report, did).layout}
        thirds = sorted(
            (layout[c.id] for c in db_session.get(Report, did).charts if c.id != top),
            key=lambda i: i["col"],
        )
        assert [(i["row"], i["col"], i["colSpan"]) for i in thirds] == [
            (5, 1, 8),
            (5, 9, 8),
            (5, 17, 8),
        ]

    def test_removing_a_tile_closes_the_gap_and_history_brings_it_back(
        self, world, db_session
    ):
        did = _new(world)
        world.manage(
            world.owner,
            op="add_tile",
            dashboard_id=did,
            tile={**SALES, "title": "First", "width": "full"},
        )
        world.manage(
            world.owner,
            op="add_tile",
            dashboard_id=did,
            tile={**SALES, "title": "Second", "width": "full"},
        )
        first = next(
            c for c in db_session.get(Report, did).charts if c.title == "First"
        )
        assert world.manage(
            world.owner, op="remove_tile", dashboard_id=did, tile_id=first.id
        )["success"]
        (only,) = db_session.get(Report, did).layout
        assert only["row"] == 1  # the second tile moved up
        versions = world.read(world.owner, dashboard_id=did, history=True)["data"][
            "versions"
        ]
        with_both = next(
            v["version"] for v in versions if "Second" in (v["note"] or "")
        )
        out = world.manage(
            world.owner, op="restore", dashboard_id=did, version=with_both
        )
        assert out["success"], out
        titles = sorted(c.title for c in db_session.get(Report, did).charts)
        assert titles == ["First", "Second"]

    def test_a_change_keeps_what_norm_didnt_touch(self, world, db_session):
        """A template's tile carries settings outside Norm's terms (a delta
        label, a ratio). Renaming it must not drop them, or re-run its tool."""
        did = _new(world)
        world.manage(world.owner, op="add_tile", dashboard_id=did, tile=SALES)
        chart = db_session.get(Report, did).charts[0]
        chart.chart_spec = {
            **chart.chart_spec,
            "delta_label": "vs last month",
            "numerator_key": "sales",
        }
        db_session.flush()
        world.ran.clear()
        out = world.manage(
            world.owner,
            op="change_tile",
            dashboard_id=did,
            tile_id=chart.id,
            tile={"title": "Sales this month"},
        )
        assert out["success"], out
        db_session.refresh(chart)
        assert chart.title == "Sales this month"
        assert chart.chart_spec["delta_label"] == "vs last month"
        assert chart.chart_spec["numerator_key"] == "sales"
        assert world.ran == []  # nothing it reads changed, so no re-try


class TestAskingFirst:
    def test_a_personal_dashboard_needs_nobodys_yes(self, world):
        did = _new(world)
        assert world.preview(
            world.owner, op="add_tile", dashboard_id=did, tile=SALES
        ) == {"run": True}

    def test_a_shared_dashboard_shows_the_change_first(self, world, db_session):
        did = _new(world)
        db_session.get(Report, did).is_published = True
        db_session.flush()
        out = world.preview(world.owner, op="rename", dashboard_id=did, title="Ops 2")
        assert out["preview"]["changes"] == [
            {"field": "Title", "before": "Ops", "after": "Ops 2"}
        ]
        assert "everyone in the organisation" in out["preview"]["note"]
        bad = world.preview(
            world.owner,
            op="add_tile",
            dashboard_id=did,
            tile={**SALES, "value": "nope"},
        )
        assert "nope" in bad["error"]  # refused before anyone is asked

    def test_only_the_sharer_changes_it_and_anyone_can_copy(self, world, db_session):
        did = _new(world)
        db_session.get(Report, did).is_published = True
        db_session.flush()
        out = world.manage(world.colleague, op="rename", dashboard_id=did, title="Mine")
        assert not out["success"] and "Copy it" in out["error"]
        copy = world.manage(world.colleague, op="copy", dashboard_id=did)
        assert copy["success"]
        mine = db_session.get(Report, copy["data"]["dashboard_id"])
        assert mine.user_id == world.colleague.id and not mine.is_published


class TestAppTiles:
    def _app(self, db_session, world, shared):
        app = App(
            organization_id=world.org.id,
            created_by=world.owner.id,
            slug=f"gp-{uuid.uuid4().hex[:6]}",
            name="Gross profit",
            visibility="private",
        )
        db_session.add(app)
        db_session.flush()
        version = AppVersion(
            app_id=app.id, version=1, spec={}, created_by=world.owner.id
        )
        db_session.add(version)
        db_session.flush()
        app.current_version_id = version.id
        if shared:
            db_session.add(
                AppShare(
                    app_id=app.id,
                    principal_type="organization",
                    principal_id=world.org.id,
                    access="view",
                    granted_by=world.owner.id,
                )
            )
        db_session.flush()
        return app

    def test_a_private_app_cannot_go_on_a_shared_dashboard(self, world, db_session):
        did = _new(world)
        private = self._app(db_session, world, shared=False)
        tile = {"title": "GP", "kind": "app", "app": private.slug}
        assert world.manage(world.owner, op="add_tile", dashboard_id=did, tile=tile)[
            "success"
        ]  # fine on their own dashboard
        db_session.get(Report, did).is_published = True
        db_session.flush()
        out = world.manage(world.owner, op="add_tile", dashboard_id=did, tile=tile)
        assert not out["success"] and "private" in out["error"]
        shared = self._app(db_session, world, shared=True)
        out = world.manage(
            world.owner,
            op="add_tile",
            dashboard_id=did,
            tile={**tile, "app": shared.slug},
        )
        assert out["success"], out


class TestTheDashboardOnScreen:
    def test_norm_is_told_which_dashboard_the_page_shows(self, world, db_session):
        from app.agents.prompt_builder import _dashboard_on_screen

        did = _new(world)
        text = _dashboard_on_screen(db_session, world.owner.id, "reports")
        assert did in text and "**Ops**" in text and "never Loaded's" in text
        assert _dashboard_on_screen(db_session, world.owner.id, "hr") == ""


class TestThroughTheToolLoop:
    """The preview's answer decides it: "run" runs with no card, a card waits."""

    def _loop(self, world, db_session, monkeypatch, params):
        from app.agents import tool_loop
        from app.db.models import Thread, ToolCall
        from scripts.sync_dashboard_tools import ROWS
        from tests.tool_loop_harness import (
            ScriptedLlm,
            end_turn,
            tool,
            tool_round,
            tool_use,
        )

        db_session.add(
            ConnectionSpec(
                id=str(uuid.uuid4()),
                connector_name="norm_reports",
                display_name="Reports",
                execution_mode="internal",
                auth_type="none",
                auth_config={},
                tools=[r for r in ROWS if r["action"] == "manage_dashboard"],
                endpoints=[],
                enabled=True,
            )
        )
        db_session.flush()
        thread = (
            db_session.query(Thread).filter(Thread.user_id == world.owner.id).first()
        )
        ScriptedLlm(
            [
                tool_round(tool_use("t1", "norm_reports__manage_dashboard", params)),
                end_turn("done"),
            ]
        ).install(monkeypatch)
        tool_loop.run_tool_loop(
            "go",
            thread,
            db_session,
            "system prompt",
            [tool("norm_reports__manage_dashboard", "POST")],
            config_db=db_session,
        )
        return db_session.get(ToolCall, "t1")

    def test_personal_runs_and_shared_waits(self, world, db_session, monkeypatch):
        did = _new(world)
        tc = self._loop(
            world,
            db_session,
            monkeypatch,
            {"op": "rename", "dashboard_id": did, "title": "Mine now"},
        )
        assert tc.status == "executed" and tc.preview is None
        assert db_session.get(Report, did).title == "Mine now"

    def test_a_shared_dashboard_waits_for_approval(
        self, world, db_session, monkeypatch
    ):
        did = _new(world)
        db_session.get(Report, did).is_published = True
        db_session.flush()
        tc = self._loop(
            world,
            db_session,
            monkeypatch,
            {"op": "rename", "dashboard_id": did, "title": "Changed"},
        )
        assert tc.status == "pending_approval"
        assert tc.preview["changes"][0]["after"] == "Changed"
        assert db_session.get(Report, did).title == "Ops"
