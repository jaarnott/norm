"""Build Executive Chef-specific context from the database."""

from sqlalchemy.orm import Session

from app.services.venue_service import get_user_venues


def build_executive_chef_context(db: Session, user_id: str | None = None) -> dict:
    """Return context dict with venue info for the Executive Chef agent."""
    venues = get_user_venues(db, user_id)

    return {
        "venues": [{"id": v.id, "name": v.name} for v in venues],
    }
