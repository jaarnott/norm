from sqlalchemy.orm import Session
from app.db.models import Venue


def _normalize(s: str) -> str:
    return (
        s.lower()
        .replace("'", "")
        .replace("\u2019", "")
        .replace(",", "")
        .replace("&", "and")
    )


def resolve_venue(text: str, db: Session) -> dict | None:
    """Match free text against known venue names (case/punctuation insensitive).

    Bidirectional: matches "murdochs" to "Mr Murdochs" and
    "sales at La Zeppa" to "La Zeppa".
    """
    from app.services.caller_scope import venue_query

    text_norm = _normalize(text)
    # Only the caller's organisation's venues when Norm knows who is asking
    # (caller_scope) — this picks a venue's LOGIN for a consolidator's call,
    # and it matched every organisation's venues (Oct 2026).
    scoped = venue_query(db)
    venues = (scoped if scoped is not None else db.query(Venue)).all()
    for venue in venues:
        name_norm = _normalize(venue.name)
        if name_norm in text_norm or text_norm in name_norm:
            return {"id": venue.id, "name": venue.name, "location": venue.location}
    return None
