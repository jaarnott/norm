"""The organisation endpoints Norm Functions needs: company settings
(currency, default timezone, tax wording and the Functions mode) and the
venue-delete guard.

Company settings are an owner's act (services/functions/company.py) and the
mode most of all — shadow and live open Functions to the whole organisation —
so who may change them, validation, all-or-nothing saves and the audit line
are pinned here, across organisations too. A venue holding Functions records
refuses deletion with a 409 that says why, instead of a foreign-key 500."""

import logging
import uuid

import pytest

from app.auth.permissions import STANDARD_ROLES
from app.auth.security import create_access_token
from app.db.functions_models import (
    FnEnquiry,
    FnMailboxAccount,
    FnMinSpend,
    FnVenueArea,
)
from app.db.models import (
    Organization,
    OrganizationMembership,
    Role,
    UserVenueAccess,
    Venue,
)
from tests.conftest import _make_user, _make_venue
from tests.functions_world import add_contact, add_enquiry, make_world


def _auth(user):
    return {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}


def _url(org):
    return f"/api/organizations/{org.id}/company-settings"


@pytest.fixture()
def w(db_session):
    return make_world(db_session, mode="off")


def _manager(db, org):
    """A manager by role — org:manage and all — but not an owner."""
    defn = STANDARD_ROLES["manager"]
    role = Role(
        id=str(uuid.uuid4()),
        name="manager",
        display_name=defn["display_name"],
        description=defn["description"],
        is_system=True,
        permissions=defn["permissions"],
        organization_id=None,
    )
    db.add(role)
    user = _make_user(db, full_name="Mia Manager")
    db.add(
        OrganizationMembership(
            user_id=user.id, organization_id=org.id, role="manager", role_id=role.id
        )
    )
    db.flush()
    return user


class TestReading:
    def test_a_member_reads_the_defaults(self, client, w):
        r = client.get(_url(w.org), headers=_auth(w.staff))
        assert r.status_code == 200
        body = r.json()
        assert body["currency"] == "NZD"
        assert body["default_timezone"] == "Pacific/Auckland"
        assert body["tax_label"] == "incl. GST"
        assert body["functions_mode"] == "off"
        assert body["can_edit"] is False
        assert "NZD" in body["currencies"] and "XAU" not in body["currencies"]

    def test_owners_and_platform_admins_may_edit(self, client, w, admin_headers):
        assert client.get(_url(w.org), headers=_auth(w.owner)).json()["can_edit"]
        assert client.get(_url(w.org), headers=admin_headers).json()["can_edit"]

    def test_a_manager_may_read_but_not_edit(self, client, db_session, w):
        manager = _manager(db_session, w.org)
        r = client.get(_url(w.org), headers=_auth(manager))
        assert r.status_code == 200 and r.json()["can_edit"] is False

    def test_another_orgs_owner_cannot_read(self, client, w):
        r = client.get(_url(w.org), headers=_auth(w.other_user))
        assert r.status_code == 403

    def test_unknown_org(self, client, w):
        r = client.get(
            f"/api/organizations/{uuid.uuid4()}/company-settings",
            headers=_auth(w.owner),
        )
        assert r.status_code == 404


class TestChanging:
    def test_an_owner_changes_currency_timezone_and_tax_wording(
        self, client, db_session, w
    ):
        r = client.put(
            _url(w.org),
            headers=_auth(w.owner),
            json={
                "currency": " aud ",
                "default_timezone": "Australia/Sydney",
                "tax_label": "incl. GST (10%)",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert (body["currency"], body["default_timezone"], body["tax_label"]) == (
            "AUD",
            "Australia/Sydney",
            "incl. GST (10%)",
        )
        assert body["can_edit"] is True
        db_session.expire_all()
        org = db_session.get(Organization, w.org.id)
        assert (org.currency, org.default_timezone) == ("AUD", "Australia/Sydney")

    @pytest.mark.parametrize(
        "body",
        [
            {"currency": "ABC"},  # three letters, but no such currency
            {"currency": "NZDD"},
            {"currency": "XAU"},  # gold is ISO 4217, but nobody trades in it
            {"default_timezone": "Mars/Olympus_Mons"},
            {"default_timezone": ""},
            {"tax_label": "x" * 41},
            {"tax_label": "   "},
            {"functions_mode": "on"},
        ],
    )
    def test_invalid_values_are_refused(self, client, w, body):
        r = client.put(_url(w.org), headers=_auth(w.owner), json=body)
        assert r.status_code == 422, (body, r.text)
        assert isinstance(r.json()["detail"], str)

    def test_all_or_nothing(self, client, db_session, w):
        r = client.put(
            _url(w.org),
            headers=_auth(w.owner),
            json={"currency": "AUD", "default_timezone": "Mars/Olympus_Mons"},
        )
        assert r.status_code == 422
        assert (
            client.get(_url(w.org), headers=_auth(w.owner)).json()["currency"] == "NZD"
        )
        db_session.expire_all()
        assert db_session.get(Organization, w.org.id).currency == "NZD"

    def test_staff_and_managers_are_refused(self, client, db_session, w):
        manager = _manager(db_session, w.org)
        for user in (w.staff, manager):
            r = client.put(_url(w.org), headers=_auth(user), json={"currency": "AUD"})
            assert r.status_code == 403, r.text
        assert db_session.get(Organization, w.org.id).currency == "NZD"

    def test_another_orgs_owner_is_refused(self, client, db_session, w):
        r = client.put(
            _url(w.org), headers=_auth(w.other_user), json={"functions_mode": "live"}
        )
        assert r.status_code == 403
        db_session.expire_all()
        assert db_session.get(Organization, w.org.id).functions_mode == "off"

    def test_a_platform_admin_may_change_any_org(
        self, client, db_session, w, admin_headers
    ):
        r = client.put(
            _url(w.org), headers=admin_headers, json={"tax_label": "excl. GST"}
        )
        assert r.status_code == 200
        assert r.json()["tax_label"] == "excl. GST"


class TestFunctionsMode:
    def test_an_owner_switches_it_and_the_change_is_logged(self, client, w, caplog):
        with caplog.at_level(logging.INFO, logger="app.routers.organizations"):
            r = client.put(
                _url(w.org), headers=_auth(w.owner), json={"functions_mode": "shadow"}
            )
        assert r.status_code == 200 and r.json()["functions_mode"] == "shadow"
        (record,) = [
            x for x in caplog.records if x.getMessage() == "functions_mode_changed"
        ]
        assert (record.org_id, record.previous_mode, record.mode, record.by) == (
            w.org.id,
            "off",
            "shadow",
            w.owner.id,
        )

    def test_saving_the_same_mode_logs_nothing(self, client, w, caplog):
        with caplog.at_level(logging.INFO, logger="app.routers.organizations"):
            r = client.put(
                _url(w.org), headers=_auth(w.owner), json={"functions_mode": "off"}
            )
        assert r.status_code == 200
        assert not [
            x for x in caplog.records if x.getMessage() == "functions_mode_changed"
        ]

    def test_staff_cannot_switch_it(self, client, db_session, w):
        r = client.put(
            _url(w.org), headers=_auth(w.staff), json={"functions_mode": "live"}
        )
        assert r.status_code == 403
        db_session.expire_all()
        assert db_session.get(Organization, w.org.id).functions_mode == "off"

    def test_going_live_needs_every_functions_venue_to_have_a_timezone(
        self, client, db_session, w
    ):
        nowhere = _make_venue(db_session, name="No Zone Bar", organization_id=w.org.id)
        add_enquiry(
            db_session, w.org.id, add_contact(db_session, w.org.id).id, nowhere.id
        )
        r = client.put(
            _url(w.org), headers=_auth(w.owner), json={"functions_mode": "live"}
        )
        assert r.status_code == 422
        assert "No Zone Bar" in r.json()["detail"]
        db_session.expire_all()
        assert db_session.get(Organization, w.org.id).functions_mode == "off"

    def test_switching_it_on_shows_the_functions_member(self, client, db_session, w):
        """The switch and the team payload agree (no catalog rows yet: the
        stand-in member appears once the mode is on)."""
        from app.db.config_models import MarketplaceApp

        db_session.add(
            MarketplaceApp(
                slug="procurement-agent",
                name="Procurement",
                description="",
                tier="agent",
                bundled=True,
                composition={"owns_agents": ["procurement"]},
            )
        )
        db_session.flush()

        def members():
            team = client.get("/api/team", headers=_auth(w.staff)).json()
            return {m["slug"] for m in team["members"]}

        assert "functions" not in members()
        client.put(
            _url(w.org), headers=_auth(w.owner), json={"functions_mode": "shadow"}
        )
        assert "functions" in members()


class TestVenueDeleteGuard:
    def _delete(self, client, venue, admin_headers):
        return client.delete(f"/api/venues/{venue.id}", headers=admin_headers)

    def test_a_venue_with_enquiries_is_refused_with_a_reason(
        self, client, db_session, w, admin_headers
    ):
        add_enquiry(
            db_session, w.org.id, add_contact(db_session, w.org.id).id, w.venue.id
        )
        r = self._delete(client, w.venue, admin_headers)
        assert r.status_code == 409
        assert (
            "enquiries (1)" in r.json()["detail"] and "Brew Bar" in r.json()["detail"]
        )
        db_session.expire_all()
        assert db_session.get(Venue, w.venue.id) is not None
        # refused before anything was touched
        assert (
            db_session.query(UserVenueAccess)
            .filter(UserVenueAccess.venue_id == w.venue.id)
            .count()
            > 0
        )

    def test_a_group_wide_enquirys_option_at_the_venue_counts(
        self, client, db_session, w, admin_headers
    ):
        """The enquiry has no venue, its minimum-spend option does — checking
        enquiries and events alone would still end in the FK error."""
        enquiry = add_enquiry(
            db_session, w.org.id, add_contact(db_session, w.org.id).id, None
        )
        db_session.add(
            FnMinSpend(
                organization_id=w.org.id,
                enquiry_id=enquiry.id,
                venue_id=w.venue.id,
                amount_cents=500_000,
            )
        )
        db_session.flush()
        r = self._delete(client, w.venue, admin_headers)
        assert r.status_code == 409 and "min spends (1)" in r.json()["detail"]

    def test_a_venue_mailbox_counts(self, client, db_session, w, admin_headers):
        db_session.add(
            FnMailboxAccount(
                organization_id=w.org.id,
                venue_id=w.venue2.id,
                google_email="events@garden.example",
            )
        )
        db_session.flush()
        r = self._delete(client, w.venue2, admin_headers)
        assert r.status_code == 409 and "mailbox accounts (1)" in r.json()["detail"]

    def test_functions_config_alone_goes_with_the_venue(
        self, client, db_session, w, admin_headers
    ):
        # Brew Bar has areas and approver settings, and an enquiry sits at the
        # OTHER venue — neither keeps it.
        add_enquiry(
            db_session, w.org.id, add_contact(db_session, w.org.id).id, w.venue2.id
        )
        r = self._delete(client, w.venue, admin_headers)
        assert r.status_code == 200, r.text
        db_session.expire_all()
        assert db_session.get(Venue, w.venue.id) is None
        assert (
            db_session.query(FnVenueArea)
            .filter(FnVenueArea.venue_id == w.venue.id)
            .count()
            == 0
        )
        assert (
            db_session.query(FnEnquiry)
            .filter(FnEnquiry.venue_id == w.venue2.id)
            .count()
            == 1
        )
