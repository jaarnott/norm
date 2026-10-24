"""Team apps (docs/plans/team-apps-skeleton.md, 24 Oct 2026): a ``tier='team'``
catalog row (Kitchen, Front of house, Managers) is a second place a job app's
screens can show, plus the tools Norm may use for a person whose role reaches
only that team app. The catalog says what a team app contains, the
organisation's switches say whether a screen is there at all, the role says
who sees it.

Module-level fixtures are general: later classes (Today providers, open_app)
share them."""

import importlib.util
import pathlib
import re
import uuid
from unittest.mock import patch

import pytest

from app.db.config_models import MarketplaceApp
from app.db.models import Connection, OrgAppEntitlement
from app.services import agent_catalog, entitlements, screens, today, today_training
from tests.conftest import _make_membership
from tests.org_helpers import auth_headers, make_org, make_user, make_venue

# The Today tests reuse the staff directory's organisation and its test app
# (the shared test app does not mount the staff routes).
from tests.test_staff_directory import _app as _staff_app  # noqa: E402
from tests.test_staff_directory import _world as _staff_world  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _fresh_cache():
    agent_catalog.invalidate_cache()
    yield
    agent_catalog.invalidate_cache()


def _seed():
    """The catalog seed script, loaded as a module (its APPS and helpers)."""
    spec = importlib.util.spec_from_file_location(
        "seed", REPO / "apps/api/scripts/sync_marketplace_catalog.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _row(db, slug, tier, composition, *, name=None, icon=None, bundled=True):
    row = MarketplaceApp(
        slug=slug,
        name=name or slug,
        description="",
        icon=icon,
        tier=tier,
        bundled=bundled,
        composition=composition,
    )
    db.add(row)
    db.flush()  # one flush per row: created_at is the catalog's order
    return row


def _screen(
    key, page_id, label, *, connection=None, teams=(), team=None, team_only=False
):
    """A component with a page, placed as the seed's ``_c`` stores it: a
    ``teams`` list (``team=`` is the one-team shorthand)."""
    c = {"key": key, "page": {"id": page_id, "label": label}}
    if connection:
        c["connection"] = connection
    placed = list(teams) + ([team] if team else [])
    if placed:
        c["teams"] = placed
    if team_only:
        c["team_only"] = True
    return c


def _team_rows(db, *, kitchen_tools=("lh.get_recipes",)):
    """The three team rows, inserted out of seed order on purpose."""
    _row(
        db,
        "managers",
        "team",
        {"tagline": "Sign-offs.", "uses_tools": []},
        name="Managers",
        icon="🛡",
    )
    _row(
        db,
        "kitchen",
        "team",
        {"tagline": "The kitchen's day.", "uses_tools": list(kitchen_tools)},
        name="Kitchen",
        icon="🍲",
    )
    _row(
        db,
        "front_of_house",
        "team",
        {"tagline": "The floor's day.", "uses_tools": []},
        name="Front of house",
        icon="🛎",
    )


def _world(db, *, team_rows=True, loaded=True):
    """One agent with three job apps: Recipes (a Loaded screen also in
    Kitchen), Prep (a Kitchen-only screen with no system) and Floor (a screen
    also in Front of house). Nothing is placed on Managers."""
    org = make_org(db, "Lott")
    venue = make_venue(db, "Cafe", org)
    if loaded:
        db.add(
            Connection(
                connector_name="loadedhub", venue_id=venue.id, enabled="true", config={}
            )
        )
        db.flush()
    _row(db, "executive-chef-agent", "agent", {"owns_agents": ["executive_chef"]})
    _row(
        db,
        "recipes",
        "app",
        {
            "member": "executive_chef",
            "tools": ["lh.get_recipes"],
            "components": [
                _screen(
                    "recipe_editor",
                    "recipes",
                    "Recipes",
                    connection="loaded",
                    team="kitchen",
                )
            ],
        },
        name="Recipes & Costing",
    )
    _row(
        db,
        "prep",
        "app",
        {
            "member": "executive_chef",
            "components": [
                _screen("prep_board", "prep", "Prep", team="kitchen", team_only=True)
            ],
        },
        name="Prep",
    )
    _row(
        db,
        "floor",
        "app",
        {
            "member": "executive_chef",
            "components": [
                _screen("floor_board", "floor", "Floor", team="front_of_house"),
                _screen("floor_report", "floor-report", "Floor report"),
            ],
        },
        name="Floor",
    )
    if team_rows:
        _team_rows(db)
    return org


def _place_checklists(db):
    """A fourth job app whose one screen is the same in Kitchen and Front of
    house (the owner's rule of 24 Oct: one screen, placed in several team
    apps, switched per team app)."""
    return _row(
        db,
        "checklists",
        "app",
        {
            "member": "executive_chef",
            "components": [
                _screen(
                    "checklist_board",
                    "checklists",
                    "Checklists",
                    teams=["kitchen", "front_of_house"],
                )
            ],
        },
        name="Checklists",
    )


def _team(db, org, slug):
    return next(t for t in screens.team_apps(db, db, org.id) if t["slug"] == slug)


def _kitchen(db, org):
    return _team(db, org, "kitchen")


def _page_ids(team):
    return [s["page_id"] for s in team["screens"]]


class TestCatalog:
    """The seed's team rows and the component keys that place a screen."""

    def test_the_three_team_rows(self):
        seed = _seed()
        rows = [a for a in seed.APPS if a["tier"] == "team"]
        assert rows == seed.TEAM_APPS
        assert [r["slug"] for r in rows] == list(screens.TEAM_SLUGS)
        assert [r["name"] for r in rows] == ["Kitchen", "Front of house", "Managers"]
        for r in rows:
            comp = r["composition"]
            assert set(comp) == {"tagline", "uses_tools"}
            assert r["description"] == comp["tagline"]
            assert r["bundled"] is True and r["price_cents"] == 0
            assert r["icon"]
        by_slug = {r["slug"]: r for r in rows}
        assert by_slug["kitchen"]["composition"]["uses_tools"] == [
            "loadedhub.get_recipes"
        ]
        assert by_slug["front_of_house"]["composition"]["uses_tools"] == []
        assert by_slug["managers"]["composition"]["uses_tools"] == []

    def test_team_icons_resolve_to_a_line_icon(self):
        """resolveAppIcon prints no emoji: each team icon must be one it maps
        (apps/web/app/components/ui/appIcons.ts BY_EMOJI)."""
        src = (REPO / "apps/web/app/components/ui/appIcons.ts").read_text()
        block = src[
            src.index("const BY_EMOJI") : src.index("};", src.index("const BY_EMOJI"))
        ]
        mapped = set(re.findall(r"'([^']+)':", block))
        for r in _seed().TEAM_APPS:
            assert r["icon"].replace("️", "") in mapped, r["slug"]

    def test_a_component_carries_its_placements_as_a_list(self):
        """``teams`` is always a list and ``team`` is never stored: the
        one-team shorthand becomes a one-entry list."""
        seed = _seed()
        page = seed._p("prep", "Prep", "ClipboardList")
        one = seed._c("prep_board", page=page, team="kitchen")
        assert one["teams"] == ["kitchen"] and "team_only" not in one
        assert "team" not in one
        two = seed._c("prep_board", page=page, teams=["kitchen", "front_of_house"])
        assert two["teams"] == ["kitchen", "front_of_house"]
        only = seed._c("prep_board", page=page, teams=["kitchen"], team_only=True)
        assert only["teams"] == ["kitchen"] and only["team_only"] is True
        plain = seed._c("prep_board", page=page)
        assert "teams" not in plain and "team" not in plain
        assert "team_only" not in plain

    def test_every_seeded_placement_is_a_valid_teams_list(self):
        """What the seed's validator refuses, pinned on the seed data itself:
        each entry names a team row, no team is listed twice, ``team_only``
        has a non-empty list, and the old ``team`` key is gone."""
        seed = _seed()
        team_slugs = {a["slug"] for a in seed.APPS if a["tier"] == "team"}
        for a in seed.APPS:
            if a["tier"] != "app":
                continue
            for c in a["composition"].get("components") or []:
                assert "team" not in c, f"{a['slug']}/{c['key']} still uses team"
                teams = c.get("teams")
                if teams is None:
                    assert not c.get("team_only"), f"{a['slug']}/{c['key']}"
                    continue
                assert isinstance(teams, list) and teams, f"{a['slug']}/{c['key']}"
                assert len(set(teams)) == len(teams), f"{a['slug']}/{c['key']}"
                assert set(teams) <= team_slugs, f"{a['slug']}/{c['key']}"
                assert c.get("page"), f"{a['slug']}/{c['key']} is not a screen"


class TestScreens:
    """services/screens.py: what a team app contains for an organisation."""

    def test_app_screens_say_where_a_screen_is_placed(self, db_session):
        _world(db_session)
        _place_checklists(db_session)
        rows = {r.slug: r for r in entitlements._catalog(db_session)}
        recipes = screens.app_screens(rows["recipes"])[0]
        assert (recipes["teams"], recipes["team_only"]) == (["kitchen"], False)
        prep = screens.app_screens(rows["prep"])[0]
        assert (prep["teams"], prep["team_only"]) == (["kitchen"], True)
        report = screens.app_screens(rows["floor"])[1]
        assert (report["teams"], report["team_only"]) == ([], False)
        both = screens.app_screens(rows["checklists"])[0]
        assert both["teams"] == ["kitchen", "front_of_house"]
        assert "team" not in both
        # an App-platform pseudo-screen is placed nowhere
        hiring = _row(
            db_session,
            "hiring",
            "app",
            {"member": "executive_chef", "app_slug": "hiring"},
        )
        pseudo = screens.app_screens(hiring)[0]
        assert (pseudo["teams"], pseudo["team_only"]) == ([], False)
        # a stale or malformed key reads as no placement, never a crash
        odd = _row(
            db_session,
            "odd",
            "app",
            {
                "member": "executive_chef",
                "components": [
                    {"key": "odd", "page": {"id": "odd"}, "teams": "kitchen"},
                    {"key": "old", "page": {"id": "old"}, "team": "kitchen"},
                ],
            },
        )
        assert [sc["teams"] for sc in screens.app_screens(odd)] == [[], []]

    def test_team_rows_come_in_seed_order(self, db_session):
        _world(db_session)  # inserts managers, kitchen, front_of_house
        assert [r.slug for r in screens.team_rows(db_session)] == list(
            screens.TEAM_SLUGS
        )

    def test_each_team_app_lists_the_screens_placed_on_it(self, db_session):
        org = _world(db_session)
        teams = screens.team_apps(db_session, db_session, org.id)
        assert [t["slug"] for t in teams] == ["kitchen", "front_of_house", "managers"]
        kitchen, foh, managers = teams
        # the App's component order, Apps in catalog order
        assert _page_ids(kitchen) == ["recipes", "prep"]
        assert _page_ids(foh) == ["floor"]
        assert managers["screens"] == []  # still returned, empty
        assert kitchen["name"] == "Kitchen" and kitchen["icon"] == "🍲"
        assert kitchen["tagline"] == "The kitchen's day."
        assert kitchen["tools"] == [{"key": "lh.get_recipes", "label": "Get recipes"}]
        assert foh["tools"] == []
        recipes = kitchen["screens"][0]
        assert recipes == {
            "page_id": "recipes",
            "label": "Recipes",
            "component": "recipe_editor",
            "connection": "loaded",
            "connection_name": "Loaded",
            "app_slug": "recipes",
            "app_name": "Recipes & Costing",
            "member": "executive_chef",
            "teams": ["kitchen"],
            "team_only": False,
        }
        assert kitchen["screens"][1]["team_only"] is True

    def test_a_screen_in_two_team_apps_is_the_same_screen_in_both(self, db_session):
        org = _world(db_session)
        _place_checklists(db_session)
        assert _page_ids(_kitchen(db_session, org)) == ["recipes", "prep", "checklists"]
        foh = _team(db_session, org, "front_of_house")
        assert _page_ids(foh) == ["floor", "checklists"]
        in_kitchen = _kitchen(db_session, org)["screens"][2]
        in_foh = foh["screens"][1]
        assert in_kitchen == in_foh  # one screen, not two
        assert in_kitchen["teams"] == ["kitchen", "front_of_house"]
        assert in_kitchen["component"] == "checklist_board"

    def test_a_job_app_switched_off_takes_its_screen_out(self, db_session):
        org = _world(db_session)
        db_session.add(
            OrgAppEntitlement(organization_id=org.id, app_slug="recipes", enabled=False)
        )
        db_session.flush()
        assert _page_ids(_kitchen(db_session, org)) == ["prep"]

    def test_a_screen_whose_system_is_missing_is_gone(self, db_session):
        org = _world(db_session, loaded=False)
        assert _page_ids(_kitchen(db_session, org)) == ["prep"]  # Prep needs no system

    def test_a_placement_switched_off_leaves_the_other_team_app(self, db_session):
        """``screen:checklists@kitchen`` off: Checklists leaves Kitchen and
        stays in Front of house; switching it back on brings it back."""
        org = _world(db_session)
        _place_checklists(db_session)
        row = OrgAppEntitlement(
            organization_id=org.id,
            app_slug=screens.placement_key("checklists", "kitchen"),
            enabled=False,
        )
        db_session.add(row)
        db_session.flush()
        assert screens.placements_off(org.id, db_session) == {("checklists", "kitchen")}
        assert _page_ids(_kitchen(db_session, org)) == ["recipes", "prep"]
        assert _page_ids(_team(db_session, org, "front_of_house")) == [
            "floor",
            "checklists",
        ]
        # the agent's menu is not touched by a placement switch
        assert screens.screens_off(org.id, db_session) == set()
        assert screens.pages_hidden(org.id, db_session, db_session) == set()
        row.enabled = True
        db_session.flush()
        assert screens.placements_off(org.id, db_session) == set()
        assert _page_ids(_kitchen(db_session, org)) == ["recipes", "prep", "checklists"]

    def test_the_agent_menu_switch_does_not_touch_team_placements(self, db_session):
        """``screen:recipes`` off hides Recipes in the Executive Chef's menu
        and nowhere else: Kitchen still has it."""
        org = _world(db_session)
        db_session.add(
            OrgAppEntitlement(
                organization_id=org.id, app_slug="screen:recipes", enabled=False
            )
        )
        db_session.flush()
        assert screens.pages_hidden(org.id, db_session, db_session) == {"recipes"}
        assert _page_ids(_kitchen(db_session, org)) == ["recipes", "prep"]
        assert screens.placements_off(org.id, db_session) == set()

    def test_placement_keys(self):
        assert screens.placement_key("recipes", "kitchen") == "screen:recipes@kitchen"
        assert screens.PLACEMENT_SEP == "@"

    def test_retiring_the_agent_empties_the_team_app(self, db_session):
        org = _world(db_session)
        db_session.add(
            OrgAppEntitlement(
                organization_id=org.id, app_slug="executive-chef-agent", enabled=False
            )
        )
        db_session.flush()
        assert _kitchen(db_session, org)["screens"] == []

    def test_no_team_rows_means_no_team_apps(self, db_session):
        org = _world(db_session, team_rows=False)
        assert screens.team_apps(db_session, db_session, org.id) == []
        assert screens.team_rows(db_session) == []
        assert entitlements.team_tools(db_session) == {}
        # a placement on a team app that isn't installed changes nothing else
        assert screens.team_only_pages(db_session) == {"prep"}
        assert screens.pages_hidden(org.id, db_session, db_session) == set()

    def test_no_organisation_shows_everything(self, db_session):
        _world(db_session)
        teams = screens.team_apps(db_session, db_session, None)
        assert _page_ids(teams[0]) == ["recipes", "prep"]

    def test_team_only_pages(self, db_session):
        _world(db_session)
        assert screens.team_only_pages(db_session) == {"prep"}

    def test_team_tools(self, db_session):
        _world(db_session)
        assert entitlements.team_tools(db_session) == {
            "kitchen": {"lh.get_recipes"},
            "front_of_house": set(),
            "managers": set(),
        }

    def test_the_other_readers_ignore_team_rows(self, db_session):
        """A team app owns nothing and is never 'on': tool_users, tool_owners,
        apps_on and apps_reachable don't know it."""
        org = _world(db_session)
        assert entitlements.tool_users(db_session)["lh.get_recipes"] == {"recipes"}
        assert entitlements.tool_owners(db_session)["lh.get_recipes"] == "recipes"
        assert "kitchen" not in entitlements.app_index(db_session)
        on = entitlements.apps_on(org.id, db_session, db_session)
        reach = entitlements.apps_reachable(org.id, db_session, db_session)
        assert {"recipes", "prep", "floor"} <= on
        assert not ({"kitchen", "front_of_house", "managers"} & (on | reach))


class TestTeamPayload:
    """GET /api/team and the admin App Map carry the team apps."""

    def test_team_payload_carries_the_team_apps(self, db_session):
        org = _world(db_session)
        payload = agent_catalog.team_payload(org.id, db_session, db_session)
        assert [t["slug"] for t in payload["team_apps"]] == [
            "kitchen",
            "front_of_house",
            "managers",
        ]
        kitchen = payload["team_apps"][0]
        assert kitchen["tools"] == [{"key": "lh.get_recipes", "label": "Get recipes"}]
        assert _page_ids(kitchen) == ["recipes", "prep"]
        assert payload["team_only_pages"] == ["prep"]
        assert payload["placements_off"] == []
        apps = {a["slug"]: a for a in payload["apps"]}
        recipes_screen = apps["recipes"]["screens"][0]
        assert (recipes_screen["teams"], recipes_screen["team_only"]) == (
            ["kitchen"],
            False,
        )
        assert "team" not in recipes_screen
        assert apps["prep"]["screens"][0]["team_only"] is True
        assert apps["floor"]["screens"][1]["teams"] == []

    def test_the_payload_names_the_placements_switched_off(self, db_session):
        org = _world(db_session)
        _place_checklists(db_session)
        db_session.add(
            OrgAppEntitlement(
                organization_id=org.id,
                app_slug=screens.placement_key("checklists", "kitchen"),
                enabled=False,
            )
        )
        db_session.add(  # the agent-menu switch is not a placement
            OrgAppEntitlement(
                organization_id=org.id, app_slug="screen:recipes", enabled=False
            )
        )
        db_session.flush()
        payload = agent_catalog.team_payload(org.id, db_session, db_session)
        assert payload["placements_off"] == ["checklists@kitchen"]
        assert "recipes" in payload["pages_hidden"]
        kitchen = payload["team_apps"][0]
        assert _page_ids(kitchen) == ["recipes", "prep"]

    def test_the_placement_route_switches_one_team_app(self, client, db_session):
        """POST /api/marketplace/screens/{page}/teams/{team}/{action}: owners
        switch one placement; the other team app and the agent's menu stay."""
        from app.db.models import Role

        org = _world(db_session)
        _place_checklists(db_session)
        owner = make_user(db_session)
        role = Role(
            id=str(uuid.uuid4()),
            organization_id=org.id,
            name="owner-ish",
            display_name="Owner-ish",
            permissions=["billing:manage"],
            is_system=False,
        )
        db_session.add(role)
        db_session.flush()
        _make_membership(db_session, owner, org).role_id = role.id
        db_session.commit()
        headers = auth_headers(owner)
        r = client.post(
            "/api/marketplace/screens/checklists/teams/kitchen/disable", headers=headers
        )
        assert r.status_code == 200, r.text
        assert r.json() == {
            "page_id": "checklists",
            "team": "kitchen",
            "enabled": False,
        }
        row = (
            db_session.query(OrgAppEntitlement)
            .filter(OrgAppEntitlement.app_slug == "screen:checklists@kitchen")
            .one()
        )
        assert row.enabled is False and row.organization_id == org.id
        assert _page_ids(_kitchen(db_session, org)) == ["recipes", "prep"]
        assert _page_ids(_team(db_session, org, "front_of_house")) == [
            "floor",
            "checklists",
        ]
        assert screens.pages_hidden(org.id, db_session, db_session) == set()
        r = client.post(
            "/api/marketplace/screens/checklists/teams/kitchen/enable", headers=headers
        )
        assert r.status_code == 200 and r.json()["enabled"] is True
        db_session.expire_all()
        assert _page_ids(_kitchen(db_session, org)) == ["recipes", "prep", "checklists"]
        # a page the catalog does not place in that team app
        r = client.post(
            "/api/marketplace/screens/prep/teams/front_of_house/disable",
            headers=headers,
        )
        assert r.status_code == 404
        assert "prep" in r.json()["detail"] and "front_of_house" in r.json()["detail"]
        assert (
            client.post(
                "/api/marketplace/screens/nope/teams/kitchen/disable", headers=headers
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/marketplace/screens/checklists/teams/kitchen/toggle",
                headers=headers,
            ).status_code
            == 404
        )
        # the same gate as the agent-menu switch: billing:manage
        member = _person(
            db_session, org, _role(db_session, org, "m", agents=None, apps=None)
        )
        db_session.commit()
        r = client.post(
            "/api/marketplace/screens/checklists/teams/kitchen/disable",
            headers=auth_headers(member),
        )
        assert r.status_code == 403

    def test_the_route_serves_it(self, client, db_session):
        org = _world(db_session)
        user = make_user(db_session)
        _make_membership(db_session, user, org)
        body = client.get("/api/team", headers=auth_headers(user)).json()
        assert [t["slug"] for t in body["team_apps"]] == [
            "kitchen",
            "front_of_house",
            "managers",
        ]
        assert body["team_only_pages"] == ["prep"]

    def test_without_team_rows_the_payloads_are_as_before(self, db_session):
        org = _world(db_session, team_rows=False)
        payload = agent_catalog.team_payload(org.id, db_session, db_session)
        assert payload["team_apps"] == []
        assert agent_catalog.app_map_payload(db_session)["team_apps"] == []

    def test_the_marketplace_neither_lists_nor_switches_a_team_app(
        self, client, db_session
    ):
        """A team app is not an App: GET /api/marketplace leaves the team
        rows out (the Apps hub would shelve them with a bare tier badge),
        and enable/disable refuses them — a role decides who sees one."""
        from app.db.models import Role

        org = _world(db_session)
        user = make_user(db_session)
        role = Role(
            id=str(uuid.uuid4()),
            organization_id=org.id,
            name="owner-ish",
            display_name="Owner-ish",
            permissions=["billing:manage"],
            is_system=False,
        )
        db_session.add(role)
        db_session.flush()
        _make_membership(db_session, user, org).role_id = role.id
        db_session.commit()
        slugs = {
            a["slug"]
            for a in client.get("/api/marketplace", headers=auth_headers(user)).json()[
                "apps"
            ]
        }
        assert "recipes" in slugs
        assert not slugs & {"kitchen", "front_of_house", "managers"}
        r = client.post("/api/marketplace/kitchen/disable", headers=auth_headers(user))
        assert r.status_code == 400
        assert "team app" in r.json()["detail"]
        assert (
            db_session.query(OrgAppEntitlement)
            .filter(OrgAppEntitlement.app_slug == "kitchen")
            .count()
            == 0
        )

    def test_app_map_lists_every_placement_switches_ignored(self, db_session):
        org = _world(db_session, loaded=False)
        db_session.add(
            OrgAppEntitlement(organization_id=org.id, app_slug="recipes", enabled=False)
        )
        db_session.flush()
        body = agent_catalog.app_map_payload(db_session, db_session, org.id)
        teams = {t["slug"]: t for t in body["team_apps"]}
        assert list(teams) == ["kitchen", "front_of_house", "managers"]
        assert teams["kitchen"]["screens"] == [
            {
                "page_id": "recipes",
                "label": "Recipes",
                "app_slug": "recipes",
                "app_name": "Recipes & Costing",
                "member": "executive_chef",
                "teams": ["kitchen"],
                "team_only": False,
            },
            {
                "page_id": "prep",
                "label": "Prep",
                "app_slug": "prep",
                "app_name": "Prep",
                "member": "executive_chef",
                "teams": ["kitchen"],
                "team_only": True,
            },
        ]
        assert teams["managers"]["screens"] == []
        tool = teams["kitchen"]["tools"][0]
        assert tool["key"] == "lh.get_recipes" and tool["label"] == "Get recipes"
        assert teams["kitchen"]["tagline"] == "The kitchen's day."
        apps = {a["slug"]: a for a in body["apps"]}
        assert apps["recipes"]["screens"][0]["teams"] == ["kitchen"]
        assert apps["prep"]["screens"][0]["team_only"] is True

    def test_app_map_lists_a_screen_under_each_team_app_it_is_in(self, db_session):
        _world(db_session)
        _place_checklists(db_session)
        body = agent_catalog.app_map_payload(db_session)
        teams = {t["slug"]: t for t in body["team_apps"]}
        kitchen = [s["page_id"] for s in teams["kitchen"]["screens"]]
        foh = [s["page_id"] for s in teams["front_of_house"]["screens"]]
        assert kitchen == ["recipes", "prep", "checklists"]
        assert foh == ["floor", "checklists"]
        assert teams["kitchen"]["screens"][2]["teams"] == ["kitchen", "front_of_house"]


class TestFindings:
    """agent_catalog.ownership_findings and the config validator."""

    def test_a_clean_catalog_reports_only_the_empty_team_app(self, db_session):
        _world(db_session)
        f = agent_catalog.ownership_findings(db_session)
        assert f["team_apps_without_screens"] == ["managers"]
        assert f["unknown_team"] == []
        assert f["team_tools_unowned"] == []

    def test_a_screen_on_a_team_app_the_catalog_lacks(self, db_session):
        _world(db_session)
        _row(
            db_session,
            "bar",
            "app",
            {
                "member": "executive_chef",
                "components": [
                    _screen("bar_board", "bar", "Bar", teams=["managers", "bar_team"])
                ],
            },
        )
        f = agent_catalog.ownership_findings(db_session)
        assert f["unknown_team"] == ["bar/bar → bar_team"]
        # the known entry of the same list still counts as a placement
        assert f["team_apps_without_screens"] == []

    def test_a_team_tool_no_app_owns(self, db_session):
        org = make_org(db_session, "Lott")
        _row(
            db_session,
            "executive-chef-agent",
            "agent",
            {"owns_agents": ["executive_chef"]},
        )
        _team_rows(db_session, kitchen_tools=("lh.get_recipes", "lh.nothing"))
        f = agent_catalog.ownership_findings(db_session)
        assert f["team_tools_unowned"] == ["lh.get_recipes", "lh.nothing"]
        assert f["team_apps_without_screens"] == [
            "kitchen",
            "front_of_house",
            "managers",
        ]
        # and no team app is on anyone's menu
        assert screens.team_apps(db_session, db_session, org.id)[0]["screens"] == []

    def test_the_validator_surfaces_the_two_errors_not_the_empty_team(self):
        from app.services.config_validator import check_app_ownership

        issues = check_app_ownership(
            {
                "team_apps_without_screens": ["managers"],
                "unknown_team": ["bar/bar → bar_team"],
                "team_tools_unowned": ["lh.nothing"],
            }
        )
        assert {i.where for i in issues} == {
            "app_map.screen.bar/bar → bar_team",
            "app_map.tool.lh.nothing",
        }
        assert all(i.severity == "error" for i in issues)


# ------------------------------------------------- stream 1: roles, Today, open_app ----


def _role(db, org, name, *, agents, apps):
    from app.db.models import Role

    role = Role(
        id=str(uuid.uuid4()),
        organization_id=org.id,
        name=name,
        display_name=name.replace("_", " ").title(),
        is_system=False,
        permissions=["tasks:read"],
        agents=agents,
        apps=apps,
    )
    db.add(role)
    db.flush()
    return role


def _person(db, org, role):
    user = make_user(db)
    _make_membership(db, user, org).role_id = role.id if role else None
    db.flush()
    return user


class TestRoleTools:
    """§3.4 from the team-app side (the chat-side rule, with the tool list,
    is tests/test_staff_exposure.py::TestTeamAppTools): a role that names a
    team app passes exactly that team app's tools, through
    prompt_builder._role_blocked_tools and the App door."""

    def test_a_kitchen_role_passes_the_team_apps_tools_only(self, db_session):
        from app.agents.prompt_builder import _role_blocked_tools

        org = _world(db_session)
        kitchen = _person(
            db_session, org, _role(db_session, org, "k", agents=[], apps=["kitchen"])
        )
        users = entitlements.tool_users(db_session)
        users["lh.get_costs"] = {"recipes"}  # another Recipes tool, not in the team row
        reach = entitlements.apps_reachable(org.id, db_session, db_session)
        blocked = _role_blocked_tools(
            db_session,
            kitchen.id,
            db_session,
            users,
            reach,
            entitlements.team_tools(db_session),
        )
        assert blocked == {"lh.get_costs"}
        # without the team rule (no team_tools) nothing passes
        assert _role_blocked_tools(
            db_session, kitchen.id, db_session, users, reach
        ) == {
            "lh.get_recipes",
            "lh.get_costs",
        }

    def test_the_team_rule_needs_a_reachable_app(self, db_session):
        from app.agents.prompt_builder import _role_blocked_tools

        org = _world(db_session)
        kitchen = _person(
            db_session, org, _role(db_session, org, "k", agents=[], apps=["kitchen"])
        )
        users = entitlements.tool_users(db_session)
        blocked = _role_blocked_tools(
            db_session,
            kitchen.id,
            db_session,
            users,
            set(),
            entitlements.team_tools(db_session),
        )
        assert blocked == {"lh.get_recipes"}

    def test_the_app_door_passes_a_team_apps_tool(self, db_session):
        from fastapi import HTTPException

        from app.services.app_runtime import _check_role_reach

        org = _world(db_session)
        kitchen = _person(
            db_session, org, _role(db_session, org, "k", agents=[], apps=["kitchen"])
        )
        floor = _person(
            db_session,
            org,
            _role(db_session, org, "f", agents=[], apps=["front_of_house"]),
        )
        _check_role_reach(db_session, db_session, kitchen, "lh", "get_recipes")
        with pytest.raises(HTTPException):
            _check_role_reach(db_session, db_session, floor, "lh", "get_recipes")

    def test_the_direct_routes_need_a_reachable_app_too(self, db_session):
        """§3.4 on the routes a page calls itself (screen_access.tool_reachable)
        and the App door: with Executive Chef not hired, no App giving
        get_recipes is reachable, so the Kitchen team app's listing opens
        nothing — as in the chat. A role naming the agent is unchanged."""
        from fastapi import HTTPException

        from app.services import screen_access
        from app.services.app_runtime import _check_role_reach

        org = _world(db_session)
        kitchen = _person(
            db_session, org, _role(db_session, org, "k", agents=[], apps=["kitchen"])
        )
        chef = _person(
            db_session,
            org,
            _role(db_session, org, "c", agents=["executive_chef"], apps=[]),
        )
        assert screen_access.tool_reachable(
            db_session, db_session, kitchen, "lh", "get_recipes"
        )
        db_session.add(
            OrgAppEntitlement(
                organization_id=org.id, app_slug="executive-chef-agent", enabled=False
            )
        )
        db_session.flush()
        assert not screen_access.tool_reachable(
            db_session, db_session, kitchen, "lh", "get_recipes"
        )
        with pytest.raises(HTTPException):
            _check_role_reach(db_session, db_session, kitchen, "lh", "get_recipes")
        # the App rule is not narrowed for a role that names the agent
        assert screen_access.tool_reachable(
            db_session, db_session, chef, "lh", "get_recipes"
        )


# ---- Today ----


def _item(title, *, due=None, **kw):
    return today.TodayItem(kind="t", title=title, due_at=due, **kw)


@pytest.fixture()
def today_client(db_session):
    """tests/test_staff_directory.py's app (the staff routes), on this test's
    session."""
    from fastapi.testclient import TestClient

    from app.db.engine import get_config_db, get_db

    def _override():
        yield db_session

    _staff_app.dependency_overrides[get_db] = _override
    _staff_app.dependency_overrides[get_config_db] = _override
    with TestClient(_staff_app, raise_server_exceptions=False) as c:
        yield c
    _staff_app.dependency_overrides.clear()


@pytest.fixture()
def providers(monkeypatch):
    """A clean registry for the test; the built-in modules stay out."""
    reg: dict = {}
    monkeypatch.setattr(today, "_PROVIDERS", reg)
    monkeypatch.setattr(today, "_loaded", True)
    return reg


class TestToday:
    """§3.5: services/today.py gathers every provider's items."""

    def test_a_provider_that_raises_is_skipped(self, db_session, providers, caplog):
        org = _world(db_session)
        user = make_user(db_session)

        @today.register("good")
        def _good(db, cdb, u, org_id, venue_ids):
            return [_item("Prep list")]

        @today.register("bad")
        def _bad(db, cdb, u, org_id, venue_ids):
            raise RuntimeError("boom")

        @today.register("wrong_shape")
        def _wrong(db, cdb, u, org_id, venue_ids):
            return {"title": "not a list"}

        with caplog.at_level("ERROR"):
            items = today.items_for(db_session, db_session, user, org.id, [])
        assert [i["title"] for i in items] == ["Prep list"]
        assert "provider bad failed" in caplog.text
        assert "provider wrong_shape returned" in caplog.text

    def test_items_sort_by_due_then_title_undated_last(self, db_session, providers):
        from datetime import date, datetime, timezone

        org = _world(db_session)
        user = make_user(db_session)
        noon = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)

        @today.register("one")
        def _one(db, cdb, u, org_id, venue_ids):
            return [
                _item("Zebra"),
                _item("Fridge check", due=noon),
                _item("Apple"),
                _item("Opening list", due=date(2026, 10, 24)),
            ]

        @today.register("two")
        def _two(db, cdb, u, org_id, venue_ids):
            return [_item("Cleaning", due=date(2026, 10, 23))]

        items = today.items_for(db_session, db_session, user, org.id, [])
        assert [i["title"] for i in items] == [
            "Cleaning",
            "Opening list",
            "Fridge check",
            "Apple",
            "Zebra",
        ]
        assert [i["due_at"] for i in items] == [
            "2026-10-23",
            "2026-10-24",
            "2026-10-24T12:00:00+00:00",
            None,
            None,
        ]

    def test_an_item_carries_its_venue_team_and_page(self, db_session, providers):
        org = _world(db_session)
        user = make_user(db_session)
        seen: dict = {}

        @today.register("prep")
        def _prep(db, cdb, u, org_id, venue_ids):
            seen["args"] = (u.id, org_id, list(venue_ids))
            return [
                _item(
                    "Prep list",
                    detail="3 of 12 done",
                    tone="warn",
                    venue_id="v1",
                    team="kitchen",
                    page_id="prep",
                    params={"list": "l1"},
                )
            ]

        items = today.items_for(db_session, db_session, user, org.id, ["v1", "v2"])
        assert seen["args"] == (user.id, org.id, ["v1", "v2"])
        assert items == [
            {
                "kind": "t",
                "title": "Prep list",
                "detail": "3 of 12 done",
                "due_at": None,
                "tone": "warn",
                "venue_id": "v1",
                "team": "kitchen",
                "page_id": "prep",
                "app_slug": None,
                "params": {"list": "l1"},
            }
        ]

    def test_the_training_provider_is_registered_by_default(self):
        assert "training" in today.providers()
        assert today.providers()["training"] is today_training.training

    def test_the_training_provider_maps_the_member_view_as_before(
        self, db_session, monkeypatch
    ):
        """The same items /api/me/today made before providers existed: an
        assignment in progress, and a plan section due (tone from its date,
        due_at the plain date string)."""
        from app.db.models import AppRecord

        w = _staff_world(db_session)
        db_session.add(
            AppRecord(
                namespace="hr_suite",
                organization_id=w.org.id,
                collection="people",
                data={"name": "Member", "norm_user_id": w.member.id},
            )
        )
        db_session.flush()
        monkeypatch.setattr("app.services.builtin_apps.is_on", lambda *a: True)
        monkeypatch.setattr(
            "app.services.app_runtime.run_logic",
            lambda *a, **k: {
                "success": True,
                "data": {
                    "assignments": [
                        {
                            "program": "Food safety",
                            "status": "in_progress",
                            "percent": 0,
                        },
                        {
                            "program": "Knife skills",
                            "status": "in_progress",
                            "percent": 40,
                            "instance": "La Zeppa",
                        },
                        {"program": "Done", "status": "completed", "percent": 100},
                    ],
                    "plans": [
                        {
                            "program": "Induction",
                            "sections": [
                                {"name": "Fire exits", "status": "completed"},
                                {
                                    "name": "Allergens",
                                    "status": "open",
                                    "due_date": "2020-01-02",
                                },
                                {
                                    "name": "Tills",
                                    "status": "open",
                                    "due_date": "2999-01-02",
                                },
                            ],
                        }
                    ],
                },
            },
        )
        items = today_training.training(db_session, db_session, w.member, w.org.id, [])
        assert [i.to_dict() for i in today.sort_items(items)] == [
            {
                "kind": "training",
                "title": "Allergens",
                "detail": "Induction",
                "due_at": "2020-01-02",
                "tone": "overdue",
                "venue_id": None,
                "team": None,
                "page_id": "training",
                "app_slug": "training",
                "params": None,
            },
            {
                "kind": "training",
                "title": "Tills",
                "detail": "Induction",
                "due_at": "2999-01-02",
                "tone": "due",
                "venue_id": None,
                "team": None,
                "page_id": "training",
                "app_slug": "training",
                "params": None,
            },
            {
                "kind": "training",
                "title": "Food safety",
                "detail": "Not started",
                "due_at": None,
                "tone": "info",
                "venue_id": None,
                "team": None,
                "page_id": "training",
                "app_slug": "training",
                "params": None,
            },
            {
                "kind": "training",
                "title": "Knife skills",
                "detail": "40% done · La Zeppa",
                "due_at": None,
                "tone": "info",
                "venue_id": None,
                "team": None,
                "page_id": "training",
                "app_slug": "training",
                "params": None,
            },
        ]

    def test_the_route_still_answers_training_end_to_end(
        self, db_session, today_client, monkeypatch
    ):
        """tests/test_staff_directory.py::TestToday's setup, through the
        provider registry: Training's own member view answers the route."""
        from app.db.models import AppRecord

        w = _staff_world(db_session)
        headers = auth_headers(w.member)
        monkeypatch.setattr("app.services.builtin_apps.is_on", lambda *a: True)
        assert today_client.get("/api/me/today", headers=headers).json() == {
            "items": []
        }
        person = AppRecord(
            namespace="hr_suite",
            organization_id=w.org.id,
            collection="people",
            data={
                "name": "Member",
                "role": "team_member",
                "is_active": True,
                "norm_user_id": w.member.id,
            },
        )
        program = AppRecord(
            namespace="hr_suite",
            organization_id=w.org.id,
            collection="programs",
            data={"name": "Food safety", "is_active": True},
        )
        db_session.add_all([person, program])
        db_session.flush()
        db_session.add(
            AppRecord(
                namespace="hr_suite",
                organization_id=w.org.id,
                collection="assignments",
                data={
                    "person_id": person.id,
                    "program_id": program.id,
                    "status": "in_progress",
                },
            )
        )
        db_session.flush()
        items = today_client.get("/api/me/today", headers=headers).json()["items"]
        assert [
            (i["kind"], i["title"], i["app_slug"], i["page_id"]) for i in items
        ] == [("training", "Food safety", "training", "training")]
        assert items[0]["detail"] == "Not started" and items[0]["tone"] == "info"

    def test_the_route_passes_the_persons_venues_and_never_fails(
        self, db_session, today_client, monkeypatch, providers
    ):
        from tests.conftest import _make_venue_access

        w = _staff_world(db_session)
        _make_venue_access(db_session, w.member, w.a)
        seen: dict = {}

        @today.register("probe")
        def _probe(db, cdb, u, org_id, venue_ids):
            seen["venues"] = list(venue_ids)
            return [_item("Probe", venue_id=venue_ids[0])]

        body = today_client.get("/api/me/today", headers=auth_headers(w.member)).json()
        assert seen["venues"] == [w.a.id]
        assert body["items"][0]["venue_id"] == w.a.id
        # the whole thing failing reads as nothing due
        monkeypatch.setattr(
            today, "items_for", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
        )
        r = today_client.get("/api/me/today", headers=auth_headers(w.member))
        assert r.status_code == 200 and r.json() == {"items": []}


# ---- open_app ----


class _NoClose:
    """The handler closes its config session; the test session must survive."""

    def __init__(self, session):
        self._s = session

    def __getattr__(self, name):
        return getattr(self._s, name)

    def close(self):
        pass


def _thread(db, user):
    from app.db.models import Thread

    th = Thread(id=str(uuid.uuid4()), user_id=user.id, domain="norm", status="active")
    db.add(th)
    db.flush()
    return th


def _open(db, thread, **params):
    from app.agents.internal_tools import _open_app

    with patch("app.db.engine._ConfigSessionLocal", return_value=_NoClose(db)):
        return _open_app(params, db, thread.id)


class TestOpenApp:
    """§3.6: open_app opens a team app's screen with ``{team, page}``."""

    def _chef(self, db, org, apps=("kitchen", "managers")):
        role = _role(db, org, "head_chef", agents=[], apps=list(apps))
        user = _person(db, org, role)
        return user, _thread(db, user)

    def test_by_slug_opens_the_first_screen(self, db_session):
        org = _world(db_session)
        _, thread = self._chef(db_session, org)
        out = _open(db_session, thread, app="kitchen")
        assert out["success"] is True, out
        assert out["data"] == {
            "team": "kitchen",
            "name": "Kitchen",
            "page": "recipes",
            "label": "Recipes",
            "component": "recipe_editor",
            "app_slug": "recipes",
            "app_name": "Recipes & Costing",
        }

    def test_by_name_any_case_and_a_page_label(self, db_session):
        org = _world(db_session)
        _, thread = self._chef(db_session, org)
        out = _open(db_session, thread, app="KITCHEN", page="PREP")
        assert out["success"] is True, out
        assert (out["data"]["team"], out["data"]["page"]) == ("kitchen", "prep")
        # a page id works the same
        out = _open(db_session, thread, app="Kitchen", page="prep")
        assert out["data"]["page"] == "prep"

    def test_an_unknown_page_lists_the_screens(self, db_session):
        org = _world(db_session)
        _, thread = self._chef(db_session, org)
        out = _open(db_session, thread, app="kitchen", page="Fridges")
        assert out["success"] is False and out["data"] == {}
        assert "no screen 'Fridges'" in out["error"]
        assert "Recipes (recipes)" in out["error"] and "Prep (prep)" in out["error"]

    def test_a_team_app_with_no_screens_is_a_plain_sentence(self, db_session):
        org = _world(db_session)
        _, thread = self._chef(db_session, org)
        out = _open(db_session, thread, app="Managers")
        assert out["success"] is True
        assert out["data"]["message"] == "Managers has no screens yet."
        assert out["data"]["_no_display"] is True  # no card is painted
        assert "page" not in out["data"]

    def test_a_screen_the_organisation_has_off_is_not_there(self, db_session):
        org = _world(db_session)
        _, thread = self._chef(db_session, org)
        db_session.add(
            OrgAppEntitlement(organization_id=org.id, app_slug="recipes", enabled=False)
        )
        db_session.flush()
        out = _open(db_session, thread, app="kitchen")
        assert out["data"]["page"] == "prep"

    def test_a_team_app_the_role_does_not_reach_is_unknown(self, db_session):
        org = _world(db_session)
        _, thread = self._chef(db_session, org, apps=("kitchen",))
        out = _open(db_session, thread, app="Front of house")
        assert out["success"] is False and out["data"] == {}
        assert "Kitchen (kitchen, a team app)" in out["error"]
        assert "(front_of_house" not in out["error"]  # not offered

    def test_an_owner_reaches_every_team_app(self, db_session):
        org = _world(db_session)
        owner = make_user(db_session)
        _make_membership(db_session, owner, org)  # no role: everything
        out = _open(db_session, _thread(db_session, owner), app="front of house")
        assert out["success"] is True and out["data"]["page"] == "floor"

    def test_without_team_rows_nothing_changes(self, db_session):
        org = _world(db_session, team_rows=False)
        _, thread = self._chef(db_session, org)
        out = _open(db_session, thread, app="kitchen")
        assert out["success"] is False and out["data"] == {}
        # A Kitchen role reaches no built-in app unless one of its screens is
        # placed in Kitchen (the door rule, screen_access.app_reachable), and
        # with no team rows there is nothing at all to offer.
        assert out["error"] == "There are no apps you can open here."

    def test_the_tool_row_offers_page(self):
        seed_dir = REPO / "apps/api/scripts"
        spec = importlib.util.spec_from_file_location(
            "sync_app_components", seed_dir / "sync_app_components.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        row = mod.OPEN_APP
        assert "page" in row["optional_fields"] and "page" in row["field_schema"]
        assert "Kitchen" in row["description"] and "team app" in row["description"]
