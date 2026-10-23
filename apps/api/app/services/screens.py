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
    """Page ids an owner has switched off."""
    if not organization_id:
        return set()
    return {
        key[len(SCREEN_PREFIX) :]
        for key, enabled in _switches(organization_id, db).items()
        if key.startswith(SCREEN_PREFIX) and not enabled
    }


def app_screens(app_row) -> list[dict]:
    """An App's screens: its components with a page, and the pages of the
    App-platform app it fronts (``app:<slug>``), each with its system."""
    comp = app_row.composition or {}
    out = [
        {
            "page_id": c["page"]["id"],
            "label": c["page"].get("label") or c.get("key"),
            "component": c.get("key"),
            "connection": c.get("connection"),
        }
        for c in comp.get("components") or []
        if isinstance(c, dict)
        and isinstance(c.get("page"), dict)
        and c["page"].get("id")
    ]
    if comp.get("app_slug"):
        out.append(
            {
                "page_id": f"app:{comp['app_slug']}",
                "label": app_row.name,
                "component": "app_runner",
                "connection": comp.get("app_connection"),
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
