"""Staff access: the routes a page calls directly follow the role too
(services/screen_access; docs/plans/staff-access.md §11.9).

The chat and an App's /call already followed the role. These routes only
checked the venue, so a Kitchen login could add shifts, read the staff list
with pay, place an order, open a manager's scheduled task or read every
colleague's email. Each test here is a staff token refused (or shown less)
beside a manager who keeps exactly what they had. Nothing calls Loaded: the
outbound call is replaced in every test that would make one.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm.attributes import flag_modified

from app.auth.permissions import STANDARD_ROLES
from app.db.config_models import MarketplaceApp
from app.db.engine import get_config_db, get_db
from app.db.models import (
    AutomatedTask,
    BackgroundJob,
    Connection,
    Memory,
    Thread,
    VenueDevice,
    WorkingDocument,
)
from app.services import agent_catalog, screen_access
from app.services.finance_core import modes
from tests.conftest import _make_organization
from tests.org_helpers import auth_headers, grant_venue, make_venue, seed_system_roles
from tests.test_staff_exposure import _person, _role, _row, _tool_spec

SALES = "lh.get_sales"  # Sales Analysis's (the reports AI agent)
RECIPES = "lh.get_recipes"  # Recipes & Costing's, and listed by the Kitchen team app
PAY = "lh.get_staff"  # an API endpoint no App claims (the staff list with pay)


@pytest.fixture(autouse=True)
def _fresh_cache():
    agent_catalog.invalidate_cache()
    yield
    agent_catalog.invalidate_cache()


def _screen(key, page=None, **placement):
    out = {"key": key, "page": {"id": page, "label": page} if page else None}
    out.update(placement)
    return out


class World:
    """One organisation and venue, the catalog Apps whose screens these routes
    serve, a Kitchen role (no AI agent, the Kitchen team app), a venue staff
    member under it, and two managers (the system Manager role)."""

    def __init__(self, db):
        self.db = db
        self.roles = seed_system_roles(db)
        self.org = _make_organization(db, name="Lott Group")
        self.org.functions_mode = "live"
        self.venue = make_venue(db, "La Zeppa", self.org)
        _tool_spec(db, "lh", ["get_sales", "get_recipes", "get_staff"])
        db.add(
            Connection(
                connector_name="lh", venue_id=self.venue.id, enabled="true", config={}
            )
        )
        _row(
            db,
            "norm-core",
            "app",
            {
                "member": "*",
                "switchable": False,
                "components": [_screen("dashboard_view")],
            },
        )
        _row(
            db,
            "rosters",
            "app",
            {
                "member": "time_attendance",
                "components": [_screen("roster_editor", "roster")],
            },
        )
        _row(
            db,
            "ordering",
            "app",
            {
                "member": "procurement",
                "components": [
                    _screen("orders_dashboard", "orders"),
                    _screen("purchase_order_editor"),
                    _screen("stock_catalogue", "stock-catalogue"),
                ],
            },
        )
        _row(
            db,
            "invoice-receiving",
            "app",
            {
                "member": "procurement",
                "components": [
                    _screen("invoices_dashboard", "invoices"),
                    _screen("receive_invoice_editor"),
                ],
            },
        )
        _row(
            db,
            "recipes-and-costing",
            "app",
            {
                "member": "executive_chef",
                "tools": [RECIPES],
                "components": [_screen("recipe_editor", "recipes")],
            },
        )
        _row(
            db,
            "sales-analysis",
            "app",
            {
                "member": "reports",
                "tools": [SALES],
                "components": [_screen("sales_analysis", "sales-analysis")],
            },
        )
        _row(
            db,
            "saved-reports",
            "app",
            {
                "member": "reports",
                "components": [
                    _screen("saved_reports_board", "saved-reports"),
                    _screen("report_builder"),
                ],
            },
        )
        _row(
            db, "kitchen", "team", {"tagline": "The kitchen.", "uses_tools": [RECIPES]}
        )
        self.kitchen = _role(
            db,
            self.org,
            "kitchen",
            agents=[],
            apps=["kitchen"],
            permissions=list(STANDARD_ROLES["team_member"]["permissions"]),
        )
        self.staff = _person(db, self.org, self.kitchen, name="Sam Chef")
        self.manager = _person(db, self.org, self.roles["manager"], name="Mia Manager")
        self.manager2 = _person(db, self.org, self.roles["manager"], name="Max Manager")
        for user in (self.staff, self.manager, self.manager2):
            grant_venue(db, user, self.venue)
        db.flush()

    def place(self, app_slug: str, key: str, **placement) -> None:
        """Place one of an App's screens in a team app (``teams`` list, or
        the older single ``team`` key)."""
        row = (
            self.db.query(MarketplaceApp).filter(MarketplaceApp.slug == app_slug).one()
        )
        comp = dict(row.composition)
        comp["components"] = [
            {**c, **placement} if c["key"] == key else c for c in comp["components"]
        ]
        row.composition = comp
        flag_modified(row, "composition")
        self.db.flush()


@pytest.fixture()
def w(db_session):
    return World(db_session)


def _h(user):
    return auth_headers(user)


# ------------------------------------------------------------ component API


@pytest.fixture()
def component_calls(monkeypatch):
    """execute_component_action replaced: records the call, never leaves Norm."""
    calls: list[tuple] = []

    def fake(component_key, action_name, params, venue_id, db, config_db):
        calls.append((component_key, action_name, venue_id))
        return {"status_code": 200, "data": [], "error": None}

    monkeypatch.setattr("app.services.component_api.execute_component_action", fake)
    return calls


def _component(client, user, venue, key, action):
    return client.post(
        f"/api/component-api/{key}/{action}",
        json={"venue_id": venue.id, "params": {}},
        headers=_h(user),
    )


class TestComponentApi:
    REFUSED = [
        ("roster_editor", "add_shift"),
        ("roster_editor", "update_shift"),
        ("roster_editor", "delete_shift"),
        ("roster_editor", "staff_list"),
        ("orders_dashboard", "send_order"),
        ("purchase_order_editor", "create_orders_batch"),
        ("recipe_editor", "list_recipes"),
    ]

    def test_staff_are_refused_screens_their_role_does_not_reach(
        self, w, client, component_calls
    ):
        for key, action in self.REFUSED:
            r = _component(client, w.staff, w.venue, key, action)
            assert r.status_code == 403, (key, action, r.text)
            assert r.json()["detail"] == screen_access.REFUSED
        assert component_calls == []

    def test_a_screen_placed_in_the_kitchen_team_app_opens_for_kitchen(
        self, w, client, component_calls
    ):
        w.place("recipes-and-costing", "recipe_editor", teams=["kitchen"])
        r = _component(client, w.staff, w.venue, "recipe_editor", "list_recipes")
        assert r.status_code == 200, r.text
        assert component_calls == [("recipe_editor", "list_recipes", w.venue.id)]
        # Placing one screen opens nothing else.
        r = _component(client, w.staff, w.venue, "roster_editor", "add_shift")
        assert r.status_code == 403

    def test_a_placement_switched_off_opens_nothing(self, w, client, component_calls):
        """The catalog's ``teams`` list alone isn't enough: the placement must
        show for the organisation (screen:<page_id>@<team> not off)."""
        from app.db.models import OrgAppEntitlement

        w.place("recipes-and-costing", "recipe_editor", teams=["kitchen"])
        w.db.add(
            OrgAppEntitlement(
                organization_id=w.org.id,
                app_slug="screen:recipes@kitchen",
                enabled=False,
            )
        )
        w.db.flush()
        r = _component(client, w.staff, w.venue, "recipe_editor", "list_recipes")
        assert r.status_code == 403

    def test_a_placement_in_another_team_app_does_not(self, w, client, component_calls):
        w.place("recipes-and-costing", "recipe_editor", teams=["front_of_house"])
        r = _component(client, w.staff, w.venue, "recipe_editor", "list_recipes")
        assert r.status_code == 403

    def test_a_manager_reaches_every_screen_as_before(self, w, client, component_calls):
        for key, action in self.REFUSED + [("mystery_editor", "load")]:
            r = _component(client, w.manager, w.venue, key, action)
            assert r.status_code == 200, (key, action, r.text)
        assert len(component_calls) == len(self.REFUSED) + 1

    def test_a_screen_no_app_claims_is_not_for_staff(self, w, client, component_calls):
        r = _component(client, w.staff, w.venue, "mystery_editor", "load")
        assert r.status_code == 403

    def test_the_mcp_app_tools_ask_the_same_question(self, w, db_session, monkeypatch):
        """The MCP door into the same actions (mcp/app_tools) refuses a role
        that doesn't reach the screen even with the MCP scope in hand."""
        from app.mcp import app_tools
        from app.mcp.principal import McpPrincipal

        monkeypatch.setattr(
            "app.services.component_api.execute_component_action",
            lambda *a, **k: {"status_code": 200, "data": {"ok": True}},
        )

        def principal(user):
            return McpPrincipal(
                user_id=user.id,
                organization_id=w.org.id,
                venue_ids=(w.venue.id,),
                scopes=frozenset({"mcp:orders:read"}),
            )

        params = {
            "component_key": "purchase_order_editor",
            "action_name": "get_units",
            "venue_id": w.venue.id,
        }
        with pytest.raises(app_tools.AppToolError, match="isn't available"):
            app_tools._component_api(params, principal(w.staff), db_session, db_session)
        out = app_tools._component_api(
            params, principal(w.manager), db_session, db_session
        )
        assert out["data"] == {"ok": True}


# ------------------------------------------------------- the screen routers


@pytest.fixture()
def outbound(monkeypatch, w):
    """Every outbound call the screen routers make, replaced."""
    from app.routers import (
        invoice_fixes,
        recipe_editor,
        stock_catalogue,
        supplier_tenders,
    )

    monkeypatch.setattr(invoice_fixes, "_Loaded", lambda *a, **k: None)
    monkeypatch.setattr(invoice_fixes.catalog, "units", lambda lh: [])
    monkeypatch.setattr(recipe_editor, "save_recipe", lambda *a, **k: {"saved": True})
    monkeypatch.setattr(
        "app.services.menu_engineering.product_recipe_links", lambda *a, **k: {}
    )
    monkeypatch.setattr(stock_catalogue, "_venue", lambda *a, **k: w.venue)
    monkeypatch.setattr(
        "app.connectors.tool_executor.execute_connector_tool",
        lambda *a, **k: SimpleNamespace(
            success=True, payload={"items": []}, error=None
        ),
    )
    monkeypatch.setattr(supplier_tenders, "_call", lambda *a, **k: [])
    monkeypatch.setattr(
        "app.connectors.spec_executor.execute_spec",
        lambda *a, **k: (
            SimpleNamespace(
                success=True, response_payload={"rows": []}, error_message=None
            ),
            None,
        ),
    )
    modes.set_mode(w.db, w.org.id, "entertainers", "live")
    w.db.flush()


def _routes(w):
    """(method, path, json) for one route of every router gated here. A
    manager gets 200 from each (the outbound fixture answers for Loaded)."""
    v = w.venue.id
    return [
        ("GET", f"/api/invoice-fixes/units?venue_id={v}", None),
        ("POST", "/api/recipe-editor/save", {"venue_id": v, "recipe": {}}),
        (
            "POST",
            "/api/menu-engineering/recipe-links",
            {"venue_id": v, "start": "2026-10-01", "end": "2026-10-07"},
        ),
        ("GET", f"/api/stock-catalogue?venue_id={v}", None),
        ("POST", "/api/supplier-tenders/list", {"venue_id": v}),
        ("GET", "/api/reports", None),
        ("POST", "/api/connectors/lh/execute/get_sales", {"params": {"venue_id": v}}),
        ("POST", "/api/connectors/lh/execute/get_staff", {"params": {"venue_id": v}}),
        (
            "POST",
            "/api/working-documents/from-connector",
            {"connector_name": "lh", "action": "get_staff", "venue_id": v},
        ),
        ("GET", f"/api/organizations/{w.org.id}/usage", None),
        ("GET", f"/api/organizations/{w.org.id}/usage/daily", None),
        ("GET", f"/api/organizations/{w.org.id}/usage/breakdown", None),
        ("GET", "/api/functions/meta", None),
        ("GET", "/api/functions/inbox/mailboxes", None),
        ("GET", "/api/functions/forms", None),
        ("GET", "/api/entertainers/meta", None),
        (
            "POST",
            "/api/automated-tasks",
            {"title": "Daily sales", "agent_slug": "reports", "prompt": "Sales?"},
        ),
    ]


def _call(client, method, path, body, user):
    return client.request(method, path, json=body, headers=_h(user))


class TestScreenRouters:
    def test_venue_staff_are_refused(self, w, client, outbound):
        for method, path, body in _routes(w):
            r = _call(client, method, path, body, w.staff)
            assert r.status_code == 403, (path, r.status_code, r.text)

    def test_a_manager_keeps_every_one(self, w, client, outbound):
        for method, path, body in _routes(w):
            r = _call(client, method, path, body, w.manager)
            assert r.status_code == 200, (path, r.status_code, r.text)

    def test_the_writes_and_files_are_refused_before_anything_runs(
        self, w, client, outbound
    ):
        """Page writes, the BambooHR file proxy and the recipe extraction:
        refused for staff; a manager gets past the role check to the route's
        own answer (a read isn't a write; no BambooHR; no such upload)."""
        v = w.venue.id
        cases = [
            ("POST", "/api/connectors/lh/execute/get_sales/preview", {"venue_id": v}),
            ("POST", "/api/connectors/lh/execute/get_sales/confirm", {"venue_id": v}),
            ("GET", "/api/connectors/bamboohr/files/123", None),
            ("POST", "/api/uploads/no-such-upload/extract-recipe", None),
        ]
        for method, path, body in cases:
            assert _call(client, method, path, body, w.staff).status_code == 403, path
            assert _call(client, method, path, body, w.manager).status_code in (
                400,
                404,
            ), path

    def test_a_tool_the_kitchen_team_app_lists_is_open_to_kitchen(
        self, w, client, outbound
    ):
        r = _call(
            client,
            "POST",
            "/api/connectors/lh/execute/get_recipes",
            {"params": {"venue_id": w.venue.id}},
            w.staff,
        )
        assert r.status_code == 200, r.text


class TestReports:
    def test_a_role_that_reaches_saved_reports_still_cant_chart_sales(
        self, w, client, db_session
    ):
        """Saved Reports opens the router; each chart's tool is checked again
        when it runs, so a report is no way round the chat's tool list."""
        from app.db.models import Report, ReportChart

        reporter = _role(
            db_session, w.org, "reporter", agents=[], apps=["saved-reports"]
        )
        person = _person(db_session, w.org, reporter, name="Rae Reports")
        report = Report(user_id=person.id, title="Mine", organization_id=w.org.id)
        db_session.add(report)
        db_session.flush()
        chart = ReportChart(
            report_id=report.id,
            title="Sales",
            chart_type="bar",
            script={"connector": "lh", "action": "get_sales", "params": {}},
        )
        db_session.add(chart)
        db_session.flush()
        assert client.get("/api/reports", headers=_h(person)).status_code == 200
        r = client.post(
            f"/api/reports/{report.id}/charts/{chart.id}/test", headers=_h(person)
        )
        assert r.status_code == 403
        r = client.post(f"/api/reports/{report.id}/refresh", headers=_h(person))
        assert r.status_code == 200
        assert r.json()["refresh_errors"][0]["error"] == screen_access.REFUSED_TOOL


class TestSalesAnalysis:
    def test_the_revenue_group_routes_are_the_screens(self, w, db_session, monkeypatch):
        from app.routers import sales_analysis

        app = FastAPI()
        app.include_router(sales_analysis.router, prefix="/api")
        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_config_db] = lambda: db_session
        monkeypatch.setattr(sales_analysis, "_venue", lambda *a, **k: w.venue)
        monkeypatch.setattr(
            sales_analysis, "_tool_def", lambda *a, **k: {"action": "set_revenue_group"}
        )
        monkeypatch.setattr(
            "app.services.previews.preview_call",
            lambda *a, **k: SimpleNamespace(kind="done", result={"data": "nothing"}),
        )
        body = {"venue_id": w.venue.id, "revenue_group": "Food", "items": ["Fries"]}
        with TestClient(app) as c:
            path = "/api/sales-analysis/revenue-group/preview"
            assert c.post(path, json=body, headers=_h(w.staff)).status_code == 403
            r = c.post(path, json=body, headers=_h(w.manager))
            assert r.status_code == 200, r.text


# ------------------------------------------------- threads and scheduled tasks


def _task_with_threads(db, w):
    conv = Thread(
        user_id=w.manager.id,
        domain="reports",
        intent="reports.automated_conversation",
        status="awaiting_tool_approval",
        raw_prompt="Weekly wages",
        extracted_fields={},
        missing_fields=[],
    )
    db.add(conv)
    db.flush()
    task = AutomatedTask(
        title="Weekly wages",
        agent_slug="reports",
        prompt="Wages against sales",
        created_by=w.manager.id,
        organization_id=w.org.id,
        conversation_thread_id=conv.id,
    )
    db.add(task)
    db.flush()
    return task, conv


class TestThreadsAndTasks:
    def test_staff_cant_open_or_act_in_a_managers_scheduled_task(
        self, w, client, db_session
    ):
        task, conv = _task_with_threads(db_session, w)
        h = _h(w.staff)
        assert client.get(f"/api/threads/{conv.id}", headers=h).status_code == 404
        assert (
            client.post(f"/api/threads/{conv.id}/approve", headers=h).status_code == 404
        )
        assert (
            client.get(f"/api/automated-tasks/{task.id}", headers=h).status_code == 404
        )
        listed = client.get("/api/automated-tasks", headers=h).json()["tasks"]
        assert task.id not in {t["id"] for t in listed}
        r = client.post(f"/api/automated-tasks/{task.id}/run", json={}, headers=h)
        assert r.status_code == 404

    def test_the_managers_colleagues_still_share_it(self, w, client, db_session):
        task, conv = _task_with_threads(db_session, w)
        h = _h(w.manager2)
        assert client.get(f"/api/threads/{conv.id}", headers=h).status_code == 200
        assert (
            client.get(f"/api/automated-tasks/{task.id}", headers=h).status_code == 200
        )
        listed = client.get("/api/automated-tasks", headers=h).json()["tasks"]
        assert task.id in {t["id"] for t in listed}

    def test_a_widget_runs_only_a_tool_the_role_reaches(
        self, w, client, db_session, monkeypatch
    ):
        from app.agents import tool_loop

        monkeypatch.setattr(
            tool_loop,
            "_find_tool_def",
            lambda *a, **k: {"action": "get_sales", "method": "GET"},
        )
        monkeypatch.setattr(
            tool_loop, "_execute_tool_call", lambda *a, **k: {"rows": []}
        )
        for user, expected in ((w.staff, 403), (w.manager, 200)):
            thread = Thread(
                user_id=user.id,
                domain="norm",
                status="completed",
                raw_prompt="hi",
                extracted_fields={},
                missing_fields=[],
            )
            db_session.add(thread)
            db_session.flush()
            r = client.post(
                f"/api/threads/{thread.id}/widget-action",
                json={"connector_name": "lh", "action": "get_sales", "params": {}},
                headers=_h(user),
            )
            assert r.status_code == expected, r.text


# ------------------------------------------------------- organisation data


class TestOrganisation:
    def test_staff_dont_get_the_member_list(self, w, client):
        path = f"/api/organizations/{w.org.id}"
        staff_view = client.get(path, headers=_h(w.staff))
        assert staff_view.status_code == 200
        assert staff_view.json()["members"] == []
        manager_view = client.get(path, headers=_h(w.manager)).json()
        assert {m["user_id"] for m in manager_view["members"]} >= {w.staff.id}


@pytest.fixture()
def own_client(db_session):
    """Memories and background jobs aren't on the shared test app."""
    from app.routers import background_jobs, memories

    app = FastAPI()
    app.include_router(memories.router, prefix="/api")
    app.include_router(background_jobs.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_config_db] = lambda: db_session
    with TestClient(app) as c:
        yield c


class TestOwnOnly:
    def test_staff_see_and_change_only_their_own_memories(
        self, w, own_client, db_session
    ):
        org_rule = Memory(
            scope="org",
            organization_id=w.org.id,
            type="context",
            title="Wages target",
            body="Wages run at 30% of sales.",
            status="candidate",
        )
        mine = Memory(
            scope="user",
            organization_id=w.org.id,
            user_id=w.staff.id,
            type="preference",
            title="Short answers",
            body="Keep it short.",
            status="active",
        )
        db_session.add_all([org_rule, mine])
        db_session.flush()
        staff_list = own_client.get("/api/memories", headers=_h(w.staff)).json()
        assert [m["id"] for m in staff_list] == [mine.id]
        r = own_client.post(f"/api/memories/{org_rule.id}/approve", headers=_h(w.staff))
        assert r.status_code == 404
        r = own_client.post(
            f"/api/memories/{org_rule.id}/approve", headers=_h(w.manager)
        )
        assert r.status_code == 200

    def test_a_colleagues_venue_job_is_the_managers(self, w, own_client, db_session):
        job = BackgroundJob(
            kind="receive_invoices",
            user_id=w.manager.id,
            venue_id=w.venue.id,
            organization_id=w.org.id,
        )
        db_session.add(job)
        db_session.flush()
        assert (
            own_client.get(f"/api/jobs/{job.id}", headers=_h(w.staff)).status_code
            == 404
        )
        r = own_client.post(f"/api/jobs/{job.id}/cancel", headers=_h(w.staff))
        assert r.status_code == 404
        assert (
            own_client.get(f"/api/jobs/{job.id}", headers=_h(w.manager2)).status_code
            == 200
        )


class TestWorkingDocuments:
    def test_a_venues_roster_document_needs_the_roster_screen(
        self, w, client, db_session
    ):
        doc = WorkingDocument(
            doc_type="roster", connector_name="lh", venue_id=w.venue.id, data={}
        )
        db_session.add(doc)
        db_session.flush()
        path = f"/api/working-documents/{doc.id}"
        assert client.get(path, headers=_h(w.staff)).status_code == 403
        r = client.patch(path, json={"ops": [], "version": 1}, headers=_h(w.staff))
        assert r.status_code == 403
        assert client.get(path, headers=_h(w.manager)).status_code == 200

    def test_a_document_type_is_one_the_role_may_edit_even_in_its_own_thread(
        self, w, client, db_session, outbound
    ):
        """From-connector took any doc_type, and a document in the caller's
        own thread skipped the screen check, so a buyer (Procurement only)
        could load a read as a "roster" and patch shifts into Loaded."""
        buyer = _person(
            db_session,
            w.org,
            _role(db_session, w.org, "buyer", agents=["procurement"], apps=[]),
            name="Bea Buyer",
        )
        grant_venue(db_session, buyer, w.venue)
        thread = Thread(
            user_id=buyer.id,
            venue_id=w.venue.id,
            domain="procurement",
            intent="procurement.tool_use",
            status="completed",
            raw_prompt="order",
            extracted_fields={},
            missing_fields=[],
        )
        db_session.add(thread)
        db_session.flush()
        load = {
            "connector_name": "lh",
            "action": "get_staff",
            "doc_type": "roster",
            "venue_id": w.venue.id,
            "thread_id": thread.id,
        }
        r = client.post(
            "/api/working-documents/from-connector", json=load, headers=_h(buyer)
        )
        assert r.status_code == 403, r.text
        # an order is the buyer's to load
        r = client.post(
            "/api/working-documents/from-connector",
            json={**load, "doc_type": "order"},
            headers=_h(buyer),
        )
        assert r.status_code == 200, r.text
        # a roster document already in the buyer's own thread is refused too
        doc = WorkingDocument(
            thread_id=thread.id,
            doc_type="roster",
            connector_name="lh",
            venue_id=w.venue.id,
            data={},
        )
        db_session.add(doc)
        db_session.flush()
        path = f"/api/threads/{thread.id}/working-documents/{doc.id}"
        r = client.patch(path, json={"ops": [], "version": 1}, headers=_h(buyer))
        assert r.status_code == 403, r.text
        # a manager is unchanged
        r = client.post(
            "/api/working-documents/from-connector",
            json={**load, "thread_id": None},
            headers=_h(w.manager),
        )
        assert r.status_code == 200, r.text

    def test_a_screen_placed_in_a_team_app_loads_for_staff(
        self, w, client, db_session, outbound
    ):
        """The Roster page loads through loadedhub.get_roster, an API
        endpoint no App claims, so venue staff were refused it even with the
        Rosters screen placed in their team app. The screen's own load
        passes for a role that reaches the screen; any other read does not."""
        _tool_spec(db_session, "loadedhub", ["get_roster"])
        db_session.add(
            Connection(
                connector_name="loadedhub",
                venue_id=w.venue.id,
                enabled="true",
                config={},
            )
        )
        db_session.flush()
        load = {
            "connector_name": "loadedhub",
            "action": "get_roster",
            "doc_type": "roster",
            "venue_id": w.venue.id,
        }

        def post(body):
            return client.post(
                "/api/working-documents/from-connector", json=body, headers=_h(w.staff)
            )

        assert post(load).status_code == 403
        w.place("rosters", "roster_editor", teams=["kitchen"])
        r = post(load)
        assert r.status_code == 200, r.text
        assert r.json()["doc_type"] == "roster"
        # not a back door to another read under the same document type
        other = {**load, "connector_name": "lh", "action": "get_staff"}
        assert post(other).status_code == 403

    def test_an_ai_client_cant_edit_a_draft_whose_screen_the_role_lacks(
        self, w, db_session
    ):
        """The MCP draft tools checked only that the caller owns the thread."""
        from app.mcp.app_tools import AppToolError, execute_app_tool
        from app.mcp.principal import McpPrincipal

        def draft_of(user):
            thread = Thread(
                user_id=user.id,
                venue_id=w.venue.id,
                domain="procurement",
                intent="procurement.tool_use",
                status="completed",
                raw_prompt="order",
                extracted_fields={},
                missing_fields=[],
            )
            db_session.add(thread)
            db_session.flush()
            doc = WorkingDocument(
                thread_id=thread.id,
                doc_type="order",
                connector_name="norm",
                venue_id=w.venue.id,
                data={"lines": []},
            )
            db_session.add(doc)
            db_session.flush()
            principal = McpPrincipal(
                user_id=user.id,
                organization_id=w.org.id,
                venue_ids=(w.venue.id,),
                scopes=frozenset({"mcp:orders:read", "mcp:orders:draft"}),
            )
            return {"working_document_id": doc.id}, principal

        params, principal = draft_of(w.staff)
        with pytest.raises(AppToolError):
            execute_app_tool(
                "norm__get_working_document", params, principal, db_session, db_session
            )
        params, principal = draft_of(w.manager)
        out = execute_app_tool(
            "norm__get_working_document", params, principal, db_session, db_session
        )
        assert out["id"] == params["working_document_id"]

    def test_page_loads_mirror_the_page_registry(self):
        """screen_access.PAGE_LOADS is the server's copy of each functional
        page's connector load (pageRegistry.ts); a page whose load changes
        there must change here, or a placed screen stops loading for staff."""
        import pathlib
        import re

        registry = (
            pathlib.Path(__file__).resolve().parents[2]
            / "web/app/components/pages/pageRegistry.ts"
        )
        if not registry.exists():
            pytest.skip("the web app is not in this checkout")
        found = {}
        for block in registry.read_text().split("\n  {")[1:]:
            page = re.search(r"\bid: '([^']+)'", block)
            load = re.search(
                r"loadAction:\s*\{\s*connector: '([^']+)',\s*action: '([^']+)'",
                block,
            )
            if page and load and load.group(1) not in ("_none", "norm"):
                found[page.group(1)] = (load.group(1), load.group(2))
        assert found == screen_access.PAGE_LOADS


class TestVenueConnections:
    """Settings → Venues lists every venue of the organisation and asks each
    one's connections; only the venue's creator has an access row for it."""

    def test_a_manager_sees_a_venue_of_their_organisation_without_a_row(
        self, w, client, db_session
    ):
        other = make_venue(db_session, "Lott Cafe", w.org)
        stranger_org = _make_organization(db_session, name="Elsewhere")
        stranger = _person(db_session, stranger_org, w.roles["manager"], name="Ola")
        for path in (
            f"/api/connectors?venue_id={other.id}",
            f"/api/oauth/status/lh?venue_id={other.id}",
        ):
            assert client.get(path, headers=_h(w.manager)).status_code == 200, path
            # venue staff, and a manager of another organisation, are refused
            assert client.get(path, headers=_h(w.staff)).status_code == 403, path
            assert client.get(path, headers=_h(stranger)).status_code == 403, path


# ---------------------------------------------------------------- tablets


def _tablet(db, w):
    from datetime import timedelta

    from app.auth.security import create_access_token

    device_user = _person(db, w.org, w.kitchen, name="La Zeppa kitchen")
    device_user.is_device = True
    device = VenueDevice(
        organization_id=w.org.id,
        venue_id=w.venue.id,
        name="La Zeppa kitchen",
        user_id=device_user.id,
    )
    db.add(device)
    db.flush()
    device_token = create_access_token(
        {"sub": device_user.id, "type": "device", "device": device.id},
        expires_delta=timedelta(days=1),
    )
    pin_token = create_access_token(
        {"sub": w.staff.id, "type": "pin", "device": device.id},
        expires_delta=timedelta(minutes=10),
    )
    return device, device_token, pin_token


class TestTablets:
    def test_a_tablet_cant_connect_an_ai_client_or_talk_to_a_task(
        self, w, client, db_session
    ):
        task, _ = _task_with_threads(db_session, w)
        _, device_token, pin_token = _tablet(db_session, w)
        consent = {
            "client_id": "c",
            "redirect_uri": "https://claude.ai/cb",
            "scope": "mcp:venues:read",
            "code_challenge": "x" * 43,
            "organization_id": w.org.id,
        }
        for token in (device_token, pin_token):
            h = {"Authorization": f"Bearer {token}"}
            r = client.post("/api/mcp/oauth/consent", json=consent, headers=h)
            assert r.status_code == 403, r.text
            r = client.post(
                f"/api/automated-tasks/{task.id}/message",
                json={"message": "hi"},
                headers=h,
            )
            assert r.status_code == 403, r.text

    def test_signing_the_tablet_out_ends_its_pin_sessions(self, w, client, db_session):
        from datetime import datetime, timezone

        device, _, pin_token = _tablet(db_session, w)
        h = {"Authorization": f"Bearer {pin_token}"}
        assert client.get("/api/auth/me", headers=h).status_code == 200
        device.revoked_at = datetime.now(timezone.utc)
        db_session.flush()
        assert client.get("/api/auth/me", headers=h).status_code == 401


# ------------------------------------------------------------ app runtime


class TestAppRuntime:
    def test_an_app_cant_hand_staff_an_endpoint_no_app_claims(self, w, db_session):
        from app.services.app_runtime import _check_role_reach

        with pytest.raises(HTTPException) as refused:
            _check_role_reach(db_session, db_session, w.staff, "lh", "get_staff")
        assert refused.value.status_code == 403
        _check_role_reach(db_session, db_session, w.manager, "lh", "get_staff")
        # The Kitchen team app's own tool still passes.
        _check_role_reach(db_session, db_session, w.staff, "lh", "get_recipes")


def test_screen_access_unit_rules(w, db_session):
    """The rule itself, once: a team placement opens one screen, Norm Core is
    never venue staff's, a platform admin reaches everything."""
    from tests.conftest import _make_user

    admin = _make_user(db_session, role="admin")
    assert screen_access.component_reachable(
        db_session, db_session, admin, "roster_editor"
    )
    assert not screen_access.component_reachable(
        db_session, db_session, w.staff, "dashboard_view"
    )
    assert screen_access.component_reachable(
        db_session, db_session, w.manager, "dashboard_view"
    )
    assert not screen_access.app_reachable(db_session, db_session, w.staff, "rosters")
    w.place("rosters", "roster_editor", teams=["kitchen"])
    assert screen_access.app_reachable(db_session, db_session, w.staff, "rosters")
    assert screen_access.catalog_app_reachable(
        db_session, db_session, w.staff, "rosters"
    )
    assert not screen_access.app_reachable(
        db_session, db_session, w.staff, "custom:rosters"
    )
    assert screen_access.doc_reachable(db_session, db_session, w.staff, "roster")
    assert not screen_access.doc_reachable(db_session, db_session, w.staff, "order")
    assert screen_access.tool_reachable(
        db_session, db_session, w.staff, "lh", "get_recipes"
    )
    assert not screen_access.tool_reachable(
        db_session, db_session, w.staff, "lh", "get_sales"
    )


# ------------------------------------------------- a built-in in a team app


class TestBuiltInInATeamApp:
    """Training (a built-in, fronted by the catalog row ``training``) placed in
    the Kitchen team app opens for a Kitchen role while that placement shows:
    the App on, the placement switched on. The chat's tools are not widened by
    it (tool_reachable is unchanged)."""

    @pytest.fixture()
    def t(self, w, db_session):
        _row(db_session, "hr-agent", "agent", {"owns_agents": ["hr"]})
        _row(db_session, "front_of_house", "team", {"tagline": "The floor."})
        _row(
            db_session,
            "training",
            "app",
            {
                "member": "hr",
                "app_slug": "training",
                "components": [_screen("training", "training", teams=["kitchen"])],
            },
        )
        self.db, self.org = db_session, w.org
        return w

    def _sees(self, client, user) -> bool:
        listed = client.get("/api/apps", headers=_h(user)).json()["apps"]
        return "training" in {a["slug"] for a in listed}

    def _opens(self, client, user) -> int:
        return client.get("/api/apps/training", headers=_h(user)).status_code

    def _runs(self, client, user) -> int:
        """Run its logic: read one content item (a venue staff member's own
        view allows that op)."""
        from app.db.models import AppRecord

        db = self.db
        row = AppRecord(
            namespace="hr_suite",
            organization_id=self.org.id,
            collection="content",
            data={"title": "Knife safety", "body": {"html": "<p>Hold it so.</p>"}},
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(row)
        db.flush()
        return client.post(
            "/api/apps/training/run",
            json={"params": {"op": "content", "content_id": row.id}},
            headers=_h(user),
        ).status_code

    def _switch_off(self, db, org, key):
        from app.db.models import OrgAppEntitlement

        db.add(OrgAppEntitlement(organization_id=org.id, app_slug=key, enabled=False))
        db.flush()

    def test_kitchen_staff_get_and_run_it_while_the_placement_shows(self, t, client):
        assert self._sees(client, t.staff)
        assert self._opens(client, t.staff) == 200
        assert self._runs(client, t.staff) == 200

    def test_refused_when_the_placement_is_switched_off(self, t, client, db_session):
        self._switch_off(db_session, t.org, "screen:training@kitchen")
        assert not self._sees(client, t.staff)
        assert self._opens(client, t.staff) == 403
        assert self._runs(client, t.staff) == 403
        # The manager reaches it through its AI agent, as before.
        assert self._sees(client, t.manager)
        assert self._opens(client, t.manager) == 200

    def test_refused_when_the_app_is_off(self, t, client, db_session):
        """With the App off the built-in isn't there for anyone (404, as
        before: no difference is disclosed) and its placement shows nowhere."""
        self._switch_off(db_session, t.org, "training")
        assert not self._sees(client, t.staff)
        assert self._opens(client, t.staff) == 404
        assert not screen_access.app_reachable(
            db_session, db_session, t.staff, "training", member="hr"
        )
        assert not self._sees(client, t.manager)

    def test_refused_when_placed_only_in_front_of_house(self, t, client, db_session):
        t.place("training", "training", teams=["front_of_house"])
        assert not self._sees(client, t.staff)
        assert self._opens(client, t.staff) == 403

    def test_a_manager_is_unchanged(self, t, client):
        assert self._sees(client, t.manager)
        assert self._opens(client, t.manager) == 200
        assert self._runs(client, t.manager) == 200

    def test_open_app_offers_it_on_the_same_rule(self, t, db_session, monkeypatch):
        from app.agents.internal_tools import _open_app

        monkeypatch.setattr(
            "app.db.engine._ConfigSessionLocal", lambda: _Unclosed(db_session)
        )
        thread = Thread(
            user_id=t.staff.id,
            domain="norm",
            status="completed",
            raw_prompt="hi",
            extracted_fields={},
            missing_fields=[],
        )
        db_session.add(thread)
        db_session.flush()
        out = _open_app({"app": "training"}, db_session, thread.id)
        assert out["success"], out
        self._switch_off(db_session, t.org, "screen:training@kitchen")
        out = _open_app({"app": "training"}, db_session, thread.id)
        assert not out["success"]

    def test_the_placement_never_widens_the_chat_tools(self, t, db_session):
        from app.services.entitlements import tool_users

        assert "lh.get_sales" in tool_users(db_session)
        assert not screen_access.tool_reachable(
            db_session, db_session, t.staff, "lh", "get_sales"
        )


class _Unclosed:
    """The test session, handed out where code opens (and closes) its own
    config session."""

    def __init__(self, session):
        self._s = session

    def __getattr__(self, name):
        return getattr(self._s, name)

    def close(self):
        pass
