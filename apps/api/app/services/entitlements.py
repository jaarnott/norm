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
    2. prompt_builder tools   -> tool_users() + apps_reachable(): a tool
                                 reaches the agent iff some App that gives it
                                 belongs to a hired agent (23 Oct 2026: an
                                 App's own switch governs only its screens)
    3. mcp projection         -> inherits (2): project_tools gates through
                                 _collect_tools, so no third code path exists.

Hierarchy v2 (Sep 2026 — AI Team Members -> Apps -> Components -> Connections)
and Apps v3 (28 Sep 2026): ``tier='agent'`` rows are the hireable team members
(owns_agents joins them to agent_configs; hiring one is the entitlement);
``tier='app'`` rows are the Apps, each naming its ONE member in
composition["member"]; ``tier='team'`` rows (Oct 2026) are the team apps venue
staff see, which own nothing and only this module's ``team_tools`` reads.
Every App has an enabled state: hiring a member enables
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
from dataclasses import dataclass

from sqlalchemy.orm import Session
from app.services import active_org

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
    """The organisation the user is acting in: the one the request asked for when
    they belong to it, else their oldest membership (services/active_org.py)."""
    if not user_id:
        return None

    m = active_org.membership_for(db, user_id)
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
    return _mode_off_wins(entitled, organization_id, db)


def _mode_off_wins(
    entitled: set[str], organization_id: str | None, db: Session
) -> set[str]:
    """A native module's catalog rows count only while its switch on the
    organisation is on: ``off`` beats any entitlement, so an organisation that
    hasn't been switched on is neither shown the App nor billed for it
    (billing reads this same set). Norm Functions' team member and App go
    with ``functions_mode``; the three Finance Apps (Payroll, Entertainers,
    Budget — 24 Oct 2026) each follow their own mode (``payroll_mode``,
    ``entertainers_mode``, ``finance_mode``) — ``finance_core.catalog``
    decides those, and the two old Finance rows while they still exist.
    A mode is only read when one of the rows is entitled."""
    from app.services.functions.catalog import CATALOG_SLUGS

    if entitled & CATALOG_SLUGS:
        from app.services.functions.entitlement import functions_mode

        if functions_mode(db, organization_id) == "off":
            entitled = entitled - CATALOG_SLUGS
    try:
        from app.services.finance_core.catalog import entitled_after_modes
    except ImportError:  # pragma: no cover — the Finance catalog ships with F1
        return entitled
    return entitled_after_modes(entitled, organization_id, db)


# The old name, for anything that still imports it.
_functions_off_wins = _mode_off_wins


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

#: The job apps that replace each system-named App (owner, 23 Oct 2026 —
#: docs/plans/agents-and-apps-restructure.md). The code that names the new
#: apps ships before the catalog switches to them, so until a new App has a
#: catalog row of its own it counts as on exactly when the App it comes from
#: is (apps_on). Remove once the switch has run everywhere.
SUCCESSORS: dict[str, tuple[str, ...]] = {
    "loaded-stock": ("ordering", "invoice-receiving", "stocktakes", "supplier-tenders"),
    "loaded-kitchen": ("recipes-and-costing", "menu-manager", "menu-engineering"),
    "loaded-time": ("rosters", "attendance"),
    "loaded-reports": ("sales-analysis", "weekly-report"),
    "xero": ("bills", "profit-and-loss"),
    "bamboohr-app": ("employee-records",),
}


def _with_successors(on: set[str], catalog_slugs: set[str]) -> set[str]:
    for old, new in SUCCESSORS.items():
        if old in on:
            on.update(n for n in new if n not in catalog_slugs)
    return on


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


def app_tools(app_row) -> list[str]:
    """Every tool an App gives its agent: the ones it owns (``tools``) and the
    ones it uses from other Apps (``uses_tools``) — stock levels are owned by
    Stocktakes and used by Ordering and Recipes & Costing (owner, 23 Oct 2026:
    a tool can belong to more than one App)."""
    comp = app_row.composition or {}
    owned = list(comp.get("tools") or [])
    return owned + [k for k in comp.get("uses_tools") or [] if k not in owned]


def tool_users(config_db: Session) -> dict[str, set[str]]:
    """``connector.action`` -> every App slug that gives the tool to its
    agent, owner and users alike. Exposure reads this; labels, approvals and
    coverage read the one owner (tool_owners)."""
    users: dict[str, set[str]] = {}
    for a in _catalog(config_db):
        if a.tier in ("app", "user"):
            for key in app_tools(a):
                users.setdefault(key, set()).add(a.slug)
    return users


@dataclass(frozen=True)
class AppInfo:
    """What a role check needs to know about one catalog App: its slug, the
    org-built app it fronts (``composition.app_slug``, e.g. ``training``) and
    the AI agent it belongs to (``composition.member``)."""

    slug: str
    app_slug: str | None
    member: str | None

    @property
    def key(self) -> str:
        """The name a role's ``apps`` list reaches this App by: the app it
        fronts when it fronts one, else its own catalog slug."""
        return self.app_slug or self.slug


def app_index(config_db: Session) -> dict[str, AppInfo]:
    """Catalog App slug -> AppInfo, for every App that can own tools (the
    ``app`` and ``user`` tiers). The role filter in prompt_builder looks a
    tool's owner up here (services/role_access.py)."""
    out: dict[str, AppInfo] = {}
    for a in _catalog(config_db):
        if a.tier in ("app", "user"):
            comp = a.composition or {}
            out[a.slug] = AppInfo(
                slug=a.slug,
                app_slug=(comp.get("app_slug") or None),
                member=app_member(a),
            )
    return out


def tool_owners(config_db: Session) -> dict[str, str]:
    """``connector.action`` -> the ONE App slug that owns the tool (lists it
    in ``tools``; others may use it too, tool_users). This is the App Map: a
    tool no App owns never reaches the agent or MCP."""
    owners: dict[str, str] = {}
    for a in _catalog(config_db):
        if a.tier in ("app", "user"):
            for key in (a.composition or {}).get("tools") or []:
                owners.setdefault(key, a.slug)
    return owners


def team_tools(config_db: Session) -> dict[str, set[str]]:
    """Team app slug -> the tools Norm may use for a person whose role reaches
    only that team app (a ``tier='team'`` row's ``uses_tools``; docs/plans/
    team-apps-skeleton.md §3.2). A team app owns nothing: each of these tools
    is owned by some App, and it reaches the person only while an App giving
    it is reachable for the organisation (prompt_builder._role_blocked_tools).
    Empty while the catalog has no team rows. Team rows are ignored by every
    other reader here (tool_users, tool_owners, apps_on, apps_reachable)."""
    out: dict[str, set[str]] = {}
    for a in _catalog(config_db):
        if a.tier == "team":
            out[a.slug] = set((a.composition or {}).get("uses_tools") or [])
    return out


def skill_owners(config_db: Session) -> dict[str, str]:
    """Playbook slug -> the ONE App slug that owns the skill."""
    owners: dict[str, str] = {}
    for a in _catalog(config_db):
        if a.tier in ("app", "user"):
            for slug in (a.composition or {}).get("skills") or []:
                owners.setdefault(slug, a.slug)
    return owners


def apps_reachable(
    organization_id: str | None, db: Session, config_db: Session
) -> set[str] | None:
    """The Apps whose tools and skills Norm may use for this org, or None when
    gating is inactive: every App whose AI agent is hired — an App that's on
    by default whether or not an owner switched it off (its switch takes its
    screens out of the menus; a hired agent keeps every tool of its Apps,
    owner 23 Oct 2026), and an opt-in App (bundled=False: the Finance Apps)
    once the organisation has switched it on. A module's mode still wins:
    Functions off takes its App's tools away, and each of the three Finance
    Apps follows its own mode. A published community App counts while the
    org has it."""
    apps = _catalog(config_db)
    if not _agent_rows(apps) or not organization_id:
        return None
    hired = hired_agent_slugs(organization_id, db, config_db) or set()
    entitled = entitled_slugs(organization_id, db, config_db)  # modes applied
    # On-by-default Apps a module's mode doesn't veto, switched off or not.
    bundled = _mode_off_wins(
        {a.slug for a in apps if a.tier == "app" and a.bundled},
        organization_id,
        db,
    )
    out: set[str] = set()
    for a in apps:
        if a.tier == "app" and _catalog_app_members(a) & hired:
            if a.slug in entitled or a.slug in bundled or not is_switchable(a):
                out.add(a.slug)
        elif a.tier == "user" and a.slug in entitled:
            out.add(a.slug)
    out = _with_successors(out, {a.slug for a in apps})
    from app.services.finance_core.catalog import adjust_apps_on as finance_apps_on
    from app.services.functions.entitlement import adjust_apps_on

    out = adjust_apps_on(out, organization_id, db, apps)
    return finance_apps_on(out, organization_id, db, apps, hired=hired)


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
    on = _with_successors(on, {a.slug for a in apps})
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
    # entitlement.py has the rule). The three Finance Apps (24 Oct 2026),
    # each following its own mode, stand in the same way while the Finance
    # agent is hired (services/finance_core/catalog.py — which also bridges
    # the two old Finance rows; they are deliberately not in SUCCESSORS,
    # which ignores modes).
    from app.services.finance_core.catalog import adjust_apps_on as finance_apps_on
    from app.services.functions.entitlement import adjust_apps_on

    on = adjust_apps_on(on | custom_on, organization_id, db, apps)
    return finance_apps_on(on, organization_id, db, apps, hired=hired)


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
