"""Venue access that no membership explains.

Venue access is its own list, separate from organisation membership. Removing a
member used to leave their venue rows behind, so a removed person kept every
venue (rich@loadedhub.com, 18 Oct 2026). Nothing but a check can notice that
the two lists have drifted apart.
"""

from __future__ import annotations

from sqlalchemy.orm import Session


def orphaned_access(db: Session) -> list[dict]:
    """Venue-access rows where the user is not a member of the venue's
    organisation. Platform admins are listed too: they have no way in except a
    membership, by design."""
    from app.db.models import OrganizationMembership, User, UserVenueAccess, Venue

    members = {
        (m.user_id, m.organization_id)
        for m in db.query(
            OrganizationMembership.user_id, OrganizationMembership.organization_id
        )
    }
    out = []
    rows = (
        db.query(UserVenueAccess, User, Venue)
        .join(User, User.id == UserVenueAccess.user_id)
        .join(Venue, Venue.id == UserVenueAccess.venue_id)
        .all()
    )
    for access, user, venue in rows:
        if venue.organization_id is None:
            continue
        if (user.id, venue.organization_id) not in members:
            out.append(
                {
                    "user_id": user.id,
                    "email": user.email,
                    "venue_id": venue.id,
                    "venue": venue.name,
                    "organization_id": venue.organization_id,
                    "granted_at": access.granted_at.isoformat()
                    if access.granted_at
                    else None,
                }
            )
    return sorted(out, key=lambda r: (r["email"], r["venue"]))
