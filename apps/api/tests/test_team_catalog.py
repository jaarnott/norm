"""The team catalog: Apps inherit connection needs from their components, a
member's WORKS WITH is the union over its Apps, and /api/team is the one
payload the team page + sidebar + page gate all read.

Hierarchy v2 (27 Sep 2026): Organisation -> AI Team Members -> Apps ->
Components -> Connections. Components may need zero, one or many Connections;
a Norm-native App legitimately needs none (Norm HR); internal pipes are never
the user's to connect — except Norm-managed ones surfaced under a Norm name
(cook_brothers_app -> "Norm Kitchen Sync")."""

import pytest

from app.db.config_models import (
    ComponentApiConfig,
    ConnectionSpec,
    MarketplaceApp,
)
from app.services import agent_catalog
from tests.conftest import _make_membership, _make_organization


@pytest.fixture(autouse=True)
def _fresh_cache():
    agent_catalog.invalidate_cache()
    yield
    agent_catalog.invalidate_cache()


def _spec(db, name, mode="template"):
    db.add(
        ConnectionSpec(
            connector_name=name,
            display_name=name.title(),
            auth_type="none",
            execution_mode=mode,
            enabled=True,
            tools=[],
        )
    )
    db.flush()


def _row(db, slug, tier, composition, *, bundled=True, price=0, name=None):
    row = MarketplaceApp(
        slug=slug,
        name=name or slug.title(),
        description="",
        tier=tier,
        bundled=bundled,
        price_cents=price,
        composition=composition,
    )
    db.add(row)
    db.flush()
    return row


class TestRequiredConnections:
    def test_component_api_rows_are_inherited(self, db_session):
        _spec(db_session, "loadedhub")
        db_session.add(
            ComponentApiConfig(
                component_key="orders_dashboard",
                connector_name="loadedhub",
                action_name="get_orders_summary",
                path_template="//x",
            )
        )
        db_session.flush()
        app = _row(
            db_session,
            "loaded-procurement",
            "app",
            {"components": [{"key": "orders_dashboard"}]},
        )
        assert agent_catalog.required_connections_for_app(app, db_session) == {
            "loadedhub"
        }

    def test_explicit_component_connections_cover_self_loading(self, db_session):
        _spec(db_session, "loadedhub")
        app = _row(
            db_session,
            "loaded-procurement",
            "app",
            {
                "components": [
                    {"key": "invoices_dashboard", "connections": ["loadedhub"]}
                ]
            },
        )
        assert agent_catalog.required_connections_for_app(app, db_session) == {
            "loadedhub"
        }

    def test_a_norm_native_app_requires_nothing(self, db_session):
        app = _row(db_session, "norm-hr", "app", {"app_slug": "hiring"})
        assert agent_catalog.required_connections_for_app(app, db_session) == set()

    def test_internal_pipes_are_not_requirements(self, db_session):
        _spec(db_session, "norm", mode="internal")
        app = _row(
            db_session,
            "norm-reports",
            "app",
            {"components": [{"key": "report_builder", "connections": ["norm"]}]},
        )
        assert agent_catalog.required_connections_for_app(app, db_session) == set()

    def test_norm_managed_pipe_is_kept_but_renamed(self, db_session):
        _spec(db_session, "cook_brothers_app", mode="internal")
        app = _row(
            db_session,
            "loaded-kitchen",
            "app",
            {
                "components": [
                    {"key": "supplier_tenders", "connections": ["cook_brothers_app"]}
                ]
            },
        )
        assert agent_catalog.required_connections_for_app(app, db_session) == {
            "cook_brothers_app"
        }
        assert (
            agent_catalog.connection_display_name("cook_brothers_app", db_session)
            == "Norm Kitchen Sync"
        )


class TestTeamPayload:
    def _seed_team(self, db):
        _spec(db, "bamboohr")
        db.add(
            ComponentApiConfig(
                component_key="hiring_board",
                connector_name="bamboohr",
                action_name="load",
                path_template="//x",
            )
        )
        db.flush()
        _row(
            db,
            "bamboohr-app",
            "app",
            {
                "components": [
                    {
                        "key": "hiring_board",
                        "page": {"id": "hiring", "label": "Hiring", "icon": "Users"},
                    }
                ]
            },
            name="BambooHR",
        )
        _row(db, "norm-hr", "app", {"app_slug": "hiring"}, name="Norm HR")
        _row(
            db,
            "hr-agent",
            "agent",
            {
                "owns_agents": ["hr"],
                "tagline": "Runs hiring and employee records.",
                "unlocks": ["norm-hr", "bamboohr-app"],
            },
            price=1000,
            name="HR",
        )

    def test_gating_inactive_without_agent_rows(self, db_session):
        org = _make_organization(db_session)
        payload = agent_catalog.team_payload(org.id, db_session, db_session)
        assert payload["gating_active"] is False
        assert payload["members"] == []

    def test_member_apps_and_works_with(self, db_session):
        org = _make_organization(db_session)
        self._seed_team(db_session)
        payload = agent_catalog.team_payload(org.id, db_session, db_session)
        assert payload["gating_active"] is True
        (member,) = payload["members"]
        assert member["slug"] == "hr" and member["hired"] is True
        assert member["price_cents"] == 1000
        apps = {a["slug"]: a for a in member["apps"]}
        assert set(apps) == {"norm-hr", "bamboohr-app"}
        # the Norm-native App needs nothing; the BambooHR App inherits its
        # component's connector; the member's WORKS WITH is the union.
        assert apps["norm-hr"]["required_connections"] == []
        assert [c["connector"] for c in apps["bamboohr-app"]["required_connections"]] == [
            "bamboohr"
        ]
        assert member["works_with"] == ["bamboohr"]
        assert apps["bamboohr-app"]["pages"] == [
            {"id": "hiring", "label": "Hiring", "icon": "Users"}
        ]
        # every App has an enabled state; bundled + hired => on
        assert apps["bamboohr-app"]["enabled"] is True

    def test_switched_off_app_reports_disabled(self, db_session):
        from app.db.models import OrgAppEntitlement

        org = _make_organization(db_session)
        self._seed_team(db_session)
        db_session.add(
            OrgAppEntitlement(
                organization_id=org.id, app_slug="bamboohr-app", enabled=False
            )
        )
        db_session.flush()
        payload = agent_catalog.team_payload(org.id, db_session, db_session)
        apps = {a["slug"]: a for a in payload["members"][0]["apps"]}
        assert apps["bamboohr-app"]["enabled"] is False
        assert apps["norm-hr"]["enabled"] is True

    def test_reports_and_app_builder_are_always_included(self, db_session):
        org = _make_organization(db_session)
        self._seed_team(db_session)
        payload = agent_catalog.team_payload(org.id, db_session, db_session)
        assert payload["always_included"] == ["app_builder", "reports"]


class TestTeamEndpoint:
    def test_endpoint_shape(self, client, db_session, admin_user, admin_headers):
        org = _make_organization(db_session)
        _make_membership(db_session, admin_user, org)
        resp = client.get("/api/team", headers=admin_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["gating_active"] is False
        assert "members" in body and "always_included" in body

    def test_any_member_may_read(self, client, db_session, manager_user, manager_headers):
        org = _make_organization(db_session)
        _make_membership(db_session, manager_user, org)
        resp = client.get("/api/team", headers=manager_headers)
        assert resp.status_code == 200
