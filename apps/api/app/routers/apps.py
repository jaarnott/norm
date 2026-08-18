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

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
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

    # Pins are a per-user nav preference, not a property of the app — stored
    # on the user's own preferences blob under a reserved key.
    pinned = set((user.dashboard_preferences or {}).get("_pinned_apps") or [])

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
                "pinned": app.slug in pinned,
                # Where this app's pages live. Defaulted here rather than in
                # the column so existing apps keep their old home.
                "agent": app.agent or "app_builder",
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
        "agent": app.agent or "app_builder",
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
    #: Which agent's menu this app's pages join. Omit for the App Builder.
    agent: str | None = None
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


class PinRequest(BaseModel):
    pinned: bool


@router.post("/apps/{slug}/pin")
async def pin_app(
    slug: str,
    body: PinRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Pin (or unpin) an app to this user's nav.

    A pin is the viewer's own shortcut, not a share: anyone who can RUN the
    app may pin it, and the pin lives on their preferences — the same blob the
    dashboard picker uses — so it follows them across devices and nobody
    else's nav moves.
    """
    from sqlalchemy.orm.attributes import flag_modified

    app, _ = _load(db, slug, user)
    if not resolve_access(db, app, user).can_run:
        raise HTTPException(404, "app not found")

    prefs = dict(user.dashboard_preferences or {})
    pins = [s for s in (prefs.get("_pinned_apps") or []) if isinstance(s, str)]
    if body.pinned and app.slug not in pins:
        pins.append(app.slug)
    if not body.pinned:
        pins = [s for s in pins if s != app.slug]
    prefs["_pinned_apps"] = pins
    user.dashboard_preferences = prefs
    flag_modified(user, "dashboard_preferences")
    db.commit()
    return {"pinned": app.slug in pins}


# ---------------------------------------------------------------------------
# Storage — an app's own rows.
#
# Thin like everything else here: `app_runtime.store_*` holds the rules, these
# endpoints resolve the app and hand over. Note the collection is a PATH
# segment, so an app that never declared it is refused by name rather than
# quietly returning nothing.
# ---------------------------------------------------------------------------


class RecordQuery(BaseModel):
    where: dict | None = None
    venue_id: str | None = None
    include_global: bool = True
    order_by: str | None = None
    descending: bool = False
    limit: int = 200
    offset: int = 0
    #: Ask for the count instead of the rows — so a caller does not have to
    #: fetch a whole collection to say how big it is.
    count_only: bool = False


class RecordBody(BaseModel):
    data: dict
    venue_id: str | None = None


@router.post("/apps/{slug}/records/{collection}/query")
async def app_records_query(
    slug: str,
    collection: str,
    body: RecordQuery,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """A POST because a query carries a body, not because it changes anything —
    the door audits it as a read."""
    from app.services.app_runtime import store_count, store_list

    app, version = _load(db, slug, user)
    if body.count_only:
        return {
            "count": store_count(
                db,
                app=app,
                version=version,
                user=user,
                collection=collection,
                where=body.where,
                venue_id=body.venue_id,
                include_global=body.include_global,
            )
        }
    return {
        "records": store_list(
            db,
            app=app,
            version=version,
            user=user,
            collection=collection,
            where=body.where,
            venue_id=body.venue_id,
            include_global=body.include_global,
            order_by=body.order_by,
            descending=body.descending,
            limit=body.limit,
            offset=body.offset,
        )
    }


@router.get("/apps/{slug}/records/{collection}/{record_id}")
async def app_record_get(
    slug: str,
    collection: str,
    record_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.services.app_runtime import store_get

    app, version = _load(db, slug, user)
    return store_get(
        db,
        app=app,
        version=version,
        user=user,
        collection=collection,
        record_id=record_id,
    )


@router.post("/apps/{slug}/records/{collection}")
async def app_record_create(
    slug: str,
    collection: str,
    body: RecordBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.services.app_runtime import store_put

    app, version = _load(db, slug, user)
    out = store_put(
        db,
        app=app,
        version=version,
        user=user,
        collection=collection,
        data=body.data,
        venue_id=body.venue_id,
    )
    db.commit()
    return out


@router.put("/apps/{slug}/records/{collection}/{record_id}")
async def app_record_update(
    slug: str,
    collection: str,
    record_id: str,
    body: RecordBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.services.app_runtime import store_put

    app, version = _load(db, slug, user)
    out = store_put(
        db,
        app=app,
        version=version,
        user=user,
        collection=collection,
        record_id=record_id,
        data=body.data,
        venue_id=body.venue_id,
    )
    db.commit()
    return out


@router.delete("/apps/{slug}/records/{collection}/{record_id}")
async def app_record_delete(
    slug: str,
    collection: str,
    record_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.services.app_runtime import store_delete

    app, version = _load(db, slug, user)
    out = store_delete(
        db,
        app=app,
        version=version,
        user=user,
        collection=collection,
        record_id=record_id,
    )
    db.commit()
    return out


# ---------------------------------------------------------------------------
# Files — bytes an app owns.
#
# Deliberately NOT a public URL. Orbit's evidence lived in a world-readable
# bucket, so anyone who ever saw a link kept access forever; here every fetch
# re-runs the same guard the record itself gets.
# ---------------------------------------------------------------------------


@router.post("/apps/{slug}/files/{collection}")
async def app_file_upload(
    slug: str,
    collection: str,
    file: UploadFile = File(...),
    record_id: str | None = Form(None),
    venue_id: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.services.app_runtime import file_put

    app, version = _load(db, slug, user)
    out = file_put(
        db,
        app=app,
        version=version,
        user=user,
        collection=collection,
        record_id=record_id,
        filename=file.filename or "file",
        content_type=file.content_type,
        data=await file.read(),
        venue_id=venue_id,
    )
    db.commit()
    return out


@router.get("/apps/{slug}/files/{collection}")
async def app_file_list(
    slug: str,
    collection: str,
    record_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.services.app_runtime import file_list

    app, version = _load(db, slug, user)
    return {
        "files": file_list(
            db,
            app=app,
            version=version,
            user=user,
            collection=collection,
            record_id=record_id,
        )
    }


@router.get("/apps/{slug}/file/{file_id}")
async def app_file_download(
    slug: str,
    file_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.services.app_runtime import file_fetch

    app, version = _load(db, slug, user)
    row = file_fetch(db, app=app, version=version, user=user, file_id=file_id)
    db.commit()
    return Response(
        content=row.data,
        media_type=row.content_type or "application/octet-stream",
        headers={
            # `inline` so a photo of a signed checklist opens rather than
            # downloads; the filename is quoted because staff name files freely.
            "Content-Disposition": f'inline; filename="{row.filename}"',
            "Cache-Control": "private, no-store",
        },
    )


@router.delete("/apps/{slug}/file/{file_id}")
async def app_file_delete(
    slug: str,
    file_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.services.app_runtime import file_delete

    app, version = _load(db, slug, user)
    out = file_delete(db, app=app, version=version, user=user, file_id=file_id)
    db.commit()
    return out
