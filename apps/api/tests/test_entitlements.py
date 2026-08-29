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


def _app(db, slug, *, bundled=True, connections=None, tool_actions=None, owns_agents=None, agents=None):
    row = MarketplaceApp(
        slug=slug,
        name=slug.title(),
        description="",
        tier="platform",
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
        org = _make_organization(db_session)
        _app(db_session, "hr-agent", bundled=True, owns_agents=["hr"])
        assert agent_entitled("hr", org.id, db_session, db_session) is True
        _entitle(db_session, org, "hr-agent", enabled=False)
        assert agent_entitled("hr", org.id, db_session, db_session) is False


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
