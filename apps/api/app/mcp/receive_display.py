"""Build the display block for a ``received_invoice`` working document.

The invoice analog of ``po_display.py``. When a playbook opens an invoice to
receive it, this turns the draft into the SAME ReceiveInvoiceEditor the web app
mounts — pre-resolving the reference data (the units catalogue and the
candidate purchase orders) into the block server-side, so the card paints
complete inside Claude with no callbacks (the sandbox has no route to the
web-only ``/invoice-fixes/*`` reads).

Two deliberate degradations for the embedded surface:

- The **PDF viewer** cannot work in a sandboxed iframe (no session to
  authenticate the file stream, no ``window.open`` for a blob), so the card
  hides it when ``props.embedded`` and offers ``open_in_norm`` instead.
- Pre-resolution is **best-effort**: a credentials/network failure leaves the
  card without the unit catalogue, and it degrades to accept-as-shown rather
  than failing the block.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.mcp.principal import McpPrincipal

logger = logging.getLogger(__name__)


def receive_editor_block(
    payload: dict,
    venue_id: str | None,
    principal: McpPrincipal,
    db: Session,
    config_db: Session,
) -> dict | None:
    """The ``{component, data, props}`` block for a received-invoice draft.

    Returns None to let the caller fall back to the workflow card.
    """
    from app.db.models import Thread, WorkingDocument
    from app.services.received_invoice import build_received_invoice_data

    doc = (
        db.query(WorkingDocument)
        .filter(WorkingDocument.id == payload.get("working_document_id"))
        .first()
    )
    if doc is None:
        return None
    # The doc was created by this same call, but re-check ownership rather than
    # trust the payload — it is worker-thread output, not a trusted handle.
    thread = db.query(Thread).filter(Thread.id == doc.thread_id).first()
    if thread is None or thread.user_id != principal.user_id:
        return None

    data = dict(doc.data or {})
    # The playbook path stores the raw get_invoice_detail; shape it (and persist
    # the shaped form, so the editor's update_line-by-index patches address real
    # rows — the same reason po_display persists resolved lines).
    if "reference_number" not in data:
        data = build_received_invoice_data(data)
        from sqlalchemy.orm.attributes import flag_modified

        doc.data = data
        flag_modified(doc, "data")
        db.commit()
        data = dict(doc.data or {})

    venue_id = venue_id or data.get("venue_id") or doc.venue_id
    data["working_document_id"] = doc.id
    data["thread_id"] = doc.thread_id

    _attach_reference_data(data, venue_id, db, config_db)
    _attach_suggestions(data, venue_id, db, config_db)

    # Persist the merged review/suggestions so the SAME WorkingDocument caches
    # them for every surface (a later web open reads this instead of re-running).
    from sqlalchemy.orm.attributes import flag_modified

    doc.data = {
        k: v for k, v in data.items() if k not in ("working_document_id", "thread_id")
    }
    flag_modified(doc, "data")
    db.commit()

    props: dict = {
        "embedded": True,
        "thread_id": doc.thread_id,
        "title": (
            f"Receive Invoice · {data.get('reference_number')}"
            if data.get("reference_number")
            else "Receive Invoice"
        ),
        "open_in_norm": payload.get("open_in_norm"),
    }
    if venue_id:
        props["activeVenueId"] = venue_id
    block = {"component": "receive_invoice_editor", "data": data, "props": props}
    summary = _suggestion_summary(data)
    if summary:
        # Deterministic narration for the MODEL (the card is for the human):
        # surfaced into the tool result so Claude can talk about the checks and
        # suggestions it cannot see inside the iframe. String assembly only.
        block["result_summary"] = summary
    return block


def _attach_reference_data(
    data: dict, venue_id: str | None, db: Session, config_db: Session
) -> None:
    """Bake the unit catalogue + candidate POs into the block (best-effort).

    Mirrors what the web editor fetches from ``/invoice-fixes/units`` and
    ``/invoice-fixes/purchase-orders`` — done here so the embedded card needs no
    round-trips. A failure is swallowed: the card still renders and can be
    accepted as shown.
    """
    if not venue_id:
        return
    from app.services.received_invoice import LoadedInvoiceClient

    try:
        lh = LoadedInvoiceClient(db, config_db, venue_id)
    except Exception as exc:  # noqa: BLE001 — reference data is enhancement
        logger.info("receive_display: no client for reference data: %s", exc)
        return

    try:
        # Ordered qty / substitution flags / un-received lines from the linked PO,
        # matched by stock code — the same reference data the web draft attaches.
        from app.routers.invoice_fixes import _attach_po_reference
        from app.services.invoice_po_reference import enrich_loaded_snapshot
        from app.services.received_invoice import attach_item_names

        _attach_po_reference(data, lh)
        attach_item_names(data, lh)
        enrich_loaded_snapshot(data)
    except Exception as exc:  # noqa: BLE001 — reference data is enhancement
        logger.info("receive_display: PO reference pre-resolve failed: %s", exc)

    try:
        units = lh.get("/1.0/stock/internal/units")
        data["_units"] = [
            {
                "id": u.get("id"),
                "name": u.get("name"),
                "type": u.get("stockUnitType"),
                "ratio": u.get("ratio"),
            }
            for u in (units or [])
            if isinstance(u, dict) and not u.get("datestampDeleted")
        ]
    except Exception as exc:  # noqa: BLE001
        logger.info("receive_display: units pre-resolve failed: %s", exc)

    try:
        pos = lh.get(
            "/1.0/stock/internal/purchase-orders?from=1901-01-01&to=9999-12-31"
        )
        pos = pos if isinstance(pos, list) else (pos or {}).get("data") or []
        supplier_id = data.get("linked_supplier_id")
        data["_purchase_orders"] = [
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
            if isinstance(p, dict)
            and not p.get("datestampDeleted")
            # Mirror the editor's own candidate filter: this invoice's supplier.
            and (not supplier_id or p.get("supplierId") == supplier_id)
        ]
    except Exception as exc:  # noqa: BLE001
        logger.info("receive_display: POs pre-resolve failed: %s", exc)


def _attach_suggestions(
    data: dict, venue_id: str | None, db: Session, config_db: Session
) -> None:
    """Run the replica review and REPLACE the draft payload (best-effort).

    The same pipeline the web ``/invoice-fixes/review`` endpoint runs
    (``services/invoice_review.review_invoice``), so the embedded card carries
    the same suggestions, issues and confidence as the web editor. Guarded by
    the draft's own cache (``reviewed_at``), so re-opens are free; a failure
    degrades to the plain mirror rather than failing the block.
    """
    from app.services.invoice_review import DOC_SCHEMA, review_invoice

    if not venue_id:
        return
    if data.get("doc_schema") == DOC_SCHEMA and data.get("reviewed_at"):
        return
    try:
        fresh = review_invoice(
            db,
            config_db,
            venue_id,
            str(data.get("invoice_id") or ""),
            require_valid_po=False,  # interactive card — note, not block
        )
        keep = {
            k: data[k]
            for k in ("working_document_id", "thread_id", "venue_id")
            if k in data
        }
        data.clear()
        data.update(fresh)
        data.update(keep)
    except Exception as exc:  # noqa: BLE001 — suggestions are enhancement
        logger.info("receive_display: review/suggestions failed: %s", exc)


def _suggestion_summary(data: dict) -> str | None:
    """One deterministic text block for the MODEL: confidence + issues +
    suggestions. The card shows the human the same thing interactively; this
    keeps Claude's narration accurate without a second data path or any LLM
    call."""
    parts: list[str] = []
    confidence = data.get("confidence")
    issues = data.get("issues") or []
    suggestions = data.get("suggestions") or []
    # Lead with it: Claude cannot see inside the card, and "this document
    # REVERSES stock and cost" changes what every other number means.
    if data.get("is_credit_note"):
        parts.append(
            "This is a CREDIT NOTE — receiving it reverses stock and cost "
            "(quantities and totals are negative)."
        )
    if confidence:
        blocking = sum(1 for i in issues if i.get("blocking"))
        parts.append(
            f"Review: {'ready to receive' if confidence == 'ready' else 'needs review'}"
            + (f" — {blocking} blocking issue(s)" if blocking else "")
            + (f", {len(suggestions)} suggested change(s)" if suggestions else "")
            + "."
        )
    for i in issues:
        if i.get("message"):
            flag = "BLOCKING" if i.get("blocking") else "note"
            parts.append(f"- {flag}: {i['message']}")
    for s in suggestions:
        if s.get("explanation"):
            parts.append(f"- Suggested: {s['explanation']}")
    return "\n".join(parts) if parts else None
