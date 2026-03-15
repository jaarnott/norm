from sqlalchemy.orm import Session
from app.db.models import ProductAlias


def resolve_product(text: str, db: Session) -> dict | None:
    """Match free text against product aliases. Longest alias wins."""
    text_lower = text.lower()
    aliases = db.query(ProductAlias).all()

    best_match = None
    best_len = 0

    for pa in aliases:
        if pa.alias in text_lower and len(pa.alias) > best_len:
            product = pa.product
            best_match = {
                "id": product.id,
                "name": product.name,
                "category": product.category,
                "unit": product.unit,
                "supplier": product.supplier.name if product.supplier else None,
                "supplier_id": product.supplier_id,
            }
            best_len = len(pa.alias)

    return best_match
