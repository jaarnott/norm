"""Marketplace entitlement semantics (docs/apps-marketplace-plan.md Phase 1).

The rules every enforcement point relies on, pinned:

  * explicit row wins; no row -> the app's `bundled` default; app not in the
    catalog -> allowed (curation, not lockout);
  * an EMPTY catalog filters nothing (dark launch: seeding machinery before
    the seed changes no behaviour);
  * only composition["owns_agents"] can switch an agent off — the
    informational "agents" list on an integration app must never do so;
  * the prompt_builder filter drops a disabled app's connector bindings, and
    project_tools inherits that (it gates through _collect_tools), so the MCP
    surface honors marketplace toggles with no second code path.
"""

import uuid

from app.db.config_models import MarketplaceApp
from app.services.entitlements import (
    agent_entitled,
    entitled_slugs,
    org_id_for_user,
    unentitled_connectors,
)
from app.db.models import OrgAppEntitlement
from tests.conftest import _make_membership, _make_organization, _make_user


def _app(db, slug, *, bundled=True, connections=None, tool_actions=None, owns_agents=None, agents=None, tier="platform"):
    row = MarketplaceApp(
        slug=slug,
        name=slug.title(),
        description="",
        tier=tier,
        bundled=bundled,
        composition={
            **({"connections": connections} if connections else {}),
            **({"tool_actions": tool_actions} if tool_actions else {}),
            **({"owns_agents": owns_agents} if owns_agents else {}),
            **({"agents": agents} if agents else {}),
        },
    )
    db.add(row)
    db.flush()
    return row


def _entitle(db, org, slug, enabled):
    db.add(
        OrgAppEntitlement(
            id=str(uuid.uuid4()),
            organization_id=org.id,
            app_slug=slug,
            enabled=enabled,
        )
    )
    db.flush()


class TestEntitledSlugs:
    def test_bundled_default_applies_without_a_row(self, db_session):
        org = _make_organization(db_session)
        _app(db_session, "loaded", bundled=True, connections=["loadedhub"])
        _app(db_session, "paid-thing", bundled=False)
        got = entitled_slugs(org.id, db_session, db_session)
        assert got == {"loaded"}

    def test_explicit_row_wins_over_bundled_default(self, db_session):
        org = _make_organization(db_session)
        _app(db_session, "loaded", bundled=True, connections=["loadedhub"])
        _app(db_session, "paid-thing", bundled=False)
        _entitle(db_session, org, "loaded", enabled=False)
        _entitle(db_session, org, "paid-thing", enabled=True)
        got = entitled_slugs(org.id, db_session, db_session)
        assert got == {"paid-thing"}


class TestConnectorFilter:
    def test_empty_catalog_filters_nothing(self, db_session):
        org = _make_organization(db_session)
        assert unentitled_connectors(org.id, db_session, db_session) == set()

    def test_disabled_app_blocks_its_connector_only(self, db_session):
        org = _make_organization(db_session)
        _app(db_session, "loaded", bundled=True, connections=["loadedhub"])
        _app(db_session, "bamboo", bundled=True, connections=["bamboohr"])
        _entitle(db_session, org, "loaded", enabled=False)
        assert unentitled_connectors(org.id, db_session, db_session) == {"loadedhub"}

    def test_unclaimed_connector_is_never_filtered(self, db_session):
        # No catalog row names 'gmail' — it must keep working untouched.
        org = _make_organization(db_session)
        _app(db_session, "loaded", bundled=True, connections=["loadedhub"])
        _entitle(db_session, org, "loaded", enabled=False)
        blocked = unentitled_connectors(org.id, db_session, db_session)
        assert "gmail" not in blocked

    def test_connection_survives_while_any_entitled_app_declares_it(self, db_session):
        """The connections/apps split's key semantic: disabling ONE app never
        blocks a connection another entitled app also declares."""
        org = _make_organization(db_session)
        _app(db_session, "loaded", bundled=True, connections=["loadedhub", "cook_brothers_app"])
        _app(db_session, "cb", bundled=True, connections=["cook_brothers_app"])
        _entitle(db_session, org, "cb", enabled=False)
        # loaded (entitled) still declares cook_brothers_app -> stays available
        assert unentitled_connectors(org.id, db_session, db_session) == set()
        _entitle(db_session, org, "loaded", enabled=False)
        # now nobody entitled declares either
        assert unentitled_connectors(org.id, db_session, db_session) == {
            "loadedhub", "cook_brothers_app",
        }

    def test_unknown_org_filters_nothing(self, db_session):
        _app(db_session, "loaded", bundled=False, connections=["loadedhub"])
        assert unentitled_connectors(None, db_session, db_session) == set()


class TestAgentGate:
    def test_unowned_agent_always_allowed(self, db_session):
        org = _make_organization(db_session)
        # integration app SERVES hr (informational) and is disabled — the hr
        # agent itself must stay on. Only owns_agents can switch an agent off.
        _app(db_session, "loaded", bundled=True, connections=["loadedhub"], agents=["hr"])
        _entitle(db_session, org, "loaded", enabled=False)
        assert agent_entitled("hr", org.id, db_session, db_session) is True

    def test_owned_agent_follows_its_bundle(self, db_session):
        # Hierarchy v2: gating arms only once tier='agent' rows exist — the
        # dark-launch switch. owns_agents on any other tier gates nothing.
        org = _make_organization(db_session)
        _app(db_session, "hr-agent", bundled=True, owns_agents=["hr"], tier="agent")
        assert agent_entitled("hr", org.id, db_session, db_session) is True
        _entitle(db_session, org, "hr-agent", enabled=False)
        assert agent_entitled("hr", org.id, db_session, db_session) is False

    def test_owns_agents_on_a_platform_row_no_longer_gates(self, db_session):
        # Pre-rollout catalogs mark billable agents this way; they must stay
        # fail-open until the team rollout flips tiers.
        org = _make_organization(db_session)
        _app(db_session, "hr-agent", bundled=True, owns_agents=["hr"])
        _entitle(db_session, org, "hr-agent", enabled=False)
        assert agent_entitled("hr", org.id, db_session, db_session) is True


class TestPromptBuilderFilter:
    def test_disabled_app_removes_its_tools(self, db_session):
        from app.db.config_models import AgentConnectionBinding, ConnectionSpec
        from app.agents.prompt_builder import _collect_tools
        from app.db.models import Connection

        user = _make_user(db_session)
        org = _make_organization(db_session)
        _make_membership(db_session, user, org)
        db_session.add(
            ConnectionSpec(
                connector_name="fake_lh",
                display_name="Fake",
                auth_type="none",
                execution_mode="internal",
                enabled=True,
                tools=[{"action": "get_things", "method": "GET", "description": "x"}],
            )
        )
        db_session.add(
            AgentConnectionBinding(
                agent_slug="procurement",
                connector_name="fake_lh",
                capabilities=[{"action": "get_things", "enabled": True}],
                enabled=True,
            )
        )
        db_session.add(
            Connection(connector_name="fake_lh", enabled="true", config={})
        )
        db_session.flush()

        before = {
            (t["connector"], t["action"])
            for t in _collect_tools(db_session, user_id=user.id, config_db=db_session)
        }
        assert ("fake_lh", "get_things") in before

        _app(db_session, "fake-app", bundled=True, connections=["fake_lh"], tool_actions=["fake_lh.*"])
        _entitle(db_session, org, "fake-app", enabled=False)
        after = {
            (t["connector"], t["action"])
            for t in _collect_tools(db_session, user_id=user.id, config_db=db_session)
        }
        assert ("fake_lh", "get_things") not in after
        # dark-launch property: nothing else moved
        assert before - after == {("fake_lh", "get_things")}


class TestOrgLookup:
    def test_membership_resolves(self, db_session):
        user = _make_user(db_session)
        org = _make_organization(db_session)
        _make_membership(db_session, user, org)
        assert org_id_for_user(user.id, db_session) == org.id
        assert org_id_for_user(None, db_session) is None


def _seed_tool(db, connector, action, agent_slug):
    """One spec+binding+credential so _collect_tools can see one tool."""
    from app.db.config_models import AgentConnectionBinding, ConnectionSpec
    from app.db.models import Connection

    if not db.query(ConnectionSpec).filter_by(connector_name=connector).first():
        db.add(
            ConnectionSpec(
                connector_name=connector,
                display_name=connector,
                auth_type="none",
                execution_mode="internal",
                enabled=True,
                tools=[{"action": action, "method": "GET", "description": "x"}],
            )
        )
        db.add(Connection(connector_name=connector, enabled="true", config={}))
    db.add(
        AgentConnectionBinding(
            agent_slug=agent_slug,
            connector_name=connector,
            capabilities=[{"action": action, "enabled": True}],
            enabled=True,
        )
    )
    db.flush()


class TestHiredMembers:
    """Hierarchy v2: hired_agent_slugs is the team-member gate's one truth."""

    def test_no_agent_rows_means_gating_inactive(self, db_session):
        from app.services.entitlements import hired_agent_slugs

        org = _make_organization(db_session)
        _app(db_session, "loaded", bundled=True)  # a catalog, but no members
        assert hired_agent_slugs(org.id, db_session, db_session) is None

    def test_bundled_member_is_hired_by_default(self, db_session):
        from app.services.entitlements import ALWAYS_INCLUDED_AGENTS, hired_agent_slugs

        org = _make_organization(db_session)
        _app(db_session, "procurement-agent", bundled=True, owns_agents=["procurement"], tier="agent")
        hired = hired_agent_slugs(org.id, db_session, db_session)
        assert hired is not None
        assert "procurement" in hired
        assert ALWAYS_INCLUDED_AGENTS <= hired

    def test_retiring_a_member_fires_only_them(self, db_session):
        from app.services.entitlements import hired_agent_slugs

        org = _make_organization(db_session)
        _app(db_session, "procurement-agent", bundled=True, owns_agents=["procurement"], tier="agent")
        _app(db_session, "hr-agent", bundled=True, owns_agents=["hr"], tier="agent")
        _entitle(db_session, org, "hr-agent", enabled=False)
        hired = hired_agent_slugs(org.id, db_session, db_session)
        assert "procurement" in hired and "hr" not in hired

    def test_always_included_survive_any_override(self, db_session):
        from app.services.entitlements import hired_agent_slugs

        org = _make_organization(db_session)
        _app(db_session, "hr-agent", bundled=True, owns_agents=["hr"], tier="agent")
        _entitle(db_session, org, "reports", enabled=False)  # orphan row, ignored
        hired = hired_agent_slugs(org.id, db_session, db_session)
        assert {"base", "router", "reports", "app_builder"} <= hired

    def test_collect_tools_drops_a_fired_members_bindings(self, db_session):
        from app.agents.prompt_builder import _collect_tools

        user = _make_user(db_session)
        org = _make_organization(db_session)
        _make_membership(db_session, user, org)
        _seed_tool(db_session, "fake_lh2", "get_stuff", "procurement")
        _app(db_session, "procurement-agent", bundled=True, owns_agents=["procurement"], tier="agent")

        tools = {
            (t["connector"], t["action"])
            for t in _collect_tools(db_session, user_id=user.id, config_db=db_session)
        }
        assert ("fake_lh2", "get_stuff") in tools

        _entitle(db_session, org, "procurement-agent", enabled=False)
        tools = {
            (t["connector"], t["action"])
            for t in _collect_tools(db_session, user_id=user.id, config_db=db_session)
        }
        assert ("fake_lh2", "get_stuff") not in tools

    def test_a_tool_carried_by_two_members_survives_one_retirement(self, db_session):
        from app.agents.prompt_builder import _collect_tools

        user = _make_user(db_session)
        org = _make_organization(db_session)
        _make_membership(db_session, user, org)
        _seed_tool(db_session, "fake_lh3", "get_shared", "procurement")
        _seed_tool(db_session, "fake_lh3", "get_shared", "executive_chef")
        _app(db_session, "procurement-agent", bundled=True, owns_agents=["procurement"], tier="agent")
        _app(db_session, "chef-agent", bundled=True, owns_agents=["executive_chef"], tier="agent")
        _entitle(db_session, org, "procurement-agent", enabled=False)

        tools = {
            (t["connector"], t["action"])
            for t in _collect_tools(db_session, user_id=user.id, config_db=db_session)
        }
        assert ("fake_lh3", "get_shared") in tools

    def test_always_included_members_bindings_survive(self, db_session):
        from app.agents.prompt_builder import _collect_tools

        user = _make_user(db_session)
        org = _make_organization(db_session)
        _make_membership(db_session, user, org)
        _seed_tool(db_session, "fake_norm", "remember_stuff", "base")
        _app(db_session, "hr-agent", bundled=True, owns_agents=["hr"], tier="agent")
        _entitle(db_session, org, "hr-agent", enabled=False)

        tools = {
            (t["connector"], t["action"])
            for t in _collect_tools(db_session, user_id=user.id, config_db=db_session)
        }
        assert ("fake_norm", "remember_stuff") in tools


class TestAppsOn:
    """Every App has an enabled state: member hired AND app entitled."""

    def _member_with_app(self, db, *, app_bundled=True, app_price=0):
        _app(db, "hr-agent", bundled=True, owns_agents=["hr"], tier="agent")
        row = _app(db, "bamboohr-app", bundled=app_bundled, tier="app")
        row.price_cents = app_price
        # the member uses the app
        member = db.query(MarketplaceApp).filter_by(slug="hr-agent").first()
        member.composition = {**member.composition, "unlocks": ["bamboohr-app"]}
        db.flush()

    def test_hiring_enables_bundled_apps_by_default(self, db_session):
        from app.services.entitlements import apps_on

        org = _make_organization(db_session)
        self._member_with_app(db_session)
        assert "bamboohr-app" in apps_on(org.id, db_session, db_session)

    def test_an_app_can_be_switched_off_alone(self, db_session):
        from app.services.entitlements import apps_on, hired_agent_slugs

        org = _make_organization(db_session)
        self._member_with_app(db_session)
        _entitle(db_session, org, "bamboohr-app", enabled=False)
        assert "bamboohr-app" not in apps_on(org.id, db_session, db_session)
        # ...without firing the member
        assert "hr" in hired_agent_slugs(org.id, db_session, db_session)

    def test_a_paid_app_stays_off_until_enabled(self, db_session):
        from app.services.entitlements import apps_on

        org = _make_organization(db_session)
        self._member_with_app(db_session, app_bundled=False, app_price=400)
        assert "bamboohr-app" not in apps_on(org.id, db_session, db_session)
        _entitle(db_session, org, "bamboohr-app", enabled=True)
        assert "bamboohr-app" in apps_on(org.id, db_session, db_session)

    def test_retiring_the_member_turns_its_apps_off(self, db_session):
        from app.services.entitlements import apps_on

        org = _make_organization(db_session)
        self._member_with_app(db_session)
        _entitle(db_session, org, "hr-agent", enabled=False)
        assert "bamboohr-app" not in apps_on(org.id, db_session, db_session)

    def test_published_community_app_gates_on_entitlement_alone(self, db_session):
        from app.services.entitlements import apps_on

        org = _make_organization(db_session)
        _app(db_session, "hr-agent", bundled=True, owns_agents=["hr"], tier="agent")
        _app(db_session, "team-tracker", bundled=False, tier="user")
        assert "team-tracker" not in apps_on(org.id, db_session, db_session)
        _entitle(db_session, org, "team-tracker", enabled=True)
        assert "team-tracker" in apps_on(org.id, db_session, db_session)

    def test_gating_inactive_returns_none(self, db_session):
        from app.services.entitlements import apps_on

        org = _make_organization(db_session)
        assert apps_on(org.id, db_session, db_session) is None
