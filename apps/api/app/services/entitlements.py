"""App entitlement resolution — which marketplace Apps an org has enabled.

The catalog (`marketplace_apps`, config db) says what exists and what each App
lights up; `org_app_entitlements` (main db) says what an org has switched on.
The rule, in one place so every filter agrees:

    explicit row        -> its `enabled` value wins
    no row              -> the app's `bundled` default applies
    app not in catalog  -> allowed (the catalog is a curation layer, not a
                           lockout)

Fail-open is deliberate throughout: entitlement is a billing/visibility act,
and a marketplace hiccup must never take tools away from a venue mid-service.
The enforcement points all resolve through here:

    1. agent entry            -> agent_entitled()
    2. prompt_builder tools   -> tool_owners() + apps_on(): a tool reaches the
                                 agent iff the App that owns it is on
    3. mcp projection         -> inherits (2): project_tools gates through
                                 _collect_tools, so no third code path exists.

Hierarchy v2 (Sep 2026 — AI Team Members -> Apps -> Components -> Connections)
and Apps v3 (28 Sep 2026): ``tier='agent'`` rows are the hireable team members
(owns_agents joins them to agent_configs; hiring one is the entitlement);
``tier='app'`` rows are the Apps, each naming its ONE member in
composition["member"]. Every App has an enabled state: hiring a member enables
its bundled Apps by default, individual Apps can be switched off (an explicit
entitlement row), and a priced App ships bundled=false until enabled.
``hired_agent_slugs``/``apps_on`` resolve those two levels; both return None
while no tier='agent' rows exist (or there is no org) — gating inactive.

Norm Functions (Oct 2026) adds a switch of its own, the organisation's
``functions_mode``: ``off`` wins over anything the catalog says, and while its
member and App rows are not in the catalog yet, an organisation whose mode is
on counts as having both (services/functions/catalog.py declares them).
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
    entitled = {a.slug for a in apps if overrides.get(a.slug, bool(a.bundled))}
    return _functions_off_wins(entitled, organization_id, db)


def _functions_off_wins(
    entitled: set[str], organization_id: str | None, db: Session
) -> set[str]:
    """Norm Functions' catalog rows — its team member and its App — count only
    while the org's ``functions_mode`` is on: ``off`` beats any entitlement,
    so an organisation that hasn't been switched on is neither shown Functions
    nor billed for it (billing reads this same set). The mode is only read
    when one of those rows is entitled."""
    from app.services.functions.catalog import CATALOG_SLUGS

    if not entitled & CATALOG_SLUGS:
        return entitled
    from app.services.functions.entitlement import functions_mode

    if functions_mode(db, organization_id) == "off":
        return entitled - CATALOG_SLUGS
    return entitled


# The team members every organisation gets for free, always on: the base
# assistant, Reports and App Builder (owner decision 27 Sep 2026: separate free
# tabs, not hireable).
ALWAYS_INCLUDED_AGENTS = frozenset({"base", "reports", "app_builder"})


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
    return _with_functions_stand_in(hired, organization_id, db, agent_rows)


def _with_functions_stand_in(
    hired: set[str], organization_id: str, db: Session, agent_rows
) -> set[str]:
    """Until the seed writes Norm Functions' member row, the member counts as
    hired exactly when the org's ``functions_mode`` is on — the fallback its
    App gets in services/functions/entitlement.py, so the Functions tab
    appears for a shadow or live org without a write to the shared config
    DB. Once the row exists, entitled_slugs decides (mode included)."""
    from app.services.functions.catalog import MEMBER_SLUG
    from app.services.functions.entitlement import functions_mode

    owned = {s for a in agent_rows for s in a.composition["owns_agents"]}
    if MEMBER_SLUG not in owned and functions_mode(db, organization_id) != "off":
        hired.add(MEMBER_SLUG)
    return hired


CUSTOM_PREFIX = "custom:"


def custom_app_owner(app) -> str:
    """The team member a custom (App-platform) app is bound to. NULL agent =
    App Builder, where apps have always lived by default."""
    return app.agent or "app_builder"


# Norm Core's member: bound to every team member (the only App allowed to be).
ALL_MEMBERS = "*"


def app_member(app_row) -> str | None:
    """The ONE team member an App is bound to (Apps v3, 28 Sep 2026): its
    composition["member"]; "*" means every member (Norm Core only)."""
    return (app_row.composition or {}).get("member") or None


def _catalog_app_members(app_row) -> set[str]:
    """Member slugs an App counts as bound to, for hire checks. Norm Core
    ("*") is bound to the always-hired base assistant, so it's always on."""
    m = app_member(app_row)
    if m == ALL_MEMBERS:
        return {"base"}
    return {m} if m else set()


def is_switchable(app_row) -> bool:
    """False for Apps that are always on while their member is hired
    (Norm Core, Saved Reports, App Builder)."""
    return (app_row.composition or {}).get("switchable", True) is not False


def tool_owners(config_db: Session) -> dict[str, str]:
    """``connector.action`` -> the ONE App slug that owns the tool. This is
    the App Map, the only tool-exposure list: a tool no App owns never
    reaches the agent or MCP."""
    owners: dict[str, str] = {}
    for a in _catalog(config_db):
        if a.tier in ("app", "user"):
            for key in (a.composition or {}).get("tools") or []:
                owners.setdefault(key, a.slug)
    return owners


def skill_owners(config_db: Session) -> dict[str, str]:
    """Playbook slug -> the ONE App slug that owns the skill."""
    owners: dict[str, str] = {}
    for a in _catalog(config_db):
        if a.tier in ("app", "user"):
            for slug in (a.composition or {}).get("skills") or []:
                owners.setdefault(slug, a.slug)
    return owners


def apps_on(
    organization_id: str | None, db: Session, config_db: Session
) -> set[str] | None:
    """Slugs of the Apps that are ON for this org, or None when gating is
    inactive.

    Every App has an enabled state. A catalog App (tier='app') is on iff some
    team member that uses it is hired (always-included members count) AND the
    App itself is entitled — explicit row wins, else bundled default; a priced
    App ships bundled=false until enabled. A published community App
    (tier='user') is on iff entitled. A custom App — the org's own
    App-platform apps, reported as ``custom:<slug>`` — is on iff the member
    it's bound to is hired and the org hasn't switched it off (custom apps
    default ON: the team built them)."""
    apps = _catalog(config_db)
    agent_rows = _agent_rows(apps)
    if not agent_rows or not organization_id:
        return None
    hired = hired_agent_slugs(organization_id, db, config_db) or set()
    entitled = entitled_slugs(organization_id, db, config_db)
    on: set[str] = set()
    pointers: dict[str, str] = {}  # App-platform slug -> catalog row fronting it
    for a in apps:
        # Only first-party rows front a custom app. A community row's app_slug
        # is a bare slug from its publisher's org; honouring it here would
        # take over every org's custom app that happens to share the slug.
        ptr = (a.composition or {}).get("app_slug")
        if ptr and a.tier == "app":
            pointers[ptr] = a.slug
        if a.tier == "app":
            entitled_or_fixed = (not is_switchable(a)) or a.slug in entitled
            if entitled_or_fixed and _catalog_app_members(a) & hired:
                on.add(a.slug)
        elif a.tier == "user" and a.slug in entitled:
            on.add(a.slug)
    # A custom app fronted by a catalog row (Norm HR -> `hiring`) is ONE App:
    # the catalog row's switch governs it, so its pinned page follows too.
    custom_on = _custom_apps_on(organization_id, db, hired)
    for ptr, catalog_slug in pointers.items():
        key = f"{CUSTOM_PREFIX}{ptr}"
        custom_on.discard(key)
        if catalog_slug in on:
            custom_on.add(key)
    # Norm Functions is also switched by the org's functions_mode, and counts
    # as on without a catalog row while none exists (services/functions/
    # entitlement.py has the rule).
    from app.services.functions.entitlement import adjust_apps_on

    return adjust_apps_on(on | custom_on, organization_id, db, apps)


def _custom_apps_on(organization_id: str, db: Session, hired: set[str]) -> set[str]:
    from app.db.models import App, OrgAppEntitlement

    try:
        off = {
            e.app_slug
            for e in db.query(OrgAppEntitlement)
            .filter(
                OrgAppEntitlement.organization_id == organization_id,
                OrgAppEntitlement.app_slug.like(f"{CUSTOM_PREFIX}%"),
                OrgAppEntitlement.enabled == False,  # noqa: E712
            )
            .all()
        }
        rows = (
            db.query(App)
            .filter(App.organization_id == organization_id, App.archived_at.is_(None))
            .all()
        )
    except Exception:  # pragma: no cover — fail open like everything here
        logger.warning("custom apps unavailable — failing open")
        return set()
    return {
        f"{CUSTOM_PREFIX}{a.slug}"
        for a in rows
        if custom_app_owner(a) in hired and f"{CUSTOM_PREFIX}{a.slug}" not in off
    }


def agent_entitled(
    domain: str | None, organization_id: str | None, db: Session, config_db: Session
) -> bool:
    """False only when team-member gating is active, some tier='agent' row
    owns this domain, and the org hasn't hired it. Always-included members
    (base, reports, app_builder) and unowned domains are always
    allowed; gating inactive (hired_agent_slugs -> None) allows everything —
    the fail-open contract every enforcement point shares."""
    if not domain or not organization_id:
        return True
    hired = hired_agent_slugs(organization_id, db, config_db)
    if hired is None:
        return True
    if domain in hired:
        return True
    from app.services.functions.catalog import MEMBER_SLUG

    owned = {
        slug
        for a in _agent_rows(_catalog(config_db))
        for slug in a.composition["owns_agents"]
    }
    # Norm Functions' member is gated before its row exists too: it is hired
    # only while the org's functions_mode is on (_with_functions_stand_in).
    owned.add(MEMBER_SLUG)
    # A domain no agent row owns is unhireable, therefore ungateable.
    return domain not in owned
