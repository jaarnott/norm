"""Staff access, stream 3 (docs/plans/staff-access.md section 11.4): a role
narrows the App Map per person — the tools in a chat, the model it runs on,
the monthly cap for venue staff, and which apps /api/apps serves.

The rule is enforced by the tool list, not by asking Norm to behave: a tool
the role doesn't reach is never in the menu, and the tool loop refuses any
name outside it.
"""

from __future__ import annotations

import datetime
import json
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.db.config_models import ConnectionSpec, MarketplaceApp
from app.db.models import App, AppShare, AppVersion, Connection, Role, TokenUsage
from app.services import agent_catalog, role_access, staff_cap
from app.services.staff_cap import OFF_MESSAGE, REACHED_MESSAGE, StaffCapExceeded
from tests.conftest import _make_membership, _make_organization, _make_user
from tests.org_helpers import auth_headers, seed_system_roles
from tests.tool_loop_harness import end_turn, tool


@pytest.fixture(autouse=True)
def _fresh_cache():
    agent_catalog.invalidate_cache()
    yield
    agent_catalog.invalidate_cache()


# ---------------------------------------------------------------- world ----


def _tool_spec(db, name, actions):
    db.add(
        ConnectionSpec(
            connector_name=name,
            display_name=name,
            auth_type="none",
            execution_mode="internal",
            enabled=True,
            tools=[
                {"action": a, "method": "GET", "description": f"{a} thing."}
                for a in actions
            ],
        )
    )
    db.add(Connection(connector_name=name, enabled="true", config={}))
    db.flush()


def _row(db, slug, tier, composition):
    db.add(
        MarketplaceApp(
            slug=slug,
            name=slug,
            description="",
            tier=tier,
            bundled=True,
            composition=composition,
        )
    )
    db.flush()


def _role(db, org, name, *, agents, apps, permissions=None):
    role = Role(
        id=str(uuid.uuid4()),
        organization_id=org.id,
        name=name,
        display_name=name.replace("_", " ").title(),
        is_system=False,
        permissions=permissions or ["tasks:read", "apps:build"],
        agents=agents,
        apps=apps,
    )
    db.add(role)
    db.flush()
    return role


def _person(db, org, role, *, platform_role="user", name="Sam Chef"):
    user = _make_user(db, role=platform_role, full_name=name)
    _make_membership(db, user, org).role_id = role.id if role else None
    db.flush()
    return user


CORE = "nm.remember"
HIRING = "hr.hiring_list_jobs"
TRAINING = "hr.training_list_plans"
LABOUR = "lh.get_labour"  # an HR App with no app_slug (agent-level)
STOCK = "lh.get_stock"  # Procurement's
EVERYTHING = {CORE, HIRING, TRAINING, LABOUR, STOCK}


def _catalog(db):
    """Two AI agents; Norm Core (bound to every agent); two HR Apps that front
    built-in apps (hiring, training), one agent-level HR App, and one of
    Procurement's."""
    _tool_spec(db, "nm", ["remember"])
    _tool_spec(db, "hr", ["hiring_list_jobs", "training_list_plans"])
    _tool_spec(db, "lh", ["get_labour", "get_stock"])
    _row(db, "hr-agent", "agent", {"owns_agents": ["hr"]})
    _row(db, "procurement-agent", "agent", {"owns_agents": ["procurement"]})
    _row(
        db,
        role_access.CORE_APP_SLUG,
        "app",
        {"member": "*", "switchable": False, "tools": [CORE]},
    )
    _row(db, "hiring", "app", {"member": "hr", "app_slug": "hiring", "tools": [HIRING]})
    _row(
        db,
        "training",
        "app",
        {"member": "hr", "app_slug": "training", "tools": [TRAINING]},
    )
    _row(db, "loaded-staff", "app", {"member": "hr", "tools": [LABOUR]})
    _row(db, "stock", "app", {"member": "procurement", "tools": [STOCK]})


class World:
    """An organisation with the catalog above and one person per role."""

    def __init__(self, db: Session):
        self.db = db
        self.roles = seed_system_roles(db)
        self.org = _make_organization(db, name="Lott Group")
        _catalog(db)
        self.kitchen = _role(db, self.org, "kitchen", agents=[], apps=["training"])
        self.hr_only = _role(db, self.org, "hr_only", agents=["hr"], apps=[])
        self.staff = _person(db, self.org, self.kitchen)
        self.hr_person = _person(db, self.org, self.hr_only, name="Hana HR")
        self.owner = _person(db, self.org, self.roles["owner"], name="Olly Owner")
        self.admin = _person(
            db, self.org, self.roles["team_member"], platform_role="admin"
        )


def _tools(db, user):
    from app.agents.prompt_builder import _collect_tools

    return {
        f"{t['connector']}.{t['action']}"
        for t in _collect_tools(db, user_id=user.id, config_db=db)
    }


# ------------------------------------------------------------ the tools ----


class TestToolsFollowTheRole:
    def test_staff_with_one_app_get_only_that_apps_tools(self, db_session):
        """agents=[] + apps=['training']: Training's tools, nothing from the
        other HR Apps, nothing from Norm Core."""
        w = World(db_session)
        assert _tools(db_session, w.staff) == {TRAINING}

    def test_a_role_with_an_agent_gets_its_apps_and_norm_core(self, db_session):
        """agents=['hr'] + apps=[]: every HR App (fronting a built-in or not)
        plus Norm Core; nothing of Procurement's."""
        w = World(db_session)
        assert _tools(db_session, w.hr_person) == {HIRING, TRAINING, LABOUR, CORE}

    def test_null_null_is_unchanged(self, db_session):
        w = World(db_session)
        assert _tools(db_session, w.owner) == EVERYTHING

    def test_platform_admin_is_unchanged_whatever_their_role_says(self, db_session):
        w = World(db_session)
        assert _tools(db_session, w.admin) == EVERYTHING

    def test_the_system_team_member_role_reaches_nothing(self, db_session):
        w = World(db_session)
        nobody = _person(db_session, w.org, w.roles["team_member"])
        assert _tools(db_session, nobody) == set()

    def test_the_app_map_still_applies_first(self, db_session):
        """The role narrows what the organisation has; it never adds. An App's
        tools come with its AI agent's hire (an App's own switch takes only
        its screens away, owner 23 Oct 2026), so retiring HR takes Training's
        tools from the kitchen role whatever its apps list says."""
        from app.db.models import OrgAppEntitlement

        w = World(db_session)
        db_session.add(
            OrgAppEntitlement(
                organization_id=w.org.id, app_slug="hr-agent", enabled=False
            )
        )
        db_session.flush()
        assert _tools(db_session, w.staff) == set()

    def test_mcp_projection_inherits_the_filter(self, db_session):
        from app.db.models import McpCapability
        from app.mcp.projection import project_tools

        w = World(db_session)
        for action in ("hiring_list_jobs", "training_list_plans"):
            db_session.add(
                McpCapability(
                    kind="connector",
                    target="hr",
                    action=action,
                    scopes=["mcp:reports:read"],
                    enabled=True,
                )
            )
        db_session.flush()

        def actions(user):
            return {
                t.action
                for t in project_tools(
                    db_session,
                    db_session,
                    user_id=user.id,
                    granted_scopes=frozenset({"mcp:reports:read"}),
                    venue_names=["V"],
                )
            }

        assert actions(w.owner) == {"hiring_list_jobs", "training_list_plans"}
        assert actions(w.staff) == {"training_list_plans"}


class TestAppIndex:
    def test_names_the_fronted_app_and_the_member(self, db_session):
        from app.services.entitlements import app_index

        World(db_session)
        index = app_index(db_session)
        assert index["training"].key == "training"
        assert index["training"].member == "hr"
        assert index["loaded-staff"].key == "loaded-staff"
        assert index["stock"].member == "procurement"
        assert "hr-agent" not in index  # agent rows own no tools


# ----------------------------------------------------------- the prompt ----


class TestWhoYoureTalkingTo:
    def test_staff_get_the_section_and_managers_do_not(self, db_session):
        from app.agents.prompt_builder import who_guidance
        from tests.conftest import _make_venue, _make_venue_access

        w = World(db_session)
        venue = _make_venue(db_session, organization_id=w.org.id, name="La Zeppa")
        _make_venue_access(db_session, w.staff, venue)
        text = who_guidance(db_session, w.staff.id, w.staff, [venue])
        assert "## Who you're talking to" in text
        assert "Sam Chef works at La Zeppa (Kitchen)." in text
        assert "say a manager can help" in text
        assert who_guidance(db_session, w.owner.id, w.owner, [venue]) == ""
        assert who_guidance(db_session, w.hr_person.id, w.hr_person, [venue]) == ""

    def test_build_tool_definitions_appends_it_for_staff(self, db_session):
        from app.agents.prompt_builder import build_tool_definitions

        w = World(db_session)
        prompt, tools = build_tool_definitions(
            "norm", db_session, user_id=w.staff.id, config_db=db_session
        )
        assert [t["name"] for t in tools if "__" in t["name"]] == [
            "hr__training_list_plans"
        ]
        assert prompt.rstrip().endswith("say a manager can help.")
        prompt, _ = build_tool_definitions(
            "norm", db_session, user_id=w.owner.id, config_db=db_session
        )
        assert "Who you're talking to" not in prompt


# ------------------------------------------------------------ the model ----


class _Llm:
    """Stands in for call_llm_with_tools and records the model asked for."""

    def __init__(self):
        self.models: list[str | None] = []

    def __call__(
        self,
        system_prompt,
        messages,
        tools=None,
        model=None,
        db=None,
        thread_id=None,
        call_type=None,
        tool_choice=None,
    ):
        self.models.append(model)
        return end_turn("Here you go."), None


def _chat(db, user, monkeypatch) -> _Llm:
    from app.agents.norm import NormAgent

    agent = NormAgent()
    monkeypatch.setattr(
        agent,
        "get_tool_definitions",
        lambda *a, **k: ("sys", [tool("hr__training_list_plans")]),
    )
    llm = _Llm()
    monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm_with_tools", llm)
    out = agent.handle_message("what training is due?", db, user.id, config_db=db)
    assert out["status"] == "completed"
    return llm


class TestStaffModel:
    def test_staff_chat_runs_on_the_staff_model(self, db_session, monkeypatch):
        w = World(db_session)
        monkeypatch.setattr(settings, "STAFF_CHAT_MODEL", "claude-sonnet-5-5")
        llm = _chat(db_session, w.staff, monkeypatch)
        assert llm.models == ["claude-sonnet-5-5"]

    def test_a_manager_keeps_the_usual_model(self, db_session, monkeypatch):
        w = World(db_session)
        llm = _chat(db_session, w.owner, monkeypatch)
        assert llm.models == [None]
        llm = _chat(db_session, w.hr_person, monkeypatch)
        assert llm.models == [None]

    def test_light_mode_never_picks_a_dearer_model(self, db_session, monkeypatch):
        from app.agents import tool_loop
        from app.services.billing_service import FALLBACK_MODEL

        monkeypatch.setattr(
            "app.services.billing_service.light_mode_for_thread", lambda db, t: True
        )
        # Sonnet costs more than the light model: light mode wins.
        assert tool_loop._model_for_turn(db_session, "t", "claude-sonnet-5-5") == (
            FALLBACK_MODEL
        )
        # An override already as cheap as light mode stays.
        assert tool_loop._model_for_turn(db_session, "t", FALLBACK_MODEL) == (
            FALLBACK_MODEL
        )
        # No override: call_llm_with_tools decides, as before.
        assert tool_loop._model_for_turn(db_session, "t", None) is None
        monkeypatch.setattr(
            "app.services.billing_service.light_mode_for_thread", lambda db, t: False
        )
        assert (
            tool_loop._model_for_turn(db_session, "t", "claude-sonnet-5-5")
            == "claude-sonnet-5-5"
        )

    def test_staff_with_no_tools_are_told_about_their_role(
        self, db_session, monkeypatch
    ):
        from app.agents.norm import NormAgent

        w = World(db_session)
        nobody = _person(db_session, w.org, w.roles["team_member"])
        agent = NormAgent()
        monkeypatch.setattr(agent, "get_tool_definitions", lambda *a, **k: ("", []))
        out = agent.handle_message("hi", db_session, nobody.id, config_db=db_session)
        assert "A manager can add apps to your role" in out["conversation"][-1]["text"]
        out = agent.handle_message("hi", db_session, w.owner.id, config_db=db_session)
        assert "Hire an AI agent" in out["conversation"][-1]["text"]


# -------------------------------------------------------------- the cap ----


def _usage(db, org, user, date, cost):
    db.add(
        TokenUsage(
            organization_id=org.id,
            user_id=user.id,
            date=date,
            cost_usd=cost,
            llm_call_count=1,
        )
    )
    db.flush()


class TestStaffCap:
    def test_cap_is_the_orgs_or_the_default(self, db_session, monkeypatch):
        monkeypatch.setattr(settings, "STAFF_CHAT_DEFAULT_CAP_CENTS", 200)
        org = _make_organization(db_session)
        assert staff_cap.cap_cents(org) == 200
        assert staff_cap.cap_cents(None) == 200
        org.staff_chat_cap_cents = 500
        assert staff_cap.cap_cents(org) == 500
        org.staff_chat_cap_cents = 0
        assert staff_cap.cap_cents(org) == 0

    def test_the_month_boundary_is_the_one_the_usage_rows_use(self, db_session):
        """usage_service dates its rows by the API's clock (UTC), so the cap
        month is the UTC one too — an Auckland organisation whose month
        rolled over 13 hours early read 0 spend while the day's rows were
        still landing under October."""
        org = _make_organization(db_session)
        user = _make_user(db_session)
        _usage(db_session, org, user, "2026-10-31", 1.0)
        _usage(db_session, org, user, "2026-11-01", 0.5)
        # Noon UTC on 31 Oct is already 1 Nov in Auckland (UTC+13): still
        # October here, whatever timezone the organisation keeps.
        noon = datetime.datetime(2026, 10, 31, 12, tzinfo=datetime.timezone.utc)
        for tz in ("Pacific/Auckland", "UTC", "Mars/Olympus", None):
            assert staff_cap.month_cost_usd(
                db_session, org.id, user.id, tz_name=tz, now=noon
            ) == pytest.approx(1.0)
        later = datetime.datetime(2026, 11, 1, 0, 30, tzinfo=datetime.timezone.utc)
        assert staff_cap.month_cost_usd(
            db_session, org.id, user.id, now=later
        ) == pytest.approx(0.5)
        # Only this person's rows in this organisation count.
        other = _make_user(db_session)
        _usage(db_session, org, other, "2026-11-01", 9.0)
        assert staff_cap.month_cost_usd(
            db_session, org.id, user.id, now=later
        ) == pytest.approx(0.5)

    def test_check_raises_only_for_staff(self, db_session, monkeypatch):
        monkeypatch.setattr(settings, "STAFF_CHAT_DEFAULT_CAP_CENTS", 200)
        w = World(db_session)
        today = staff_cap.today_in(w.org.default_timezone).isoformat()

        # Under the cap: fine.
        _usage(db_session, w.org, w.staff, today, 1.99)
        staff_cap.check(db_session, w.staff.id)
        # At the cap: refused, with the sentence the person reads.
        _usage(db_session, w.org, w.staff, "2000-01-01", 0)  # a stray row, ignored
        db_session.query(TokenUsage).filter(
            TokenUsage.user_id == w.staff.id, TokenUsage.date == today
        ).update({TokenUsage.cost_usd: 2.0})
        with pytest.raises(StaffCapExceeded) as e:
            staff_cap.check(db_session, w.staff.id)
        assert str(e.value) == REACHED_MESSAGE
        assert e.value.cap_cents == 200 and e.value.used_cents == 200

        # The organisation's own cap wins over the default.
        w.org.staff_chat_cap_cents = 1000
        db_session.flush()
        staff_cap.check(db_session, w.staff.id)

        # Managers, people with an agent and platform admins are never capped.
        for person in (w.owner, w.hr_person, w.admin):
            _usage(db_session, w.org, person, today, 50.0)
            staff_cap.check(db_session, person.id)
        staff_cap.check(db_session, None)

    def test_zero_switches_staff_chat_off(self, db_session):
        w = World(db_session)
        w.org.staff_chat_cap_cents = 0
        db_session.flush()
        with pytest.raises(StaffCapExceeded) as e:
            staff_cap.check(db_session, w.staff.id)
        assert str(e.value) == OFF_MESSAGE
        staff_cap.check(db_session, w.owner.id)  # the owner still chats

    def test_the_supervisor_checks_before_any_model_call(self, db_session, monkeypatch):
        from app.services import supervisor

        w = World(db_session)
        w.org.staff_chat_cap_cents = 0
        db_session.flush()

        def never():
            raise AssertionError("the agent must not run")

        monkeypatch.setattr(supervisor, "norm_agent", never)
        with pytest.raises(StaffCapExceeded):
            supervisor.handle_message(
                "hi", db_session, config_db=db_session, user_id=w.staff.id
            )


class TestStreamRefusal:
    """The messages router turns StaffCapExceeded into the same kind of
    friendly refusal as QuotaExceededError: the sentence is saved in the
    thread and the stream ends with it."""

    def test_the_refusal_is_saved_and_streamed(self, client, db_session, monkeypatch):
        from app.db.models import Message
        from app.routers import messages as messages_mod

        w = World(db_session)
        monkeypatch.setattr(
            messages_mod, "SessionLocal", lambda: Session(bind=db_session.get_bind())
        )

        def refuse(*a, **k):
            raise StaffCapExceeded(OFF_MESSAGE, used_cents=0, cap_cents=0)

        monkeypatch.setattr(messages_mod, "handle_message", refuse)
        resp = client.post(
            "/api/messages/stream",
            json={"message": "what's on today?"},
            headers=auth_headers(w.staff),
        )
        assert resp.status_code == 200
        events = [
            json.loads(line[len("data: ") :])
            for line in resp.text.splitlines()
            if line.startswith("data: ")
        ]
        terminal = events[-1]
        assert terminal["type"] == "error"
        assert terminal["reason"] == "staff_cap"
        assert terminal["message"] == OFF_MESSAGE
        # Not the organisation's quota: the billing notice must not appear.
        assert not [e for e in events if e["type"] == "quota_exceeded"]
        created = [e for e in events if e["type"] == "thread_created"]
        assert created
        texts = [
            m.content
            for m in db_session.query(Message)
            .filter(Message.thread_id == created[0]["thread_id"])
            .order_by(Message.created_at)
        ]
        assert texts == ["what's on today?", OFF_MESSAGE]


# ----------------------------------------------------- the organisation ----


class TestOrganizationCap:
    def test_round_trips_and_reports_the_default(self, client, db_session, monkeypatch):
        monkeypatch.setattr(settings, "STAFF_CHAT_DEFAULT_CAP_CENTS", 200)
        w = World(db_session)
        url = f"/api/organizations/{w.org.id}"
        body = client.get(url, headers=auth_headers(w.owner)).json()
        assert body["staff_chat_cap_cents"] is None
        assert body["staff_chat_cap_default_cents"] == 200
        assert body["staff_access_enabled"] is False

        r = client.put(
            url, json={"staff_chat_cap_cents": 500}, headers=auth_headers(w.owner)
        )
        assert r.status_code == 200 and r.json()["staff_chat_cap_cents"] == 500
        assert w.org.staff_chat_cap_cents == 500

        r = client.put(
            url, json={"staff_chat_cap_cents": 0}, headers=auth_headers(w.owner)
        )
        assert r.status_code == 200 and r.json()["staff_chat_cap_cents"] == 0

        r = client.put(
            url, json={"staff_chat_cap_cents": None}, headers=auth_headers(w.owner)
        )
        assert r.status_code == 200 and r.json()["staff_chat_cap_cents"] is None

        r = client.put(
            url, json={"staff_chat_cap_cents": -1}, headers=auth_headers(w.owner)
        )
        assert r.status_code == 400

        # A body that doesn't mention the cap leaves it alone.
        w.org.staff_chat_cap_cents = 300
        db_session.flush()
        r = client.put(url, json={"name": "Lott"}, headers=auth_headers(w.owner))
        assert r.status_code == 200 and r.json()["staff_chat_cap_cents"] == 300

    def test_billing_manage_may_set_the_cap_but_nothing_else(self, client, db_session):
        w = World(db_session)
        billing = _role(
            db_session,
            w.org,
            "billing",
            agents=None,
            apps=None,
            permissions=["billing:manage"],
        )
        bookkeeper = _person(db_session, w.org, billing)
        url = f"/api/organizations/{w.org.id}"
        r = client.put(
            url, json={"staff_chat_cap_cents": 150}, headers=auth_headers(bookkeeper)
        )
        assert r.status_code == 200 and r.json()["staff_chat_cap_cents"] == 150
        r = client.put(url, json={"name": "Mine now"}, headers=auth_headers(bookkeeper))
        assert r.status_code == 403
        r = client.put(
            url,
            json={"name": "Mine now", "staff_chat_cap_cents": 1},
            headers=auth_headers(bookkeeper),
        )
        assert r.status_code == 403
        # Venue staff can't touch it.
        r = client.put(
            url, json={"staff_chat_cap_cents": 99999}, headers=auth_headers(w.staff)
        )
        assert r.status_code == 403


# ------------------------------------------------------------- the apps ----


def _custom_app(db, org, author, slug, *, agent="hr", audience="agent"):
    app = App(
        organization_id=org.id,
        created_by=author.id,
        slug=slug,
        name=slug.title(),
        visibility="organization",
        agent=agent,
        kind="app",
        audience=audience,
    )
    db.add(app)
    db.flush()
    v = AppVersion(
        app_id=app.id, version=1, spec={}, ui_source="<div/>", created_by=author.id
    )
    db.add(v)
    db.flush()
    app.current_version_id = v.id
    # Shared with the whole organisation, so anyone in it may run it.
    db.add(
        AppShare(
            app_id=app.id,
            principal_type="organization",
            principal_id=org.id,
            granted_by=author.id,
        )
    )
    db.flush()
    return app


def _slugs(client, user):
    r = client.get("/api/apps", headers=auth_headers(user))
    assert r.status_code == 200
    return {a["slug"]: a for a in r.json()["apps"]}


class TestAppsList:
    def test_staff_see_only_their_roles_apps(self, client, db_session):
        w = World(db_session)
        w.kitchen.apps = ["training", "custom:cleaning"]
        db_session.flush()
        _custom_app(db_session, w.org, w.owner, "cleaning")
        _custom_app(db_session, w.org, w.owner, "rosters", agent="hr")
        listed = _slugs(client, w.staff)
        assert set(listed) == {"training", "cleaning"}
        assert listed["training"]["audience"] == "agent"
        assert listed["cleaning"]["audience"] == "agent"

    def test_an_agents_apps_come_with_the_agent(self, client, db_session):
        w = World(db_session)
        _custom_app(db_session, w.org, w.owner, "rosters", agent="hr")
        _custom_app(db_session, w.org, w.owner, "orders", agent="procurement")
        assert set(_slugs(client, w.hr_person)) == {
            "hiring",
            "training",
            "performance",
            "rosters",
        }

    def test_owners_and_platform_admins_see_everything(self, client, db_session):
        w = World(db_session)
        _custom_app(db_session, w.org, w.owner, "orders", agent="procurement")
        everything = {"hiring", "training", "performance", "orders"}
        assert set(_slugs(client, w.owner)) == everything
        assert set(_slugs(client, w.admin)) == everything

    def test_a_refused_app_is_a_403_on_every_door(self, client, db_session):
        w = World(db_session)
        h = auth_headers(w.staff)
        assert client.get("/api/apps/training", headers=h).status_code == 200
        assert client.get("/api/apps/hiring", headers=h).status_code == 403
        r = client.post("/api/apps/hiring/run", json={}, headers=h)
        assert r.status_code == 403
        r = client.post(
            "/api/apps/hiring/call",
            json={"connector": "norm", "action": "noop"},
            headers=h,
        )
        assert r.status_code == 403
        # A platform admin bypasses the role.
        assert (
            client.get("/api/apps/hiring", headers=auth_headers(w.admin)).status_code
            == 200
        )
        # Something that doesn't exist is still a 404, not a 403.
        assert client.get("/api/apps/nothing", headers=h).status_code == 404


class TestTheAppDoor:
    def test_a_team_app_cannot_reach_past_the_viewers_role(
        self, db_session, monkeypatch
    ):
        """Scopes are optional on an app, so the permission intersection can be
        empty: an app declaring lh.get_labour with no scopes, listed in the
        Kitchen role, would hand a chef the labour figures through /call.
        The door applies the role's reach like the chat menu does."""
        from app.connectors import tool_executor
        from app.connectors.tool_executor import ToolResult
        from app.services import app_runtime

        w = World(db_session)
        w.kitchen.apps = ["training", "custom:labour-board"]
        db_session.flush()
        app = _custom_app(db_session, w.org, w.owner, "labour-board", audience="team")
        version = db_session.query(AppVersion).filter(AppVersion.app_id == app.id).one()
        version.spec = {
            "actions": [{"connector": "lh", "action": "get_labour"}],
            "scopes": [],
        }
        db_session.flush()
        calls = []
        monkeypatch.setattr(
            tool_executor,
            "execute_connector_tool",
            lambda *a, **k: calls.append(a) or ToolResult(success=True, payload={}),
        )

        def call(user):
            return app_runtime.call_action(
                db_session,
                db_session,
                app=app,
                version=version,
                user=user,
                venue_id=None,
                connector="lh",
                action="get_labour",
                commit=False,
            )

        with pytest.raises(HTTPException) as refused:
            call(w.staff)
        assert refused.value.status_code == 403
        assert "lh.get_labour" in refused.value.detail
        assert calls == []
        # The HR-only person reaches the HR agent's apps; the owner everything.
        call(w.hr_person)
        call(w.owner)
        assert len(calls) == 2

    def test_training_keeps_other_peoples_records_from_venue_staff(
        self, client, db_session
    ):
        """Kitchen reaches the Training app (its own progress on Home), but
        its management views — everyone's name, email, manager and training —
        are a manager's (hr:read); plan section 4 puts other people's HR
        records out of reach."""
        w = World(db_session)
        h = auth_headers(w.staff)
        for op in ("people", "overview", "tracker", "plans"):
            r = client.post(
                "/api/apps/training/run", json={"params": {"op": op}}, headers=h
            )
            assert r.status_code == 200, (op, r.text)
            assert "manager" in r.json()["data"].get("error", ""), (op, r.text)
            assert "people" not in r.json()["data"]
        r = client.post(
            "/api/apps/training/run",
            json={"params": {"op": "member", "person_id": "someone-else"}},
            headers=h,
        )
        assert "manager" in r.json()["data"].get("error", "")
        # A manager (org:manage) still sees People.
        r = client.post(
            "/api/apps/training/run",
            json={"params": {"op": "people"}},
            headers=auth_headers(w.owner),
        )
        assert r.status_code == 200 and "people" in r.json()["data"], r.text


class TestAudience:
    def test_built_ins_declare_an_audience(self):
        from app.services.builtin_apps import builtin_apps

        assert {b.audience for b in builtin_apps().values()} == {"agent"}

    def test_round_trips_through_save_and_settings(self, client, db_session):
        w = World(db_session)
        h = auth_headers(w.owner)
        r = client.post(
            "/api/apps",
            json={
                "name": "Weekly cleaning",
                "slug": "weekly-cleaning",
                "kind": "app",
                "agent": "hr",
                "audience": "team",
                "spec": {},
                "ui_source": "<div/>",
            },
            headers=h,
        )
        assert r.status_code == 200, r.text
        assert (
            client.get("/api/apps/weekly-cleaning", headers=h).json()["audience"]
            == "team"
        )
        assert _slugs(client, w.owner)["weekly-cleaning"]["audience"] == "team"

        r = client.put(
            "/api/apps/weekly-cleaning", json={"audience": "both"}, headers=h
        )
        assert r.status_code == 200 and r.json()["audience"] == "both"
        assert (
            client.get("/api/apps/weekly-cleaning", headers=h).json()["audience"]
            == "both"
        )

        r = client.put(
            "/api/apps/weekly-cleaning", json={"audience": "everyone"}, headers=h
        )
        assert r.status_code == 400
        # A built-in's audience is Norm's to set.
        r = client.put("/api/apps/training", json={"audience": "team"}, headers=h)
        assert r.status_code == 400

    def test_only_an_author_or_editor_changes_it(self, client, db_session):
        w = World(db_session)
        _custom_app(db_session, w.org, w.owner, "rosters", agent="hr")
        r = client.put(
            "/api/apps/rosters",
            json={"audience": "team"},
            headers=auth_headers(w.hr_person),
        )
        assert r.status_code == 403


# ---------------------------------------------------------- team apps ----


RECIPES = "lh.get_recipes"  # Recipes & Costing's, listed by the Kitchen team app
COSTS = "lh.get_menu_item_costs"  # Recipes & Costing's, not in any team app


def _team_world(db) -> World:
    """World plus the Executive Chef with Recipes & Costing (two tools) and
    the three team rows; the Kitchen team app lists only the recipes read
    (docs/plans/team-apps-skeleton.md §2)."""
    w = World(db)
    spec = db.query(ConnectionSpec).filter(ConnectionSpec.connector_name == "lh").one()
    spec.tools = list(spec.tools) + [
        {"action": "get_recipes", "method": "GET", "description": "Recipes."},
        {"action": "get_menu_item_costs", "method": "GET", "description": "Costs."},
    ]
    db.flush()
    _row(db, "chef-agent", "agent", {"owns_agents": ["executive_chef"]})
    _row(
        db,
        "recipes-and-costing",
        "app",
        {"member": "executive_chef", "tools": [RECIPES, COSTS]},
    )
    for slug, tools in (
        ("kitchen", [RECIPES]),
        ("front_of_house", []),
        ("managers", []),
    ):
        _row(db, slug, "team", {"tagline": slug, "uses_tools": tools})
    w.kitchen_team = _role(db, w.org, "kitchen_team", agents=[], apps=["kitchen"])
    w.chef = _person(db, w.org, w.kitchen_team, name="Kit Chen")
    return w


class TestTeamAppTools:
    """§3.4: a team app hands Norm exactly the tools its row lists, for a
    role that reaches only that team app."""

    def test_a_kitchen_role_gets_the_team_apps_tools_and_no_norm_core(self, db_session):
        w = _team_world(db_session)
        assert _tools(db_session, w.chef) == {RECIPES}

    def test_the_team_app_never_adds_a_tool_whose_agent_is_not_hired(self, db_session):
        """The tool still comes with its App's AI agent: retire the Executive
        Chef and the Kitchen team app's recipes read goes with it."""
        from app.db.models import OrgAppEntitlement

        w = _team_world(db_session)
        db_session.add(
            OrgAppEntitlement(
                organization_id=w.org.id, app_slug="chef-agent", enabled=False
            )
        )
        db_session.flush()
        assert _tools(db_session, w.chef) == set()

    def test_ticking_the_job_app_still_gives_the_whole_app(self, db_session):
        w = _team_world(db_session)
        w.kitchen_team.apps = ["kitchen", "recipes-and-costing"]
        db_session.flush()
        assert _tools(db_session, w.chef) == {RECIPES, COSTS}

    def test_a_team_app_the_role_does_not_name_gives_nothing(self, db_session):
        w = _team_world(db_session)
        w.kitchen_team.apps = ["front_of_house"]
        db_session.flush()
        assert _tools(db_session, w.chef) == set()

    def test_null_null_and_the_other_roles_are_unchanged(self, db_session):
        w = _team_world(db_session)
        assert _tools(db_session, w.owner) == EVERYTHING | {RECIPES, COSTS}
        assert _tools(db_session, w.staff) == {TRAINING}
        assert _tools(db_session, w.hr_person) == {HIRING, TRAINING, LABOUR, CORE}

    def test_the_app_door_reads_the_same_rule(self, db_session):
        """app_runtime._check_role_reach: an App-platform app calling the
        recipes read for a kitchen-role chef passes; the costs read is
        refused as before."""
        from app.services.app_runtime import _check_role_reach

        w = _team_world(db_session)
        _check_role_reach(db_session, db_session, w.chef, "lh", "get_recipes")
        with pytest.raises(HTTPException) as e:
            _check_role_reach(
                db_session, db_session, w.chef, "lh", "get_menu_item_costs"
            )
        assert e.value.status_code == 403
