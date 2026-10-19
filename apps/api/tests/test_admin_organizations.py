"""The Organisations page for platform admins (Oct 2026).

Admins see every organisation with its venues and people, create and delete
organisations, and change who is in them — without ever being made a member
of a customer's organisation themselves.
"""

import pytest
from app.db.models import (
    Organization,
    OrganizationMembership,
    Role,
    Subscription,
    User,
    UserVenueAccess,
    Venue,
)
from tests.conftest import _make_membership
from tests.org_helpers import auth_headers, grant_venue, make_org, make_user, make_venue


@pytest.fixture(autouse=True)
def _system_roles(db_session):
    """The standard roles (owner, manager, team member, payroll admin) exist in
    every real database; a fresh test database has none until seeded."""
    from app.db.models import Role
    from tests.org_helpers import seed_system_roles

    if not (
        db_session.query(Role)
        .filter(Role.name == "owner", Role.organization_id.is_(None))
        .first()
    ):
        seed_system_roles(db_session)


def _role(db, name):
    return (
        db.query(Role).filter(Role.name == name, Role.organization_id.is_(None)).first()
    )


class TestOnlyPlatformAdmins:
    def test_everyone_else_is_refused(self, client, db_session, manager_headers):
        for method, path in (
            ("get", "/api/admin/organizations"),
            ("post", "/api/admin/organizations"),
            ("delete", "/api/admin/organizations/x"),
            ("put", "/api/admin/organizations/x/members/y"),
        ):
            kw = {"json": {"name": "X"}} if method in ("post", "put") else {}
            r = getattr(client, method)(path, headers=manager_headers, **kw)
            assert r.status_code == 403, (method, path)


class TestOverview:
    def test_lists_organisations_venues_people_and_unassigned_accounts(
        self, client, db_session, admin_headers
    ):
        org = make_org(db_session, "Acme")
        bar = make_venue(db_session, "Acme Bar", org)
        make_venue(db_session, "Acme Cafe", org)
        owner = make_user(db_session)
        _make_membership(db_session, owner, org)
        grant_venue(db_session, owner, bar)
        loner = make_user(db_session)
        db_session.commit()

        data = client.get("/api/admin/organizations", headers=admin_headers).json()
        acme = next(o for o in data["organizations"] if o["id"] == org.id)
        assert [v["name"] for v in acme["venues"]] == ["Acme Bar", "Acme Cafe"]
        assert [(m["email"], m["venue_ids"]) for m in acme["members"]] == [
            (owner.email, [bar.id])
        ]
        assert loner.email in [u["email"] for u in data["unassigned_users"]]
        assert owner.email not in [u["email"] for u in data["unassigned_users"]]
        assert {r["name"] for r in data["roles"]} >= {"owner", "team_member"}


class TestCreate:
    def test_the_admin_is_not_made_a_member(
        self, client, db_session, admin_user, admin_headers
    ):
        r = client.post(
            "/api/admin/organizations",
            json={"name": "New Customer Ltd", "timezone": "Australia/Melbourne"},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        org = db_session.query(Organization).filter_by(id=r.json()["id"]).one()
        assert org.currency == "AUD"
        assert (
            db_session.query(OrganizationMembership)
            .filter_by(organization_id=org.id)
            .count()
            == 0
        )

    def test_adding_a_venue_for_a_customer_gives_the_admin_no_access(
        self, client, db_session, admin_user, admin_headers
    ):
        org = make_org(db_session, "Customer")
        db_session.commit()
        r = client.post(
            f"/api/organizations/{org.id}/venues",
            json={"name": "Customer Bar"},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        assert (
            db_session.query(UserVenueAccess).filter_by(user_id=admin_user.id).count()
            == 0
        )


class TestDelete:
    def test_refused_while_it_has_venues(self, client, db_session, admin_headers):
        org = make_org(db_session, "Busy")
        make_venue(db_session, "Busy Bar", org)
        db_session.commit()
        r = client.delete(f"/api/admin/organizations/{org.id}", headers=admin_headers)
        assert r.status_code == 409
        assert "Busy Bar" in r.json()["detail"]

    def test_refused_with_billing_history(self, client, db_session, admin_headers):
        org = make_org(db_session, "Paying")
        db_session.add(
            Subscription(organization_id=org.id, token_plan="core", status="active")
        )
        db_session.commit()
        r = client.delete(f"/api/admin/organizations/{org.id}", headers=admin_headers)
        assert r.status_code == 409
        assert "billing" in r.json()["detail"].lower()

    def test_an_empty_organisation_goes_and_its_people_keep_their_accounts(
        self, client, db_session, admin_headers
    ):
        org = make_org(db_session, "Empty")
        person = make_user(db_session)
        _make_membership(db_session, person, org)
        db_session.commit()
        r = client.delete(f"/api/admin/organizations/{org.id}", headers=admin_headers)
        assert r.status_code == 200, r.text
        assert r.json() == {"deleted": "Empty", "members_removed": 1}
        assert db_session.query(Organization).filter_by(id=org.id).first() is None
        assert db_session.query(User).filter_by(id=person.id).first() is not None


class TestMemberAccess:
    def test_role_and_venues_change_in_one_organisation_only(
        self, client, db_session, admin_headers
    ):
        a, b = make_org(db_session, "A"), make_org(db_session, "B")
        a1, a2 = make_venue(db_session, "A1", a), make_venue(db_session, "A2", a)
        b1 = make_venue(db_session, "B1", b)
        person = make_user(db_session)
        _make_membership(db_session, person, a)
        _make_membership(db_session, person, b)
        grant_venue(db_session, person, a1)
        grant_venue(db_session, person, b1)
        db_session.commit()
        manager = _role(db_session, "manager")

        r = client.put(
            f"/api/admin/organizations/{a.id}/members/{person.id}",
            json={"role_id": manager.id, "venue_ids": [a2.id]},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        rows = {
            x.venue_id
            for x in db_session.query(UserVenueAccess).filter_by(user_id=person.id)
        }
        assert rows == {a2.id, b1.id}  # B untouched
        m = (
            db_session.query(OrganizationMembership)
            .filter_by(user_id=person.id, organization_id=a.id)
            .one()
        )
        assert m.role_id == manager.id

    def test_a_venue_from_another_organisation_is_refused(
        self, client, db_session, admin_headers
    ):
        a, b = make_org(db_session, "A"), make_org(db_session, "B")
        b1 = make_venue(db_session, "B1", b)
        person = make_user(db_session)
        _make_membership(db_session, person, a)
        db_session.commit()
        r = client.put(
            f"/api/admin/organizations/{a.id}/members/{person.id}",
            json={"venue_ids": [b1.id]},
            headers=admin_headers,
        )
        assert r.status_code == 422


class TestTeamPageVenueEdit:
    def test_naming_the_organisation_leaves_the_others_alone(self, client, db_session):
        a, b = make_org(db_session, "A"), make_org(db_session, "B")
        a1, b1 = make_venue(db_session, "A1", a), make_venue(db_session, "B1", b)
        boss = make_user(db_session)
        owner = _role(db_session, "owner")
        for org in (a, b):
            db_session.add(
                OrganizationMembership(
                    user_id=boss.id, organization_id=org.id, role_id=owner.id
                )
            )
        person = make_user(db_session)
        _make_membership(db_session, person, a)
        _make_membership(db_session, person, b)
        grant_venue(db_session, person, a1)
        grant_venue(db_session, person, b1)
        db_session.commit()

        r = client.put(
            f"/api/users/{person.id}/venues",
            json={"venue_ids": [], "organization_id": a.id},
            headers=auth_headers(boss),
        )
        assert r.status_code == 200, r.text
        rows = {
            x.venue_id
            for x in db_session.query(UserVenueAccess).filter_by(user_id=person.id)
        }
        assert rows == {b1.id}
        assert db_session.query(Venue).filter_by(id=a1.id).first() is not None
