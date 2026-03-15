"""Build procurement-specific context from the database."""

from sqlalchemy.orm import Session

from app.db.models import Venue, ProductAlias


def build_procurement_context(db: Session, user_id: str | None = None) -> dict:
    """Return context dict with venue names, product aliases, and open order."""
    venues = db.query(Venue).all()
    venue_names = [v.name for v in venues]

    aliases = db.query(ProductAlias).all()
    product_alias_list = [a.alias for a in aliases]

    ctx: dict = {
        "venue_names": venue_names,
        "product_aliases": product_alias_list,
    }

    return ctx
