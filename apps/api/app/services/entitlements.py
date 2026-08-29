"""App entitlement resolution — which marketplace Apps an org has enabled.

The catalog (`marketplace_apps`, config db) says what exists and what each App
lights up; `org_app_entitlements` (main db) says what an org has switched on.
The rule, in one place so every filter agrees:

    explicit row        -> its `enabled` value wins
    no row              -> the app's `bundled` default applies
    app not in catalog  -> allowed (the catalog is a curation layer, not a
                           lockout; an unclaimed connector keeps working)

Fail-open is deliberate throughout: entitlement is a billing/visibility act,
and a marketplace hiccup must never take tools away from a venue mid-service.
The three enforcement points all resolve through here (the "three filters" of
docs/lite-apps-architecture.md Part 4, docs/apps-marketplace-plan.md Phase 1):

    1. agent entry            -> agent_entitled()
    2. prompt_builder tools   -> unentitled_connectors()  (skipped bindings)
    3. mcp projection         -> inherits (2): project_tools gates through
                                 _collect_tools, so no third code path exists.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _catalog(config_db: Session):
    from app.db.config_models import MarketplaceApp

    try:
        return (
            config_db.query(MarketplaceApp)
            .filter(MarketplaceApp.status == "active")
            .all()
        )
    except Exception:  # pragma: no cover — pre-create_all or transient DB issue
        logger.warning("marketplace catalog unavailable — failing open")
        return []


def org_id_for_user(user_id: str | None, db: Session) -> str | None:
    """The user's org (first membership — orgs are single-membership today)."""
    if not user_id:
        return None
    from app.db.models import OrganizationMembership

    m = (
        db.query(OrganizationMembership)
        .filter(OrganizationMembership.user_id == user_id)
        .first()
    )
    return m.organization_id if m else None


def entitled_slugs(
    organization_id: str | None, db: Session, config_db: Session
) -> set[str]:
    """Slugs of every catalog App this org is entitled to."""
    apps = _catalog(config_db)
    if not apps:
        return set()
    overrides: dict[str, bool] = {}
    if organization_id:
        from app.db.models import OrgAppEntitlement

        try:
            overrides = {
                e.app_slug: e.enabled
                for e in db.query(OrgAppEntitlement)
                .filter(OrgAppEntitlement.organization_id == organization_id)
                .all()
            }
        except Exception:  # pragma: no cover — migration not applied yet
            logger.warning("org_app_entitlements unavailable — failing open")
            overrides = {}
    return {a.slug for a in apps if overrides.get(a.slug, bool(a.bundled))}


def _declared_connections(app) -> set[str]:
    comp = app.composition or {}
    conns = set(comp.get("connections") or [])
    # transitional: rows seeded before the connections/apps split carried a
    # single `spec` key.
    if comp.get("spec"):
        conns.add(comp["spec"])
    return conns


def unentitled_connectors(
    organization_id: str | None, db: Session, config_db: Session
) -> set[str]:
    """Connection names this org has NO entitled App for.

    Apps declare the connections they consume (composition["connections"], one
    or many — the connections/apps split). A connection stays available while
    ANY entitled app declares it; it is blocked only when every declaring app
    is disabled. An undeclared connection is never filtered, and an empty
    catalog filters nothing (the dark-launch property).
    """
    apps = _catalog(config_db)
    if not apps or not organization_id:
        return set()
    entitled = entitled_slugs(organization_id, db, config_db)
    declared: set[str] = set()
    kept: set[str] = set()
    for a in apps:
        conns = _declared_connections(a)
        declared |= conns
        if a.slug in entitled:
            kept |= conns
    return declared - kept


def unentitled_tool_actions(
    organization_id: str | None, db: Session, config_db: Session
) -> set[str]:
    """``connector.action`` keys claimed only by disabled Apps.

    ``composition["tool_actions"]`` entries are exact keys or a per-connector
    wildcard ``connector.*``. Same claim semantics as connections: available
    while any entitled app claims it; unclaimed actions are never filtered.
    """
    apps = _catalog(config_db)
    if not apps or not organization_id:
        return set()
    entitled = entitled_slugs(organization_id, db, config_db)
    claimed: set[str] = set()
    kept: set[str] = set()
    for a in apps:
        keys = set((a.composition or {}).get("tool_actions") or [])
        claimed |= keys
        if a.slug in entitled:
            kept |= keys
    return claimed - kept


def agent_entitled(
    domain: str | None, organization_id: str | None, db: Session, config_db: Session
) -> bool:
    """False only when some catalog App OWNS this agent and none of the owning
    apps is entitled. Ownership is composition["owns_agents"] — the agent-bundle
    rows (Norm HR etc.). composition["agents"] is informational ("this app's
    pages appear in these agents' menus") and deliberately NOT consulted here,
    so disabling an integration app never switches an agent off. An unowned
    agent is always allowed."""
    if not domain or not organization_id:
        return True
    apps = _catalog(config_db)
    owning = [
        a.slug
        for a in apps
        if domain in ((a.composition or {}).get("owns_agents") or [])
    ]
    if not owning:
        return True
    entitled = entitled_slugs(organization_id, db, config_db)
    return any(slug in entitled for slug in owning)
