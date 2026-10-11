"""Create a stock item in Loaded from a name, a group and a unit.

The received-invoice card has created items since Aug 2026
(routers/invoice_fixes.create_stock_item), with the invoice's supplier,
code and cost embedded. The recipe card needs the same create with no
supplier — a recipe line only says what the item is; its supplier arrives
with its first invoice. Verified live for the invoice path: POST
/1.0/stock/internal/items returns the created item.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

# Loaded's base unit per type + the unitType enum used on a stock item.
BASE_UNIT_BY_TYPE = {"Weight": "kilo", "Volume": "litre", "Count": "each"}
UNIT_TYPE_INDEX = {"Weight": 0, "Volume": 1, "Count": 2}


def _norm(text: object) -> str:
    return " ".join(str(text or "").lower().split())


def create_stock_item(
    venue_id: str,
    name: str,
    group_id: str,
    unit_id: str,
    db: Session,
    config_db: Session,
    *,
    uppercase: bool = True,
) -> dict:
    """Create the item and return ``{item_id, item_name, unit_id, unit_name,
    unit_ratio}``. ``unit_id`` is the unit the recipe line uses; the item's
    counting unit is that unit's base (kilo / litre / each). Names go up in
    CAPITALS, the convention in every Loaded catalogue Norm has seen."""
    from app.services.received_invoice import LoadedInvoiceClient

    name = " ".join(str(name or "").split())
    if uppercase:
        name = name.upper()
    if not name or not group_id or not unit_id:
        raise ValueError("a name, a stock group and a unit are required")

    lh = LoadedInvoiceClient(db, config_db, venue_id)
    units = lh.get("/1.0/stock/internal/units")
    units = units if isinstance(units, list) else (units or {}).get("data") or []
    unit = next((u for u in units if u.get("id") == unit_id), None)
    if not unit:
        raise ValueError("unit not found in Loaded")
    utype = unit.get("stockUnitType") or "Weight"
    base = (
        next(
            (
                u
                for u in units
                if _norm(u.get("name")) == BASE_UNIT_BY_TYPE.get(utype, "")
                and not u.get("datestampDeleted")
            ),
            None,
        )
        or unit
    )
    payload = {
        "name": name,
        "groupId": group_id,
        "unitType": UNIT_TYPE_INDEX.get(utype, 0),
        "countingUnitId": base.get("id"),
        "countingUnitRatio": base.get("ratio") or 1.0,
        "orderingUnitId": unit_id,
        "orderingUnitRatio": unit.get("ratio") or 1.0,
        "globalSalesTaxSortOrder": 1,
        "itemType": "Default",
        "suppliers": [],
    }
    created = lh.request("POST", "/1.0/stock/internal/items", payload)
    item_id = created.get("id") if isinstance(created, dict) else None
    if not item_id:
        raise ValueError("Loaded did not return the created stock item")
    return {
        "item_id": item_id,
        "item_name": name,
        "unit_id": unit_id,
        "unit_name": unit.get("name"),
        "unit_ratio": unit.get("ratio") or 1.0,
        "counting_unit_name": base.get("name"),
        "counting_unit_ratio": base.get("ratio") or 1.0,
    }
