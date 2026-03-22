"""Build procurement-specific context from the database."""

from sqlalchemy.orm import Session

from app.db.models import ProductAlias
from app.services.venue_service import get_user_venues


def build_procurement_context(db: Session, user_id: str | None = None) -> dict:
    """Return context dict with venue info and product aliases."""
    venues = get_user_venues(db, user_id)

    aliases = db.query(ProductAlias).all()
    product_alias_list = [a.alias for a in aliases]

    return {
        "venues": [{"id": v.id, "name": v.name} for v in venues],
        "product_aliases": product_alias_list,
    }
