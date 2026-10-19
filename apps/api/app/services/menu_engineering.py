"""Menu Engineering data helpers.

The Menu Engineering view gets units/sales/cost/margin per product from Loaded's
Cost-of-Goods report (loadedhub ``get_cogs_detail``, over OAuth) — but that report
carries no recipe id. Loaded's POS-item → recipe links live on its older
``wapi`` host, which the OAuth connector can't reach; Loaded Legacy can
(``list_pos_item_links``, keyed by the POS item's id). The POS items' names
come over OAuth (``get_pos_items_raw``), so a product name maps to its recipe.

Until 19 Oct 2026 the links came from the Cook Brothers App's
``kitchen_get_product_sales`` — its own copy of product sales with a recipe id
each (docs/plans/loaded-legacy-takeover.md).
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def product_recipe_links(
    venue_id: str, start: str, end: str, db: Session, config_db: Session
) -> dict[str, str]:
    """Map POS product name -> recipe id.

    Returns ``{}`` when the venue isn't connected to Loaded Legacy (or a read
    fails), so the caller falls back to name-matching against the menus.
    ``start``/``end`` are kept for the caller's contract: a link is the POS
    item's current recipe, whatever the period.
    """
    from app.connectors import loaded_legacy
    from app.connectors.tool_executor import execute_connector_tool

    try:
        links = loaded_legacy.call(
            loaded_legacy.LIST_POS_ITEM_LINKS, {}, venue_id, db, config_db
        )
    except loaded_legacy.LoadedLegacyError as exc:
        logger.info("menu engineering: no recipe links (%s)", exc)
        return {}
    recipe_of = {
        str(ln.get("posIdentifier")): ln.get("recipeId")
        for ln in (links if isinstance(links, list) else [])
        if isinstance(ln, dict)
        and ln.get("recipeId")
        and ln.get("posIdentifier")
        and not ln.get("stockDeleted")
    }
    if not recipe_of:
        return {}
    items = execute_connector_tool(
        "loadedhub",
        "get_pos_items_raw",
        {},
        db,
        config_db,
        venue_id=venue_id,
        strict_venue=True,
    )
    rows = items.payload if items.success and isinstance(items.payload, list) else []
    out: dict[str, str] = {}
    for it in rows:
        if not isinstance(it, dict) or it.get("whenDeleted"):
            continue
        name = (it.get("name") or "").strip()
        rid = recipe_of.get(str(it.get("posIdentifier")))
        if name and rid and name not in out:
            out[name] = rid
    return out
