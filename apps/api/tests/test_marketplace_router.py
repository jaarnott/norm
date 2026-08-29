"""Marketplace API: browse open to members, changes gated on billing:manage.

The gate matters: enabling a paid App IS a billing act, so the existing
Owner-only scope is reused — no new permission was invented
(docs/apps-marketplace-plan.md Phase 1).
"""

import uuid

from app.db.config_models import MarketplaceApp
from app.db.models import Role
from tests.conftest import _make_membership, _make_organization, _make_user


def _seed_app(db, slug="loaded", bundled=True):
    db.add(
        MarketplaceApp(
            slug=slug,
            name=slug.title(),
            description="",
            tier="integration",
            bundled=bundled,
            composition={"spec": "loadedhub"},
        )
    )
    db.flush()


def _org_user(db, *, permissions):
    user = _make_user(db)
    org = _make_organization(db)
    role = Role(
        id=str(uuid.uuid4()),
        organization_id=org.id,
        name=f"r-{uuid.uuid4().hex[:6]}",
        display_name="R",
        permissions=permissions,
        is_system=False,
    )
    db.add(role)
    db.flush()
    mem = _make_membership(db, user, org)
    mem.role_id = role.id
    db.flush()
    return user, org


def _headers(user):
    from app.auth.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}


class TestBrowse:
    def test_lists_catalog_with_effective_state(self, client, db_session):
        _seed_app(db_session, "loaded", bundled=True)
        _seed_app(db_session, "paid", bundled=False)
        user, _org = _org_user(db_session, permissions=["org:read"])
        db_session.commit()
        r = client.get("/api/marketplace", headers=_headers(user))
        assert r.status_code == 200
        by_slug = {a["slug"]: a for a in r.json()["apps"]}
        assert by_slug["loaded"]["enabled"] is True  # bundled default
        assert by_slug["paid"]["enabled"] is False


class TestEnableDisable:
    def test_owner_scope_can_toggle(self, client, db_session):
        _seed_app(db_session)
        user, _org = _org_user(db_session, permissions=["billing:manage"])
        db_session.commit()
        r = client.post("/api/marketplace/loaded/disable", headers=_headers(user))
        assert r.status_code == 200 and r.json()["enabled"] is False
        r = client.get("/api/marketplace", headers=_headers(user))
        assert {a["slug"]: a["enabled"] for a in r.json()["apps"]}["loaded"] is False
        r = client.post("/api/marketplace/loaded/enable", headers=_headers(user))
        assert r.status_code == 200 and r.json()["enabled"] is True

    def test_without_billing_manage_is_403(self, client, db_session):
        _seed_app(db_session)
        user, _org = _org_user(db_session, permissions=["org:read", "apps:build"])
        db_session.commit()
        r = client.post("/api/marketplace/loaded/disable", headers=_headers(user))
        assert r.status_code == 403

    def test_unknown_app_is_404(self, client, db_session):
        user, _org = _org_user(db_session, permissions=["billing:manage"])
        db_session.commit()
        r = client.post("/api/marketplace/nope/enable", headers=_headers(user))
        assert r.status_code == 404


class TestSubmission:
    def _user_app(self, db, org, user, slug="mini"):
        import uuid as _uuid

        from app.db.models import App, AppVersion

        app = App(
            id=str(_uuid.uuid4()),
            organization_id=org.id,
            created_by=user.id,
            slug=slug,
            name="Mini Dashboard",
            description="Tiny sales view",
            icon="📈",
            agent="reports",
            purpose="demo",
            visibility="organization",
        )
        db.add(app)
        db.flush()
        ver = AppVersion(
            id=str(_uuid.uuid4()),
            app_id=app.id,
            version=1,
            spec={"actions": [{"connector": "loadedhub", "action": "get_sales"}]},
            ui_source="<div/>",
            created_by=user.id,
        )
        db.add(ver)
        db.flush()
        app.current_version_id = ver.id
        db.flush()
        return app

    def test_submit_creates_pending_row_with_derived_connections(
        self, client, db_session
    ):
        user, org = _org_user(db_session, permissions=["billing:manage"])
        self._user_app(db_session, org, user)
        db_session.commit()
        r = client.post(
            "/api/marketplace/submit",
            headers=_headers(user),
            json={"app_slug": "mini"},
        )
        assert r.status_code == 200 and r.json()["status"] == "pending"
        slug = r.json()["slug"]
        # visible to the submitting org while pending
        listing = client.get("/api/marketplace", headers=_headers(user)).json()
        row = next(a for a in listing["apps"] if a["slug"] == slug)
        assert row["tier"] == "user" and row["status"] == "pending"
        assert row["composition"]["connections"] == ["loadedhub"]

    def test_pending_hidden_from_other_orgs_until_approved(self, client, db_session):
        owner, org = _org_user(db_session, permissions=["billing:manage"])
        self._user_app(db_session, org, owner)
        db_session.commit()
        slug = client.post(
            "/api/marketplace/submit", headers=_headers(owner), json={"app_slug": "mini"}
        ).json()["slug"]
        stranger, _org2 = _org_user(db_session, permissions=["org:read"])
        db_session.commit()
        listing = client.get("/api/marketplace", headers=_headers(stranger)).json()
        assert slug not in {a["slug"] for a in listing["apps"]}
        # platform admin approves -> visible to everyone
        admin = _make_user(db_session, role="admin")
        db_session.commit()
        r = client.post(f"/api/marketplace/{slug}/approve", headers=_headers(admin))
        assert r.status_code == 200
        listing = client.get("/api/marketplace", headers=_headers(stranger)).json()
        assert slug in {a["slug"] for a in listing["apps"]}

    def test_approve_requires_platform_admin(self, client, db_session):
        user, org = _org_user(db_session, permissions=["billing:manage"])
        self._user_app(db_session, org, user)
        db_session.commit()
        slug = client.post(
            "/api/marketplace/submit", headers=_headers(user), json={"app_slug": "mini"}
        ).json()["slug"]
        r = client.post(f"/api/marketplace/{slug}/approve", headers=_headers(user))
        assert r.status_code == 403
