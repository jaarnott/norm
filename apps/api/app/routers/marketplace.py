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

    app = (
        config_db.query(MarketplaceApp)
        .filter(MarketplaceApp.slug == slug, MarketplaceApp.status == "active")
        .first()
    )
    if not app:
        raise HTTPException(404, f"No marketplace app '{slug}'")
    org_id = org_id_for_user(user.id, db)
    if not org_id:
        raise HTTPException(400, "You are not a member of an organization.")
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
    return {"slug": slug, "enabled": enabled}


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
    app_row = (
        db.query(App)
        .filter(App.organization_id == org_id, App.slug == req.app_slug)
        .first()
    )
    if not app_row:
        raise HTTPException(404, f"No app '{req.app_slug}' in your organization.")
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
        "agents": [app_row.agent] if app_row.agent else [],
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
