"""Apply user-approved fixes to unreceivable LoadedHub invoices.

The review_and_receive_invoices consolidator proposes structured `fixes`
(link a purchase order; correct a line's unit of measure). The InvoiceFixesCard
renders them and POSTs the selected ones here. Each fix is applied
independently against the venue's LoadedHub connector — a failure isolates to
its own row.

Fix contracts (verified live in the LoadedHub test env, 18 Jul 2026):

- link_po: resolve the referenced PO number to a PO id via
  GET /1.0/stock/internal/purchase-orders (server-side searchTerm is a no-op,
  so filter client-side on orderNumber), then PUT the invoice with
  linkedPurchaseOrderId + purchaseOrderNumber set. Linking does not re-match
  lines.

- unit: mirrors Loaded's own "update variant?" flow.
    1. resolve the proposed unit name to a Loaded unit (GET .../units) — id,
       ratio, stockUnitType. If no confident match exists, the fix fails
       (the unit must be created in Loaded first).
    2. PUT the invoice with the line's unit / linkedUnitId / linkedUnitRatio
       set to the resolved unit.
    3. resolve the supplier variant (GET .../items/{itemId} → suppliers[]
       where supplierId == invoice supplier AND stockCode == line code) and
       PATCH .../item-supplier-variant/{variantId} { unitId } so future
       invoices match.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.engine import get_config_db, get_db
from app.db.models import User

# The LoadedHub client, the receive primitive and the draft shaper live in the
# service so the web router and the MCP submit tool share ONE implementation.
# Kept under the old private names here so the endpoints and tests below (and
# test_invoice_fixes_handler.py) are untouched by the move.
from app.services.received_invoice import (
    LoadedInvoiceClient as _Loaded,
    ReceiveRequest,
    _norm,
    _po_key,
    build_received_invoice_data,
    do_receive as _do_receive,
)

# Item-matching moved to the shared LLM-function service (norm.match_stock_items,
# called by the review engine via call_api). Re-imported under the old private
# names so this router's endpoints and tests keep working unchanged.
from app.services.item_match import (
    _classify_item_lines,  # noqa: F401 — kept for tests (IF._classify_item_lines)
    _default_variant,
    _fetch_raw_stock_items,  # noqa: F401 — kept for tests
    _fetch_stock_groups,
    _match_stock_items,  # noqa: F401 — kept for tests
    _match_subset,  # noqa: F401 — kept for tests
    _new_item_lines,  # noqa: F401 — kept for tests
    suggest_item_matches_for_invoice,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class ApplyFixesRequest(BaseModel):
    venue_id: str
    fixes: list[dict]


class InvoiceStatusRequest(BaseModel):
    venue_id: str
    invoice_ids: list[str] = []


class DraftRequest(BaseModel):
    venue_id: str
    invoice_id: str


def _apply_link_po(lh: _Loaded, fix: dict, db: Session) -> str:  # noqa: ARG001 — db unused; uniform applier signature
    from app.services.received_invoice import resolve_po_id

    if not _po_key(fix.get("po_number")):
        raise RuntimeError("no PO number to link")
    resolved = resolve_po_id(lh, fix.get("po_number"))
    if not resolved:
        raise RuntimeError(
            f"purchase order {fix.get('po_number')} not found or ambiguous in Loaded"
        )
    other = resolved.get("linked_invoice_id")
    if other and other != fix["invoice_id"]:
        # Split order: the PO is already linked to another invoice. Loaded is
        # 1:1, so don't steal the link — leave this invoice unlinked.
        return (
            f"Purchase order {resolved.get('order_number')} is already linked to "
            "another invoice — left unlinked"
        )
    inv = lh.invoice(fix["invoice_id"])
    inv["linkedPurchaseOrderId"] = resolved["id"]
    inv["purchaseOrderNumber"] = resolved.get("order_number")
    lh.request("PUT", f"/1.0/stock/internal/invoices/{fix['invoice_id']}", inv)
    return f"Linked purchase order {resolved.get('order_number')}"


def _resolve_unit(lh: _Loaded, proposed: str) -> dict | None:
    """Find the Loaded unit whose name matches the proposed unit.

    Exact name first; for a simple (non-multipack) unit fall back to a
    guideline-equivalent match by (type, magnitude). A MULTIPACK proposed name
    ('5x3kg') matches by exact name ONLY — a ratio-equal but differently-named
    unit ('15 KG') is a different pack, so an unmatched multipack is created.
    """
    from app.services.invoice_units import is_multipack, parse_unit

    units = lh.get("/1.0/stock/internal/units")
    units = [u for u in (units or []) if not u.get("datestampDeleted")]
    for u in units:
        if _norm(u.get("name")) == _norm(proposed):
            return u
    if is_multipack(proposed):
        return None
    target = parse_unit(proposed)
    if target:
        for u in units:
            pu = parse_unit(u.get("name"))
            if pu and pu[0] == target[0] and abs(pu[1] - target[1]) < 0.001:
                return u
    return None


_UNIT_TYPES = {"Weight", "Volume", "Count"}


def _resolve_unit_spec(name: str, db: Session) -> dict | None:
    """Resolve a unit NAME to Loaded's create fields {ratio, stock_unit_type} via
    the LLM. Only used when a unit must be CREATED (it isn't in the catalogue), so
    the cost is paid rarely; robust to naming conventions ('5x3kg', '6x1 Litre',
    '2x2.3KG'). Returns None if no confident spec.
    """
    from app.interpreter.llm_interpreter import call_llm

    schema = {
        "ratio": (
            "number — the unit's size as a multiple of the BASE unit of its "
            "type: Weight is in KILOGRAMS, Volume in LITRES, Count in items. A "
            "multipack 'NxM' means N of M (multiply): '5x3kg' → 15 (5 x 3kg), "
            "'6x700ml' → 4.2, '12x1L' → 12, '2kg' → 2, '500g' → 0.5, "
            "'12 pack' → 12, 'Each' → 1."
        ),
        "stock_unit_type": "one of 'Weight', 'Volume', 'Count'",
    }
    system_prompt = (
        "You convert a stock unit name into its base-unit size and type for a "
        "stock system (Weight→kilograms, Volume→litres, Count→items). Return "
        "ONLY a JSON object matching this schema:\n"
        + json.dumps(schema, indent=1)
        + "\nNever guess — if the name is not a real unit of weight, volume or "
        "count, return null for both fields."
    )
    try:
        parsed, _ = call_llm(
            system_prompt=system_prompt,
            user_prompt=f"Unit name: {name}",
            db=db,
            call_type="extraction",
            max_tokens=200,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("unit-spec resolve failed for %s: %s", name, exc)
        return None
    parsed = parsed if isinstance(parsed, dict) else {}
    try:
        ratio = float(parsed.get("ratio"))
    except (TypeError, ValueError):
        return None
    utype = parsed.get("stock_unit_type")
    if ratio <= 0 or utype not in _UNIT_TYPES:
        return None
    return {"ratio": ratio, "stock_unit_type": utype}


def _apply_unit(lh: _Loaded, fix: dict, db: Session) -> str:
    proposed = fix.get("proposed_unit", "")
    unit = _resolve_unit(lh, proposed)
    created = False
    if not unit:
        # Not in Loaded — create it. The LLM supplies the base-unit ratio + type
        # (robust to '5x3kg' / '6x1 Litre' conventions); only fires on a create.
        spec = _resolve_unit_spec(proposed, db)
        if not spec:
            raise RuntimeError(
                f"could not resolve a unit definition for '{proposed}' — "
                "create it in Loaded manually"
            )
        unit = lh.request(
            "POST",
            "/1.0/stock/internal/units",
            {
                "name": proposed,
                "ratio": spec["ratio"],
                "stockUnitType": spec["stock_unit_type"],
            },
        )
        # Loaded returns the created unit; re-fetch by name if the POST response
        # isn't the object itself.
        if not isinstance(unit, dict) or not unit.get("id"):
            unit = _resolve_unit(lh, proposed)
        if not isinstance(unit, dict) or not unit.get("id"):
            raise RuntimeError(f"created unit '{proposed}' but could not read it back")
        created = True
    inv = lh.invoice(fix["invoice_id"])
    line = next(
        (ln for ln in inv.get("lines") or [] if ln.get("id") == fix.get("line_id")),
        None,
    )
    if not line:
        raise RuntimeError("invoice line no longer present")
    line["unit"] = unit.get("name")
    line["linkedUnitId"] = unit.get("id")
    line["linkedUnitRatio"] = unit.get("ratio")
    lh.request("PUT", f"/1.0/stock/internal/invoices/{fix['invoice_id']}", inv)

    # Update the matched supplier variant (Loaded's "update variant?" step).
    variant_note = ""
    item = lh.get(f"/1.0/stock/internal/items/{fix['linked_item_id']}")
    variants = (item or {}).get("suppliers") or []
    supplier = fix.get("linked_supplier_id")
    code = _norm(fix.get("line_code"))
    variant = next(
        (
            v
            for v in variants
            if v.get("supplierId") == supplier and _norm(v.get("stockCode")) == code
        ),
        None,
    )
    if variant:
        lh.request(
            "PATCH",
            f"/1.0/stock/internal/item-supplier-variant/{variant['id']}",
            {"unitId": unit.get("id")},
        )
        variant_note = " and updated the variant"
    verb = "Created and set" if created else "Set"
    return f"{verb} unit to {unit.get('name')}{variant_note}"


_APPLIERS = {"link_po": _apply_link_po, "unit": _apply_unit}


@router.post("/invoice-fixes/apply")
async def apply_invoice_fixes(
    body: ApplyFixesRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Apply the selected invoice fixes; per-fix results, failures isolated."""
    lh = _Loaded(db, config_db, body.venue_id)
    results = []
    for fix in body.fixes:
        fid = fix.get("id")
        applier = _APPLIERS.get(fix.get("type"))
        if not applier:
            results.append({"id": fid, "ok": False, "message": "unknown fix type"})
            continue
        try:
            message = applier(lh, fix, db)
            results.append({"id": fid, "ok": True, "message": message})
        except Exception as exc:  # noqa: BLE001 — isolate each fix
            logger.warning("invoice fix %s failed: %s", fid, exc)
            results.append({"id": fid, "ok": False, "message": str(exc)})
    applied = sum(1 for r in results if r["ok"])
    return {"results": results, "applied": applied, "total": len(results)}


class AcceptFixRequest(BaseModel):
    venue_id: str
    invoice_id: str
    fix: dict


@router.post("/invoice-fixes/accept")
async def accept_invoice_fix(
    body: AcceptFixRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Apply ONE suggested change to Loaded WITHOUT receiving the invoice, then
    refresh the draft so the change reads as real: the accepted suggestion is a
    normal PUT of the invoice (never sets ``isReceived``), then the draft is
    re-synced from the now-updated Loaded invoice and the review re-runs.
    """
    fix = body.fix or {}
    applier = _APPLIERS.get(fix.get("type"))
    if not applier:
        raise HTTPException(400, f"unknown fix type: {fix.get('type')}")

    lh = _Loaded(db, config_db, body.venue_id)
    # Write the change to Loaded (link a PO / correct a unit; a missing unit is
    # created first). The appliers PUT the invoice with only the change set —
    # they never set isReceived, so the invoice stays unreceived.
    message = applier(lh, fix, db)
    document = _reshape_draft_after_write(db, lh, body.venue_id, body.invoice_id)
    return {"message": message, "document": document}


def _reshape_draft_after_write(
    db: Session, lh, venue_id: str, invoice_id: str
) -> dict | None:
    """Re-pull + re-SHAPE the venue's open draft for an invoice from the fresh
    Loaded invoice, clearing the cached review so it re-runs. Used after any
    non-receiving write (accept a fix, create/link a stock item) — a write can
    change the invoice server-side (e.g. linking a PO re-matches lines; creating
    an item links the line), so the draft's lines/flags must come from Loaded.
    Best-effort; returns the refreshed doc dict or None if there's no draft.
    """
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.models import WorkingDocument
    from app.routers.working_documents import _doc_to_dict

    docs = (
        db.query(WorkingDocument)
        .filter(
            WorkingDocument.doc_type == "received_invoice",
            WorkingDocument.venue_id == venue_id,
        )
        .all()
    )
    doc = next(
        (
            d
            for d in docs
            if (d.external_ref or {}).get("invoice_id") == invoice_id
            and not (d.data or {}).get("is_received")
        ),
        None,
    )
    if doc is None:
        return None
    try:
        detail = lh.invoice(invoice_id)
        fresh = build_received_invoice_data(detail)
        for k in ("working_document_id", "thread_id", "venue_id"):
            if k in (doc.data or {}):
                fresh[k] = doc.data[k]
        # PO resolution from the copy is the consolidator's job (runs in /review);
        # the draft only mirrors Loaded's own linked PO deterministically.
        _attach_po_reference(fresh, lh)
        fresh["checks"] = None
        fresh["suggestions"] = []
        fresh["check_reasons"] = []
        doc.data = fresh
        flag_modified(doc, "data")
        db.commit()
        db.refresh(doc)
    except Exception as exc:  # noqa: BLE001 — refresh is best-effort
        logger.info("reshape draft failed: %s", exc)
    return _doc_to_dict(doc)


class CreateItemRequest(BaseModel):
    venue_id: str
    invoice_id: str
    line_id: str
    group_id: str
    name: str | None = None
    unit_id: str | None = None
    brand_id: str | None = None


# Loaded's base unit per type + the unitType enum used on a stock item.
_BASE_UNIT_BY_TYPE = {"Weight": "kilo", "Volume": "litre", "Count": "each"}
_UNIT_TYPE_INDEX = {"Weight": 0, "Volume": 1, "Count": 2}


@router.post("/invoice-fixes/create-item")
async def create_stock_item(
    body: CreateItemRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Explicitly create a NEW stock item (+ its supplier variant) in Loaded and
    link the invoice line to it — WITHOUT receiving. A deliberate, controlled
    action: the caller supplies the stock group; the unit must already be
    resolved. Verified live: POST /1.0/stock/internal/items with the variant
    embedded in ``suppliers[]`` returns the created item.
    """
    lh = _Loaded(db, config_db, body.venue_id)
    inv = lh.invoice(body.invoice_id)
    line = next(
        (ln for ln in inv.get("lines") or [] if ln.get("id") == body.line_id), None
    )
    if not line:
        raise HTTPException(404, "invoice line not found")
    unit_id = body.unit_id or line.get("linkedUnitId")
    if not unit_id:
        raise HTTPException(400, "resolve the line's unit before creating the item")
    name = (body.name or line.get("description") or line.get("code") or "").strip()
    if not name or not body.group_id:
        raise HTTPException(400, "a name and a stock group are required")

    units = lh.get("/1.0/stock/internal/units")
    unit = next((u for u in (units or []) if u.get("id") == unit_id), None)
    if not unit:
        raise HTTPException(400, "unit not found in Loaded")
    utype = unit.get("stockUnitType") or "Weight"
    base = (
        next(
            (
                u
                for u in units
                if _norm(u.get("name")) == _BASE_UNIT_BY_TYPE.get(utype, "")
                and not u.get("datestampDeleted")
            ),
            None,
        )
        or unit
    )
    supplier_id = inv.get("linkedSupplierId")

    payload = {
        "name": name,
        "groupId": body.group_id,
        "unitType": _UNIT_TYPE_INDEX.get(utype, 0),
        "countingUnitId": base.get("id"),
        "countingUnitRatio": base.get("ratio") or 1.0,
        "orderingUnitId": unit_id,
        "orderingUnitRatio": unit.get("ratio") or 1.0,
        "globalSalesTaxSortOrder": 1,
        "defaultSupplierId": supplier_id,
        "defaultBrandId": body.brand_id,
        "itemType": "Default",
        "suppliers": [
            {
                "supplierId": supplier_id,
                "stockCode": line.get("code"),
                "unitId": unit_id,
                "unitCost": line.get("unitCost"),
                "brandId": body.brand_id,
                "defaultForSupplier": True,
                "description": line.get("description"),
            }
        ],
    }
    created = lh.request("POST", "/1.0/stock/internal/items", payload)
    item_id = created.get("id") if isinstance(created, dict) else None
    if not item_id:
        raise HTTPException(502, "Loaded did not return the created stock item")

    # Link the line to the new item (+ its unit) — a PUT that never receives.
    line["linkedItemId"] = item_id
    line["linkedUnitId"] = unit_id
    line["unit"] = unit.get("name")
    line["linkedUnitRatio"] = unit.get("ratio")
    lh.request("PUT", f"/1.0/stock/internal/invoices/{body.invoice_id}", inv)

    document = _reshape_draft_after_write(db, lh, body.venue_id, body.invoice_id)
    return {"message": f"Created stock item '{name}'", "document": document}


# ---------------------------------------------------------------------------
# Reference reads + full "Accept & Receive" for the editable card
# ---------------------------------------------------------------------------


@router.get("/invoice-fixes/outstanding")
async def outstanding_invoices(
    venue_id: str,
    response: Response,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Unreceived supplier invoices for the Invoices page.

    The Invoices dashboard self-loads from here (its functional page uses no
    connector loadAction), so the list refreshes on venue change and after a
    receive without a config-DB component-api row.
    """
    response.headers["Cache-Control"] = "no-store"
    lh = _Loaded(db, config_db, venue_id)
    invs = lh.get(
        "/1.0/stock/internal/invoices"
        "?from=1901-01-01&to=9999-12-31&status=NotReceived&page=0&pageSize=200"
    )
    invs = invs if isinstance(invs, list) else (invs or {}).get("data") or []
    return {
        "invoices": [
            i
            for i in invs
            if isinstance(i, dict)
            and not i.get("isReceived")
            and not i.get("deletedAt")
        ]
    }


@router.get("/invoice-fixes/units")
async def list_units(
    venue_id: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Loaded units catalog for the card's unit dropdown."""
    lh = _Loaded(db, config_db, venue_id)
    units = lh.get("/1.0/stock/internal/units")
    return {
        "units": [
            {
                "id": u.get("id"),
                "name": u.get("name"),
                "type": u.get("stockUnitType"),
                "ratio": u.get("ratio"),
            }
            for u in (units or [])
            if not u.get("datestampDeleted")
        ]
    }


@router.get("/invoice-fixes/stock-groups")
async def list_stock_groups(
    venue_id: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Loaded stock groups (subcategories) for the create-stock-item form."""
    lh = _Loaded(db, config_db, venue_id)
    return {"groups": _fetch_stock_groups(lh)}


@router.get("/invoice-fixes/suppliers")
async def list_suppliers(
    venue_id: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Loaded suppliers for the editor's Supplier dropdown.

    Reuses the purchase-order editor's configured `get_suppliers` component-api
    so we never hardcode a Loaded path here.
    """
    from app.services.component_api import ComponentApiError, execute_component_action

    try:
        result = execute_component_action(
            "purchase_order_editor", "get_suppliers", {}, venue_id, db, config_db
        )
    except ComponentApiError as e:
        raise HTTPException(502, str(e)) from e
    rows = result.get("data") if isinstance(result, dict) else result
    rows = rows if isinstance(rows, list) else (rows or {}).get("data") or []
    return {
        "suppliers": [
            {"id": s.get("id"), "name": s.get("name") or s.get("supplierName")}
            for s in rows
            if isinstance(s, dict) and not s.get("datestampDeleted")
        ]
    }


@router.get("/invoice-fixes/stock-items")
async def list_stock_items(
    venue_id: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Stock catalogue for the editor's Add Item search.

    Reuses the PO editor's `get_stock_items_detail` component-api and resolves
    each item's default supplier-variant (code / unit / cost) the same way
    po_display._resolve_lines does — so an added line carries what Loaded needs.
    """
    from app.services.component_api import ComponentApiError, execute_component_action

    try:
        result = execute_component_action(
            "purchase_order_editor",
            "get_stock_items_detail",
            {},
            venue_id,
            db,
            config_db,
        )
    except ComponentApiError as e:
        raise HTTPException(502, str(e)) from e
    items = result.get("data") if isinstance(result, dict) else result
    items = items if isinstance(items, list) else (items or {}).get("data") or []
    out = []
    for i in items:
        if not isinstance(i, dict):
            continue
        v = _default_variant(i) or {}
        out.append(
            {
                "id": i.get("id"),
                "name": i.get("name"),
                "code": v.get("stockCode"),
                "unit_id": v.get("unitId") or i.get("orderingUnitId"),
                "unit_ratio": v.get("unitRatio") or i.get("orderingUnitRatio") or 1,
                "unit_cost": v.get("unitCost") or 0,
            }
        )
    return {"stock_items": out}


# ---------------------------------------------------------------------------
# Match an unmatched line to an EXISTING stock item (LLM), else offer create
# ---------------------------------------------------------------------------


class MatchItemsRequest(BaseModel):
    venue_id: str
    invoice_id: str


class LinkItemRequest(BaseModel):
    venue_id: str
    invoice_id: str
    line_id: str
    item_id: str


@router.post("/invoice-fixes/match-items")
async def match_stock_items(
    body: MatchItemsRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """For each NEW (unlinked) item line, suggest an existing catalogue match (to
    link) or a normalized name + group (to create). Read-only — writes nothing.

    Thin wrapper over the shared LLM function (services/item_match.py) — the same
    matcher the review engine calls via ``norm.match_stock_items``.
    """
    return {
        "suggestions": suggest_item_matches_for_invoice(
            body.venue_id, body.invoice_id, db, config_db
        )
    }


@router.post("/invoice-fixes/link-item")
async def link_stock_item(
    body: LinkItemRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Link an EXISTING Loaded stock item to an unmatched invoice line — without
    receiving. Registers this supplier's variant (stockCode) on the item when it is
    missing, so the supplier's future invoices auto-match and the line stops
    recurring as NEW. NOTE: the add-variant PUT is inferred — verify live.
    """
    lh = _Loaded(db, config_db, body.venue_id)
    inv = lh.invoice(body.invoice_id)
    line = next(
        (ln for ln in inv.get("lines") or [] if ln.get("id") == body.line_id), None
    )
    if not line:
        raise HTTPException(404, "invoice line not found")
    item = lh.get(f"/1.0/stock/internal/items/{body.item_id}")
    if not isinstance(item, dict) or not item.get("id"):
        raise HTTPException(404, "stock item not found in Loaded")

    supplier_id = inv.get("linkedSupplierId")
    code = line.get("code")
    variants = [v for v in (item.get("suppliers") or []) if isinstance(v, dict)]
    existing = next(
        (
            v
            for v in variants
            if v.get("supplierId") == supplier_id
            and _norm(v.get("stockCode")) == _norm(code)
        ),
        None,
    )
    # Keep the line's already-resolved unit; else the variant's unit; else the
    # item's ordering unit — so the linked line ends receivable.
    unit_id = (
        line.get("linkedUnitId")
        or (existing or {}).get("unitId")
        or item.get("orderingUnitId")
    )
    units = lh.get("/1.0/stock/internal/units")
    unit = next((u for u in (units or []) if u.get("id") == unit_id), None)

    # Register the supplier variant when it doesn't exist yet (INFERRED PUT).
    registered = False
    if supplier_id and not existing:
        variants.append(
            {
                "supplierId": supplier_id,
                "stockCode": code,
                "unitId": unit_id,
                "unitCost": line.get("unitCost"),
                "brandId": line.get("linkedBrandId"),
                "defaultForSupplier": False,
                "description": line.get("description"),
            }
        )
        item["suppliers"] = variants
        lh.request("PUT", f"/1.0/stock/internal/items/{body.item_id}", item)
        registered = True

    line["linkedItemId"] = body.item_id
    if unit:
        line["linkedUnitId"] = unit.get("id")
        line["unit"] = unit.get("name")
        line["linkedUnitRatio"] = unit.get("ratio")
    lh.request("PUT", f"/1.0/stock/internal/invoices/{body.invoice_id}", inv)

    document = _reshape_draft_after_write(db, lh, body.venue_id, body.invoice_id)
    note = " (registered supplier variant)" if registered else ""
    return {"message": f"Linked to '{item.get('name')}'{note}", "document": document}


class ResolvePoRequest(BaseModel):
    venue_id: str
    invoice_id: str


def _extract_copy_po(lh: _Loaded, inv: dict, db: Session) -> dict:
    """Extract the BUYER PO number + supplier order number from the invoice copy.

    Loaded's own `purchaseOrderNumber` field is populated by the supplier feed
    and often holds the supplier's OWN order number (e.g. Bidfood "O/N"), not
    the buyer's PO. The buyer's PO — the one that matches a Loaded purchase
    order — is only on the printed copy (e.g. "Customer Order No"). Read it
    directly. Best-effort: any failure returns both as None.

    Shared by the `/invoice-fixes/resolve-po` endpoint and the draft auto-link.
    """
    file_id = inv.get("fileId")
    if not file_id:
        return {"customer_po": None, "supplier_order_number": None}
    try:
        b64, ctype = lh.file_base64(file_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("resolve-po file download failed: %s", exc)
        return {"customer_po": None, "supplier_order_number": None}

    from app.interpreter.llm_interpreter import call_llm

    schema = {
        "customer_purchase_order_number": (
            "string or null — the BUYER's / customer's purchase order number: "
            "the order number the buyer (the venue) raised in their own system. "
            "Suppliers label it 'Customer Order No', 'Cust Order No', 'Your "
            "Order', 'Your Ref', 'PO Number', 'Purchase Order', 'Order No'. It "
            "is the number to match against a purchase order."
        ),
        "supplier_order_number": (
            "string or null — the SUPPLIER's OWN order/reference number "
            "(labelled 'O/N', 'Our Order', 'Our Ref', 'Sales Order', 'Invoice "
            "No', 'Delivery No'). NOT the buyer's PO."
        ),
    }
    system_prompt = (
        "You extract identifiers from a supplier invoice exactly as printed. "
        "Distinguish the BUYER's purchase order number from the SUPPLIER's own "
        "order number — they are different. Return ONLY a JSON object matching "
        "this schema:\n" + json.dumps(schema, indent=1) + "\nUse null when a "
        "field is not present. Never guess."
    )
    documents = [
        {
            "type": "document",
            "source": {"type": "base64", "media_type": ctype, "data": b64},
        }
    ]
    try:
        parsed, _ = call_llm(
            system_prompt=system_prompt,
            user_prompt="Extract the buyer PO number and the supplier order number.",
            db=db,
            call_type="extraction",
            max_tokens=512,
            documents=documents,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("resolve-po extraction failed: %s", exc)
        return {"customer_po": None, "supplier_order_number": None}
    parsed = parsed if isinstance(parsed, dict) else {}
    return {
        "customer_po": parsed.get("customer_purchase_order_number"),
        "supplier_order_number": parsed.get("supplier_order_number"),
    }


@router.post("/invoice-fixes/resolve-po")
async def resolve_po(
    body: ResolvePoRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Extract the CUSTOMER purchase order number from the invoice copy (PDF)."""
    lh = _Loaded(db, config_db, body.venue_id)
    inv = lh.invoice(body.invoice_id)
    return _extract_copy_po(lh, inv, db)


@router.get("/invoice-fixes/file")
async def invoice_file(
    venue_id: str,
    invoice_id: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Stream the invoice's attached copy (the supplier PDF) for inline viewing.

    The card resolves the file id from the invoice itself — same source the
    PO-extraction path uses — so no extra field is needed in the consolidator
    payload.
    """
    import base64

    lh = _Loaded(db, config_db, venue_id)
    inv = lh.invoice(invoice_id)
    file_id = inv.get("fileId")
    if not file_id:
        raise HTTPException(404, "no invoice copy attached")
    b64, ctype = lh.file_base64(file_id)
    ref = inv.get("referenceNumber") or invoice_id
    ext = "pdf" if "pdf" in (ctype or "").lower() else "bin"
    return Response(
        content=base64.b64decode(b64),
        media_type=ctype or "application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="invoice-{ref}.{ext}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/invoice-fixes/status")
async def invoice_status(
    body: InvoiceStatusRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Current state of each card's invoice, read from Loaded.

    The card's "received" state is not stored in the thread — the display block
    is a snapshot from when the consolidator ran. On reload the card asks the
    system of record instead, so an invoice received earlier (here, or directly
    in Loaded) still renders as received.
    """
    lh = _Loaded(db, config_db, body.venue_id)
    statuses: dict[str, dict] = {}
    for inv_id in body.invoice_ids[:50]:
        try:
            inv = lh.invoice(inv_id)
        except Exception as exc:  # noqa: BLE001 — one bad id must not fail the rest
            logger.warning("status lookup failed for %s: %s", inv_id, exc)
            continue
        # Resolve the linked PO's own order number. Loaded's bulk PO list only
        # returns *open* orders, so once a PO is received it disappears from
        # there — the card can only name it by fetching it directly. (The
        # invoice's own purchaseOrderNumber is often the supplier's order
        # number, not the buyer PO, so it can't stand in for this.)
        linked_po_id = inv.get("linkedPurchaseOrderId")
        linked_po_number = None
        if linked_po_id:
            try:
                po = lh.get(f"/1.0/stock/internal/purchase-orders/{linked_po_id}")
                linked_po_number = (po or {}).get("orderNumber")
            except Exception as exc:  # noqa: BLE001 — naming it is best-effort
                logger.warning("linked PO lookup failed for %s: %s", linked_po_id, exc)
        statuses[inv_id] = {
            "is_received": bool(inv.get("isReceived")),
            "received_at": inv.get("receivedAt"),
            "reference_number": inv.get("referenceNumber"),
            "linked_purchase_order_id": linked_po_id,
            "linked_purchase_order_number": linked_po_number,
            "purchase_order_number": inv.get("purchaseOrderNumber"),
        }
    return {"statuses": statuses}


@router.get("/invoice-fixes/purchase-orders")
async def list_purchase_orders(
    venue_id: str,
    response: Response,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Loaded purchase orders for the card's Order Number picker."""
    # Never cache: POs change as invoices get received, and a stale list keeps
    # already-received POs in the picker (and can miss newly-added fields).
    response.headers["Cache-Control"] = "no-store"
    lh = _Loaded(db, config_db, venue_id)
    pos = lh.get("/1.0/stock/internal/purchase-orders?from=1901-01-01&to=9999-12-31")
    pos = pos if isinstance(pos, list) else (pos or {}).get("data") or []
    # Mirror Loaded's own receive screen: it bulk-loads purchase orders and
    # filters the Order Number dropdown client-side to the invoice's supplier
    # and to POs that aren't already invoiced/linked. Return the fields the
    # card needs to do the same filtering.
    return {
        "purchase_orders": [
            {
                "id": p.get("id"),
                "order_number": p.get("orderNumber"),
                "supplier_name": p.get("supplierName"),
                "supplier_id": p.get("supplierId"),
                "created_at": p.get("createdAt"),
                "linked_invoice_id": p.get("linkedInvoiceId"),
                "invoiced": bool(p.get("invoicedAt")),
                "received": bool(p.get("isReceived")),
                "status": p.get("status"),
            }
            for p in pos
            if not p.get("datestampDeleted")
        ]
    }


@router.post("/invoice-fixes/receive")
async def receive_invoice(
    body: ReceiveRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Apply the card's edits to a draft invoice and (optionally) receive it."""
    lh = _Loaded(db, config_db, body.venue_id)
    return _do_receive(lh, body)


# Read-only Loaded metadata refreshed on every open of an existing draft, so a
# draft created before a shaper field existed is healed (e.g. one opened before
# sale_tax_rate was added would otherwise compute $0 tax). Deliberately EXCLUDES
# the user's editable values (qty, cost, unit, PO link, notes, total,
# received_at) and the review's per-line fields (copy_*, reference_cost,
# quantity_ordered from the PO), which are matched/kept by id.
_META_HEADER_FIELDS = (
    "subtotal",
    "tax_amount",
    "discount_amount",
    "unit_cost_includes_tax",
    "loaded_invoice_fingerprint",
)
_META_LINE_FIELDS = ("brand", "tax_amount", "sale_tax_rate")


def _refresh_metadata(doc, detail: dict) -> None:
    from sqlalchemy.orm.attributes import flag_modified

    fresh = build_received_invoice_data(detail)
    data = dict(doc.data or {})
    for f in _META_HEADER_FIELDS:
        data[f] = fresh.get(f)
    fresh_lines = {ln.get("id"): ln for ln in fresh.get("lines") or []}
    for ln in data.get("lines") or []:
        src = fresh_lines.get(ln.get("id"))
        if src:
            for f in _META_LINE_FIELDS:
                ln[f] = src.get(f)
    doc.data = data
    flag_modified(doc, "data")


def _attach_po_reference(data: dict, lh) -> None:
    """Mirror Loaded's Receive Invoice reconciliation against the linked PO.

    Loaded reconciles an invoice line to its purchase-order line by the supplier
    CODE (the exact ordered variant) — NOT the stock item. A delivered line whose
    code isn't on the PO shows no ordered qty even when the SAME item was ordered
    under a different code, and that ordered line stays as "ordered, not received"
    (verified live: BROCCOLI delivered ``VEGF0223`` vs ordered ``165618`` — same
    item, shown as two separate lines). Only a CODELESS invoice line falls back to
    ``itemId`` (so a line Loaded left un-coded still picks up its ordered qty, e.g.
    PORK RACK), and its display code then borrows the PO line's ``itemCode``.

    Per line it sets ``quantity_ordered`` / ``reference_cost`` (from the PO),
    ``on_order`` (matched a PO line), ``display_code``, and — for a delivery under
    a different code — ``substitute_for`` (the original ordered line, shown as an
    expandable row). ``ordered_not_received`` lists only the PO items GENUINELY not
    delivered (no invoice line by code or item); an item delivered as a substitute
    is represented by its substitute line, not repeated here.

    Loaded leaves ``quantityOrdered`` null on the invoice detail, so the linked PO
    is the only source. Best-effort and idempotent — run on every open.
    """
    lines = data.get("lines") or []
    po_id = data.get("linked_purchase_order_id")
    if not po_id:
        # No order to reconcile against: clear any stale reference data.
        data.pop("ordered_not_received", None)
        data.pop("order_date", None)
        for ln in lines:
            ln["quantity_ordered"] = None
            ln["reference_cost"] = None
            ln["on_order"] = None
            ln["substitute_for"] = None
            ln["display_code"] = ln.get("code")
        return

    po = lh.get(f"/1.0/stock/internal/purchase-orders/{po_id}")
    if not isinstance(po, dict):
        return  # keep last-known-good reference data on a bad fetch

    data["order_date"] = po.get("createdAt")
    po_by_item: dict[str, dict] = {}
    po_by_code: dict[str, dict] = {}
    for pl in po.get("lines") or []:
        if not isinstance(pl, dict):
            continue
        if pl.get("itemId"):
            po_by_item.setdefault(pl.get("itemId"), pl)
        if pl.get("itemCode"):
            po_by_code.setdefault(_norm(pl.get("itemCode")), pl)

    consumed: set[int] = set()  # id() of PO lines matched to an invoice line
    for ln in lines:
        ln["substitute_for"] = None
        code = _norm(ln.get("code")) if ln.get("code") else ""
        item_id = ln.get("linked_item_id")
        # A coded line matches by CODE (Loaded's exact ordered variant). If its
        # code isn't on the PO but the SAME item WAS ordered under a different
        # code, it's a SUBSTITUTE — delivered under a different stock code. A
        # codeless line just matches by itemId.
        pl = None
        is_sub = False
        if code:
            pl = po_by_code.get(code)
            if not pl and item_id:
                pl = po_by_item.get(item_id)
                is_sub = pl is not None
        elif item_id:
            pl = po_by_item.get(item_id)
        if pl:
            consumed.add(id(pl))
            ln["quantity_ordered"] = pl.get("quantityOrdered")
            ln["reference_cost"] = pl.get("unitCost")
            ln["on_order"] = True
            ln["display_code"] = ln.get("code") or pl.get("itemCode")
            if is_sub:
                # The original ordered line this delivery stands in for — shown as
                # a full expandable row under the substitute; NOT also listed as
                # "ordered, not delivered" (it WAS delivered, under another code).
                ln["substitute_for"] = {
                    "code": pl.get("itemCode"),
                    "description": pl.get("itemName"),
                    "unit": pl.get("unitName"),
                    "quantity_ordered": pl.get("quantityOrdered"),
                    "unit_cost": pl.get("unitCost"),
                }
        else:
            ln["quantity_ordered"] = None
            ln["reference_cost"] = None
            ln["on_order"] = False
            ln["display_code"] = ln.get("code")

    # PO items with no matching invoice line — ordered but not delivered on this
    # invoice. Loaded shows these as receivable rows (ordered qty, received 0)
    # regardless of the PO's cumulative received, so we mirror that: the full
    # ordered qty, deduped by item.
    ordered_not_received = []
    seen: set = set()
    for pl in po.get("lines") or []:
        if not isinstance(pl, dict) or id(pl) in consumed:
            continue
        key = pl.get("itemId") or _norm(pl.get("itemCode"))
        if key in seen:
            continue
        seen.add(key)
        ordered_not_received.append(
            {
                "code": pl.get("itemCode"),
                "description": pl.get("itemName"),
                "unit": pl.get("unitName"),
                "quantity_ordered": pl.get("quantityOrdered"),
                "unit_cost": pl.get("unitCost"),
            }
        )
    data["ordered_not_received"] = ordered_not_received


@router.post("/invoice-fixes/draft")
async def create_receive_draft(
    body: DraftRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Open one outstanding invoice as a ``received_invoice`` working-document draft.

    This is the "fast open": the draft is shaped straight from the live
    ``get_invoice_detail`` (real qty/cost/unit and the linked PO), so the editor
    paints complete immediately. The PDF-copy review runs afterwards and is
    persisted into this same draft (``/invoice-fixes/review``), so a re-open
    never re-runs it.

    Idempotent per (venue, invoice): re-opening returns the existing unreceived
    draft rather than minting a second one — its accumulated edits and cached
    review survive.

    ``sync_mode="submit"`` deliberately: receiving is one atomic PUT, so edits
    accumulate locally and flush once via ``norm__receive_invoice`` /
    ``/invoice-fixes/receive`` — never per-keystroke through document_sync.
    """
    from app.db.models import WorkingDocument
    from app.routers.working_documents import _doc_to_dict

    existing = (
        db.query(WorkingDocument)
        .filter(
            WorkingDocument.doc_type == "received_invoice",
            WorkingDocument.venue_id == body.venue_id,
        )
        .all()
    )
    for doc in existing:
        ref = doc.external_ref or {}
        if ref.get("invoice_id") == body.invoice_id and not (doc.data or {}).get(
            "is_received"
        ):
            # Heal read-only metadata (tax rate, etc.) and re-attach the PO
            # reference (ordered qty / substitution flags / un-received lines)
            # from the live invoice + PO, keeping the user's edits and the
            # review. Best-effort.
            try:
                from sqlalchemy.orm.attributes import flag_modified

                lh = _Loaded(db, config_db, body.venue_id)
                detail = lh.invoice(body.invoice_id)
                _refresh_metadata(doc, detail)
                _attach_po_reference(doc.data, lh)
                # Fingerprint gate: the review is cached per invoice STATE. If
                # the invoice changed in Loaded since the review ran (its content
                # fingerprint moved — Loaded has no revision field), clear the
                # cached review so the editor's /review call re-runs it;
                # unchanged → the cache stands and no LLM/consolidator runs.
                # _refresh_metadata already updated loaded_invoice_fingerprint
                # from the live detail via _META_HEADER_FIELDS.
                d = doc.data
                if d.get("checks") and d.get("reviewed_invoice_fingerprint") != d.get(
                    "loaded_invoice_fingerprint"
                ):
                    d["checks"] = None
                    d["check_reasons"] = []
                    d["suggestions"] = []
                flag_modified(doc, "data")
                db.commit()
                db.refresh(doc)
            except Exception as exc:  # noqa: BLE001 — refresh is enhancement
                logger.info("draft metadata refresh failed: %s", exc)
            return _doc_to_dict(doc)

    lh = _Loaded(db, config_db, body.venue_id)
    detail = lh.invoice(body.invoice_id)
    data = build_received_invoice_data(detail)
    try:
        # Deterministic mirror only — PO retrieval from the copy is the
        # consolidator's job, run in /invoice-fixes/review.
        _attach_po_reference(data, lh)
    except Exception as exc:  # noqa: BLE001 — reference data is enhancement
        logger.info("draft PO reference unavailable: %s", exc)
    doc = WorkingDocument(
        thread_id=None,
        doc_type="received_invoice",
        connector_name="loadedhub",
        venue_id=body.venue_id,
        sync_mode="submit",
        data=data,
        external_ref={"invoice_id": body.invoice_id, "venue_id": body.venue_id},
        sync_status="synced",
        version=1,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return _doc_to_dict(doc)


# Fields make_fix_invoice() adds per line that the draft doesn't already carry —
# the PDF-copy comparison. Merged onto the matching draft line by id.
_REVIEW_LINE_FIELDS = (
    "copy_unit",
    "copy_quantity",
    "copy_unit_price",
    "copy_line_total",
    "recommended_unit",
    "copy_unit_mismatch",
    "copy_quantity_mismatch",
    "copy_duplicate",
    # Item-match suggestions for NEW lines (the engine's norm.match_stock_items
    # LLM function): link an existing item, or a normalized create name + group.
    "matched_item",
    "suggested_name",
    "suggested_group_id",
)


@router.post("/invoice-fixes/review")
async def review_receive_draft(
    body: DraftRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Run the PDF-copy review for one invoice and cache it into its draft.

    This is the "checks after" half of fast-open: the editor calls it once the
    draft is showing. It reuses the batch consolidator's exact gates (via a
    single-invoice mode) so the checklist never drifts, then persists the
    review's ``copy_*`` line comparisons, ``suggestions`` and packed ``checks``
    string into the SAME working document. A draft that already carries
    ``checks`` is returned untouched — the (LLM) PDF extraction runs once per
    invoice, not once per open.
    """
    from app.db.models import WorkingDocument
    from app.routers.working_documents import _doc_to_dict
    from sqlalchemy.orm.attributes import flag_modified

    doc = (
        db.query(WorkingDocument)
        .filter(
            WorkingDocument.doc_type == "received_invoice",
            WorkingDocument.venue_id == body.venue_id,
        )
        .all()
    )
    doc = next(
        (
            d
            for d in doc
            if (d.external_ref or {}).get("invoice_id") == body.invoice_id
            and not (d.data or {}).get("is_received")
        ),
        None,
    )
    if doc is None:
        raise HTTPException(404, "no open draft for this invoice — open it first")

    data = doc.data or {}
    if data.get("checks"):
        # Cached: the review already ran for this draft.
        return _doc_to_dict(doc)

    run_review_and_merge(data, body.venue_id, body.invoice_id, db, config_db)

    doc.data = data
    flag_modified(doc, "data")
    db.commit()
    db.refresh(doc)
    return _doc_to_dict(doc)


def run_review_and_merge(
    data: dict, venue_id: str, invoice_id: str, db: Session, config_db: Session
) -> None:
    """Run the review ENGINE for one invoice and merge its artifact onto ``data``.

    The single merge code path — used by ``/invoice-fixes/review`` (web) and the
    embedded builder (``app/mcp/receive_display.py``), so the two surfaces can
    never drift. Mutates ``data`` in place; caller persists. Raises HTTPException
    on misconfiguration (the embedded caller wraps best-effort).
    """
    import datetime as _dt

    from app.agents.internal_tools import execute_consolidator
    from app.db.config_models import ConnectorSpec
    from app.db.models import Venue

    spec = (
        config_db.query(ConnectorSpec)
        .filter(ConnectorSpec.connector_name == "loadedhub")
        .first()
    )
    tool_def = next(
        (
            t
            for t in (spec.tools if spec else []) or []
            if t.get("action") == "review_and_receive_invoices"
        ),
        None,
    )
    if not tool_def or not tool_def.get("consolidator_config"):
        raise HTTPException(
            500, "review_and_receive_invoices consolidator not configured"
        )

    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    if venue is None:
        raise HTTPException(404, "venue not found")
    tz = getattr(venue, "timezone", None) or "Pacific/Auckland"
    try:
        from zoneinfo import ZoneInfo

        today = _dt.datetime.now(ZoneInfo(tz)).date().isoformat()
    except Exception:
        today = _dt.date.today().isoformat()

    cfg = {**tool_def["consolidator_config"], "action": "review_and_receive_invoices"}
    review_params = {
        "venue": venue.name,
        "invoice_id": invoice_id,
        "today": today,
    }
    # If the USER picked a PO in the editor that Loaded hasn't linked yet (kept
    # local per the "validate without writeback" rule), pass it so the copy is
    # validated against it. Auto-resolving a PO from the copy is NOT done here —
    # the consolidator owns that (it reads the copy's PO number and resolves it).
    po_override = data.get("linked_purchase_order_id")
    if po_override:
        review_params["purchase_order_id"] = po_override
    result = execute_consolidator(cfg, review_params, db, None)
    fixes = (result.get("data") or {}).get("fix_invoices") or []
    fx = fixes[0] if fixes else None

    if fx:
        by_id = {ln.get("id"): ln for ln in fx.get("lines") or []}
        for ln in data.get("lines") or []:
            src = by_id.get(ln.get("id"))
            if src:
                for f in _REVIEW_LINE_FIELDS:
                    ln[f] = src.get(f)
        data["suggestions"] = fx.get("suggestions") or []
        data["check_reasons"] = fx.get("check_reasons") or []
        data["checks"] = fx.get("checks")
    else:
        # The invoice never reached the copy comparison (e.g. a credit note, or
        # no PDF attached). Record that the review ran so it isn't retried every
        # open; the editor renders fine without copy checks.
        data["suggestions"] = []
        data["check_reasons"] = []
        data.setdefault("checks", "")

    # (link_po suggestions that can't be actioned are already withheld by the
    # consolidator: in single-invoice review it resolves the referenced PO number
    # itself and only suggests a link when a real Loaded PO matched — a supplier's
    # own ref that resolves to nothing is never surfaced.)

    # The linked PO's ordered qty / reference cost / order date and the
    # substitution + un-received flags are attached on every draft open by
    # _attach_po_reference (matched by stock code), not here — the review is
    # cached, so anything attached here would never refresh on a re-open.
    data["reviewed_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    # Stamp the invoice version this review ran against: /draft compares it to
    # the live version on every open and clears the cache only when it moved.
    data["reviewed_invoice_fingerprint"] = data.get("loaded_invoice_fingerprint")
