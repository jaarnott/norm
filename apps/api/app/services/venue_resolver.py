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
    text_norm = _normalize(text)
    venues = db.query(Venue).all()
    for venue in venues:
        name_norm = _normalize(venue.name)
        if name_norm in text_norm or text_norm in name_norm:
            return {"id": venue.id, "name": venue.name, "location": venue.location}
    return None
