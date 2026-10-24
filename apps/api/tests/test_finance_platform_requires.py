"""``requires: [...]`` on a tool row — enforced at every door (Finance Phase 0,
plan §5.2 item 2, §17). A payroll tool is not in a manager's menu, not in the
MCP projection, and refused if named anyway: in chat, through the page
execute route, from a saved chart and from a self-built app. Nobody known
(a job, a script) is refused too. ``norm.caller_holds`` is the engine's own
way to ask the same question.

Non-admin users throughout (a platform admin holds every scope)."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.agents import tool_loop as TL
from app.agents.prompt_builder import _collect_tools
from app.agents.tool_loop import _execute_tool_call
from app.connectors.tool_executor import execute_connector_tool
from app.db.config_models import (
    AgentConnectionBinding,
    ConnectionSpec,
    MarketplaceApp,
    McpCapability,
)
from app.db.models import App, AppVersion, Connection, Report, ReportChart, ToolCall
from app.mcp.projection import project_tools
from app.services import agent_catalog, app_runtime, caller_scope
from tests.conftest import _make_membership, _make_thread
from tests.org_helpers import (
    auth_headers,
    grant_venue,
    make_org,
    make_user,
    make_venue,
    seed_system_roles,
)

CONNECTOR = "payspec"
READS = "def run(p, call_api, log):\n    return {'rows': [1, 2]}\n"
WRITES = "def run(p, call_api, log):\n    return {'written': True}\n"


@pytest.fixture(autouse=True)
def _fresh_cache():
    agent_catalog.invalidate_cache()
    yield
    agent_catalog.invalidate_cache()


def _tool(action, code, **extra):
    return {
        "action": action,
        "method": "GET",
        "description": f"{action} thing.",
        "effect": extra.pop("effect", "read"),
        "consolidator_config": {"function_code": code, "max_api_calls": 2},
        **extra,
    }


@pytest.fixture()
def world(db_session):
    """One organisation, the four system roles, a venue everyone holds, and a
    spec with a payroll read (payroll:read), a payroll write (payroll:write)
    and a plain read — all claimed by one App that is on."""
    roles = seed_system_roles(db_session)
    org = make_org(db_session, "Cook Brothers")
    venue = make_venue(db_session, "La Zeppa", org)
    people = {}
    for name in ("owner", "manager", "team_member", "payroll_admin"):
        u = make_user(db_session)
        m = _make_membership(db_session, u, org)
        m.role_id = roles[name].id
        grant_venue(db_session, u, venue)
        people[name] = u
    db_session.add(
        ConnectionSpec(
            connector_name=CONNECTOR,
            display_name=CONNECTOR,
            auth_type="none",
            execution_mode="internal",
            enabled=True,
            tools=[
                _tool("get_payrun", READS, requires=["payroll:read"]),
                _tool("get_thing", READS),
                _tool(
                    "set_payrun",
                    WRITES,
                    effect="write",
                    requires=["payroll:write"],
                    approval={"default": "auto", "label": "Set the payrun"},
                ),
            ],
        )
    )
    db_session.add(Connection(connector_name=CONNECTOR, enabled="true", config={}))
    # The engine-only built-in's own row, as scripts/sync_norm_finance.py
    # installs it: the executor finds a handler through its spec row.
    db_session.add(
        ConnectionSpec(
            connector_name="norm",
            display_name="Norm",
            auth_type="none",
            execution_mode="internal",
            enabled=True,
            tools=[
                {
                    "action": "caller_holds",
                    "method": "GET",
                    "engine_only": True,
                    "description": "Does the person acting hold an org scope?",
                    "field_schema": {"scope": {"type": "string"}},
                }
            ],
        )
    )
    db_session.add(
        AgentConnectionBinding(
            agent_slug="finance",
            connector_name=CONNECTOR,
            capabilities=[
                {"action": a, "enabled": True, "label": a}
                for a in ("get_payrun", "get_thing", "set_payrun")
            ],
            enabled=True,
        )
    )
    db_session.add(
        MarketplaceApp(
            slug="finance-agent",
            name="Finance",
            description="",
            tier="agent",
            bundled=True,
            composition={"owns_agents": ["finance"]},
        )
    )
    # A stand-in App, not the real `payroll`: that one is dropped while
    # the organisation's payroll mode is off (the mode
    # rule, tests/test_finance_platform_links_files.py), which is a different
    # door from the one tested here.
    db_session.add(
        MarketplaceApp(
            slug="pay-app",
            name="Pay app",
            description="",
            tier="app",
            bundled=True,
            composition={
                "member": "finance",
                "tools": [
                    f"{CONNECTOR}.{a}"
                    for a in ("get_payrun", "get_thing", "set_payrun")
                ],
            },
        )
    )
    db_session.flush()
    return SimpleNamespace(org=org, venue=venue, roles=roles, **people)


def _menu(db, user) -> set[str]:
    return {
        t["action"]
        for t in _collect_tools(db, user_id=user.id, config_db=db)
        if t["connector"] == CONNECTOR
    }


class TestMenus:
    def test_a_managers_thread_menu_omits_the_payroll_tools(self, db_session, world):
        assert _menu(db_session, world.manager) == {"get_thing"}
        # The system Team Member is venue staff (staff access, Oct 2026): a
        # role with no AI agent reaches no tool at all, so `requires` never
        # gets a turn (test_staff_exposure has the role rules).
        assert _menu(db_session, world.team_member) == set()

    def test_an_owner_and_a_payroll_admin_see_them(self, db_session, world):
        assert _menu(db_session, world.owner) == {
            "get_payrun",
            "get_thing",
            "set_payrun",
        }
        assert "get_payrun" in _menu(db_session, world.payroll_admin)

    def test_no_person_no_requires_rows(self, db_session, world):
        """A system prompt built with no user never carries a gated tool."""
        assert "get_payrun" not in {
            t["action"]
            for t in _collect_tools(db_session, user_id=None, config_db=db_session)
        }

    def test_the_mcp_projection_omits_it_too(self, db_session, world):
        for action, scope in (
            ("get_payrun", "mcp:payroll:read"),
            ("get_thing", "mcp:reports:read"),
        ):
            db_session.add(
                McpCapability(
                    kind="connector",
                    target=CONNECTOR,
                    action=action,
                    scopes=[scope],
                    enabled=True,
                )
            )
        db_session.flush()
        granted = {"mcp:payroll:read", "mcp:reports:read"}

        def names(user):
            return {
                t.action
                for t in project_tools(
                    db_session,
                    db_session,
                    user_id=user.id,
                    granted_scopes=granted,
                    venue_names=["La Zeppa"],
                )
                if t.connector == CONNECTOR
            }

        # The token holds mcp:payroll:read — the person's role still decides.
        assert names(world.manager) == {"get_thing"}
        assert names(world.owner) == {"get_payrun", "get_thing"}


class TestDoors:
    def test_the_execute_route_refuses_a_manager_and_serves_an_owner(
        self, client, db_session, world
    ):
        def call(user):
            return client.post(
                f"/api/connectors/{CONNECTOR}/execute/get_payrun",
                json={"params": {"venue_id": world.venue.id}},
                headers=auth_headers(user),
            )

        refused = call(world.manager)
        assert refused.status_code == 200 and refused.json()["success"] is False
        assert "permission" in refused.json()["error"]
        served = call(world.owner)
        assert served.json()["success"] is True
        assert served.json()["data"]["rows"] == [1, 2]

    def test_a_team_members_chart_is_refused(self, client, db_session, world):
        def chart_for(user):
            report = Report(
                id=str(uuid.uuid4()),
                user_id=user.id,
                organization_id=world.org.id,
                venue_id=world.venue.id,
                title="Pay",
            )
            db_session.add(report)
            db_session.flush()
            chart = ReportChart(
                id=str(uuid.uuid4()),
                report_id=report.id,
                title="Payrun",
                chart_type="bar",
                chart_spec={},
                script={"connector": CONNECTOR, "action": "get_payrun", "params": {}},
            )
            db_session.add(chart)
            db_session.flush()
            return report, chart

        report, chart = chart_for(world.team_member)
        out = client.post(
            f"/api/reports/{report.id}/charts/{chart.id}/refresh",
            json={},
            headers=auth_headers(world.team_member),
        )
        assert out.status_code == 200
        assert "permission" in out.json().get("error", "")

        report, chart = chart_for(world.owner)
        out = client.post(
            f"/api/reports/{report.id}/charts/{chart.id}/refresh",
            json={},
            headers=auth_headers(world.owner),
        )
        assert out.status_code == 200 and not out.json().get("error")

    def test_a_self_built_app_is_refused_for_its_author_without_the_scope(
        self, db_session, world
    ):
        """The app's declared scopes are a ceiling, not a key: a team member's
        own app calling a payroll tool is refused for them and runs for an
        owner it is shared with."""

        def run_as(author, user):
            app = App(
                organization_id=world.org.id,
                created_by=author.id,
                slug=f"app-{uuid.uuid4().hex[:6]}",
                name="Pay app",
                visibility="private",
            )
            db_session.add(app)
            db_session.flush()
            version = AppVersion(
                app_id=app.id,
                version=1,
                spec={
                    "actions": [{"connector": CONNECTOR, "action": "get_payrun"}],
                    "scopes": ["mcp:reports:read"],
                },
                created_by=author.id,
            )
            db_session.add(version)
            db_session.flush()
            app.current_version_id = version.id
            db_session.flush()
            return app_runtime.call_action(
                db_session,
                db_session,
                app=app,
                version=version,
                user=user,
                venue_id=world.venue.id,
                connector=CONNECTOR,
                action="get_payrun",
                params={},
            )

        # A manager holds reports:read (the app's scope) but not payroll:read
        # (D8) — the one missing piece is the tool's own `requires`. (The
        # system Team Member is venue staff since staff access and holds
        # neither, so it would be refused on the app's scope first.)
        with pytest.raises(HTTPException) as refused:
            run_as(world.manager, world.manager)
        assert refused.value.status_code == 403
        assert (
            "permission" in refused.value.detail
            and "payroll:read" in refused.value.detail
        )
        served = run_as(world.owner, world.owner)  # the door returns the payload
        assert served["rows"] == [1, 2]

    def _tool_call(self, db, thread, action):
        tc = ToolCall(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            iteration=1,
            tool_name=f"{CONNECTOR}__{action}",
            connector_name=CONNECTOR,
            action=action,
            method="GET",
            status="executing",
            input_params={},
        )
        db.add(tc)
        db.flush()
        return tc

    def test_a_manager_in_chat_cannot_run_a_payroll_write(self, db_session, world):
        thread = _make_thread(db_session, world.manager, domain="norm")
        tc = self._tool_call(db_session, thread, "set_payrun")
        out = _execute_tool_call(tc, db_session, db_session)
        assert out["success"] is False and "payroll:write" in out["error"]
        assert tc.status == "failed" and "permission" in tc.error_message

        thread = _make_thread(db_session, world.owner, domain="norm")
        tc = self._tool_call(db_session, thread, "set_payrun")
        out = _execute_tool_call(tc, db_session, db_session)
        assert out["success"] is True and tc.status == "executed"
        # The loop stamps the venue a result belongs to (main, Oct 2026).
        assert out["data"]["written"] is True

    def test_a_manager_in_chat_still_runs_a_plain_tool(self, db_session, world):
        thread = _make_thread(db_session, world.manager, domain="norm")
        tc = self._tool_call(db_session, thread, "get_thing")
        assert _execute_tool_call(tc, db_session, db_session)["success"] is True

    def test_nobody_known_is_refused(self, db_session, world):
        """No thread, no user — a job or a script: pay data never leaves on
        nobody's authority, even with the venue named."""
        out = execute_connector_tool(
            CONNECTOR, "get_payrun", {}, db_session, db_session
        )
        assert out.success is False and "who is asking" in out.error
        out = execute_connector_tool(
            CONNECTOR, "get_payrun", {}, db_session, db_session, venue_id=world.venue.id
        )
        assert out.success is False and "who is asking" in out.error
        # A plain tool still runs for nobody — nothing changed for it.
        assert execute_connector_tool(
            CONNECTOR, "get_thing", {}, db_session, db_session
        ).success

    def test_the_signed_in_person_counts_at_their_venue(self, db_session, world):
        """A venue scope alone names nobody; with ``user=`` the executor keeps
        the person, so a page or chart at a venue sees their role."""
        out = execute_connector_tool(
            CONNECTOR,
            "get_payrun",
            {},
            db_session,
            db_session,
            venue_id=world.venue.id,
            user=world.owner,
        )
        assert out.success is True
        out = execute_connector_tool(
            CONNECTOR,
            "get_payrun",
            {},
            db_session,
            db_session,
            venue_id=world.venue.id,
            user=world.manager,
        )
        assert out.success is False and "permission" in out.error

    def test_a_platform_admin_passes(self, db_session, world):
        admin = make_user(db_session, role="admin")
        out = execute_connector_tool(
            CONNECTOR, "get_payrun", {}, db_session, db_session, user=admin
        )
        assert out.success is True


class TestCallerHolds:
    def test_answers_for_the_person_acting(self, db_session, world):
        with caller_scope.use(caller_scope.for_user(db_session, world.owner)):
            assert caller_scope.holds(db_session, "payroll:read") is True
            out = execute_connector_tool(
                "norm",
                "caller_holds",
                {"scope": "payroll:read"},
                db_session,
                db_session,
            )
            assert out.payload == {"holds": True}
        with caller_scope.use(caller_scope.for_user(db_session, world.manager)):
            assert caller_scope.holds(db_session, "payroll:read") is False
            assert caller_scope.holds(db_session, "finance:read") is True

    def test_nobody_known_is_false(self, db_session, world):
        assert caller_scope.holds(db_session, "payroll:read") is False
        out = execute_connector_tool(
            "norm", "caller_holds", {"scope": "payroll:read"}, db_session, db_session
        )
        assert out.payload == {"holds": False}

    def test_a_scope_is_required(self, db_session, world):
        out = execute_connector_tool("norm", "caller_holds", {}, db_session, db_session)
        assert out.success is False and "scope" in out.error

    def test_it_is_not_in_any_menu(self, db_session, world):
        """Engine-only: a model never sees it (the config row says engine_only;
        here there is no row at all, which is the same answer)."""
        assert "caller_holds" not in {
            t["action"]
            for t in _collect_tools(
                db_session, user_id=world.owner.id, config_db=db_session
            )
        }
        assert TL is not None
