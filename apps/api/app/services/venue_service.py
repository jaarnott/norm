"""Venue utilities shared across agents and services."""

from sqlalchemy.orm import Session

from app.db.models import Venue, UserVenueAccess


def get_user_venues(db: Session, user_id: str | None = None) -> list[Venue]:
    """Return venues accessible to a user, or all venues if no user specified."""
    if not user_id:
        return db.query(Venue).order_by(Venue.name).all()

    access = db.query(UserVenueAccess).filter(UserVenueAccess.user_id == user_id).all()
    if not access:
        # Fallback for migration period — user has no explicit access yet
        return db.query(Venue).order_by(Venue.name).all()

    venue_ids = [a.venue_id for a in access]
    return db.query(Venue).filter(Venue.id.in_(venue_ids)).order_by(Venue.name).all()


def resolve_venue_id(venue_name: str | None, db: Session) -> str | None:
    """Fuzzy-resolve a venue name to its ID. Returns None if not found."""
    if not venue_name:
        return None

    from app.services.venue_resolver import resolve_venue
    venue = resolve_venue(venue_name, db)
    return venue["id"] if venue else None
