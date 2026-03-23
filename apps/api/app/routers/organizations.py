"""Organization, membership, and venue management endpoints."""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models import (
    Organization,
    OrganizationMembership,
    UserVenueAccess,
    Venue,
    User,
    ConnectorConfig,
)
from app.auth.dependencies import get_current_user, require_role

router = APIRouter()


# --- Request bodies ---


class CreateOrgBody(BaseModel):
    name: str
    slug: str
    billing_email: str | None = None
    plan: str = "starter"


class UpdateOrgBody(BaseModel):
    name: str | None = None
    billing_email: str | None = None
    plan: str | None = None


class AddMemberBody(BaseModel):
    user_id: str
    role: str = "member"  # owner|admin|member


class CreateVenueBody(BaseModel):
    name: str
    location: str | None = None
    timezone: str | None = None


class UpdateVenueBody(BaseModel):
    name: str | None = None
    location: str | None = None
    timezone: str | None = None


# --- Helpers ---


def _org_to_dict(org: Organization, db: Session, include_details: bool = False) -> dict:
    d = {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "billing_email": org.billing_email,
        "plan": org.plan,
        "is_active": org.is_active,
        "venue_count": len(org.venues),
        "member_count": len(org.memberships),
        "created_at": org.created_at.isoformat() if org.created_at else None,
    }
    if include_details:
        d["venues"] = [
            {
                "id": v.id,
                "name": v.name,
                "location": v.location,
                "connector_count": db.query(ConnectorConfig)
                .filter(
                    ConnectorConfig.venue_id == v.id, ConnectorConfig.enabled == "true"
                )
                .count(),
            }
            for v in org.venues
        ]
        d["members"] = [
            {
                "id": m.id,
                "user_id": m.user_id,
                "email": m.user.email if m.user else "",
                "full_name": m.user.full_name if m.user else "",
                "role": m.role,
            }
            for m in org.memberships
        ]
    return d


def _get_user_org(user: User, db: Session) -> Organization | None:
    """Get the user's primary organization."""
    membership = (
        db.query(OrganizationMembership)
        .filter(OrganizationMembership.user_id == user.id)
        .first()
    )
    if not membership:
        return None
    return (
        db.query(Organization)
        .filter(Organization.id == membership.organization_id)
        .first()
    )


# --- Organization CRUD ---


@router.get("/organizations")
async def list_organizations(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """List organizations the current user belongs to."""
    memberships = (
        db.query(OrganizationMembership)
        .filter(OrganizationMembership.user_id == user.id)
        .all()
    )
    org_ids = [m.organization_id for m in memberships]
    orgs = (
        db.query(Organization).filter(Organization.id.in_(org_ids)).all()
        if org_ids
        else []
    )
    return {"organizations": [_org_to_dict(o, db) for o in orgs]}


@router.post("/organizations")
async def create_organization(
    body: CreateOrgBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    """Create a new organization. The creating user becomes the owner."""
    existing = db.query(Organization).filter(Organization.slug == body.slug).first()
    if existing:
        raise HTTPException(400, f"Organization with slug '{body.slug}' already exists")

    org = Organization(
        name=body.name,
        slug=body.slug,
        billing_email=body.billing_email,
        plan=body.plan,
    )
    db.add(org)
    db.flush()

    # Make the creating user the owner
    membership = OrganizationMembership(
        user_id=user.id,
        organization_id=org.id,
        role="owner",
    )
    db.add(membership)
    db.commit()

    return _org_to_dict(org, db, include_details=True)


@router.get("/organizations/{org_id}")
async def get_organization(
    org_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Get organization details with venues and members."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(404, "Organization not found")

    # Verify user is a member
    membership = (
        db.query(OrganizationMembership)
        .filter(
            OrganizationMembership.organization_id == org_id,
            OrganizationMembership.user_id == user.id,
        )
        .first()
    )
    if not membership and user.role != "admin":
        raise HTTPException(403, "Not a member of this organization")

    return _org_to_dict(org, db, include_details=True)


@router.put("/organizations/{org_id}")
async def update_organization(
    org_id: str,
    body: UpdateOrgBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update organization details."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(404, "Organization not found")

    # Verify user is owner or admin
    membership = (
        db.query(OrganizationMembership)
        .filter(
            OrganizationMembership.organization_id == org_id,
            OrganizationMembership.user_id == user.id,
            OrganizationMembership.role.in_(["owner", "admin"]),
        )
        .first()
    )
    if not membership and user.role != "admin":
        raise HTTPException(403, "Insufficient permissions")

    if body.name is not None:
        org.name = body.name
    if body.billing_email is not None:
        org.billing_email = body.billing_email
    if body.plan is not None:
        org.plan = body.plan

    db.commit()
    return _org_to_dict(org, db, include_details=True)


# --- Member management ---


@router.post("/organizations/{org_id}/members")
async def add_member(
    org_id: str,
    body: AddMemberBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Add a user to an organization."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(404, "Organization not found")

    target_user = db.query(User).filter(User.id == body.user_id).first()
    if not target_user:
        raise HTTPException(404, "User not found")

    existing = (
        db.query(OrganizationMembership)
        .filter(
            OrganizationMembership.organization_id == org_id,
            OrganizationMembership.user_id == body.user_id,
        )
        .first()
    )
    if existing:
        existing.role = body.role
    else:
        db.add(
            OrganizationMembership(
                user_id=body.user_id,
                organization_id=org_id,
                role=body.role,
            )
        )

    db.commit()
    return {"ok": True}


@router.delete("/organizations/{org_id}/members/{member_user_id}")
async def remove_member(
    org_id: str,
    member_user_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Remove a user from an organization."""
    membership = (
        db.query(OrganizationMembership)
        .filter(
            OrganizationMembership.organization_id == org_id,
            OrganizationMembership.user_id == member_user_id,
        )
        .first()
    )
    if not membership:
        raise HTTPException(404, "Membership not found")

    db.delete(membership)
    db.commit()
    return {"ok": True}


# --- Venue management within an org ---


@router.post("/organizations/{org_id}/venues")
async def create_venue(
    org_id: str,
    body: CreateVenueBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a venue within an organization."""
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(404, "Organization not found")

    venue = Venue(
        name=body.name,
        location=body.location,
        timezone=body.timezone,
        organization_id=org_id,
    )
    db.add(venue)
    db.flush()

    # Grant the creating user access to the venue
    db.add(UserVenueAccess(user_id=user.id, venue_id=venue.id))
    db.commit()

    # Sync venue count with Stripe billing
    try:
        from app.services.billing_service import sync_venue_count

        sync_venue_count(db, org_id)
        db.commit()
    except Exception:
        pass  # Don't fail venue creation if billing sync fails

    return {
        "id": venue.id,
        "name": venue.name,
        "location": venue.location,
        "timezone": venue.timezone,
        "organization_id": org_id,
    }


@router.put("/venues/{venue_id}")
async def update_venue(
    venue_id: str,
    body: UpdateVenueBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update venue details."""
    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    if not venue:
        raise HTTPException(404, "Venue not found")

    if body.name is not None:
        venue.name = body.name
    if body.location is not None:
        venue.location = body.location
    if body.timezone is not None:
        venue.timezone = body.timezone

    db.commit()
    return {
        "id": venue.id,
        "name": venue.name,
        "location": venue.location,
        "timezone": venue.timezone,
    }


@router.delete("/venues/{venue_id}")
async def delete_venue(
    venue_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    """Delete a venue and its connector configs."""
    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    if not venue:
        raise HTTPException(404, "Venue not found")

    org_id = venue.organization_id
    # Remove venue access entries
    db.query(UserVenueAccess).filter(UserVenueAccess.venue_id == venue_id).delete()
    # Remove venue connector configs
    db.query(ConnectorConfig).filter(ConnectorConfig.venue_id == venue_id).delete()
    db.delete(venue)
    db.commit()

    # Sync venue count with Stripe billing
    try:
        from app.services.billing_service import sync_venue_count

        sync_venue_count(db, org_id)
        db.commit()
    except Exception:
        pass  # Don't fail venue deletion if billing sync fails

    return {"ok": True}


@router.get("/venues/{venue_id}/connectors")
async def list_venue_connectors(
    venue_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """List connector configuration status for a specific venue."""
    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    if not venue:
        raise HTTPException(404, "Venue not found")

    configs = (
        db.query(ConnectorConfig).filter(ConnectorConfig.venue_id == venue_id).all()
    )
    return {
        "venue_id": venue_id,
        "venue_name": venue.name,
        "connectors": [
            {
                "connector_name": c.connector_name,
                "enabled": c.enabled == "true",
                "has_oauth": bool(c.access_token),
                "oauth_metadata": c.oauth_metadata,
            }
            for c in configs
        ],
    }


# --- User venue access ---


@router.get("/users/{user_id}/venues")
async def list_user_venues(
    user_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """List venues a user has access to."""
    access = db.query(UserVenueAccess).filter(UserVenueAccess.user_id == user_id).all()
    venue_ids = [a.venue_id for a in access]
    venues = db.query(Venue).filter(Venue.id.in_(venue_ids)).all() if venue_ids else []
    return {
        "venues": [{"id": v.id, "name": v.name, "location": v.location} for v in venues]
    }


@router.put("/users/{user_id}/venues")
async def set_user_venues(
    user_id: str,
    body: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Set venue access for a user. Body: {"venue_ids": ["v1", "v2"]}."""
    venue_ids = body.get("venue_ids", [])

    # Remove existing access
    db.query(UserVenueAccess).filter(UserVenueAccess.user_id == user_id).delete()

    # Add new access
    for vid in venue_ids:
        db.add(UserVenueAccess(user_id=user_id, venue_id=vid))

    db.commit()
    return {"ok": True, "venue_count": len(venue_ids)}


@router.get("/organizations/{org_id}/usage")
async def get_usage(
    org_id: str,
    month: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get monthly token usage summary for an organization."""
    from app.db.models import TokenUsage
    import datetime as dt

    if not month:
        month = dt.date.today().strftime("%Y-%m")

    rows = (
        db.query(TokenUsage)
        .filter(
            TokenUsage.organization_id == org_id,
            TokenUsage.date.like(f"{month}%"),
        )
        .all()
    )

    total_input = sum(r.input_tokens or 0 for r in rows)
    total_output = sum(r.output_tokens or 0 for r in rows)
    total_calls = sum(r.llm_call_count or 0 for r in rows)

    # Per-user breakdown
    by_user: dict[str, dict] = {}
    for r in rows:
        uid = r.user_id or "system"
        if uid not in by_user:
            by_user[uid] = {"input_tokens": 0, "output_tokens": 0, "llm_call_count": 0}
        by_user[uid]["input_tokens"] += r.input_tokens or 0
        by_user[uid]["output_tokens"] += r.output_tokens or 0
        by_user[uid]["llm_call_count"] += r.llm_call_count or 0

    return {
        "month": month,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_tokens": total_input + total_output,
        "total_llm_calls": total_calls,
        "by_user": by_user,
    }


@router.get("/organizations/{org_id}/usage/daily")
async def get_daily_usage(
    org_id: str,
    month: str | None = None,
    user_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get daily token usage breakdown for an organization, optionally filtered by user."""
    from app.db.models import TokenUsage
    import datetime as dt

    if not month:
        month = dt.date.today().strftime("%Y-%m")

    query = db.query(TokenUsage).filter(
        TokenUsage.organization_id == org_id,
        TokenUsage.date.like(f"{month}%"),
    )
    if user_id:
        query = query.filter(TokenUsage.user_id == user_id)
    rows = query.order_by(TokenUsage.date).all()

    by_day: dict[str, dict] = {}
    for r in rows:
        if r.date not in by_day:
            by_day[r.date] = {
                "input_tokens": 0,
                "output_tokens": 0,
                "llm_call_count": 0,
            }
        by_day[r.date]["input_tokens"] += r.input_tokens or 0
        by_day[r.date]["output_tokens"] += r.output_tokens or 0
        by_day[r.date]["llm_call_count"] += r.llm_call_count or 0

    return {"month": month, "days": by_day}


@router.get("/users/by-email")
async def find_user_by_email(
    email: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Find a user by email address. Used for adding members to an organization."""
    target = db.query(User).filter(User.email == email).first()
    if not target:
        raise HTTPException(404, "User not found")
    return {
        "id": target.id,
        "email": target.email,
        "full_name": target.full_name,
        "role": target.role,
    }
