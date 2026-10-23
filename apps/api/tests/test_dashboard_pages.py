"""Dashboards are pages of code: a kind of app (docs/plans/apps-and-dashboards-one-system.md).

Asked to make Rich's dashboard a dashboard, Norm could only put the app in a
tile container — no Edit button, the wrong background — because dashboards
were tiles and apps were pages (22 Oct 2026). Now a dashboard is an app of kind
``dashboard`` and the owner's rules hold: only its owner changes it; anyone it
is shared with, and anyone using one of Norm's templates, uses it AS IT IS
(no copy, so they get its updates) or makes their own copy to change.

These pin: the kind and the AI agent a new page names; edits that change only
what they name; a deliberate change of kind; use-without-copy for shared
dashboards and templates; copies; each person's own settings; history; and
what Norm is told and given.
"""

import pytest
from fastapi import HTTPException

from app.agents.internal_tools import get_handler, get_previewer
from app.db.config_models import PageTemplate
from app.db.models import App, AppRecord, AppShare
from app.services import app_runtime as AR
from app.services import dashboards, page_templates
from tests.conftest import _make_organization, _make_thread
from tests.org_helpers import auth_headers
from tests.supplier_fakes import bind_sessions
from tests.test_app_platform import READ_SPEC, _member

BUILD = ["reports:read", "apps:build", "apps:share"]
PAGE = '<div data-norm-row><div data-norm-col><section data-norm-block="sales"><h3>Sales</h3></section></div></div>'


@pytest.fixture()
def world(db_session, monkeypatch):
    bind_sessions(monkeypatch, db_session)
    org = _make_organization(db_session)
    owner = _member(db_session, org, BUILD)
    colleague = _member(db_session, org, BUILD)
    viewer = _member(db_session, org, ["reports:read"])
    db_session.flush()
    threads = {}
    for u in (owner, colleague, viewer):
        t = _make_thread(db_session, u, domain="norm")
        t.organization_id = org.id
        threads[u.id] = t.id
    db_session.flush()

    class W:
        pass

    w = W()
    w.org, w.owner, w.colleague, w.viewer, w.threads = (
        org,
        owner,
        colleague,
        viewer,
        threads,
    )
    return w


def _dash(db, user, slug="lott", **over):
    return AR.save_app(
        db,
        user,
        {
            "name": "Lott Cafe",
            "slug": slug,
            "kind": "dashboard",
            "agent": "reports",
            "spec": {
                **READ_SPEC,
                "storage": {"namespace": f"{slug}_ns", "collections": ["notes"]},
            },
            "ui_source": PAGE,
            **over,
        },
    )


def _template(db, slug="operations", agent="reports", version=1, ui=PAGE):
    t = PageTemplate(
        slug=slug,
        agent_slug=agent,
        name="Operations",
        spec={
            **READ_SPEC,
            "storage": {"namespace": "lott_ns", "collections": ["notes"]},
        },
        ui_source=ui,
        version=version,
        enabled=True,
    )
    db.add(t)
    db.flush()
    return t


class TestSavingAPage:
    def test_a_dashboard_lives_on_a_dashboard_page(self, world, db_session):
        with pytest.raises(HTTPException) as e:
            _dash(db_session, world.owner, agent="functions")
        assert "Dashboard page" in str(e.value.detail)
        out = _dash(db_session, world.owner)
        assert (out["kind"], out["agent"]) == ("dashboard", "reports")

    def test_edits_change_only_what_they_name(self, world, db_session):
        _dash(db_session, world.owner)
        out = AR.save_app(
            db_session,
            world.owner,
            {
                "slug": "lott",
                "edits": [{"find": "<h3>Sales</h3>", "replace": "<h3>Net sales</h3>"}],
            },
        )
        app = db_session.query(App).filter(App.slug == "lott").one()
        v = AR.current_version(db_session, app)
        assert out["version"] == 2 and app.name == "Lott Cafe"
        assert v.ui_source == PAGE.replace("Sales", "Net sales")
        assert v.spec["storage"]["namespace"] == "lott_ns"  # carried over
        # A find that isn't there exactly once saves nothing.
        for find in ("<h3>Wages</h3>", "data-norm"):
            with pytest.raises(HTTPException) as e:
                AR.save_app(
                    db_session,
                    world.owner,
                    {"slug": "lott", "edits": [{"find": find, "replace": "x"}]},
                )
            assert "exactly once" in str(e.value.detail)
        assert AR.current_version(db_session, app).version == 2

    def test_kind_changes_only_when_asked_and_a_dashboard_is_shared_whole(
        self, world, db_session
    ):
        AR.save_app(
            db_session,
            world.owner,
            {
                "name": "Top",
                "slug": "top",
                "kind": "app",
                "agent": "reports",
                "spec": READ_SPEC,
                "ui_source": PAGE,
            },
        )
        app = db_session.query(App).filter(App.slug == "top").one()
        db_session.add_all(
            [
                AppShare(
                    app_id=app.id,
                    principal_type="user",
                    principal_id=world.colleague.id,
                    access="edit",
                ),
                AppShare(
                    app_id=app.id,
                    principal_type="organization",
                    principal_id=world.org.id,
                    access="edit",
                ),
            ]
        )
        db_session.flush()
        with pytest.raises(HTTPException) as e:
            AR.save_app(db_session, world.owner, {"slug": "top", "kind": "dashboard"})
        assert "change_kind" in str(e.value.detail)
        AR.save_app(
            db_session,
            world.owner,
            {"slug": "top", "kind": "dashboard", "change_kind": True},
        )
        shares = db_session.query(AppShare).filter(AppShare.app_id == app.id).all()
        assert [(s.principal_type, s.access) for s in shares] == [
            ("organization", "view")
        ]
        assert app.kind == "dashboard" and app.visibility == "organization"

    def test_only_the_owner_changes_a_shared_dashboard(self, world, db_session):
        _dash(db_session, world.owner)
        dashboards.set_shared(db_session, world.owner, "lott", True)
        app = db_session.query(App).filter(App.slug == "lott").one()
        # An old edit share means nothing on a dashboard.
        db_session.add(
            AppShare(
                app_id=app.id,
                principal_type="user",
                principal_id=world.colleague.id,
                access="edit",
            )
        )
        db_session.flush()
        with pytest.raises(HTTPException) as e:
            AR.save_app(
                db_session, world.colleague, {"slug": "lott", "ui_source": "<p/>"}
            )
        assert e.value.status_code == 403 and "copy" in str(e.value.detail)


class TestUsingWithoutCopying:
    def test_a_shared_dashboard_is_used_as_it_is_and_follows_its_owner(
        self, world, db_session
    ):
        _dash(db_session, world.owner)
        dashboards.set_shared(db_session, world.owner, "lott", True)
        used = dashboards.use(
            db_session, db_session, world.colleague, "reports", "app:lott"
        )
        assert (used["source"], used["can_change"], used["version"]) == (
            "shared",
            False,
            1,
        )
        AR.save_app(db_session, world.owner, {"slug": "lott", "ui_source": "<p>v2</p>"})
        shown = dashboards.shown(db_session, db_session, world.colleague, "reports")
        assert (shown["ref"], shown["version"]) == ("app:lott", 2)
        # Stop sharing: it's gone from their page, with nothing of theirs lost.
        dashboards.set_shared(db_session, world.owner, "lott", False)
        assert (
            dashboards.shown(db_session, db_session, world.colleague, "reports") is None
        )

    def test_a_template_is_used_live_reads_its_own_records_and_never_writes(
        self, world, db_session
    ):
        _template(db_session)
        assert dashboards.has_any(db_session, db_session, world.viewer, "reports")
        shown = dashboards.shown(db_session, db_session, world.viewer, "reports")
        assert (shown["ref"], shown["can_change"]) == ("template:operations", False)
        app, version = page_templates.load_for_user(
            db_session, db_session, "template:operations", world.viewer
        )
        access = AR.resolve_access(db_session, app, world.viewer)
        assert (access.can_run, access.role, access.write_approved) == (
            True,
            "view",
            False,
        )
        # Its records are its own, never those of an app that shares the name.
        assert version.spec["storage"]["namespace"] == "template_operations"
        # The next published version is what everyone using it sees.
        db_session.get(PageTemplate, "operations").version = 2
        db_session.flush()
        assert (
            dashboards.shown(db_session, db_session, world.viewer, "reports")["version"]
            == 2
        )
        # A second template for the page (Group performance) leaves the first
        # as the one shown to someone who hasn't chosen: no chooser.
        import datetime as dt

        later = _template(db_session, slug="group-performance")
        later.created_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5)
        db_session.flush()
        shown = dashboards.shown(db_session, db_session, world.viewer, "reports")
        assert shown["ref"] == "template:operations"


class TestCopies:
    def test_a_copy_is_theirs_starts_without_records_and_keeps_their_settings(
        self, world, db_session
    ):
        _dash(db_session, world.owner)
        dashboards.set_shared(db_session, world.owner, "lott", True)
        src = db_session.query(App).filter(App.slug == "lott").one()
        db_session.add(
            AppRecord(
                namespace="lott_ns",
                organization_id=world.org.id,
                collection="notes",
                data={"n": 1},
            )
        )
        AR.mine_set(db_session, src, world.colleague, {"colours": "blue"})
        copy = dashboards.copy(db_session, db_session, world.colleague, "app:lott")
        mine = db_session.query(App).filter(App.slug == copy["slug"]).one()
        spec = AR.current_version(db_session, mine).spec
        assert (copy["slug"], copy["source"], copy["can_change"]) == (
            "lott-2",
            "mine",
            True,
        )
        assert mine.kind == "dashboard" and mine.created_by == world.colleague.id
        assert spec["storage"]["namespace"] != "lott_ns"
        assert (
            db_session.query(AppRecord)
            .filter(AppRecord.namespace == spec["storage"]["namespace"])
            .count()
            == 0
        )
        assert AR.mine_get(db_session, mine, world.colleague) == {"colours": "blue"}
        assert (
            dashboards.shown(db_session, db_session, world.colleague, "reports")["ref"]
            == "app:lott-2"
        )

    def test_copying_needs_the_build_permission(self, world, db_session):
        _template(db_session)
        with pytest.raises(HTTPException) as e:
            dashboards.copy(db_session, db_session, world.viewer, "template:operations")
        assert "apps:build" in str(e.value.detail)


class TestOwnSettingsAndHistory:
    def test_each_persons_settings_are_their_own(self, world, db_session):
        _dash(db_session, world.owner)
        app = db_session.query(App).filter(App.slug == "lott").one()
        AR.mine_set(db_session, app, world.owner, {"mode": "$", "colour": "red"})
        AR.mine_set(db_session, app, world.colleague, {"mode": "%"})
        AR.mine_set(db_session, app, world.owner, {"colour": None})
        assert AR.mine_get(db_session, app, world.owner) == {"mode": "$"}
        assert AR.mine_get(db_session, app, world.colleague) == {"mode": "%"}

    def test_bringing_back_a_version_adds_one(self, world, db_session):
        _dash(db_session, world.owner)
        AR.save_app(
            db_session,
            world.owner,
            {"slug": "lott", "ui_source": "<p>lost the chart</p>"},
        )
        app = db_session.query(App).filter(App.slug == "lott").one()
        with pytest.raises(HTTPException):
            AR.restore_version(
                db_session, app, world.colleague, 1, AR.AppAccess(True, "view", False)
            )
        out = AR.restore_version(
            db_session,
            app,
            world.owner,
            1,
            AR.resolve_access(db_session, app, world.owner),
        )
        assert (out["version"], out["from"]) == (3, 1)
        assert AR.current_version(db_session, app).ui_source == PAGE
        assert [v["version"] for v in AR.list_versions(db_session, app)] == [3, 2, 1]


class TestNormsTools:
    def _tool(self, world, db_session, user, action, **params):
        return get_handler("norm_reports", action)(
            params, db_session, world.threads[user.id]
        )

    def test_norm_lists_uses_and_copies(self, world, db_session):
        _template(db_session)
        _dash(db_session, world.owner)
        dashboards.set_shared(db_session, world.owner, "lott", True)
        out = self._tool(
            world, db_session, world.colleague, "get_dashboards", agent="reports"
        )
        page = out["data"]["pages"][0]
        assert page["shows"]["ref"] == "template:operations"
        assert [d["ref"] for d in page["shared_with_them"]] == ["app:lott"]
        used = self._tool(
            world,
            db_session,
            world.colleague,
            "manage_dashboard",
            op="use",
            ref="app:lott",
        )
        assert used["success"] and used["data"]["dashboard"]["can_change"] is False
        made = self._tool(
            world,
            db_session,
            world.colleague,
            "manage_dashboard",
            op="copy",
            ref="template:operations",
        )
        assert made["data"]["dashboard"]["slug"] == "operations"

    def test_get_app_says_who_can_change_it(self, world, db_session):
        _template(db_session)
        _dash(db_session, world.owner)
        dashboards.set_shared(db_session, world.owner, "lott", True)
        read = get_handler("norm", "get_app")
        theirs = read({"slug": "lott"}, db_session, world.threads[world.colleague.id])[
            "data"
        ]
        assert theirs["can_change"] is False and "copy" in theirs["why_not"]
        template = read(
            {"slug": "template:operations"},
            db_session,
            world.threads[world.colleague.id],
        )["data"]
        assert template["can_change"] is False and template["ui_source"] == PAGE

    def test_a_new_dashboard_from_norm_is_what_the_page_shows(self, world, db_session):
        out = get_handler("norm", "save_app")(
            {
                "name": "Ops",
                "slug": "ops",
                "kind": "dashboard",
                "agent": "reports",
                "spec": READ_SPEC,
                "ui_source": PAGE,
            },
            db_session,
            world.threads[world.owner.id],
        )
        assert out["data"]["open_url"] == "/app?page=dashboard-reports"
        assert (
            dashboards.shown(db_session, db_session, world.owner, "reports")["ref"]
            == "app:ops"
        )

    def test_only_a_change_others_see_asks_first(self, world, db_session):
        _dash(db_session, world.owner)
        preview = get_previewer("norm_reports", "manage_dashboard")
        thread = world.threads[world.owner.id]
        assert preview({"op": "unshare", "ref": "app:lott"}, db_session, thread) == {
            "run": True
        }
        dashboards.set_shared(db_session, world.owner, "lott", True)
        card = preview({"op": "unshare", "ref": "app:lott"}, db_session, thread)
        assert card["preview"]["changes"][0]["after"] == "No"
        assert preview({"op": "use", "ref": "app:lott"}, db_session, thread) == {
            "run": True
        }

    def test_norm_is_told_a_template_cant_be_changed(self, world, db_session):
        from app.agents.prompt_builder import _dashboard_on_screen

        _template(db_session)
        text = _dashboard_on_screen(db_session, world.colleague.id, "reports")
        assert "template:operations" in text and "copy" in text


class TestRoutes:
    def test_the_page_the_template_and_the_persons_settings(
        self, client, world, db_session
    ):
        h = auth_headers(world.viewer)
        assert client.get("/api/dashboards/reports", headers=h).json() == {
            "dashboard": None,
            "tiles": True,
        }
        _template(db_session)
        db_session.commit()
        page = client.get("/api/dashboards/reports", headers=h).json()
        assert (
            page["tiles"] is False and page["dashboard"]["ref"] == "template:operations"
        )
        app = client.get("/api/apps/template:operations", headers=h).json()
        assert (app["kind"], app["template"], app["access"], app["owner"]) == (
            "dashboard",
            True,
            "view",
            "Norm",
        )
        assert (
            client.put(
                "/api/apps/template:operations/mine",
                json={"data": {"mode": "$"}},
                headers=h,
            ).status_code
            == 200
        )
        assert client.get("/api/apps/template:operations/mine", headers=h).json() == {
            "data": {"mode": "$"}
        }

    def test_a_dashboard_is_never_shared_with_named_people_or_published(
        self, client, world, db_session
    ):
        _dash(db_session, world.owner)
        db_session.commit()
        h = auth_headers(world.owner)
        r = client.post(
            "/api/apps/lott/share",
            json={
                "principal_type": "user",
                "principal_id": world.colleague.id,
                "access": "edit",
            },
            headers=h,
        )
        assert r.status_code == 400
        assert client.post(
            "/api/dashboards/share", json={"slug": "lott", "shared": True}, headers=h
        ).json()["shared"]
        versions = client.get("/api/apps/lott/versions", headers=h).json()["versions"]
        assert [v["version"] for v in versions] == [1]


class TestNothingBlocksTheServer:
    def test_app_and_dashboard_routes_run_on_their_own_threads(self):
        """Their work blocks (the database, a connector call to Loaded that can
        take many seconds). As `async def` it ran on the process's event loop,
        so a dashboard fanning out twelve calls froze every other request: the
        Change dashboard list took 14 s, then failed with 503s (22 Oct 2026)."""
        import inspect

        from app.routers import apps as apps_router
        from app.routers import dashboards as dashboards_router

        awaits_the_upload = {"app_file_upload"}
        blocking = [
            r.endpoint.__name__
            for module in (apps_router, dashboards_router)
            for r in module.router.routes
            if inspect.iscoroutinefunction(r.endpoint)
            and r.endpoint.__name__ not in awaits_the_upload
        ]
        assert blocking == []
