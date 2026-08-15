"""The app platform's HTTP surface.

Thin by design. Every authorization decision lives in
``services/app_runtime`` so there is one place to read and one place to get
wrong — these endpoints resolve the app and the version, then hand over.

The split that matters: ``/call`` and ``/run`` are what a RUNNING app uses and
are open to anyone the app is shared with; everything that changes an app
requires ``apps:build``, and widening its audience requires ``apps:share``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_permission
from app.db.engine import get_config_db, get_db
from app.db.models import User
from app.services.app_runtime import (
    call_action,
    describe_reach,
    org_permissions,
    required_permissions,
    resolve_access,
    run_logic,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _load(db: Session, slug: str, user: User):
    """Resolve an app + the version to run, or 404.

    A viewer always runs the app's CURRENT version. Sharing pins a version by
    setting ``current_version_id``, so the author editing a draft never changes
    what anyone else is running.
    """
    from app.db.models import App, AppVersion, OrganizationMembership

    membership = (
        db.query(OrganizationMembership)
        .filter(OrganizationMembership.user_id == user.id)
        .first()
    )
    q = db.query(App).filter(App.slug == slug)
    if membership:
        q = q.filter(App.organization_id == membership.organization_id)
    app = q.first()
    if not app:
        raise HTTPException(404, "app not found")

    version = None
    if app.current_version_id:
        version = (
            db.query(AppVersion).filter(AppVersion.id == app.current_version_id).first()
        )
    if version is None:
        version = (
            db.query(AppVersion)
            .filter(AppVersion.app_id == app.id)
            .order_by(AppVersion.version.desc())
            .first()
        )
    if version is None:
        raise HTTPException(409, f"'{app.name}' has no saved version yet")
    return app, version


class CallRequest(BaseModel):
    connector: str
    action: str
    params: dict | None = None
    venue_id: str | None = None


@router.post("/apps/{slug}/call")
async def app_call(
    slug: str,
    body: CallRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """The data door. Everything an app reads or writes comes through here."""
    app, version = _load(db, slug, user)
    data = call_action(
        db,
        config_db,
        app=app,
        version=version,
        user=user,
        venue_id=body.venue_id,
        connector=body.connector,
        action=body.action,
        params=body.params,
    )
    return {"data": data}


class RunRequest(BaseModel):
    params: dict | None = None
    venue_id: str | None = None


@router.post("/apps/{slug}/run")
async def app_run(
    slug: str,
    body: RunRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Run the app's server-side logic in the consolidator sandbox."""
    app, version = _load(db, slug, user)
    out = run_logic(
        db,
        config_db,
        app=app,
        version=version,
        user=user,
        venue_id=body.venue_id,
        params=body.params,
    )
    if not out.get("success"):
        raise HTTPException(400, out.get("error") or "the app's logic failed")
    return {"data": out.get("data"), "logs": out.get("_logs") or []}


@router.get("/apps")
async def list_apps(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Every app this user may run — theirs, plus whatever is shared with them."""
    from app.db.models import App, OrganizationMembership

    membership = (
        db.query(OrganizationMembership)
        .filter(OrganizationMembership.user_id == user.id)
        .first()
    )
    if not membership:
        return {"apps": []}

    out = []
    for app in (
        db.query(App)
        .filter(
            App.organization_id == membership.organization_id,
            App.archived_at.is_(None),
        )
        .order_by(App.name)
        .all()
    ):
        access = resolve_access(db, app, user)
        if not access.can_run:
            continue
        out.append(
            {
                "slug": app.slug,
                "name": app.name,
                "description": app.description,
                "icon": app.icon,
                "visibility": app.visibility,
                "mine": app.created_by == user.id,
                "access": access.role,
            }
        )
    return {"apps": out}


@router.get("/apps/{slug}")
async def get_app(
    slug: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """One app, with the version to render and its reach in plain language.

    ``reach`` and ``missing_permissions`` are what the runtime shows BEFORE it
    renders anything: the consent text is the same vocabulary the Claude
    connector uses, and a viewer who cannot do what the app does is told which
    permission is missing rather than watching every call fail.
    """
    app, version = _load(db, slug, user)
    access = resolve_access(db, app, user)
    if not access.can_run:
        raise HTTPException(404, "app not found")
    spec = version.spec or {}
    return {
        "slug": app.slug,
        "name": app.name,
        "description": app.description,
        "icon": app.icon,
        "purpose": app.purpose,
        "visibility": app.visibility,
        "access": access.role,
        "write_approved": access.write_approved,
        "version": version.version,
        "spec": spec,
        "ui_source": version.ui_source,
        "has_logic": bool(version.logic_source),
        "reach": describe_reach(spec),
        "missing_permissions": sorted(
            required_permissions(spec) - org_permissions(db, user)
        ),
    }


class SaveRequest(BaseModel):
    name: str
    slug: str | None = None
    description: str | None = None
    icon: str | None = None
    purpose: str | None = None
    spec: dict
    ui_source: str | None = None
    logic_source: str | None = None
    changelog: str | None = None


@router.post("/apps")
async def save_app_endpoint(
    body: SaveRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("apps:build")),
):
    """Create an app or add a version — the shared implementation lives in
    ``app_runtime.save_app`` so this endpoint and the builder agent's tool can
    never drift."""
    from app.services.app_runtime import save_app

    return save_app(db, user, body.model_dump())


class ShareRequest(BaseModel):
    principal_type: str  # user | venue | organization
    principal_id: str
    access: str = "view"
    approve_writes: bool = False


@router.post("/apps/{slug}/share")
async def share_app(
    slug: str,
    body: ShareRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("apps:share")),
):
    """Widen an app's audience — and, separately, approve its writes for them.

    Approving writes requires holding every permission the app's write actions
    need. Sharing is how an app reaches someone else's data; the person doing it
    must be able to do those things themselves.
    """
    from app.db.models import AppShare

    app, version = _load(db, slug, user)
    access = resolve_access(db, app, user)
    if access.role != "owner" and access.role != "edit":
        raise HTTPException(403, "only the author can share this app")
    if body.principal_type not in ("user", "venue", "organization"):
        raise HTTPException(400, "principal_type must be user, venue or organization")

    if body.approve_writes:
        missing = required_permissions(version.spec or {}) - org_permissions(db, user)
        if missing:
            raise HTTPException(
                403,
                "you cannot approve writes you could not perform yourself — "
                "missing: " + ", ".join(sorted(missing)),
            )

    row = (
        db.query(AppShare)
        .filter(
            AppShare.app_id == app.id,
            AppShare.principal_type == body.principal_type,
            AppShare.principal_id == body.principal_id,
        )
        .first()
    )
    if row is None:
        row = AppShare(
            app_id=app.id,
            principal_type=body.principal_type,
            principal_id=body.principal_id,
            granted_by=user.id,
        )
        db.add(row)
    row.access = body.access if body.access in ("view", "edit") else "view"
    row.write_actions_approved = bool(body.approve_writes)

    if body.principal_type == "organization":
        app.visibility = "organization"
    elif app.visibility == "private":
        app.visibility = (
            body.principal_type + "s" if body.principal_type == "user" else "venue"
        )
    db.commit()
    return {"ok": True, "visibility": app.visibility}


def _principal_label(db: Session, share) -> str:
    """A human name for a share row — who this grant actually reaches."""
    from app.db.models import Organization, User as UserModel, Venue

    if share.principal_type == "user":
        u = db.query(UserModel).filter(UserModel.id == share.principal_id).first()
        return (u.full_name or u.email) if u else "a removed user"
    if share.principal_type == "venue":
        v = db.query(Venue).filter(Venue.id == share.principal_id).first()
        return f"everyone at {v.name}" if v else "a removed venue"
    org = db.query(Organization).filter(Organization.id == share.principal_id).first()
    return f"everyone at {org.name}" if org else "the whole organization"


@router.get("/apps/{slug}/shares")
async def list_shares(
    slug: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Who can run this app. Owners/editors only — the audience list is itself
    information about the org."""
    from app.db.models import AppShare

    app, version = _load(db, slug, user)
    access = resolve_access(db, app, user)
    if access.role not in ("owner", "edit"):
        raise HTTPException(403, "only the author can see who this app is shared with")

    spec = version.spec or {}
    writes = [
        f"{e.get('connector')}.{e.get('action')}"
        for e in spec.get("writes") or []
        if isinstance(e, dict)
    ]
    return {
        "visibility": app.visibility,
        "writes": writes,
        "reach": describe_reach(spec),
        "shares": [
            {
                "id": s.id,
                "principal_type": s.principal_type,
                "principal_id": s.principal_id,
                "label": _principal_label(db, s),
                "access": s.access,
                "write_actions_approved": s.write_actions_approved,
            }
            for s in db.query(AppShare).filter(AppShare.app_id == app.id).all()
        ],
    }


@router.get("/apps/{slug}/share-candidates")
async def share_candidates(
    slug: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("apps:share")),
):
    """Who a share COULD name: the app's org members and venues, plus the
    org-wide option. Served per app so the web page needs no org plumbing."""
    from app.db.models import (
        Organization,
        OrganizationMembership,
        User as UserModel,
        Venue,
    )

    app, _ = _load(db, slug, user)
    access = resolve_access(db, app, user)
    if access.role not in ("owner", "edit"):
        raise HTTPException(403, "only the author can share this app")

    members = []
    for m in (
        db.query(OrganizationMembership)
        .filter(OrganizationMembership.organization_id == app.organization_id)
        .all()
    ):
        u = db.query(UserModel).filter(UserModel.id == m.user_id).first()
        if u and u.id != user.id:
            members.append({"id": u.id, "label": u.full_name or u.email})
    venues = [
        {"id": v.id, "label": v.name}
        for v in db.query(Venue)
        .filter(Venue.organization_id == app.organization_id)
        .order_by(Venue.name)
        .all()
    ]
    org = db.query(Organization).filter(Organization.id == app.organization_id).first()
    return {
        "users": members,
        "venues": venues,
        "organization": {
            "id": app.organization_id,
            "label": org.name if org else "organization",
        },
    }


@router.delete("/apps/{slug}/share/{share_id}")
async def revoke_share(
    slug: str,
    share_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("apps:share")),
):
    """Take a grant back. Visibility narrows to private when the last share
    goes — an app with no shares IS private, and the label should say so."""
    from app.db.models import AppShare

    app, _ = _load(db, slug, user)
    access = resolve_access(db, app, user)
    if access.role not in ("owner", "edit"):
        raise HTTPException(403, "only the author can share this app")

    row = (
        db.query(AppShare)
        .filter(AppShare.app_id == app.id, AppShare.id == share_id)
        .first()
    )
    if not row:
        raise HTTPException(404, "share not found")
    db.delete(row)
    db.flush()
    remaining = db.query(AppShare).filter(AppShare.app_id == app.id).all()
    if not remaining:
        app.visibility = "private"
    elif not any(s.principal_type == "organization" for s in remaining):
        app.visibility = (
            "users" if any(s.principal_type == "user" for s in remaining) else "venue"
        )
    db.commit()
    return {"ok": True, "visibility": app.visibility}
