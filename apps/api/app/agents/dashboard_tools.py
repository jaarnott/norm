"""Norm's dashboard tools: which dashboard each Dashboard page shows (Oct 2026).

A dashboard is a page of code, built like any app with ``save_app`` (kind
``dashboard``), or one of Norm's templates. The tiles these tools used to
place are gone (docs/plans/apps-and-dashboards-one-system.md); what's left is
what a person does WITH dashboards, over the rules in services/dashboards.py:

* ``get_dashboards`` (read) says what each AI agent's Dashboard page shows the
  person and what else they can pick: their own, those shared with them, and
  Norm's templates — and who can change each. Given one ``ref`` it shows that
  dashboard in the conversation.
* ``manage_dashboard`` (write): ``use`` one as it is (no copy — they get its
  updates), ``copy`` one to change, ``share`` / ``unshare`` the person's own
  with the organisation, ``restore`` an earlier version, or ``remove`` one.
  Anything that changes what other people see on a shared dashboard shows a
  before-and-after card first (services/previews.py); the rest runs at once.

A dashboard is referred to as ``app:<slug>`` (the organisation's own) or
``template:<slug>`` (Norm's).
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.agents.internal_tools import _thread_user, previews, register
from app.services.app_runtime import DASHBOARD_AGENTS as AGENTS

OPS = ("use", "copy", "share", "unshare", "restore", "remove")
#: Ops that only ever change what the person themselves sees.
_PRIVATE_OPS = {"use", "copy", "share"}


class _Refused(Exception):
    """A change that can't be made as asked; the message goes to Norm."""


def _config_session():
    from app.db.engine import _ConfigSessionLocal

    return _ConfigSessionLocal()


def _own_app(db, user, ref_or_slug: str):
    """The person's own dashboard named by ``app:<slug>`` or a bare slug."""
    from app.db.models import App
    from app.services import active_org
    from app.services.app_runtime import not_yours

    slug = str(ref_or_slug or "").removeprefix("app:").strip()
    if not slug or slug.startswith("template:"):
        raise _Refused(
            "That's one of Norm's templates: nobody changes it. Offer the "
            "person a copy of their own (op 'copy')."
        )
    org = active_org.org_id_for(db, user.id)
    app = (
        db.query(App)
        .filter(
            App.organization_id == org,
            App.slug == slug,
            App.kind == "dashboard",
            App.archived_at.is_(None),
        )
        .first()
    )
    if app is None:
        raise _Refused(f"There's no dashboard '{slug}' here. Read get_dashboards.")
    if app.created_by != user.id:
        raise _Refused(not_yours(db, app))
    return app


def _shared(db, app) -> bool:
    from app.services.dashboards import _is_shared

    return _is_shared(db, app)


@register("norm_reports", "get_dashboards")
def _get_dashboards(params: dict, db: Session, thread_id: str | None) -> dict:
    from app.services import dashboards

    user = _thread_user(db, thread_id)
    if user is None:
        return {"success": False, "data": None, "error": "no user on this thread"}
    params = dict(params or {})
    cdb = _config_session()
    try:
        ref = str(params.get("ref") or "").strip()
        if ref:
            one = dashboards.resolve(db, cdb, user, ref)
            if one is None:
                return {
                    "success": False,
                    "data": None,
                    "error": f"no dashboard '{ref}' this person can use here",
                }
            # Shown in the conversation by the app_runner card.
            return {"success": True, "data": {**one, "slug": one["slug"]}}
        agent = str(params.get("agent") or "").strip()
        agents = [agent] if agent else list(AGENTS)
        if agent and agent not in AGENTS:
            return {
                "success": False,
                "data": None,
                "error": "agent is one of: " + ", ".join(AGENTS),
            }
        pages = []
        for a in agents:
            choices = dashboards.available(db, cdb, user, a)
            if not agent and not (
                choices["mine"] or choices["shared"] or choices["templates"]
            ):
                continue
            pages.append(
                {
                    "agent": a,
                    "shows": dashboards.shown(db, cdb, user, a),
                    "mine": choices["mine"],
                    "shared_with_them": choices["shared"],
                    "templates": choices["templates"],
                }
            )
        return {
            "success": True,
            "data": {
                "pages": pages,
                "rules": (
                    "The person can USE any of these as it is, without a copy "
                    "(op 'use'): they then get its updates. Only can_change ones "
                    "can be changed — with save_app, kind 'dashboard', the same "
                    "slug. To change a template or someone else's dashboard, ask "
                    "whether they'd like their own copy (op 'copy') first."
                ),
                "_no_display": True,
            },
        }
    finally:
        cdb.close()


def _plan(db, user, params: dict) -> dict:
    """Check a change and say what it does, without writing anything."""
    from app.services import dashboards
    from app.services.app_runtime import current_version

    op = str(params.get("op") or "")
    if op not in OPS:
        raise _Refused(f"op must be one of {', '.join(OPS)}.")
    if op in ("use", "copy"):
        cdb = _config_session()
        try:
            one = dashboards.resolve(
                db,
                cdb,
                user,
                str(params.get("ref") or ""),
                str(params.get("agent") or "") or None,
            )
        finally:
            cdb.close()
        if one is None:
            raise _Refused(
                "No dashboard with that ref that this person can use here "
                "(refs look like app:<slug> or template:<slug>; read get_dashboards)."
            )
        return {"op": op, "dashboard": one, "app": None, "lines": []}
    app = _own_app(db, user, str(params.get("ref") or params.get("slug") or ""))
    lines: list[dict] = []
    if op == "unshare":
        lines.append(
            {"field": "Shared with the organisation", "before": "Yes", "after": "No"}
        )
    elif op == "restore":
        from app.db.models import AppVersion

        n = int(params.get("version") or 0)
        if (
            db.query(AppVersion)
            .filter(AppVersion.app_id == app.id, AppVersion.version == n)
            .first()
            is None
        ):
            raise _Refused(f"This dashboard has no version {n}.")
        v = current_version(db, app)
        lines.append(
            {
                "field": "Version",
                "before": v.version if v else None,
                "after": f"version {n}, brought back",
            }
        )
    elif op == "remove":
        lines.append({"field": "Dashboard", "before": app.name, "after": "removed"})
    return {"op": op, "dashboard": None, "app": app, "lines": lines}


@register("norm_reports", "manage_dashboard")
def _manage_dashboard(params: dict, db: Session, thread_id: str | None) -> dict:
    from app.services import dashboards
    from app.services.app_runtime import resolve_access, restore_version

    user = _thread_user(db, thread_id)
    if user is None:
        return {"success": False, "data": None, "error": "no user on this thread"}
    params = dict(params or {})
    try:
        plan = _plan(db, user, params)
    except _Refused as exc:
        return {"success": False, "data": None, "error": str(exc)}
    op = plan["op"]
    cdb = _config_session()
    try:
        if op == "use":
            one = plan["dashboard"]
            out = dashboards.use(db, cdb, user, one["agent"], one["ref"])
            done = (
                f"{out['name']} is now what their {one['agent']} Dashboard page shows."
            )
        elif op == "copy":
            out = dashboards.copy(
                db, cdb, user, plan["dashboard"]["ref"], name=params.get("name")
            )
            done = (
                f"Their own copy, '{out['name']}' (slug {out['slug']}), is made and "
                "is what that Dashboard page shows now. Change it with save_app, "
                f"kind 'dashboard', slug '{out['slug']}'."
            )
        elif op in ("share", "unshare"):
            out = dashboards.set_shared(
                db,
                user,
                plan["app"].slug,
                op == "share",
                approve_writes=str(params.get("approve_writes")).lower() == "true",
            )
            done = (
                "Everyone in the organisation can now use it as it is; only the "
                "person changes it."
                if op == "share"
                else "It's theirs alone again."
            )
        elif op == "restore":
            app = plan["app"]
            r = restore_version(
                db, app, user, int(params["version"]), resolve_access(db, app, user)
            )
            out = dashboards.describe_app(db, app, user)
            done = f"Version {r['from']} is back, as version {r['version']}."
        else:  # remove
            out = dashboards.remove(db, user, plan["app"].slug)
            done = f"'{out['name']}' is removed; anyone using it is back to their next choice."
    except HTTPException as exc:
        db.rollback()
        return {"success": False, "data": None, "error": str(exc.detail)}
    finally:
        cdb.close()
    return {
        "success": True,
        "data": {
            "done": op,
            "dashboard": out,
            "now": done,
            # The page's dashboard reloads (lib/liveChanges).
            "agent": (out or {}).get("agent"),
        },
    }


@previews("norm_reports", "manage_dashboard")
def _preview_manage_dashboard(params: dict, db: Session, thread_id: str | None) -> dict:
    """What only the person sees needs nobody's yes; a change to a dashboard
    shared with the organisation shows what it changes first."""
    user = _thread_user(db, thread_id)
    if user is None:
        return {"error": "no user on this thread"}
    params = dict(params or {})
    try:
        plan = _plan(db, user, params)
    except _Refused as exc:
        return {"error": str(exc)}
    app = plan["app"]
    if plan["op"] in _PRIVATE_OPS or app is None or not _shared(db, app):
        return {"run": True}
    from app.services.app_runtime import current_version

    v = current_version(db, app)
    return {
        "preview": {
            "target": app.name,
            "changes": plan["lines"],
            "note": "This dashboard is shared: everyone in the organisation sees the change.",
        },
        "basis": {
            "dashboard": app.id,
            "version": v.version if v else None,
            "updated": str(app.updated_at),
            "params": params,
        },
    }
