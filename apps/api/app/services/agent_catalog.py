"""The team catalog — AI Team Members, the Apps they use, and what each App
needs connected.

Hierarchy v2 (plan of 27 Sep 2026): Organisation -> AI Team Members -> Apps ->
Components -> Connections. This module answers the storefront's questions from
the marketplace catalog + component plumbing, so requirements are COMPUTED,
never hand-maintained:

    a component may require zero, one or more Connections
    an App inherits the combined Connection requirements of its Components
    a team member's WORKS WITH line is the union over the Apps it uses

Config-DB only (three small queries, 60s TTL cache) — deliberately no main-DB
dependency, so /api/team stays cheap and the answers are the same for every
org; org-specific state (hired, enabled) is layered on by the caller from
entitlements.
"""

from __future__ import annotations

import logging
import time

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Pipes that are Norm's own machinery. They never appear as a user-facing
# requirement: `internal` execution-mode specs need no credential at all, and
# cook_brothers_app is managed by Norm — when it does need attention it is
# surfaced under a Norm name, not the company brand.
INTERNAL_BRANDS = {"cook_brothers_app": "Norm Kitchen Sync"}

_CACHE_TTL_SECONDS = 60
_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, build):
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    value = build()
    _cache[key] = (now + _CACHE_TTL_SECONDS, value)
    return value


def invalidate_cache() -> None:
    """Tests and the sync scripts call this after touching the catalog."""
    _cache.clear()


def _catalog_rows(config_db: Session) -> list:
    from app.services.entitlements import _catalog

    return _catalog(config_db)


def _internal_connectors(config_db: Session) -> set[str]:
    """Connector names that are Norm-internal plumbing (no credential)."""

    def build() -> set[str]:
        from app.db.config_models import ConnectionSpec

        try:
            rows = config_db.query(
                ConnectionSpec.connector_name, ConnectionSpec.auth_type
            ).all()
        except Exception:  # pragma: no cover — transient config-DB issue
            logger.warning("connection specs unavailable — assuming none internal")
            return set()
        # auth_type "none" = Norm's own pipe, nothing for anyone to connect.
        # (Not execution_mode: Gmail is executed in-process but still needs
        # each user to connect their account.)
        return {name for (name, auth) in rows if auth == "none"}

    return _cached("internal_connectors", build)


def _component_api_connectors(config_db: Session) -> dict[str, set[str]]:
    """component_key -> connector names its component-api rows call."""

    def build() -> dict[str, set[str]]:
        from app.db.config_models import ComponentApiConfig

        out: dict[str, set[str]] = {}
        try:
            rows = config_db.query(
                ComponentApiConfig.component_key, ComponentApiConfig.connector_name
            ).all()
        except Exception:  # pragma: no cover
            logger.warning("component_api_configs unavailable — no derived connectors")
            return {}
        for key, connector in rows:
            out.setdefault(key, set()).add(connector)
        return out

    return _cached("component_api_connectors", build)


def required_connections_for_app(app_row, config_db: Session) -> set[str]:
    """The Connections an App needs, inherited from its components.

    Per component: its component_api rows' connectors, union an explicit
    `connections: [...]` on the component entry (self-loading components —
    supplier_tenders reads through the CB App, invoices_dashboard hits
    /invoice-fixes which reads Loaded — have no component_api rows to derive
    from). An app_slug-shaped App (App-platform pointer) or a published
    community App carries an explicit composition-level `connections` list
    instead (the submit flow already derives it from declared actions); a
    Norm-native storage App legitimately requires NOTHING. Norm-internal
    pipes are excluded — they are not the user's to connect.
    """
    comp = app_row.composition or {}
    # Two spellings feed DISPLAY requirements: legacy top-level `connections`
    # and `component_connections` — the latter exists so a requirement can be
    # declared without also feeding the unentitled_connectors BLOCKING filter
    # (single-claimer rows would let one App switch black-hole a connector).
    required: set[str] = set(comp.get("connections") or [])
    required |= set(comp.get("component_connections") or [])
    by_component = _component_api_connectors(config_db)
    for entry in comp.get("components") or []:
        if not isinstance(entry, dict):
            continue
        required |= set(entry.get("connections") or [])
        if entry.get("key"):
            required |= by_component.get(entry["key"], set())
    # Apps v3: an App's tools need their connectors too (Loaded Reports has
    # no components at all — its need comes from get_sales & co).
    for key in comp.get("tools") or []:
        required.add(str(key).split(".", 1)[0])
    # Per-user mailboxes (Norm Core's gmail.send_email) are each user's own
    # optional connection, not something a venue has to connect.
    from app.agents.prompt_builder import USER_SCOPED_CONNECTORS

    required -= USER_SCOPED_CONNECTORS
    internal = _internal_connectors(config_db)
    return {c for c in required if c not in internal or c in INTERNAL_BRANDS}


def connection_display_name(connector_name: str, config_db: Session) -> str:
    """The user-facing name for a connection requirement."""
    if connector_name in INTERNAL_BRANDS:
        return INTERNAL_BRANDS[connector_name]

    def build() -> dict[str, str]:
        from app.db.config_models import ConnectionSpec

        try:
            rows = config_db.query(
                ConnectionSpec.connector_name, ConnectionSpec.display_name
            ).all()
        except Exception:  # pragma: no cover
            return {}
        return {name: display for (name, display) in rows}

    names: dict[str, str] = _cached("connector_display_names", build)
    return names.get(connector_name, connector_name)


# The always-included members get cards too (owner decision 27 Sep): free,
# always on, never retired — but they have Apps like anyone else.
INCLUDED_MEMBERS = [
    {
        "slug": "reports",
        "name": "Reports",
        "tagline": "Sales, labour, budgets and COGS analysis; dashboards, saved reports and scheduled report tasks.",
    },
    {
        "slug": "app_builder",
        "name": "App Builder",
        "tagline": "Builds custom Apps for your team by chatting — describe what you need and it makes it.",
    },
]

# The server-side mirror of apps/web/app/components/display/DisplayBlockRenderer
# REGISTRY — the components that exist. tests/test_app_map.py pins it against
# the web file so the two can't drift.
REGISTERED_COMPONENTS = frozenset(
    {
        "generic_table",
        "roster_table",
        "purchase_order_editor",
        "roster_editor",
        "criteria_editor",
        "hiring_board",
        "automated_task_preview",
        "automated_task_board",
        "chart",
        "report_builder",
        "saved_reports_board",
        "orders_dashboard",
        "invoices_dashboard",
        "apps_dashboard",
        "app_runner",
        "menu_editor",
        "recipe_editor",
        "menu_engineering",
        "supplier_tenders",
        "tool_approval",
        "receive_invoice_editor",
        "venue_picker",
        "stock_picker",
        "dashboard_view",
        "mcp_embed",
        "connector_connect",
    }
)


def _first_sentence(text: str) -> str:
    import re

    t = re.sub(r"^(\[[^\]]*\]\s*)+", "", (text or "").strip())
    return t.split(". ")[0].strip().rstrip(".")[:180]


class _CapabilityContext:
    """Everything needed to describe tools/components/skills, loaded once."""

    def __init__(self, config_db: Session):
        from app.agents.internal_tools import registered_actions
        from app.db.config_models import (
            AgentConnectionBinding,
            ConnectionSpec,
            Playbook,
        )

        self.specs = {
            s.connector_name: s for s in config_db.query(ConnectionSpec).all()
        }
        self.tool_defs: dict[str, dict] = {}
        for name, spec in self.specs.items():
            for t in spec.tools or []:
                if isinstance(t, dict) and t.get("action"):
                    self.tool_defs[f"{name}.{t['action']}"] = t
        self.labels: dict[str, str] = {}
        for b in config_db.query(AgentConnectionBinding).all():
            for cap in b.capabilities or []:
                if isinstance(cap, dict) and cap.get("action") and cap.get("label"):
                    self.labels.setdefault(
                        f"{b.connector_name}.{cap['action']}", cap["label"]
                    )
        self.handlers = registered_actions()
        self.playbooks = {pb.slug: pb for pb in config_db.query(Playbook).all()}

    def tool(self, key: str) -> dict:
        from app.services.consolidator_coverage import _classify

        conn, _, action = key.partition(".")
        t = self.tool_defs.get(key, {})
        spec = self.specs.get(conn)
        if t.get("consolidator_config"):
            kind = _classify(t)  # consolidator (or a demoted backend)
        elif (conn, action) in self.handlers or (spec and spec.auth_type == "none"):
            kind = "norm function"
        elif spec and spec.execution_mode == "mcp":
            kind = "CB tool"
        else:
            kind = "raw"
        method = str(t.get("method") or "GET").upper()
        return {
            "key": key,
            "label": self.labels.get(key) or action.replace("_", " ").capitalize(),
            "description": _first_sentence(t.get("description") or ""),
            "writes": method != "GET" and not t.get("read_only"),
            "type": kind,
            "exists": bool(t),
            "engine_only": bool(t.get("engine_only")),
        }

    def skill(self, slug: str) -> dict:
        pb = self.playbooks.get(slug)
        return {
            "slug": slug,
            "label": pb.display_name if pb else slug,
            "enabled": bool(pb and pb.enabled),
        }


def _component_entry(entry: dict) -> dict:
    page = entry.get("page")
    return {
        "key": entry.get("key"),
        "label": (page or {}).get("label")
        or str(entry.get("key", "")).replace("_", " ").title(),
        "description": entry.get("description") or "",
        "page": page,
        "shared": bool(entry.get("shared")),
    }


def _app_platform_info(
    app_slug: str | None, db: Session | None, organization_id: str | None
) -> dict:
    """What an App-platform app's CURRENT version declares: the collections of
    data it keeps, and its components — the screens Norm may open directly,
    with the inputs that say where each starts (services/app_components.py).
    Derived per request, so the App Map can't drift from the running app."""
    empty = {"data": [], "components": []}
    if not app_slug:
        return empty
    from app.db.models import App, AppVersion
    from app.services.app_components import declared_components
    from app.services.builtin_apps import get_builtin

    try:
        # Built into Norm: the same code for every org — no org row to read.
        builtin = get_builtin(app_slug)
        if builtin is not None:
            app = builtin
            spec = builtin.spec
        else:
            if db is None or not organization_id:
                return empty
            app = (
                db.query(App)
                .filter(App.organization_id == organization_id, App.slug == app_slug)
                .first()
            )
            if not app or not app.current_version_id:
                return empty
            ver = (
                db.query(AppVersion)
                .filter(AppVersion.id == app.current_version_id)
                .first()
            )
            spec = (ver.spec if ver else None) or {}
        return {
            "data": list((spec.get("storage") or {}).get("collections") or []),
            "components": [
                {
                    "key": c["key"],
                    "label": c["label"],
                    "description": c["description"],
                    # the app's menu item is placed from the App row itself
                    "page": None,
                    "shared": False,
                    "app_page": c["page"],
                    "inputs": c["inputs"],
                }
                for c in declared_components(
                    spec, slug=app.slug, name=app.name, description=app.description
                )
            ],
        }
    except Exception:  # pragma: no cover
        return empty


def _custom_apps(organization_id: str | None, db: Session) -> list:
    if not organization_id:
        return []
    from app.db.models import App

    from app.services.builtin_apps import reserved_slugs

    try:
        return [
            a
            for a in db.query(App)
            .filter(App.organization_id == organization_id, App.archived_at.is_(None))
            .order_by(App.name)
            .all()
            # a leftover per-org copy of a built-in is not the org's own app
            if a.slug not in reserved_slugs()
        ]
    except Exception:  # pragma: no cover
        logger.warning("custom apps unavailable")
        return []


def _custom_required_connections(app, db: Session, config_db: Session) -> set[str]:
    """A custom App's connection needs = the connectors its CURRENT version's
    declared actions call (its reach is per-version by design), minus Norm's
    internal pipes. A storage-only App needs nothing."""
    if not app.current_version_id:
        return set()
    from app.db.models import AppVersion

    ver = db.query(AppVersion).filter(AppVersion.id == app.current_version_id).first()
    spec = (ver.spec if ver else None) or {}
    conns = {
        a.get("connector")
        for a in spec.get("actions") or []
        if isinstance(a, dict) and a.get("connector")
    }
    internal = _internal_connectors(config_db)
    return {c for c in conns if c not in internal or c in INTERNAL_BRANDS}


def _catalog_app_entry(
    row, agent_rows, ctx, config_db, db=None, organization_id=None
) -> dict:
    """One catalog App, described: who it's bound to and what it unlocks."""
    from app.services.entitlements import ALL_MEMBERS, app_member, is_switchable

    comp = row.composition or {}
    member = app_member(row, agent_rows)
    required = required_connections_for_app(row, config_db)
    components = [
        _component_entry(e) for e in comp.get("components") or [] if isinstance(e, dict)
    ]
    platform = _app_platform_info(comp.get("app_slug"), db, organization_id)
    return {
        "slug": row.slug,
        "name": row.name,
        "description": row.description,
        "icon": row.icon,
        "kind": "community" if row.tier == "user" else "app",
        "member": member,
        "bound_to_all": member == ALL_MEMBERS,
        "switchable": is_switchable(row),
        "bundled": bool(row.bundled),
        "price_cents": row.price_cents or 0,
        "status": row.status,
        "pages": [c["page"] for c in components if c["page"]],
        "components": components + platform["components"],
        "tools": [ctx.tool(k) for k in comp.get("tools") or []],
        "skills": [ctx.skill(sl) for sl in comp.get("skills") or []],
        "data": platform["data"],
        "app_platform": bool(comp.get("app_slug")),
        "app_slug": comp.get("app_slug"),
        "required_connections": [
            {"connector": c, "display_name": connection_display_name(c, config_db)}
            for c in sorted(required)
        ],
    }


def team_payload(organization_id: str | None, db: Session, config_db: Session) -> dict:
    """Everything the team page and the sidebar need, in one response.

    Apps v3: every App is bound to ONE team member (Norm Core: all of them)
    and has an enabled state; each App carries what it unlocks — its tools,
    components, skills and connection needs — for the capability panel.

    `gating_active: false` (no tier='agent' rows yet, or no org) tells the web
    to show everything — the dark-launch/fail-open contract.
    """
    from app.services.entitlements import (
        ALL_MEMBERS,
        ALWAYS_INCLUDED_AGENTS,
        CUSTOM_PREFIX,
        apps_on,
        custom_app_owner,
        entitled_slugs,
        hired_agent_slugs,
    )

    rows = _catalog_rows(config_db)
    agent_rows = [
        a
        for a in rows
        if a.tier == "agent" and (a.composition or {}).get("owns_agents")
    ]
    hired = hired_agent_slugs(organization_id, db, config_db)
    on = apps_on(organization_id, db, config_db)
    entitled = (
        entitled_slugs(organization_id, db, config_db) if organization_id else set()
    )
    gating_active = hired is not None
    ctx = _CapabilityContext(config_db)

    def is_on(slug: str) -> bool:
        return (not gating_active) or (on is not None and slug in on)

    all_apps: list[dict] = []
    for row in rows:
        if row.tier not in ("app", "user"):
            continue
        entry = _catalog_app_entry(row, agent_rows, ctx, config_db, db, organization_id)
        entry["enabled"] = is_on(row.slug)
        all_apps.append(entry)

    # A custom app a first-party catalog row already fronts (Norm Hiring ->
    # `hiring`) is the same App — list it once, under the catalog row. Not
    # community rows: their app_slug is a bare slug from the publisher's org
    # (see entitlements.apps_on).
    fronted = {
        (r.composition or {}).get("app_slug")
        for r in rows
        if r.tier == "app" and (r.composition or {}).get("app_slug")
    }
    for app in _custom_apps(organization_id, db):
        if app.slug in fronted:
            continue
        slug = f"{CUSTOM_PREFIX}{app.slug}"
        platform = _app_platform_info(app.slug, db, organization_id)
        all_apps.append(
            {
                "slug": slug,
                "app_slug": app.slug,
                "name": app.name,
                "description": app.description,
                "icon": app.icon,
                "kind": "custom",
                "member": custom_app_owner(app),
                "bound_to_all": False,
                "switchable": True,
                "bundled": True,
                "price_cents": 0,
                "status": "active",
                "enabled": is_on(slug),
                "pages": [],
                "components": platform["components"],
                "tools": [],
                "skills": [],
                "data": platform["data"],
                "app_platform": True,
                "required_connections": [
                    {
                        "connector": c,
                        "display_name": connection_display_name(c, config_db),
                    }
                    for c in sorted(_custom_required_connections(app, db, config_db))
                ],
            }
        )
    for a in all_apps:  # back-compat for older web builds
        a["bound_to"] = (
            ["*"] if a["bound_to_all"] else ([a["member"]] if a["member"] else [])
        )

    def apps_for(member_slug: str) -> list[dict]:
        return [a for a in all_apps if a["member"] in (member_slug, ALL_MEMBERS)]

    def works_with(apps: list[dict]) -> list[str]:
        return sorted(
            {c["connector"] for app in apps for c in app["required_connections"]}
        )

    members = []
    for a in sorted(agent_rows, key=lambda r: r.slug):
        comp = a.composition or {}
        slugs = comp.get("owns_agents") or []
        slug = slugs[0] if slugs else a.slug
        apps = apps_for(slug)
        members.append(
            {
                "slug": slug,
                "catalog_slug": a.slug,
                "name": a.name,
                "icon": a.icon,
                "tagline": comp.get("tagline") or a.description,
                "hireable": True,
                "hired": (not gating_active) or slug in hired,
                "enabled_explicitly": a.slug in entitled,
                "price_cents": a.price_cents or 0,
                "apps": apps,
                "works_with": works_with(apps),
            }
        )

    included = [
        {
            **m,
            "apps": apps_for(m["slug"]),
            "works_with": works_with(apps_for(m["slug"])),
        }
        for m in INCLUDED_MEMBERS
    ]

    return {
        "gating_active": gating_active,
        "always_included": sorted(ALWAYS_INCLUDED_AGENTS - {"base", "router"}),
        "included": included,
        "members": members,
        "apps": all_apps,
    }


def _agent_visible_tools(config_db: Session) -> set[str]:
    """Every tool some enabled binding exposes to the agent (platform-wide),
    minus engine-only and code-retired ones — the set ownership must cover."""
    from app.agents.prompt_builder import _ENGINE_AND_MCP_ONLY, _RETIRED_ACTIONS
    from app.db.config_models import AgentConnectionBinding, ConnectionSpec

    specs = {s.connector_name: s for s in config_db.query(ConnectionSpec).all()}
    out: set[str] = set()
    for b in (
        config_db.query(AgentConnectionBinding)
        .filter(AgentConnectionBinding.enabled.is_(True))
        .all()
    ):  # noqa: E712
        spec = specs.get(b.connector_name)
        if not spec or not spec.enabled:
            continue
        tools = {
            t["action"]: t
            for t in spec.tools or []
            if isinstance(t, dict) and t.get("action")
        }
        caps = b.capabilities or []
        acts = (
            list(tools)
            if not caps
            else [
                c["action"]
                for c in caps
                if isinstance(c, dict) and c.get("enabled", True) and c.get("action")
            ]
        )
        for a in acts:
            t = tools.get(a)
            if (
                not t
                or t.get("engine_only")
                or a in _RETIRED_ACTIONS
                or a in _ENGINE_AND_MCP_ONLY
            ):
                continue
            out.add(f"{b.connector_name}.{a}")
    return out


def ownership_findings(config_db: Session) -> dict:
    """Apps v3 ownership, checked: what belongs to no App, what's claimed
    twice, and which members don't exist. Shared by the admin App Map's
    Unassigned panel and the config validator, so they can't disagree."""
    from app.db.config_models import AgentConfig, Playbook
    from app.services.entitlements import (
        ALL_MEMBERS,
        ALWAYS_INCLUDED_AGENTS,
        app_member,
    )

    rows = _catalog_rows(config_db)
    agent_rows = [a for a in rows if a.tier == "agent"]
    app_rows = [a for a in rows if a.tier in ("app", "user")]
    armed = any((a.composition or {}).get("tools") for a in app_rows)

    def claims(field: str, inner: str | None = None) -> dict[str, list[str]]:
        seen: dict[str, list[str]] = {}
        for a in app_rows:
            for e in (a.composition or {}).get(field) or []:
                k = e.get(inner) if (inner and isinstance(e, dict)) else e
                if k:
                    seen.setdefault(k, []).append(a.slug)
        return seen

    tool_claims = claims("tools")
    comp_claims = claims("components", "key")
    skill_claims = claims("skills")
    enabled_skills = {
        pb.slug
        for pb in config_db.query(Playbook).filter(Playbook.enabled.is_(True)).all()
    }  # noqa: E712
    known_members = {r[0] for r in config_db.query(AgentConfig.agent_slug).all()} | set(
        ALWAYS_INCLUDED_AGENTS
    )
    visible = _agent_visible_tools(config_db)

    bad_members = []
    for a in app_rows:
        m = app_member(a, agent_rows)
        if m == ALL_MEMBERS and a.slug != "norm-core":
            bad_members.append(
                {
                    "app": a.slug,
                    "member": m,
                    "problem": "only Norm Core may be bound to every member",
                }
            )
        elif m is None or (m != ALL_MEMBERS and m not in known_members):
            bad_members.append(
                {
                    "app": a.slug,
                    "member": m,
                    "problem": "not bound to an existing team member",
                }
            )

    return {
        "armed": armed,
        "unowned_tools": sorted(visible - set(tool_claims)) if armed else [],
        "unowned_components": sorted(REGISTERED_COMPONENTS - set(comp_claims))
        if comp_claims
        else [],
        "unowned_skills": sorted(enabled_skills - set(skill_claims))
        if skill_claims
        else [],
        "double_claims": sorted(
            [
                {"kind": kind, "key": k, "apps": v}
                for kind, src in (
                    ("tool", tool_claims),
                    ("component", comp_claims),
                    ("skill", skill_claims),
                )
                for k, v in src.items()
                if len(v) > 1
            ],
            key=lambda d: (d["kind"], d["key"]),
        ),
        "unknown_components": sorted(set(comp_claims) - REGISTERED_COMPONENTS),
        "bad_members": bad_members,
    }


def app_map_payload(
    config_db: Session, db: Session | None = None, organization_id: str | None = None
) -> dict:
    """The admin App Map: the platform's App → member → tools / components /
    skills / connection table, derived live (never a hand-kept copy).

    App-platform Apps (Norm Hiring, Norm Training) keep their components in the
    app's own version, which lives in an org — given the viewing admin's org,
    those are read from there; without one they're simply not listed."""
    from app.services.entitlements import app_member

    rows = _catalog_rows(config_db)
    agent_rows = [a for a in rows if a.tier == "agent"]
    ctx = _CapabilityContext(config_db)
    member_names = {
        (a.composition or {}).get("owns_agents", [a.slug])[0]: a.name
        for a in agent_rows
    }
    member_names.update({m["slug"]: m["name"] for m in INCLUDED_MEMBERS})
    member_names["*"] = "All members"
    apps = []
    for row in rows:
        if row.tier not in ("app", "user"):
            continue
        entry = _catalog_app_entry(row, agent_rows, ctx, config_db, db, organization_id)
        entry["member_name"] = member_names.get(
            app_member(row, agent_rows) or "", app_member(row, agent_rows)
        )
        apps.append(entry)
    order = {
        "*": 0,
        "reports": 1,
        "procurement": 2,
        "executive_chef": 3,
        "time_attendance": 4,
        "hr": 5,
        "marketing": 6,
        "app_builder": 7,
    }
    apps.sort(key=lambda a: (order.get(a["member"] or "", 9), a["name"]))
    return {"apps": apps, "findings": ownership_findings(config_db)}
