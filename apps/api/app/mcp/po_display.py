"""Build display-block payloads for playbook workflow results.

The ``create_stock_order`` playbook renders into the SAME PurchaseOrderEditor
component the web app uses (bundled in display-block.html), not a bespoke
summary card. The component normally resolves its bare order lines
(``{itemId, quantity}``) into product names, supplier variants and prices by
fetching reference data on mount — three HTTP calls the sandboxed iframe
would have to route through ``tools/call``, with ~1 MB of stock items coming
back through the host.

So the resolution happens HERE instead, once, server-side, mirroring the
component's own auto-resolve (PurchaseOrderEditor.tsx): the app receives
lines that already carry ``stock_code``/``product``/``supplier``/prices, the
component sees lines-with-codes and skips its auto-resolve, and the card
paints complete with zero callbacks. Reference data is then only needed if
the user starts typing in the add-item search, and the app shim lazy-loads it
in pages at that point.

Failure here must never fail the workflow: a draft with unresolved lines in
Norm is still a draft — the block falls back to the plain workflow card and
the ``open_in_norm`` link.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.mcp.principal import McpPrincipal

logger = logging.getLogger(__name__)


def playbook_display_block(
    payload: dict,
    venue_id: str | None,
    principal: McpPrincipal,
    db: Session,
    config_db: Session,
) -> dict | None:
    """The ``{component, data, props}`` block for a playbook outcome, or None.

    draft_created + an order doc -> the real purchase-order editor.
    Everything else -> the workflow status card (also a bundled component).
    """
    if not isinstance(payload, dict):
        return None

    try:
        if payload.get("status") == "draft_created" and payload.get("doc_type") in (
            "order",
            "purchase_order",
        ):
            block = _po_editor_block(payload, venue_id, principal, db, config_db)
            if block is not None:
                return block
    except Exception:
        # Resolution is enhancement, not correctness — fall through to the card.
        logger.exception("po_display_block_failed")

    return {
        "component": "workflow_result",
        "data": payload,
        "props": {"embedded": True},
    }


def _po_editor_block(
    payload: dict,
    venue_id: str | None,
    principal: McpPrincipal,
    db: Session,
    config_db: Session,
) -> dict | None:
    from app.db.models import Thread, Venue, WorkingDocument

    doc = (
        db.query(WorkingDocument)
        .filter(WorkingDocument.id == payload.get("working_document_id"))
        .first()
    )
    if doc is None:
        return None
    # The doc was created by this same call, but re-check rather than assume —
    # the payload is worker-thread output, not a trusted handle.
    thread = db.query(Thread).filter(Thread.id == doc.thread_id).first()
    if thread is None or thread.user_id != principal.user_id:
        return None

    data = dict(doc.data or {})
    venue_id = venue_id or data.get("venue_id") or doc.venue_id
    data["working_document_id"] = doc.id
    data["thread_id"] = doc.thread_id

    venue = db.query(Venue).filter(Venue.id == venue_id).first() if venue_id else None

    lines = _resolve_lines(data.get("order_lines"), venue_id, db, config_db)
    if lines and not data.get("lines"):
        data["lines"] = lines
        # Persist the resolved lines into the draft itself. The patch ops the
        # app sends (`update_line` by index) address doc.data["lines"] — if
        # only the bare order_lines were stored, an edit would land in an
        # empty array and silently vanish on the next fetch. Not a version
        # bump: this materializes what the draft already means, it isn't a
        # user edit, and the app reads the version from its own GET afterward.
        from sqlalchemy.orm.attributes import flag_modified

        doc.data = {
            k: v
            for k, v in data.items()
            if k not in ("working_document_id", "thread_id")
        }
        flag_modified(doc, "data")
        db.commit()
    elif not data.get("lines"):
        # Unresolved: the item was ambiguous ("which Corona?") or the agent
        # hasn't picked one yet, so there is nothing to draw. Do NOT hand the
        # editor the bare `order_lines`/`needs_selection` — it would render a
        # phantom line (a quantity with no product), which is exactly the
        # "rendered before Claude was ready" the demo hit. Show the editor's
        # "preparing" empty state instead; the card polls and fills in the real
        # lines once the agent resolves the draft. The DRAFT keeps order_lines
        # (this only strips the block's copy).
        data["lines"] = []
        data.pop("order_lines", None)
        data.pop("needs_selection", None)

    props: dict = {
        "embedded": True,
        "thread_id": doc.thread_id,
        "title": f"Purchase Order — {venue.name}" if venue else "Purchase Order",
        "open_in_norm": payload.get("open_in_norm"),
    }
    if venue_id:
        props["activeVenueId"] = venue_id
    return {"component": "purchase_order_editor", "data": data, "props": props}


def _resolve_lines(
    order_lines,
    venue_id: str | None,
    db: Session,
    config_db: Session,
) -> list[dict]:
    """Bare ``{itemId, quantity}`` lines -> full editor lines.

    Mirrors the component's auto-resolve: same variant preference order, same
    field names. Returns [] when reference data is unavailable — the app then
    falls back to lazy-loading it, which is the pre-existing behaviour.
    """
    from app.services.component_api import ComponentApiError, execute_component_action

    if not isinstance(order_lines, list) or not order_lines or not venue_id:
        return []

    def _call(action: str, params: dict | None = None) -> list | dict:
        try:
            result = execute_component_action(
                "purchase_order_editor", action, params or {}, venue_id, db, config_db
            )
        except ComponentApiError:
            return []
        if result.get("error"):
            return []
        return result.get("data") or []

    items = _call("get_stock_items_detail")
    if not isinstance(items, list) or not items:
        return []
    suppliers = _call("get_suppliers")
    units = _call("get_units")

    supplier_names = {
        str(s.get("id")): str(s.get("name") or s.get("supplierName") or "")
        for s in suppliers
        if isinstance(s, dict)
    }
    unit_info = {
        str(u.get("id")): {
            "name": str(u.get("name") or u.get("unitName") or ""),
            "ratio": float(u.get("ratio") or u.get("unitRatio") or 1),
        }
        for u in units
        if isinstance(u, dict)
    }
    by_id = {str(i.get("id")): i for i in items if isinstance(i, dict)}

    def _variant_view(item: dict, v: dict) -> dict:
        unit = unit_info.get(str(v.get("unitId")), {})
        return {
            "id": v.get("id"),
            "supplierId": v.get("supplierId"),
            "supplierName": supplier_names.get(str(v.get("supplierId")), "Unknown"),
            "unitId": v.get("unitId"),
            "unitName": unit.get("name", ""),
            "unitRatio": unit.get("ratio") or item.get("orderingUnitRatio") or 1,
            "unitCost": float(v.get("unitCost") or 0),
            "stockCode": str(v.get("stockCode") or ""),
            "brandId": v.get("brandId"),
            "defaultForSupplier": bool(v.get("defaultForSupplier")),
        }

    resolved: list[dict] = []
    for idx, ol in enumerate(order_lines):
        if not isinstance(ol, dict):
            continue
        item = by_id.get(str(ol.get("itemId") or ""))
        if item is None:
            continue
        variants = [
            _variant_view(item, v)
            for v in (item.get("suppliers") or [])
            if isinstance(v, dict)
        ]

        want_variant = str(ol.get("variantId") or "")
        want_supplier = str(ol.get("supplierId") or "")
        default_supplier = str(item.get("defaultSupplierId") or "")
        variant = None
        if want_variant:
            variant = next((v for v in variants if str(v["id"]) == want_variant), None)
        if variant is None and want_supplier:
            variant = next(
                (
                    v
                    for v in variants
                    if str(v["supplierId"]) == want_supplier and v["defaultForSupplier"]
                ),
                None,
            ) or next(
                (v for v in variants if str(v["supplierId"]) == want_supplier), None
            )
        if variant is None:
            variant = (
                next(
                    (
                        v
                        for v in variants
                        if str(v["supplierId"]) == default_supplier
                        and v["defaultForSupplier"]
                    ),
                    None,
                )
                or next((v for v in variants if v["defaultForSupplier"]), None)
                or (variants[0] if variants else None)
            )

        quantity = ol.get("quantity") or ol.get("orderQty") or 1
        resolved.append(
            {
                "id": f"line-{idx}",
                "stock_code": (variant or {}).get("stockCode", ""),
                "product": str(item.get("name") or ""),
                "supplier": (variant or {}).get("supplierName", ""),
                "quantity": quantity,
                "unit": (variant or {}).get("unitName")
                or str(item.get("orderingUnitName") or "each"),
                "unit_price": (variant or {}).get("unitCost", 0),
                "itemId": item.get("id"),
                "unitId": (variant or {}).get("unitId") or item.get("orderingUnitId"),
                "unitRatio": (variant or {}).get("unitRatio")
                or item.get("orderingUnitRatio")
                or 1,
                "unitCost": (variant or {}).get("unitCost", 0),
                "taxPercent": 0.15 if item.get("globalSalesTaxSortOrder") == 1 else 0,
                "supplierId": (variant or {}).get("supplierId") or default_supplier,
                "supplierName": (variant or {}).get("supplierName", ""),
                "brandId": (variant or {}).get("brandId"),
                "variantId": (variant or {}).get("id"),
            }
        )

    if resolved:
        _apply_live_prices(resolved, venue_id, db, config_db)
    return resolved


def _apply_live_prices(
    lines: list[dict], venue_id: str, db: Session, config_db: Session
) -> None:
    """Overlay current supplier costs, the way the component does post-resolve.

    Best-effort: a missing price leaves the variant's stored cost in place.
    """
    from zoneinfo import ZoneInfo

    from app.services.component_api import ComponentApiError, execute_component_action
    from app.db.models import Venue

    item_ids = [line["itemId"] for line in lines if line.get("itemId")]
    if not item_ids:
        return

    tz = "Pacific/Auckland"
    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    if venue is not None and getattr(venue, "timezone", None):
        tz = venue.timezone
    try:
        now = datetime.now(ZoneInfo(tz))
    except Exception:
        now = datetime.now()
    ts = now.strftime("%Y-%m-%dT%H:%M:00.000%z")
    if len(ts) >= 5 and ts[-5] in "+-":
        ts = ts[:-2] + ":" + ts[-2:]

    from urllib.parse import quote

    qs = "&".join(
        f"itemIdTimeStrings={quote(f'{item_id},{ts}')}" for item_id in item_ids
    )
    try:
        result = execute_component_action(
            "purchase_order_editor",
            "get_live_prices",
            {"query_string": qs},
            venue_id,
            db,
            config_db,
        )
    except ComponentApiError:
        return
    data = result.get("data")
    costs = data.get("itemCosts") if isinstance(data, dict) else None
    if not isinstance(costs, dict):
        return
    for line in lines:
        entries = costs.get(str(line.get("itemId")))
        if isinstance(entries, list) and entries and isinstance(entries[0], dict):
            cost = entries[0].get("cost")
            if isinstance(cost, (int, float)):
                line["unit_price"] = cost
                line["unitCost"] = cost
