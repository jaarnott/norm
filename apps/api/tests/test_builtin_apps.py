"""Apps built into Norm — Norm Hiring and Norm Training (28 Sep 2026).

Their code ships with Norm and serves EVERY organization, so the property
that matters most is that which org's data a request reaches can never come
from the request. These tests pin that, and the rest of the contract
(services/builtin_apps.py):

* the org is the viewer's own membership — two orgs never see each other's
  rows through the same built-in, and no membership means no app;
* the built-ins' storage namespace is reserved: a user-built app can neither
  save a claim on it nor reach it at run time, nor fetch one of its files by
  id through a same-named collection;
* the org's catalog App being off refuses the app at the door, not just in
  the menu;
* anyone in the org can open it and change its data (no permissions yet);
* audit rows name the built-in and the org — there is no app row;
* nobody can share it or re-save it, and its slug can't be taken.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.auth.security import create_access_token
from app.db.config_models import MarketplaceApp
from app.db.models import (
    App,
    AppCall,
    AppFile,
    AppRecord,
    AppVersion,
    OrgAppEntitlement,
)
from app.services import app_runtime as AR
from app.services.builtin_apps import (
    builtin_apps,
    get_builtin,
    load_for_user,
    reserved_namespaces,
)
from tests.conftest import _make_membership, _make_organization, _make_user


def _member(db, org):
    """Someone in ``org`` with NO role — no permissions at all."""
    user = _make_user(db, email=f"{uuid.uuid4().hex[:8]}@t.local")
    _make_membership(db, user, org)
    return user


def _person(db, org, name):
    row = AppRecord(
        id=str(uuid.uuid4()),
        namespace="hr_suite",
        organization_id=org.id,
        collection="people",
        data={"name": name},
    )
    db.add(row)
    db.flush()
    return row.id


def _people(db, user):
    app, version = load_for_user(db, db, "training", user)
    rows = AR.store_list(db, app=app, version=version, user=user, collection="people")
    return {r["name"] for r in (rows["records"] if isinstance(rows, dict) else rows)}


class TestTheRegistry:
    def test_hiring_and_training_ship_with_norm(self):
        assert set(builtin_apps()) == {"hiring", "training"}
        for b in builtin_apps().values():
            assert b.ui_source and b.logic_source and len(b.build) == 8
            # anyone in the org has access for now — no permission scopes
            assert b.spec.get("scopes") == []

    def test_their_namespace_is_reserved(self):
        assert reserved_namespaces() == {"hr_suite"}


class TestTenancy:
    def test_each_org_sees_only_its_own_rows(self, db_session):
        a, b = _make_organization(db_session), _make_organization(db_session)
        _person(db_session, a, "Aroha")
        _person(db_session, b, "Bex")
        assert _people(db_session, _member(db_session, a)) == {"Aroha"}
        assert _people(db_session, _member(db_session, b)) == {"Bex"}

    def test_another_orgs_record_is_not_found_by_id(self, db_session):
        a, b = _make_organization(db_session), _make_organization(db_session)
        theirs = _person(db_session, b, "Bex")
        user = _member(db_session, a)
        app, version = load_for_user(db_session, db_session, "training", user)
        with pytest.raises(HTTPException) as e:
            AR.store_get(
                db_session,
                app=app,
                version=version,
                user=user,
                collection="people",
                record_id=theirs,
            )
        assert e.value.status_code == 404

    def test_the_org_is_re_checked_not_trusted(self, db_session):
        # An app object bound to org B, handed to someone in org A, is refused.
        from app.services.builtin_apps import bind

        a, b = _make_organization(db_session), _make_organization(db_session)
        app, version = bind(get_builtin("training"), b.id)
        assert (
            AR.resolve_access(db_session, app, _member(db_session, a)).can_run is False
        )

    def test_no_membership_means_no_app(self, db_session):
        loner = _make_user(db_session, email=f"{uuid.uuid4().hex[:8]}@t.local")
        with pytest.raises(HTTPException) as e:
            load_for_user(db_session, db_session, "hiring", loner)
        assert e.value.status_code == 404


class TestTheSwitch:
    def test_an_org_with_the_app_off_is_refused_at_the_door(self, db_session):
        org = _make_organization(db_session)
        user = _member(db_session, org)
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
            ]
        )
        db_session.flush()
        assert load_for_user(db_session, db_session, "hiring", user) is not None
        db_session.add(
            OrgAppEntitlement(organization_id=org.id, app_slug="hiring", enabled=False)
        )
        db_session.flush()
        with pytest.raises(HTTPException):
            load_for_user(db_session, db_session, "hiring", user)


class TestAnyoneInTheOrg:
    def test_a_member_with_no_role_can_change_data(self, db_session):
        org = _make_organization(db_session)
        user = _member(db_session, org)
        app, version = load_for_user(db_session, db_session, "training", user)
        out = AR.store_put(
            db_session,
            app=app,
            version=version,
            user=user,
            collection="people",
            data={"name": "Sam"},
        )
        assert out
        assert _people(db_session, user) == {"Sam"}

    def test_audit_names_the_builtin_and_the_org(self, db_session):
        org = _make_organization(db_session)
        user = _member(db_session, org)
        _people(db_session, user)
        row = (
            db_session.query(AppCall)
            .filter(AppCall.user_id == user.id)
            .order_by(AppCall.created_at.desc())
            .first()
        )
        assert row.app_id is None
        assert row.builtin_slug == "training"
        assert row.organization_id == org.id


def _custom(db, org, user, spec, slug="nosy"):
    app = App(
        organization_id=org.id,
        created_by=user.id,
        slug=slug,
        name=slug,
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
    return app, ver


class TestUserBuiltAppsCannotReachIt:
    def test_saving_a_claim_on_the_namespace_is_refused(self, db_session):
        org = _make_organization(db_session)
        user = _member(db_session, org)
        with pytest.raises(HTTPException) as e:
            AR.save_app(
                db_session,
                user,
                {
                    "name": "Nosy",
                    "spec": {
                        "storage": {"namespace": "hr_suite", "collections": ["people"]}
                    },
                },
            )
        assert "reserved" in e.value.detail

    def test_a_legacy_claim_is_refused_at_run_time(self, db_session):
        # An app saved before the reservation existed still cannot read it.
        org = _make_organization(db_session)
        user = _member(db_session, org)
        _person(db_session, org, "Aroha")
        app, ver = _custom(
            db_session,
            org,
            user,
            {"storage": {"namespace": "hr_suite", "collections": ["people"]}},
        )
        with pytest.raises(HTTPException) as e:
            AR.store_list(
                db_session, app=app, version=ver, user=user, collection="people"
            )
        assert e.value.status_code == 403

    def test_a_file_is_not_reachable_through_a_same_named_collection(self, db_session):
        org = _make_organization(db_session)
        user = _member(db_session, org)
        cv = AppFile(
            id=str(uuid.uuid4()),
            namespace="hr_suite",
            organization_id=org.id,
            collection="people",
            filename="cv.pdf",
            content_type="application/pdf",
            size_bytes=3,
            data=b"pdf",
        )
        db_session.add(cv)
        db_session.flush()
        app, ver = _custom(
            db_session,
            org,
            user,
            {"storage": {"namespace": "my_suite", "collections": ["people"]}},
        )
        with pytest.raises(HTTPException) as e:
            AR.file_fetch(db_session, app=app, version=ver, user=user, file_id=cv.id)
        assert e.value.status_code == 404

    def test_the_slug_cannot_be_taken(self, db_session):
        org = _make_organization(db_session)
        user = _member(db_session, org)
        with pytest.raises(HTTPException) as e:
            AR.save_app(
                db_session, user, {"name": "Hiring", "slug": "hiring", "spec": {}}
            )
        assert "built into Norm" in e.value.detail


class TestOverHttp:
    def _headers(self, db, org):
        user = _member(db, org)
        return user, {
            "Authorization": f"Bearer {create_access_token({'sub': user.id})}"
        }

    def test_listed_and_served_from_norm(self, client, db_session):
        org = _make_organization(db_session)
        _, headers = self._headers(db_session, org)
        listed = client.get("/api/apps", headers=headers).json()["apps"]
        assert {a["slug"] for a in listed if a.get("builtin")} == {"hiring", "training"}
        detail = client.get("/api/apps/hiring", headers=headers).json()
        assert detail["builtin"] is True
        assert detail["ui_source"] == get_builtin("hiring").ui_source
        assert detail["missing_permissions"] == []

    def test_it_cannot_be_shared(self, client, db_session):
        org = _make_organization(db_session)
        _, headers = self._headers(db_session, org)
        r = client.post(
            "/api/apps/hiring/share",
            headers=headers,
            json={"principal_type": "organization", "principal_id": org.id},
        )
        assert r.status_code in (400, 403)

    def test_a_leftover_per_org_copy_is_ignored(self, client, db_session):
        # The per-org copy the old installer made is archived by migration;
        # even one that wasn't never shadows the built-in.
        org = _make_organization(db_session)
        user, headers = self._headers(db_session, org)
        _custom(db_session, org, user, {"storage": {}}, slug="hiring")
        listed = client.get("/api/apps", headers=headers).json()["apps"]
        assert [a for a in listed if a["slug"] == "hiring"] == [
            next(a for a in listed if a["slug"] == "hiring" and a.get("builtin"))
        ]
        assert client.get("/api/apps/hiring", headers=headers).json()["builtin"] is True


class TestVenues:
    def test_a_venue_from_another_org_is_refused(self, db_session):
        # Venue-access rows are org-blind in places; a built-in never tags
        # this org's rows with another org's venue (pre-push review).
        from app.db.models import UserVenueAccess
        from tests.conftest import _make_venue

        a, b = _make_organization(db_session), _make_organization(db_session)
        user = _member(db_session, a)
        theirs = _make_venue(db_session, organization_id=b.id)
        db_session.add(UserVenueAccess(user_id=user.id, venue_id=theirs.id))
        db_session.flush()
        app, version = load_for_user(db_session, db_session, "training", user)
        with pytest.raises(HTTPException) as e:
            AR.store_put(
                db_session,
                app=app,
                version=version,
                user=user,
                collection="people",
                data={"name": "Sam"},
                venue_id=theirs.id,
            )
        assert e.value.status_code == 403

    def test_publishing_a_builtin_is_refused(self, client, db_session):
        from app.db.models import OrganizationMembership, Role

        org = _make_organization(db_session)
        user = _member(db_session, org)
        owner = Role(
            id=str(uuid.uuid4()),
            organization_id=org.id,
            name=f"o-{uuid.uuid4().hex[:6]}",
            display_name="Owner",
            permissions=["billing:manage"],
        )
        db_session.add(owner)
        db_session.flush()
        db_session.query(OrganizationMembership).filter_by(user_id=user.id).update(
            {"role_id": owner.id}
        )
        db_session.flush()
        headers = {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}
        r = client.post(
            "/api/marketplace/submit", headers=headers, json={"app_slug": "hiring"}
        )
        assert r.status_code == 400 and "built into Norm" in r.json()["detail"]
