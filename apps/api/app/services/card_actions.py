"""A button in a card that changes something in another system.

Norm asks before IT writes (services/approvals.py). A card's own button —
Place Order, Save on the menu or roster editor, Accept and Receive on an
invoice — is a person making the change themselves: pressing it is the
approval. Two things still have to hold, and until Oct 2026 neither did:

- **It acts only at a venue the person can open.** These routes took any
  ``venue_id`` the browser sent and used that venue's Loaded login; with none
  they used whichever venue's login came first — another organisation's
  included.
- **It is on the record.** A change made from a card left no trace of who
  made it; an Approval row (``card_write``) now says who, where and what.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import Approval, User, Venue


def venue_for(db: Session, user: User, venue_id: str | None) -> str:
    """The venue this call may act at, or an HTTP error.

    The one named, if the person may open it (a platform admin may open any).
    None named: their only venue, if they have exactly one — never "whichever
    login comes first".
    """
    from app.db.models import UserVenueAccess
    from app.services.venue_service import user_can_access_venue

    is_admin = getattr(user, "role", None) == "admin"
    if venue_id:
        if is_admin or user_can_access_venue(db, user.id, venue_id):
            return venue_id
        raise HTTPException(403, "You don't have access to that venue")
    mine = [
        row.venue_id
        for row in db.query(UserVenueAccess)
        .filter(UserVenueAccess.user_id == user.id)
        .all()
    ]
    if len(mine) == 1:
        return mine[0]
    raise HTTPException(400, "Choose a venue first")


def record(
    db: Session,
    user: User,
    venue_id: str | None,
    what: str,
    detail: str | None = None,
    thread_id: str | None = None,
) -> None:
    """Who changed what, where, from a card. Flushed with the caller's work."""
    venue = db.query(Venue).filter(Venue.id == venue_id).first() if venue_id else None
    where = venue.name if venue else (venue_id or "no venue")
    db.add(
        Approval(
            thread_id=thread_id,
            action="card_write",
            performed_by=user.email,
            user_id=user.id,
            notes=f"{what} at {where}" + (f": {detail}" if detail else ""),
        )
    )
    db.flush()
