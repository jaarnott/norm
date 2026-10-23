"""Which connections a call may use — never another organisation's.

A call uses its venue's own connection, else its organisation's
organisation-wide one (a row with no venue, like BambooHR). Until 23 Oct 2026
"organisation-wide" meant ANY venue-less row: production's BambooHR row is Cook
Brothers', and every other organisation's calls fell back to it. A venue-less
row now names its organisation (Connection.organization_id); one that names
none is a platform row (the Anthropic key) and serves no organisation's call.

The organisation is the venue's, when the call has a venue; else the one the
caller passes; else the caller scope's (services/caller_scope.py).
"""

from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session


def org_for(db: Session, venue_id: str | None = None, org_id: str | None = None):
    """The organisation a call acts for: its venue's, else ``org_id``, else
    the caller scope's. None when nothing says (a system context)."""
    if venue_id:
        from app.db.models import Venue

        org = db.query(Venue.organization_id).filter(Venue.id == venue_id).scalar()
        if org:
            return org
    if org_id:
        return org_id
    from app.services import caller_scope

    scope = caller_scope.current()
    return scope.org_id if scope else None


def org_wide(query, org_id: str | None):
    """Narrow a Connection query to the organisation-wide (venue-less) row of
    ``org_id``. With no organisation, nothing — a venue-less row with no
    organisation is a platform row, not anyone's."""
    from app.db.models import Connection

    query = query.filter(Connection.venue_id.is_(None), Connection.user_id.is_(None))
    if not org_id:
        return query.filter(Connection.id.is_(None))  # matches nothing
    return query.filter(Connection.organization_id == org_id)


def platform_only(query):
    """Narrow a Connection query to platform rows: no venue, no user, no
    organisation (the Anthropic key)."""
    from app.db.models import Connection

    return query.filter(
        Connection.venue_id.is_(None),
        Connection.user_id.is_(None),
        Connection.organization_id.is_(None),
    )


def in_org(query, org_id: str | None):
    """Narrow a Connection query to the rows this organisation's calls may
    use: its venues' rows and its organisation-wide row. No organisation:
    nothing."""
    from app.db.models import Connection, Venue

    if not org_id:
        return query.filter(Connection.id.is_(None))
    venues = select(Venue.id).where(Venue.organization_id == org_id)
    return query.filter(
        or_(
            Connection.venue_id.in_(venues),
            and_(
                Connection.venue_id.is_(None),
                Connection.user_id.is_(None),
                Connection.organization_id == org_id,
            ),
        )
    )


def venue_or_org_wide(
    db: Session,
    connector_name: str,
    venue_id: str | None,
    *,
    org_id: str | None = None,
    enabled_only: bool = True,
):
    """The venue's own connection, else its organisation's organisation-wide
    one — the fallback every path shares."""
    from app.db.models import Connection

    def base():
        q = db.query(Connection).filter(
            Connection.connector_name == connector_name,
            Connection.user_id.is_(None),
        )
        return q.filter(Connection.enabled == "true") if enabled_only else q

    if venue_id:
        row = base().filter(Connection.venue_id == venue_id).first()
        if row is not None:
            return row
    return org_wide(base(), org_for(db, venue_id, org_id)).first()
