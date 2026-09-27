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

Hierarchy v2 (Sep 2026 — AI Team Members -> Apps -> Components -> Connections):
the catalog now carries two more tiers. ``tier='agent'`` rows are the hireable
team members (owns_agents joins them to agent_configs; hiring one is the
entitlement); ``tier='app'`` rows are the Apps a member uses
(composition["unlocks"] on the agent row names them). Every App has an enabled
state: hiring a member enables its bundled Apps by default, individual Apps
can be switched off (an explicit entitlement row), and a priced App ships
bundled=false until enabled. ``hired_agent_slugs``/``apps_on`` resolve those
two levels; both return None while no tier='agent' rows exist, which is the
dark-launch switch — code deploys everywhere before the shared config DB
changes shape.
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


# The team members every organisation gets for free, always on: the base
# assistant, the router (an internal identity, never a product), Reports and
# App Builder (owner decision 27 Sep 2026: separate free tabs, not hireable).
ALWAYS_INCLUDED_AGENTS = frozenset({"base", "router", "reports", "app_builder"})


def _agent_rows(apps) -> list:
    return [
        a
        for a in apps
        if a.tier == "agent" and (a.composition or {}).get("owns_agents")
    ]


def hired_agent_slugs(
    organization_id: str | None, db: Session, config_db: Session
) -> set[str] | None:
    """Agent slugs this org has hired, or None when gating is inactive.

    None (fail open — keep everything) when the catalog has no tier='agent'
    rows yet or there is no org to resolve for; the caller must treat None as
    "no filtering". Otherwise ALWAYS_INCLUDED_AGENTS plus the owns_agents of
    every entitled agent-tier row. Explicit entitlement rows win, bundled
    defaults apply — inherited from entitled_slugs."""
    apps = _catalog(config_db)
    agent_rows = _agent_rows(apps)
    if not agent_rows or not organization_id:
        return None
    entitled = entitled_slugs(organization_id, db, config_db)
    hired = set(ALWAYS_INCLUDED_AGENTS)
    for a in agent_rows:
        if a.slug in entitled:
            hired.update(a.composition["owns_agents"])
    return hired


def apps_on(
    organization_id: str | None, db: Session, config_db: Session
) -> set[str] | None:
    """Slugs of the Apps that are ON for this org, or None when gating is
    inactive.

    Every App has an enabled state. A tier='app' row is on iff a hired team
    member uses it (some entitled agent row's composition["unlocks"] names it)
    AND the App itself is entitled (explicit row wins, else bundled default) —
    hiring a member enables its bundled Apps, an org can switch one off, a
    priced App ships bundled=false until enabled. A tier='user' row (published
    community App) is on iff entitled — its pages fall back to the Norm menu
    when its member isn't hired, so hire state doesn't gate it."""
    apps = _catalog(config_db)
    agent_rows = _agent_rows(apps)
    if not agent_rows or not organization_id:
        return None
    entitled = entitled_slugs(organization_id, db, config_db)
    unlocked: set[str] = set()
    for a in agent_rows:
        if a.slug in entitled:
            unlocked.update((a.composition or {}).get("unlocks") or [])
    on: set[str] = set()
    for a in apps:
        if a.tier == "app" and a.slug in unlocked and a.slug in entitled:
            on.add(a.slug)
        elif a.tier == "user" and a.slug in entitled:
            on.add(a.slug)
    return on


def agent_entitled(
    domain: str | None, organization_id: str | None, db: Session, config_db: Session
) -> bool:
    """False only when team-member gating is active, some tier='agent' row
    owns this domain, and the org hasn't hired it. Always-included members
    (base, router, reports, app_builder) and unowned domains are always
    allowed; gating inactive (hired_agent_slugs -> None) allows everything —
    the fail-open contract every enforcement point shares."""
    if not domain or not organization_id:
        return True
    hired = hired_agent_slugs(organization_id, db, config_db)
    if hired is None:
        return True
    if domain in hired:
        return True
    owned = {
        slug
        for a in _agent_rows(_catalog(config_db))
        for slug in a.composition["owns_agents"]
    }
    # A domain no agent row owns is unhireable, therefore ungateable.
    return domain not in owned
