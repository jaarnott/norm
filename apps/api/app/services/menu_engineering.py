"""Menu Engineering data helpers.

The Menu Engineering view gets units/sales/cost/margin per product from Loaded's
Cost-of-Goods report (loadedhub ``get_cogs_detail``, over OAuth) — but that report
carries no recipe id. The proper POS-item -> recipe link lives behind Loaded's
``wapi`` host, which the OAuth connector can't reach; the **Cook Brothers App**
can, and its ``kitchen_get_product_sales`` returns ``recipe_id`` per product. This
bridges to it so the table can link a product straight to its recipe.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

PRODUCT_SALES_ACTION = "kitchen_get_product_sales"


def product_recipe_links(
    venue_id: str, start: str, end: str, db: Session, config_db: Session
) -> dict[str, str]:
    """Map POS product name -> recipe id via the Cook Brothers App.

    Returns ``{}`` when the venue isn't connected to the CB App (or the call
    fails), so the caller falls back to name-matching against the menus.
    """
    from app.connectors.spec_executor import execute_spec
    from app.services.recipe_save import RecipeSaveError, _cb_context, _op

    try:
        spec, cfg = _cb_context(venue_id, db, config_db)
    except RecipeSaveError:
        return {}
    op = _op(spec, PRODUCT_SALES_ACTION)
    result, _ = execute_spec(
        spec,
        op,
        {"from": start, "to": end, "limit": 5000},
        cfg.config,
        db,
        venue_id=venue_id,
    )
    if not result.success:
        return {}
    payload = result.response_payload or {}
    data = payload.get("data") if isinstance(payload, dict) else payload
    sales = data.get("sales") if isinstance(data, dict) else None
    links: dict[str, str] = {}
    for row in sales or []:
        if not isinstance(row, dict):
            continue
        name = (row.get("pos_item_name") or "").strip()
        rid = row.get("recipe_id")
        if name and rid and name not in links:
            links[name] = rid
    return links
