"""Venue utilities shared across agents and services."""

from sqlalchemy.orm import Session

from app.db.models import Venue, UserVenueAccess


def get_user_venues(db: Session, user_id: str | None = None) -> list[Venue]:
    """Venues a user may access — strictly their UserVenueAccess list.

    Fails CLOSED: a user with no access rows gets NO venues. The old "migration
    period" fallback returned ``db.query(Venue).all()`` with no org filter, so a
    user with no rows was handed every org's venues — the cross-org exposure
    behind "a venue shows up in conversation view but isn't on my company list".
    This now matches the MCP surface, which already fails closed (see
    ``mcp/execution.py``: "no consented venues means no venues, full stop").

    ``user_id=None`` is a SYSTEM context (no user to scope to) and still returns
    every venue — internal callers rely on it, and it is never reached from a
    user-facing request.
    """
    if not user_id:
        return db.query(Venue).order_by(Venue.name).all()

    venue_ids = [
        a.venue_id
        for a in db.query(UserVenueAccess)
        .filter(UserVenueAccess.user_id == user_id)
        .all()
    ]
    if not venue_ids:
        return []

    return db.query(Venue).filter(Venue.id.in_(venue_ids)).order_by(Venue.name).all()


def user_can_access_venue(
    db: Session, user_id: str | None, venue_id: str | None
) -> bool:
    """Whether a user may act on a specific venue.

    Defense in depth for endpoints that take a caller-supplied ``venue_id``
    (e.g. ``POST /messages``): the venue picker is scoped by ``get_user_venues``,
    but the send path used to trust whatever id it was handed. ``venue_id=None``
    means no venue was requested (allowed); ``user_id=None`` is a system context.
    """
    if not venue_id or not user_id:
        return True
    return (
        db.query(UserVenueAccess)
        .filter(
            UserVenueAccess.user_id == user_id,
            UserVenueAccess.venue_id == venue_id,
        )
        .first()
        is not None
    )


def resolve_venue_id(venue_name: str | None, db: Session) -> str | None:
    """Fuzzy-resolve a venue name to its ID. Returns None if not found."""
    if not venue_name:
        return None

    from app.services.venue_resolver import resolve_venue

    venue = resolve_venue(venue_name, db)
    return venue["id"] if venue else None
