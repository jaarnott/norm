"""App components (28 Sep 2026): the screens Norm may open in an App-platform
app, and the inputs that say where each starts. Norm Hiring and Norm Training
are one component each, with navigation kept inside and optional inputs
(job/candidate, program/person) — so "show me the chef role's pipeline" opens
Hiring on that pipeline. See app/services/app_components.py."""

import json
import pathlib
import re
import uuid
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.db.config_models import MarketplaceApp
from app.db.models import App, AppVersion, OrgAppEntitlement, Thread
from app.services import agent_catalog
from app.services.app_components import (
    check_inputs,
    declared_components,
    pick_component,
    validate_components,
)
from tests.conftest import _make_membership, _make_organization, _make_user

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "app" / "builtin_apps"


@pytest.fixture(autouse=True)
def _fresh_cache():
    agent_catalog.invalidate_cache()
    yield
    agent_catalog.invalidate_cache()


class _NoClose:
    """The handler closes its config session; the test session must survive."""

    def __init__(self, session):
        self._s = session

    def __getattr__(self, name):
        return getattr(self._s, name)

    def close(self):
        pass


def _spec(slug):
    return json.loads((FIXTURES / f"{slug}.json").read_text())["spec"]


def _install(db, org, user, slug, spec, name=None):
    app = App(
        organization_id=org.id,
        created_by=user.id,
        slug=slug,
        name=name or slug.title(),
        agent="hr",
        visibility="private",
    )
    db.add(app)
    db.flush()
    ver = AppVersion(
        app_id=app.id, version=1, spec=spec, ui_source="<div/>", created_by=user.id
    )
    db.add(ver)
    db.flush()
    app.current_version_id = ver.id
    db.flush()
    return app


def _world(db):
    user = _make_user(db, email=f"{uuid.uuid4().hex[:8]}@t.local")
    org = _make_organization(db)
    _make_membership(db, user, org)
    thread = Thread(id=str(uuid.uuid4()), user_id=user.id, domain="hr", status="active")
    db.add(thread)
    db.flush()
    return user, org, thread


def _open(db, thread, **params):
    from app.agents.internal_tools import _open_app

    with patch("app.db.engine._ConfigSessionLocal", return_value=_NoClose(db)):
        return _open_app(params, db, thread.id)


class TestTheContract:
    def test_an_app_that_declares_nothing_is_one_component(self):
        comps = declared_components({}, slug="top-sellers", name="Top Sellers")
        assert comps == [
            {
                "key": "top_sellers",
                "label": "Top Sellers",
                "description": "",
                "page": True,
                "inputs": [],
            }
        ]

    def test_inputs_normalise_from_either_spelling(self):
        comps = declared_components(
            {
                "components": [
                    {"key": "x", "inputs": {"a": "one", "b": {"description": "two"}}}
                ]
            },
            slug="s",
            name="S",
        )
        assert comps[0]["inputs"] == [
            {"name": "a", "description": "one"},
            {"name": "b", "description": "two"},
        ]
        assert comps[0]["page"] is True  # a lone component is the app's page

    def test_a_malformed_declaration_is_named(self):
        problems = validate_components(
            {
                "components": [
                    {"key": "Bad Key"},
                    {"key": "ok", "inputs": ["job"]},
                    {"key": "ok"},
                ]
            }
        )
        assert any("snake_case" in p for p in problems)
        assert any("inputs must be an object" in p for p in problems)
        assert any("declared twice" in p for p in problems)
        assert validate_components({}) == []

    def test_undeclared_inputs_are_named_and_empty_ones_dropped(self):
        comp = declared_components(
            {"components": [{"key": "h", "inputs": {"job": "j"}}]}, slug="h", name="H"
        )[0]
        assert check_inputs(comp, {"job": "Chef", "shoe": "x"}) == (
            {"job": "Chef"},
            ["shoe"],
        )
        assert check_inputs(comp, {"job": ""}) == ({}, [])
        assert pick_component([comp], None) is comp
        assert pick_component([comp], "nope") is None


class TestSavingAnApp:
    def test_save_refuses_a_malformed_components_declaration(self, db_session):
        from app.services.app_runtime import save_app

        user, org, _ = _world(db_session)
        with pytest.raises(HTTPException) as err:
            save_app(
                db_session,
                user,
                {"name": "Thing", "spec": {"components": [{"key": "Not Snake"}]}},
            )
        assert err.value.status_code == 400
        assert "snake_case" in err.value.detail


class TestTheFirstPartyApps:
    """The fixtures must declare exactly the inputs their pages act on — an
    input Norm offers but the page ignores would open the app in the wrong
    place, silently."""

    @pytest.mark.parametrize("slug", ["hiring", "training"])
    def test_declared_inputs_are_the_ones_the_page_reads(self, slug):
        spec = _spec(slug)
        assert validate_components(spec) == []
        comps = declared_components(spec, slug=slug, name=slug)
        assert len(comps) == 1 and comps[0]["key"] == slug and comps[0]["page"]
        declared = {i["name"] for i in comps[0]["inputs"]}
        html = (FIXTURES / f"{slug}.html").read_text()
        read = set(re.findall(r"\bins\.([a-z_]+)", html))
        assert declared == read

    @pytest.mark.parametrize("slug", ["hiring", "training"])
    def test_the_locate_op_takes_the_named_inputs(self, slug):
        # `view` is handled in the page; everything else goes to `locate`.
        comps = declared_components(_spec(slug), slug=slug, name=slug)
        named = {i["name"] for i in comps[0]["inputs"]} - {"view"}
        logic = (FIXTURES / f"{slug}.py").read_text()
        locate = logic[logic.index('if op == "locate"') :]
        locate = locate[: locate.index("\n    if op ==", 1)]
        assert all(f'params.get("{n}")' in locate for n in named)


class TestOpenApp:
    def test_opens_by_the_catalog_name_at_a_job(self, db_session):
        _, org, thread = _world(db_session)
        db_session.add(
            MarketplaceApp(
                slug="hiring",
                name="Norm Hiring",
                description="",
                tier="app",
                bundled=True,
                composition={"member": "hr", "app_slug": "hiring"},
            )
        )
        db_session.flush()
        out = _open(db_session, thread, app="Norm Hiring", inputs={"job": "Head Chef"})
        assert out["success"] is True, out
        assert out["data"] == {
            "slug": "hiring",
            "name": "Norm Hiring",
            "component": "hiring",
            "inputs": {"job": "Head Chef"},
        }

    def test_an_undeclared_input_is_refused_with_the_valid_ones(self, db_session):
        user, org, thread = _world(db_session)
        out = _open(db_session, thread, app="hiring", inputs={"shift": "Friday"})
        assert out["success"] is False
        assert out["data"] == {}  # a refusal never paints an empty app card
        assert "job" in out["error"] and "candidate" in out["error"]

    def test_an_unknown_app_lists_the_ones_there_are(self, db_session):
        user, org, thread = _world(db_session)
        out = _open(db_session, thread, app="Payroll")
        assert out["success"] is False and "Training (training)" in out["error"]

    def test_an_app_that_is_switched_off_cannot_be_opened(self, db_session):
        user, org, thread = _world(db_session)
        db_session.add_all(
            [
                MarketplaceApp(
                    slug="hr-agent",
                    name="HR",
                    description="",
                    tier="agent",
                    bundled=True,
                    composition={"owns_agents": ["hr"]},
                ),
                MarketplaceApp(
                    slug="hiring",
                    name="Norm Hiring",
                    description="",
                    tier="app",
                    bundled=True,
                    composition={"member": "hr", "app_slug": "hiring"},
                ),
                OrgAppEntitlement(
                    organization_id=org.id, app_slug="hiring", enabled=False
                ),
            ]
        )
        db_session.flush()
        out = _open(db_session, thread, app="Norm Hiring")
        assert out["success"] is False and out["data"] == {}

    def test_it_never_reaches_the_mcp_surface(self):
        from app.mcp.projection import MCP_DENYLIST

        assert ("norm", "open_app") in MCP_DENYLIST


class TestTheTeamPageShowsThem:
    def test_a_fronted_app_lists_its_component_and_inputs(self, db_session):
        user, org, _ = _world(db_session)
        db_session.add(
            MarketplaceApp(
                slug="training",
                name="Norm Training",
                description="",
                tier="app",
                bundled=True,
                composition={"member": "hr", "app_slug": "training"},
            )
        )
        db_session.flush()
        payload = agent_catalog.team_payload(org.id, db_session, db_session)
        app = next(a for a in payload["apps"] if a["slug"] == "training")
        comp = next(c for c in app["components"] if c["key"] == "training")
        assert comp["app_page"] is True and comp["page"] is None
        assert {i["name"] for i in comp["inputs"]} == {"view", "program", "person"}

    def test_a_custom_app_without_a_declaration_still_has_one(self, db_session):
        user, org, _ = _world(db_session)
        _install(db_session, org, user, "top-sellers", {}, name="Top Sellers")
        payload = agent_catalog.team_payload(org.id, db_session, db_session)
        app = next(a for a in payload["apps"] if a["slug"] == "custom:top-sellers")
        assert [c["key"] for c in app["components"]] == ["top_sellers"]
