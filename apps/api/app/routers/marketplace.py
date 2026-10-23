"""The app marketplace — browse the catalog, enable/disable Apps per org.

An **App** is the one user-facing unit (integration apps like Loaded beside
platform apps like Hiring — docs/apps-marketplace-plan.md). Browsing is open to
any signed-in member; changing an org's entitlements is gated on
``billing:manage``, which only the Owner role carries — enabling a paid app IS
a billing act, so the existing scope is the gate and no new scope exists.

Disabling is a visibility/billing act, never a deletion: the entitlement row
flips, data stays, re-enabling restores the app over it. Enforcement happens in
``services/entitlements.py`` (agent gate + prompt_builder filter, which the MCP
projection inherits).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_permission
from app.db.engine import get_config_db, get_db
from app.db.models import OrgAppEntitlement, User
from app.services.entitlements import entitled_slugs, org_id_for_user

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/marketplace")
async def list_marketplace(
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Every active catalog App with this org's effective state and what
    enabling it lights up (the composition — pages, components, agents)."""
    from app.db.config_models import MarketplaceApp

    org_id = org_id_for_user(user.id, db)
    entitled = entitled_slugs(org_id, db, config_db) if org_id else set()
    rows = (
        config_db.query(MarketplaceApp)
        .order_by(MarketplaceApp.tier, MarketplaceApp.name)
        .all()
    )
    # Pending submissions are visible to platform admins (who approve) and to
    # the org that submitted them; everyone else sees only active apps.
    apps = [
        a
        for a in rows
        if a.status == "active"
        or (
            a.status == "pending"
            and (
                user.role == "admin"
                or (a.composition or {}).get("origin_org") == org_id
            )
        )
    ]
    return {
        "organization_id": org_id,
        "apps": [
            {
                "slug": a.slug,
                "name": a.name,
                "description": a.description,
                "icon": a.icon,
                "tier": a.tier,
                "bundled": a.bundled,
                "price_cents": a.price_cents,
                "status": a.status,
                "enabled": a.slug in entitled,
                "composition": a.composition or {},
            }
            for a in apps
        ],
    }


def _set_enabled(
    slug: str, enabled: bool, db: Session, config_db: Session, user: User
) -> dict:
    from app.db.config_models import MarketplaceApp
    from app.services.entitlements import CUSTOM_PREFIX

    org_id = org_id_for_user(user.id, db)
    if not org_id:
        raise HTTPException(400, "You are not a member of an organization.")
    if slug.startswith(CUSTOM_PREFIX):
        # One of the org's own App-platform apps: same switch, same row
        # shape, keyed custom:<slug>. Only an app IN the caller's org — an app
        # slug is unique per org, never across orgs.
        from app.db.models import App

        exists = (
            db.query(App)
            .filter(
                App.organization_id == org_id,
                App.slug == slug[len(CUSTOM_PREFIX) :],
                App.archived_at.is_(None),
            )
            .first()
        )
        if not exists:
            raise HTTPException(404, f"No app '{slug[len(CUSTOM_PREFIX) :]}'")
    else:
        app = (
            config_db.query(MarketplaceApp)
            .filter(MarketplaceApp.slug == slug, MarketplaceApp.status == "active")
            .first()
        )
        if not app:
            raise HTTPException(404, f"No marketplace app '{slug}'")
        from app.services.entitlements import is_switchable

        if app.tier == "app" and not is_switchable(app):
            raise HTTPException(
                400, f"{app.name} is always on while its AI agent is hired."
            )
    row = (
        db.query(OrgAppEntitlement)
        .filter(
            OrgAppEntitlement.organization_id == org_id,
            OrgAppEntitlement.app_slug == slug,
        )
        .first()
    )
    if row is None:
        row = OrgAppEntitlement(organization_id=org_id, app_slug=slug, enabled=enabled)
        db.add(row)
    else:
        row.enabled = enabled
    db.commit()
    logger.info(
        "marketplace_%s",
        "enable" if enabled else "disable",
        extra={"org_id": org_id, "app": slug, "by": user.id},
    )
    # An AI agent's $10 rides on the subscription: hiring or retiring one
    # updates the Stripe quantity (best effort; logged, never raised).
    if not slug.startswith(CUSTOM_PREFIX) and app.tier == "agent":
        from app.services.billing_service import sync_agent_count

        sync_agent_count(db, org_id, config_db)
    return {"slug": slug, "enabled": enabled}


def _switch(db: Session, org_id: str, key: str, enabled: bool) -> None:
    row = (
        db.query(OrgAppEntitlement)
        .filter(
            OrgAppEntitlement.organization_id == org_id,
            OrgAppEntitlement.app_slug == key,
        )
        .first()
    )
    if row is None:
        db.add(OrgAppEntitlement(organization_id=org_id, app_slug=key, enabled=enabled))
    else:
        row.enabled = enabled
    db.commit()


@router.post("/marketplace/screens/{page_id}/{action}")
def set_screen(
    page_id: str,
    action: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(require_permission("billing:manage")),
):
    """Switch one of an App's screens on or off for the organisation — so it
    can take hiring from BambooHR and reviews from Hospo HR (owner, 23 Oct
    2026; services/screens.py). Only a catalog App's own screen."""
    from app.services.entitlements import _catalog
    from app.services.screens import SCREEN_PREFIX, app_screens

    if action not in ("enable", "disable"):
        raise HTTPException(404, "enable or disable")
    org_id = org_id_for_user(user.id, db)
    if not org_id:
        raise HTTPException(400, "You are not a member of an organization.")
    known = {
        sc["page_id"]
        for row in _catalog(config_db)
        if row.tier == "app"
        for sc in app_screens(row)
    }
    if page_id not in known:
        raise HTTPException(404, f"No screen '{page_id}'")
    _switch(db, org_id, f"{SCREEN_PREFIX}{page_id}", action == "enable")
    return {"page_id": page_id, "enabled": action == "enable"}


@router.post("/connections/built-in/{system}/{action}")
def set_built_in_connection(
    system: str,
    action: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("settings:connectors")),
):
    """Turn Hospo HR on or off for the organisation: connected like BambooHR,
    but with nothing to enter (services/screens.py). Hospo Functions follows
    the organisation's Functions setting instead, so it isn't switched here."""
    from app.services.screens import CONNECTION_PREFIX

    if system != "hospo-hr":
        raise HTTPException(
            404, "Only Hospo HR is switched here; Hospo Functions follows Functions."
        )
    if action not in ("enable", "disable"):
        raise HTTPException(404, "enable or disable")
    org_id = org_id_for_user(user.id, db)
    if not org_id:
        raise HTTPException(400, "You are not a member of an organization.")
    _switch(db, org_id, f"{CONNECTION_PREFIX}{system}", action == "enable")
    return {"system": system, "enabled": action == "enable"}


@router.post("/marketplace/{slug}/enable")
async def enable_app(
    slug: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(require_permission("billing:manage")),
):
    return _set_enabled(slug, True, db, config_db, user)


@router.post("/marketplace/{slug}/disable")
async def disable_app(
    slug: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(require_permission("billing:manage")),
):
    return _set_enabled(slug, False, db, config_db, user)


class SubmitRequest(BaseModel):
    app_slug: str


@router.post("/marketplace/submit")
async def submit_app(
    req: SubmitRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(require_permission("billing:manage")),
):
    """Publish a user-built app into the marketplace as a pending submission.

    The 'deliberate promotion' the App model anticipated (models.py): an
    org-scoped App becomes a global catalog row only through this explicit,
    owner-gated act, and goes live only after platform-admin approval. The
    catalog row derives its connections from the app version's declared
    actions, so the marketplace can show connection readiness like any other
    app.
    """
    from app.db.config_models import MarketplaceApp
    from app.db.models import App, AppVersion

    org_id = org_id_for_user(user.id, db)
    if not org_id:
        raise HTTPException(400, "You are not a member of an organization.")
    # Norm's own apps (services/builtin_apps.py) are already in every org —
    # there is nothing of the org's to publish, only a leftover copy.
    from app.services.builtin_apps import reserved_slugs

    if req.app_slug in reserved_slugs():
        raise HTTPException(
            400, f"'{req.app_slug}' is built into Norm — it can't be published."
        )
    app_row = (
        db.query(App)
        .filter(
            App.organization_id == org_id,
            App.slug == req.app_slug,
            App.archived_at.is_(None),
        )
        .first()
    )
    if not app_row:
        raise HTTPException(404, f"No app '{req.app_slug}' in your organization.")
    if app_row.kind == "dashboard":
        raise HTTPException(
            400,
            f"'{app_row.name}' is a dashboard, and dashboards stay inside the "
            "business. To publish it, make it an app first.",
        )
    # The marketplace files every app under its AI agent; one made before
    # apps had to name an agent picks one on its next save first.
    if not app_row.agent:
        raise HTTPException(
            400,
            f"'{app_row.name}' isn't under an AI agent yet. Ask the App Builder "
            "to put it under one, then publish it.",
        )
    version = (
        db.query(AppVersion).filter(AppVersion.id == app_row.current_version_id).first()
    )
    spec = (version.spec if version else {}) or {}
    connections = sorted(
        {a.get("connector") for a in spec.get("actions") or [] if a.get("connector")}
    )
    slug = f"{req.app_slug}-{org_id[:8]}"
    existing = (
        config_db.query(MarketplaceApp).filter(MarketplaceApp.slug == slug).first()
    )
    composition = {
        "app_slug": req.app_slug,
        "origin_org": org_id,
        "connections": connections,
        "agents": [app_row.agent],
        "components": [],
    }
    if existing:
        if existing.status == "active":
            return {"slug": slug, "status": "active"}
        existing.status = "pending"
        existing.name = app_row.name
        existing.description = app_row.description or ""
        existing.icon = app_row.icon
        existing.composition = composition
    else:
        config_db.add(
            MarketplaceApp(
                slug=slug,
                name=app_row.name,
                description=app_row.description or "",
                icon=app_row.icon,
                tier="user",
                bundled=False,
                price_cents=0,
                status="pending",
                composition=composition,
            )
        )
    config_db.commit()
    logger.info("marketplace_submit", extra={"org_id": org_id, "app": req.app_slug})
    return {"slug": slug, "status": "pending"}


@router.get("/public/marketplace")
def public_marketplace(config_db: Session = Depends(get_config_db)):
    """The App Marketplace, from the live catalog (owner, 23 Oct 2026): the AI
    agents, each with the Apps the marketplace lists (those with a
    ``listing``), and the systems they work with. No login, so only what the
    public page shows. Venues' published Apps come from
    /public/marketplace/apps."""
    from app.services.agent_catalog import INCLUDED_MEMBERS
    from app.services.entitlements import _catalog
    from app.services.screens import BUILT_IN, SYSTEMS, system_name

    rows = _catalog(config_db)
    agents = [
        {
            "slug": (r.composition or {}).get("owns_agents", [r.slug])[0],
            "name": r.name,
            "tagline": (r.composition or {}).get("tagline") or r.description,
            "price_cents": r.price_cents or 0,
        }
        for r in rows
        if r.tier == "agent" and (r.composition or {}).get("owns_agents")
    ] + [
        {
            "slug": m["slug"],
            "name": m["name"],
            "tagline": m["tagline"],
            "price_cents": 0,
        }
        for m in INCLUDED_MEMBERS
    ]
    apps = []
    for r in rows:
        comp = r.composition or {}
        if r.tier != "app" or not comp.get("listing"):
            continue
        apps.append(
            {
                "slug": r.slug,
                "name": r.name,
                "agent": comp.get("member"),
                "description": r.description or "",
                "listing": comp["listing"],
                "works_with": [system_name(s) for s in comp.get("works_with") or []],
                "price_cents": r.price_cents or 0,
            }
        )
    used = {s for a in apps for s in a["works_with"]}
    connections = [
        {"key": k, "name": name, "built_in": k in BUILT_IN}
        for k, (name, _) in SYSTEMS.items()
        if name in used
    ]
    return {"agents": agents, "apps": apps, "connections": connections}


@router.get("/public/marketplace/apps")
def public_marketplace_apps(config_db: Session = Depends(get_config_db)):
    """Apps people built in Norm and published, once approved — for the public
    App Marketplace page (apps/web/app/(marketing)/marketplace), which files
    each under its AI agent beside Norm's own. No login, so only what that
    page shows: never the organisation that made it (owner, 21 Oct 2026)."""
    from app.db.config_models import MarketplaceApp

    rows = (
        config_db.query(MarketplaceApp)
        .filter(MarketplaceApp.tier == "user", MarketplaceApp.status == "active")
        .order_by(MarketplaceApp.name)
        .all()
    )
    out = []
    for row in rows:
        comp = row.composition or {}
        agent = next((a for a in comp.get("agents") or [] if a), None)
        if not agent:
            continue  # published before every app had one: nowhere to file it
        out.append(
            {
                "slug": row.slug,
                "name": row.name,
                "description": row.description or "",
                "agent": agent,
                "connections": list(comp.get("connections") or []),
                "price_cents": row.price_cents or 0,
            }
        )
    return {"apps": out}


@router.post("/marketplace/{slug}/approve")
async def approve_app(
    slug: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(require_permission("admin:system")),
):
    """Platform-admin approval: a pending submission goes live in the catalog."""
    from app.db.config_models import MarketplaceApp

    app = config_db.query(MarketplaceApp).filter(MarketplaceApp.slug == slug).first()
    if not app:
        raise HTTPException(404, f"No marketplace app '{slug}'")
    if app.tier != "user":
        raise HTTPException(400, "Only user submissions need approval.")
    app.status = "active"
    config_db.commit()
    logger.info("marketplace_approve", extra={"app": slug, "by": user.id})
    return {"slug": slug, "status": "active"}
