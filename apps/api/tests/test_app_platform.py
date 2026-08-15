"""The app platform's data door.

A Norm app is user-authored code: untrusted markup in a sandboxed iframe and
untrusted Python in the consolidator sandbox. Neither is the security boundary
— ``services/app_runtime.call_action`` is, and these are its tests.

Every case here is a way an app could reach further than it should: past its
own declared actions, past the viewer's permissions, past the write approval,
past venue access, or around the door entirely by going through the sandbox.
"""

import uuid

import pytest
from fastapi import HTTPException

from app.db.models import (
    App,
    AppCall,
    AppShare,
    AppVersion,
    Role,
)
from app.services import app_runtime as AR
from tests.conftest import (
    _make_membership,
    _make_organization,
    _make_user,
    _make_venue,
    _make_venue_access,
)


def _role(db, org, name, perms):
    role = Role(
        id=str(uuid.uuid4()),
        organization_id=org.id,
        name=name,
        display_name=name,
        permissions=perms,
    )
    db.add(role)
    db.flush()
    return role


def _member(db, org, perms, email=None):
    """A user whose org role holds exactly ``perms``."""
    user = _make_user(db, email=email or f"{uuid.uuid4().hex[:8]}@t.local")
    mem = _make_membership(db, user, org)
    mem.role_id = _role(db, org, f"r-{uuid.uuid4().hex[:6]}", perms).id
    db.flush()
    return user


def _app(db, org, author, **over):
    row = App(
        organization_id=org.id,
        created_by=author.id,
        slug=over.pop("slug", f"app-{uuid.uuid4().hex[:6]}"),
        name=over.pop("name", "Weekly performance"),
        visibility=over.pop("visibility", "private"),
        **over,
    )
    db.add(row)
    db.flush()
    return row


def _version(db, app, spec, author, **over):
    v = AppVersion(
        app_id=app.id,
        version=over.pop("version", 1),
        spec=spec,
        created_by=author.id,
        **over,
    )
    db.add(v)
    db.flush()
    app.current_version_id = v.id
    db.flush()
    return v


READ_SPEC = {
    "actions": [{"connector": "loadedhub", "action": "get_sales_for_period"}],
    "scopes": ["mcp:reports:read"],
}
WRITE_SPEC = {
    "actions": [
        {"connector": "loadedhub", "action": "get_sales_for_period"},
        {"connector": "loadedhub", "action": "place_order"},
    ],
    "writes": [{"connector": "loadedhub", "action": "place_order"}],
    "scopes": ["mcp:reports:read", "mcp:orders:draft"],
}


@pytest.fixture()
def org(db_session):
    return _make_organization(db_session)


@pytest.fixture()
def author(db_session, org):
    # Everything mcp:reports:read and mcp:orders:draft require.
    return _member(
        db_session,
        org,
        ["reports:read", "orders:read", "orders:write", "apps:build", "apps:share"],
    )


def _call(db, config_db, app, version, user, **over):
    kwargs = dict(
        app=app,
        version=version,
        user=user,
        venue_id=None,
        connector="loadedhub",
        action="get_sales_for_period",
        params={},
    )
    kwargs.update(over)
    return AR.call_action(db, config_db, **kwargs)


class TestAllowlist:
    """Reach is declared per VERSION, so editing an app can never widen what an
    already-shared copy may touch."""

    def test_an_undeclared_action_is_refused_by_name(self, db_session, org, author):
        app = _app(db_session, org, author)
        v = _version(db_session, app, READ_SPEC, author)
        with pytest.raises(HTTPException) as e:
            _call(db_session, None, app, v, author, action="get_roster")
        assert e.value.status_code == 403
        assert "get_roster" in str(e.value.detail)

    def test_a_declared_action_passes_the_allowlist(
        self, db_session, org, author, monkeypatch
    ):
        app = _app(db_session, org, author)
        v = _version(db_session, app, READ_SPEC, author)
        seen = {}

        def fake_exec(connector, action, params, db, cdb, **kw):
            seen.update({"connector": connector, "action": action, **kw})
            return type("R", (), {"success": True, "payload": [1, 2], "error": None})()

        monkeypatch.setattr(AR, "_tool_method", lambda *a: "GET")
        monkeypatch.setattr(
            "app.connectors.tool_executor.execute_connector_tool", fake_exec
        )
        assert _call(db_session, None, app, v, author) == [1, 2]
        # No silent fall back to another venue's credentials.
        assert seen["strict_venue"] is True


class TestIntersection:
    """Effective permission = the VIEWER's permissions ∩ the app's declared
    scopes. Both directions have to hold."""

    def test_a_viewer_without_the_permission_is_refused(
        self, db_session, org, author, monkeypatch
    ):
        app = _app(db_session, org, author)
        v = _version(db_session, app, READ_SPEC, author)
        # Shared with someone who cannot read reports at all.
        junior = _member(db_session, org, ["tasks:read"])
        db_session.add(
            AppShare(app_id=app.id, principal_type="user", principal_id=junior.id)
        )
        db_session.flush()
        monkeypatch.setattr(AR, "_tool_method", lambda *a: "GET")
        with pytest.raises(HTTPException) as e:
            _call(db_session, None, app, v, junior)
        assert e.value.status_code == 403
        assert "reports:read" in str(e.value.detail)

    def test_the_app_cannot_exceed_its_author(self, db_session, org):
        # The save-time half of the same rule: an author declaring reach they
        # do not hold is refused while it can still be fixed.
        weak = _member(db_session, org, ["apps:build", "tasks:read"])
        missing = AR.required_permissions(READ_SPEC) - AR.org_permissions(
            db_session, weak
        )
        assert "reports:read" in missing

    def test_a_platform_admin_holds_every_org_permission(self, db_session, org):
        admin = _make_user(db_session, email="a@t.local", role="admin")
        assert AR.required_permissions(WRITE_SPEC) <= AR.org_permissions(
            db_session, admin
        )


class TestWritePolicy:
    """A write is opted into twice: declared by the version, approved on the
    share. Default off."""

    def test_an_undeclared_write_is_refused(self, db_session, org, author, monkeypatch):
        spec = dict(WRITE_SPEC, writes=[])  # action allowed, but not AS a write
        app = _app(db_session, org, author)
        v = _version(db_session, app, spec, author)
        monkeypatch.setattr(AR, "_tool_method", lambda *a: "POST")
        with pytest.raises(HTTPException) as e:
            _call(db_session, None, app, v, author, action="place_order")
        assert e.value.status_code == 403
        assert "not declared as a write" in str(e.value.detail)

    def test_a_shared_viewer_without_approval_is_refused(
        self, db_session, org, author, monkeypatch
    ):
        app = _app(db_session, org, author)
        v = _version(db_session, app, WRITE_SPEC, author)
        peer = _member(db_session, org, ["reports:read", "orders:read", "orders:write"])
        db_session.add(
            AppShare(
                app_id=app.id,
                principal_type="user",
                principal_id=peer.id,
                write_actions_approved=False,
            )
        )
        db_session.flush()
        monkeypatch.setattr(AR, "_tool_method", lambda *a: "POST")
        with pytest.raises(HTTPException) as e:
            _call(db_session, None, app, v, peer, action="place_order")
        assert e.value.status_code == 403
        assert "writes are not approved" in str(e.value.detail)

    def test_approval_lets_the_write_through(
        self, db_session, org, author, monkeypatch
    ):
        app = _app(db_session, org, author)
        v = _version(db_session, app, WRITE_SPEC, author)
        peer = _member(db_session, org, ["reports:read", "orders:read", "orders:write"])
        db_session.add(
            AppShare(
                app_id=app.id,
                principal_type="user",
                principal_id=peer.id,
                write_actions_approved=True,
            )
        )
        db_session.flush()
        monkeypatch.setattr(AR, "_tool_method", lambda *a: "POST")
        monkeypatch.setattr(
            "app.connectors.tool_executor.execute_connector_tool",
            lambda *a, **k: type(
                "R", (), {"success": True, "payload": "ok", "error": None}
            )(),
        )
        assert _call(db_session, None, app, v, peer, action="place_order") == "ok"

    def test_the_method_comes_from_the_spec_not_the_app(
        self, db_session, org, author, monkeypatch
    ):
        # An app cannot relabel a write as a read: the method is read off the
        # ConnectorSpec, never off anything the app declares.
        app = _app(db_session, org, author)
        v = _version(db_session, app, dict(WRITE_SPEC, writes=[]), author)
        monkeypatch.setattr(AR, "_tool_method", lambda *a: "DELETE")
        with pytest.raises(HTTPException) as e:
            _call(db_session, None, app, v, author, action="place_order")
        assert "changes data" in str(e.value.detail)


class TestVenueAccess:
    """Fails CLOSED, unlike venue_service.get_user_venues, which hands a user
    with no access rows every venue on the platform."""

    def test_no_access_row_means_no_venue(self, db_session, org, author, monkeypatch):
        app = _app(db_session, org, author)
        v = _version(db_session, app, READ_SPEC, author)
        other = _make_venue(db_session, organization_id=org.id)
        monkeypatch.setattr(AR, "_tool_method", lambda *a: "GET")
        with pytest.raises(HTTPException) as e:
            _call(db_session, None, app, v, author, venue_id=other.id)
        assert e.value.status_code == 403
        assert "access to that venue" in str(e.value.detail)

    def test_a_granted_venue_is_allowed(self, db_session, org, author, monkeypatch):
        app = _app(db_session, org, author)
        v = _version(db_session, app, READ_SPEC, author)
        mine = _make_venue(db_session, organization_id=org.id)
        _make_venue_access(db_session, author, mine)
        monkeypatch.setattr(AR, "_tool_method", lambda *a: "GET")
        monkeypatch.setattr(
            "app.connectors.tool_executor.execute_connector_tool",
            lambda *a, **k: type(
                "R", (), {"success": True, "payload": 1, "error": None}
            )(),
        )
        assert _call(db_session, None, app, v, author, venue_id=mine.id) == 1


class TestAudience:
    def test_a_private_app_does_not_exist_for_anyone_else(
        self, db_session, org, author
    ):
        app = _app(db_session, org, author)
        stranger = _member(db_session, org, ["reports:read"])
        assert AR.resolve_access(db_session, app, author).can_run is True
        assert AR.resolve_access(db_session, app, stranger).can_run is False

    def test_another_org_never_sees_it(self, db_session, org, author):
        app = _app(db_session, org, author)
        other_org = _make_organization(db_session, name="Other", slug="other")
        outsider = _member(db_session, other_org, ["reports:read"])
        db_session.add(
            AppShare(
                app_id=app.id, principal_type="organization", principal_id=other_org.id
            )
        )
        db_session.flush()
        # The share names another org; membership of THIS app's org is what
        # counts, so it still resolves to nothing.
        assert AR.resolve_access(db_session, app, outsider).can_run is False

    def test_the_widest_matching_grant_wins(self, db_session, org, author):
        app = _app(db_session, org, author)
        peer = _member(db_session, org, ["reports:read"])
        db_session.add_all(
            [
                AppShare(
                    app_id=app.id,
                    principal_type="organization",
                    principal_id=org.id,
                    access="view",
                ),
                AppShare(
                    app_id=app.id,
                    principal_type="user",
                    principal_id=peer.id,
                    access="edit",
                    write_actions_approved=True,
                ),
            ]
        )
        db_session.flush()
        access = AR.resolve_access(db_session, app, peer)
        assert access.role == "edit" and access.write_approved is True

    def test_archived_apps_stop_running(self, db_session, org, author):
        import datetime

        app = _app(db_session, org, author)
        app.archived_at = datetime.datetime.now(datetime.timezone.utc)
        db_session.flush()
        assert AR.resolve_access(db_session, app, author).can_run is False


class TestAudit:
    def test_every_call_is_recorded_including_failures(
        self, db_session, org, author, monkeypatch
    ):
        app = _app(db_session, org, author)
        v = _version(db_session, app, READ_SPEC, author)
        monkeypatch.setattr(AR, "_tool_method", lambda *a: "GET")
        monkeypatch.setattr(
            "app.connectors.tool_executor.execute_connector_tool",
            lambda *a, **k: type(
                "R", (), {"success": False, "payload": None, "error": "boom"}
            )(),
        )
        with pytest.raises(HTTPException):
            _call(db_session, None, app, v, author)
        row = db_session.query(AppCall).filter(AppCall.app_id == app.id).one()
        assert row.ok is False and row.error == "boom"
        assert row.connector == "loadedhub" and row.method == "GET"
        assert row.app_version_id == v.id


class TestLogicGoesThroughTheDoor:
    """The sandbox must not be a way around the app's own declared reach."""

    def test_an_undeclared_call_inside_run_is_refused(
        self, db_session, org, author, monkeypatch
    ):
        app = _app(db_session, org, author)
        code = (
            "def run(params, call_api, log):\n"
            "    return call_api('loadedhub', 'get_roster', {})\n"
        )
        v = _version(db_session, app, READ_SPEC, author, logic_source=code)
        monkeypatch.setattr(AR, "_tool_method", lambda *a: "GET")
        out = AR.run_logic(
            db_session, None, app=app, version=v, user=author, venue_id=None
        )
        # The refusal comes back as data (call_api's contract), never as data
        # the app asked for.
        assert out["success"] is True
        assert "get_roster" in str(out["data"]["error"])

    def test_a_declared_call_inside_run_works(
        self, db_session, org, author, monkeypatch
    ):
        app = _app(db_session, org, author)
        code = (
            "def run(params, call_api, log):\n"
            "    return call_api('loadedhub', 'get_sales_for_period', {})\n"
        )
        v = _version(db_session, app, READ_SPEC, author, logic_source=code)
        monkeypatch.setattr(AR, "_tool_method", lambda *a: "GET")
        monkeypatch.setattr(
            "app.connectors.tool_executor.execute_connector_tool",
            lambda *a, **k: type(
                "R", (), {"success": True, "payload": {"total": 12}, "error": None}
            )(),
        )
        out = AR.run_logic(
            db_session, None, app=app, version=v, user=author, venue_id=None
        )
        assert out["data"] == {"total": 12}


class TestReach:
    def test_reach_reads_as_consent_text(self):
        text = " ".join(AR.describe_reach(WRITE_SPEC))
        assert "View sales and performance data" in text
        assert "Draft purchase orders" in text

    def test_an_unknown_scope_grants_nothing(self):
        assert AR.required_permissions({"scopes": ["mcp:not:a:scope"]}) == set()


class TestBuilderTools:
    """The App Builder's internal tools. save_app must be the SAME
    implementation as the web endpoint (the author-permission intersection can
    never drift), and the catalogue is the ground truth that keeps invented
    action names out of specs."""

    def _thread(self, db, user):
        from tests.conftest import _make_thread

        return _make_thread(db, user=user)

    def test_save_app_creates_then_versions(self, db_session, org, author):
        from app.agents.internal_tools import get_handler

        handler = get_handler("norm", "save_app")
        thread = self._thread(db_session, author)
        payload = {
            "name": "Outstanding invoices",
            "slug": "outstanding-invoices",
            "icon": "🧾",
            "spec": READ_SPEC,
            "ui_source": "<div>v1</div>",
        }
        out = handler(payload, db_session, thread.id)
        assert out["success"], out
        assert out["data"]["slug"] == "outstanding-invoices"
        assert out["data"]["version"] == 1
        assert out["data"]["open_url"] == "/apps/outstanding-invoices"
        # Same slug again → a new immutable version, not a new app.
        out2 = handler({**payload, "ui_source": "<div>v2</div>"}, db_session, thread.id)
        assert out2["data"]["version"] == 2
        from app.db.models import App as AppModel

        assert (
            db_session.query(AppModel)
            .filter(
                AppModel.slug == "outstanding-invoices",
                AppModel.organization_id == org.id,
            )
            .count()
            == 1
        )

    def test_a_rename_without_the_slug_is_refused(self, db_session, org, author):
        # Optional slug let a rename mint a NEW app: the model sent the new
        # name only, save_app derived a slug from it, and the org had two apps
        # (live, 15 Aug 2026). The tool now demands the choice be explicit.
        from app.agents.internal_tools import get_handler

        thread = self._thread(db_session, author)
        out = get_handler("norm", "save_app")(
            {"name": "Renamed", "spec": READ_SPEC, "ui_source": "<div/>"},
            db_session,
            thread.id,
        )
        assert not out["success"]
        assert "slug" in out["error"] and "never changes" in out["error"]

    def test_save_app_refuses_reach_the_author_lacks(self, db_session, org):
        from app.agents.internal_tools import get_handler

        weak = _member(db_session, org, ["apps:build", "tasks:read"])
        thread = self._thread(db_session, weak)
        out = get_handler("norm", "save_app")(
            {"name": "Too far", "slug": "too-far", "spec": READ_SPEC, "ui_source": "<div/>"},
            db_session,
            thread.id,
        )
        assert not out["success"]
        assert "reports:read" in out["error"]

    def test_save_app_requires_the_build_permission(self, db_session, org):
        from app.agents.internal_tools import get_handler

        norole = _member(db_session, org, ["tasks:read"])
        thread = self._thread(db_session, norole)
        out = get_handler("norm", "save_app")(
            {"name": "X", "slug": "x", "spec": {}, "ui_source": "<div/>"},
            db_session,
            thread.id,
        )
        assert not out["success"] and "apps:build" in out["error"]

    def test_get_app_round_trips_and_respects_audience(self, db_session, org, author):
        from app.agents.internal_tools import get_handler

        thread = self._thread(db_session, author)
        get_handler("norm", "save_app")(
            {
                "name": "Mine",
                "slug": "mine",
                "spec": READ_SPEC,
                "ui_source": "<b>hi</b>",
            },
            db_session,
            thread.id,
        )
        out = get_handler("norm", "get_app")({"slug": "mine"}, db_session, thread.id)
        assert out["success"] and out["data"]["ui_source"] == "<b>hi</b>"
        # A private app does not exist for anyone else.
        stranger = _member(db_session, org, ["reports:read"])
        s_thread = self._thread(db_session, stranger)
        out2 = get_handler("norm", "get_app")({"slug": "mine"}, db_session, s_thread.id)
        assert not out2["success"]

    def test_capabilities_lists_scopes(self, db_session, org, author):
        # The scope half is code-defined and testable without the config DB;
        # the action half needs live ConnectorSpec rows (covered in the live
        # walk).
        from app.agents.internal_tools import get_handler

        thread = self._thread(db_session, author)
        out = get_handler("norm", "list_app_capabilities")({}, db_session, thread.id)
        assert out["success"]
        names = {s["name"] for s in out["data"]["scopes"]}
        assert "mcp:reports:read" in names and "mcp:orders:draft" in names


class TestSpecValidation:
    """A spec whose permission story is a typo must be refused at save, not
    silently accepted with a vacuous intersection (the builder invented
    'loadedhub:read' on its first real run — this class exists because of it)."""

    def test_an_unknown_scope_is_refused_with_the_vocabulary(
        self, db_session, org, author
    ):
        with pytest.raises(HTTPException) as e:
            AR.save_app(
                db_session,
                author,
                {
                    "name": "Typo app",
                    "spec": {"actions": [], "scopes": ["loadedhub:read"]},
                    "ui_source": "<div/>",
                },
            )
        assert e.value.status_code == 400
        assert "loadedhub:read" in str(e.value.detail)
        assert "mcp:reports:read" in str(e.value.detail)  # names the valid ones

    def test_a_malformed_action_entry_is_refused(self, db_session, org, author):
        with pytest.raises(HTTPException) as e:
            AR.save_app(
                db_session,
                author,
                {
                    "name": "Bad actions",
                    "spec": {"actions": ["get_sales_for_period"], "scopes": []},
                    "ui_source": "<div/>",
                },
            )
        assert e.value.status_code == 400


class TestSharingEndpoints:
    """The sharing lifecycle over HTTP: grant → list → revoke, and the
    visibility label always telling the truth about who can see the app."""

    def _client_for(self, client, db, user):
        from app.auth.security import create_access_token

        token = create_access_token({"sub": user.id, "email": user.email})
        return {"Authorization": f"Bearer {token}"}

    def _made(self, db, org, author):
        app = _app(db, org, author, slug="share-me", name="Share me")
        _version(db, app, WRITE_SPEC, author)
        db.commit()
        return app

    def test_grant_list_revoke_round_trip(self, client, db_session, org, author):
        self._made(db_session, org, author)
        peer = _member(
            db_session,
            org,
            ["reports:read", "orders:read", "orders:write", "apps:build", "apps:share"],
        )
        db_session.commit()
        h = self._client_for(client, db_session, author)

        r = client.post(
            "/api/apps/share-me/share",
            json={"principal_type": "user", "principal_id": peer.id},
            headers=h,
        )
        assert r.status_code == 200, r.text
        assert r.json()["visibility"] == "users"

        r = client.get("/api/apps/share-me/shares", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["writes"] == ["loadedhub.place_order"]
        assert len(body["shares"]) == 1
        share = body["shares"][0]
        assert share["write_actions_approved"] is False  # default OFF, always

        r = client.delete(f"/api/apps/share-me/share/{share['id']}", headers=h)
        assert r.status_code == 200
        assert r.json()["visibility"] == "private"  # no shares IS private

    def test_approving_writes_needs_the_permissions(self, client, db_session, org):
        # An author who can share but cannot place orders must not be able to
        # approve an app's order-writing for someone else.
        sharer = _member(db_session, org, ["reports:read", "apps:build", "apps:share"])
        app = _app(db_session, org, sharer, slug="writey", name="Writey")
        # Version written by someone else with wider reach; sharer tries to
        # approve its writes.
        _version(db_session, app, WRITE_SPEC, sharer)
        peer = _member(db_session, org, ["reports:read"])
        db_session.commit()
        h = self._client_for(client, db_session, sharer)
        r = client.post(
            "/api/apps/writey/share",
            json={
                "principal_type": "user",
                "principal_id": peer.id,
                "approve_writes": True,
            },
            headers=h,
        )
        assert r.status_code == 403
        assert "could not perform yourself" in r.json()["detail"]

    def test_a_viewer_cannot_see_the_share_list(self, client, db_session, org, author):
        self._made(db_session, org, author)
        peer = _member(db_session, org, ["reports:read"])
        db_session.add(
            AppShare(
                app_id=db_session.query(App)
                .filter(App.slug == "share-me", App.organization_id == org.id)
                .first()
                .id,
                principal_type="user",
                principal_id=peer.id,
            )
        )
        db_session.commit()
        h = self._client_for(client, db_session, peer)
        r = client.get("/api/apps/share-me/shares", headers=h)
        assert r.status_code == 403

    def test_candidates_lists_the_org_not_the_world(
        self, client, db_session, org, author
    ):
        self._made(db_session, org, author)
        peer = _member(db_session, org, ["reports:read"], email="peer@t.local")
        other_org = _make_organization(db_session, name="Elsewhere", slug="elsewhere")
        outsider = _member(
            db_session, other_org, ["reports:read"], email="outsider@t.local"
        )
        author_role = [
            "reports:read",
            "orders:read",
            "orders:write",
            "apps:build",
            "apps:share",
        ]
        # author's role needs apps:share for the endpoint's permission gate
        from app.db.models import OrganizationMembership

        mem = (
            db_session.query(OrganizationMembership)
            .filter(OrganizationMembership.user_id == author.id)
            .first()
        )
        role = db_session.query(Role).filter(Role.id == mem.role_id).first()
        role.permissions = author_role
        db_session.commit()
        h = self._client_for(client, db_session, author)
        r = client.get("/api/apps/share-me/share-candidates", headers=h)
        assert r.status_code == 200, r.text
        ids = {u["id"] for u in r.json()["users"]}
        assert peer.id in ids
        assert outsider.id not in ids
        assert author.id not in ids  # you don't share with yourself


class TestPinning:
    """A pin is the viewer's own nav shortcut — per user, never per app."""

    def _headers(self, user):
        from app.auth.security import create_access_token

        return {
            "Authorization": f"Bearer {create_access_token({'sub': user.id, 'email': user.email})}"
        }

    def test_pin_round_trip_and_isolation(self, client, db_session, org, author):
        app = _app(db_session, org, author, slug="pin-me", name="Pin me")
        _version(db_session, app, READ_SPEC, author)
        peer = _member(db_session, org, ["reports:read"])
        db_session.add(
            AppShare(app_id=app.id, principal_type="user", principal_id=peer.id)
        )
        db_session.commit()

        h = self._headers(author)
        r = client.post("/api/apps/pin-me/pin", json={"pinned": True}, headers=h)
        assert r.status_code == 200 and r.json()["pinned"] is True
        apps = client.get("/api/apps", headers=h).json()["apps"]
        assert next(a for a in apps if a["slug"] == "pin-me")["pinned"] is True

        # The peer's nav is untouched by the author's pin.
        hp = self._headers(peer)
        apps_p = client.get("/api/apps", headers=hp).json()["apps"]
        assert next(a for a in apps_p if a["slug"] == "pin-me")["pinned"] is False

        r = client.post("/api/apps/pin-me/pin", json={"pinned": False}, headers=h)
        assert r.json()["pinned"] is False

    def test_a_stranger_cannot_pin_a_private_app(self, client, db_session, org, author):
        app = _app(db_session, org, author, slug="secret", name="Secret")
        _version(db_session, app, READ_SPEC, author)
        stranger = _member(db_session, org, ["reports:read"])
        db_session.commit()
        r = client.post(
            "/api/apps/secret/pin",
            json={"pinned": True},
            headers=self._headers(stranger),
        )
        assert r.status_code == 404
