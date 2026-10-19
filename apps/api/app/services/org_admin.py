"""Creating and removing organisations and venues — one set of rules for the
sign-up form and the platform admins' Organisations page (Oct 2026).

Before this, an organisation could only be made by an API call that left its
owner without a role (so the owner was refused everywhere), and a new account
belonged to no organisation at all, so a person who signed up saw nothing.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy.orm import Session

#: Roles every organisation shares (organization_id is NULL on these rows).
SYSTEM_ROLES = ("owner", "manager", "payroll_admin", "team_member")


class OrgAdminError(Exception):
    """A refusal a person should read: why something can't be done yet."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def system_role(db: Session, name: str):
    from app.db.models import Role

    return (
        db.query(Role).filter(Role.name == name, Role.organization_id.is_(None)).first()
    )


def unique_slug(db: Session, name: str) -> str:
    from app.db.models import Organization

    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40] or "org"
    slug = base
    while db.query(Organization.id).filter(Organization.slug == slug).first():
        slug = f"{base}-{uuid.uuid4().hex[:4]}"
    return slug


def currency_for(timezone: str | None) -> str:
    """Australian venues trade in AUD; everyone else defaults to NZD as before."""
    return "AUD" if (timezone or "").startswith("Australia/") else "NZD"


def create_organization(
    db: Session,
    name: str,
    *,
    owner=None,
    billing_email: str | None = None,
    timezone: str | None = None,
):
    """An organisation, and — when ``owner`` is given — that person as its
    owner with the owner ROLE (the old route set only the label, so the owner
    held no permissions). Platform admins creating one for a customer pass no
    owner: they are never made members of other people's organisations."""
    from app.db.models import Organization, OrganizationMembership

    name = (name or "").strip()
    if not name:
        raise OrgAdminError("Give the organisation a name", 422)
    org = Organization(
        name=name,
        slug=unique_slug(db, name),
        billing_email=billing_email,
        currency=currency_for(timezone),
    )
    if timezone:
        org.default_timezone = timezone
    db.add(org)
    db.flush()
    if owner is not None:
        role = system_role(db, "owner")
        db.add(
            OrganizationMembership(
                user_id=owner.id,
                organization_id=org.id,
                role="owner",
                role_id=role.id if role else None,
            )
        )
        db.flush()
    return org


def create_venue(
    db: Session,
    org_id: str,
    name: str,
    *,
    location: str | None = None,
    timezone: str | None = None,
    day_start_time: str | None = None,
    grant_to=None,
):
    """A venue in ``org_id``. ``grant_to`` gets access to it — only ever a
    member of that organisation, so a platform admin adding a venue to a
    customer's organisation is not handed access to it."""
    from app.db.models import OrganizationMembership, UserVenueAccess, Venue

    name = (name or "").strip()
    if not name:
        raise OrgAdminError("Give the venue a name", 422)
    venue = Venue(
        name=name,
        location=location,
        timezone=timezone,
        day_start_time=day_start_time,
        organization_id=org_id,
    )
    db.add(venue)
    db.flush()
    if grant_to is not None:
        member = (
            db.query(OrganizationMembership.id)
            .filter(
                OrganizationMembership.user_id == grant_to.id,
                OrganizationMembership.organization_id == org_id,
            )
            .first()
        )
        if member:
            db.add(UserVenueAccess(user_id=grant_to.id, venue_id=venue.id))
            db.flush()
    return venue


def delete_organization(db: Session, org_id: str) -> dict:
    """Remove an organisation that has nothing left that must be kept.

    Refused while it still has venues (each venue's own delete decides what
    happens to its history, and refuses while it holds Norm Functions bookings)
    or any billing history (money records are never deleted). Then its
    memberships, roles, App switches, claude.ai connections, memories, usage
    counts and jobs go; email logs and conversations keep their rows and lose
    the reference. People who belonged to it keep their accounts."""
    from app.db.mcp_models import (
        McpAuditLog,
        McpAuthorizationCode,
        McpGrant,
        McpToken,
    )
    from app.db.models import (
        App,
        BackgroundJob,
        BillingEvent,
        EmailLog,
        InvoiceAutopilotOutcome,
        Memory,
        MemorySignal,
        Organization,
        OrganizationMembership,
        OrgAppEntitlement,
        Role,
        Subscription,
        Thread,
        TokenTopUp,
        TokenUsage,
        Venue,
    )

    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        raise OrgAdminError("Organisation not found", 404)
    venues = db.query(Venue.name).filter(Venue.organization_id == org_id).all()
    if venues:
        names = ", ".join(sorted(v[0] for v in venues))
        raise OrgAdminError(
            f"{org.name} still has venues ({names}). Delete them first; "
            "each venue decides what happens to its history."
        )
    for model, what in (
        (Subscription, "a subscription"),
        (BillingEvent, "billing events"),
        (TokenTopUp, "top-ups"),
    ):
        if db.query(model.id).filter(model.organization_id == org_id).first():
            raise OrgAdminError(
                f"{org.name} has {what}. Billing history is kept, so the "
                "organisation can't be deleted."
            )

    members = db.query(OrganizationMembership).filter(
        OrganizationMembership.organization_id == org_id
    )
    removed_people = members.count()

    def gone(model, col=None):
        db.query(model).filter(
            (col if col is not None else model.organization_id) == org_id
        ).delete(synchronize_session=False)

    for model in (McpAuditLog, McpToken, McpAuthorizationCode, McpGrant):
        gone(model)
    members.delete(synchronize_session=False)
    gone(Role)
    for model in (
        OrgAppEntitlement,
        Memory,
        MemorySignal,
        TokenUsage,
        InvoiceAutopilotOutcome,
        BackgroundJob,
        App,
    ):
        gone(model)
    db.query(EmailLog).filter(EmailLog.organization_id == org_id).update(
        {"organization_id": None}, synchronize_session=False
    )
    db.query(Thread).filter(Thread.organization_id == org_id).update(
        {"organization_id": None}, synchronize_session=False
    )
    name = org.name
    db.delete(org)
    db.flush()
    return {"deleted": name, "members_removed": removed_people}


def overview(db: Session) -> dict:
    """Every organisation with its venues and people, and the accounts that
    belong to no organisation (old or never-finished sign-ups)."""
    from app.db.models import (
        Organization,
        OrganizationMembership,
        Role,
        User,
        UserVenueAccess,
        Venue,
    )

    roles = {r.id: r for r in db.query(Role).all()}
    venues_by_org: dict[str, list] = {}
    for v in db.query(Venue).order_by(Venue.name).all():
        venues_by_org.setdefault(v.organization_id, []).append(v)
    access: dict[str, set] = {}
    for a in db.query(UserVenueAccess.user_id, UserVenueAccess.venue_id):
        access.setdefault(a[0], set()).add(a[1])
    users = {u.id: u for u in db.query(User).all()}
    members_by_org: dict[str, list] = {}
    member_ids = set()
    for m in db.query(OrganizationMembership).all():
        members_by_org.setdefault(m.organization_id, []).append(m)
        member_ids.add(m.user_id)

    def person(u):
        return {
            "user_id": u.id,
            "email": u.email,
            "full_name": u.full_name,
            "is_active": bool(u.is_active),
            "platform_admin": u.role == "admin",
        }

    out = []
    for org in db.query(Organization).order_by(Organization.name).all():
        org_venues = venues_by_org.get(org.id, [])
        org_venue_ids = {v.id for v in org_venues}
        people = []
        for m in members_by_org.get(org.id, []):
            u = users.get(m.user_id)
            if u is None:
                continue
            role = roles.get(m.role_id)
            people.append(
                {
                    **person(u),
                    "role_id": m.role_id,
                    "role": role.display_name if role else None,
                    "venue_ids": sorted(access.get(u.id, set()) & org_venue_ids),
                }
            )
        people.sort(key=lambda p: (p["email"] or "").lower())
        out.append(
            {
                "id": org.id,
                "name": org.name,
                "slug": org.slug,
                "currency": org.currency,
                "created_at": org.created_at.isoformat() if org.created_at else None,
                "venues": [
                    {
                        "id": v.id,
                        "name": v.name,
                        "location": v.location,
                        "timezone": v.timezone,
                    }
                    for v in org_venues
                ],
                "members": people,
            }
        )
    unassigned = sorted(
        (person(u) for uid, u in users.items() if uid not in member_ids),
        key=lambda p: (p["email"] or "").lower(),
    )
    system_roles = [
        {"id": r.id, "name": r.name, "display_name": r.display_name}
        for r in roles.values()
        if r.organization_id is None
    ]
    return {
        "organizations": out,
        "unassigned_users": unassigned,
        "roles": sorted(system_roles, key=lambda r: r["display_name"] or ""),
    }


def set_member_access(
    db: Session,
    org_id: str,
    user_id: str,
    *,
    role_id: str | None = None,
    venue_ids: list[str] | None = None,
) -> None:
    """Change a member's role and/or their venues IN THIS ORGANISATION only;
    their access elsewhere is untouched."""
    from app.db.models import OrganizationMembership, Role, UserVenueAccess, Venue

    m = (
        db.query(OrganizationMembership)
        .filter(
            OrganizationMembership.organization_id == org_id,
            OrganizationMembership.user_id == user_id,
        )
        .first()
    )
    if m is None:
        raise OrgAdminError("That person is not in this organisation", 404)
    if role_id is not None:
        role = db.query(Role).filter(Role.id == role_id).first()
        if role is None or role.organization_id not in (None, org_id):
            raise OrgAdminError("That role isn't available here", 422)
        m.role_id = role.id
        m.role = "owner" if role.name == "owner" else "member"
    if venue_ids is not None:
        org_venue_ids = {
            v[0] for v in db.query(Venue.id).filter(Venue.organization_id == org_id)
        }
        if any(v not in org_venue_ids for v in venue_ids):
            raise OrgAdminError("A venue named is not in this organisation", 422)
        db.query(UserVenueAccess).filter(
            UserVenueAccess.user_id == user_id,
            UserVenueAccess.venue_id.in_(org_venue_ids),
        ).delete(synchronize_session=False)
        for vid in dict.fromkeys(venue_ids):
            db.add(UserVenueAccess(user_id=user_id, venue_id=vid))
    db.flush()
