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
from app.services import active_org

logger = logging.getLogger(__name__)
router = APIRouter()


def _app_key(app) -> str:
    """The name a role's ``apps`` list reaches this app by
    (services/role_access.allows_app): a built-in by its slug (``training``),
    anything else as ``custom:<slug>``."""
    from app.services.builtin_apps import is_builtin
    from app.services.entitlements import CUSTOM_PREFIX

    return app.slug if is_builtin(app) else f"{CUSTOM_PREFIX}{app.slug}"


def _role_allows(db: Session, app, user: User) -> bool:
    """Whether the viewer's role reaches this app: named in the role's apps
    list, or belonging to one of the role's AI agents. Platform admins and
    roles with no limits pass (services/role_access.py)."""
    from app.services import role_access
    from app.services.entitlements import custom_app_owner

    if user.role == "admin":
        return True
    access = role_access.for_user(db, user.id)
    return role_access.allows_app(access, _app_key(app), member=custom_app_owner(app))


def _load(db: Session, slug: str, user: User, config_db: Session):
    """Resolve an app + the version to run: 404 when there is no such app for
    this viewer, 403 when there is but their role doesn't reach it (staff
    access, docs/plans/staff-access.md section 11.4)."""
    app, version = _resolve(db, slug, user, config_db)
    if not _role_allows(db, app, user):
        raise HTTPException(403, "Your role doesn't include this app.")
    return app, version


def _resolve(db: Session, slug: str, user: User, config_db: Session):
    """Resolve an app + the version to run, or 404.

    A viewer always runs the app's CURRENT version. Sharing pins a version by
    setting ``current_version_id``, so the author editing a draft never changes
    what anyone else is running.

    An app built into Norm (Norm Hiring, Norm Training) resolves first: its
    code comes from the repo, bound to the viewer's own org, and only while
    the org has its catalog App on (services/builtin_apps.py).
    """
    from app.db.models import App, AppVersion
    from app.services import page_templates
    from app.services.builtin_apps import load_for_user

    builtin = load_for_user(db, config_db, slug, user)
    if builtin is not None:
        return builtin
    # One of Norm's ready-made dashboards, bound to the viewer's own org
    # (services/page_templates.py).
    template = page_templates.load_for_user(db, config_db, slug, user)
    if template is not None:
        return template

    membership = active_org.membership_for(db, user.id)
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


def _content_disposition(filename: str | None) -> str:
    from urllib.parse import quote

    name = filename or "file"
    ascii_name = (
        name.encode("ascii", "replace")
        .decode("ascii")
        .replace('"', "'")
        .replace("\\", "_")
    )
    return f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"


def _refuse_builtin(app) -> None:
    """Sharing is how a user-built app reaches people. A built-in app already
    reaches everyone in the org, and has no row a share could point at."""
    from app.services.builtin_apps import is_builtin
    from app.services.page_templates import is_template

    if is_builtin(app):
        raise HTTPException(
            400,
            f"{app.name} is built into Norm — everyone in your organization "
            "already has it",
        )
    if is_template(app):
        raise HTTPException(
            400,
            f"{app.name} is one of Norm's templates — everyone already has it. "
            "Make a copy to change or share your own.",
        )


class CallRequest(BaseModel):
    connector: str
    action: str
    params: dict | None = None
    venue_id: str | None = None


# These routes are plain `def`, not `async def`: their work blocks (database,
# and a connector call to Loaded that can take many seconds). FastAPI runs a
# `def` route on its own thread; an `async def` one runs on the process's event
# loop, so one slow call froze every other request on that process. A dashboard
# fanning out twelve such calls left the Change dashboard list waiting, then
# failing with 503s (22 Oct 2026). Same lesson as the dojo endpoints (Aug 2026).
@router.post("/apps/{slug}/call")
def app_call(
    slug: str,
    body: CallRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """The data door. Everything an app reads or writes comes through here."""
    app, version = _load(db, slug, user, config_db)
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
def app_run(
    slug: str,
    body: RunRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Run the app's server-side logic in the consolidator sandbox."""
    app, version = _load(db, slug, user, config_db)
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
        # Nothing persisted — the storage door only flushes, and a failed run
        # must leave no half-written rows behind.
        db.rollback()
        raise HTTPException(400, out.get("error") or "the app's logic failed")
    # An op may have written through `store` inside run(); the door flushes but
    # does not commit, so the endpoint is what makes those writes durable. A
    # read-only run commits nothing and is unharmed.
    db.commit()
    return {"data": out.get("data"), "logs": out.get("_logs") or []}


@router.get("/apps")
def list_apps(
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Every app this user may run — the apps built into Norm that their org
    has on, then theirs, plus whatever is shared with them."""
    from app.db.models import App

    membership = active_org.membership_for(db, user.id)
    if not membership:
        return {"apps": []}

    # Pins are a per-user nav preference, not a property of the app — stored
    # on the user's own preferences blob under a reserved key.
    pinned = set((user.dashboard_preferences or {}).get("_pinned_apps") or [])

    from app.services import role_access
    from app.services.builtin_apps import builtin_apps, is_on

    # The viewer's role narrows the list per person (staff access): an app
    # the role doesn't name, and whose AI agent the role doesn't have, is
    # left out. Platform admins and roles with no limits see everything.
    access = (
        role_access.EVERYTHING
        if user.role == "admin"
        else role_access.for_user(db, user.id)
    )

    out = []
    for b in builtin_apps().values():
        if not is_on(b.slug, membership.organization_id, db, config_db):
            continue
        if not role_access.allows_app(access, b.slug, member=b.agent):
            continue
        out.append(
            {
                "slug": b.slug,
                "name": b.name,
                "description": b.description,
                "icon": b.icon,
                "visibility": "builtin",
                "builtin": True,
                "mine": False,
                "access": "view",
                "pinned": b.slug in pinned,
                "agent": b.agent or "app_builder",
                "kind": "app",
                "audience": b.audience,
            }
        )
    for app in (
        db.query(App)
        .filter(
            App.organization_id == membership.organization_id,
            App.archived_at.is_(None),
        )
        .order_by(App.name)
        .all()
    ):
        if app.slug in builtin_apps():
            continue  # a leftover per-org copy; the built-in is listed above
        share = resolve_access(db, app, user)
        if not share.can_run:
            continue
        if not role_access.allows_app(
            access, _app_key(app), member=app.agent or "app_builder"
        ):
            continue
        out.append(
            {
                "slug": app.slug,
                "name": app.name,
                "description": app.description,
                "icon": app.icon,
                "visibility": app.visibility,
                "kind": app.kind,
                "mine": app.created_by == user.id,
                "access": share.role,
                "pinned": app.slug in pinned,
                # Where this app's pages live. Defaulted here rather than in
                # the column so existing apps keep their old home.
                "agent": app.agent or "app_builder",
                # Where it shows: under its agent, in the apps section, or both.
                "audience": app.audience or "agent",
            }
        )
    return {"apps": out}


@router.get("/apps/{slug}")
def get_app(
    slug: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """One app, with the version to render and its reach in plain language.

    ``reach`` and ``missing_permissions`` are what the runtime shows BEFORE it
    renders anything: the consent text is the same vocabulary the Claude
    connector uses, and a viewer who cannot do what the app does is told which
    permission is missing rather than watching every call fail.
    """
    from app.services.app_runtime import owner_name

    app, version = _load(db, slug, user, config_db)
    access = resolve_access(db, app, user)
    if not access.can_run:
        raise HTTPException(404, "app not found")
    spec = version.spec or {}
    template = bool(getattr(app, "template", False))
    return {
        "slug": app.slug,
        "name": app.name,
        "description": app.description,
        "icon": app.icon,
        "agent": app.agent or "app_builder",
        "audience": getattr(app, "audience", None) or "agent",
        "purpose": app.purpose,
        "visibility": app.visibility,
        # app | dashboard; one of Norm's templates is a dashboard anyone uses
        "kind": getattr(app, "kind", "app"),
        "template": template,
        "owner": (
            "Norm"
            if template or getattr(app, "builtin", False)
            else ("you" if app.created_by == user.id else owner_name(db, app))
        ),
        "builtin": bool(getattr(app, "builtin", False)),
        # a built-in's version is its code's hash — it changes with Norm
        "build": getattr(version, "build", None),
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
    #: Required for a new page; a change to an existing one may leave out
    #: whatever it doesn't change (app_runtime.save_app carries it over).
    name: str | None = None
    slug: str | None = None
    description: str | None = None
    icon: str | None = None
    #: The AI agent whose Dashboard page or menu it joins (required when new).
    agent: str | None = None
    #: Where it shows: agent (under its agent, as before) | team (the apps
    #: section a role's apps list reaches) | both. Kept when not sent.
    audience: str | None = None
    #: dashboard | app (required when new); changes only with change_kind.
    kind: str | None = None
    change_kind: bool | None = None
    purpose: str | None = None
    spec: dict | None = None
    ui_source: str | None = None
    logic_source: str | None = None
    #: [{find, replace}] instead of the whole ui_source / logic_source.
    edits: list | None = None
    logic_edits: list | None = None
    changelog: str | None = None


@router.post("/apps")
def save_app_endpoint(
    body: SaveRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("apps:build")),
):
    """Create an app or add a version — the shared implementation lives in
    ``app_runtime.save_app`` so this endpoint and the builder agent's tool can
    never drift."""
    from app.services.app_runtime import save_app

    # Only what was sent: an unsent ui_source must carry over, never be wiped.
    payload = body.model_dump(exclude_unset=True)
    audience = payload.pop("audience", None)
    if audience is not None:
        audience = _valid_audience(audience)
    out = save_app(db, user, payload)
    if audience is not None:
        _set_audience(db, out.get("slug") or body.slug, user, audience)
    return out


def _valid_audience(value) -> str:
    from app.services.builtin_apps import AUDIENCES

    v = str(value or "").strip().lower()
    if v not in AUDIENCES:
        raise HTTPException(400, "audience must be agent, team or both")
    return v


def _set_audience(db: Session, slug: str | None, user: User, audience: str) -> None:
    """Record where an org-built app shows. The caller has just saved it (or
    holds edit access, checked by the PUT below)."""
    from app.db.models import App

    membership = active_org.membership_for(db, user.id)
    if not membership or not slug:
        return
    app = (
        db.query(App)
        .filter(App.slug == slug, App.organization_id == membership.organization_id)
        .first()
    )
    if app is not None and app.audience != audience:
        app.audience = audience
        db.commit()


class AppSettingsRequest(BaseModel):
    audience: str | None = None


@router.put("/apps/{slug}")
def update_app_settings(
    slug: str,
    body: AppSettingsRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(require_permission("apps:build")),
):
    """An app's settings that are not a new version of its code: for now,
    ``audience`` — whether it shows under its agent, in the apps section a
    role's apps list reaches, or both."""
    app, _ = _load(db, slug, user, config_db)
    _refuse_builtin(app)
    if resolve_access(db, app, user).role not in ("owner", "edit"):
        raise HTTPException(403, "you can't change this app's settings")
    if body.audience is not None:
        app.audience = _valid_audience(body.audience)
    db.commit()
    return {"slug": app.slug, "audience": app.audience or "agent"}


class HandEditRequest(BaseModel):
    ui_source: str
    #: The version the person started editing. A newer one (Norm saved while
    #: they edited) is never overwritten: 409, and they edit again on top.
    base_version: int
    note: str | None = None


@router.post("/apps/{slug}/edit")
def save_hand_edit(
    slug: str,
    body: HandEditRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(require_permission("apps:build")),
):
    """A person's own edit of an app's page (text retyped, sections moved),
    saved as a new version. Only the page changes: the version's declared
    reach and its logic are carried over as they are, so an edit by hand can
    never widen what the app may do. Norm starts its next change from it."""
    from app.db.models import AppVersion

    app, version = _load(db, slug, user, config_db)
    if getattr(app, "builtin", False):
        raise HTTPException(403, "Norm's own apps can't be edited")
    if resolve_access(db, app, user).role not in ("owner", "edit"):
        raise HTTPException(403, "you can't edit this app")
    # As for Norm's save: the page runs as whoever opens it, so it is written
    # only by someone who could declare the app's reach themselves.
    missing = required_permissions(version.spec or {}) - org_permissions(db, user)
    if missing:
        raise HTTPException(
            403,
            "this app can do more than you can yourself — missing: "
            + ", ".join(sorted(missing)),
        )
    if version.version != body.base_version:
        raise HTTPException(
            409,
            f"Version {version.version} was saved while you were editing "
            f"version {body.base_version}.",
        )
    last = (
        db.query(AppVersion)
        .filter(AppVersion.app_id == app.id)
        .order_by(AppVersion.version.desc())
        .first()
    )
    edited = AppVersion(
        app_id=app.id,
        version=(last.version if last else 0) + 1,
        spec=version.spec,
        ui_source=body.ui_source,
        logic_source=version.logic_source,
        changelog=(body.note or "Edited by hand")[:2000],
        created_by=user.id,
    )
    db.add(edited)
    db.flush()
    app.current_version_id = edited.id
    db.commit()
    return {"slug": app.slug, "version": edited.version}


class ShareRequest(BaseModel):
    principal_type: str  # user | venue | organization
    principal_id: str
    access: str = "view"
    approve_writes: bool = False


@router.post("/apps/{slug}/share")
def share_app(
    slug: str,
    body: ShareRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(require_permission("apps:share")),
):
    """Widen an app's audience — and, separately, approve its writes for them.

    Approving writes requires holding every permission the app's write actions
    need. Sharing is how an app reaches someone else's data; the person doing it
    must be able to do those things themselves.
    """
    from app.db.models import AppShare

    app, version = _load(db, slug, user, config_db)
    _refuse_builtin(app)
    if getattr(app, "kind", "app") == "dashboard":
        raise HTTPException(
            400,
            "a dashboard is shared with the whole organisation, to use as it "
            "is: use Share on the dashboard (POST /api/dashboards/share)",
        )
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
def list_shares(
    slug: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Who can run this app. Owners/editors only — the audience list is itself
    information about the org."""
    from app.db.models import AppShare

    app, version = _load(db, slug, user, config_db)
    _refuse_builtin(app)
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
def share_candidates(
    slug: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
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

    app, _ = _load(db, slug, user, config_db)
    _refuse_builtin(app)
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
def revoke_share(
    slug: str,
    share_id: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(require_permission("apps:share")),
):
    """Take a grant back. Visibility narrows to private when the last share
    goes — an app with no shares IS private, and the label should say so."""
    from app.db.models import AppShare

    app, _ = _load(db, slug, user, config_db)
    _refuse_builtin(app)
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


@router.get("/apps/{slug}/versions")
def app_versions(
    slug: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Every saved version, newest first — for the person who can bring one
    back (an App's owner and editors; a Dashboard's owner)."""
    from app.services.app_runtime import list_versions

    app, _ = _load(db, slug, user, config_db)
    _refuse_builtin(app)
    access = resolve_access(db, app, user)
    if access.role not in ("owner", "edit"):
        raise HTTPException(403, "only the person who made it can see its history")
    return {"versions": list_versions(db, app)}


@router.post("/apps/{slug}/versions/{number}/restore")
def restore_app_version(
    slug: str,
    number: int,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Bring back an old version as the newest one (nothing is overwritten)."""
    from app.services.app_runtime import restore_version

    app, _ = _load(db, slug, user, config_db)
    _refuse_builtin(app)
    return restore_version(db, app, user, number, resolve_access(db, app, user))


class MineRequest(BaseModel):
    #: Names to set; a null value removes that one.
    data: dict


@router.get("/apps/{slug}/mine")
def get_mine(
    slug: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """This person's own settings on this page (norm.mine) — nobody else's."""
    from app.services.app_runtime import mine_get

    app, _ = _load(db, slug, user, config_db)
    if not resolve_access(db, app, user).can_run:
        raise HTTPException(404, "app not found")
    return {"data": mine_get(db, app, user)}


@router.put("/apps/{slug}/mine")
def put_mine(
    slug: str,
    body: MineRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Change this person's own settings on this page. Anyone who can use the
    page may — it changes only their own screen, so it needs no approval."""
    from app.services.app_runtime import mine_set

    app, _ = _load(db, slug, user, config_db)
    if not resolve_access(db, app, user).can_run:
        raise HTTPException(404, "app not found")
    data = mine_set(db, app, user, body.data)
    db.commit()
    return {"data": data}


@router.post("/apps/{slug}/pin")
def pin_app(
    slug: str,
    body: PinRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Pin (or unpin) an app to this user's nav.

    A pin is the viewer's own shortcut, not a share: anyone who can RUN the
    app may pin it, and the pin lives on their preferences — the same blob the
    dashboard picker uses — so it follows them across devices and nobody
    else's nav moves.
    """
    from sqlalchemy.orm.attributes import flag_modified

    app, _ = _load(db, slug, user, config_db)
    if not resolve_access(db, app, user).can_run:
        raise HTTPException(404, "app not found")
    if body.pinned and getattr(app, "kind", "app") == "dashboard":
        raise HTTPException(
            400,
            f"'{app.name}' is a dashboard: it sits on the "
            "AI agent's Dashboard page — pick it there with Change dashboard",
        )

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
def app_records_query(
    slug: str,
    collection: str,
    body: RecordQuery,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """A POST because a query carries a body, not because it changes anything —
    the door audits it as a read."""
    from app.services.app_runtime import store_count, store_list

    app, version = _load(db, slug, user, config_db)
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
def app_record_get(
    slug: str,
    collection: str,
    record_id: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    from app.services.app_runtime import store_get

    app, version = _load(db, slug, user, config_db)
    return store_get(
        db,
        app=app,
        version=version,
        user=user,
        collection=collection,
        record_id=record_id,
    )


@router.post("/apps/{slug}/records/{collection}")
def app_record_create(
    slug: str,
    collection: str,
    body: RecordBody,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    from app.services.app_runtime import store_put

    app, version = _load(db, slug, user, config_db)
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
def app_record_update(
    slug: str,
    collection: str,
    record_id: str,
    body: RecordBody,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    from app.services.app_runtime import store_put

    app, version = _load(db, slug, user, config_db)
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
def app_record_delete(
    slug: str,
    collection: str,
    record_id: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    from app.services.app_runtime import store_delete

    app, version = _load(db, slug, user, config_db)
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
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    from app.services.app_runtime import file_put

    app, version = _load(db, slug, user, config_db)
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
def app_file_list(
    slug: str,
    collection: str,
    record_id: str | None = None,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    from app.services.app_runtime import file_list

    app, version = _load(db, slug, user, config_db)
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
def app_file_download(
    slug: str,
    file_id: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    from app.services.app_runtime import file_fetch

    app, version = _load(db, slug, user, config_db)
    row = file_fetch(db, app=app, version=version, user=user, file_id=file_id)
    db.commit()
    return Response(
        content=row.data,
        media_type=row.content_type or "application/octet-stream",
        headers={
            # `inline` so a photo of a signed checklist opens rather than
            # downloads. Staff name files freely (Māori names, quotes): an
            # ASCII fallback plus the RFC 5987 UTF-8 form — a raw non-Latin-1
            # name in a header is a 500.
            "Content-Disposition": _content_disposition(row.filename),
            "Cache-Control": "private, no-store",
        },
    )


@router.delete("/apps/{slug}/file/{file_id}")
def app_file_delete(
    slug: str,
    file_id: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    from app.services.app_runtime import file_delete

    app, version = _load(db, slug, user, config_db)
    out = file_delete(db, app=app, version=version, user=user, file_id=file_id)
    db.commit()
    return out
