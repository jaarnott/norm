"""What the invoice copy says — from whatever Norm already knows.

Reconciliation and receiving read the same piece of paper. Receiving reads it
with the supplier's own extraction spec (trained in the dojo) and stores the
result verbatim on the invoice's working document; reconciliation was reading
it again with a private five-field schema and one generic instruction. Two
consequences, both measured on the 17 Aug 2026 run:

- The private schema asks for a single ``purchase_order_number``. ``PDF_SCHEMA``
  splits that in two ON PURPOSE — ``customer_purchase_order_number`` (ours, the
  one that matches a Loaded PO) and ``supplier_order_number`` (theirs, kept as
  a decoy so it can never be misfiled as ours). Without the split, an invoice
  printing ORD10658598 above our 1520599 reads as a PO mismatch. That was 27 of
  67 failures.
- Schema and instructions are both cache-key material, so the two paths shared
  no cache rows: every copy the receive flow had already read was read and paid
  for again, one model call at a time.

So this module is deliberately a READER of the receive path, never a fork of
it. It adds nothing to the extraction contract and changes nothing about how
receiving works — it looks up what receiving already produced, and when there
is nothing to find it calls the same extractor with the same schema and the
same per-supplier instructions, landing on the same cache row.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: Where the header came from, reported per invoice so a run can be audited:
#: "norm read this months ago" is a very different cost from "we just read it".
SOURCE_STORED = "stored"
SOURCE_EXTRACTED = "extracted"


def stored_header(db: Session, venue_id: str, invoice_id: str) -> dict | None:
    """The extraction the RECEIVE flow already made for this invoice, if any.

    `invoice_review` writes it verbatim onto the working document as
    `extracted_snapshot` — same schema, same spec-composed instructions, and no
    LLM call to repeat. Deleted and superseded documents are skipped, but a
    RECEIVED one is exactly what reconciliation wants: the invoice is received,
    that is why it is being reconciled.
    """
    from app.db.models import WorkingDocument

    rows = (
        db.query(WorkingDocument)
        .filter(
            WorkingDocument.doc_type == "received_invoice",
            WorkingDocument.venue_id == venue_id,
        )
        .all()
    )
    docs = [
        d
        for d in rows
        if (d.external_ref or {}).get("invoice_id") == invoice_id
        and not (d.data or {}).get("is_deleted")
    ]
    if not docs:
        return None
    # Newest first; a doc that carries a review is better evidence than a bare
    # draft that never got one.
    docs.sort(
        key=lambda d: (
            bool((d.data or {}).get("reviewed_at")),
            d.updated_at or d.created_at,
        ),
        reverse=True,
    )
    header = ((docs[0].data or {}).get("extracted_snapshot") or {}).get("header")
    return dict(header) if isinstance(header, dict) else None


def copy_headers(
    db: Session,
    config_db: Session,
    lh,
    venue_id: str,
    invoices: list[dict],
) -> dict[str, dict]:
    """``{invoice_id: header}`` for every invoice, cheapest source first.

    ``invoices`` items need ``id`` and may carry ``fileId``, ``supplierName``
    and ``purchaseOrderNumber``. The returned header is a ``PDF_SCHEMA`` header
    plus ``_source`` (stored | extracted), ``_po_verdict``/``_po_note``, and on
    failure ``error``.

    The PO verdict is stamped HERE rather than by the caller: the consolidator
    that consumes this runs sandboxed and cannot import anything, and the rule
    for telling our PO number from the supplier's is worth having in exactly
    one tested place.
    """
    from app.services.invoice_extraction import (
        extract_invoice_copies_parallel,
        pdf_instructions_for,
    )

    out: dict[str, dict] = {}
    pending: list[dict] = []

    for inv in invoices:
        iid = str(inv.get("id") or "")
        if not iid:
            continue
        header = stored_header(db, venue_id, iid)
        if header is not None:
            out[iid] = {**header, "_source": SOURCE_STORED}
            continue
        if not inv.get("fileId"):
            out[iid] = {
                "error": "no invoice copy attached",
                "_source": SOURCE_EXTRACTED,
            }
            continue
        pending.append(inv)

    if pending:
        # One instruction set per SUPPLIER, not per invoice: composing it hits
        # the config DB for the spec, and a run is mostly a handful of
        # suppliers with many invoices each.
        by_supplier: dict[str, str] = {}
        requests = []
        for inv in pending:
            name = str(inv.get("supplierName") or "")
            if name not in by_supplier:
                by_supplier[name] = pdf_instructions_for(
                    config_db, loaded_supplier=name or None
                )
            requests.append(
                {
                    "file_id": inv.get("fileId"),
                    "instructions": by_supplier[name],
                    "venue_key": venue_id,
                }
            )
        results = extract_invoice_copies_parallel(db, lh, requests)
        for inv, res in zip(pending, results):
            res = res if isinstance(res, dict) else {"error": "unreadable"}
            out[str(inv["id"])] = {**res, "_source": SOURCE_EXTRACTED}

    by_id = {str(i.get("id")): i for i in invoices if i.get("id")}
    for iid, header in out.items():
        if header.get("error"):
            continue
        state, note = po_verdict(
            (by_id.get(iid) or {}).get("purchaseOrderNumber"), header
        )
        header["_po_verdict"], header["_po_note"] = state, note

    return out


def po_verdict(loaded_po: object, header: dict) -> tuple[str, str]:
    """``(verdict, note)`` for one invoice's PO — ``match`` | ``mismatch`` | ``absent``.

    Reconcile only when Norm can tell OUR number from the supplier's and they
    agree. Two ways that happens, and both are a real match:

    - the copy's customer PO equals Loaded's — the ordinary case;
    - the copy's SUPPLIER order number equals Loaded's — Loaded's own
      `purchaseOrderNumber` is frequently the supplier's number rather than
      ours (documented in tests/test_invoice_fixes_handler.py), so the two
      sides are naming the same document by the supplier's name for it.

    Anything else is left as a mismatch for a person. Two numbers that are
    genuinely different is exactly what this check is for.
    """
    from app.services.received_invoice import _po_key

    ours = _po_key(header.get("customer_purchase_order_number"))
    theirs = _po_key(header.get("supplier_order_number"))
    loaded = _po_key(loaded_po)

    if not loaded:
        found = header.get("customer_purchase_order_number") or header.get(
            "supplier_order_number"
        )
        return "absent", (
            f"Received invoice has no PO number (the copy shows {found})"
            if found
            else "No PO number on the received invoice or the invoice copy"
        )
    if ours and ours == loaded:
        return "match", ""
    if theirs and theirs == loaded:
        return "match", (
            "matched on the supplier's own order number — Loaded's PO field "
            f"holds {header.get('supplier_order_number')}, not a Loaded order"
        )
    if ours:
        return "mismatch", (
            f"PO number mismatch: received invoice PO#{loaded_po} vs invoice "
            f"copy {header.get('customer_purchase_order_number')}"
        )
    return "mismatch", (
        f"PO number mismatch: received invoice PO#{loaded_po} vs invoice copy "
        f"{header.get('supplier_order_number') or '(none found)'}"
    )
