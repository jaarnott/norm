"""Org-scoped endpoints check the organisation being acted on.

PUT /venues/{id} had no permission check at all: any signed-in user — a Team
Member included — could rename any venue, or change its timezone and so move
its business-day boundary. Its siblings in the same router used
``require_permission``, which checks a scope in the caller's FIRST membership
and never in the organisation being acted on, so a manager of one
organisation could edit, delete or join another's, and grant themselves
access to its venues. Found Oct 2026.
"""

import uuid

import pytest

from app.auth.permissions import STANDARD_ROLES
from app.auth.security import create_access_token
from app.db.models import OrganizationMembership, Role, UserVenueAccess, Venue
from tests.conftest import (
    _make_organization,
    _make_user,
    _make_venue,
    _make_venue_access,
)


def _auth(user):
    return {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}


@pytest.fixture()
def roles(db_session):
    out = {}
    for name, defn in STANDARD_ROLES.items():
        role = Role(
            id=str(uuid.uuid4()),
            name=name,
            display_name=defn["display_name"],
            description=defn["description"],
            is_system=True,
            permissions=defn["permissions"],
            organization_id=None,
        )
        db_session.add(role)
        out[name] = role
    db_session.flush()
    return out


def _member(db, user, org, role):
    m = OrganizationMembership(
        id=str(uuid.uuid4()),
        user_id=user.id,
        organization_id=org.id,
        role=role.name,
        role_id=role.id,
    )
    db.add(m)
    db.flush()
    return m


@pytest.fixture()
def two_orgs(db_session, roles):
    """Org A with a manager and a team member; org B with its own manager,
    a staff member and a venue."""
    a, b = (
        _make_organization(db_session, name="A"),
        _make_organization(db_session, name="B"),
    )
    venue_a = _make_venue(db_session, name="A venue", organization_id=a.id)
    venue_b = _make_venue(db_session, name="B venue", organization_id=b.id)
    manager_a = _make_user(db_session, full_name="Manager A")
    member_a = _make_user(db_session, full_name="Team Member A")
    manager_b = _make_user(db_session, full_name="Manager B")
    staff_b = _make_user(db_session, full_name="Staff B")
    _member(db_session, manager_a, a, roles["manager"])
    _member(db_session, member_a, a, roles["team_member"])
    _member(db_session, manager_b, b, roles["manager"])
    _member(db_session, staff_b, b, roles["team_member"])
    _make_venue_access(db_session, staff_b, venue_b)
    return {
        "a": a,
        "b": b,
        "venue_a": venue_a,
        "venue_b": venue_b,
        "manager_a": manager_a,
        "member_a": member_a,
        "manager_b": manager_b,
        "staff_b": staff_b,
    }


class TestEditingAVenue:
    def test_a_team_member_cannot_edit_their_own_orgs_venue(
        self, client, db_session, two_orgs
    ):
        """The hole as reported: no permission check at all."""
        v = two_orgs["venue_a"]
        r = client.put(
            f"/api/venues/{v.id}",
            json={"name": "Renamed", "timezone": "Europe/London"},
            headers=_auth(two_orgs["member_a"]),
        )
        assert r.status_code == 403
        db_session.refresh(v)
        assert v.name == "A venue" and v.timezone != "Europe/London"

    def test_a_manager_cannot_edit_another_orgs_venue(
        self, client, db_session, two_orgs
    ):
        v = two_orgs["venue_b"]
        r = client.put(
            f"/api/venues/{v.id}",
            json={"name": "Hijacked"},
            headers=_auth(two_orgs["manager_a"]),
        )
        assert r.status_code == 403
        db_session.refresh(v)
        assert v.name == "B venue"

    def test_a_manager_edits_their_own_orgs_venue(self, client, two_orgs):
        r = client.put(
            f"/api/venues/{two_orgs['venue_a'].id}",
            json={"name": "A venue (new)", "timezone": "Pacific/Auckland"},
            headers=_auth(two_orgs["manager_a"]),
        )
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "A venue (new)"

    def test_the_permission_is_checked_in_the_venues_own_org(
        self, client, db_session, roles, two_orgs
    ):
        """A manager in org A who is only a Team Member in org B: before,
        ``require_permission`` read the FIRST membership and let them act as a
        manager in B."""
        _member(db_session, two_orgs["manager_a"], two_orgs["b"], roles["team_member"])
        r = client.put(
            f"/api/venues/{two_orgs['venue_b'].id}",
            json={"name": "Nope"},
            headers=_auth(two_orgs["manager_a"]),
        )
        assert r.status_code == 403

    def test_a_platform_admin_still_can(self, client, two_orgs, db_session):
        admin = _make_user(db_session, role="admin")
        r = client.put(
            f"/api/venues/{two_orgs['venue_b'].id}",
            json={"location": "Christchurch"},
            headers=_auth(admin),
        )
        assert r.status_code == 200


class TestTheOtherOrgScopedWrites:
    def test_cannot_delete_another_orgs_venue(self, client, db_session, two_orgs):
        v = two_orgs["venue_b"]
        r = client.delete(f"/api/venues/{v.id}", headers=_auth(two_orgs["manager_a"]))
        assert r.status_code == 403
        assert db_session.get(Venue, v.id) is not None

    def test_cannot_create_a_venue_in_another_org(self, client, two_orgs):
        r = client.post(
            f"/api/organizations/{two_orgs['b'].id}/venues",
            json={"name": "Planted"},
            headers=_auth(two_orgs["manager_a"]),
        )
        assert r.status_code == 403

    def test_cannot_join_another_org_as_owner(self, client, db_session, two_orgs):
        r = client.post(
            f"/api/organizations/{two_orgs['b'].id}/members",
            json={"user_id": two_orgs["manager_a"].id, "role": "owner"},
            headers=_auth(two_orgs["manager_a"]),
        )
        assert r.status_code == 403
        assert (
            db_session.query(OrganizationMembership)
            .filter_by(
                user_id=two_orgs["manager_a"].id, organization_id=two_orgs["b"].id
            )
            .first()
            is None
        )

    def test_cannot_remove_another_orgs_member(self, client, two_orgs):
        r = client.delete(
            f"/api/organizations/{two_orgs['b'].id}/members/{two_orgs['manager_b'].id}",
            headers=_auth(two_orgs["manager_a"]),
        )
        assert r.status_code == 403

    def test_cannot_rename_another_org(self, client, two_orgs):
        r = client.put(
            f"/api/organizations/{two_orgs['b'].id}",
            json={"name": "Taken over"},
            headers=_auth(two_orgs["manager_a"]),
        )
        assert r.status_code == 403


class TestVenueAccessGrants:
    def test_cannot_grant_yourself_another_orgs_venue(
        self, client, db_session, two_orgs
    ):
        me = two_orgs["manager_a"]
        r = client.put(
            f"/api/users/{me.id}/venues",
            json={"venue_ids": [two_orgs["venue_b"].id]},
            headers=_auth(me),
        )
        assert r.status_code == 403
        assert db_session.query(UserVenueAccess).filter_by(user_id=me.id).count() == 0

    def test_cannot_change_another_orgs_members_access(self, client, two_orgs):
        r = client.put(
            f"/api/users/{two_orgs['staff_b'].id}/venues",
            json={"venue_ids": []},
            headers=_auth(two_orgs["manager_a"]),
        )
        assert r.status_code == 403

    def test_own_org_grant_leaves_other_orgs_access_alone(
        self, client, db_session, roles, two_orgs
    ):
        """A member of both orgs: a manager in A setting their A venues must
        not wipe the access they hold at B (it used to delete every row)."""
        both = two_orgs["staff_b"]
        _member(db_session, both, two_orgs["a"], roles["team_member"])
        r = client.put(
            f"/api/users/{both.id}/venues",
            json={"venue_ids": [two_orgs["venue_a"].id]},
            headers=_auth(two_orgs["manager_a"]),
        )
        assert r.status_code == 200, r.text
        held = {
            a.venue_id
            for a in db_session.query(UserVenueAccess).filter_by(user_id=both.id)
        }
        assert held == {two_orgs["venue_a"].id, two_orgs["venue_b"].id}

    def test_an_unknown_venue_is_a_404_not_a_broken_row(self, client, two_orgs):
        r = client.put(
            f"/api/users/{two_orgs['member_a'].id}/venues",
            json={"venue_ids": [str(uuid.uuid4())]},
            headers=_auth(two_orgs["manager_a"]),
        )
        assert r.status_code == 404


class TestReadsAcrossOrgs:
    def test_cannot_read_another_orgs_venue_connectors(self, client, two_orgs):
        r = client.get(
            f"/api/venues/{two_orgs['venue_b'].id}/connectors",
            headers=_auth(two_orgs["manager_a"]),
        )
        assert r.status_code == 403

    def test_cannot_list_another_orgs_users_venues(self, client, two_orgs):
        r = client.get(
            f"/api/users/{two_orgs['staff_b'].id}/venues",
            headers=_auth(two_orgs["manager_a"]),
        )
        assert r.status_code == 403

    def test_own_org_member_and_self_still_work(self, client, two_orgs):
        manager = two_orgs["manager_a"]
        for target in (two_orgs["member_a"], manager):
            r = client.get(f"/api/users/{target.id}/venues", headers=_auth(manager))
            assert r.status_code == 200
