"""Which screens, tools and pages a person's role reaches, for the routes a
page calls directly (staff access, docs/plans/staff-access.md §11.9;
team apps, docs/plans/team-apps-skeleton.md).

The chat is already narrowed by the role (prompt_builder._role_blocked_tools)
and so is an App's ``/call`` (app_runtime._check_role_reach). The routes a
page calls itself — ``/component-api``, ``/connectors/{name}/execute``, the
invoice, recipe, stock, Functions and report routes — checked only that the
caller could open the venue, so a Kitchen login could add a shift, read the
staff list with pay, or place an order. This module is the one rule they ask:

- **A screen (a catalog component)** is reachable when the role reaches an
  App that has it (``allows_app`` on the App's key with its AI agent as
  ``member``; Norm Core's only when ``core_allowed``), or when the screen
  shows in a team app the role reaches — its ``teams`` list names the team
  app AND the organisation has that placement showing (screens.team_apps:
  the App on, its system on, ``screen:<page_id>@<team>`` not switched off).
- **An app** (a built-in like Training, a job app) the same way: the role
  names it or its AI agent, or one of its screens shows in a team app the
  role reaches (app_reachable). An org-built app only by the first rule.
- **A tool** is reachable when one of the Apps giving it passes the same
  filter the chat menu uses, or a team app the role reaches lists it in its
  ``uses_tools`` — a screen's placement never widens the tools.
- **Something no App claims** stays open to everyone but venue staff, so
  owners and managers keep what they had.

Platform admins and roles with no limits (``Access(None, None)``) reach
everything, exactly as before.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.services import role_access

REFUSED = "This screen isn't available to your role."
REFUSED_TOOL = "Your role doesn't reach this. A manager can help."
REFUSED_MANAGERS = "This is for managers. Your role doesn't include it."

#: A working document's type -> the screen that edits it
#: (services/document_sync.py maps the syncing ones the same way).
DOC_COMPONENTS = {
    "roster": "roster_editor",
    "order": "purchase_order_editor",
    "criteria": "criteria_editor",
    "recipe": "recipe_editor",
    "received_invoice": "receive_invoice_editor",
}


def access_for(db: Session, user, org_id: str | None = None) -> role_access.Access:
    """The role's reach in ``org_id`` (or the organisation the request acts
    in); a platform admin (or no one to ask about) limits nothing here — the
    routes have their own sign-in checks."""
    if user is None or getattr(user, "role", None) == "admin":
        return role_access.EVERYTHING
    return role_access.for_user(db, user.id, org_id)


def is_staff(db: Session, user, org_id: str | None = None) -> bool:
    """Venue staff: a role that names no AI agent at all."""
    return access_for(db, user, org_id).staff


def require_not_staff(
    db: Session, user, message: str = REFUSED_MANAGERS, org_id: str | None = None
) -> None:
    """403 for venue staff, for a route that is a manager's by nature."""
    if is_staff(db, user, org_id):
        raise HTTPException(403, message)


# ── The catalog ──────────────────────────────────────────────────────────────


def _rows(config_db: Session) -> list:
    """Catalog Apps that own screens and tools (``app`` and ``user`` tiers)."""
    from app.services.entitlements import _catalog

    return [r for r in _catalog(config_db) if r.tier in ("app", "user")]


def _components(row) -> list[dict]:
    out = []
    for c in (row.composition or {}).get("components") or []:
        if isinstance(c, dict) and c.get("key"):
            out.append(c)
        elif isinstance(c, str) and c:
            out.append({"key": c})
    return out


def _app_allowed(access: role_access.Access, row) -> bool:
    from app.services.entitlements import ALL_MEMBERS, app_member

    member = app_member(row)
    if row.slug == role_access.CORE_APP_SLUG or member == ALL_MEMBERS:
        return role_access.core_allowed(access)
    key = (row.composition or {}).get("app_slug") or row.slug
    return role_access.allows_app(access, key, member=member)


def _visible_placements(
    db: Session, config_db: Session, user, access: role_access.Access
) -> list[dict]:
    """The screens showing in the team apps this role reaches, as the
    organisation has them (screens.team_apps: the App on, its system on, the
    placement ``screen:<page_id>@<team>`` not switched off). A placement
    counts only while it shows — the catalog's ``teams`` list alone opens
    nothing."""
    from app.services import active_org
    from app.services.screens import team_apps

    org = active_org.org_id_for(db, user.id) if user is not None else None
    if not org:
        return []
    return [
        screen
        for team in team_apps(config_db, db, org)
        if role_access.allows_app(access, team["slug"])
        for screen in team["screens"]
    ]


# ── Screens ──────────────────────────────────────────────────────────────────


def component_reachable(db: Session, config_db: Session, user, component_key) -> bool:
    """Whether the person's role reaches this screen (a catalog component)."""
    access = access_for(db, user)
    if access == role_access.EVERYTHING:
        return True
    owners = [
        row
        for row in _rows(config_db)
        if any(c["key"] == component_key for c in _components(row))
    ]
    if not owners:
        # No App has it: what it always was for everyone but venue staff.
        return not access.staff
    if any(_app_allowed(access, row) for row in owners):
        return True
    slugs = {row.slug for row in owners}
    return any(
        sc.get("component") == component_key and sc.get("app_slug") in slugs
        for sc in _visible_placements(db, config_db, user, access)
    )


def require_component(db: Session, config_db: Session, user, component_key) -> None:
    if not component_reachable(db, config_db, user, component_key):
        raise HTTPException(403, REFUSED)


def require_any_component(db: Session, config_db: Session, user, keys) -> None:
    """403 unless the role reaches at least one of these screens (a route
    shared by an App's screens)."""
    if not any(component_reachable(db, config_db, user, k) for k in keys):
        raise HTTPException(403, REFUSED)


def app_reachable(
    db: Session, config_db: Session, user, app_key: str, *, member: str | None = None
) -> bool:
    """Whether the role reaches an app by the key a role's ``apps`` list
    names it with (role_access.allows_app: a built-in's slug, ``custom:<slug>``
    for an org-built app, a job app's catalog slug), with ``member`` its AI
    agent — or, for a built-in or a job app, through a team app: a catalog
    row fronting it (``composition.app_slug`` == the key, or its own slug ==
    the key) has a screen showing in a team app the role reaches
    (_visible_placements). An org-built app (``custom:``) has only the first
    rule. Tools are not this question (tool_reachable)."""
    from app.services.entitlements import CUSTOM_PREFIX

    access = access_for(db, user)
    if role_access.allows_app(access, app_key, member=member):
        return True
    if str(app_key).startswith(CUSTOM_PREFIX):
        return False
    fronting = {
        row.slug
        for row in _rows(config_db)
        if (row.composition or {}).get("app_slug") == app_key or row.slug == app_key
    }
    if not fronting:
        return False
    return any(
        sc.get("app_slug") in fronting
        for sc in _visible_placements(db, config_db, user, access)
    )


def catalog_app_reachable(db: Session, config_db: Session, user, app_slug) -> bool:
    """Whether the role reaches a catalog App by its slug, for a router that
    serves the whole App (Functions): the App itself (its AI agent, or the
    role naming it), or one of its screens showing in a team app the role
    reaches. With no such row in the catalog, everyone but venue staff."""
    access = access_for(db, user)
    if access == role_access.EVERYTHING:
        return True
    row = next((r for r in _rows(config_db) if r.slug == app_slug), None)
    if row is None:
        return not access.staff
    if _app_allowed(access, row):
        return True
    return any(
        sc.get("app_slug") == row.slug
        for sc in _visible_placements(db, config_db, user, access)
    )


def needs(*component_keys: str):
    """A route dependency: the caller's role must reach one of these screens.
    Used as ``APIRouter(dependencies=[Depends(needs("stock_catalogue"))])``."""
    from app.auth.dependencies import get_current_user
    from app.db.engine import get_config_db, get_db

    def _dependency(
        user=Depends(get_current_user),
        db: Session = Depends(get_db),
        config_db: Session = Depends(get_config_db),
    ) -> None:
        require_any_component(db, config_db, user, component_keys)

    return _dependency


#: A page's own load, by the page id a functional page saves as its document
#: type: the FUNCTIONAL_PAGES ``loadAction`` in apps/web/app/components/pages/
#: pageRegistry.ts whose connector is an outside system (the self-loading
#: ``_none`` pages and the ``norm`` task boards are not here). A role that
#: reaches the screen may run its load even though the load is an API
#: endpoint no App claims — a screen placed in a team app loads for venue
#: staff (team-apps skeleton §1, §4.2). tests/test_staff_routes.py checks
#: this against pageRegistry.ts.
PAGE_LOADS: dict[str, tuple[str, str]] = {
    "roster": ("loadedhub", "get_roster"),
    "hiring": ("bamboohr", "get_jobs"),
    "orders": ("loadedhub", "get_purchase_orders_summary"),
}


def doc_reachable(db: Session, config_db: Session, user, doc_type: str | None) -> bool:
    """Whether the role reaches the screen that edits this kind of working
    document: a known type (DOC_COMPONENTS), else the screen whose page id it
    is (a functional page saves its page id as the type)."""
    access = access_for(db, user)
    if access == role_access.EVERYTHING:
        return True
    key = DOC_COMPONENTS.get(str(doc_type or ""))
    if key is None:
        for row in _rows(config_db):
            for c in _components(row):
                page = c.get("page")
                if isinstance(page, dict) and page.get("id") == doc_type:
                    key = c["key"]
                    break
            if key:
                break
    if key is None:
        return not access.staff
    return component_reachable(db, config_db, user, key)


# ── Tools ────────────────────────────────────────────────────────────────────


def tool_reachable(db: Session, config_db: Session, user, connector, action) -> bool:
    """Whether the role reaches ``connector.action``: the chat menu's own
    filter (an App giving it that the role reaches, or a team app the role
    reaches listing it). A row no App gives (an API endpoint, a built-in no
    App claims) is open to everyone but venue staff."""
    from app.agents.prompt_builder import _role_blocked_tools
    from app.services.entitlements import team_tools, tool_users

    access = access_for(db, user)
    if access == role_access.EVERYTHING:
        return True
    key = f"{connector}.{action}"
    giving = tool_users(config_db).get(key)
    if not giving:
        return not access.staff
    return key not in _role_blocked_tools(
        db,
        user.id,
        config_db,
        {key: giving},
        team_tools=team_tools(config_db),
        team_reachable=team_reachable_apps(db, config_db, user),
    )


def team_reachable_apps(db: Session, config_db: Session, user) -> set[str] | None:
    """The Apps reachable for the caller's organisation (entitlements.
    apps_reachable), for the team rule of a direct route: a team app's
    ``uses_tools`` entry passes only while an App giving the tool is
    reachable (team-apps skeleton §3.4) — a Kitchen role ran the recipes
    tools through ``/connectors/.../execute`` in an organisation that had
    not hired Executive Chef."""
    from app.services.entitlements import apps_reachable, org_id_for_user

    return apps_reachable(org_id_for_user(user.id, db), db, config_db)


def require_tool(db: Session, config_db: Session, user, connector, action) -> None:
    if not tool_reachable(db, config_db, user, connector, action):
        raise HTTPException(403, REFUSED_TOOL)


def require_page_load(
    db: Session, config_db: Session, user, connector, action, doc_type
) -> None:
    """A page's load into a working document (``/working-documents/
    from-connector``). The role must reach the screen that edits the
    document's type (doc_reachable): the type stamped on the document is
    what later decides which screen's writes its edits sync through, so a
    role without the Rosters screen could load any read as a ``roster`` and
    patch shifts into Loaded. And it must reach the read: the tool itself,
    or the screen's own load (PAGE_LOADS)."""
    if not doc_reachable(db, config_db, user, doc_type):
        raise HTTPException(403, REFUSED)
    if PAGE_LOADS.get(str(doc_type or "")) == (connector, action):
        return
    require_tool(db, config_db, user, connector, action)
