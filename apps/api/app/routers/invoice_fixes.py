"""The Receive Invoice editor's server surface.

The review itself lives in ``services/invoice_review`` (the replica as the
single suggestion engine); this router wires it to the web editor:

- ``/draft`` opens (or heals) the working document; ``/review`` runs the
  replica review and squashes the payload onto every twin; ``/receive``
  builds the receive request server-side from the doc's working values.
- ``/accept`` applies the few suggestions that are LOADED WRITES rather than
  local doc edits (delete a duplicate/statement draft; the legacy link_po
  path). Everything else is a local working-document patch until receive.
- ``/create-item``, ``/create-unit``, ``/create-supplier`` create catalogue
  entries so blocked lines become receivable; the reference-data GETs feed
  the editor's pickers.

Loaded write contracts verified live in the LoadedHub test env (18 Jul 2026):
link_po = PUT the invoice with linkedPurchaseOrderId + purchaseOrderNumber
(no line re-matching); unit changes PUT the invoice line then PATCH the
supplier variant so future invoices match.
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
from app.services.autopilot_metrics import record_receive_outcome
from app.services.invoice_po_reference import (
    enrich_loaded_snapshot as _enrich_snapshot,
    loaded_reference as _loaded_reference,
    seed_working_from_loaded as _seed_working,
)
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
            (d.data or {}).get("reviewed_at") is not None
            or (d.data or {}).get("checks") is not None,
            d.created_at or _dt.datetime.min.replace(tzinfo=_dt.timezone.utc),
        ),
        reverse=True,
    )
    return docs


# The Loaded mirror resolves stock items the way Loaded's own screen does —
# from the CATALOGUE, by supplier code. Every draft open rebuilds the snapshot,
# so the catalogue is needed on each of them; a short in-process cache keeps
# that from becoming a component-api call per open (the catalogue is ~750 items
# and changes rarely).
def enrich_loaded_snapshot(
    data: dict, lh, venue_id: str, db: Session, *, seed_working: bool = False
) -> None:
    """Resolve the Loaded mirror's lines, best-effort.

    Without a catalogue nothing is guessed — the mirror keeps the supplier's
    raw text rather than showing a wrong item (which is exactly what resolving
    via the purchase order used to do).

    ``seed_working`` additionally starts the WORKING lines on Loaded's own
    resolution (see ``seed_working_from_loaded``). Pass it only where ``data``
    was just rebuilt from Loaded — never on a draft carrying user edits, or a
    dismissed link would quietly come back on the next open.
    """
    catalogue, units = _loaded_reference(venue_id, db, lh)
    if seed_working:
        _seed_working(data, catalogue=catalogue, units=units)
    _enrich_snapshot(data, catalogue=catalogue, units=units)


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
            enrich_loaded_snapshot(fresh, lh, venue_id, db, seed_working=True)
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


class CreateBrandRequest(BaseModel):
    venue_id: str
    name: str


@router.post("/invoice-fixes/create-brand")
async def create_stock_brand(
    body: CreateBrandRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """The Loaded brand record for a brand name the catalogue lacks.

    Loaded refuses to receive a line that names a brand it has no record for
    (its own client blocks on it, and ``do_receive`` guards the same way), and
    the brand text comes from LOADED's line — we deliberately do not extract
    brands from the copy, which would be noise.

    Resolve-first, exactly like create-unit and create-supplier: an existing
    record is returned rather than duplicated, so a double-click cannot litter
    the catalogue. The CREATE is the one Loaded write; the LINE takes the brand
    as a local edit and lands on receive.

    ``POST /1.0/stock/internal/brands {name}`` → 201 ``{id, name, masterId,
    datestampDeleted}`` (verified live on the test venue, 11 Aug 2026).
    """
    from app.services.invoice_replica import fetch_brands

    name = body.name.strip()
    if not name:
        raise HTTPException(400, "a brand name is required")
    lh = _Loaded(db, config_db, body.venue_id)

    existing = next(
        (b for b in fetch_brands(lh) if _norm(b.get("name")) == _norm(name)), None
    )
    if existing:
        return {
            "message": f"Found brand '{existing.get('name')}'",
            "created": False,
            "brand_id": existing.get("id"),
            "brand_name": existing.get("name"),
        }

    created = lh.request("POST", "/1.0/stock/internal/brands", {"name": name})
    brand_id = created.get("id") if isinstance(created, dict) else None
    if not brand_id:
        raise HTTPException(502, "Loaded did not return the created brand")
    return {
        "message": f"Created brand '{name}'",
        "created": True,
        "brand_id": brand_id,
        "brand_name": created.get("name") or name,
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
    """Receive an invoice from its working document (the doc-driven path).

    The canonical body is just ``{venue_id, invoice_id}``: the server loads
    the canonical doc, gates on the reviewed fingerprint (409 when the
    invoice changed in Loaded since the review), and builds the receive
    request from the doc's WORKING values — Loaded's draft + accepted
    suggestions + manual edits (``receive_request_from_doc``). A body that
    still carries explicit ``lines`` takes the legacy client-built path
    unchanged (older cards mid-deploy).
    """
    from app.services.received_invoice import (
        invalidate_conflicting_drafts,
        invoice_fingerprint,
        receive_request_from_doc,
    )

    lh = _Loaded(db, config_db, body.venue_id)
    req = body
    # Hoisted so the outcome recorder below can see the doc that was actually
    # received. The legacy client-built path leaves them empty — deliberately:
    # with no working document there is nothing honest to record.
    docs: list = []
    data: dict = {}
    if not body.lines:
        docs = _open_docs_for(db, body.venue_id, body.invoice_id)
        if not docs:
            raise HTTPException(404, "no open draft for this invoice — open it first")
        data = docs[0].data or {}
        want = data.get("reviewed_invoice_fingerprint")
        if want:
            live = lh.invoice(body.invoice_id)
            if invoice_fingerprint(live) != want:
                raise HTTPException(
                    409,
                    "this invoice changed in Loaded since it was reviewed — "
                    "reopen it to re-review before receiving",
                )
        req = receive_request_from_doc(data, body.venue_id, body.invoice_id)
    out = _do_receive(lh, req)
    if req.receive and isinstance(out, dict) and out.get("received"):
        # Sibling drafts may now be duplicates (same number, just received) or
        # reference a PO that just got invoiced — mark twins received and clear
        # conflicting cached reviews so their cards re-review, not re-receive.
        invalidate_conflicting_drafts(
            db,
            body.venue_id,
            body.invoice_id,
            reference_number=req.reference_number,
            po_ids=(req.linked_purchase_order_id, req.po_number),
        )
        # Would autopilot have produced this same result? Best-effort and
        # isolated — Loaded has already accepted the receive by now.
        record_receive_outcome(
            db,
            venue_id=body.venue_id,
            invoice_id=body.invoice_id,
            data=data,
            mode="interactive",
            actor="user",
            user_id=user.id,
            working_document_id=docs[0].id if docs else None,
            thread_id=docs[0].thread_id if docs else None,
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
    # Server-derived, so it heals onto drafts opened before it existed.
    "is_credit_note",
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
    # The admin "Loaded view" mirror always shows Loaded's CURRENT truth,
    # regardless of local edits — refreshed on every open.
    data["loaded_snapshot"] = fresh.get("loaded_snapshot")
    doc.data = data
    flag_modified(doc, "data")


def _attach_po_reference(data: dict, lh) -> None:
    """Fetch the linked order and project it onto the draft.

    Thin delegate to ``services.invoice_po_reference`` — the projection lives
    there because it must also run (pure, no network) after every working-
    document patch, and because it is the ONE writer of that derived state.
    """
    from app.services.invoice_po_reference import attach_po_reference

    attach_po_reference(data, lh)


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
            enrich_loaded_snapshot(doc.data, lh, body.venue_id, db)
            # Fingerprint gate: the review is cached per invoice STATE. If
            # the invoice changed in Loaded since the review ran (its content
            # fingerprint moved — Loaded has no revision field), clear the
            # cached review so the editor's /review call re-runs it;
            # unchanged → the cache stands and no LLM/consolidator runs.
            # _refresh_metadata already updated loaded_invoice_fingerprint
            # from the live detail via _META_HEADER_FIELDS.
            d = doc.data
            if (d.get("checks") or d.get("reviewed_at")) and d.get(
                "reviewed_invoice_fingerprint"
            ) != d.get("loaded_invoice_fingerprint"):
                d["checks"] = None  # legacy-shape marker
                d["check_reasons"] = []
                d["suggestions"] = []
                d["reviewed_at"] = None  # replica_v1 marker
                d["issues"] = []
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
        enrich_loaded_snapshot(data, lh, body.venue_id, db, seed_working=True)
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
        enrich_loaded_snapshot(fresh, lh, body.venue_id, db, seed_working=True)
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


class ReviewRequest(BaseModel):
    venue_id: str
    invoice_id: str
    # Re-run the replica: recompute the review and SQUASH the working values
    # back to fresh Loaded data + fresh suggestions, discarding local edits
    # and accept/dismiss state (the editor confirms before sending this).
    force: bool = False


@router.post("/invoice-fixes/review")
async def review_receive_draft(
    body: ReviewRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Run the replica review for one invoice and cache it into its draft.

    The single review pipeline (``services/invoice_review.review_invoice``):
    working values from Loaded's draft, the replica sidecar, unified
    suggestions with explanations, and confidence issues. A draft that
    already carries a replica_v1 review is returned untouched unless
    ``force`` — the (LLM) PDF extraction runs once per invoice, not per open.
    Re-running REPLACES the doc payload wholesale (squash semantics).
    """
    from sqlalchemy.orm.attributes import flag_modified

    from app.routers.working_documents import _doc_to_dict
    from app.services.invoice_review import DOC_SCHEMA, review_invoice

    docs = _open_docs_for(db, body.venue_id, body.invoice_id)
    if not docs:
        raise HTTPException(404, "no open draft for this invoice — open it first")

    doc = docs[0]  # canonical: carries a review if any twin does
    data = doc.data or {}
    if (
        data.get("doc_schema") == DOC_SCHEMA
        and data.get("reviewed_at")
        and not body.force
    ):
        return _doc_to_dict(doc)

    # require_valid_po=False: the interactive card has a human looking at it —
    # a PO-less invoice gets a note, not a block (the batch/autopilot default
    # stays strict).
    fresh = review_invoice(
        db, config_db, body.venue_id, body.invoice_id, require_valid_po=False
    )
    try:
        lh = _Loaded(db, config_db, body.venue_id)
        _attach_po_reference(fresh, lh)
        attach_item_names(fresh, lh)
        enrich_loaded_snapshot(fresh, lh, body.venue_id, db)
    except Exception as exc:  # noqa: BLE001 — reference data is enhancement
        logger.info("review PO reference unavailable: %s", exc)

    for target in docs:
        payload = dict(fresh)
        for k in ("working_document_id", "thread_id", "venue_id"):
            if k in (target.data or {}):
                payload[k] = target.data[k]
        target.data = payload
        target.version += 1
        flag_modified(target, "data")
    db.commit()
    db.refresh(doc)
    return _doc_to_dict(doc)


class AddToDojoRequest(BaseModel):
    venue_id: str
    invoice_id: str


def _stage_and_analyse(
    db: Session, venue_id: str, invoice_id: str, *, draft: bool, analyse: bool = True
) -> dict:
    """File an invoice's PDF as a dojo sample and (optionally) kick the sensei.

    Shared by Add-to-dojo (admin, analysed) and Cannot-receive (any user, not
    analysed — an unattended Opus pass per press is real money).

    Both file a REAL sample, not a draft. ``draft`` is the Dojo page's
    "somebody expanded this row" state, which is invisible in the UI by
    design: drafts are excluded from "awaiting review"
    (``dojo/overview``) and never show the "in dojo" chip. Filing a human's
    explicit "Norm can't do this one" that way made the button look broken —
    it left the invoice sitting in the outstanding list exactly as before.
    A sample with no baseline is already harmless to regression: it scores
    "new", never "fail", so the draft flag was protecting nothing here.

    Raises RuntimeError when the invoice has no copy attached.
    """
    from app.services import sensei_runner, spec_dojo

    staged = spec_dojo.stage_invoice_sample(db, venue_id, invoice_id, draft=draft)
    sample_id = staged["sample_id"]
    if not analyse:
        return staged

    # Out of process where a job is configured, in a thread otherwise. This
    # used to be a bare daemon thread, which died with its container and left
    # the sample reading "sensei analysing…" until a staleness rule noticed.
    where = sensei_runner.start_analysis(sample_id)
    logger.info("dojo analysis for %s started (%s)", sample_id, where)
    return staged


class CannotReceiveRequest(BaseModel):
    venue_id: str
    invoice_id: str
    reason: str | None = None


@router.post("/invoice-fixes/cannot-receive")
async def cannot_receive(
    body: CannotReceiveRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """ "Norm can't do this one" — file it for training and record the verdict.

    Deliberately NOT admin-only (unlike add-to-dojo): the person who hits the
    problem is the one who should flag it, which is also what gives the
    autopilot report its coverage. The sample joins the dojo proper so it is
    visible in "awaiting review" — a human verdict that must not be silently
    filed where nobody looks. The invoice is left untouched in Loaded and its
    working document stays open, because "couldn't receive today, received
    fine tomorrow" is a real and useful sequence.
    """
    docs = _open_docs_for(db, body.venue_id, body.invoice_id)
    data = (docs[0].data or {}) if docs else {}

    staged: dict = {}
    stage_error: str | None = None
    try:
        staged = _stage_and_analyse(
            db, body.venue_id, body.invoice_id, draft=False, analyse=False
        )
    except RuntimeError as exc:
        # No PDF attached — it cannot be staged, but the human's verdict is
        # the measurement and losing it would defeat the feature.
        stage_error = str(exc)

    record_receive_outcome(
        db,
        venue_id=body.venue_id,
        invoice_id=body.invoice_id,
        data=data,
        mode="interactive",
        actor="user",
        user_id=user.id,
        working_document_id=docs[0].id if docs else None,
        thread_id=docs[0].thread_id if docs else None,
        received=False,
        outcome_override="dojo",
        dojo={
            "sample_id": staged.get("sample_id"),
            "spec_id": staged.get("spec_id"),
            "spec_name": staged.get("spec_name"),
            "reason": body.reason,
            "staged": bool(staged),
            "error": stage_error,
        },
    )
    return {
        "staged": bool(staged),
        "sample_id": staged.get("sample_id"),
        "spec_name": staged.get("spec_name"),
        # Was it ALREADY a dojo sample before this press? Staging is idempotent,
        # so a second press is a silent no-op — the card needs to say "already
        # filed" rather than claim it just did something.
        "already_in_dojo": bool(staged.get("already_in_dojo")),
        "reason": stage_error,
    }


@router.post("/invoice-fixes/add-to-dojo")
async def add_to_dojo(
    body: AddToDojoRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("admin:system")),
):
    """One click on the invoice card: file this invoice's PDF as a dojo
    sample under its supplier's spec (created empty when the supplier has
    none) and kick the SENSEI in the background — the training
    loop's intake. Returns immediately; the analysis lands on the sample
    (Settings → Supplier Specs) in a minute or two.
    """
    try:
        staged = _stage_and_analyse(db, body.venue_id, body.invoice_id, draft=False)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "sample_id": staged["sample_id"],
        "spec_id": staged["spec_id"],
        "spec_name": staged["spec_name"],
        "created_spec": staged["created_spec"],
        "already_in_dojo": staged["already_in_dojo"],
        "analysis": "running",
    }
