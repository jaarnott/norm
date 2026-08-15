"""Context for the App Builder agent — the user's venues, for venue-scoped apps."""

from sqlalchemy.orm import Session

from app.services.venue_service import get_user_venues


def build_app_builder_context(db: Session, user_id: str | None = None) -> dict:
    venues = get_user_venues(db, user_id)
    return {"venues": [{"id": v.id, "name": v.name} for v in venues]}
