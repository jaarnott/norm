"""An App's screens follow the systems an organisation has (owner, 23 Oct 2026).

Hiring has the BambooHR hiring board and Hospo HR's own pages: an organisation
with both sees both, one with only Hospo HR sees Hospo HR's, and an owner can
switch single screens off to mix them (hiring from BambooHR, reviews from Hospo
HR). services/screens.py; docs/plans/agents-and-apps-restructure.md §4."""

import uuid

import pytest

from app.db.config_models import MarketplaceApp
from app.db.models import Connection
from app.services import agent_catalog, screens
from tests.org_helpers import auth_headers, make_org, make_user, make_venue
from tests.conftest import _make_membership


@pytest.fixture(autouse=True)
def _fresh_cache():
    agent_catalog.invalidate_cache()
    yield
    agent_catalog.invalidate_cache()


def _catalog(db):
    db.add_all(
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
                name="Hiring",
                description="",
                tier="app",
                bundled=True,
                composition={
                    "member": "hr",
                    "components": [
                        {
                            "key": "hiring_board",
                            "page": {"id": "hiring", "label": "Hiring (BambooHR)"},
                            "connection": "bamboohr",
                        }
                    ],
                    "app_slug": "hiring",
                    "app_connection": "hospo-hr",
                },
            ),
        ]
    )
    db.flush()


def _owner(db, org):
    from app.db.models import OrganizationMembership, Role

    user = make_user(db)
    role = Role(
        id=str(uuid.uuid4()),
        name=f"own-{uuid.uuid4().hex[:6]}",
        display_name="Owner",
        description="",
        is_system=False,
        permissions=["billing:manage", "settings:connectors"],
        organization_id=org.id,
    )
    db.add(role)
    db.flush()
    db.add(
        OrganizationMembership(
            id=str(uuid.uuid4()),
            user_id=user.id,
            organization_id=org.id,
            role="owner",
            role_id=role.id,
        )
    )
    db.flush()
    return user


class TestScreensFollowTheSystems:
    def test_hospo_hr_alone_shows_its_pages_and_not_bamboohrs(self, db_session):
        _catalog(db_session)
        org = make_org(db_session, "Lott")
        make_venue(db_session, "Cafe", org)
        hidden = screens.pages_hidden(org.id, db_session, db_session)
        assert hidden == {"hiring"}  # the BambooHR board; app:hiring shows

    def test_with_both_connected_both_show(self, db_session):
        _catalog(db_session)
        org = make_org(db_session, "Cook")
        venue = make_venue(db_session, "Bessie", org)
        db_session.add(Connection(connector_name="bamboohr", venue_id=venue.id, enabled="true", config={}))
        db_session.flush()
        assert screens.pages_hidden(org.id, db_session, db_session) == set()

    def test_an_owner_mixes_them_with_the_switches(self, client, db_session):
        _catalog(db_session)
        org = make_org(db_session, "Cook")
        venue = make_venue(db_session, "Bessie", org)
        db_session.add(Connection(connector_name="bamboohr", venue_id=venue.id, enabled="true", config={}))
        headers = auth_headers(_owner(db_session, org))
        db_session.commit()
        # hiring from BambooHR: Hospo HR's hiring pages off
        r = client.post("/api/marketplace/screens/app:hiring/disable", headers=headers)
        assert r.status_code == 200
        assert screens.pages_hidden(org.id, db_session, db_session) == {"app:hiring"}
        # Hospo HR off altogether: its pages stay hidden whatever their switch
        client.post("/api/marketplace/screens/app:hiring/enable", headers=headers)
        r = client.post("/api/connections/built-in/hospo-hr/disable", headers=headers)
        assert r.status_code == 200
        assert screens.pages_hidden(org.id, db_session, db_session) == {"app:hiring"}
        assert client.post("/api/marketplace/screens/nope/disable", headers=headers).status_code == 404
        assert (
            client.post("/api/connections/built-in/hospo-functions/disable", headers=headers).status_code
            == 404
        )

    def test_the_team_page_hears_it(self, client, db_session):
        _catalog(db_session)
        org = make_org(db_session, "Lott")
        user = make_user(db_session)
        _make_membership(db_session, user, org)
        db_session.commit()
        data = client.get("/api/team", headers=auth_headers(user)).json()
        assert data["pages_hidden"] == ["hiring"]
        hiring = next(a for a in data["apps"] if a["slug"] == "hiring")
        board = next(s for s in hiring["screens"] if s["page_id"] == "hiring")
        assert board["connection_name"] == "BambooHR" and not board["available"]


class TestTheChecks:
    def test_an_app_without_a_screen_and_a_shared_tool_nobody_owns(self, db_session):
        _catalog(db_session)
        db_session.add(
            MarketplaceApp(
                slug="ghost",
                name="Ghost",
                description="",
                tier="app",
                bundled=True,
                composition={"member": "hr", "uses_tools": ["lh.nothing"]},
            )
        )
        db_session.flush()
        f = agent_catalog.ownership_findings(db_session)
        assert f["apps_without_screen"] == ["ghost"]
        assert f["unowned_uses"] == ["lh.nothing"]
