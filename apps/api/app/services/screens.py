"""Which of an App's screens an organisation sees (owner, 23 Oct 2026).

An App's screens each name the system they're for — its ``connection``:
Hiring has the BambooHR hiring board and Hospo HR's own pages. A screen is in
the menu when its App is on, the organisation has its system, and an owner
hasn't switched that screen off. An organisation with both systems sees both
sets; switching screens off lets it mix them (hiring from BambooHR, reviews
from Hospo HR). Screens decide nothing about tools: a hired AI agent keeps
every tool of its Apps (entitlements.apps_reachable).

Systems are what people connect (``SYSTEMS``): Loaded is two connectors.
Hospo HR and Hospo Functions are Norm's own — connected without credentials:
Hospo HR is on unless an owner turns it off, Hospo Functions follows the
organisation's Functions mode. docs/plans/agents-and-apps-restructure.md §4.

Team apps (Oct 2026, docs/plans/team-apps-skeleton.md): a ``tier='team'``
catalog row (Kitchen, Front of house, Managers) is a second place a job app's
screen can show. A component's ``teams`` lists the team apps it is placed in
— the same screen in every one of them (owner, 24 Oct 2026: never a different
screen per team app; a team-level extra is a permission that shows more in
the same screen); ``team_only`` keeps it out of its agent's menu. A screen is
in a team app when its App is on, the organisation has its system, and the
owner has not switched THAT placement off: ``screen:<page_id>@<team>``
(``placement_key``), one switch per team app, so Recipes can be on in Kitchen
and off in Front of house. The agent-menu switch ``screen:<page_id>`` does
not touch team placements, and a placement switch does not touch the agent's
menu. Who sees a team app is the role (``roles.apps``). With no team rows in
the catalog there are no team apps, and nothing else differs.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: system -> its display name and the connectors that make it connected
SYSTEMS: dict[str, tuple[str, frozenset[str]]] = {
    "loaded": ("Loaded", frozenset({"loadedhub", "loaded_legacy"})),
    "xero": ("Xero", frozenset({"xero"})),
    "bamboohr": ("BambooHR", frozenset({"bamboohr"})),
    "hospo-hr": ("Hospo HR", frozenset()),
    "hospo-functions": ("Hospo Functions", frozenset()),
}
#: Norm's own systems: switched, not connected with credentials.
BUILT_IN = ("hospo-hr", "hospo-functions")

CONNECTION_PREFIX = "connection:"
SCREEN_PREFIX = "screen:"
#: ``screen:<page_id>@<team>`` switches one screen's placement in one team app.
PLACEMENT_SEP = "@"

#: The team apps, in the order they are listed everywhere (the seed's order).
TEAM_SLUGS = ("kitchen", "front_of_house", "managers")


def system_name(system: str | None) -> str | None:
    return SYSTEMS[system][0] if system in SYSTEMS else system


def _switches(organization_id: str, db: Session) -> dict[str, bool]:
    from app.db.models import OrgAppEntitlement

    try:
        return {
            e.app_slug: e.enabled
            for e in db.query(OrgAppEntitlement)
            .filter(
                OrgAppEntitlement.organization_id == organization_id,
                (OrgAppEntitlement.app_slug.like(f"{CONNECTION_PREFIX}%"))
                | (OrgAppEntitlement.app_slug.like(f"{SCREEN_PREFIX}%")),
            )
            .all()
        }
    except Exception:  # pragma: no cover — fail open like entitlements
        logger.warning("screen switches unavailable — failing open")
        return {}


def systems_on(organization_id: str | None, db: Session) -> set[str] | None:
    """The systems this organisation has: a connector of the system connected
    at one of its venues or organisation-wide, or a built-in one switched on.
    None with no organisation (show everything)."""
    if not organization_id:
        return None
    from app.db.models import Connection
    from app.services import connection_scope
    from app.services.functions.entitlement import functions_mode

    connected = {
        r[0]
        for r in connection_scope.in_org(
            db.query(Connection.connector_name).filter(Connection.enabled == "true"),
            organization_id,
        ).all()
    }
    switches = _switches(organization_id, db)
    on = {s for s, (_, conns) in SYSTEMS.items() if conns & connected}
    if switches.get(f"{CONNECTION_PREFIX}hospo-hr", True):
        on.add("hospo-hr")
    if functions_mode(db, organization_id) != "off":
        on.add("hospo-functions")
    return on


def screens_off(organization_id: str | None, db: Session) -> set[str]:
    """Page ids an owner has switched off in the agents' menus. Team
    placements (``screen:<page_id>@<team>``) are a separate switch
    (``placements_off``) and are not in this set."""
    if not organization_id:
        return set()
    return {
        key[len(SCREEN_PREFIX) :]
        for key, enabled in _switches(organization_id, db).items()
        if key.startswith(SCREEN_PREFIX) and PLACEMENT_SEP not in key and not enabled
    }


def placement_key(page_id: str, team: str) -> str:
    """The org_app_entitlements key that switches one screen's placement in
    one team app: ``screen:<page_id>@<team>``."""
    return f"{SCREEN_PREFIX}{page_id}{PLACEMENT_SEP}{team}"


def placements_off(organization_id: str | None, db: Session) -> set[tuple[str, str]]:
    """``(page_id, team)`` pairs an owner has switched off: the screen stays
    in its other team apps and in its agent's menu."""
    if not organization_id:
        return set()
    out: set[tuple[str, str]] = set()
    for key, enabled in _switches(organization_id, db).items():
        if enabled or not key.startswith(SCREEN_PREFIX):
            continue
        body = key[len(SCREEN_PREFIX) :]
        if PLACEMENT_SEP not in body:
            continue
        page_id, _, team = body.rpartition(PLACEMENT_SEP)
        if page_id and team:
            out.add((page_id, team))
    return out


def _teams_of(component: dict) -> list[str]:
    """The team apps a component's ``teams`` lists, as the seed writes it (a
    list of slugs; anything else reads as no placement)."""
    teams = component.get("teams")
    if not isinstance(teams, list):
        return []
    return [t for t in teams if isinstance(t, str) and t]


def app_screens(app_row) -> list[dict]:
    """An App's screens: its components with a page, and the pages of the
    App-platform app it fronts (``app:<slug>``), each with its system and
    where it is placed — ``teams`` (the team apps it also shows in, the same
    screen in each; ``[]`` for the agent's menu alone) and ``team_only``
    (shows only in its team apps, never in its agent's menu)."""
    comp = app_row.composition or {}
    out = []
    for c in comp.get("components") or []:
        if not (
            isinstance(c, dict)
            and isinstance(c.get("page"), dict)
            and c["page"].get("id")
        ):
            continue
        teams = _teams_of(c)
        out.append(
            {
                "page_id": c["page"]["id"],
                "label": c["page"].get("label") or c.get("key"),
                "component": c.get("key"),
                "connection": c.get("connection"),
                "teams": teams,
                "team_only": bool(c.get("team_only")) and bool(teams),
            }
        )
    if comp.get("app_slug"):
        out.append(
            {
                "page_id": f"app:{comp['app_slug']}",
                "label": app_row.name,
                "component": "app_runner",
                "connection": comp.get("app_connection"),
                "teams": [],
                "team_only": False,
            }
        )
    return out


def screen_state(screen: dict, systems: set[str] | None, off: set[str]) -> dict:
    """Whether the organisation has the screen's system, whether it's switched
    on, and so whether it shows."""
    system = screen.get("connection")
    available = systems is None or not system or system in systems
    switched_on = screen["page_id"] not in off
    return {
        **screen,
        "connection_name": system_name(system),
        "available": available,
        "switched_on": switched_on,
        "visible": available and switched_on,
    }


def pages_hidden(
    organization_id: str | None, db: Session, config_db: Session
) -> set[str] | None:
    """Page ids of catalog Apps' screens this organisation doesn't see — its
    system missing, or switched off. None with no organisation."""
    if not organization_id:
        return None
    from app.services.entitlements import _catalog

    systems = systems_on(organization_id, db)
    off = screens_off(organization_id, db)
    hidden: set[str] = set()
    for row in _catalog(config_db):
        if row.tier != "app":
            continue
        for s in app_screens(row):
            if not screen_state(s, systems, off)["visible"]:
                hidden.add(s["page_id"])
    return hidden


# ── Team apps ────────────────────────────────────────────────────────────────


def team_rows(config_db: Session) -> list:
    """The active ``tier='team'`` catalog rows, in seed order (TEAM_SLUGS;
    a slug not listed there sorts last, by name). Empty until the owner
    installs them — and then there are no team apps."""
    from app.services.entitlements import _catalog

    return team_order(r for r in _catalog(config_db) if r.tier == "team")


def team_order(rows) -> list:
    """Team rows in seed order (TEAM_SLUGS), the one order they are listed in."""
    order = {slug: i for i, slug in enumerate(TEAM_SLUGS)}
    return sorted(rows, key=lambda r: (order.get(r.slug, len(order)), r.name or ""))


def _team_tool_label(config_db: Session):
    """``connector.action`` -> the label the App Map shows for it (the same
    source the team payload's tool entries use)."""
    from app.services.agent_catalog import _CapabilityContext

    ctx = _CapabilityContext(config_db)
    return lambda key: ctx.tool(key)["label"]


def team_only_pages(config_db: Session) -> set[str]:
    """Page ids of screens marked ``team_only``: they show in their team apps
    and never in their agent's menu (the menu filters them out)."""
    from app.services.entitlements import _catalog

    return {
        s["page_id"]
        for row in _catalog(config_db)
        if row.tier == "app"
        for s in app_screens(row)
        if s["team_only"]
    }


def _app_order_key(row) -> tuple:
    # The seed writes rows in its own order on first run, so creation time
    # is the catalog's order; the slug settles ties.
    return (row.created_at.isoformat() if row.created_at else "", row.slug)


def team_apps(
    config_db: Session,
    db: Session,
    organization_id: str | None,
    *,
    tool_label=None,
) -> list[dict]:
    """The team apps as this organisation sees them: one entry per team row —
    ``{slug, name, icon, tagline, tools: [{key, label}], screens: [...]}``.

    ``screens`` are the screens of every App that is ON for the organisation
    (entitlements.apps_on) whose ``teams`` lists this team app, whose system
    the organisation has, and whose placement here is not switched off
    (``placements_off``: ``screen:<page_id>@<team>``) — each ``{page_id,
    label, component, connection, connection_name, app_slug, app_name,
    member, teams, team_only}``, in the App's component order, Apps in
    catalog order. The same screen appears in every team app it lists. The
    agent-menu switch ``screen:<page_id>`` (``screens_off``) does not apply
    here: a screen can be off in the agent's menu and on in a team app, and
    the other way round. A team row with no screens is still returned with
    ``screens: []`` (the shell hides it; the Roles form still lists it).
    The web filters by the viewer's role; this is org-wide.

    ``tool_label`` (key -> label) lets a caller that already has a
    _CapabilityContext reuse it; by default one is built here."""
    rows = team_rows(config_db)
    if not rows:
        return []
    from app.services.entitlements import _catalog, app_member, apps_on

    on = apps_on(organization_id, db, config_db)  # None = everything on
    systems = systems_on(organization_id, db)
    p_off = placements_off(organization_id, db)
    label_of = tool_label or _team_tool_label(config_db)
    placed: dict[str, list[dict]] = {r.slug: [] for r in rows}
    apps = sorted(
        (r for r in _catalog(config_db) if r.tier == "app"), key=_app_order_key
    )
    for app in apps:
        if on is not None and app.slug not in on:
            continue
        member = app_member(app)
        for sc in app_screens(app):
            teams = [t for t in sc["teams"] if t in placed]
            if not teams:
                continue
            # the placement switch, not the agent-menu switch
            state = screen_state(sc, systems, set())
            if not state["available"]:
                continue
            for team in teams:
                if (sc["page_id"], team) in p_off:
                    continue
                placed[team].append(
                    {
                        "page_id": sc["page_id"],
                        "label": sc["label"],
                        "component": sc["component"],
                        "connection": sc["connection"],
                        "connection_name": state["connection_name"],
                        "app_slug": app.slug,
                        "app_name": app.name,
                        "member": member,
                        "teams": list(sc["teams"]),
                        "team_only": sc["team_only"],
                    }
                )
    return [
        {
            "slug": r.slug,
            "name": r.name,
            "icon": r.icon,
            "tagline": (r.composition or {}).get("tagline") or r.description or "",
            "tools": [
                {"key": key, "label": label_of(key)}
                for key in (r.composition or {}).get("uses_tools") or []
            ],
            "screens": placed[r.slug],
        }
        for r in rows
    ]
