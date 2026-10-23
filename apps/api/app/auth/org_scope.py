"""The one check for anything scoped to an organisation in the path.

``require_permission`` alone is not enough for an org-scoped route: it checks
the scope in the caller's ACTIVE membership (the X-Organization header, else
their oldest), never in the organisation being acted on. So an Owner of
organisation A who is also a Kitchen staff member of B could send
``X-Organization: A`` and add staff, set PINs or sign tablets in for B
(Oct 2026). Every org-scoped route calls this with the org in its path.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import OrganizationMembership, User


def require_org_permission(
    db: Session, user: User, org_id: str | None, scope: str | None = None
) -> OrganizationMembership | None:
    """403 unless the caller belongs to ``org_id`` and, when ``scope`` is
    given, holds it in THAT organisation. Platform admins pass (and get
    None back); everyone else gets their membership there."""
    if user.role == "admin":
        return None
    membership = (
        db.query(OrganizationMembership)
        .filter(
            OrganizationMembership.organization_id == org_id,
            OrganizationMembership.user_id == user.id,
        )
        .first()
        if org_id
        else None
    )
    if not membership:
        raise HTTPException(403, "Not a member of this organization")
    if scope is None:
        return membership
    role = membership.role_obj
    if not role or scope not in (role.permissions or []):
        raise HTTPException(403, f"Missing permissions: {scope}")
    return membership


def permissions_in(membership: OrganizationMembership | None) -> set[str] | None:
    """What the caller holds in that organisation; None for a platform admin
    (who holds everything)."""
    if membership is None:
        return None
    role = membership.role_obj
    return set((role.permissions if role else None) or [])
