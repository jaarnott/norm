"""Receiving a supplier invoice into Loaded — the shared primitives.

Two things live here so both the web REST router (`routers/invoice_fixes.py`)
and the MCP submit tool (`mcp/app_tools.py::norm__receive_invoice`) call ONE
implementation, never a second hand-rolled PUT:

- ``LoadedInvoiceClient`` — a thin authenticated LoadedHub client scoped to one
  venue connector (formerly ``invoice_fixes._Loaded``).
- ``do_receive`` — apply the card's edits to a draft invoice and (optionally)
  receive it: one PUT carries header + line edits, variant unit changes are
  PATCHed after (formerly ``invoice_fixes._do_receive``). Loaded has no dedicated
  receive endpoint — receiving IS re-PUTting the whole invoice with
  ``isReceived=true``.

Plus ``build_received_invoice_data`` — turn a raw ``get_invoice_detail`` payload
(camelCase, the LoadedHub read shape) into the snake_case working-document
``data`` the Receive Invoice editor reads and patches. This is where the three
gaps the old card had are fixed *by construction*: the real ``total`` (with the
``subtotal``/``tax_amount`` breakdown retained), the real per-line
``quantity_received``/``unit_cost`` (not absent PDF-copy figures), and the
carried ``linked_purchase_order_id`` so a linked PO reads as linked.
"""

from __future__ import annotations

import datetime
import logging

import httpx
from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.config_models import ConnectorSpec
from app.db.models import ConnectorConfig

logger = logging.getLogger(__name__)

_HOST = "https://api.loadedhub.com"


def _norm(text: object) -> str:
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def _po_key(text: object) -> str:
    """Normalise a PO number for matching: alphanumerics only, drop a leading
    'po' (so 'PO#1520987' == '1520987')."""
    k = _norm(text)
    return k[2:] if k.startswith("po") else k


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
    # Editable header fields (Loaded-parity). Each is applied to the invoice
    # only when supplied (not None), so a caller that sends none is unchanged.
    reference_number: str | None = None
    issued_at: str | None = None
    due_at: str | None = None
    received_at: str | None = None
    total: float | None = None
    linked_supplier_id: str | None = None
    unit_cost_includes_tax: bool | None = None
    notes: str | None = None


class LoadedInvoiceClient:
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
        # Credential values are user-entered: strip stray whitespace from every
        # header before it reaches the wire. A leading space on a stored
        # x-loaded-company-id made httpx reject the request as an illegal
        # header (500) — which the invoices dashboard renders as an empty list.
        # (The spec executor already strips its templated headers; this client
        # must match.)
        self._headers = {k: str(v).strip() for k, v in headers.items()}

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


def resolve_po_id(
    lh: LoadedInvoiceClient,
    po_number: object,
    supplier_id: str | None = None,
) -> dict | None:
    """Resolve a purchase-order NUMBER (e.g. "1520272") to a Loaded PO id.

    Loaded has no PO-by-number / search endpoint, and its bulk PO list returns
    only OPEN orders — so a number for an already-received or older PO can't be
    found there. Two passes:

    1. the open-PO list (fast; covers not-yet-invoiced POs), preferring a match
       on ``supplier_id`` when given;
    2. a fallback that scans the received/invoiced-invoices feed for an invoice
       carrying the same PO number and reads its ``linkedPurchaseOrderId`` — the
       only route to a received PO's id (verified live: 1520272 → 4c8c77df…).
       The feed projection omits ``linkedPurchaseOrderId``, so the full
       ``get_invoice_detail`` is fetched for a matching row.

    Returns ``{"id", "order_number", "linked_invoice_id"}`` or None when the
    number is missing, unresolved, or ambiguous (resolves to >1 distinct id).
    """
    want = _po_key(po_number)
    if not want:
        return None

    # Pass 1 — the open-PO list.
    try:
        pos = lh.get(
            "/1.0/stock/internal/purchase-orders?from=1901-01-01&to=9999-12-31"
        )
        pos = pos if isinstance(pos, list) else (pos or {}).get("data") or []
        matches = [p for p in pos if _po_key(p.get("orderNumber")) == want]
        if supplier_id and any(p.get("supplierId") == supplier_id for p in matches):
            matches = [p for p in matches if p.get("supplierId") == supplier_id]
        if len({p.get("id") for p in matches}) == 1:
            p = matches[0]
            return {
                "id": p.get("id"),
                "order_number": p.get("orderNumber"),
                "linked_invoice_id": p.get("linkedInvoiceId"),
            }
        if len(matches) > 1:
            return None  # genuinely ambiguous in the open list
    except Exception as exc:  # noqa: BLE001 — fall through to the feed pass
        logger.info("resolve_po_id open-list pass failed: %s", exc)

    # Pass 2 — the received/invoiced feed (reaches already-received POs).
    try:
        today = datetime.date.today()
        frm = (today - datetime.timedelta(days=400)).isoformat()
        to = (today + datetime.timedelta(days=1)).isoformat()
        feed = lh.get(
            "/1.0/stock/internal/stock-received"
            f"?from={frm}&to={to}&property=Invoiced"
            "&includeAdjustingInvoices=true&ifNoneGetLastReceived=false"
        )
        rows = feed if isinstance(feed, list) else (feed or {}).get("data") or []
        po_ids: set[str] = set()
        for r in rows:
            if not isinstance(r, dict) or _po_key(r.get("purchaseOrderNumber")) != want:
                continue
            try:
                det = lh.invoice(r.get("id"))
            except Exception:  # noqa: BLE001 — skip a row we can't read
                continue
            pid = det.get("linkedPurchaseOrderId")
            if pid:
                po_ids.add(pid)
        if len(po_ids) == 1:
            pid = next(iter(po_ids))
            order_number = linked_invoice_id = None
            try:
                po = lh.get(f"/1.0/stock/internal/purchase-orders/{pid}")
                if isinstance(po, dict):
                    order_number = po.get("orderNumber")
                    linked_invoice_id = po.get("linkedInvoiceId")
            except Exception:  # noqa: BLE001 — id is enough on its own
                pass
            return {
                "id": pid,
                "order_number": order_number,
                "linked_invoice_id": linked_invoice_id,
            }
    except Exception as exc:  # noqa: BLE001 — resolution is best-effort
        logger.info("resolve_po_id feed pass failed: %s", exc)

    return None


def do_receive(lh: LoadedInvoiceClient, body: ReceiveRequest) -> dict:
    """Apply the card's edits to a draft invoice and (optionally) receive it.

    One PUT carries every header + line edit; variant unit changes are PATCHed
    after. Pure orchestration over an authenticated client, so it is unit-
    testable with a scripted fake.
    """
    inv = lh.invoice(body.invoice_id)

    # Header: link a PO if requested (id wins; else resolve the number).
    po_id = body.linked_purchase_order_id
    po_number = body.po_number if po_id else None
    if not po_id and body.po_number:
        resolved = resolve_po_id(lh, body.po_number, inv.get("linkedSupplierId"))
        if not resolved:
            raise HTTPException(
                400, f"purchase order {body.po_number} not found or ambiguous"
            )
        po_id = resolved["id"]
        po_number = resolved.get("order_number") or body.po_number

    po_link_skipped = False
    if po_id:
        # Fetch the PO once — for the display number AND the 1:1 link guard.
        # Loaded's invoice LIST renders invoice.purchaseOrderNumber, which the
        # supplier feed fills with the SUPPLIER's own order number (Bidfood
        # "12195941-1"), not the buyer's PO — so write the resolved orderNumber.
        po = None
        try:
            po = lh.get(f"/1.0/stock/internal/purchase-orders/{po_id}")
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning("could not fetch linked PO %s: %s", po_id, exc)
        if isinstance(po, dict):
            if not po_number:
                po_number = po.get("orderNumber")
            other = po.get("linkedInvoiceId")
            if other and other != body.invoice_id:
                # Split order: Loaded models PO↔invoice as 1:1 and this PO is
                # already linked to another invoice. Writing the link here would
                # steal it from that invoice — so receive WITHOUT re-linking.
                po_link_skipped = True
                logger.info(
                    "receive: PO %s already linked to invoice %s — receiving %s "
                    "without re-linking",
                    po_number or po_id,
                    other,
                    body.invoice_id,
                )
        if not po_link_skipped:
            inv["linkedPurchaseOrderId"] = po_id
            if po_number:
                inv["purchaseOrderNumber"] = po_number

    # Editable header fields — apply each only when the caller supplied it, so a
    # receive that changes nothing in the header leaves it exactly as Loaded has
    # it. snake_case request field -> camelCase Loaded field.
    _HEADER_FIELDS = {
        "reference_number": "referenceNumber",
        "issued_at": "issuedAt",
        "due_at": "dueAt",
        "received_at": "receivedAt",
        "total": "total",
        "linked_supplier_id": "linkedSupplierId",
        "unit_cost_includes_tax": "unitCostIncludesTax",
        "notes": "notes",
    }
    for src, dst in _HEADER_FIELDS.items():
        val = getattr(body, src)
        if val is not None:
            inv[dst] = val

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
    existing_ids = {ln.get("id") for ln in inv.get("lines") or []}
    for ln in inv.get("lines") or []:
        e = edits.get(ln.get("id"))
        if not e:
            continue
        if e.get("struck"):
            # The card struck this line (a redundant $0 duplicate the review
            # flagged) — soft-delete it in Loaded so it drops out of the received
            # invoice. deletedAt is Loaded's own delete marker; the receive guard
            # below already skips deletedAt lines. No other edits matter once struck.
            ln["deletedAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            continue
        for src, dst in _LINE_FIELDS.items():
            if src in e and e[src] is not None:
                ln[dst] = e[src]

    # New lines the editor added (Add Item): a request line whose id matches no
    # existing invoice line is appended as a NEW Loaded line. The client's temp
    # id is dropped — Loaded assigns the real one. Only append a line that names
    # a real stock item, so a stray empty row can't create rubbish.
    for e in body.lines:
        if e.get("id") in existing_ids:
            continue
        if not (e.get("code") or e.get("linked_item_id")):
            continue
        new_line = {}
        for src, dst in _LINE_FIELDS.items():
            if e.get(src) is not None:
                new_line[dst] = e[src]
        for src, dst in (
            ("code", "code"),
            ("description", "description"),
            ("linked_item_id", "linkedItemId"),
        ):
            if e.get(src) is not None:
                new_line[dst] = e[src]
        inv.setdefault("lines", []).append(new_line)

    if body.receive:
        # Guard: every line must be resolved to a real Loaded catalogue entry
        # before receiving — a NEW stock item / unit / brand is created only by
        # an explicit, controlled action (never silently on receive).
        unresolved = []
        for ln in inv.get("lines") or []:
            if ln.get("deletedAt"):
                continue
            name = ln.get("description") or ln.get("code") or "?"
            if not ln.get("linkedItemId"):
                unresolved.append(f"stock item '{name}'")
            elif not ln.get("linkedUnitId"):
                unresolved.append(f"unit for '{name}'")
            elif ln.get("brand") and not ln.get("linkedBrandId"):
                unresolved.append(f"brand '{ln.get('brand')}' on '{name}'")
        if unresolved:
            shown = "; ".join(unresolved[:5])
            if len(unresolved) > 5:
                shown += f"; … and {len(unresolved) - 5} more"
            raise HTTPException(
                400,
                f"{len(unresolved)} new value(s) must be created in Loaded before "
                f"receiving: {shown}",
            )
        inv["isReceived"] = True
        # Honour a Received Date set in the header; otherwise stamp now.
        if not inv.get("receivedAt"):
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
        "linked_purchase_order": None if po_link_skipped else po_number,
        "po_link_skipped": po_link_skipped,
        "variant_updates": variant_results,
    }


# ── Draft shaping ────────────────────────────────────────────────────────


def invoice_fingerprint(detail: dict) -> str:
    """Stable hash of an invoice's REVIEW-RELEVANT content.

    Loaded's ``version`` field is a static label ("Current"), so change
    detection hashes what the review actually depends on: the live lines
    (quantities, costs, units, item links), the totals, the PO link and the
    attached file. Anything else (e.g. notes) changing does not invalidate a
    cached review.

    FNV-1a (pure Python) rather than hashlib deliberately: the
    ``prepare_receive_invoice`` consolidator must shape IDENTICALLY to this
    builder (tests/test_prepare_receive_invoice.py) and the sandbox has no
    hashlib. Change detection, not security — collisions are inconsequential.
    """
    import json as _json

    material = {
        "lines": [
            [
                ln.get("id"),
                ln.get("quantityReceived"),
                ln.get("unitCost"),
                ln.get("totalCost"),
                ln.get("linkedItemId"),
                ln.get("linkedUnitId"),
                ln.get("unit"),
                ln.get("code"),
                bool(ln.get("deletedAt")),
            ]
            for ln in (detail.get("lines") or [])
            if isinstance(ln, dict)
        ],
        "subtotal": detail.get("subtotal"),
        "tax": detail.get("taxAmount"),
        "total": detail.get("total"),
        "po": detail.get("linkedPurchaseOrderId"),
        "file": detail.get("fileId"),
    }
    h = 0xCBF29CE484222325
    for b in _json.dumps(material, sort_keys=True, default=str).encode():
        h = ((h ^ b) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return format(h, "016x")


def _line_from_detail(ln: dict) -> dict:
    """One camelCase Loaded invoice line → the editor's snake_case line.

    ``original_unit_id`` is stored at creation so the submit path can derive
    ``variant_updates`` (lines whose unit changed) server-side, without the
    client having to remember what the unit was.
    """
    linked_unit_id = ln.get("linkedUnitId")
    return {
        "id": ln.get("id"),
        "code": ln.get("code"),
        "description": ln.get("description"),
        "brand": ln.get("brand"),
        "unit": ln.get("unit"),
        "linked_unit_id": linked_unit_id,
        "original_unit_id": linked_unit_id,
        "unit_ratio": ln.get("linkedUnitRatio"),
        "quantity_ordered": ln.get("quantityOrdered"),
        "quantity_received": ln.get("quantityReceived"),
        "unit_cost": ln.get("unitCost"),
        "total_cost": ln.get("totalCost"),
        "tax_amount": ln.get("taxAmount"),
        "sale_tax_rate": ln.get("saleTaxRate"),
        "linked_item_id": ln.get("linkedItemId"),
        # linked_*_id absent while brand/unit set = Loaded would show it NEW.
        "linked_brand_id": ln.get("linkedBrandId"),
        "item_type": ln.get("itemType"),
    }


def build_received_invoice_data(detail: dict) -> dict:
    """A raw ``get_invoice_detail`` payload → the received-invoice draft ``data``.

    Snake_case throughout, deliberately: it must match ``do_receive``'s
    ``_LINE_FIELDS`` map and the editor's line shape. The PDF-review fields
    (``copy_*`` per line, ``suggestions``, ``checks``) are NOT set here — they
    are filled in later by the review pass and persisted into the same draft, so
    a re-open never re-runs the extraction.
    """
    detail = detail or {}
    lines = [
        _line_from_detail(ln)
        for ln in (detail.get("lines") or [])
        if isinstance(ln, dict)
    ]
    return {
        "invoice_id": detail.get("id"),
        "reference_number": detail.get("referenceNumber"),
        "supplier_name": detail.get("supplierName"),
        "linked_supplier_id": detail.get("linkedSupplierId"),
        "purchase_order_number": detail.get("purchaseOrderNumber"),
        "linked_purchase_order_id": detail.get("linkedPurchaseOrderId"),
        "issued_at": detail.get("issuedAt"),
        "due_at": detail.get("dueAt"),
        "received_at": detail.get("receivedAt"),
        "subtotal": detail.get("subtotal"),
        "tax_amount": detail.get("taxAmount"),
        "discount_amount": detail.get("discountAmount"),
        "total": detail.get("total"),
        "unit_cost_includes_tax": bool(detail.get("unitCostIncludesTax")),
        "file_id": detail.get("fileId"),
        "is_received": bool(detail.get("isReceived")),
        "status": "draft",
        "notes": detail.get("notes") or "",
        # Content fingerprint of the live invoice (Loaded's `version` field is a
        # static label, not a revision). The review stamps the fingerprint it ran
        # against (reviewed_invoice_fingerprint); a later open whose live detail
        # hashes differently invalidates the cached review, an unchanged one
        # skips it entirely — costs nothing, /draft fetches the detail anyway.
        "loaded_invoice_fingerprint": invoice_fingerprint(detail),
        "lines": lines,
    }
