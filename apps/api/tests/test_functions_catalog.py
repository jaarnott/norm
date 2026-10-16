"""Norm Functions in the catalog: its team member and its App, declared once
(services/functions/catalog.py) and written by the seed — and, until the seed
has run, stood in for wherever an organisation's functions_mode is on.

Both states are pinned: no catalog rows (local testing, and the days between
the deploy and seeding the shared config DB) and seeded rows (normal
entitlement AND mode != off). In both, ``off`` wins over everything."""

import importlib.util
import pathlib
import re

import pytest

from app.db.config_models import MarketplaceApp
from app.db.models import OrgAppEntitlement
from app.services import agent_catalog
from app.services.entitlements import (
    agent_entitled,
    apps_on,
    entitled_slugs,
    hired_agent_slugs,
)
from app.services.functions import catalog
from app.services.functions.entitlement import functions_enabled
from tests.conftest import _make_membership, _make_organization, _make_user

REPO = pathlib.Path(__file__).resolve().parents[3]
FUNCTIONS_COMPONENTS = {
    k for k in agent_catalog.REGISTERED_COMPONENTS if k.startswith("functions_")
}


@pytest.fixture(autouse=True)
def _fresh_cache():
    agent_catalog.invalidate_cache()
    yield
    agent_catalog.invalidate_cache()


def _seed_apps() -> dict[str, dict]:
    spec = importlib.util.spec_from_file_location(
        "seed", REPO / "apps/api/scripts/sync_marketplace_catalog.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {a["slug"]: a for a in mod.APPS}


def _org(db, mode="off", name="Cook Bros"):
    org = _make_organization(db, name=name)
    org.functions_mode = mode
    user = _make_user(db)
    _make_membership(db, user, org)
    db.flush()
    return org, user


def _procurement_row(db):
    """Any member row arms team gating — the state every environment is in."""
    db.add(
        MarketplaceApp(
            slug="procurement-agent",
            name="Procurement",
            description="",
            tier="agent",
            bundled=True,
            composition={"owns_agents": ["procurement"]},
        )
    )
    db.flush()


def _seed_functions_rows(db, *, price=0, key=None):
    """The two rows as the seed writes them (priced as asked)."""
    for row in catalog.stand_in_rows(catalog.CATALOG_SLUGS):
        if row.tier == "agent":
            row.price_cents, row.stripe_price_key = price, key
        db.add(row)
    db.flush()


def _functions_member(payload):
    return next((m for m in payload["members"] if m["slug"] == "functions"), None)


def _functions_app(payload):
    return next((a for a in payload["apps"] if a["slug"] == "functions"), None)


class TestOneDeclaration:
    def test_the_seed_writes_the_declared_member_and_app(self):
        apps = _seed_apps()
        member, app = apps["functions-agent"], apps["functions"]
        assert member["tier"] == "agent"
        assert member["composition"]["owns_agents"] == ["functions"]
        assert app["tier"] == "app" and app["composition"]["member"] == "functions"
        assert app["name"] == "Norm Functions"

    def test_the_stand_ins_are_the_rows_the_seed_writes(self):
        """No drift between what the fallback shows and what the seed will
        write — except the price: nothing bills for a row that isn't there."""
        apps = _seed_apps()
        for row in catalog.stand_in_rows(catalog.CATALOG_SLUGS):
            seeded = apps[row.slug]
            for field in ("name", "description", "icon", "tier", "composition"):
                assert getattr(row, field) == seeded[field], (row.slug, field)
            assert row.bundled is seeded.get("bundled", True)
            assert row.price_cents == 0

    def test_the_app_owns_the_seven_functions_components(self):
        keys = [c["key"] for c in catalog.APP["components"]]
        assert len(keys) == 7 and set(keys) == FUNCTIONS_COMPONENTS

    def test_its_pages_are_the_web_registrys(self):
        src = (REPO / "apps/web/app/components/pages/pageRegistry.ts").read_text()
        web = {}
        for m in re.finditer(
            r"\{\s*id: '(functions-[a-z]+)',(.*?)loadAction", src, re.S
        ):
            body = m.group(2)
            web[m.group(1)] = {
                "app": re.search(r"app: '(\w+)'", body).group(1),
                "label": re.search(r"label: '([^']+)'", body).group(1),
                "icon": re.search(r"icon: (\w+)", body).group(1),
                "agent": re.search(r"agent: '(\w+)'", body).group(1),
                "component": re.search(r"component: '(\w+)'", body).group(1),
            }
        declared = {
            c["page"]["id"]: {
                "app": catalog.APP["slug"],
                "label": c["page"]["label"],
                "icon": c["page"]["icon"],
                "agent": catalog.MEMBER_SLUG,
                "component": c["key"],
            }
            for c in catalog.APP["components"]
        }
        assert web == declared

    def test_the_note_fits_the_prompt_budget(self):
        from app.services.agent_config_service import APP_NOTE_MAX_CHARS

        assert 0 < len(catalog.APP["note"]) <= APP_NOTE_MAX_CHARS

    def test_the_member_is_answered_by_the_norm_agent(self):
        from app.agents.registry import MEMBERS, get_agent, norm_agent

        assert catalog.MEMBER_SLUG in MEMBERS
        assert get_agent(catalog.MEMBER_SLUG) is norm_agent()


class TestBeforeTheSeed:
    """No functions rows in the catalog: the mode alone decides, and the team
    payload carries stand-ins built from the declaration."""

    def test_mode_off_shows_nothing(self, db_session):
        _procurement_row(db_session)
        org, _ = _org(db_session, "off")
        assert "functions" not in hired_agent_slugs(org.id, db_session, db_session)
        assert "functions" not in apps_on(org.id, db_session, db_session)
        assert not agent_entitled("functions", org.id, db_session, db_session)
        assert not functions_enabled(db_session, db_session, org.id)
        payload = agent_catalog.team_payload(org.id, db_session, db_session)
        assert _functions_member(payload) is None
        assert _functions_app(payload) is None

    @pytest.mark.parametrize("mode", ["shadow", "live"])
    def test_mode_on_hires_the_stand_in_member_and_its_app(self, db_session, mode):
        _procurement_row(db_session)
        org, _ = _org(db_session, mode)
        assert "functions" in hired_agent_slugs(org.id, db_session, db_session)
        assert "functions" in apps_on(org.id, db_session, db_session)
        assert agent_entitled("functions", org.id, db_session, db_session)
        assert functions_enabled(db_session, db_session, org.id)

        payload = agent_catalog.team_payload(org.id, db_session, db_session)
        member = _functions_member(payload)
        assert member["catalog_slug"] == "functions-agent"
        assert member["name"] == "Functions"
        assert member["hired"] is True
        # nothing in the catalog to hire, retire or bill yet
        assert member["hireable"] is False and member["price_cents"] == 0
        (app,) = member["apps"]
        assert app["slug"] == "functions" and app["enabled"] is True
        assert app["switchable"] is False
        assert app["required_connections"] == []  # runs on Norm
        assert [p["id"] for p in app["pages"]] == [
            c["page"]["id"] for c in catalog.APP["components"]
        ]
        assert _functions_app(payload) == app

    def test_one_org_on_beside_another_off(self, db_session):
        _procurement_row(db_session)
        on_org, _ = _org(db_session, "shadow", name="On Co")
        off_org, _ = _org(db_session, "off", name="Off Co")
        assert _functions_member(
            agent_catalog.team_payload(on_org.id, db_session, db_session)
        )
        assert not _functions_member(
            agent_catalog.team_payload(off_org.id, db_session, db_session)
        )
        assert "functions" not in hired_agent_slugs(off_org.id, db_session, db_session)

    def test_gating_inactive_still_lists_the_stand_ins(self, db_session):
        org, _ = _org(db_session, "shadow")
        payload = agent_catalog.team_payload(org.id, db_session, db_session)
        assert payload["gating_active"] is False
        assert _functions_member(payload)["hired"] is True
        assert _functions_app(payload)["enabled"] is True

    def test_other_members_are_untouched(self, db_session):
        _procurement_row(db_session)
        org, _ = _org(db_session, "shadow")
        payload = agent_catalog.team_payload(org.id, db_session, db_session)
        procurement = next(m for m in payload["members"] if m["slug"] == "procurement")
        assert procurement["hireable"] is True and procurement["hired"] is True


class TestAfterTheSeed:
    """Seeded rows: normal entitlement, and the mode still has the last word."""

    def test_mode_off_hides_the_rows_and_their_entitlement(self, db_session):
        _procurement_row(db_session)
        _seed_functions_rows(db_session)
        org, _ = _org(db_session, "off")
        assert (
            not entitled_slugs(org.id, db_session, db_session) & catalog.CATALOG_SLUGS
        )
        assert "functions" not in hired_agent_slugs(org.id, db_session, db_session)
        assert "functions" not in apps_on(org.id, db_session, db_session)
        assert not agent_entitled("functions", org.id, db_session, db_session)
        assert not functions_enabled(db_session, db_session, org.id)
        payload = agent_catalog.team_payload(org.id, db_session, db_session)
        assert _functions_member(payload) is None and _functions_app(payload) is None

    def test_mode_on_is_ordinary_entitlement(self, db_session):
        _procurement_row(db_session)
        _seed_functions_rows(db_session)
        org, _ = _org(db_session, "shadow")
        assert catalog.CATALOG_SLUGS <= entitled_slugs(org.id, db_session, db_session)
        assert functions_enabled(db_session, db_session, org.id)
        payload = agent_catalog.team_payload(org.id, db_session, db_session)
        member = _functions_member(payload)
        assert member["hired"] is True and member["hireable"] is True
        app = _functions_app(payload)
        assert app["enabled"] is True and app["switchable"] is True

    def test_retiring_the_member_switches_functions_off(self, db_session):
        _procurement_row(db_session)
        _seed_functions_rows(db_session)
        org, _ = _org(db_session, "live")
        db_session.add(
            OrgAppEntitlement(
                organization_id=org.id, app_slug="functions-agent", enabled=False
            )
        )
        db_session.flush()
        assert "functions" not in hired_agent_slugs(org.id, db_session, db_session)
        assert "functions" not in apps_on(org.id, db_session, db_session)
        assert not agent_entitled("functions", org.id, db_session, db_session)
        assert not functions_enabled(db_session, db_session, org.id)
        member = _functions_member(
            agent_catalog.team_payload(org.id, db_session, db_session)
        )
        assert member["hired"] is False and member["hireable"] is True

    def test_switching_the_app_off_keeps_the_member(self, db_session):
        _procurement_row(db_session)
        _seed_functions_rows(db_session)
        org, _ = _org(db_session, "live")
        db_session.add(
            OrgAppEntitlement(
                organization_id=org.id, app_slug="functions", enabled=False
            )
        )
        db_session.flush()
        assert "functions" in hired_agent_slugs(org.id, db_session, db_session)
        assert not functions_enabled(db_session, db_session, org.id)
        assert (
            _functions_app(agent_catalog.team_payload(org.id, db_session, db_session))[
                "enabled"
            ]
            is False
        )

    def test_an_org_whose_mode_is_off_is_never_billed_for_the_member(self, db_session):
        from app.services.billing_service import get_agent_apps

        _procurement_row(db_session)
        _seed_functions_rows(db_session, price=1000, key="agent")
        org, _ = _org(db_session, "off")

        def billed():
            rows = get_agent_apps(db_session, org.id, db_session)
            return next(a for a in rows if a["slug"] == "functions-agent")["enabled"]

        assert billed() is False
        org.functions_mode = "shadow"
        db_session.flush()
        assert billed() is True


class TestTeamEndpoint:
    def test_a_member_of_an_org_with_functions_on_sees_it(self, client, db_session):
        from app.auth.security import create_access_token

        _procurement_row(db_session)
        _, user = _org(db_session, "shadow")
        r = client.get(
            "/api/team",
            headers={
                "Authorization": f"Bearer {create_access_token({'sub': user.id})}"
            },
        )
        assert r.status_code == 200
        assert _functions_member(r.json())["hired"] is True

    def test_a_member_of_an_org_with_functions_off_does_not(self, client, db_session):
        from app.auth.security import create_access_token

        _procurement_row(db_session)
        _, user = _org(db_session, "off")
        r = client.get(
            "/api/team",
            headers={
                "Authorization": f"Bearer {create_access_token({'sub': user.id})}"
            },
        )
        assert r.status_code == 200
        assert _functions_member(r.json()) is None
