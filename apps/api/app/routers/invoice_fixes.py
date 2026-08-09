"""Apply user-approved fixes to unreceivable LoadedHub invoices.

The review_and_receive_invoices engine proposes structured `fixes` (link a
purchase order; correct a line's unit of measure; delete a statement/duplicate
draft). The Receive Invoice editor renders them and POSTs ONE accepted fix at a
time to /invoice-fixes/accept; each is applied against the venue's LoadedHub
connector.

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

import datetime as _dt
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_permission
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
    attach_item_names,
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


def _apply_delete_invoice(lh: _Loaded, fix: dict, db: Session) -> str:  # noqa: ARG001 — db unused; uniform applier signature
    """Delete a draft invoice from Loaded — the accept for the engine's
    ``delete_invoice`` suggestion (a supplier STATEMENT uploaded as a draft).

    Verified live in the Loaded test env (02 Aug 2026):
    ``DELETE /1.0/stock/internal/invoices/{id}`` → 204 and the draft drops out
    of the NotReceived list. Only ever offered by the review engine, and only
    fired by the user's explicit Accept.
    """
    lh.request("DELETE", f"/1.0/stock/internal/invoices/{fix['invoice_id']}")
    return "Draft deleted from Loaded"


def _resolve_unit(lh: _Loaded, proposed: str) -> dict | None:
    """Find the Loaded unit whose name matches the proposed unit.

    Exact name first; for a simple (non-multipack) unit fall back to a
    guideline-equivalent match by (type, magnitude). A MULTIPACK proposed name
    ('5x3kg') matches by exact name ONLY — a ratio-equal but differently-named
    unit ('15 KG') is a different pack, so an unmatched multipack is created.
    """
    from app.services.invoice_units import (
        _unit_norm,
        is_multipack,
        multipack_equal,
        parse_unit,
    )

    units = lh.get("/1.0/stock/internal/units")
    units = [u for u in (units or []) if not u.get("datestampDeleted")]
    for u in units:
        # Case/whitespace-insensitive, but dots kept — the alnum norm used to
        # let '1.9 KG' name-match '19 KG'.
        if _unit_norm(u.get("name")) == _unit_norm(proposed):
            return u
    if is_multipack(proposed):
        # Component-equivalence, not fuzzier: '6x1L' resolves to an existing
        # '6 X 1 Litre' (same count, same inner size) instead of creating a
        # near-duplicate unit. A ratio-equal but differently-SHAPED pack
        # ('24 pack' vs '4x6 pack') still does NOT match.
        for u in units:
            if multipack_equal(u.get("name"), proposed):
                return u
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
            "'6x700ml' → 4.2, '12x1L' → 12, '4x6 pack' → 24 (4 packs of 6), "
            "'2kg' → 2, '500g' → 0.5, '12 pack' → 12, 'Each' → 1."
        ),
        "stock_unit_type": "one of 'Weight', 'Volume', 'Count'",
    }
    system_prompt = (
        "You convert a stock unit name into its base-unit size and type for a "
        "stock system (Weight→kilograms, Volume→litres, Count→items). Return "
        "ONLY a JSON object matching this schema:\n"
        + json.dumps(schema, indent=1)
        + "\nDimensions that are NOT weight, volume or count — length/metres, "
        "sheets, ply, micron — are DESCRIPTIVE, not the stock dimension: the "
        "unit counts ITEMS. '8x300m' (8 rolls of 300 metres) → "
        '{"ratio": 8, "stock_unit_type": "Count"}; \'2x50m\' → 2.'
        "\nNever guess — if the name carries no size at all (a bare packaging "
        'word like \'carton\'), return {"ratio": null, "stock_unit_type": null}.'
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


def _get_or_create_unit(lh: _Loaded, name: str, db: Session) -> tuple[dict, bool]:
    """The Loaded unit for ``name`` — resolved from the catalogue, else CREATED.
    The LLM supplies the base-unit ratio + type (robust to '5x3kg' /
    '6x1 Litre' conventions); only fires on a create. Returns (unit, created).
    Raises RuntimeError when no confident definition exists — a junk unit is
    never created.
    """
    unit = _resolve_unit(lh, name)
    if unit:
        return unit, False
    spec = _resolve_unit_spec(name, db)
    if not spec:
        raise RuntimeError(
            f"could not resolve a unit definition for '{name}' — "
            "create it in Loaded manually"
        )
    unit = lh.request(
        "POST",
        "/1.0/stock/internal/units",
        {
            "name": name,
            "ratio": spec["ratio"],
            "stockUnitType": spec["stock_unit_type"],
        },
    )
    # Loaded returns the created unit; re-fetch by name if the POST response
    # isn't the object itself.
    if not isinstance(unit, dict) or not unit.get("id"):
        unit = _resolve_unit(lh, name)
    if not isinstance(unit, dict) or not unit.get("id"):
        raise RuntimeError(f"created unit '{name}' but could not read it back")
    return unit, True


def _apply_unit(lh: _Loaded, fix: dict, db: Session) -> str:
    proposed = fix.get("proposed_unit", "")
    unit, created = _get_or_create_unit(lh, proposed, db)
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


_APPLIERS = {
    "link_po": _apply_link_po,
    "unit": _apply_unit,
    "delete_invoice": _apply_delete_invoice,
}


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

    if fix.get("type") == "delete_invoice":
        # The invoice no longer exists in Loaded. The draft docs stay as
        # TOMBSTONES (is_deleted) rather than being removed — old chat threads
        # still hold cards pointing at them, and a card must be able to say
        # "deleted" instead of hanging on a 404 forever.
        from sqlalchemy.orm.attributes import flag_modified

        from app.db.models import WorkingDocument
        from app.services.received_invoice import invalidate_conflicting_drafts

        ref_number = supplier = None
        for doc in (
            db.query(WorkingDocument)
            .filter(
                WorkingDocument.doc_type == "received_invoice",
                WorkingDocument.venue_id == body.venue_id,
            )
            .all()
        ):
            if (doc.external_ref or {}).get("invoice_id") == body.invoice_id:
                data = doc.data or {}
                ref_number = ref_number or data.get("reference_number")
                supplier = supplier or data.get("supplier_name")
                data["is_deleted"] = True
                data["status"] = "deleted"
                data["deleted_reason"] = fix.get("summary") or message
                doc.data = data
                flag_modified(doc, "data")
        db.commit()
        invalidate_conflicting_drafts(
            db,
            body.venue_id,
            body.invoice_id,
            reference_number=ref_number,
            supplier_name=supplier,
            received=False,
        )
        return {"message": message, "document": None, "deleted": True}

    document = _reshape_draft_after_write(db, lh, body.venue_id, body.invoice_id)
    return {"message": message, "document": document}


def _open_docs_for(db: Session, venue_id: str, invoice_id: str):
    """Every OPEN received_invoice doc for this invoice, canonical first.

    Twin docs exist historically (the fan-out used to key per thread); state
    changes must land on ALL of them so any bound card reads the same truth.
    Canonical = a doc that already carries a review, else the newest.
    """
    from app.db.models import WorkingDocument

    docs = [
        d
        for d in db.query(WorkingDocument)
        .filter(
            WorkingDocument.doc_type == "received_invoice",
            WorkingDocument.venue_id == venue_id,
        )
        .all()
        if (d.external_ref or {}).get("invoice_id") == invoice_id
        and not (d.data or {}).get("is_received")
        and not (d.data or {}).get("is_deleted")
    ]
    docs.sort(
        key=lambda d: (
            (d.data or {}).get("checks") is not None,
            d.created_at or _dt.datetime.min.replace(tzinfo=_dt.timezone.utc),
        ),
        reverse=True,
    )
    return docs


def _reshape_draft_after_write(
    db: Session, lh, venue_id: str, invoice_id: str
) -> dict | None:
    """Re-pull + re-SHAPE the venue's open drafts for an invoice from the fresh
    Loaded invoice, clearing the cached review so it re-runs. Used after any
    non-receiving write (accept a fix, create/link a stock item) — a write can
    change the invoice server-side (e.g. linking a PO re-matches lines), so the
    draft's lines/flags must come from Loaded. Applies to ALL open twin docs so
    every bound card converges. Best-effort; returns the canonical doc dict or
    None if there's no draft.
    """
    from sqlalchemy.orm.attributes import flag_modified

    from app.routers.working_documents import _doc_to_dict
    from app.services.received_invoice import carry_local_state

    docs = _open_docs_for(db, venue_id, invoice_id)
    if not docs:
        return None
    try:
        detail = lh.invoice(invoice_id)
        for doc in docs:
            fresh = build_received_invoice_data(detail)
            for k in ("working_document_id", "thread_id", "venue_id"):
                if k in (doc.data or {}):
                    fresh[k] = doc.data[k]
            # Local editor state (action log, struck flags, local item links,
            # locally-added lines) survives the rebuild.
            carry_local_state(fresh, doc.data or {})
            # PO resolution from the copy is the consolidator's job (runs in
            # /review); the draft only mirrors Loaded's own linked PO.
            _attach_po_reference(fresh, lh)
            attach_item_names(fresh, lh)
            fresh["checks"] = None
            fresh["suggestions"] = []
            fresh["check_reasons"] = []
            doc.data = fresh
            doc.version += 1
            flag_modified(doc, "data")
        db.commit()
        db.refresh(docs[0])
    except Exception as exc:  # noqa: BLE001 — refresh is best-effort
        logger.info("reshape draft failed: %s", exc)
    return _doc_to_dict(docs[0])


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
                # invoice-line cost: Loaded renamed unitCost → unitCostExclTax
                "unitCost": (
                    line.get("unitCostExclTax")
                    if line.get("unitCostExclTax") is not None
                    else line.get("unitCost")
                ),
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

    # Creating the item (+ its supplier variant) is the only Loaded write
    # here. The LINE links locally in the editor and lands in Loaded at
    # receive time — nothing touches the invoice until Accept & Receive.
    return {
        "message": f"Created stock item '{name}'",
        "item_id": item_id,
        "item_name": name,
        "unit_id": unit_id,
        "unit_name": unit.get("name"),
        "unit_ratio": unit.get("ratio"),
    }


class CreateUnitRequest(BaseModel):
    venue_id: str
    name: str


@router.post("/invoice-fixes/create-unit")
async def create_stock_unit(
    body: CreateUnitRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """The Loaded unit for a copy-delivered unit name the catalogue lacks —
    resolved if an equivalent exists, else explicitly created (same contract as
    create-item: the CREATE is the one Loaded write; the line takes the unit as
    a LOCAL edit in the editor and lands on the line + variant at receive).
    """
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "a unit name is required")
    lh = _Loaded(db, config_db, body.venue_id)
    try:
        unit, created = _get_or_create_unit(lh, name, db)
    except RuntimeError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "message": f"{'Created' if created else 'Found'} unit '{unit.get('name')}'",
        "created": created,
        "unit_id": unit.get("id"),
        "unit_name": unit.get("name"),
        "unit_ratio": unit.get("ratio"),
    }


class CreateSupplierRequest(BaseModel):
    venue_id: str
    name: str


@router.post("/invoice-fixes/create-supplier")
async def create_supplier(
    body: CreateSupplierRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """The Loaded supplier for a copy-printed supplier name the venue lacks —
    resolved if a record already covers it (normalized containment, the
    supplier-matching convention — never a duplicate record), else explicitly
    created (POST /suppliers, verified live 08 Aug 2026: 201 + the created
    object). Same contract as create-unit: the CREATE is the one Loaded
    write; the invoice takes the supplier as a LOCAL edit in the editor and
    lands on the header at receive.
    """
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "a supplier name is required")
    lh = _Loaded(db, config_db, body.venue_id)

    def _n(v):
        return "".join(ch for ch in str(v or "").lower() if ch.isalnum())

    rows = lh.get("/1.0/stock/internal/suppliers")
    rows = rows if isinstance(rows, list) else []
    target = _n(name)
    for s in rows:
        if not isinstance(s, dict) or s.get("removedAt") or s.get("datestampDeleted"):
            continue
        cand = _n(s.get("name"))
        if (
            len(cand) >= 3
            and len(target) >= 3
            and (cand == target or cand in target or target in cand)
        ):
            return {
                "message": f"Found existing supplier '{s.get('name')}'",
                "created": False,
                "supplier_id": s.get("id"),
                "supplier_name": s.get("name"),
            }

    created = lh.request("POST", "/1.0/stock/internal/suppliers", {"name": name})
    if not isinstance(created, dict) or not created.get("id"):
        raise HTTPException(502, "Loaded did not return the created supplier")
    return {
        "message": f"Created supplier '{created.get('name') or name}'",
        "created": True,
        "supplier_id": created.get("id"),
        "supplier_name": created.get("name") or name,
    }


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
            # Loaded renamed the supplier delete marker datestampDeleted -> removedAt
            # (Aug 2026); read both so either payload vintage filters correctly.
            if isinstance(s, dict)
            and not (s.get("removedAt") or s.get("datestampDeleted"))
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
                "unitCost": (
                    line.get("unitCostExclTax")
                    if line.get("unitCostExclTax") is not None
                    else line.get("unitCost")
                ),
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
    invoice_id: str | None = None,
    file_id: str | None = None,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Stream an invoice's attached copy (the supplier PDF) for inline viewing.

    By ``invoice_id`` for drafts (the file id resolves off the invoice detail —
    same source the PO-extraction path uses). Already-RECEIVED invoices 404 on
    that detail route, so their copies are requested by ``file_id`` directly —
    the review captures it off the received feed (duplicate_of_file_id).
    """
    import base64

    if not invoice_id and not file_id:
        raise HTTPException(422, "invoice_id or file_id is required")
    lh = _Loaded(db, config_db, venue_id)
    ref = invoice_id
    if not file_id:
        inv = lh.invoice(invoice_id)
        file_id = inv.get("fileId")
        if not file_id:
            raise HTTPException(404, "no invoice copy attached")
        ref = inv.get("referenceNumber") or invoice_id
    b64, ctype = lh.file_base64(file_id)
    ref = ref or "copy"
    ext = "pdf" if "pdf" in (ctype or "").lower() else "bin"
    return Response(
        content=base64.b64decode(b64),
        media_type=ctype or "application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="invoice-{ref}.{ext}"',
            "Cache-Control": "no-store",
        },
    )


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
    from app.services.received_invoice import invalidate_conflicting_drafts

    lh = _Loaded(db, config_db, body.venue_id)
    out = _do_receive(lh, body)
    if body.receive and isinstance(out, dict) and out.get("received"):
        # Sibling drafts may now be duplicates (same number, just received) or
        # reference a PO that just got invoiced — mark twins received and clear
        # conflicting cached reviews so their cards re-review, not re-receive.
        invalidate_conflicting_drafts(
            db,
            body.venue_id,
            body.invoice_id,
            reference_number=body.reference_number,
            po_ids=(body.linked_purchase_order_id, body.po_number),
        )
    return out


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
    if not po_id and data.get("split_po_id"):
        # Split order: the referenced PO is linked to a SIBLING invoice, so
        # this draft carries no Loaded link — but the user still needs the
        # order's reference data (QTY ORDERED, ordered-not-delivered) for
        # this delivery. Reconcile against the split PO WITHOUT touching the
        # link fields — from the FIRST open, before any accept: the engine
        # already validated the lines against this order, so showing what it
        # says is honest context for the split/remove decision.
        po_id = data.get("split_po_id")
    if not po_id:
        # No order to reconcile against: clear any stale reference data.
        data.pop("ordered_not_received", None)
        data.pop("ordered_received_elsewhere", None)
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
                # The PO line's stock item: lets the editor reconcile a LOCAL
                # item link immediately (delivered as a substitute → drop from
                # this list, badge the line) without waiting for a reopen.
                "item_id": pl.get("itemId"),
            }
        )

    # Split order: PO lines missing from THIS invoice may have been received
    # on the SIBLING delivery — those aren't "not delivered", they arrived on
    # the other invoice. Partition them into their own section (matched by
    # code, then item id — the same convention as above). Best-effort: if the
    # sibling can't be fetched, everything stays under "not delivered".
    ordered_received_elsewhere = []
    if (
        ordered_not_received
        and data.get("split_po_id")
        and not data.get("linked_purchase_order_id")
        and data.get("split_sibling_invoice_id")
    ):
        sib_qty: dict[str, object] = {}
        try:
            sib = lh.invoice(data["split_sibling_invoice_id"])
            for sl in (sib or {}).get("lines") or []:
                if not isinstance(sl, dict) or sl.get("deletedAt"):
                    continue
                for k in (_norm(sl.get("code")), str(sl.get("linkedItemId") or "")):
                    if k:
                        sib_qty[k] = sl.get("quantityReceived")
        except Exception as exc:  # noqa: BLE001 — reference data is enhancement
            logger.info("split sibling lines unavailable: %s", exc)
        if sib_qty:
            still_missing = []
            for o in ordered_not_received:
                k_code = _norm(o.get("code"))
                k_item = str(o.get("item_id") or "")
                hit = (
                    k_code
                    if k_code in sib_qty
                    else (k_item if k_item in sib_qty else None)
                )
                if hit is not None:
                    ordered_received_elsewhere.append(
                        {**o, "quantity_received": sib_qty[hit]}
                    )
                else:
                    still_missing.append(o)
            ordered_not_received = still_missing
    data["ordered_not_received"] = ordered_not_received
    data["ordered_received_elsewhere"] = ordered_received_elsewhere


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

    # Canonical pick: the twin that carries a review (else newest) — every
    # surface must resolve the same doc or the review appears to "move".
    docs = _open_docs_for(db, body.venue_id, body.invoice_id)
    if docs:
        doc = docs[0]
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
            attach_item_names(doc.data, lh)
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
        attach_item_names(data, lh)
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
    "copy_unit_cost_mismatch",
    "copy_duplicate",
    # The line is on the draft but NOT on the attached copy — drives the
    # editor's "remove line" suggestion (strike-style).
    "copy_missing",
    # The copy carries unit/size info that can't be read — the editor asks
    # the user to CONFIRM the unit (no proposed value).
    "unit_needs_confirmation",
    # Item-match suggestions for NEW lines (the engine's norm.match_stock_items
    # LLM function): link an existing item, or a normalized create name + group.
    "matched_item",
    "suggested_name",
    "suggested_group_id",
)


@router.post("/invoice-fixes/reset-validation")
async def reset_validation(
    body: DraftRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Wipe every cached validation artifact for one invoice — retest support.

    Deletes the invoice's PDF-extraction cache rows (matched by the extracted
    invoice_number, since the cache stores no file id) and rebuilds every open
    draft for the invoice fresh from Loaded — no cached review, no suggestion
    action log, no local line state. The editor's next /review then runs the
    whole validation from scratch, including the LLM extraction AND the
    stock-item match (its cache rows are keyed by this invoice's Loaded line
    ids, so a stale/declined match — the Sailor Jerry case, 08 Aug 2026 —
    is cleared here rather than surviving every reset). Only the small
    PO-number extraction stays cached (content-keyed, not per-invoice).
    """
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.models import DocumentExtraction
    from app.routers.working_documents import _doc_to_dict
    from app.services.received_invoice import _norm

    lh = _Loaded(db, config_db, body.venue_id)
    detail = lh.invoice(body.invoice_id)
    ref = _norm(detail.get("referenceNumber"))

    extractions_deleted = 0
    if ref:
        rows = (
            db.query(DocumentExtraction)
            .filter(DocumentExtraction.action == "download_invoice_file")
            .all()
        )
        for row in rows:
            data = row.data if isinstance(row.data, dict) else {}
            if _norm(data.get("invoice_number")) == ref:
                db.delete(row)
                extractions_deleted += 1

    line_ids = {
        ln.get("id")
        for ln in detail.get("lines") or []
        if isinstance(ln, dict) and ln.get("id")
    }
    if line_ids:
        for row in (
            db.query(DocumentExtraction)
            .filter(DocumentExtraction.action == "match_stock_items")
            .all()
        ):
            data = row.data if isinstance(row.data, dict) else {}
            if line_ids & set(data.keys()):
                db.delete(row)
                extractions_deleted += 1

    refreshed = None
    docs_reset = 0
    for doc in _open_docs_for(db, body.venue_id, body.invoice_id):
        fresh = build_received_invoice_data(detail)
        for k in ("working_document_id", "thread_id", "venue_id"):
            if k in (doc.data or {}):
                fresh[k] = doc.data[k]
        _attach_po_reference(fresh, lh)
        attach_item_names(fresh, lh)
        doc.data = fresh
        doc.version += 1
        flag_modified(doc, "data")
        docs_reset += 1
        refreshed = refreshed or doc
    db.commit()
    if refreshed is not None:
        db.refresh(refreshed)
    return {
        "extractions_deleted": extractions_deleted,
        "documents_reset": docs_reset,
        "document": _doc_to_dict(refreshed) if refreshed is not None else None,
    }


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
    from app.routers.working_documents import _doc_to_dict
    from sqlalchemy.orm.attributes import flag_modified

    docs = _open_docs_for(db, body.venue_id, body.invoice_id)
    if not docs:
        raise HTTPException(404, "no open draft for this invoice — open it first")

    doc = docs[0]  # canonical: carries a review if any twin does
    data = doc.data or {}
    if data.get("checks") is not None:
        # Cached: the review already ran. "" is a real result too (credit note /
        # no PDF — the review ran and produced no artifact); treating it as
        # uncached used to re-run the engine on every open.
        return _doc_to_dict(doc)

    run_review_and_merge(data, body.venue_id, body.invoice_id, db, config_db)

    if data.get("split_po_id") and not data.get("linked_purchase_order_id"):
        # Split order detected by the review: attach the order's reference
        # data (QTY ORDERED / ordered-not-delivered) NOW so the editor shows
        # the split state without a reopen — the open path re-attaches on
        # every subsequent open anyway. Best-effort.
        try:
            _attach_po_reference(data, _Loaded(db, config_db, body.venue_id))
        except Exception as exc:  # noqa: BLE001 — reference data is enhancement
            logger.info("split PO reference unavailable: %s", exc)

    doc.data = data
    doc.version += 1
    flag_modified(doc, "data")
    # Twin docs (historic per-thread duplicates) receive the same review state
    # so every bound card reads one truth — a card PATCHing its own twin used
    # to get a bare doc back and visibly lose the validation.
    for twin in docs[1:]:
        _copy_review_state(data, twin.data or {})
        twin.version += 1
        flag_modified(twin, "data")
    db.commit()
    db.refresh(doc)
    return _doc_to_dict(doc)


# ONE registry for header-level review state: every field the engine card
# emits that must survive BOTH merge paths — run_review_and_merge (artifact →
# doc) and _copy_review_state (doc → twin). Register new card fields HERE and
# nowhere else; a field emitted by the engine but missing from this tuple is a
# silently dropped feature (it has happened).
_REVIEW_HEADER_FIELDS = (
    "checks",
    "check_reasons",
    "suggestions",
    # buyer PO read off the copy + whether it resolved to no Loaded PO
    "copy_po",
    "po_unresolved",
    # copy-printed totals when Loaded's header disagrees (e.g. $0 feed)
    "copy_total_mismatch",
    "copy_total",
    "copy_subtotal",
    "copy_tax_amount",
    # the copy's printed invoice number when it disagrees with the draft's
    # reference — the editor's "Invoice number X → Y" suggestion
    "copy_invoice_number",
    # supplier printed on the copy + the matched Loaded record
    "copy_supplier",
    "supplier_differs",
    "matched_supplier_id",
    "matched_supplier_name",
    # the linked PO's supplier when it isn't this invoice's supplier — the
    # editor's "unlink this order" suggestion
    "po_supplier_mismatch",
    "po_supplier_name",
    # the already-received sibling when this draft is a duplicate — the editor
    # links to it in Loaded and serves its copy for side-by-side comparison
    # (file id captured off the received feed so the copy can be fetched
    # without a detail round-trip). The PO variant means the goods were
    # receipted straight against the order — no invoice document exists.
    "duplicate_of_invoice_id",
    "duplicate_of_file_id",
    "duplicate_of_purchase_order_id",
    # split-order state: the referenced PO is already linked to a sibling
    # invoice (second delivery vs doubled-up — see the engine's split branch)
    "split_order",
    "split_po_suggested",
    "split_remove_po",
    "split_po_id",
    "split_po_number",
    "split_sibling_invoice_id",
    "split_sibling_reference",
    "split_sibling_file_id",
)


def _ident(v):
    return v


# Per-field normalization applied when merging the engine artifact onto the
# doc (booleans coerced, list fields defaulted) — display semantics only.
_REVIEW_HEADER_COERCE = {
    "check_reasons": lambda v: v or [],
    "suggestions": lambda v: v or [],
    "po_unresolved": bool,
    "copy_total_mismatch": bool,
    "supplier_differs": bool,
    "po_supplier_mismatch": bool,
    "split_order": bool,
    "split_po_suggested": bool,
    "split_remove_po": bool,
}


def _copy_review_state(src: dict, dst: dict) -> None:
    """Copy the review-owned state from one doc payload onto a twin (in place):
    header review keys + per-line ``_REVIEW_LINE_FIELDS`` matched by line id.
    Local editor state on the twin (struck, links, log) is untouched."""
    for k in (
        *_REVIEW_HEADER_FIELDS,
        "reviewed_invoice_fingerprint",
        "loaded_invoice_fingerprint",
    ):
        dst[k] = src.get(k)
    src_by_id = {ln.get("id"): ln for ln in src.get("lines") or [] if ln.get("id")}
    for ln in dst.get("lines") or []:
        src_ln = src_by_id.get(ln.get("id"))
        if src_ln:
            for f in _REVIEW_LINE_FIELDS:
                ln[f] = src_ln.get(f)


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
        # ONE registry drives every header-level review field — see
        # _REVIEW_HEADER_FIELDS. Adding a card field only there wires both
        # merge paths (artifact → doc here, doc → twin in _copy_review_state).
        for k in _REVIEW_HEADER_FIELDS:
            data[k] = _REVIEW_HEADER_COERCE.get(k, _ident)(fx.get(k))
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


class AddToDojoRequest(BaseModel):
    venue_id: str
    invoice_id: str


@router.post("/invoice-fixes/add-to-dojo")
async def add_to_dojo(
    body: AddToDojoRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(require_permission("admin:system")),
):
    """One click on the invoice card: file this invoice's PDF as a dojo
    sample under its supplier's spec (created empty when the supplier has
    none) and kick the analysis agent in the background — the training
    loop's intake. Returns immediately; the analysis lands on the sample
    (Settings → Supplier Specs) in a minute or two.
    """
    import base64
    import threading

    from app.db.config_models import SupplierSpecSample
    from app.db.engine import _ConfigSessionLocal
    from app.services import spec_dojo

    lh = _Loaded(db, config_db, body.venue_id)
    det = lh.invoice(body.invoice_id)
    if not det.get("fileId"):
        raise HTTPException(400, "no invoice copy attached — nothing to add")
    supplier = det.get("supplierName") or ""

    # The request's config session is read-only; spec/sample writes go through
    # a dedicated RW session (same pattern as the engine-side internal tools).
    wcdb = _ConfigSessionLocal()
    try:
        spec, created = spec_dojo.find_or_create_spec_for_supplier(wcdb, supplier)
        existing = (
            wcdb.query(SupplierSpecSample)
            .filter(
                SupplierSpecSample.spec_id == spec.id,
                SupplierSpecSample.source_invoice_id == body.invoice_id,
            )
            .first()
        )
        if existing:
            sample_id = existing.id
        else:
            b64, ctype = lh.file_base64(det["fileId"])
            sample = SupplierSpecSample(
                spec_id=spec.id,
                label=f"{det.get('referenceNumber') or body.invoice_id}.pdf",
                content_type=ctype or "application/pdf",
                pdf_bytes=base64.b64decode(b64),
                source_venue_id=body.venue_id,
                source_invoice_id=body.invoice_id,
            )
            wcdb.add(sample)
            wcdb.commit()
            wcdb.refresh(sample)
            sample_id = sample.id
        spec_id, spec_name = spec.id, spec.name
    finally:
        wcdb.close()

    def _run_analysis() -> None:
        from app.db.engine import SessionLocal, _ConfigSessionLocal as _CSL

        wdb, acdb = SessionLocal(), _CSL()
        try:
            spec_dojo.analyse_sample(wdb, acdb, sample_id)
        except Exception:  # noqa: BLE001 — background; the sample records its own failure
            logger.exception("background dojo analysis failed for %s", sample_id)
        finally:
            wdb.close()
            acdb.close()

    threading.Thread(
        target=_run_analysis, daemon=True, name=f"dojo-analysis-{sample_id[:8]}"
    ).start()

    return {
        "sample_id": sample_id,
        "spec_id": spec_id,
        "spec_name": spec_name,
        "created_spec": created,
        "already_in_dojo": bool(existing),
        "analysis": "running",
    }
