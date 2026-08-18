"""The app platform's storage door.

`app_records` is what makes Norm the system of record for a migrated app
instead of a front-end over someone else's database. It is also a new way for
an app to reach data, so it gets the same four checks `call_action` applies —
audience, declared reach, permission intersection, write approval — and these
are the tests that hold it to them.

Every case here is a way one app could read or change rows it has no business
touching: another org's, another venue's, another namespace's, a collection it
never declared, or any of them without the write approval.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.db.models import App, AppRecord, AppShare, AppVersion, Role
from app.services import app_runtime as AR
from tests.conftest import (
    _make_membership,
    _make_organization,
    _make_user,
    _make_venue,
    _make_venue_access,
)

STORAGE_SPEC = {
    "actions": [],
    "writes": [],
    "scopes": ["mcp:hr:read"],
    "storage": {
        "namespace": "hr_suite",
        "collections": ["people", "programs"],
        "shared_with": ["training"],
    },
}


def _role(db, org, perms):
    role = Role(
        id=str(uuid.uuid4()),
        organization_id=org.id,
        name=f"r-{uuid.uuid4().hex[:6]}",
        display_name="r",
        permissions=perms,
    )
    db.add(role)
    db.flush()
    return role


def _member(db, org, perms):
    user = _make_user(db, email=f"{uuid.uuid4().hex[:8]}@t.local")
    mem = _make_membership(db, user, org)
    mem.role_id = _role(db, org, perms).id
    db.flush()
    return user


def _app_with(db, org, author, spec, slug="hiring", name="Hiring"):
    app = App(
        organization_id=org.id,
        created_by=author.id,
        slug=slug,
        name=name,
        visibility="private",
    )
    db.add(app)
    db.flush()
    v = AppVersion(app_id=app.id, version=1, spec=spec, created_by=author.id)
    db.add(v)
    db.flush()
    app.current_version_id = v.id
    db.flush()
    return app, v


@pytest.fixture()
def org(db_session):
    return _make_organization(db_session)


@pytest.fixture()
def author(db_session, org):
    return _member(db_session, org, ["hr:read", "apps:build", "apps:share"])


class TestDeclaredCollections:
    def test_an_undeclared_collection_is_refused_by_name(self, db_session, org, author):
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        with pytest.raises(HTTPException) as e:
            AR.store_list(
                db_session,
                app=app,
                version=v,
                user=author,
                collection="salaries",
            )
        assert e.value.status_code == 403
        assert "salaries" in str(e.value.detail)
        # names what WAS declared, so the author isn't guessing
        assert "people" in str(e.value.detail)

    def test_an_app_declaring_no_storage_cannot_store(self, db_session, org, author):
        app, v = _app_with(
            db_session, org, author, {"actions": [], "scopes": []}, slug="viewer"
        )
        with pytest.raises(HTTPException) as e:
            AR.store_list(
                db_session, app=app, version=v, user=author, collection="people"
            )
        assert "does not declare any storage" in str(e.value.detail)


class TestRoundTrip:
    def test_put_get_list_delete(self, db_session, org, author):
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        row = AR.store_put(
            db_session,
            app=app,
            version=v,
            user=author,
            collection="people",
            data={"name": "Sam", "loadedhub_id": "lh-1"},
        )
        assert row["name"] == "Sam" and row["id"]

        got = AR.store_get(
            db_session,
            app=app,
            version=v,
            user=author,
            collection="people",
            record_id=row["id"],
        )
        assert got["loadedhub_id"] == "lh-1"

        rows = AR.store_list(
            db_session, app=app, version=v, user=author, collection="people"
        )
        assert [r["name"] for r in rows] == ["Sam"]

        AR.store_put(
            db_session,
            app=app,
            version=v,
            user=author,
            collection="people",
            record_id=row["id"],
            data={"name": "Sam B", "loadedhub_id": "lh-1"},
        )
        assert (
            AR.store_get(
                db_session,
                app=app,
                version=v,
                user=author,
                collection="people",
                record_id=row["id"],
            )["name"]
            == "Sam B"
        )

        AR.store_delete(
            db_session,
            app=app,
            version=v,
            user=author,
            collection="people",
            record_id=row["id"],
        )
        assert (
            AR.store_list(
                db_session, app=app, version=v, user=author, collection="people"
            )
            == []
        )

    def test_where_filters_on_data_keys(self, db_session, org, author):
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        for status in ("active", "completed", "active"):
            AR.store_put(
                db_session,
                app=app,
                version=v,
                user=author,
                collection="programs",
                data={"status": status},
            )
        rows = AR.store_list(
            db_session,
            app=app,
            version=v,
            user=author,
            collection="programs",
            where={"status": "active"},
        )
        assert len(rows) == 2


class TestVenueScoping:
    def test_a_venue_query_also_returns_global_rows(self, db_session, org, author):
        """The bug this exists to prevent: Orbit's own API filters programs by
        `venue_id IN (...)`, so its group-wide programs (venue NULL) are
        invisible through it — and every assignment hanging off one vanishes
        too."""
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        venue = _make_venue(db_session, organization_id=org.id)
        _make_venue_access(db_session, author, venue)
        AR.store_put(
            db_session,
            app=app,
            version=v,
            user=author,
            collection="programs",
            data={"name": "Global induction"},
        )
        AR.store_put(
            db_session,
            app=app,
            version=v,
            user=author,
            collection="programs",
            data={"name": "Venue-only"},
            venue_id=venue.id,
        )
        names = {
            r["name"]
            for r in AR.store_list(
                db_session,
                app=app,
                version=v,
                user=author,
                collection="programs",
                venue_id=venue.id,
            )
        }
        assert names == {"Global induction", "Venue-only"}

        only = {
            r["name"]
            for r in AR.store_list(
                db_session,
                app=app,
                version=v,
                user=author,
                collection="programs",
                venue_id=venue.id,
                include_global=False,
            )
        }
        assert only == {"Venue-only"}

    def test_a_venue_the_viewer_cannot_reach_is_refused(self, db_session, org, author):
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        other = _make_venue(db_session, organization_id=org.id, name="Not mine")
        peer = _member(db_session, org, ["hr:read"])
        db_session.add(
            AppShare(
                app_id=app.id,
                principal_type="user",
                principal_id=peer.id,
                write_actions_approved=True,
            )
        )
        db_session.flush()
        with pytest.raises(HTTPException) as e:
            AR.store_list(
                db_session,
                app=app,
                version=v,
                user=peer,
                collection="people",
                venue_id=other.id,
            )
        assert "access to that venue" in str(e.value.detail)


class TestIsolation:
    def test_another_org_cannot_be_read(self, db_session, org, author):
        """The hard tenancy boundary: rows are filtered by the APP's
        organization, never by anything a request can influence."""
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        AR.store_put(
            db_session,
            app=app,
            version=v,
            user=author,
            collection="people",
            data={"name": "Ours"},
        )
        other_org = _make_organization(db_session, name="Other Co")
        other_author = _member(db_session, other_org, ["hr:read"])
        other_app, other_v = _app_with(
            db_session, other_org, other_author, STORAGE_SPEC, slug="hiring"
        )
        rows = AR.store_list(
            db_session,
            app=other_app,
            version=other_v,
            user=other_author,
            collection="people",
        )
        assert rows == []

    def test_a_foreign_record_id_is_not_reachable_by_guessing(
        self, db_session, org, author
    ):
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        mine = AR.store_put(
            db_session,
            app=app,
            version=v,
            user=author,
            collection="people",
            data={"name": "Ours"},
        )
        # Same org, same collection name, DIFFERENT namespace.
        other_spec = {
            **STORAGE_SPEC,
            "storage": {"namespace": "somewhere_else", "collections": ["people"]},
        }
        other_app, other_v = _app_with(
            db_session, org, author, other_spec, slug="other", name="Other"
        )
        with pytest.raises(HTTPException) as e:
            AR.store_get(
                db_session,
                app=other_app,
                version=other_v,
                user=author,
                collection="people",
                record_id=mine["id"],
            )
        assert e.value.status_code == 404


class TestWriteApproval:
    def test_a_shared_viewer_can_read_but_not_change(self, db_session, org, author):
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        AR.store_put(
            db_session,
            app=app,
            version=v,
            user=author,
            collection="people",
            data={"name": "Sam"},
        )
        peer = _member(db_session, org, ["hr:read"])
        db_session.add(
            AppShare(
                app_id=app.id,
                principal_type="user",
                principal_id=peer.id,
                write_actions_approved=False,
            )
        )
        db_session.flush()

        assert (
            len(
                AR.store_list(
                    db_session, app=app, version=v, user=peer, collection="people"
                )
            )
            == 1
        )
        with pytest.raises(HTTPException) as e:
            AR.store_put(
                db_session,
                app=app,
                version=v,
                user=peer,
                collection="people",
                data={"name": "Sneaky"},
            )
        assert "writes are not approved" in str(e.value.detail)

    def test_a_stranger_cannot_read_at_all(self, db_session, org, author):
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        stranger = _member(db_session, org, ["hr:read"])
        with pytest.raises(HTTPException) as e:
            AR.store_list(
                db_session, app=app, version=v, user=stranger, collection="people"
            )
        assert e.value.status_code == 404

    def test_the_intersection_rule_applies_to_storage_too(
        self, db_session, org, author
    ):
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        junior = _member(db_session, org, ["tasks:read"])  # no hr:read
        db_session.add(
            AppShare(app_id=app.id, principal_type="user", principal_id=junior.id)
        )
        db_session.flush()
        with pytest.raises(HTTPException) as e:
            AR.store_list(
                db_session, app=app, version=v, user=junior, collection="people"
            )
        assert "hr:read" in str(e.value.detail)


class TestNamespaceClaim:
    """Shared storage is by invitation. Without this rule, any app could name
    another's namespace and read its rows."""

    def test_an_uninvited_app_cannot_join_a_namespace(self, db_session, org, author):
        _app_with(db_session, org, author, STORAGE_SPEC)  # owner: hiring
        with pytest.raises(HTTPException) as e:
            AR.save_app(
                db_session,
                author,
                {
                    "name": "Nosy",
                    "slug": "nosy",
                    "spec": {
                        "actions": [],
                        "scopes": [],
                        "storage": {
                            "namespace": "hr_suite",
                            "collections": ["people"],
                        },
                    },
                    "ui_source": "<div/>",
                },
            )
        assert e.value.status_code == 403
        assert "belongs to" in str(e.value.detail)
        assert "shared_with" in str(e.value.detail)

    def test_an_invited_app_may_join(self, db_session, org, author):
        _app_with(db_session, org, author, STORAGE_SPEC)  # shared_with: ["training"]
        out = AR.save_app(
            db_session,
            author,
            {
                "name": "Training",
                "slug": "training",
                "spec": {
                    "actions": [],
                    "scopes": [],
                    "storage": {"namespace": "hr_suite", "collections": ["programs"]},
                },
                "ui_source": "<div/>",
            },
        )
        assert out["slug"] == "training"

    def test_the_two_apps_then_see_the_same_rows(self, db_session, org, author):
        hiring, hv = _app_with(db_session, org, author, STORAGE_SPEC)
        AR.save_app(
            db_session,
            author,
            {
                "name": "Training",
                "slug": "training",
                "spec": {
                    "actions": [],
                    "scopes": [],
                    "storage": {"namespace": "hr_suite", "collections": ["people"]},
                },
                "ui_source": "<div/>",
            },
        )
        training = (
            db_session.query(App)
            .filter(App.organization_id == org.id, App.slug == "training")
            .first()
        )
        tv = (
            db_session.query(AppVersion)
            .filter(AppVersion.id == training.current_version_id)
            .first()
        )
        AR.store_put(
            db_session,
            app=hiring,
            version=hv,
            user=author,
            collection="people",
            data={"name": "Hired by Hiring"},
        )
        rows = AR.store_list(
            db_session, app=training, version=tv, user=author, collection="people"
        )
        assert [r["name"] for r in rows] == ["Hired by Hiring"]

    def test_collections_must_be_declared_as_a_list(self, db_session, org, author):
        with pytest.raises(HTTPException) as e:
            AR.save_app(
                db_session,
                author,
                {
                    "name": "Bad",
                    "slug": "bad",
                    "spec": {"storage": {"namespace": "x", "collections": "people"}},
                    "ui_source": "<div/>",
                },
            )
        assert "non-empty list" in str(e.value.detail)


class TestAudit:
    def test_every_operation_is_audited(self, db_session, org, author):
        from app.db.models import AppCall

        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        row = AR.store_put(
            db_session,
            app=app,
            version=v,
            user=author,
            collection="people",
            data={"name": "Sam"},
        )
        AR.store_list(db_session, app=app, version=v, user=author, collection="people")
        AR.store_delete(
            db_session,
            app=app,
            version=v,
            user=author,
            collection="people",
            record_id=row["id"],
        )
        calls = (
            db_session.query(AppCall)
            .filter(AppCall.app_id == app.id, AppCall.connector == "app_storage")
            .all()
        )
        assert {c.action for c in calls} == {
            "put:people",
            "list:people",
            "delete:people",
        }
        # reads and writes are distinguishable in the trail
        methods = {c.action: c.method for c in calls}
        assert methods["list:people"] == "GET"
        assert methods["put:people"] == "POST"


class TestRecordShape:
    def test_records_carry_their_own_metadata(self, db_session, org, author):
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        row = AR.store_put(
            db_session,
            app=app,
            version=v,
            user=author,
            collection="people",
            data={"name": "Sam"},
        )
        assert set(row) >= {"id", "venue_id", "created_at", "updated_at", "name"}

    def test_data_must_be_an_object(self, db_session, org, author):
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        with pytest.raises(HTTPException) as e:
            AR.store_put(
                db_session,
                app=app,
                version=v,
                user=author,
                collection="people",
                data=["not", "an", "object"],
            )
        assert "must be an object" in str(e.value.detail)


def test_the_table_is_indexed_for_the_queries_it_serves(db_session):
    """Both access shapes — by org and by venue — must be indexed; a JSONB
    scan over every app's rows would be the first thing to hurt."""
    names = {ix.name for ix in AppRecord.__table__.indexes}
    assert "ix_app_records_org_collection" in names
    assert "ix_app_records_venue_collection" in names


class TestSandboxStorage:
    """App logic gets the same door, pre-bound — it never gets to say which
    app it is, so it cannot widen its own reach."""

    def test_logic_can_read_and_write_its_own_collections(
        self, db_session, org, author
    ):
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        code = (
            "def run(params, call_api, log):\n"
            "    store.put('people', {'name': 'From logic'})\n"
            "    return {'people': store.list('people')}\n"
        )
        v.logic_source = code
        db_session.flush()
        out = AR.run_logic(
            db_session, None, app=app, version=v, user=author, venue_id=None
        )
        assert out["success"] is True
        assert [p["name"] for p in out["data"]["people"]] == ["From logic"]

    def test_logic_cannot_touch_an_undeclared_collection(self, db_session, org, author):
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        v.logic_source = (
            "def run(params, call_api, log):\n    return store.list('salaries')\n"
        )
        db_session.flush()
        out = AR.run_logic(
            db_session, None, app=app, version=v, user=author, venue_id=None
        )
        assert out["success"] is False
        assert "salaries" in str(out["error"])

    def test_an_app_with_no_storage_has_no_store(self, db_session, org, author):
        app, v = _app_with(
            db_session, org, author, {"actions": [], "scopes": []}, slug="viewer"
        )
        v.logic_source = (
            "def run(params, call_api, log):\n    return store.list('people')\n"
        )
        db_session.flush()
        out = AR.run_logic(
            db_session, None, app=app, version=v, user=author, venue_id=None
        )
        # Not bound at all — the name does not exist in the sandbox.
        assert out["success"] is False
        assert "store" in str(out["error"])


class TestStorageEndpoints:
    """The HTTP wrappers. Thin, but they are what the iframe actually calls,
    so the refusals have to survive the round trip."""

    def _headers(self, user):
        from app.auth.security import create_access_token

        return {
            "Authorization": f"Bearer {create_access_token({'sub': user.id, 'email': user.email})}"
        }

    def test_round_trip_over_http(self, client, db_session, org, author):
        app, _ = _app_with(db_session, org, author, STORAGE_SPEC, slug="hr-hiring")
        db_session.commit()
        h = self._headers(author)

        created = client.post(
            "/api/apps/hr-hiring/records/people",
            json={"data": {"name": "Sam"}},
            headers=h,
        )
        assert created.status_code == 200, created.text
        rid = created.json()["id"]

        listed = client.post(
            "/api/apps/hr-hiring/records/people/query", json={}, headers=h
        )
        assert [r["name"] for r in listed.json()["records"]] == ["Sam"]

        got = client.get(f"/api/apps/hr-hiring/records/people/{rid}", headers=h)
        assert got.json()["name"] == "Sam"

        updated = client.put(
            f"/api/apps/hr-hiring/records/people/{rid}",
            json={"data": {"name": "Sam B"}},
            headers=h,
        )
        assert updated.json()["name"] == "Sam B"

        gone = client.delete(f"/api/apps/hr-hiring/records/people/{rid}", headers=h)
        assert gone.status_code == 200
        assert (
            client.post(
                "/api/apps/hr-hiring/records/people/query", json={}, headers=h
            ).json()["records"]
            == []
        )

    def test_an_undeclared_collection_is_refused_over_http(
        self, client, db_session, org, author
    ):
        _app_with(db_session, org, author, STORAGE_SPEC, slug="hr-hiring2")
        db_session.commit()
        r = client.post(
            "/api/apps/hr-hiring2/records/salaries/query",
            json={},
            headers=self._headers(author),
        )
        assert r.status_code == 403
        assert "salaries" in r.json()["detail"]

    def test_a_stranger_gets_nothing_over_http(self, client, db_session, org, author):
        _app_with(db_session, org, author, STORAGE_SPEC, slug="hr-hiring3")
        stranger = _member(db_session, org, ["hr:read"])
        db_session.commit()
        r = client.post(
            "/api/apps/hr-hiring3/records/people/query",
            json={},
            headers=self._headers(stranger),
        )
        assert r.status_code == 404


class TestVenueReachIsNotOptIn:
    """Found by adversarial review, 17 Aug 2026, and confirmed against the real
    code before the fix: `_check_venue` only fires on a venue the CALLER names,
    so omitting `venue_id` skipped the venue gate entirely and returned the
    whole org's rows — and the by-id paths never named a venue at all, so a
    targeted read, overwrite or delete of another venue's row was never
    checked. A single-venue manager could read and destroy every venue's
    records in the namespace."""

    def _two_venues(self, db, org, author, peer):
        mine = _make_venue(db, organization_id=org.id, name="Mine")
        theirs = _make_venue(db, organization_id=org.id, name="Theirs")
        _make_venue_access(db, peer, mine)
        _make_venue_access(db, author, mine)
        _make_venue_access(db, author, theirs)
        return mine, theirs

    def _shared_app(self, db, org, author, peer):
        app, v = _app_with(db, org, author, STORAGE_SPEC)
        db.add(
            AppShare(
                app_id=app.id,
                principal_type="user",
                principal_id=peer.id,
                write_actions_approved=True,
            )
        )
        db.flush()
        return app, v

    def test_omitting_the_venue_does_not_return_other_venues(
        self, db_session, org, author
    ):
        peer = _member(db_session, org, ["hr:read"])
        mine, theirs = self._two_venues(db_session, org, author, peer)
        app, v = self._shared_app(db_session, org, author, peer)
        for venue, name in ((mine, "mine"), (theirs, "theirs"), (None, "global")):
            AR.store_put(
                db_session,
                app=app,
                version=v,
                user=author,
                collection="people",
                data={"name": name},
                venue_id=venue.id if venue else None,
            )
        names = {
            r["name"]
            for r in AR.store_list(
                db_session, app=app, version=v, user=peer, collection="people"
            )
        }
        # own venue + org-wide rows, and nothing from a venue they cannot reach
        assert names == {"mine", "global"}

    def test_a_record_in_another_venue_cannot_be_read_by_id(
        self, db_session, org, author
    ):
        peer = _member(db_session, org, ["hr:read"])
        _, theirs = self._two_venues(db_session, org, author, peer)
        app, v = self._shared_app(db_session, org, author, peer)
        row = AR.store_put(
            db_session,
            app=app,
            version=v,
            user=author,
            collection="people",
            data={"name": "secret"},
            venue_id=theirs.id,
        )
        for call in (
            lambda: AR.store_get(
                db_session,
                app=app,
                version=v,
                user=peer,
                collection="people",
                record_id=row["id"],
            ),
            lambda: AR.store_put(
                db_session,
                app=app,
                version=v,
                user=peer,
                collection="people",
                record_id=row["id"],
                data={"name": "overwritten"},
            ),
            lambda: AR.store_delete(
                db_session,
                app=app,
                version=v,
                user=peer,
                collection="people",
                record_id=row["id"],
            ),
        ):
            with pytest.raises(HTTPException) as e:
                call()
            assert "access to that venue" in str(e.value.detail)

    def test_a_platform_admin_still_sees_everything(self, db_session, org, author):
        admin = _make_user(db_session, email="admin@t.local", role="admin")
        _make_membership(db_session, admin, org)
        venue = _make_venue(db_session, organization_id=org.id, name="Anywhere")
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        # Being a platform admin bypasses org PERMISSIONS, never the audience
        # check — an admin still has to be given the app.
        db_session.add(
            AppShare(
                app_id=app.id,
                principal_type="user",
                principal_id=admin.id,
                write_actions_approved=True,
            )
        )
        db_session.flush()
        # created by the admin: the author holds no access to this venue, and
        # is correctly refused if they try.
        AR.store_put(
            db_session,
            app=app,
            version=v,
            user=admin,
            collection="people",
            data={"name": "anywhere"},
            venue_id=venue.id,
        )
        with pytest.raises(HTTPException):
            AR.store_list(
                db_session,
                app=app,
                version=v,
                user=author,
                collection="people",
                venue_id=venue.id,
            )
        # admin owns no venue access rows at all
        rows = AR.store_list(
            db_session, app=app, version=v, user=admin, collection="people"
        )
        assert [r["name"] for r in rows] == ["anywhere"]

    def test_a_path_shaped_collection_is_refused(self, db_session, org, author):
        """The other half of the traversal fix: the client encodes the segment,
        and the server refuses anything that is not a plain name."""
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        with pytest.raises(HTTPException) as e:
            AR.store_list(
                db_session,
                app=app,
                version=v,
                user=author,
                collection="../../hiring/records/people",
            )
        assert e.value.status_code == 400

    def test_updating_a_global_row_from_logic_does_not_capture_it(
        self, db_session, org, author
    ):
        """`store.put` inside the sandbox defaults to the app's current venue.
        Applied to an UPDATE that would silently pull a group-wide row into one
        venue — the promotion hazard the venue-delete path guards against, in
        reverse."""
        venue = _make_venue(db_session, organization_id=org.id, name="Current")
        _make_venue_access(db_session, author, venue)
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        row = AR.store_put(
            db_session,
            app=app,
            version=v,
            user=author,
            collection="programs",
            data={"name": "Global induction"},
        )
        v.logic_source = (
            "def run(params, call_api, log):\n"
            "    return store.put('programs', {'name': 'Renamed'}, params['rid'])\n"
        )
        db_session.flush()
        out = AR.run_logic(
            db_session,
            None,
            app=app,
            version=v,
            user=author,
            venue_id=venue.id,
            params={"rid": row["id"]},
        )
        assert out["success"] is True
        assert out["data"]["venue_id"] is None  # still group-wide


class TestNamespaceOwnershipSurvivesRevision:
    """Found by re-installing the owner, 17 Aug 2026: the claim check only
    looked at OTHER apps, so once a second app joined a namespace, re-saving
    the app that CREATED it made the joiner look like the owner — and the owner
    was refused entry to its own namespace."""

    def test_the_owner_can_save_again_after_a_joiner_exists(
        self, db_session, org, author
    ):
        owner, _ = _app_with(db_session, org, author, STORAGE_SPEC)  # hiring, owns it
        AR.save_app(
            db_session,
            author,
            {
                "name": "Training",
                "slug": "training",
                "spec": {
                    "actions": [],
                    "scopes": [],
                    "storage": {"namespace": "hr_suite", "collections": ["programs"]},
                },
                "ui_source": "<div/>",
            },
        )
        # The owner revises itself. This used to raise 403.
        out = AR.save_app(
            db_session,
            author,
            {
                "name": "Hiring",
                "slug": owner.slug,
                "spec": STORAGE_SPEC,
                "ui_source": "<div>v2</div>",
            },
        )
        assert out["version"] == 2

    def test_a_joiner_can_still_save_again(self, db_session, org, author):
        _app_with(db_session, org, author, STORAGE_SPEC)
        joiner_spec = {
            "actions": [],
            "scopes": [],
            "storage": {"namespace": "hr_suite", "collections": ["programs"]},
        }
        AR.save_app(
            db_session,
            author,
            {
                "name": "Training",
                "slug": "training",
                "spec": joiner_spec,
                "ui_source": "<div/>",
            },
        )
        out = AR.save_app(
            db_session,
            author,
            {
                "name": "Training",
                "slug": "training",
                "spec": joiner_spec,
                "ui_source": "<div>v2</div>",
            },
        )
        assert out["version"] == 2

    def test_an_uninvited_app_is_still_refused_once_a_suite_exists(
        self, db_session, org, author
    ):
        _app_with(db_session, org, author, STORAGE_SPEC)
        AR.save_app(
            db_session,
            author,
            {
                "name": "Training",
                "slug": "training",
                "spec": {
                    "actions": [],
                    "scopes": [],
                    "storage": {"namespace": "hr_suite", "collections": ["programs"]},
                },
                "ui_source": "<div/>",
            },
        )
        with pytest.raises(HTTPException) as e:
            AR.save_app(
                db_session,
                author,
                {
                    "name": "Nosy",
                    "slug": "nosy",
                    "spec": {
                        "actions": [],
                        "scopes": [],
                        "storage": {"namespace": "hr_suite", "collections": ["people"]},
                    },
                    "ui_source": "<div/>",
                },
            )
        assert "shared_with" in str(e.value.detail)


class TestNoSilentTruncation:
    """Found by migrating real data, 17 Aug 2026.

    The door bounds one query at 1,000 rows, which is right. What was wrong was
    the silence: logic asking for `limit=5000` got 1,000 rows and no signal, so
    a tracker over 6,784 completions computed itself from 1,000 of them and
    reported 193 trained people as still in progress. It looked entirely
    plausible — which is what made it dangerous.
    """

    def test_logic_reading_a_collection_gets_all_of_it(self, db_session, org, author):
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        for i in range(1250):  # comfortably past the 1,000-row query bound
            db_session.add(
                AppRecord(
                    id=str(uuid.uuid4()),
                    namespace="hr_suite",
                    organization_id=org.id,
                    collection="people",
                    data={"name": f"P{i:04d}"},
                    created_by=author.id,
                    updated_by=author.id,
                )
            )
        db_session.flush()
        v.logic_source = (
            "def run(params, call_api, log):\n"
            "    rows = store.list('people')\n"
            "    return {'n': len(rows), 'unique': len(set(r['id'] for r in rows))}\n"
        )
        db_session.flush()
        out = AR.run_logic(
            db_session, None, app=app, version=v, user=author, venue_id=None
        )
        assert out["success"] is True
        # All of them, once each — paging must not repeat or drop a page.
        assert out["data"] == {"n": 1250, "unique": 1250}

    def test_an_explicit_limit_is_still_honoured(self, db_session, org, author):
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        for i in range(50):
            db_session.add(
                AppRecord(
                    id=str(uuid.uuid4()),
                    namespace="hr_suite",
                    organization_id=org.id,
                    collection="people",
                    data={"name": f"P{i}"},
                    created_by=author.id,
                    updated_by=author.id,
                )
            )
        db_session.flush()
        v.logic_source = (
            "def run(params, call_api, log):\n"
            "    return {'n': len(store.list('people', limit=10))}\n"
        )
        db_session.flush()
        out = AR.run_logic(
            db_session, None, app=app, version=v, user=author, venue_id=None
        )
        assert out["data"] == {"n": 10}

    def test_the_direct_door_still_bounds_a_single_query(self, db_session, org, author):
        """The per-query bound itself stays — one call must never be able to
        scan a whole table."""
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        for i in range(1100):
            db_session.add(
                AppRecord(
                    id=str(uuid.uuid4()),
                    namespace="hr_suite",
                    organization_id=org.id,
                    collection="people",
                    data={"name": f"P{i}"},
                    created_by=author.id,
                    updated_by=author.id,
                )
            )
        db_session.flush()
        rows = AR.store_list(
            db_session,
            app=app,
            version=v,
            user=author,
            collection="people",
            limit=99999,
        )
        assert len(rows) == 1000


class TestVenueIsNotAssumed:
    """Found by clicking the app, 17 Aug 2026: the Hiring board said "No job
    openings yet" while one existed, under a header reading "Openings across
    the group". The door substituted the app's currently-selected venue
    whenever logic named none, so an app could not ask for "everything I can
    see" at all — and Training only escaped because every one of its programs
    happens to be group-wide."""

    def _app_with_logic(self, db, org, author, code):
        app, v = _app_with(db, org, author, STORAGE_SPEC)
        v.logic_source = code
        db.flush()
        return app, v

    def test_logic_sees_other_venues_rows_by_default(self, db_session, org, author):
        here = _make_venue(db_session, organization_id=org.id, name="Here")
        there = _make_venue(db_session, organization_id=org.id, name="There")
        _make_venue_access(db_session, author, here)
        _make_venue_access(db_session, author, there)
        app, v = self._app_with_logic(
            db_session,
            org,
            author,
            "def run(params, call_api, log):\n"
            "    return {'names': [p['name'] for p in store.list('people')]}\n",
        )
        for venue, name in ((here, "here"), (there, "there"), (None, "global")):
            AR.store_put(
                db_session,
                app=app,
                version=v,
                user=author,
                collection="people",
                data={"name": name},
                venue_id=venue.id if venue else None,
            )
        # The app is being viewed with "Here" selected — the old behaviour.
        out = AR.run_logic(
            db_session, None, app=app, version=v, user=author, venue_id=here.id
        )
        assert sorted(out["data"]["names"]) == ["global", "here", "there"]

    def test_an_app_can_still_scope_to_the_selected_venue(
        self, db_session, org, author
    ):
        here = _make_venue(db_session, organization_id=org.id, name="Here")
        there = _make_venue(db_session, organization_id=org.id, name="There")
        _make_venue_access(db_session, author, here)
        _make_venue_access(db_session, author, there)
        app, v = self._app_with_logic(
            db_session,
            org,
            author,
            "def run(params, call_api, log):\n"
            "    rows = store.list('people', venue_id=store.current_venue(),\n"
            "                      include_global=False)\n"
            "    return {'names': [p['name'] for p in rows]}\n",
        )
        for venue, name in ((here, "here"), (there, "there")):
            AR.store_put(
                db_session,
                app=app,
                version=v,
                user=author,
                collection="people",
                data={"name": name},
                venue_id=venue.id,
            )
        out = AR.run_logic(
            db_session, None, app=app, version=v, user=author, venue_id=here.id
        )
        assert out["data"]["names"] == ["here"]

    def test_a_venue_the_viewer_cannot_reach_is_still_excluded(
        self, db_session, org, author
    ):
        """Reading group-wide must not become reading everything."""
        mine = _make_venue(db_session, organization_id=org.id, name="Mine")
        theirs = _make_venue(db_session, organization_id=org.id, name="Theirs")
        _make_venue_access(db_session, author, mine)
        _make_venue_access(db_session, author, theirs)  # author sets the scene
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        peer = _member(db_session, org, ["hr:read"])
        _make_venue_access(db_session, peer, mine)
        db_session.add(
            AppShare(app_id=app.id, principal_type="user", principal_id=peer.id)
        )
        db_session.flush()
        for venue, name in ((mine, "mine"), (theirs, "theirs")):
            AR.store_put(
                db_session,
                app=app,
                version=v,
                user=author,
                collection="people",
                data={"name": name},
                venue_id=venue.id,
            )
        names = {
            r["name"]
            for r in AR.store_list(
                db_session, app=app, version=v, user=peer, collection="people"
            )
        }
        assert names == {"mine"}


class TestQuerySurface:
    """JSONB earns its keep: typed comparisons, nested paths, ordering and a
    count that does not materialise the collection."""

    def _rows(self, db, org, author, app, v, rows):
        for data in rows:
            AR.store_put(
                db, app=app, version=v, user=author, collection="programs", data=data
            )

    def test_booleans_and_nulls_compare_correctly(self, db_session, org, author):
        """`str(True)` is 'True' and JSON says 'true', so this used to match
        nothing at all."""
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        self._rows(
            db_session,
            org,
            author,
            app,
            v,
            [
                {"name": "on", "is_active": True},
                {"name": "off", "is_active": False},
                {"name": "unset"},
            ],
        )
        got = lambda w: {  # noqa: E731
            r["name"]
            for r in AR.store_list(
                db_session,
                app=app,
                version=v,
                user=author,
                collection="programs",
                where=w,
            )
        }
        assert got({"is_active": True}) == {"on"}
        assert got({"is_active": False}) == {"off"}
        assert got({"is_active": {"is_null": True}}) == {"unset"}

    def test_a_nested_path_is_reachable(self, db_session, org, author):
        """The sign-off queue is exactly this query: completions awaiting a
        sign-off that has not happened."""
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        self._rows(
            db_session,
            org,
            author,
            app,
            v,
            [
                {"name": "waiting", "result": {"awaiting_signoff": True}},
                {
                    "name": "signed",
                    "result": {"awaiting_signoff": True, "signoff_at": "2026-08-17"},
                },
                {"name": "plain", "result": {}},
            ],
        )
        rows = AR.store_list(
            db_session,
            app=app,
            version=v,
            user=author,
            collection="programs",
            where={
                "result.awaiting_signoff": True,
                "result.signoff_at": {"is_null": True},
            },
        )
        assert [r["name"] for r in rows] == ["waiting"]

    def test_in_and_comparison_operators(self, db_session, org, author):
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        self._rows(
            db_session,
            org,
            author,
            app,
            v,
            [
                {"name": "a", "due": "2026-08-01"},
                {"name": "b", "due": "2026-08-20"},
                {"name": "c", "due": "2026-09-01"},
            ],
        )
        got = lambda w: {  # noqa: E731
            r["name"]
            for r in AR.store_list(
                db_session,
                app=app,
                version=v,
                user=author,
                collection="programs",
                where=w,
            )
        }
        assert got({"name": {"in": ["a", "c"]}}) == {"a", "c"}
        assert got({"name": {"not_in": ["a"]}}) == {"b", "c"}
        assert got({"due": {"lt": "2026-08-15"}}) == {"a"}
        assert got({"due": {"gte": "2026-08-20"}}) == {"b", "c"}
        assert got({"name": {"in": []}}) == set()

    def test_ordering_by_a_document_field(self, db_session, org, author):
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        self._rows(
            db_session,
            org,
            author,
            app,
            v,
            [{"name": "c"}, {"name": "a"}, {"name": "b"}],
        )
        names = [
            r["name"]
            for r in AR.store_list(
                db_session,
                app=app,
                version=v,
                user=author,
                collection="programs",
                order_by="name",
            )
        ]
        assert names == ["a", "b", "c"]
        desc = [
            r["name"]
            for r in AR.store_list(
                db_session,
                app=app,
                version=v,
                user=author,
                collection="programs",
                order_by="name",
                descending=True,
            )
        ]
        assert desc == ["c", "b", "a"]

    def test_count_without_fetching(self, db_session, org, author):
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)
        self._rows(
            db_session,
            org,
            author,
            app,
            v,
            [
                {"name": f"p{i}", "status": "open" if i < 3 else "closed"}
                for i in range(10)
            ],
        )
        assert (
            AR.store_count(
                db_session, app=app, version=v, user=author, collection="programs"
            )
            == 10
        )
        assert (
            AR.store_count(
                db_session,
                app=app,
                version=v,
                user=author,
                collection="programs",
                where={"status": "open"},
            )
            == 3
        )


class TestOneTransaction:
    def test_an_audit_failure_does_not_discard_the_write(
        self, db_session, org, author, monkeypatch
    ):
        """The audit used to be what committed the write, so its rollback-on-
        failure threw away the very operation it was recording."""
        app, v = _app_with(db_session, org, author, STORAGE_SPEC)

        import app.services.app_runtime as mod

        def _explode(*a, **k):
            raise RuntimeError("audit table is on fire")

        monkeypatch.setattr(mod, "_audit_storage", _explode)
        with pytest.raises(RuntimeError):
            AR.store_put(
                db_session,
                app=app,
                version=v,
                user=author,
                collection="people",
                data={"name": "Survivor"},
            )
        # The row itself is still in the session's transaction.
        assert [
            r.data["name"]
            for r in db_session.query(AppRecord)
            .filter(
                AppRecord.organization_id == org.id,
                AppRecord.collection == "people",
            )
            .all()
        ] == ["Survivor"]


class TestFiles:
    """Bytes an app owns. The point of building this rather than keeping
    Supabase: Orbit's 412 evidence files sit in a PUBLIC bucket, referenced
    only from inside a JSONB blob — anyone who ever saw a link keeps access,
    and nothing can delete them. Here a file is guarded exactly like the record
    it hangs off."""

    def _app(self, db, org, author):
        return _app_with(db, org, author, STORAGE_SPEC)

    def test_upload_list_fetch_delete(self, db_session, org, author):
        app, v = self._app(db_session, org, author)
        record = AR.store_put(
            db_session,
            app=app,
            version=v,
            user=author,
            collection="people",
            data={"name": "Ana"},
        )
        f = AR.file_put(
            db_session,
            app=app,
            version=v,
            user=author,
            collection="people",
            record_id=record["id"],
            filename="checklist.png",
            content_type="image/png",
            data=b"\x89PNG fake",
        )
        assert f["size_bytes"] == 9 and f["filename"] == "checklist.png"
        # metadata only — the bytes are never in the listing
        listed = AR.file_list(
            db_session,
            app=app,
            version=v,
            user=author,
            collection="people",
            record_id=record["id"],
        )
        assert len(listed) == 1 and "data" not in listed[0]

        row = AR.file_fetch(
            db_session, app=app, version=v, user=author, file_id=f["id"]
        )
        assert row.data == b"\x89PNG fake"

        AR.file_delete(db_session, app=app, version=v, user=author, file_id=f["id"])
        assert (
            AR.file_list(
                db_session, app=app, version=v, user=author, collection="people"
            )
            == []
        )

    def test_an_undeclared_collection_cannot_hold_files(self, db_session, org, author):
        app, v = self._app(db_session, org, author)
        with pytest.raises(HTTPException) as e:
            AR.file_put(
                db_session,
                app=app,
                version=v,
                user=author,
                collection="salaries",
                record_id=None,
                filename="x.pdf",
                content_type="application/pdf",
                data=b"x",
            )
        assert "salaries" in str(e.value.detail)

    def test_a_stranger_cannot_fetch_bytes(self, db_session, org, author):
        """Every fetch re-runs the guard — the whole reason this is not a URL."""
        app, v = self._app(db_session, org, author)
        f = AR.file_put(
            db_session,
            app=app,
            version=v,
            user=author,
            collection="people",
            record_id=None,
            filename="cv.pdf",
            content_type="application/pdf",
            data=b"secret",
        )
        stranger = _member(db_session, org, ["hr:read"])
        with pytest.raises(HTTPException) as e:
            AR.file_fetch(
                db_session, app=app, version=v, user=stranger, file_id=f["id"]
            )
        assert e.value.status_code == 404

    def test_a_viewer_without_write_approval_cannot_upload(
        self, db_session, org, author
    ):
        app, v = self._app(db_session, org, author)
        peer = _member(db_session, org, ["hr:read"])
        db_session.add(
            AppShare(
                app_id=app.id,
                principal_type="user",
                principal_id=peer.id,
                write_actions_approved=False,
            )
        )
        db_session.flush()
        with pytest.raises(HTTPException) as e:
            AR.file_put(
                db_session,
                app=app,
                version=v,
                user=peer,
                collection="people",
                record_id=None,
                filename="x.pdf",
                content_type="application/pdf",
                data=b"x",
            )
        assert "writes are not approved" in str(e.value.detail)

    def test_another_venues_file_is_not_listed_or_fetchable(
        self, db_session, org, author
    ):
        mine = _make_venue(db_session, organization_id=org.id, name="Mine")
        theirs = _make_venue(db_session, organization_id=org.id, name="Theirs")
        _make_venue_access(db_session, author, mine)
        _make_venue_access(db_session, author, theirs)
        app, v = self._app(db_session, org, author)
        hidden = AR.file_put(
            db_session,
            app=app,
            version=v,
            user=author,
            collection="people",
            record_id=None,
            venue_id=theirs.id,
            filename="theirs.pdf",
            content_type="application/pdf",
            data=b"nope",
        )
        peer = _member(db_session, org, ["hr:read"])
        _make_venue_access(db_session, peer, mine)
        db_session.add(
            AppShare(
                app_id=app.id,
                principal_type="user",
                principal_id=peer.id,
                write_actions_approved=True,
            )
        )
        db_session.flush()
        assert (
            AR.file_list(db_session, app=app, version=v, user=peer, collection="people")
            == []
        )
        with pytest.raises(HTTPException) as e:
            AR.file_fetch(
                db_session, app=app, version=v, user=peer, file_id=hidden["id"]
            )
        assert "access to that venue" in str(e.value.detail)

    def test_an_oversized_file_is_refused(self, db_session, org, author):
        app, v = self._app(db_session, org, author)
        with pytest.raises(HTTPException) as e:
            AR.file_put(
                db_session,
                app=app,
                version=v,
                user=author,
                collection="people",
                record_id=None,
                filename="huge.bin",
                content_type="application/octet-stream",
                data=b"x" * (16 * 1024 * 1024),
            )
        assert e.value.status_code == 413

    def test_files_are_audited(self, db_session, org, author):
        from app.db.models import AppCall

        app, v = self._app(db_session, org, author)
        f = AR.file_put(
            db_session,
            app=app,
            version=v,
            user=author,
            collection="people",
            record_id=None,
            filename="a.pdf",
            content_type="application/pdf",
            data=b"a",
        )
        AR.file_fetch(db_session, app=app, version=v, user=author, file_id=f["id"])
        AR.file_delete(db_session, app=app, version=v, user=author, file_id=f["id"])
        actions = {
            c.action
            for c in db_session.query(AppCall).filter(AppCall.app_id == app.id).all()
        }
        assert {"file_put:people", "file_get:people", "file_delete:people"} <= actions
