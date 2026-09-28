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
            # auth "none" is what marks a pipe as Norm's own (no credential)
            auth_type="none" if mode == "internal" else "oauth2",
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
        assert [
            c["connector"] for c in apps["bamboohr-app"]["required_connections"]
        ] == ["bamboohr"]
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

    def test_any_member_may_read(
        self, client, db_session, manager_user, manager_headers
    ):
        org = _make_organization(db_session)
        _make_membership(db_session, manager_user, org)
        resp = client.get("/api/team", headers=manager_headers)
        assert resp.status_code == 200


class TestEveryAppIsBoundToAMember:
    """Every App — catalog, community or built by the team — is bound to the
    member(s) that use it and has an enabled state (owner, 27 Sep: the member
    card lists all its Apps with on/off; the All-apps list shows the binding)."""

    def _seed(self, db):
        TestTeamPayload()._seed_team(db)

    def _custom_app(self, db, org, slug, agent):
        from app.db.models import App

        db.add(App(organization_id=org.id, slug=slug, name=slug.title(), agent=agent))
        db.flush()

    def test_custom_app_binds_to_its_member_and_defaults_on(self, db_session):
        org = _make_organization(db_session)
        self._seed(db_session)
        self._custom_app(db_session, org, "rota-helper", "hr")
        payload = agent_catalog.team_payload(org.id, db_session, db_session)
        hr = payload["members"][0]
        custom = [a for a in hr["apps"] if a["kind"] == "custom"]
        assert [a["slug"] for a in custom] == ["custom:rota-helper"]
        assert custom[0]["enabled"] is True
        flat = {a["slug"]: a for a in payload["apps"]}
        assert flat["custom:rota-helper"]["bound_to"] == ["hr"]

    def test_custom_app_without_agent_binds_to_app_builder(self, db_session):
        org = _make_organization(db_session)
        self._seed(db_session)
        self._custom_app(db_session, org, "scratchpad", None)
        payload = agent_catalog.team_payload(org.id, db_session, db_session)
        builder = next(m for m in payload["included"] if m["slug"] == "app_builder")
        assert [a["slug"] for a in builder["apps"]] == ["custom:scratchpad"]

    def test_switching_a_custom_app_off(self, db_session):
        from app.db.models import OrgAppEntitlement

        org = _make_organization(db_session)
        self._seed(db_session)
        self._custom_app(db_session, org, "rota-helper", "hr")
        db_session.add(
            OrgAppEntitlement(
                organization_id=org.id, app_slug="custom:rota-helper", enabled=False
            )
        )
        db_session.flush()
        flat = {
            a["slug"]: a
            for a in agent_catalog.team_payload(org.id, db_session, db_session)["apps"]
        }
        assert flat["custom:rota-helper"]["enabled"] is False

    def test_retiring_the_member_turns_its_custom_apps_off(self, db_session):
        from app.db.models import OrgAppEntitlement

        org = _make_organization(db_session)
        self._seed(db_session)
        self._custom_app(db_session, org, "rota-helper", "hr")
        db_session.add(
            OrgAppEntitlement(
                organization_id=org.id, app_slug="hr-agent", enabled=False
            )
        )
        db_session.flush()
        flat = {
            a["slug"]: a
            for a in agent_catalog.team_payload(org.id, db_session, db_session)["apps"]
        }
        assert flat["custom:rota-helper"]["enabled"] is False

    def test_included_member_owns_catalog_apps_naming_it(self, db_session):
        org = _make_organization(db_session)
        self._seed(db_session)
        _row(
            db_session,
            "weekly-venue-performance",
            "app",
            {"app_slug": "weekly-venue-performance", "agents": ["reports"]},
        )
        payload = agent_catalog.team_payload(org.id, db_session, db_session)
        reports = next(m for m in payload["included"] if m["slug"] == "reports")
        app = next(
            a for a in reports["apps"] if a["slug"] == "weekly-venue-performance"
        )
        assert app["enabled"] is True  # reports is always hired; app bundled

    def test_other_orgs_custom_apps_never_appear(self, db_session):
        org = _make_organization(db_session)
        other = _make_organization(db_session)
        self._seed(db_session)
        self._custom_app(db_session, other, "their-app", "hr")
        slugs = {
            a["slug"]
            for a in agent_catalog.team_payload(org.id, db_session, db_session)["apps"]
        }
        assert "custom:their-app" not in slugs


class TestCustomAppSwitchEndpoint:
    def test_owner_switches_own_custom_app(
        self, client, db_session, admin_user, admin_headers
    ):
        from app.db.models import App, OrgAppEntitlement

        org = _make_organization(db_session)
        _make_membership(db_session, admin_user, org)
        db_session.add(
            App(organization_id=org.id, slug="mine", name="Mine", agent="hr")
        )
        db_session.flush()
        r = client.post("/api/marketplace/custom:mine/disable", headers=admin_headers)
        assert r.status_code == 200
        row = (
            db_session.query(OrgAppEntitlement)
            .filter_by(organization_id=org.id, app_slug="custom:mine")
            .one()
        )
        assert row.enabled is False

    def test_cannot_switch_another_orgs_app(
        self, client, db_session, admin_user, admin_headers
    ):
        from app.db.models import App

        org = _make_organization(db_session)
        other = _make_organization(db_session)
        _make_membership(db_session, admin_user, org)
        db_session.add(
            App(organization_id=other.id, slug="theirs", name="T", agent="hr")
        )
        db_session.flush()
        r = client.post("/api/marketplace/custom:theirs/disable", headers=admin_headers)
        assert r.status_code == 404


class TestFrontedCustomAppsAreOneApp:
    def test_catalog_pointer_and_custom_row_list_once(self, db_session):
        from app.db.models import App, OrgAppEntitlement
        from app.services.entitlements import apps_on

        org = _make_organization(db_session)
        TestTeamPayload()._seed_team(db_session)  # norm-hr fronts app_slug 'hiring'
        db_session.add(
            App(organization_id=org.id, slug="hiring", name="Hiring", agent="hr")
        )
        db_session.flush()
        slugs = [
            a["slug"]
            for a in agent_catalog.team_payload(org.id, db_session, db_session)["apps"]
        ]
        assert "custom:hiring" not in slugs and "norm-hr" in slugs
        # the catalog row's switch governs the pinned page key too
        assert "custom:hiring" in apps_on(org.id, db_session, db_session)
        db_session.add(
            OrgAppEntitlement(organization_id=org.id, app_slug="norm-hr", enabled=False)
        )
        db_session.flush()
        assert "custom:hiring" not in apps_on(org.id, db_session, db_session)
