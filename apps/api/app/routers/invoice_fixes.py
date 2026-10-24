"""The Receive Invoice editor's server surface.

Every endpoint here is a plain ``def``, NOT ``async def`` — deliberately.
These handlers do sync-blocking work (LoadedHub HTTP calls, SQLAlchemy),
and an ``async def`` runs that work ON the event loop, freezing every other
request while Loaded is slow (this froze the whole local site during dojo
polling, 16 Aug 2026 — same rule as routers/supplier_spec_dojo.py). Plain
``def`` handlers run in FastAPI's threadpool, where blocking is harmless.

The review itself lives in ``services/invoice_review`` (the replica as the
single suggestion engine); this router wires it to the web editor:

- ``/draft`` opens (or heals) the working document; ``/review`` runs the
  replica review and squashes the payload onto every twin; ``/receive``
  builds the receive request server-side from the doc's working values.
- ``/accept`` applies the one suggestion that is a LOADED WRITE rather than a
  local doc edit: deleting a draft that shouldn't be received (a duplicate, a
  statement, no copy, an unreadable one). Everything else is a local
  working-document patch until receive.
- ``/create-item``, ``/create-unit``, ``/create-brand``, ``/create-supplier``
  create catalogue entries so blocked lines become receivable; the
  reference-data GETs feed the editor's pickers. Both live in
  ``services/invoice_catalog``, shared with autopilot.

Loaded write contracts verified live in the LoadedHub test env (18 Jul 2026),
written today by ``do_receive`` at receive time: a PO link is a PUT of the
invoice with linkedPurchaseOrderId + purchaseOrderNumber (no line
re-matching); a unit change PUTs the invoice line then PATCHes the supplier
variant so future invoices match.
"""

from __future__ import annotations

import copy
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
from app.services import card_actions, screen_access
from app.services import invoice_catalog as catalog
from app.services.invoice_po_reference import (
    enrich_loaded_snapshot as _enrich_snapshot,
    loaded_reference as _loaded_reference,
    rows_cached_for_linked_order,
    start_from_screen as _start_from_screen,
)
from app.services.received_invoice import (
    LoadedInvoiceClient as _Loaded,
    attach_item_names,
    build_received_invoice_data,
    open_drafts as _open_docs_for,
)

logger = logging.getLogger(__name__)
# The Receive Invoice editor's routes, for a role that reaches its screens
# (services/screen_access): a venue's login alone let a Kitchen role receive
# invoices, delete drafts in Loaded and read every supplier's prices.
router = APIRouter(
    dependencies=[
        Depends(screen_access.needs("receive_invoice_editor", "invoices_dashboard"))
    ]
)


def _catalog(fn, *args, **kwargs):
    """Run a catalogue call; its refusal reaches the card as an HTTP error."""
    try:
        return fn(*args, **kwargs)
    except catalog.CatalogError as e:
        raise HTTPException(e.status, e.message) from e


class DraftRequest(BaseModel):
    venue_id: str
    invoice_id: str


def _apply_delete_invoice(lh: _Loaded, fix: dict, db: Session) -> str:  # noqa: ARG001 — db unused; uniform applier signature
    """Delete a draft invoice from Loaded — the accept for the engine's
    ``delete_invoice`` suggestion (a supplier STATEMENT uploaded as a draft).

    Verified live in the Loaded test env (02 Aug 2026):
    ``DELETE /1.0/stock/internal/invoices/{id}`` → 204 and the draft drops out
    of the NotReceived list. Only ever offered by the review engine, and only
    fired by the user's explicit Accept.
    """
    lh.request("DELETE", f"/1.0/stock/internal/invoices/{fix['invoice_id']}")
    # An Ordermentum supplier's number seen to drift proves earlier held pairs
    # real: their later copies (already received) go with this one — the
    # first received stays (services/ordermentum_pairs, owner 21 Oct 2026).
    also = [i for i in fix.get("also_remove") or [] if i and i != fix["invoice_id"]]
    for other in also:
        lh.request("DELETE", f"/1.0/stock/internal/invoices/{other}")
    if also:
        return f"Draft deleted from Loaded, with {len(also)} earlier second copy(ies)"
    return "Draft deleted from Loaded"


_APPLIERS = {
    "delete_invoice": _apply_delete_invoice,
}


class AcceptFixRequest(BaseModel):
    venue_id: str
    invoice_id: str
    fix: dict


@router.post("/invoice-fixes/accept")
def accept_invoice_fix(
    body: AcceptFixRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Apply the one suggestion that writes to Loaded without receiving:
    ``delete_invoice`` (the web editor's Accept on a draft that shouldn't be
    received). Every other suggestion is a local working-document edit that
    rides the receive; an unknown fix type is a 400.
    """
    card_actions.venue_for(db, user, body.venue_id)
    fix = body.fix or {}
    applier = _APPLIERS.get(fix.get("type"))
    if not applier:
        raise HTTPException(400, f"unknown fix type: {fix.get('type')}")

    lh = _Loaded(db, config_db, body.venue_id)
    message = _catalog(applier, lh, fix, db)
    card_actions.record(
        db,
        user,
        body.venue_id,
        f"invoice fix: {fix.get('type')}",
        f"invoice {body.invoice_id} — {message}"[:300],
    )
    db.commit()

    # The invoice no longer exists in Loaded. The draft docs stay as
    # TOMBSTONES (is_deleted) rather than being removed — old chat threads
    # still hold cards pointing at them, and a card must be able to say
    # "deleted" instead of hanging on a 404 forever.
    from sqlalchemy.orm.attributes import flag_modified

    from app.services.received_invoice import (
        cards_for_invoice,
        invalidate_conflicting_drafts,
    )

    ref_number = supplier = None
    kept = None  # the card's data, for the record below
    for doc in cards_for_invoice(db, body.venue_id, body.invoice_id).all():
        if (doc.external_ref or {}).get("invoice_id") == body.invoice_id:
            data = doc.data or {}
            ref_number = ref_number or data.get("reference_number")
            supplier = supplier or data.get("supplier_name")
            data["is_deleted"] = True
            data["status"] = "deleted"
            data["deleted_reason"] = fix.get("summary") or message
            doc.data = data
            flag_modified(doc, "data")
            if kept is None or data.get("reviewed_at"):
                kept = (doc, data)
    db.commit()
    # A person's delete is an outcome like Norm's: the activity page stops
    # listing the invoice as stopped, and shows who dealt with it.
    from app.services.autopilot_metrics import record_receive_outcome

    record_receive_outcome(
        db,
        venue_id=body.venue_id,
        invoice_id=body.invoice_id,
        data=(kept[1] if kept else {})
        or {"reference_number": ref_number, "supplier_name": supplier},
        mode="interactive",  # the web card, as its receive records
        actor="user",
        user_id=getattr(user, "id", None),
        working_document_id=kept[0].id if kept else None,
        received=False,
        outcome_override="deleted",
        extra_detail={
            "deleted": {
                "kind": fix.get("reason") or "other",
                "reason": fix.get("summary") or message,
            }
        },
    )
    invalidate_conflicting_drafts(
        db,
        body.venue_id,
        body.invoice_id,
        reference_number=ref_number,
        supplier_name=supplier,
        received=False,
    )
    return {"message": message, "document": None, "deleted": True}


# The Loaded mirror resolves stock items the way Loaded's own screen does —
# from the CATALOGUE, by supplier code. Every draft open rebuilds the snapshot,
# so the catalogue is needed on each of them; a short in-process cache keeps
# that from becoming a component-api call per open (the catalogue is ~750 items
# and changes rarely).
def enrich_loaded_snapshot(
    data: dict, lh, venue_id: str, db: Session, *, start_working: bool = False
) -> None:
    """Resolve the Loaded mirror's lines, best-effort.

    Without a catalogue nothing is guessed — the mirror keeps the supplier's
    raw text rather than showing a wrong item (which is exactly what resolving
    via the purchase order used to do).

    ``start_working`` additionally opens the WORKING lines on what that
    resolution found (``start_from_screen``). Pass it only where ``data`` was
    just built from Loaded — never on a draft carrying decisions or edits.
    """
    catalogue, units = _loaded_reference(venue_id, db, lh)
    _enrich_snapshot(data, catalogue=catalogue, units=units)
    if start_working:
        from app.services.invoice_po_reference import project_po_reference

        _start_from_screen(data)
        project_po_reference(data)  # the order rows follow the opened links


class CreateItemRequest(BaseModel):
    venue_id: str
    invoice_id: str
    line_id: str
    group_id: str
    name: str | None = None
    unit_id: str | None = None
    brand_id: str | None = None
    # The supplier the card shows — picked there, it reaches Loaded's invoice
    # only on receive, so Loaded's own header can still be empty.
    supplier_id: str | None = None


@router.post("/invoice-fixes/create-item")
def create_stock_item(
    body: CreateItemRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Create a NEW stock item (+ its supplier variant) in Loaded for an invoice
    line — or hand back the same-named one already there. Nothing touches the
    invoice: the card links the line locally and it lands on receive."""
    card_actions.venue_for(db, user, body.venue_id)
    lh = _Loaded(db, config_db, body.venue_id)
    out = _catalog(
        catalog.create_item,
        lh,
        db,
        config_db,
        venue_id=body.venue_id,
        invoice_id=body.invoice_id,
        line_id=body.line_id,
        name=body.name,
        group_id=body.group_id,
        unit_id=body.unit_id,
        brand_id=body.brand_id,
        supplier_id=body.supplier_id,
    )
    if out["created"]:
        card_actions.record(
            db,
            user,
            body.venue_id,
            "created a stock item",
            f"'{out['item_name']}' ({out['item_id']})",
        )
        db.commit()
    verb = "Created" if out["created"] else "Found"
    return {"message": f"{verb} stock item '{out['item_name']}'", **out}


class CreateUnitRequest(BaseModel):
    venue_id: str
    name: str
    # The person's own type and size (the new-unit form). Without them the
    # name is read, then the LLM asked — the accept-a-suggestion path.
    ratio: float | None = None
    unit_type: str | None = None


@router.post("/invoice-fixes/create-unit")
def create_stock_unit(
    body: CreateUnitRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """The Loaded unit for a name the catalogue lacks — resolved if an
    equivalent exists, else created. The line takes it as a local edit."""
    card_actions.venue_for(db, user, body.venue_id)
    lh = _Loaded(db, config_db, body.venue_id)
    unit, created = _catalog(
        catalog.get_or_create_unit,
        lh,
        body.name,
        db,
        ratio=body.ratio,
        unit_type=body.unit_type,
    )
    if created:
        card_actions.record(
            db, user, body.venue_id, "created a unit", f"'{unit.get('name')}'"
        )
        db.commit()
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
def create_stock_brand(
    body: CreateBrandRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """The Loaded brand record for a name the catalogue lacks — Loaded refuses
    to receive a line naming a brand it has no record for."""
    card_actions.venue_for(db, user, body.venue_id)
    lh = _Loaded(db, config_db, body.venue_id)
    brand, created = _catalog(catalog.get_or_create_brand, lh, body.name)
    if created:
        card_actions.record(
            db, user, body.venue_id, "created a brand", f"'{brand['name']}'"
        )
        db.commit()
    return {
        "message": f"{'Created' if created else 'Found'} brand '{brand['name']}'",
        "created": created,
        "brand_id": brand["id"],
        "brand_name": brand["name"],
    }


class CreateSupplierRequest(BaseModel):
    venue_id: str
    name: str
    # The invoice it's created from: a new record takes the details the
    # review read off that invoice (catalog.draft_supplier_details).
    invoice_id: str | None = None


@router.post("/invoice-fixes/create-supplier")
def create_supplier(
    body: CreateSupplierRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """The Loaded supplier for a printed name the venue lacks — resolved if a
    record already covers it, else created. The invoice takes it locally."""
    card_actions.venue_for(db, user, body.venue_id)
    lh = _Loaded(db, config_db, body.venue_id)
    details = catalog.draft_supplier_details(db, body.venue_id, body.invoice_id or "")
    supplier, created = _catalog(catalog.get_or_create_supplier, lh, body.name, details)
    if created:
        card_actions.record(
            db, user, body.venue_id, "created a supplier", f"'{supplier['name']}'"
        )
        db.commit()
    return {
        "message": (
            f"Created supplier '{supplier['name']}'"
            if created
            else f"Found existing supplier '{supplier['name']}'"
        ),
        "created": created,
        "supplier_id": supplier["id"],
        "supplier_name": supplier["name"],
    }


# ---------------------------------------------------------------------------
# Reference reads + full "Accept & Receive" for the editable card
# ---------------------------------------------------------------------------


@router.get("/invoice-fixes/outstanding")
def outstanding_invoices(
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
    card_actions.venue_for(db, user, venue_id)
    response.headers["Cache-Control"] = "no-store"
    from app.services.received_invoice import outstanding_invoice_rows

    lh = _Loaded(db, config_db, venue_id)
    return {"invoices": outstanding_invoice_rows(lh)}


@router.get("/invoice-fixes/units")
def list_units(
    venue_id: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Loaded units catalog for the card's unit dropdown."""
    card_actions.venue_for(db, user, venue_id)
    return {"units": catalog.units(_Loaded(db, config_db, venue_id))}


@router.get("/invoice-fixes/unit-spec")
def unit_spec(
    venue_id: str,
    name: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """What the card's new-unit form opens with: Loaded's existing unit for
    this name, else Norm's reading of its type and size."""
    card_actions.venue_for(db, user, venue_id)
    return catalog.suggest_unit(_Loaded(db, config_db, venue_id), name, db)


@router.get("/invoice-fixes/brands")
def list_brands(
    venue_id: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Loaded brands for the card's brand picker."""
    card_actions.venue_for(db, user, venue_id)
    return {"brands": catalog.brands(_Loaded(db, config_db, venue_id))}


@router.get("/invoice-fixes/stock-groups")
def list_stock_groups(
    venue_id: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Loaded stock groups (subcategories) for the create-stock-item form."""
    card_actions.venue_for(db, user, venue_id)
    return {"groups": catalog.stock_groups(_Loaded(db, config_db, venue_id))}


@router.get("/invoice-fixes/suppliers")
def list_suppliers(
    venue_id: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Loaded suppliers for the editor's Supplier dropdown."""
    card_actions.venue_for(db, user, venue_id)
    return {"suppliers": _catalog(catalog.suppliers, venue_id, db, config_db)}


@router.get("/invoice-fixes/stock-items")
def list_stock_items(
    venue_id: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Stock catalogue for the editor's Add Item and link-an-item searches."""
    card_actions.venue_for(db, user, venue_id)
    return {"stock_items": _catalog(catalog.stock_items, venue_id, db, config_db)}


@router.get("/invoice-fixes/file")
def invoice_file(
    venue_id: str,
    invoice_id: str | None = None,
    file_id: str | None = None,
    or_norm_copy: bool = False,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Stream an invoice's attached copy (the supplier PDF) for inline viewing.

    By ``invoice_id`` for drafts (the file id resolves off the invoice detail —
    same source the PO-extraction path uses). Already-RECEIVED invoices 404 on
    that detail route, so their copies are requested by ``file_id`` directly —
    the review captures it off the received feed (duplicate_of_file_id).
    ``or_norm_copy``: an invoice with no copy (an Ordermentum order) gets the one
    Norm draws from its data instead of a 404.
    """
    card_actions.venue_for(db, user, venue_id)
    import base64

    if not invoice_id and not file_id:
        raise HTTPException(422, "invoice_id or file_id is required")
    lh = _Loaded(db, config_db, venue_id)
    ref = invoice_id
    if not file_id:
        try:
            inv = lh.invoice(invoice_id)
        except RuntimeError:
            if not or_norm_copy:
                raise
            # Gone from Loaded (deleted since): Norm's copy draws on its record.
            return invoice_norm_copy(venue_id, invoice_id, db, config_db, user)
        file_id = inv.get("fileId")
        if not file_id and or_norm_copy:
            return invoice_norm_copy(venue_id, invoice_id, db, config_db, user)
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


@router.get("/invoice-fixes/norm-copy")
def invoice_norm_copy(
    venue_id: str,
    invoice_id: str,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """A copy Norm draws from the invoice's data in Loaded, for an invoice that
    came with none — an Ordermentum order (services/invoice_copy). It says on
    every page that Norm rebuilt it; nothing reads it back. An invoice Loaded
    no longer returns (deleted since) is drawn from Norm's own record of it."""
    from app.db.models import Venue, WorkingDocument
    from app.services import invoice_copy

    card_actions.venue_for(db, user, venue_id)
    venue = db.query(Venue).filter(Venue.id == venue_id).first()
    try:
        detail = _Loaded(db, config_db, venue_id).invoice(invoice_id)
    except RuntimeError:
        detail = None
    if not isinstance(detail, dict) or not detail.get("id"):
        doc = (
            db.query(WorkingDocument)
            .filter(
                WorkingDocument.venue_id == venue_id,
                WorkingDocument.external_ref["invoice_id"].as_string()
                == str(invoice_id),
            )
            .order_by(WorkingDocument.updated_at.desc())
            .first()
        )
        detail = invoice_copy.detail_from_record(doc.data if doc else None)
    if not detail:
        raise HTTPException(
            404, "this invoice isn't in Loaded, and Norm has no record of it"
        )
    ref = str(detail.get("referenceNumber") or invoice_id).replace('"', "")
    return Response(
        content=invoice_copy.render(detail, venue_name=getattr(venue, "name", None)),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="norm-copy-{ref}.pdf"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/invoice-fixes/purchase-orders")
def list_purchase_orders(
    venue_id: str,
    response: Response,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Loaded purchase orders for the card's Order Number picker."""
    card_actions.venue_for(db, user, venue_id)
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


class ReceiveBody(BaseModel):
    venue_id: str
    invoice_id: str


@router.post("/invoice-fixes/receive")
def receive_invoice(
    body: ReceiveBody,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Receive an invoice exactly as its reviewed working document shows it.

    The body is ``{venue_id, invoice_id}``; the server builds the receive from
    the canonical draft and refuses (409) when the invoice changed in Loaded
    since the review — see ``received_invoice.receive_from_draft``, the one
    receive path this and the claude.ai card share.
    """
    card_actions.venue_for(db, user, body.venue_id)
    from app.services.received_invoice import receive_from_draft

    lh = _Loaded(db, config_db, body.venue_id)
    out, doc = receive_from_draft(
        db,
        config_db,
        lh,
        body.venue_id,
        body.invoice_id,
        mode="interactive",
        user_id=user.id,
    )
    if isinstance(out, dict) and out.get("ok"):
        card_actions.record(
            db,
            user,
            body.venue_id,
            "received an invoice",
            f"invoice {body.invoice_id}",
            thread_id=doc.thread_id,
        )
        db.commit()
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
    """Loaded's current values onto a reopened draft — except a field that
    holds a decision or a person's edit: those were made on top of Loaded and
    stand (it used to put Loaded's brand back over an accepted "no brand" and
    a picked one, so the receive then failed Loaded's brand guard)."""
    from sqlalchemy.orm.attributes import flag_modified

    from app.services.invoice_edits import decided_fields

    fresh = build_received_invoice_data(detail)
    data = dict(doc.data or {})
    decided = decided_fields(data)
    for f in _META_HEADER_FIELDS:
        if (None, f) not in decided:
            data[f] = fresh.get(f)
    fresh_lines = {ln.get("id"): ln for ln in fresh.get("lines") or []}
    for ln in data.get("lines") or []:
        src = fresh_lines.get(ln.get("id"))
        if src:
            for f in _META_LINE_FIELDS:
                if (str(ln.get("id")), f) not in decided:
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
def create_receive_draft(
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
    accumulate locally and flush once via ``/invoice-fixes/receive`` — never
    per-keystroke through document_sync.
    """
    card_actions.venue_for(db, user, body.venue_id)
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

            from app.services.invoice_review import review_is_current

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
            if d.get("reviewed_at") and d.get("reviewed_invoice_fingerprint") != d.get(
                "loaded_invoice_fingerprint"
            ):
                # The cached-review marker. The suggestions stay until the new
                # review replaces them: it carries the decisions made on them
                # and needs the questions they answered.
                d["reviewed_at"] = None
            elif d.get("reviewed_at") and not review_is_current(d):
                # The last review stopped on an answer that didn't come
                # through, or a service that was down: the card's /review call
                # asks again (at most every RETRY_AFTER_MINUTES). Its
                # suggestions stay on screen meanwhile.
                d["reviewed_at"] = None
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
        enrich_loaded_snapshot(data, lh, body.venue_id, db, start_working=True)
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


def _wipe_validation_caches(db: Session, detail: dict) -> int:
    """Delete one invoice's cached validation artifacts; returns rows removed.

    The PDF-extraction rows are matched by the extracted invoice_number (the
    cache stores no file id); the stock-item match rows are keyed by the
    invoice's Loaded line ids (so a stale/declined match — the Sailor Jerry
    case, 08 Aug 2026 — is cleared rather than surviving every reset). Only
    the small PO-number extraction stays cached (content-keyed, not
    per-invoice).
    """
    from app.db.models import DocumentExtraction
    from app.services.received_invoice import _norm

    ref = _norm(detail.get("referenceNumber"))
    deleted = 0
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
                deleted += 1

    return deleted


@router.post("/invoice-fixes/reset-validation")
def reset_validation(
    body: DraftRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Wipe every cached validation artifact for one invoice — retest support.

    Deletes the invoice's PDF-extraction and stock-item-match cache rows
    (``_wipe_validation_caches``) and rebuilds every open draft for the
    invoice fresh from Loaded — no cached review, no suggestion action log,
    no local line state. The editor's next /review then runs the whole
    validation from scratch, including the LLM extraction.
    """
    card_actions.venue_for(db, user, body.venue_id)
    from sqlalchemy.orm.attributes import flag_modified

    from app.routers.working_documents import _doc_to_dict

    lh = _Loaded(db, config_db, body.venue_id)
    detail = lh.invoice(body.invoice_id)
    extractions_deleted = _wipe_validation_caches(db, detail)

    refreshed = None
    docs_reset = 0
    for doc in _open_docs_for(db, body.venue_id, body.invoice_id):
        fresh = build_received_invoice_data(detail)
        for k in ("working_document_id", "thread_id", "venue_id"):
            if k in (doc.data or {}):
                fresh[k] = doc.data[k]
        _attach_po_reference(fresh, lh)
        attach_item_names(fresh, lh)
        enrich_loaded_snapshot(fresh, lh, body.venue_id, db, start_working=True)
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


def _squash_review_into_drafts(
    db: Session,
    config_db: Session,
    venue_id: str,
    invoice_id: str,
    docs,
    *,
    trigger_autostudy: bool = True,
    reference: dict | None = None,
    settings: dict | None = None,
) -> None:
    """Run the review pipeline and store the result into every open draft.

    Squash semantics: each doc's payload is REPLACED wholesale, keeping only
    its own identity keys. Shared by the /review endpoint and ``heal_review``.

    ``trigger_autostudy`` gates the spec-less auto-study trigger. It MUST be
    False when this runs as a study's own heal follow-through: a study just
    finished, and re-triggering another from its heal is an infinite loop
    whenever the spec it wrote is filed under a name the review can't match
    (an orphaned spec — ATOMIC, 29 Aug 2026). Only a user-driven review should
    start a study.

    ``reference`` is the venue's catalogue/units/suppliers/tax/received-feed,
    prefetched ONCE by the caller. Without it ``build_replica`` self-fetches,
    and the stock catalogue is pulled THREE times in a single review: once by
    ``loaded_reference`` (TTL-cached), again at ``invoice_replica`` (which
    bypasses that cache), and a third time inside ``suggest_item_matches`` —
    plus the 400-day received feed twice. The batch path has always passed it;
    the interactive path, the one a human waits on, never did.
    """
    from sqlalchemy.orm.attributes import flag_modified

    from app.services.invoice_edits import rebuild_from_decisions
    from app.services.invoice_review import review_invoice

    # PO policy derives from the receive_without_po switch of the person
    # looking (review_invoice default): the card must tell the story autopilot
    # acts on — a PO-less invoice reads as blocked-from-auto-receive naming the
    # toggle, unless they have said POs aren't required (18 Aug 2026). With no
    # viewer (a background re-review), it is the draft's conversation's owner.
    if settings is None:
        from app.services.approvals import receiving_settings_for_thread

        settings = receiving_settings_for_thread(
            db, getattr(docs[0], "thread_id", None) if docs else None
        )
    fresh = review_invoice(
        db, config_db, venue_id, invoice_id, reference=reference, settings=settings
    )
    lh = None
    try:
        lh = _Loaded(db, config_db, venue_id)
        _attach_po_reference(fresh, lh)
        attach_item_names(fresh, lh)
        enrich_loaded_snapshot(fresh, lh, venue_id, db)
    except Exception as exc:  # noqa: BLE001 — reference data is enhancement
        logger.info("review PO reference unavailable: %s", exc)

    # Auto-spec: a spec-less supplier's invoice, worked in the screen, kicks off
    # a background dojo study (async) so a spec gets created without going
    # through the chat receive flow. Set on ``fresh`` BEFORE the per-draft deep
    # copy below so every open draft carries the "studying…" note. Fail-open.
    # NOT when this is a study's own heal (trigger_autostudy=False) — that would
    # loop.
    if trigger_autostudy:
        from app.services.spec_dojo import autostudy_if_spec_less

        autostudy_if_spec_less(db, config_db, venue_id, invoice_id, fresh)

    for target in docs:
        # A DEEP copy per twin: carrying a decision re-applies it to the lines,
        # and a shallow dict() shares those lists between every draft.
        payload = copy.deepcopy(fresh)
        for k in ("working_document_id", "thread_id", "venue_id"):
            if k in (target.data or {}):
                payload[k] = target.data[k]
        # The review rebuilds the card from Loaded. What was decided on it
        # comes back by replay — the accepted suggestions, then the person's
        # own edits — never by copying the old lines (owner, 19 Oct 2026:
        # Loaded plus decisions, no ghost data).
        carried, replayed = rebuild_from_decisions(
            target.data or {}, payload, previous_ops=target.pending_ops
        )
        if lh is not None and not rows_cached_for_linked_order(payload):
            try:
                _attach_po_reference(payload, lh)  # a replayed order choice
            except Exception as exc:  # noqa: BLE001 — reference data
                logger.info("order rows for the replayed link failed: %s", exc)
        if carried or replayed:
            logger.info(
                "review: carried %d decision(s) and %d edit(s) onto %s",
                carried,
                replayed,
                target.id,
            )
        target.data = payload
        target.version += 1
        flag_modified(target, "data")
    db.commit()


def heal_review(
    db: Session, config_db: Session, venue_id: str, invoice_id: str
) -> bool:
    """The Re-analyse button as a callable: wipe the invoice's cached
    validation artifacts and re-run the review into every open draft.

    Built for the sensei's auto-apply follow-through (Federal Merchants
    396152, 19 Aug 2026: the sensei fixed the prompt one minute after the
    review blocked, and the card stayed blocked until a human pressed
    Re-analyse). Returns False when the invoice has no open draft to heal.
    """
    docs = _open_docs_for(db, venue_id, invoice_id)
    if not docs:
        return False
    lh = _Loaded(db, config_db, venue_id)
    _wipe_validation_caches(db, lh.invoice(invoice_id))
    # A study just finished; re-review under the spec but NEVER start another
    # study from here (that is the infinite loop).
    _squash_review_into_drafts(
        db, config_db, venue_id, invoice_id, docs, trigger_autostudy=False
    )
    return True


def clear_studying_on_drafts(db: Session, venue_id: str, invoice_id: str) -> bool:
    """Turn OFF the ``sensei_studying`` flag on an invoice's open drafts, without
    re-reviewing them. The autostudy trigger set the flag; a study that finishes
    without applying a spec (an existing spec, a not-green proposal, a failure)
    would otherwise leave the card stuck showing "studying". This just drops the
    flag so the card falls back to its current review; auto-apply uses
    ``heal_review`` (a full re-review under the new spec) instead. Bumps the
    version so an open web card's poll adopts it. Returns whether any changed."""
    from sqlalchemy.orm.attributes import flag_modified

    changed = False
    for target in _open_docs_for(db, venue_id, invoice_id):
        if (target.data or {}).get("sensei_studying"):
            target.data = {**target.data, "sensei_studying": False}
            target.version += 1
            flag_modified(target, "data")
            changed = True
    if changed:
        db.commit()
    return changed


@router.post("/invoice-fixes/review")
def review_receive_draft(
    body: ReviewRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Run the replica review for one invoice and cache it into its draft.

    The single review pipeline (``services/invoice_review.review_invoice``):
    working values from Loaded's draft, the replica sidecar, unified
    suggestions with explanations, and confidence issues. A draft that
    already carries a current (replica_v2) review is returned untouched unless
    ``force`` — the (LLM) PDF extraction runs once per invoice, not per open.
    Re-running REPLACES the doc payload wholesale (squash semantics).
    """
    card_actions.venue_for(db, user, body.venue_id)
    from app.routers.working_documents import _doc_to_dict
    from app.services.invoice_review import review_is_current

    docs = _open_docs_for(db, body.venue_id, body.invoice_id)
    if not docs:
        raise HTTPException(404, "no open draft for this invoice — open it first")

    doc = docs[0]  # canonical: carries a review if any twin does
    data = doc.data or {}
    if review_is_current(data) and not body.force:
        return _doc_to_dict(doc)

    # Prefetch the venue's reference data ONCE. Best-effort by construction
    # (prefetch_replica_reference swallows its own failures and returns {}),
    # so a Loaded hiccup here degrades to the old self-fetching behaviour
    # rather than failing the review a human is waiting on.
    from app.services.spec_dojo import prefetch_replica_reference

    from app.services.approvals import receiving_settings

    reference = prefetch_replica_reference(db, config_db, body.venue_id)
    _squash_review_into_drafts(
        db,
        config_db,
        body.venue_id,
        body.invoice_id,
        docs,
        reference=reference,
        settings=receiving_settings(user),
    )
    db.refresh(doc)
    return _doc_to_dict(doc)


# The "Can't receive" button and its /invoice-fixes/cannot-receive endpoint were
# removed on 19 Oct 2026 (owner): training starts on its own when an invoice's
# supplier has no layout, and the invoice activity page shows what Norm did.
