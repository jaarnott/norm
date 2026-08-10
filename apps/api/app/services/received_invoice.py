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
    # Explicitly clear an existing PO link (None above means "don't touch",
    # so unlinking needs its own flag — the "PO belongs to another supplier"
    # suggestion). A po_id supplied alongside still wins: unlink, then link.
    unlink_purchase_order: bool = False
    # Split order (the referenced PO is linked to a SIBLING invoice): the PO
    # id + sibling id let the receive stamp best-effort cross-reference notes
    # on the PO and the sibling. Never links the PO (Loaded is 1:1).
    split_po_id: str | None = None
    split_sibling_invoice_id: str | None = None
    # The order-number REFERENCE field alone (scenario: the copy names a
    # split order Loaded didn't match) — written without any link. Distinct
    # from po_number above, which resolves-and-LINKS.
    purchase_order_number: str | None = None
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
    # Accepted from the copy's printed totals when Loaded's header was wrong
    # (e.g. a feed that left them $0) — written together with total.
    subtotal: float | None = None
    tax_amount: float | None = None
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
    only OPEN orders — so a number for a PO already claimed by an invoice can't
    be found there. Three passes:

    1. the open-PO list (fast; covers not-yet-invoiced POs), preferring a match
       on ``supplier_id`` when given;
    2. the outstanding-drafts list — Loaded links a PO at ingestion, which
       drops it from the open list while the invoice is still a draft; the
       drafts list carries ``purchaseOrderNumber`` AND ``linkedPurchaseOrderId``
       per row (verified live: 1521145 was reachable only here). The PO's
       ``linkedInvoiceId`` is what lets the caller run the split-order
       validator against whichever invoice holds the link;
    3. a fallback that scans the received/invoiced-invoices feed for an invoice
       carrying the same PO number and reads its ``linkedPurchaseOrderId`` — the
       only route to a fully-received PO's id (verified live: 1520272 →
       4c8c77df…). The feed projection omits ``linkedPurchaseOrderId``, so the
       full ``get_invoice_detail`` is fetched for a matching row.

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
                "supplier_id": p.get("supplierId"),
            }
        if len(matches) > 1:
            return None  # genuinely ambiguous in the open list
    except Exception as exc:  # noqa: BLE001 — fall through to the feed pass
        logger.info("resolve_po_id open-list pass failed: %s", exc)

    # Pass 2 — the outstanding-drafts list: a PO claimed by a draft invoice is
    # in NEITHER the open list nor the received feed; the drafts row carries
    # the number and the PO id directly.
    try:
        rows = lh.get("/1.0/stock/internal/invoices")
        rows = rows if isinstance(rows, list) else (rows or {}).get("data") or []
        draft_po_ids = {
            r.get("linkedPurchaseOrderId")
            for r in rows
            if isinstance(r, dict)
            and not r.get("deletedAt")
            and r.get("linkedPurchaseOrderId")
            and _po_key(r.get("purchaseOrderNumber")) == want
        }
        if len(draft_po_ids) == 1:
            pid = next(iter(draft_po_ids))
            po = lh.get(f"/1.0/stock/internal/purchase-orders/{pid}")
            if isinstance(po, dict):
                return {
                    "id": pid,
                    "order_number": po.get("orderNumber"),
                    "linked_invoice_id": po.get("linkedInvoiceId"),
                    "supplier_id": po.get("supplierId"),
                }
    except Exception as exc:  # noqa: BLE001 — fall through to the feed pass
        logger.info("resolve_po_id drafts pass failed: %s", exc)

    # Pass 3 — the received/invoiced feed (reaches already-received POs).
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
            order_number = linked_invoice_id = po_supplier_id = None
            try:
                po = lh.get(f"/1.0/stock/internal/purchase-orders/{pid}")
                if isinstance(po, dict):
                    order_number = po.get("orderNumber")
                    linked_invoice_id = po.get("linkedInvoiceId")
                    po_supplier_id = po.get("supplierId")
            except Exception:  # noqa: BLE001 — id is enough on its own
                pass
            return {
                "id": pid,
                "order_number": order_number,
                "linked_invoice_id": linked_invoice_id,
                "supplier_id": po_supplier_id,
            }
    except Exception as exc:  # noqa: BLE001 — resolution is best-effort
        logger.info("resolve_po_id feed pass failed: %s", exc)

    return None


def _register_missing_variants(
    lh: LoadedInvoiceClient, inv: dict, body: ReceiveRequest
) -> None:
    """Ensure each newly-linked line's supplier variant exists on its stock item.

    Linking an item in the editor is a LOCAL draft edit; the link (and this
    registration, which is what makes the supplier's future invoices
    auto-match) lands in Loaded only here, at receive/save time — mirroring
    what the old /link-item endpoint wrote immediately. Best-effort: a failed
    registration never blocks the receive (the line still links via the PUT).
    """
    supplier_id = body.linked_supplier_id or inv.get("linkedSupplierId")
    if not supplier_id:
        return
    pre_by_id = {ln.get("id"): ln for ln in inv.get("lines") or []}
    for e in body.lines:
        item_id = e.get("linked_item_id")
        if not item_id or e.get("struck"):
            continue
        pre = pre_by_id.get(e.get("id"))
        if pre is not None and pre.get("linkedItemId"):
            continue  # already linked in Loaded — nothing new to register
        code = (pre or {}).get("code") or e.get("code")
        try:
            item = lh.get(f"/1.0/stock/internal/items/{item_id}")
            if not isinstance(item, dict) or not item.get("id"):
                continue
            variants = [v for v in (item.get("suppliers") or []) if isinstance(v, dict)]
            if any(
                v.get("supplierId") == supplier_id
                and _norm(v.get("stockCode")) == _norm(code)
                for v in variants
            ):
                continue
            unit_id = (
                e.get("linked_unit_id")
                or (pre or {}).get("linkedUnitId")
                or item.get("orderingUnitId")
            )
            variants.append(
                {
                    "supplierId": supplier_id,
                    "stockCode": code,
                    "unitId": unit_id,
                    "unitCost": (
                        e.get("unit_cost")
                        if e.get("unit_cost") is not None
                        else _ln_unit_cost(pre or {})
                    ),
                    "brandId": (pre or {}).get("linkedBrandId"),
                    "defaultForSupplier": False,
                    "description": e.get("description")
                    or (pre or {}).get("description"),
                }
            )
            item["suppliers"] = variants
            lh.request("PUT", f"/1.0/stock/internal/items/{item_id}", item)
        except Exception as exc:  # noqa: BLE001 — registration is best-effort
            logger.warning("variant registration failed for item %s: %s", item_id, exc)


def do_receive(lh: LoadedInvoiceClient, body: ReceiveRequest) -> dict:
    """Apply the card's edits to a draft invoice and (optionally) receive it.

    One PUT carries every header + line edit; variant unit changes are PATCHed
    after. Pure orchestration over an authenticated client, so it is unit-
    testable with a scripted fake.
    """
    inv = lh.invoice(body.invoice_id)

    # Explicit unlink first (the "PO belongs to another supplier" suggestion):
    # the fetched invoice carries any existing link, so clearing must be an
    # explicit act — a None po_id below only means "don't touch". A po_id
    # supplied alongside still wins: unlink, then link the right order.
    if body.unlink_purchase_order:
        inv["linkedPurchaseOrderId"] = None
        inv["purchaseOrderNumber"] = None

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
            # The link path owns the number when it links — never let a stale
            # reference field from the card overwrite the resolved orderNumber.
            body.purchase_order_number = None

    # Editable header fields — apply each only when the caller supplied it, so a
    # receive that changes nothing in the header leaves it exactly as Loaded has
    # it. snake_case request field -> camelCase Loaded field.
    # Loaded renamed the tax-mode header (unitCostIncludesTax →
    # displayUnitCostInclusiveOfTax, observed live 05 Aug 2026) — write BOTH
    # names so the edit lands regardless of which schema the API honours.
    _HEADER_FIELDS = {
        "reference_number": ("referenceNumber",),
        # Reference field only — the LINK path above owns linking (and wins:
        # this loop runs later but the editor only sends purchase_order_number
        # when no PO is linked, i.e. the split-order accepted state).
        "purchase_order_number": ("purchaseOrderNumber",),
        "issued_at": ("issuedAt",),
        "due_at": ("dueAt",),
        "received_at": ("receivedAt",),
        "total": ("total",),
        "subtotal": ("subtotal",),
        "tax_amount": ("taxAmount",),
        "linked_supplier_id": ("linkedSupplierId",),
        "unit_cost_includes_tax": (
            "unitCostIncludesTax",
            "displayUnitCostInclusiveOfTax",
        ),
        "notes": ("notes",),
    }
    for src, dsts in _HEADER_FIELDS.items():
        val = getattr(body, src)
        if val is not None:
            for dst in dsts:
                inv[dst] = val

    # Per-line edits by id — only apply the fields the card sent.
    edits = {e.get("id"): e for e in body.lines if e.get("id")}
    # Cost fields renamed by Loaded (unitCost → unitCostExclTax) — write both.
    _LINE_FIELDS = {
        "unit": ("unit",),
        "linked_unit_id": ("linkedUnitId",),
        "unit_ratio": ("linkedUnitRatio",),
        "quantity_received": ("quantityReceived",),
        "unit_cost": ("unitCost", "unitCostExclTax"),
        "total_cost": ("totalCost", "totalCostExclTax"),
        # Lines ADDED in the editor (e.g. an accepted add_line suggestion)
        # carry the invoice's prevailing tax rate; without it Loaded computes
        # zero tax for the new line.
        "sale_tax_rate": ("saleTaxRate",),
        # Item links are LOCAL draft edits (accepting a match suggestion never
        # writes to Loaded on its own) — the link lands here, on receive.
        "linked_item_id": ("linkedItemId",),
    }
    # Register missing supplier variants for newly-linked lines BEFORE any
    # edits mutate the invoice dict (the pre-link state decides "newly").
    _register_missing_variants(lh, inv, body)
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
        for src, dsts in _LINE_FIELDS.items():
            if src in e and e[src] is not None:
                for dst in dsts:
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
        for src, dsts in _LINE_FIELDS.items():
            if e.get(src) is not None:
                for dst in dsts:
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
        # Guard: Loaded's server 500s (opaque internal-error, seen live on
        # Sawmill 201458) when receiving an invoice with NO linked supplier —
        # their feed doesn't always match the printed name to a supplier
        # record. Fail clearly here instead.
        if not (body.linked_supplier_id or inv.get("linkedSupplierId")):
            raise HTTPException(
                400,
                "link a supplier before receiving — this invoice has no Loaded "
                "supplier (pick one in the Supplier dropdown)",
            )
        # Guard: an empty draft (a statement/letter uploaded as an invoice, or
        # every line struck) has nothing to receive — deleting the draft is
        # the right action, never an empty receive.
        if not any(not ln.get("deletedAt") for ln in inv.get("lines") or []):
            raise HTTPException(
                400,
                "nothing to receive — this draft has no line items (if it "
                "isn't an invoice, delete the draft instead)",
            )
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

    # Loaded's validations (e.g. invoice-totals-mismatch) come back as clean
    # 4xx bodies — surface them as a 502 with the detail instead of letting
    # the RuntimeError become an opaque 500 the card renders as "Error 500".
    try:
        lh.request("PUT", f"/1.0/stock/internal/invoices/{body.invoice_id}", inv)
    except RuntimeError as exc:
        raise HTTPException(502, f"Loaded rejected the invoice: {exc}") from exc

    # Split-order cross-reference notes (best-effort, isolated — a note
    # failure never fails the receive): Loaded's 1:1 link can't record the
    # second delivery, so stamp the PO and the sibling invoice instead.
    # Notes PUT verified live on the test env 08 Aug 2026 (works on received
    # invoices too, and doesn't disturb their state).
    split_notes = []
    if body.receive and body.split_po_id:
        ref_label = str(
            body.reference_number or inv.get("referenceNumber") or body.invoice_id
        )
        order_label = None
        try:
            po_obj = lh.get(f"/1.0/stock/internal/purchase-orders/{body.split_po_id}")
            if isinstance(po_obj, dict) and po_obj.get("id"):
                order_label = po_obj.get("orderNumber")
                marker = f"Split order: also invoiced on {ref_label}"
                notes = str(po_obj.get("notes") or "")
                if marker not in notes:
                    po_obj["notes"] = (notes + "\n" if notes else "") + marker
                    lh.request(
                        "PUT",
                        f"/1.0/stock/internal/purchase-orders/{body.split_po_id}",
                        po_obj,
                    )
                split_notes.append({"target": "purchase_order", "ok": True})
        except Exception as exc:  # noqa: BLE001 — isolate the note write
            logger.warning("split PO note failed: %s", exc)
            split_notes.append(
                {"target": "purchase_order", "ok": False, "message": str(exc)}
            )
        if body.split_sibling_invoice_id:
            try:
                sib = lh.invoice(body.split_sibling_invoice_id)
                if isinstance(sib, dict) and sib.get("id"):
                    marker = (
                        f"Split order: order {order_label or body.purchase_order_number or ''} "
                        f"also covers {ref_label}"
                    ).replace("  ", " ")
                    notes = str(sib.get("notes") or "")
                    if marker not in notes:
                        sib["notes"] = (notes + "\n" if notes else "") + marker
                        lh.request(
                            "PUT",
                            f"/1.0/stock/internal/invoices/{body.split_sibling_invoice_id}",
                            sib,
                        )
                    split_notes.append({"target": "sibling_invoice", "ok": True})
            except Exception as exc:  # noqa: BLE001 — isolate the note write
                logger.warning("split sibling note failed: %s", exc)
                split_notes.append(
                    {"target": "sibling_invoice", "ok": False, "message": str(exc)}
                )

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
        "split_notes": split_notes,
        "variant_updates": variant_results,
    }


# ── Draft shaping ────────────────────────────────────────────────────────


def _ln_unit_cost(ln: dict):
    """Invoice-line unit cost — Loaded renamed unitCost → unitCostExclTax
    (observed live 05 Aug 2026); read the new name, fall back to the old.
    PO lines still use unitCost — these helpers are for INVOICE lines only."""
    v = ln.get("unitCostExclTax")
    return v if v is not None else ln.get("unitCost")


def _ln_total_cost(ln: dict):
    v = ln.get("totalCostExclTax")
    return v if v is not None else ln.get("totalCost")


def _inv_includes_tax(detail: dict) -> bool:
    v = detail.get("displayUnitCostInclusiveOfTax")
    if v is None:
        v = detail.get("unitCostIncludesTax")
    return bool(v)


def invoice_fingerprint(detail: dict) -> str:
    """Stable hash of an invoice's REVIEW-RELEVANT content.

    Loaded's ``version`` field is a static label ("Current"), so change
    detection hashes what the review actually depends on: the live lines
    (quantities, costs, units, item links), the totals, the PO link and the
    attached file. Anything else (e.g. notes) changing does not invalidate a
    cached review.

    FNV-1a (pure Python) kept from the era when a sandboxed consolidator had
    to reproduce it byte-for-byte (no hashlib there). Change detection, not
    security — collisions are inconsequential.
    """
    import json as _json

    material = {
        "lines": [
            [
                ln.get("id"),
                ln.get("quantityReceived"),
                _ln_unit_cost(ln),
                _ln_total_cost(ln),
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
        "unit_cost": _ln_unit_cost(ln),
        "total_cost": _ln_total_cost(ln),
        "tax_amount": ln.get("taxAmount"),
        "sale_tax_rate": ln.get("saleTaxRate"),
        "linked_item_id": ln.get("linkedItemId"),
        # linked_*_id absent while brand/unit set = Loaded would show it NEW.
        "linked_brand_id": ln.get("linkedBrandId"),
        "item_type": ln.get("itemType"),
    }


def attach_item_names(data: dict, lh: LoadedInvoiceClient) -> None:
    """Resolve linked stock items' NAMES onto the draft lines — the mirror's
    Description column.

    Loaded's "Stock Item Description" column shows the LINKED ITEM's name, not
    the supplier's raw line text (verified live: line description "Spianata
    Piccante 2kg C6" renders as the item's "SPIANATA PICCANTE"). The invoice
    detail carries only the raw description, so the names need one item fetch
    each. Latency-bounded: distinct ids fetched IN PARALLEL (one ~200-400ms
    burst), and the result persists on the working doc (``item_name`` +
    ``item_name_for``) so only the first open of a draft pays; a line is only
    re-resolved when its ``linked_item_id`` changes (re-link). Best-effort per
    item — a failed fetch leaves that line rendering the raw description.

    The raw ``description`` is deliberately untouched: the review engine's
    copy-matching, item-matching and the create-item prefill all key off it.
    """
    from concurrent.futures import ThreadPoolExecutor

    lines = data.get("lines") or []
    wanted = {
        ln["linked_item_id"]
        for ln in lines
        if ln.get("linked_item_id")
        and ln.get("item_name_for") != ln.get("linked_item_id")
    }
    if not wanted:
        return

    def fetch(item_id: str) -> tuple[str, str | None]:
        try:
            item = lh.get(f"/1.0/stock/internal/items/{item_id}")
            name = item.get("name") if isinstance(item, dict) else None
            return item_id, (str(name) if name else None)
        except Exception:  # noqa: BLE001 — display enhancement, never blocks
            return item_id, None

    with ThreadPoolExecutor(max_workers=min(8, len(wanted))) as pool:
        names = dict(pool.map(fetch, wanted))

    for ln in lines:
        item_id = ln.get("linked_item_id")
        if item_id in names and names[item_id]:
            ln["item_name"] = names[item_id]
            ln["item_name_for"] = item_id
        elif not item_id:
            # Un-linked (or re-set to NEW): no item to name.
            ln.pop("item_name", None)
            ln.pop("item_name_for", None)


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
    data = {
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
        "unit_cost_includes_tax": _inv_includes_tax(detail),
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
    # Pristine mirror of what Loaded returned, refreshed on every draft open
    # (admin-only "Loaded view" toggle in the editor): local edits mutate the
    # main fields, this snapshot always shows Loaded's current truth.
    data["loaded_snapshot"] = loaded_snapshot(data)
    return data


_SNAPSHOT_HEADER_KEYS = (
    "reference_number",
    "supplier_name",
    "linked_supplier_id",
    "purchase_order_number",
    "linked_purchase_order_id",
    "issued_at",
    "due_at",
    "received_at",
    "subtotal",
    "tax_amount",
    "discount_amount",
    "total",
    "unit_cost_includes_tax",
    "notes",
)
_SNAPSHOT_LINE_KEYS = (
    "id",
    "code",
    "description",
    "brand",
    "unit",
    "linked_unit_id",
    "unit_ratio",
    "quantity_received",
    "unit_cost",
    "total_cost",
    "tax_amount",
    "sale_tax_rate",
    "linked_item_id",
    "item_name",
)


def loaded_snapshot(shaped: dict) -> dict:
    """The editor-shape subset of a freshly-shaped draft — what Loaded holds
    RIGHT NOW for every editable field, header + lines."""
    return {
        "header": {k: shaped.get(k) for k in _SNAPSHOT_HEADER_KEYS},
        "lines": [
            {k: ln.get(k) for k in _SNAPSHOT_LINE_KEYS}
            for ln in shaped.get("lines") or []
            if isinstance(ln, dict)
        ],
    }


def carry_local_state(fresh: dict, old: dict) -> None:
    """Copy LOCAL editor state from an old received_invoice doc payload onto a
    freshly-rebuilt one (in place).

    Local state = things that exist only in the working document until receive:
    the suggestion action log, struck flags, item links (local until the
    receive PUT), and lines the editor added (id prefix "new-"). Used by
    _reshape_draft_after_write AND the batch fan-out's doc reuse — replacing
    doc.data wholesale without this silently discards the user's accepted
    edits. reset-validation deliberately does NOT call it (from-scratch).
    """
    for k in ("actioned_suggestions", "suggestion_actions"):
        if k in old:
            fresh[k] = old[k]
    old_lines = old.get("lines") or []
    fresh_by_id = {ln.get("id"): ln for ln in fresh.get("lines") or [] if ln.get("id")}
    for old_ln in old_lines:
        fresh_ln = fresh_by_id.get(old_ln.get("id"))
        if fresh_ln is None:
            if str(old_ln.get("id") or "").startswith("new-"):
                fresh.setdefault("lines", []).append(old_ln)
            continue
        if old_ln.get("struck"):
            fresh_ln["struck"] = True
        if old_ln.get("linked_item_id") and not fresh_ln.get("linked_item_id"):
            fresh_ln["linked_item_id"] = old_ln["linked_item_id"]
            if old_ln.get("item_name") is not None:
                fresh_ln["item_name"] = old_ln["item_name"]
            if not fresh_ln.get("linked_unit_id") and old_ln.get("linked_unit_id"):
                for k in ("linked_unit_id", "unit", "unit_ratio"):
                    if old_ln.get(k) is not None:
                        fresh_ln[k] = old_ln[k]


def receive_request_from_doc(
    data: dict, venue_id: str, invoice_id: str
) -> "ReceiveRequest":
    """Build the receive request server-side from a working document's WORKING
    values (Loaded's draft + accepted suggestions + manual edits).

    No receive-time pairing: working lines carry real Loaded line ids from
    birth; accepted add_line suggestions carry synthetic ids that
    ``do_receive`` appends (guarded on code/linked_item_id); strikes ride as
    ``struck`` → deletedAt. ``variant_updates`` are derived HERE (the
    ``original_unit_id`` each line was born with exists exactly for this) —
    the client no longer computes them.
    """
    lines = []
    variant_updates = []
    for ln in data.get("lines") or []:
        if not isinstance(ln, dict):
            continue
        qty, cost = ln.get("quantity_received"), ln.get("unit_cost")
        try:
            total = round(float(qty) * float(cost), 4)
        except (TypeError, ValueError):
            total = ln.get("total_cost")
        lines.append(
            {
                "id": ln.get("id"),
                "code": ln.get("code"),
                "description": ln.get("description"),
                "linked_item_id": ln.get("linked_item_id"),
                "unit": ln.get("unit"),
                "linked_unit_id": ln.get("linked_unit_id"),
                "unit_ratio": ln.get("unit_ratio"),
                "quantity_received": qty,
                "unit_cost": cost,
                "sale_tax_rate": ln.get("sale_tax_rate"),
                "total_cost": total,
                "struck": bool(ln.get("struck")),
            }
        )
        if (
            ln.get("linked_unit_id")
            and ln.get("original_unit_id")
            and ln["linked_unit_id"] != ln["original_unit_id"]
            and ln.get("linked_item_id")
            and ln.get("code")
        ):
            variant_updates.append(
                {
                    "linked_item_id": ln["linked_item_id"],
                    "line_code": ln["code"],
                    "unit_id": ln["linked_unit_id"],
                }
            )
    linked_po = data.get("linked_purchase_order_id")
    split_po_id = data.get("split_po_id")
    return ReceiveRequest(
        venue_id=venue_id,
        invoice_id=invoice_id,
        linked_purchase_order_id=linked_po,
        po_number=None,
        unlink_purchase_order=bool(data.get("po_unlinked")) and not linked_po,
        # Split order: the reference field is written without linking (Loaded
        # POs are 1:1) — only when no real link exists.
        purchase_order_number=(
            data.get("purchase_order_number") if split_po_id and not linked_po else None
        ),
        split_po_id=split_po_id,
        split_sibling_invoice_id=data.get("split_sibling_invoice_id"),
        lines=lines,
        variant_updates=variant_updates,
        receive=True,
        reference_number=data.get("reference_number"),
        issued_at=data.get("issued_at"),
        due_at=data.get("due_at"),
        received_at=data.get("received_at"),
        total=data.get("total"),
        subtotal=data.get("subtotal"),
        tax_amount=data.get("tax_amount"),
        linked_supplier_id=data.get("linked_supplier_id"),
        unit_cost_includes_tax=data.get("unit_cost_includes_tax"),
        notes=data.get("notes"),
    )


def invalidate_conflicting_drafts(
    db,
    venue_id: str,
    invoice_id: str,
    reference_number: str | None = None,
    supplier_name: str | None = None,
    po_ids: tuple | list = (),
    received: bool = True,
) -> None:
    """After an invoice is received (or its draft deleted), refresh the venue's
    OTHER open drafts that the action affects.

    Two effects, so a conversation full of Receive Invoice cards can never
    receive the same thing twice:
    - Twin docs of the ACTED invoice (repeated review runs create several docs
      for one invoice) are marked received, so their cards render as done.
    - Sibling drafts that would now conflict — same invoice number + supplier
      (a duplicate pair), or any overlap with the purchase orders the action
      touched — have their cached review cleared, so the card's next /review
      re-runs the engine against the fresh received feed and flips the
      duplicate / PO-already-invoiced state.

    Best-effort bookkeeping: never raises, commits only when it changed
    something.
    """
    from sqlalchemy.orm.attributes import flag_modified

    from app.db.models import WorkingDocument

    try:
        docs = (
            db.query(WorkingDocument)
            .filter(
                WorkingDocument.doc_type == "received_invoice",
                WorkingDocument.venue_id == venue_id,
            )
            .all()
        )
        # Identity of the acted invoice: prefer caller-supplied values, fill
        # gaps from its own docs (the delete path passes them explicitly since
        # its docs are already gone).
        own = [
            d for d in docs if (d.external_ref or {}).get("invoice_id") == invoice_id
        ]
        po_set = {str(p) for p in po_ids if p}
        for d in own:
            data = d.data or {}
            reference_number = reference_number or data.get("reference_number")
            supplier_name = supplier_name or data.get("supplier_name")
            for key in ("linked_purchase_order_id", "purchase_order_number"):
                if data.get(key):
                    po_set.add(str(data[key]))
            for s in data.get("suggestions") or []:
                if isinstance(s, dict):
                    for k in ("purchase_order_id", "po_number"):
                        if s.get(k):
                            po_set.add(str(s[k]))
        want_ref = _norm(reference_number)
        want_sup = _norm(supplier_name)

        changed = False
        for d in docs:
            data = d.data or {}
            if data.get("is_received") or data.get("is_deleted"):
                continue
            if (d.external_ref or {}).get("invoice_id") == invoice_id:
                if received:
                    data["is_received"] = True
                    data["status"] = "received"
                    d.data = data
                    d.version += 1
                    flag_modified(d, "data")
                    changed = True
                continue
            sib_ref = _norm(data.get("reference_number"))
            sib_sup = _norm(data.get("supplier_name"))
            duplicate = (
                bool(want_ref)
                and sib_ref == want_ref
                and (not want_sup or not sib_sup or sib_sup == want_sup)
            )
            sib_pos = {
                str(v)
                for v in (
                    data.get("linked_purchase_order_id"),
                    data.get("purchase_order_number"),
                )
                if v
            }
            for s in data.get("suggestions") or []:
                if isinstance(s, dict):
                    for k in ("purchase_order_id", "po_number"):
                        if s.get(k):
                            sib_pos.add(str(s[k]))
            if duplicate or (po_set and po_set & sib_pos):
                data["checks"] = None  # legacy-shape cache marker
                data["check_reasons"] = []
                data["suggestions"] = []
                # replica_v1 cache marker: clearing reviewed_at makes the
                # card's next open re-run the review against the fresh feed.
                data["reviewed_at"] = None
                data["issues"] = []
                d.data = data
                d.version += 1
                flag_modified(d, "data")
                changed = True
        if changed:
            db.commit()
    except Exception:
        logger.warning("invalidate_conflicting_drafts failed", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
