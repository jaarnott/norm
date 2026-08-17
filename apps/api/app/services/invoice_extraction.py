"""Canonical invoice-copy extraction: schema, prompts and the cached extract.

This module is the single source of truth for HOW Norm reads a supplier
invoice PDF. It owns:

- ``PDF_SCHEMA`` — the extraction schema (previously defined inside the
  review consolidator and regex-scraped by the dojo). The buyer-PO fields
  that used to need a second header-only extraction (``PO_EXTRACT_SCHEMA``)
  are folded in, so one extraction reads everything.
- ``BUILTIN_MAIN_PROMPT`` / ``main_prompt`` — the generic extraction prompt,
  overridable by the admin-edited "Main prompt" spec row.
- ``compose_pdf_instructions`` — the ONE composer for the extraction
  instructions: main prompt + the supplier-differs clause (naming Loaded's
  supplier and its aliases) + the matching supplier spec's notes. The
  composed text is part of the extraction cache key, so a prompt or spec
  edit re-extracts affected invoices exactly once.
- ``extract_invoice_copy`` / ``extract_invoice_copies_parallel`` — the
  cached extraction itself, sharing the ``DocumentExtraction`` cache rows
  (and key material shape) with the consolidator executor so
  ``/invoice-fixes/reset-validation`` keeps matching rows by
  ``action == "download_invoice_file"``.

The dojo (``spec_dojo``) and the live review path both import from here;
neither scrapes the consolidator source any more.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.connectors.function_executor import (
    _extraction_cache_get,
    _extraction_cache_key,
    _extraction_cache_put,
)
from app.db.config_models import SupplierInvoiceSpec
from app.services.supplier_identity import match_spec, norm

logger = logging.getLogger(__name__)

# The reserved spec row holding the admin-editable main extraction prompt.
MAIN_PROMPT_NAME = "Main prompt"

PDF_SCHEMA = {
    "document_type": (
        "one of 'invoice', 'credit_note', 'statement', 'other' — what the "
        "document IS. A STATEMENT summarises an account: it lists prior "
        "invoice numbers, payments and balances (e.g. 'Balance Brought "
        "Forward', 'Payment') instead of billing products. An INVOICE bills "
        "products/services with quantities and prices."
    ),
    "supplier_name": "string or null",
    "supplier_differs": (
        "boolean — ONLY meaningful when the instructions name Loaded's "
        "supplier for this invoice: true when the supplier printed on the "
        "document is a DIFFERENT BUSINESS from the named one. Naming "
        "variations of the same business ('Hancocks' vs 'Hancock Ltd' vs "
        "'Hancocks Family Merchants') are the SAME business — false. "
        "Omit/false when unsure or when no Loaded supplier was named."
    ),
    "invoice_number": "string or null",
    "invoice_date": "string or null",
    # Buyer vs supplier order references. Loaded's own purchaseOrderNumber
    # field is often the SUPPLIER's order number (e.g. Bidfood "O/N"), not
    # the buyer PO that matches a Loaded purchase order — hence two explicit
    # fields. (A third legacy catch-all `purchase_order_number` was retired
    # 17 Aug 2026: undocumented, redundant with customer_…, and its null
    # made passing extractions read as failures in the dojo.)
    "customer_purchase_order_number": (
        "string or null — the BUYER's / customer's purchase order number "
        "(the number the buyer raised in their own system), labelled "
        "'Customer Order No', 'Cust Order No', 'Your Order', 'Your Ref', "
        "'PO Number', 'Order No'"
    ),
    # DECOY, kept deliberately: nothing downstream acts on this field. Its
    # job is to give the supplier's own number an explicit home so it never
    # gets misfiled into customer_purchase_order_number (the wrong-PO-link
    # failure class the split exists to prevent). It also rides along in the
    # working document's extracted_snapshot for human reference.
    "supplier_order_number": (
        "string or null — the SUPPLIER's own order/reference number "
        "(labelled 'O/N', 'Our Order', 'Sales Order', etc.), NOT the "
        "buyer's PO"
    ),
    "lines": [
        {
            "code": "string or null — the product/item code column",
            "description": "string",
            "quantity": (
                "number — the TOTAL count of individual units billed for the "
                "line, per the quantity rules in the instructions (NOT "
                "necessarily a single printed column)"
            ),
            "unit": "string or null — EXACTLY as printed on the document",
            "unit_of_measure": (
                "string or null — the DELIVERED unit of ONE item, per the "
                "unit rules in the instructions (e.g. 'Kilo', '5L', '500g', "
                "'750ml', '12 pack', '100 piece'); null if not determinable"
            ),
            "unit_unrecognisable": (
                "boolean — true when the document DOES carry size/pack "
                "information for this line but it cannot be confidently "
                "determined: cut off (e.g. a description ending "
                "mid-parenthesis like '(1'), illegible, or ambiguous. "
                "Omit/false when the unit was derived, or when the document "
                "simply prints no size information at all."
            ),
            "unit_price_ex_tax": "number — exactly as printed",
            "line_total_ex_tax": "number — exactly as printed",
        }
    ],
    "subtotal_ex_tax": "number or null",
    "discount_amount": (
        "number or null — a document-level discount/rebate amount when one "
        "is printed (a positive number); null when none is printed"
    ),
    "tax_amount": "number or null",
    "total_incl_tax": "number or null",
}

# The GENERIC extraction prompt — deliberately simple. Layout quirks and
# per-supplier conventions belong in supplier spec rows, not here. This text
# doubles as the seed/fallback for the admin-editable "Main prompt" spec row.
BUILTIN_MAIN_PROMPT = (
    "Extract every billed LINE and the totals from this supplier "
    "invoice. Non-product charges (freight, delivery, card fees) "
    "are LINES too, wherever the document prints them — quantity 1 "
    "unless printed otherwise; unit may be null.\n\n"
    "FIRST determine document_type: a document headed "
    "'Statement' or structured as an account summary (rows of "
    "invoice numbers, payments, balances brought forward) is a "
    "'statement', NOT an invoice — still extract what you "
    "can. A document headed 'Credit Note', 'Credit', 'Credit "
    "Advice' or 'Returns' — goods sent back or an amount "
    "refunded to the customer — is a 'credit_note'. Report a "
    "credit note's numbers EXACTLY as printed, like any other "
    "document: if it prints minus signs, keep them; if it "
    "prints plain positive figures, report them positive. "
    "Never add a minus sign the document does not print, and "
    "never drop one it does.\n\n"
    "QUANTITY rules — quantity is the TOTAL number of individual "
    "units billed for the line:\n"
    "- Some suppliers SPLIT the quantity across columns (e.g. a "
    "cartons/CTN column and a single-units column): the billed "
    "quantity is cartons x pack size + singles (1 carton of 12 "
    "plus 4 singles = 16). Never report just one column of a "
    "split.\n"
    "- SELF-CHECK every line: quantity x unit_price_ex_tax must "
    "equal line_total_ex_tax (within a cent). If your quantity "
    "fails this check, re-read the line.\n"
    "- unit_price_ex_tax is the price of ONE unit exactly as "
    "printed; never adjust it to make the arithmetic work.\n\n"
    "For each line also derive unit_of_measure — the unit ONE "
    "delivered item is used in for recipe costing:\n"
    "- A weight, volume or count — never a length or a bare "
    "packaging word (pkt/box/carton/outer/unit).\n"
    "- Find the size in the unit/size columns first, then in the "
    "item description ('900ml', '500g', '4 Litre', 'Cider 330ml "
    "4x6').\n"
    "- Quantity and unit price stay AS PRINTED in their columns "
    "too — never decompose a pack into inner items: 2 cases of "
    "'6x 750ml' at $104.04/case is quantity 2 at 104.04, NEVER "
    "quantity 12 bottles at $17.34.\n"
    "- Keep it exactly as printed — never convert, multiply or "
    "split pack notation: a 5X3KG pack → '5x3kg', a '4 x 6 Pack' "
    "→ '4x6 pack', a 12PK → '12 pack', a single 2L bottle → "
    "'2L'.\n"
    "- Delivered as single inner items out of a larger pack → the "
    "inner size alone.\n"
    "- Random weight billed per kg (meat/seafood/produce) → "
    "'Kilo', never the total weight.\n"
    "- Exactly 1 of a base unit drops the 1: '1kg' → 'Kilo', "
    "'1L' → 'Litre', '1 each' → 'each'.\n"
    "- No confident unit → return null. Size present but "
    "unreadable (cut off, illegible) → null AND unit_unrecognisable "
    "true — never guess from partial text."
)


def _norm(text: object) -> str:
    return norm(text)


def main_prompt(config_db: Session) -> str:
    """The admin-edited main prompt (reserved spec row), else the built-in.

    A missing or emptied row can never break extraction.
    """
    row = (
        config_db.query(SupplierInvoiceSpec)
        .filter(
            SupplierInvoiceSpec.name == MAIN_PROMPT_NAME,
            SupplierInvoiceSpec.enabled.is_(True),
        )
        .first()
    )
    text = (row.instructions or "").strip() if row else ""
    return text or BUILTIN_MAIN_PROMPT


def find_spec_for_supplier(
    config_db: Session, *supplier_names: object
) -> SupplierInvoiceSpec | None:
    """The layout spec for a supplier, by every name we know it under.

    Takes any number of identity hints — the name printed on the copy, the
    name Loaded records, that supplier's Loaded aliases — because a global
    spec is keyed to ONE canonical business name and the account's local
    spellings are what vary.

    Precedence and ambiguity live in ``supplier_identity.match_spec``; this
    only supplies the rows. Ambiguous → None, which means the generic prompt
    plus a sensei pass rather than a confidently wrong spec.
    """
    spec, _how = match_spec(
        config_db.query(SupplierInvoiceSpec)
        .filter(SupplierInvoiceSpec.enabled.is_(True))
        .all(),
        supplier_names,
        main_prompt_name=MAIN_PROMPT_NAME,
    )
    return spec


def compose_pdf_instructions(
    config_db: Session,
    *,
    loaded_supplier: object = None,
    loaded_aliases: tuple | list = (),
    spec_notes: str = "",
    spec_name: object = None,
    main_override: str | None = None,
) -> str:
    """The ONE extraction-instruction composer.

    main prompt + (when Loaded names a supplier) the supplier-differs clause
    listing that supplier and its stored aliases + (when a spec matches) the
    supplier-specific notes. Every part is in the extraction cache key, so
    any edit re-extracts affected invoices exactly once. Aliases must be
    passed pre-sorted by the caller for key stability.
    """
    main = main_override.strip() if main_override is not None else ""
    if not main:
        main = main_prompt(config_db)
    text = main
    if loaded_supplier:
        aliases = [str(a) for a in loaded_aliases if a]
        text += (
            "\n\nLoaded records this invoice's supplier as '"
            + str(loaded_supplier)
            + "'"
            + (
                " (also known as: " + ", ".join("'" + a + "'" for a in aliases) + ")"
                if aliases
                else ""
            )
            + ". In supplier_name return the supplier printed on the "
            "document; set supplier_differs true ONLY when that is a "
            "DIFFERENT BUSINESS from ALL of those names (naming "
            "variations are the same business)."
        )
    notes = (spec_notes or "").strip()
    if notes:
        text += (
            "\n\nSupplier-specific notes for "
            + str(spec_name or loaded_supplier)
            + ":\n"
            + notes
        )
    return text


def pdf_instructions_for(
    config_db: Session,
    *,
    loaded_supplier: object = None,
    loaded_aliases: tuple | list = (),
) -> str:
    """Compose the live extraction instructions for an invoice: resolves the
    matching supplier spec's notes and delegates to the composer.

    Loaded's aliases for this supplier are identity hints too, not just prompt
    context: they are the ACCOUNT's spellings of one business, so a global
    spec named for any one of them still matches.
    """
    spec = find_spec_for_supplier(config_db, loaded_supplier, *loaded_aliases)
    return compose_pdf_instructions(
        config_db,
        loaded_supplier=loaded_supplier,
        loaded_aliases=loaded_aliases,
        spec_notes=(spec.instructions or "") if spec else "",
        spec_name=spec.name if spec else None,
    )


def extraction_system_prompt(schema: dict | None = None) -> str:
    """The extraction envelope — same shape as the consolidator executor's,
    so extractions behave identically on every path."""
    schema_text = json.dumps(schema or PDF_SCHEMA, indent=1)
    return (
        "You extract structured data from a document exactly as printed. "
        "Return ONLY a JSON object matching this schema (no markdown, no "
        f"commentary):\n{schema_text}\n"
        "Rules: copy amounts, quantities and identifiers exactly as they "
        "appear in the document; use null for any field that is not "
        "present or not legible; never guess or compute values."
    )


def _cache_key(venue_key: object, file_id: object, instructions: str) -> str:
    # connector/action mirror the consolidator executor's key material so
    # cache rows keep the shape /invoice-fixes/reset-validation matches on.
    return _extraction_cache_key(
        "loadedhub",
        "download_invoice_file",
        {"venue": str(venue_key or ""), "file_id": str(file_id or "")},
        PDF_SCHEMA,
        instructions or "",
    )


def _extract_uncached(
    session: Session,
    lh,
    file_id: str,
    instructions: str,
    cache_key: str,
    thread_id: str | None,
) -> dict:
    from app.interpreter.llm_interpreter import call_llm

    content_b64, content_type = lh.file_base64(file_id)
    parsed, _ = call_llm(
        system_prompt=extraction_system_prompt(),
        user_prompt=instructions or "Extract the fields from the attached document.",
        db=session,
        thread_id=thread_id,
        call_type="extraction",
        max_tokens=4096,
        documents=[
            {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": content_type or "application/pdf",
                    "data": content_b64,
                },
            }
        ],
    )
    if not isinstance(parsed, dict):
        return {"error": "extraction did not return an object"}
    # Cache only clean extractions — never an error dict, and never a result
    # with no usable fields (a transient read failure).
    if "error" not in parsed and any(v is not None for v in parsed.values()):
        _extraction_cache_put(
            session, cache_key, "loadedhub", "download_invoice_file", parsed
        )
    return parsed


def extract_invoice_copy(
    db: Session,
    lh,
    file_id: str,
    *,
    instructions: str,
    venue_key: object = None,
    thread_id: str | None = None,
) -> dict:
    """Cached extraction of one invoice copy.

    Returns the parsed dict, or ``{"error": ...}`` — callers must treat an
    error dict as "could not read the document", never as a successful
    extraction. Never raises.
    """
    key = _cache_key(venue_key, file_id, instructions)
    cached = _extraction_cache_get(db, key)
    if cached is not None:
        return cached
    try:
        return _extract_uncached(db, lh, file_id, instructions, key, thread_id)
    except Exception as exc:  # noqa: BLE001 — an unreadable copy is a finding
        logger.warning("invoice extraction failed for file %s: %s", file_id, exc)
        return {"error": str(exc)}


def extract_invoice_copies_parallel(
    db: Session,
    lh,
    requests: list[dict],
    *,
    max_workers: int = 10,
) -> list[dict]:
    """Extract many copies concurrently (same pattern as the consolidator
    executor's ``extract_documents_parallel``).

    Each request: ``{"file_id", "instructions", "venue_key"?}``. Returns one
    result per request in order. Cache hits answer from the caller's session;
    each miss runs on its OWN committed session so a finished extraction is
    durable immediately and one bad document never poisons the batch.
    """
    from concurrent.futures import ThreadPoolExecutor

    requests = requests or []
    results: list[dict] = [None] * len(requests)  # type: ignore[list-item]
    pending: list[tuple[int, dict, str]] = []
    for i, r in enumerate(requests):
        r = r if isinstance(r, dict) else {}
        key = _cache_key(r.get("venue_key"), r.get("file_id"), r.get("instructions"))
        cached = _extraction_cache_get(db, key)
        if cached is not None:
            results[i] = cached
        else:
            pending.append((i, r, key))
    if not pending:
        return results

    from app.db.engine import SessionLocal

    def _worker(item: tuple[int, dict, str]):
        i, r, key = item
        worker_db = SessionLocal()
        try:
            parsed = _extract_uncached(
                worker_db,
                lh,
                str(r.get("file_id") or ""),
                str(r.get("instructions") or ""),
                key,
                None,
            )
            worker_db.commit()
            return i, parsed
        except Exception as exc:  # noqa: BLE001 — per-document isolation
            worker_db.rollback()
            logger.warning(
                "invoice extraction failed for file %s: %s", r.get("file_id"), exc
            )
            return i, {"error": str(exc)}
        finally:
            worker_db.close()

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for i, parsed in pool.map(_worker, pending):
            results[i] = parsed
    return results
