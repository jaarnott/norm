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
                ConnectionSpec.connector_name, ConnectionSpec.execution_mode
            ).all()
        except Exception:  # pragma: no cover — transient config-DB issue
            logger.warning("connection specs unavailable — assuming none internal")
            return set()
        return {name for (name, mode) in rows if mode == "internal"}

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


def team_payload(organization_id: str | None, db: Session, config_db: Session) -> dict:
    """Everything the team page and the sidebar need, in one response.

    `gating_active: false` (no tier='agent' rows yet, or no org) tells the web
    to show everything — the same dark-launch/fail-open contract the tool
    filter honours. Per member: hired state, price, and the Apps it uses with
    each App's enabled state and its own connection requirements.
    """
    from app.services.entitlements import (
        ALWAYS_INCLUDED_AGENTS,
        apps_on,
        entitled_slugs,
        hired_agent_slugs,
    )

    rows = _catalog_rows(config_db)
    agent_rows = [
        a
        for a in rows
        if a.tier == "agent" and (a.composition or {}).get("owns_agents")
    ]
    app_rows = {a.slug: a for a in rows if a.tier in ("app", "user")}

    hired = hired_agent_slugs(organization_id, db, config_db)
    on = apps_on(organization_id, db, config_db)
    entitled = (
        entitled_slugs(organization_id, db, config_db) if organization_id else set()
    )
    gating_active = hired is not None

    def app_entry(app_slug: str) -> dict | None:
        row = app_rows.get(app_slug)
        if row is None:
            return None
        comp = row.composition or {}
        pages = [
            e["page"]
            for e in comp.get("components") or []
            if isinstance(e, dict) and e.get("page")
        ]
        required = sorted(required_connections_for_app(row, config_db))
        return {
            "slug": row.slug,
            "name": row.name,
            "description": row.description,
            "bundled": bool(row.bundled),
            "price_cents": row.price_cents or 0,
            "enabled": (not gating_active) or (on is not None and row.slug in on),
            "pages": pages,
            "required_connections": [
                {
                    "connector": c,
                    "display_name": connection_display_name(c, config_db),
                }
                for c in required
            ],
        }

    members = []
    for a in sorted(agent_rows, key=lambda r: r.slug):
        comp = a.composition or {}
        slugs = comp.get("owns_agents") or []
        apps = [e for e in (app_entry(s) for s in comp.get("unlocks") or []) if e]
        is_hired = (not gating_active) or any(s in hired for s in slugs)
        members.append(
            {
                "slug": slugs[0] if slugs else a.slug,
                "catalog_slug": a.slug,
                "name": a.name,
                "icon": a.icon,
                "tagline": comp.get("tagline") or a.description,
                "hireable": True,
                "hired": is_hired,
                "enabled_explicitly": a.slug in entitled,
                "price_cents": a.price_cents or 0,
                "apps": apps,
                "works_with": sorted(
                    {
                        c["connector"]
                        for app in apps
                        for c in app["required_connections"]
                    }
                ),
            }
        )

    return {
        "gating_active": gating_active,
        "always_included": sorted(ALWAYS_INCLUDED_AGENTS - {"base", "router"}),
        "members": members,
    }
