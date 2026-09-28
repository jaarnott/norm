"""Apps v3 (28 Sep 2026): one App -> one team member; every tool, component
and skill -> exactly one App; Norm Core is the one App shared by every member.

Pins: the tool-ownership filter (armed only once Apps declare tools), apps
that are always on, skills gating, unattended-run scope, ownership findings
(shared by the admin App Map and the validator), the admin endpoint, and the
seed data's own consistency against the web component registry."""

import pathlib
import re

import pytest

from app.db.config_models import (
    AgentConnectionBinding,
    ConnectionSpec,
    MarketplaceApp,
    Playbook,
)
from app.db.models import Connection, OrgAppEntitlement
from app.services import agent_catalog
from tests.conftest import _make_membership, _make_organization, _make_user

REPO = pathlib.Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _fresh_cache():
    agent_catalog.invalidate_cache()
    yield
    agent_catalog.invalidate_cache()


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


def _bind(db, agent, connector, actions):
    db.add(
        AgentConnectionBinding(
            agent_slug=agent,
            connector_name=connector,
            capabilities=[
                {"action": a, "enabled": True, "label": a.title()} for a in actions
            ],
            enabled=True,
        )
    )
    db.flush()


def _row(db, slug, tier, composition, *, bundled=True, name=None):
    db.add(
        MarketplaceApp(
            slug=slug,
            name=name or slug,
            description="",
            tier=tier,
            bundled=bundled,
            composition=composition,
        )
    )
    db.flush()


def _world(db):
    """Two members, three Apps (one always on), tools and a skill."""
    user = _make_user(db)
    org = _make_organization(db)
    _make_membership(db, user, org)
    _tool_spec(db, "lh", ["get_stock", "get_sales", "loose_tool"])
    _tool_spec(db, "nm", ["remember"])
    _bind(db, "procurement", "lh", ["get_stock", "loose_tool"])
    _bind(db, "reports", "lh", ["get_sales"])
    _bind(db, "base", "nm", ["remember"])
    _row(db, "procurement-agent", "agent", {"owns_agents": ["procurement"]})
    _row(
        db,
        "core",
        "app",
        {"member": "*", "switchable": False, "tools": ["nm.remember"]},
    )
    _row(
        db,
        "stock",
        "app",
        {"member": "procurement", "tools": ["lh.get_stock"], "skills": ["stock_skill"]},
    )
    _row(db, "reps", "app", {"member": "reports", "tools": ["lh.get_sales"]})
    db.add(
        Playbook(
            slug="stock_skill",
            agent_slug="procurement",
            display_name="Stock Skill",
            description="d",
            instructions="i",
            enabled=True,
        )
    )
    db.flush()
    return user, org


def _tools(db, user):
    from app.agents.prompt_builder import _collect_tools

    return {
        f"{t['connector']}.{t['action']}"
        for t in _collect_tools(db, user_id=user.id, config_db=db)
    }


class TestToolOwnership:
    def test_every_owned_tool_on_while_its_app_is_on(self, db_session):
        user, _ = _world(db_session)
        assert {"lh.get_stock", "lh.get_sales", "nm.remember"} <= _tools(
            db_session, user
        )

    def test_switching_an_app_off_removes_exactly_its_tools(self, db_session):
        user, org = _world(db_session)
        db_session.add(
            OrgAppEntitlement(organization_id=org.id, app_slug="stock", enabled=False)
        )
        db_session.flush()
        tools = _tools(db_session, user)
        assert "lh.get_stock" not in tools
        assert {"lh.get_sales", "nm.remember"} <= tools

    def test_retiring_the_member_removes_its_apps_tools(self, db_session):
        user, org = _world(db_session)
        db_session.add(
            OrgAppEntitlement(
                organization_id=org.id, app_slug="procurement-agent", enabled=False
            )
        )
        db_session.flush()
        tools = _tools(db_session, user)
        assert "lh.get_stock" not in tools
        # reports is always included; Norm Core is shared by everyone
        assert {"lh.get_sales", "nm.remember"} <= tools

    def test_the_app_map_is_the_exposure_list(self, db_session):
        """Bindings are retired: once Apps own tools, a tool reaches the agent
        iff an App owns it. A bound-but-unowned tool is not offered (the
        validator keeps that set at zero); an owned-but-unbound tool is."""
        user, _ = _world(db_session)
        assert "lh.loose_tool" not in _tools(db_session, user)  # bound, unowned
        _tool_spec(db_session, "cb", ["find_things"])
        _row(
            db_session,
            "extra",
            "app",
            {"member": "procurement", "tools": ["cb.find_things"]},
        )
        assert "cb.find_things" in _tools(db_session, user)  # owned, never bound

    def test_always_on_app_cannot_be_switched(
        self, client, db_session, admin_user, admin_headers
    ):
        org = _make_organization(db_session)
        _make_membership(db_session, admin_user, org)
        _row(db_session, "core", "app", {"member": "*", "switchable": False})
        r = client.post("/api/marketplace/core/disable", headers=admin_headers)
        assert r.status_code == 400

    def test_filter_dormant_until_apps_declare_tools(self, db_session):
        from app.services.entitlements import tool_owners

        _row(db_session, "old", "app", {"member": "procurement"})
        assert tool_owners(db_session) == {}


class TestSkillsFollowTheirApp:
    def test_skill_withheld_while_its_app_is_off(self, db_session):
        from app.agents.prompt_builder import blocked_playbook_slugs

        user, org = _world(db_session)
        assert not blocked_playbook_slugs(db_session, user_id=user.id, db=db_session)
        db_session.add(
            OrgAppEntitlement(organization_id=org.id, app_slug="stock", enabled=False)
        )
        db_session.flush()
        assert blocked_playbook_slugs(db_session, user_id=user.id, db=db_session) == {
            "stock_skill"
        }


class TestUnattendedScope:
    def test_member_scope_is_its_apps_plus_norm_core(self, db_session):
        from app.services.agent_config_service import default_tool_filter

        _world(db_session)
        # its Apps' tools + Norm Core, unioned with its legacy bindings
        assert set(default_tool_filter("procurement", db_session)) == {
            "get_stock",
            "loose_tool",
            "remember",
        }
        assert set(default_tool_filter("reports", db_session)) == {
            "get_sales",
            "remember",
        }

    def test_a_read_that_moved_to_another_members_app_stays_reachable(self, db_session):
        # get_labour went to Reports' Loaded Reports; a time_attendance task
        # that could read it through its binding must still be able to
        # (pre-push review, 28 Sep 2026).
        from app.services.agent_config_service import default_tool_filter

        _world(db_session)
        _bind(db_session, "time_attendance", "lh", ["get_sales"])
        scope = set(default_tool_filter("time_attendance", db_session))
        assert "get_sales" in scope and "get_stock" not in scope


class TestAgentReachFollowsItsApps:
    def test_get_agent_actions_reads_the_app_map(self, db_session):
        from app.services.agent_config_service import get_agent_actions

        _world(db_session)
        # procurement's legacy binding also lists loose_tool — ignored now
        assert get_agent_actions("procurement", db_session) == {"get_stock"}
        assert get_agent_actions("reports", db_session) == {"get_sales"}


class TestOwnershipFindings:
    def test_unowned_and_double_claims_are_found(self, db_session):
        _world(db_session)
        _row(
            db_session, "dupe", "app", {"member": "reports", "tools": ["lh.get_stock"]}
        )
        f = agent_catalog.ownership_findings(db_session)
        assert "lh.loose_tool" in f["unowned_tools"]
        assert any(d["key"] == "lh.get_stock" for d in f["double_claims"])

    def test_only_norm_core_may_be_bound_to_everyone(self, db_session):
        _world(db_session)
        _row(db_session, "greedy", "app", {"member": "*"})
        f = agent_catalog.ownership_findings(db_session)
        assert any(b["app"] == "greedy" for b in f["bad_members"])


class TestAdminEndpoint:
    def test_admin_sees_the_map(self, client, db_session, admin_headers):
        _world(db_session)
        r = client.get("/api/admin/app-map", headers=admin_headers)
        assert r.status_code == 200
        body = r.json()
        stock = next(a for a in body["apps"] if a["slug"] == "stock")
        assert stock["member"] == "procurement"
        assert [t["key"] for t in stock["tools"]] == ["lh.get_stock"]
        assert stock["tools"][0]["label"] == "Get_Stock"  # the binding's label
        assert "findings" in body

    def test_non_admin_is_refused(self, client, manager_headers):
        assert (
            client.get("/api/admin/app-map", headers=manager_headers).status_code == 403
        )


class TestSeedDataIsConsistent:
    """The seed is the reviewed source of the catalog; CI's config DB is empty,
    so its consistency is pinned here against the web registry itself."""

    def _apps(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "seed", REPO / "apps/api/scripts/sync_marketplace_catalog.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.APPS

    def test_registry_mirror_matches_the_web_registry(self):
        src = (
            REPO / "apps/web/app/components/display/DisplayBlockRenderer.tsx"
        ).read_text()
        block = src[
            src.index("const REGISTRY") : src.index("};", src.index("const REGISTRY"))
        ]
        web = set(re.findall(r"^\s+([a-z_]+):", block, re.M))
        assert web == set(agent_catalog.REGISTERED_COMPONENTS)

    def test_every_component_tool_and_skill_owned_exactly_once(self):
        apps = [a for a in self._apps() if a["tier"] == "app"]
        seen: dict[str, str] = {}
        for a in apps:
            comp = a["composition"]
            for key in (
                [f"c:{c['key']}" for c in comp.get("components") or []]
                + [f"t:{t}" for t in comp.get("tools") or []]
                + [f"s:{s}" for s in comp.get("skills") or []]
            ):
                assert key not in seen, (
                    f"{key} claimed by {seen.get(key)} and {a['slug']}"
                )
                seen[key] = a["slug"]
        components = {k[2:] for k in seen if k.startswith("c:")}
        assert components == set(agent_catalog.REGISTERED_COMPONENTS), (
            "every registry component belongs to exactly one App"
        )

    def test_every_app_has_one_member_and_only_core_is_shared(self):
        for a in self._apps():
            if a["tier"] != "app":
                continue
            m = a["composition"].get("member")
            assert m, a["slug"]
            assert m != "*" or a["slug"] == "norm-core"


class TestPreshipReviewFixes:
    """Findings from the Apps v3 pre-push review (28 Sep 2026)."""

    def test_a_personal_mailbox_is_not_an_app_requirement(self, db_session):
        # Norm Core owns gmail.send_email; Gmail is each user's own optional
        # connection, so it must not put "needs connections" on every card.
        db_session.add(
            ConnectionSpec(
                connector_name="gmail",
                display_name="Gmail",
                auth_type="oauth2",
                enabled=True,
                tools=[{"action": "send_email", "method": "POST"}],
            )
        )
        db_session.flush()
        row = MarketplaceApp(
            slug="core",
            name="Core",
            description="",
            tier="app",
            bundled=True,
            composition={"member": "*", "tools": ["gmail.send_email"]},
        )
        assert "gmail" not in agent_catalog.required_connections_for_app(
            row, db_session
        )

    def test_a_community_row_does_not_take_over_a_custom_app(self, db_session):
        from app.db.models import App
        from app.services.entitlements import apps_on

        _, org = _world(db_session)
        db_session.add(
            App(organization_id=org.id, slug="rota", name="Rota", agent="procurement")
        )
        # someone else's published App with the same bare slug, not entitled
        _row(db_session, "their-rota", "user", {"app_slug": "rota"}, bundled=False)
        assert "custom:rota" in apps_on(org.id, db_session, db_session)
        payload = agent_catalog.team_payload(org.id, db_session, db_session)
        assert any(a["slug"] == "custom:rota" for a in payload["apps"])

    def test_org_managers_cannot_write_the_shared_agent_config(
        self, client, manager_headers
    ):
        for method, path in (
            ("put", "/api/agents/base"),
            ("post", "/api/agents/base/reset-prompt"),
            ("put", "/api/agents/base/bindings/norm"),
            ("delete", "/api/agents/base/bindings/norm"),
        ):
            r = getattr(client, method)(
                path,
                headers=manager_headers,
                **({"json": {}} if method in ("put", "post") else {}),
            )
            assert r.status_code == 403, (method, path, r.status_code)
