"""End-to-end proof on the invoice that exposed the fault.

Runs the REAL review path — real Loaded reads, a real extraction — against
IN11413982 (SERVICE FOODS LTD -> La Zeppa) and asserts the three things that
were each wrong on 11 Aug 2026:

  1. the extraction prompt is Service Foods', not the Eurovintage wine spec;
  2. the copy's 'SERVICE FOODS LTD' resolves to the Loaded supplier record
     'SERVICE FOODS AUCKLAND', instead of logging UNRESOLVED;
  3. no blocking supplier_unresolved issue remains.

Read-only against Loaded: it reviews, it never receives. It DOES cost one
Opus extraction per run (the cache makes a repeat free only while the composed
prompt is unchanged — and fixing the spec deliberately changes it).

    set -a && source ../../.env && set +a && uv run python scripts/e2e_supplier_matching.py
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

import app.main as _main  # noqa: E402

_main._load_system_secrets()  # the API key lives in the config DB

from app.db.engine import SessionLocal, _ConfigSessionLocal  # noqa: E402
from app.services.invoice_extraction import find_spec_for_supplier  # noqa: E402
from app.services.invoice_review import review_invoice  # noqa: E402
from app.services.received_invoice import LoadedInvoiceClient  # noqa: E402

VENUE = "13dac930-434b-4947-b2cf-521e530b56c1"  # La Zeppa
INVOICE = "9c27631a-3577-4f0c-5946-08def73605ad"  # IN11413982
WANT_SUPPLIER_ID = "616d85fd-3cc7-4750-b9da-781233121474"  # SERVICE FOODS AUCKLAND


def main() -> int:
    db, cdb = SessionLocal(), _ConfigSessionLocal()
    lh = LoadedInvoiceClient(db, cdb, VENUE)
    detail = lh.invoice(INVOICE)
    failures: list[str] = []

    # 1 — spec selection, from the names Loaded knows this supplier by.
    spec = find_spec_for_supplier(cdb, detail.get("supplierName"))
    got = spec.name if spec else None
    print(f"1. spec for {detail.get('supplierName')!r}: {got!r}")
    if got != "Service Foods":
        failures.append(f"expected the 'Service Foods' spec, got {got!r}")

    # 2/3 — the real review.
    doc = review_invoice(db, cdb, VENUE, INVOICE, lh=lh, require_valid_po=False)
    sup_id = doc.get("linked_supplier_id")
    line = next(
        (
            r
            for r in (doc.get("replica") or {}).get("resolution_log", [])
            if "supplier:" in r
        ),
        "(no supplier line)",
    )
    print(f"2. {line}")
    print(f"   linked_supplier_id: {sup_id}")
    if sup_id != WANT_SUPPLIER_ID:
        failures.append(
            f"supplier resolved to {sup_id!r}, wanted SERVICE FOODS AUCKLAND"
        )

    blocking = [
        i
        for i in (doc.get("issues") or [])
        if i.get("code") == "supplier_unresolved" and i.get("blocking")
    ]
    print(f"3. blocking supplier_unresolved issues: {len(blocking)}")
    if blocking:
        failures.append("the invoice is still blocked on an unresolved supplier")

    # Informational: what the right prompt actually read off the paper.
    print(
        f"   PO number: {doc.get('purchase_order_number')!r} · "
        f"units: {[ln.get('unit') for ln in (doc.get('lines') or [])]}"
    )

    print("\nFAILED:" if failures else "\nPASS — all three assertions hold")
    for f in failures:
        print(f"  - {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
