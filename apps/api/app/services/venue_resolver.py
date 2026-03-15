from sqlalchemy.orm import Session
from app.db.models import Venue


def _normalize(s: str) -> str:
    return s.lower().replace("'", "").replace("\u2019", "").replace(",", "").replace("&", "and")


def resolve_venue(text: str, db: Session) -> dict | None:
    """Match free text against known venue names (case/punctuation insensitive)."""
    text_norm = _normalize(text)
    venues = db.query(Venue).all()
    for venue in venues:
        if _normalize(venue.name) in text_norm:
            return {"id": venue.id, "name": venue.name, "location": venue.location}
    return None
