"""Marketplace entitlement semantics (docs/apps-marketplace-plan.md Phase 1).

The rules every enforcement point relies on, pinned:

  * explicit row wins; no row -> the app's `bundled` default; app not in the
    catalog -> allowed (curation, not lockout);
  * only a tier='agent' row's composition["owns_agents"] can switch an agent
    off — switching an App off never does;
  * hiring/retiring members and switching Apps decides which Apps are on
    (what the App Map's tool filter reads — tests/test_app_map.py).
"""

import uuid

from app.db.config_models import MarketplaceApp
from app.services.entitlements import (
    agent_entitled,
    entitled_slugs,
    org_id_for_user,
)
from app.db.models import OrgAppEntitlement
from tests.conftest import _make_membership, _make_organization, _make_user


def _app(db, slug, *, bundled=True, owns_agents=None, member=None, tier="app"):
    row = MarketplaceApp(
        slug=slug,
        name=slug.title(),
        description="",
        tier=tier,
        bundled=bundled,
        composition={
            **({"owns_agents": owns_agents} if owns_agents else {}),
            **({"member": member} if member else {}),
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
        _app(db_session, "loaded", bundled=True)
        _app(db_session, "paid-thing", bundled=False)
        got = entitled_slugs(org.id, db_session, db_session)
        assert got == {"loaded"}

    def test_explicit_row_wins_over_bundled_default(self, db_session):
        org = _make_organization(db_session)
        _app(db_session, "loaded", bundled=True)
        _app(db_session, "paid-thing", bundled=False)
        _entitle(db_session, org, "loaded", enabled=False)
        _entitle(db_session, org, "paid-thing", enabled=True)
        got = entitled_slugs(org.id, db_session, db_session)
        assert got == {"paid-thing"}


class TestAgentGate:
    def test_unowned_agent_always_allowed(self, db_session):
        org = _make_organization(db_session)
        # Gating is armed (a member row exists) but no member row owns hr: an
        # App bound to hr is switched off, and the hr agent itself stays on.
        # Only a member row's owns_agents can switch an agent off.
        _app(db_session, "procurement-agent", owns_agents=["procurement"], tier="agent")
        _app(db_session, "bamboohr-app", bundled=True, member="hr")
        _entitle(db_session, org, "bamboohr-app", enabled=False)
        assert agent_entitled("hr", org.id, db_session, db_session) is True

    def test_owned_agent_follows_its_bundle(self, db_session):
        # Hierarchy v2: gating arms only once tier='agent' rows exist.
        org = _make_organization(db_session)
        _app(db_session, "hr-agent", bundled=True, owns_agents=["hr"], tier="agent")
        assert agent_entitled("hr", org.id, db_session, db_session) is True
        _entitle(db_session, org, "hr-agent", enabled=False)
        assert agent_entitled("hr", org.id, db_session, db_session) is False


class TestOrgLookup:
    def test_membership_resolves(self, db_session):
        user = _make_user(db_session)
        org = _make_organization(db_session)
        _make_membership(db_session, user, org)
        assert org_id_for_user(user.id, db_session) == org.id
        assert org_id_for_user(None, db_session) is None


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
        assert {"base", "reports", "app_builder"} <= hired


class TestAppsOn:
    """Every App has an enabled state: member hired AND app entitled."""

    def _member_with_app(self, db, *, app_bundled=True, app_price=0):
        _app(db, "hr-agent", bundled=True, owns_agents=["hr"], tier="agent")
        # the App names the member that uses it (Apps v3)
        row = _app(db, "bamboohr-app", bundled=app_bundled, member="hr")
        row.price_cents = app_price
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
