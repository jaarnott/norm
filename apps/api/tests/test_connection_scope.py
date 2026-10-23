"""Connections belong to an organisation (23 Oct 2026).

A connection with no venue was used by every organisation's calls, and
production's only such row is Cook Brothers' BambooHR — so another
organisation's Norm could read its employees and applicants, and anyone signed
in could fetch a BambooHR file by number. The connection routes that name only
a connector (toggle, delete, test) acted on whichever organisation's row came
first. Nobody was found to have used any of it. services/connection_scope.py
is the rule; these tests break each way in."""

import uuid

from app.connectors.tool_executor import _resolve_credentials
from app.db.config_models import ConnectionSpec
from app.db.models import Connection, OrganizationMembership, Role
from app.services import caller_scope, connection_scope
from tests.org_helpers import auth_headers, make_org, make_user, make_venue


def _row(db, connector, *, venue=None, org=None):
    row = Connection(
        connector_name=connector,
        venue_id=venue.id if venue else None,
        organization_id=org.id if org else None,
        config={"subdomain": "acme", "api_key": "k"},
        enabled="true",
    )
    db.add(row)
    db.flush()
    return row


def _manager(db, org, perms=("settings:connectors",)):
    user = make_user(db, role="user")
    role = Role(
        id=str(uuid.uuid4()),
        name=f"mgr-{uuid.uuid4().hex[:6]}",
        display_name="Manager",
        description="",
        is_system=False,
        permissions=list(perms),
        organization_id=org.id,
    )
    db.add(role)
    db.flush()
    db.add(
        OrganizationMembership(
            id=str(uuid.uuid4()),
            user_id=user.id,
            organization_id=org.id,
            role="member",
            role_id=role.id,
        )
    )
    db.flush()
    return user


class TestTheRule:
    def test_a_venues_row_takes_its_venues_organisation(self, db_session):
        org = make_org(db_session, "Lott")
        row = _row(db_session, "loadedhub", venue=make_venue(db_session, "Cafe", org))
        assert row.organization_id == org.id

    def test_another_organisations_org_wide_row_is_never_used(self, db_session):
        cook, lott = make_org(db_session, "Cook"), make_org(db_session, "Lott")
        bamboo = _row(db_session, "bamboohr", org=cook)
        cook_venue = make_venue(db_session, "Bessie", cook)
        lott_venue = make_venue(db_session, "Cafe", lott)
        # Lott has Loaded at its venue, so the loose fallback has a row of its
        # own organisation to find — and still never Cook's BambooHR.
        _row(db_session, "loadedhub", venue=lott_venue)
        for strict in (True, False):
            assert (
                _resolve_credentials(
                    "bamboohr", lott_venue.id, db_session, strict_venue=strict
                )
                is None
            )
        assert (
            _resolve_credentials(
                "bamboohr", cook_venue.id, db_session, strict_venue=True
            )
            is bamboo
        )
        # No venue: the caller scope decides.
        with caller_scope.use(caller_scope.Scope(lott.id)):
            assert _resolve_credentials("bamboohr", None, db_session) is None
        with caller_scope.use(caller_scope.Scope(cook.id)):
            assert _resolve_credentials("bamboohr", None, db_session) is bamboo

    def test_a_row_with_no_organisation_serves_nobody(self, db_session):
        org = make_org(db_session, "Lott")
        venue = make_venue(db_session, "Cafe", org)
        _row(db_session, "bamboohr")  # venue-less, organisation-less
        assert (
            connection_scope.venue_or_org_wide(db_session, "bamboohr", venue.id) is None
        )
        assert (
            connection_scope.in_org(
                db_session.query(Connection).filter(
                    Connection.connector_name == "bamboohr"
                ),
                org.id,
            ).count()
            == 0
        )


class TestTheRoutes:
    def _spec(self, db, name):
        db.add(
            ConnectionSpec(
                id=str(uuid.uuid4()),
                connector_name=name,
                display_name=name.title(),
                category="x",
                auth_type="basic",
                credential_fields=[
                    {"key": "subdomain"},
                    {"key": "api_key", "secret": True},
                ],
                enabled=True,
            )
        )
        db.flush()

    def test_name_only_routes_never_touch_an_organisations_connection(
        self, client, db_session
    ):
        cook, lott = make_org(db_session, "Cook"), make_org(db_session, "Lott")
        bamboo = _row(db_session, "bamboohr", org=cook)
        headers = auth_headers(_manager(db_session, lott))
        db_session.commit()
        assert (
            client.patch("/api/connectors/bamboohr/toggle", headers=headers).status_code
            == 404
        )
        assert (
            client.delete("/api/connectors/bamboohr", headers=headers).status_code
            == 404
        )
        db_session.refresh(bamboo)
        assert bamboo.enabled == "true"

    def test_the_list_and_a_save_stay_in_the_callers_organisation(
        self, client, db_session
    ):
        self._spec(db_session, "bamboohr")
        cook, lott = make_org(db_session, "Cook"), make_org(db_session, "Lott")
        bamboo = _row(db_session, "bamboohr", org=cook)
        lott_user = _manager(db_session, lott)
        headers = auth_headers(lott_user)
        db_session.commit()
        listed = client.get("/api/connectors", headers=headers).json()["connectors"]
        assert not next(c for c in listed if c["name"] == "bamboohr")["configured"]
        r = client.put(
            "/api/connectors/bamboohr",
            headers=headers,
            json={"config": {"subdomain": "lott", "api_key": "x"}},
        )
        assert r.status_code == 200
        db_session.refresh(bamboo)
        assert bamboo.config["subdomain"] == "acme"  # Cook's row untouched
        lott_row = (
            db_session.query(Connection)
            .filter(
                Connection.connector_name == "bamboohr",
                Connection.organization_id == lott.id,
            )
            .one()
        )
        assert lott_row.venue_id is None and lott_row.config["subdomain"] == "lott"

    def test_a_bamboohr_file_is_only_fetched_with_the_callers_own_connection(
        self, client, db_session
    ):
        cook, lott = make_org(db_session, "Cook"), make_org(db_session, "Lott")
        _row(db_session, "bamboohr", org=cook)
        headers = auth_headers(_manager(db_session, lott, perms=()))
        db_session.commit()
        r = client.get("/api/connectors/bamboohr/files/123", headers=headers)
        assert r.status_code == 400 and "not configured" in r.json()["detail"]
