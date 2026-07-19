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

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.config_models import ConnectorSpec
from app.db.engine import get_config_db, get_db
from app.db.models import ConnectorConfig, User

logger = logging.getLogger(__name__)
router = APIRouter()

_HOST = "https://api.loadedhub.com"


class ApplyFixesRequest(BaseModel):
    venue_id: str
    fixes: list[dict]


class ReceiveRequest(BaseModel):
    venue_id: str
    invoice_id: str
    # Optional PO to link before receiving (id preferred; number resolved).
    linked_purchase_order_id: str | None = None
    po_number: str | None = None
    # Per-line edits, keyed by line id. Only supplied fields are applied.
    lines: list[dict] = []
    # Variant unit updates: {linked_item_id, line_code, unit_id}.
    variant_updates: list[dict] = []
    receive: bool = True


class InvoiceStatusRequest(BaseModel):
    venue_id: str
    invoice_ids: list[str] = []


def _norm(text: object) -> str:
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def _po_key(text: object) -> str:
    """Normalise a PO number for matching: alphanumerics only, drop a leading
    'po' (so 'PO#1520987' == '1520987')."""
    k = _norm(text)
    return k[2:] if k.startswith("po") else k


class _Loaded:
    """Thin authenticated LoadedHub client scoped to one venue connector."""

    def __init__(self, db: Session, config_db: Session, venue_id: str):
        from app.connectors.spec_executor import _apply_auth

        spec = (
            config_db.query(ConnectorSpec)
            .filter(ConnectorSpec.connector_name == "loadedhub")
            .first()
        )
        if not spec:
            raise HTTPException(404, "loadedhub connector spec not found")
        cred = (
            db.query(ConnectorConfig)
            .filter(
                ConnectorConfig.connector_name == "loadedhub",
                ConnectorConfig.enabled == "true",
                ConnectorConfig.venue_id == venue_id,
            )
            .first()
        )
        if not cred:
            raise HTTPException(400, f"loadedhub not connected for venue {venue_id}")
        creds = cred.config or {}
        headers = {"Content-Type": "application/json"}
        company_id = creds.get("x_loaded_company_id")
        if company_id:
            headers["x-loaded-company-id"] = company_id
        headers, self._auth = _apply_auth(
            headers,
            spec.auth_type,
            spec.auth_config or {},
            creds,
            spec=spec,
            db=db,
            venue_id=venue_id,
        )
        self._headers = headers

    def request(self, method: str, path: str, body: object = None) -> object:
        resp = httpx.request(
            method,
            _HOST + path,
            headers=self._headers,
            json=body if isinstance(body, (dict, list)) else None,
            auth=self._auth,
            timeout=30.0,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Loaded {method} {path} → {resp.status_code}: {resp.text[:200]}"
            )
        try:
            return resp.json()
        except Exception:
            return resp.text

    def get(self, path: str) -> object:
        return self.request("GET", path)

    def file_base64(self, file_id: str) -> tuple[str, str]:
        """Download an invoice file and return (base64, content_type)."""
        import base64

        resp = httpx.get(
            _HOST + f"/1.0/stock/internal/invoices/files/{file_id}",
            headers=self._headers,
            auth=self._auth,
            timeout=30.0,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"file download → {resp.status_code}")
        ctype = resp.headers.get("content-type", "application/pdf").split(";")[0]
        return base64.b64encode(resp.content).decode(), ctype

    def invoice(self, invoice_id: str) -> dict:
        return self.request(
            "GET",
            f"/1.0/stock/invoices/{invoice_id}"
            "?isAdjustingInvoice=false&includeDeleted=false",
        )


def _apply_link_po(lh: _Loaded, fix: dict) -> str:
    want = _po_key(fix.get("po_number"))
    if not want:
        raise RuntimeError("no PO number to link")
    pos = lh.get("/1.0/stock/internal/purchase-orders?from=1901-01-01&to=9999-12-31")
    pos = pos if isinstance(pos, list) else (pos or {}).get("data") or []
    matches = [p for p in pos if _po_key(p.get("orderNumber")) == want]
    if not matches:
        raise RuntimeError(f"purchase order {fix.get('po_number')} not found in Loaded")
    if len(matches) > 1:
        raise RuntimeError(f"purchase order {fix.get('po_number')} is ambiguous")
    po = matches[0]
    inv = lh.invoice(fix["invoice_id"])
    inv["linkedPurchaseOrderId"] = po["id"]
    inv["purchaseOrderNumber"] = po.get("orderNumber")
    lh.request("PUT", f"/1.0/stock/internal/invoices/{fix['invoice_id']}", inv)
    return f"Linked purchase order {po.get('orderNumber')}"


def _resolve_unit(lh: _Loaded, proposed: str) -> dict | None:
    """Find the Loaded unit whose name matches the proposed unit (exact, then
    guideline-equivalent via parse_unit). Returns the unit dict or None."""
    from app.services.invoice_units import parse_unit

    units = lh.get("/1.0/stock/internal/units")
    units = [u for u in (units or []) if not u.get("datestampDeleted")]
    for u in units:
        if _norm(u.get("name")) == _norm(proposed):
            return u
    target = parse_unit(proposed)
    if target:
        for u in units:
            pu = parse_unit(u.get("name"))
            if pu and pu[0] == target[0] and abs(pu[1] - target[1]) < 0.001:
                return u
    return None


def _apply_unit(lh: _Loaded, fix: dict) -> str:
    unit = _resolve_unit(lh, fix.get("proposed_unit", ""))
    if not unit:
        raise RuntimeError(
            f"unit '{fix.get('proposed_unit')}' does not exist in Loaded — "
            "create it there first"
        )
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
    return f"Set unit to {unit.get('name')}{variant_note}"


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
            message = applier(lh, fix)
            results.append({"id": fid, "ok": True, "message": message})
        except Exception as exc:  # noqa: BLE001 — isolate each fix
            logger.warning("invoice fix %s failed: %s", fid, exc)
            results.append({"id": fid, "ok": False, "message": str(exc)})
    applied = sum(1 for r in results if r["ok"])
    return {"results": results, "applied": applied, "total": len(results)}


# ---------------------------------------------------------------------------
# Reference reads + full "Accept & Receive" for the editable card
# ---------------------------------------------------------------------------


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


class ResolvePoRequest(BaseModel):
    venue_id: str
    invoice_id: str


@router.post("/invoice-fixes/resolve-po")
async def resolve_po(
    body: ResolvePoRequest,
    db: Session = Depends(get_db),
    config_db: Session = Depends(get_config_db),
    user: User = Depends(get_current_user),
):
    """Extract the CUSTOMER purchase order number from the invoice copy.

    Loaded's own `purchaseOrderNumber` field is populated by the supplier feed
    and often holds the supplier's OWN order number (e.g. Bidfood "O/N"), not
    the buyer's PO. The buyer's PO — the one that matches a Loaded purchase
    order — is only on the printed copy (e.g. "Customer Order No"). Read it
    directly so the card can suggest the right PO.
    """
    lh = _Loaded(db, config_db, body.venue_id)
    inv = lh.invoice(body.invoice_id)
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


def _do_receive(lh: "_Loaded", body: "ReceiveRequest") -> dict:
    """Apply the card's edits to a draft invoice and (optionally) receive it.

    One PUT carries every header + line edit; variant unit changes are PATCHed
    after. Pure orchestration over an authenticated client, so it is unit-
    testable with a scripted fake.
    """
    import datetime

    inv = lh.invoice(body.invoice_id)

    # Header: link a PO if requested (id wins; else resolve the number).
    po_id = body.linked_purchase_order_id
    po_number = None
    if not po_id and body.po_number:
        want = _po_key(body.po_number)
        pos = lh.get(
            "/1.0/stock/internal/purchase-orders?from=1901-01-01&to=9999-12-31"
        )
        pos = pos if isinstance(pos, list) else (pos or {}).get("data") or []
        matches = [p for p in pos if _po_key(p.get("orderNumber")) == want]
        if not matches:
            raise HTTPException(400, f"purchase order {body.po_number} not found")
        if len(matches) > 1:
            raise HTTPException(400, f"purchase order {body.po_number} is ambiguous")
        po_id = matches[0]["id"]
        po_number = matches[0].get("orderNumber")
    elif po_id:
        po_number = body.po_number
    if po_id:
        inv["linkedPurchaseOrderId"] = po_id
        if po_number:
            inv["purchaseOrderNumber"] = po_number

    # Per-line edits by id — only apply the fields the card sent.
    edits = {e.get("id"): e for e in body.lines if e.get("id")}
    _LINE_FIELDS = {
        "unit": "unit",
        "linked_unit_id": "linkedUnitId",
        "unit_ratio": "linkedUnitRatio",
        "quantity_received": "quantityReceived",
        "unit_cost": "unitCost",
        "total_cost": "totalCost",
    }
    for ln in inv.get("lines") or []:
        e = edits.get(ln.get("id"))
        if not e:
            continue
        for src, dst in _LINE_FIELDS.items():
            if src in e and e[src] is not None:
                ln[dst] = e[src]

    if body.receive:
        inv["isReceived"] = True
        inv["receivedAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    lh.request("PUT", f"/1.0/stock/internal/invoices/{body.invoice_id}", inv)

    # Variant unit updates (Loaded's "update variant?" step), isolated.
    variant_results = []
    for vu in body.variant_updates:
        try:
            item = lh.get(f"/1.0/stock/internal/items/{vu['linked_item_id']}")
            code = _norm(vu.get("line_code"))
            supplier = inv.get("linkedSupplierId")
            variant = next(
                (
                    v
                    for v in (item or {}).get("suppliers") or []
                    if v.get("supplierId") == supplier
                    and _norm(v.get("stockCode")) == code
                ),
                None,
            )
            if variant:
                lh.request(
                    "PATCH",
                    f"/1.0/stock/internal/item-supplier-variant/{variant['id']}",
                    {"unitId": vu.get("unit_id")},
                )
                variant_results.append({"code": vu.get("line_code"), "ok": True})
            else:
                variant_results.append(
                    {"code": vu.get("line_code"), "ok": False, "message": "no variant"}
                )
        except Exception as exc:  # noqa: BLE001 — isolate each variant
            logger.warning("variant update failed: %s", exc)
            variant_results.append(
                {"code": vu.get("line_code"), "ok": False, "message": str(exc)}
            )

    return {
        "ok": True,
        "received": bool(body.receive),
        "linked_purchase_order": po_number,
        "variant_updates": variant_results,
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
