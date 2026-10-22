"""What each AI agent's Dashboard page shows (docs/plans/apps-and-dashboards-one-system.md).

A dashboard is a page of code: an app of kind ``dashboard`` in the person's
organisation, or one of Norm's templates (services/page_templates.py). Pages
and people refer to one as a *ref*: ``app:<slug>`` or ``template:<slug>``.

The rules, as the owner set them (22 Oct 2026):

* **Your own** dashboards are yours: nobody else sees them until you share.
* **Shared with the organisation:** everyone in it can use it as it is,
  without copying it, so they see the sharer's changes. Only the person who
  shared it changes it; anyone else can make a copy, which is theirs.
* **Templates** are the same: used as they are, so an improved template
  reaches everyone using it. A copy is only for changing it.

Each person's choice per Dashboard page and organisation lives in
``User.dashboard_preferences[PREF_KEY][org][agent]`` — a separate key from the
tile dashboards' ``by_org`` choices, so the two never fight while both exist.
"""

from __future__ import annotations

import copy as _copy
import datetime as dt
import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.services import active_org, page_templates

PREF_KEY = "dashboard_pages"


def _org(db: Session, user) -> str | None:
    return active_org.org_id_for(db, user.id)


def _dashboards(db: Session, org: str, agent: str | None = None):
    from app.db.models import App

    q = db.query(App).filter(
        App.organization_id == org,
        App.kind == "dashboard",
        App.archived_at.is_(None),
    )
    if agent:
        q = q.filter(App.agent == agent)
    return q.order_by(App.updated_at.desc())


def own(db: Session, user, org: str, agent: str | None = None) -> list:
    return [a for a in _dashboards(db, org, agent) if a.created_by == user.id]


def shared_with(db: Session, user, org: str, agent: str | None = None) -> list:
    from app.services.app_runtime import resolve_access

    return [
        a
        for a in _dashboards(db, org, agent)
        if a.created_by != user.id and resolve_access(db, a, user).can_run
    ]


def _is_shared(db: Session, app) -> bool:
    from app.db.models import AppShare

    return (
        db.query(AppShare)
        .filter(
            AppShare.app_id == app.id,
            AppShare.principal_type == "organization",
            AppShare.principal_id == app.organization_id,
        )
        .first()
        is not None
    )


def describe_app(db: Session, app, user) -> dict:
    from app.services.app_runtime import current_version, owner_name

    v = current_version(db, app)
    mine = app.created_by == user.id
    return {
        "ref": f"app:{app.slug}",
        "slug": app.slug,
        "name": app.name,
        "description": app.description,
        "icon": app.icon,
        "agent": app.agent,
        "source": "mine" if mine else "shared",
        "owner": "you" if mine else owner_name(db, app),
        "shared": _is_shared(db, app),
        "can_change": mine,
        "version": v.version if v else None,
    }


def describe_template(t) -> dict:
    return {
        "ref": f"{page_templates.PREFIX}{t.slug}",
        "slug": f"{page_templates.PREFIX}{t.slug}",
        "name": t.name,
        "description": t.description,
        "icon": t.icon,
        "agent": t.agent_slug,
        "source": "template",
        "owner": "Norm",
        "shared": False,
        "can_change": False,
        "version": t.version,
    }


def resolve(db: Session, config_db, user, ref: str | None, agent: str | None = None):
    """A ref the person may use here, described — or None. ``agent`` limits
    it to that AI agent's Dashboard page."""
    from app.db.models import App
    from app.services.app_runtime import resolve_access

    ref = str(ref or "")
    org = _org(db, user)
    if not org:
        return None
    if ref.startswith(page_templates.PREFIX):
        t = page_templates.get(config_db, ref)
        if t is None or (agent and t.agent_slug != agent):
            return None
        return describe_template(t)
    if ref.startswith("app:"):
        app = (
            db.query(App)
            .filter(
                App.organization_id == org,
                App.slug == ref[4:],
                App.kind == "dashboard",
                App.archived_at.is_(None),
            )
            .first()
        )
        if app is None or (agent and app.agent != agent):
            return None
        if not resolve_access(db, app, user).can_run:
            return None
        return describe_app(db, app, user)
    return None


def _choices(user, org: str) -> dict:
    prefs = user.dashboard_preferences or {}
    return dict(((prefs.get(PREF_KEY) or {}).get(org) or {}))


def _choose(db: Session, user, org: str, agent: str, ref: str | None) -> None:
    prefs = _copy.deepcopy(dict(user.dashboard_preferences or {}))
    pages = dict(prefs.get(PREF_KEY) or {})
    mine = dict(pages.get(org) or {})
    if ref:
        mine[agent] = ref
    else:
        mine.pop(agent, None)
    pages[org] = mine
    prefs[PREF_KEY] = pages
    user.dashboard_preferences = prefs
    flag_modified(user, "dashboard_preferences")


def available(db: Session, config_db, user, agent: str) -> dict:
    org = _org(db, user)
    if not org:
        return {"mine": [], "shared": [], "templates": [], "chosen": None}
    return {
        "mine": [describe_app(db, a, user) for a in own(db, user, org, agent)],
        "shared": [
            describe_app(db, a, user) for a in shared_with(db, user, org, agent)
        ],
        "templates": [
            describe_template(t) for t in page_templates.for_agent(config_db, agent)
        ],
        "chosen": (shown(db, config_db, user, agent) or {}).get("ref"),
    }


def shown(db: Session, config_db, user, agent: str) -> dict | None:
    """The dashboard this person's ``agent`` Dashboard page shows: their
    choice while they can still use it, else their newest own one, else the
    template published first. None means: let them pick."""
    org = _org(db, user)
    if not org:
        return None
    picked = resolve(db, config_db, user, _choices(user, org).get(agent), agent)
    if picked:
        return picked
    mine = own(db, user, org, agent)
    if mine:
        return describe_app(db, mine[0], user)
    templates = page_templates.for_agent(config_db, agent)
    if templates:
        # Several (Reports: Operations and Group performance): the one
        # published first is the page's own, so nobody meets a chooser.
        first = min(
            templates, key=lambda t: (t.created_at is None, t.created_at, t.slug)
        )
        return describe_template(first)
    return None


def has_any(db: Session, config_db, user, agent: str) -> bool:
    """Whether this page has anything of the new kind to show. Until it does,
    the page keeps showing the old tile dashboard."""
    org = _org(db, user)
    if not org:
        return False
    return bool(
        page_templates.for_agent(config_db, agent)
        or own(db, user, org, agent)
        or shared_with(db, user, org, agent)
    )


def use(db: Session, config_db, user, agent: str, ref: str) -> dict:
    org = _org(db, user)
    picked = resolve(db, config_db, user, ref, agent)
    if not org or not picked:
        raise HTTPException(404, "no such dashboard for that page")
    _choose(db, user, org, agent, picked["ref"])
    db.commit()
    return picked


def _owned(db: Session, user, slug: str):
    from app.db.models import App

    org = _org(db, user)
    app = (
        db.query(App)
        .filter(
            App.organization_id == org,
            App.slug == slug,
            App.kind == "dashboard",
            App.archived_at.is_(None),
        )
        .first()
        if org
        else None
    )
    if app is None:
        raise HTTPException(404, "no such dashboard here")
    if app.created_by != user.id:
        from app.services.app_runtime import not_yours

        raise HTTPException(403, not_yours(db, app))
    return app


def _free_slug(db: Session, org: str, base: str) -> str:
    from app.db.models import App

    base = base[:60].strip("-") or "dashboard"
    taken = {
        s
        for (s,) in db.query(App.slug)
        .filter(App.organization_id == org, App.slug.like(f"{base}%"))
        .all()
    }
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def copy(
    db: Session, config_db, user, ref: str, *, name: str | None = None, choose=True
) -> dict:
    """The person's own copy of a dashboard they can use (theirs, one shared
    with them, or a template), to change. It starts from the current version
    with no records — the original's stay with it — and their own settings on
    the original come with them."""
    from app.db.models import App, AppVersion
    from app.services.app_runtime import (
        current_version,
        mine_get,
        mine_set,
        org_permissions,
        required_permissions,
        resolve_access,
    )

    org = _org(db, user)
    source = resolve(db, config_db, user, ref)
    if not org or not source:
        raise HTTPException(404, "no such dashboard here")
    if "apps:build" not in org_permissions(db, user):
        raise HTTPException(
            403,
            "making your own copy of a dashboard needs the 'build apps' "
            "permission (apps:build)",
        )
    if source["source"] == "template":
        t = page_templates.get(config_db, ref)
        src_app, src_v = page_templates.bind(t, org)
        spec = _copy.deepcopy(t.spec or {})
    else:
        src_app = (
            db.query(App)
            .filter(App.organization_id == org, App.slug == source["slug"])
            .one()
        )
        if not resolve_access(db, src_app, user).can_run:
            raise HTTPException(404, "no such dashboard here")
        src_v = current_version(db, src_app)
        spec = _copy.deepcopy(src_v.spec or {})
    missing = required_permissions(spec) - org_permissions(db, user)
    if missing:
        raise HTTPException(
            403,
            "this dashboard can do more than you can yourself — missing: "
            + ", ".join(sorted(missing)),
        )
    base = page_templates.plain(source["slug"])
    slug = _free_slug(db, org, base)
    if spec.get("storage"):
        spec["storage"] = {
            **spec["storage"],
            "namespace": f"{slug.replace('-', '_')}_{uuid.uuid4().hex[:6]}",
        }
        spec["storage"].pop("shared_with", None)
    app = App(
        organization_id=org,
        created_by=user.id,
        slug=slug,
        name=(name or "").strip() or source["name"],
        description=source.get("description"),
        icon=source.get("icon"),
        agent=source["agent"],
        purpose=getattr(src_app, "purpose", None),
        visibility="private",
        kind="dashboard",
    )
    db.add(app)
    db.flush()
    v = AppVersion(
        app_id=app.id,
        version=1,
        spec=spec,
        ui_source=src_v.ui_source,
        logic_source=src_v.logic_source,
        changelog=f"Copied from {source['name']}, version {source['version']}",
        created_by=user.id,
    )
    db.add(v)
    db.flush()
    app.current_version_id = v.id
    theirs = mine_get(db, src_app, user)
    if theirs:
        mine_set(db, app, user, theirs)
    if choose:
        _choose(db, user, org, app.agent, f"app:{app.slug}")
    db.commit()
    return describe_app(db, app, user)


def set_shared(db: Session, user, slug: str, shared: bool, approve_writes=False):
    """Share a dashboard with the whole organisation (to use as it is), or
    stop. Only its owner can; a dashboard is never shared with named people,
    venues, or to change."""
    from app.db.models import AppShare
    from app.services.app_runtime import org_permissions, required_permissions

    app = _owned(db, user, slug)
    row = (
        db.query(AppShare)
        .filter(
            AppShare.app_id == app.id,
            AppShare.principal_type == "organization",
            AppShare.principal_id == app.organization_id,
        )
        .first()
    )
    if shared:
        if approve_writes:
            from app.services.app_runtime import current_version

            spec = current_version(db, app).spec or {}
            if required_permissions(spec) - org_permissions(db, user):
                raise HTTPException(403, "you can't approve what you can't do yourself")
        if row is None:
            row = AppShare(
                app_id=app.id,
                principal_type="organization",
                principal_id=app.organization_id,
                access="view",
                granted_by=user.id,
            )
            db.add(row)
        row.access = "view"
        row.write_actions_approved = bool(approve_writes)
        app.visibility = "organization"
    else:
        if row is not None:
            db.delete(row)
        app.visibility = "private"
    db.commit()
    return describe_app(db, app, user)


def remove(db: Session, user, slug: str) -> dict:
    """Archive the owner's dashboard. Anyone using it falls back to their next
    choice; its versions and records are kept."""
    app = _owned(db, user, slug)
    app.archived_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    return {"removed": f"app:{app.slug}", "name": app.name}


def make_template(db: Session, config_db, user, slug: str, template_slug: str):
    """A platform admin publishes their own dashboard as a template — or as
    the template's next version, which everyone using it gets."""
    from app.services.app_runtime import current_version

    if getattr(user, "role", None) != "admin":
        raise HTTPException(403, "only a platform admin can make a template")
    app = _owned(db, user, slug)
    v = current_version(db, app)
    template_slug = page_templates.plain(str(template_slug or "")).strip()
    import re

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", template_slug):
        raise HTTPException(
            400, "a template's name is lower-case letters, numbers and dashes"
        )
    t = page_templates.publish(
        config_db, slug=template_slug, app=app, version=v, user_id=user.id
    )
    config_db.commit()
    return describe_template(t)
