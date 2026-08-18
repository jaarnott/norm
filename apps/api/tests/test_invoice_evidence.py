"""Reconciliation reads the invoice through the RECEIVE path's eyes.

The 17 Aug 2026 run failed 67 invoices; 27 were "PO number mismatch" where the
copy printed the supplier's own order number (ORD10658598) beside our PO
(1520599). The receive flow had already extracted BOTH numbers correctly for
those same invoices — reconciliation just wasn't looking, because it ran its
own five-field schema asking for a single `purchase_order_number`.

These pin the two things that fixes: read what receiving already produced, and
when reading fresh use the same schema and the same per-supplier instructions
so the cache row is shared rather than paid for twice.
"""

import pytest

from app.services import invoice_evidence as EV


class _Doc:
    def __init__(self, invoice_id, header, *, reviewed=True, deleted=False, updated=1):
        self.doc_type = "received_invoice"
        self.venue_id = "v-1"
        self.external_ref = {"invoice_id": invoice_id}
        self.updated_at = updated
        self.created_at = updated
        self.data = {
            "extracted_snapshot": {"header": header} if header else {},
            "reviewed_at": "2026-08-17T00:00:00Z" if reviewed else None,
            "is_deleted": deleted,
        }


class _Db:
    def __init__(self, docs):
        self._docs = docs

    def query(self, _model):
        return self

    def filter(self, *_a, **_k):
        return self

    def all(self):
        return self._docs


HEADER = {
    "invoice_number": "IN11437546",
    "customer_purchase_order_number": "1520599",
    "supplier_order_number": "ORD10658598",
    "invoice_date": "2026-08-17",
    "total_incl_tax": 95.2,
}


class TestItReadsWhatReceivingAlreadyProduced:
    def test_the_stored_extraction_is_used(self):
        db = _Db([_Doc("inv-1", HEADER)])
        assert EV.stored_header(db, "v-1", "inv-1") == HEADER

    def test_a_deleted_draft_is_not_evidence(self):
        db = _Db([_Doc("inv-1", HEADER, deleted=True)])
        assert EV.stored_header(db, "v-1", "inv-1") is None

    def test_a_reviewed_document_beats_a_bare_draft(self):
        """A draft nobody reviewed may hold a half-built extraction."""
        db = _Db(
            [
                _Doc("inv-1", {"invoice_number": "DRAFT"}, reviewed=False, updated=9),
                _Doc("inv-1", HEADER, reviewed=True, updated=1),
            ]
        )
        assert EV.stored_header(db, "v-1", "inv-1")["invoice_number"] == "IN11437546"

    def test_an_invoice_with_a_stored_header_costs_no_model_call(self, monkeypatch):
        """The whole point: 30 of the 67 already had one. Re-reading them is
        money and latency spent to learn what Norm already knew."""
        called = []
        monkeypatch.setattr(
            "app.services.invoice_extraction.extract_invoice_copies_parallel",
            lambda *a, **k: called.append(a) or [],
        )
        db = _Db([_Doc("inv-1", HEADER)])
        out = EV.copy_headers(db, None, None, "v-1", [{"id": "inv-1", "fileId": "f-1"}])
        assert called == []
        assert out["inv-1"]["_source"] == EV.SOURCE_STORED

    def test_without_one_it_extracts_with_the_suppliers_own_spec(self, monkeypatch):
        seen = {}

        def _instr(config_db, *, loaded_supplier=None, loaded_aliases=()):
            seen["supplier"] = loaded_supplier
            return "SPEC INSTRUCTIONS FOR " + str(loaded_supplier)

        monkeypatch.setattr(
            "app.services.invoice_extraction.pdf_instructions_for", _instr
        )
        monkeypatch.setattr(
            "app.services.invoice_extraction.extract_invoice_copies_parallel",
            lambda db, lh, requests, **k: [
                {"invoice_number": "X", "_req": r} for r in requests
            ],
        )
        db = _Db([])
        out = EV.copy_headers(
            db,
            None,
            None,
            "v-1",
            [{"id": "inv-2", "fileId": "f-2", "supplierName": "Service Foods"}],
        )
        assert seen["supplier"] == "Service Foods"
        assert out["inv-2"]["_req"]["instructions"].startswith("SPEC INSTRUCTIONS")
        assert out["inv-2"]["_source"] == EV.SOURCE_EXTRACTED

    def test_one_instruction_composition_per_supplier(self, monkeypatch):
        """Composing hits the config DB for the spec; a run is a few suppliers
        with many invoices each."""
        calls = []
        monkeypatch.setattr(
            "app.services.invoice_extraction.pdf_instructions_for",
            lambda c, **k: calls.append(k.get("loaded_supplier")) or "I",
        )
        monkeypatch.setattr(
            "app.services.invoice_extraction.extract_invoice_copies_parallel",
            lambda db, lh, requests, **k: [{} for _ in requests],
        )
        EV.copy_headers(
            _Db([]),
            None,
            None,
            "v-1",
            [
                {"id": "a", "fileId": "f", "supplierName": "Bidfood"},
                {"id": "b", "fileId": "f", "supplierName": "Bidfood"},
                {"id": "c", "fileId": "f", "supplierName": "Service Foods"},
            ],
        )
        assert calls == ["Bidfood", "Service Foods"]

    def test_no_copy_attached_is_reported_not_extracted(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.invoice_extraction.extract_invoice_copies_parallel",
            lambda *a, **k: pytest.fail("nothing to read"),
        )
        out = EV.copy_headers(_Db([]), None, None, "v-1", [{"id": "inv-3"}])
        assert "no invoice copy" in out["inv-3"]["error"]


class TestThePoVerdict:
    def test_our_po_matching_loaded_reconciles(self):
        """IN11437546 — the named regression. Loaded 1520599, copy carrying
        both 1520599 and ORD10658598. It failed as a mismatch."""
        assert EV.po_verdict("1520599", HEADER)[0] == "match"

    def test_loaded_holding_the_suppliers_number_still_matches(self):
        """Loaded's purchaseOrderNumber is often the SUPPLIER's number, not a
        Loaded order — both sides then name the same document."""
        verdict, note = EV.po_verdict("ORD10658598", HEADER)
        assert verdict == "match"
        assert "supplier's own order number" in note

    def test_two_real_numbers_still_do_not_reconcile(self):
        verdict, note = EV.po_verdict("1520600", HEADER)
        assert verdict == "mismatch"
        assert "1520599" in note  # names OUR number, not the decoy

    def test_a_po_only_loaded_lacks_is_reported_with_what_was_found(self):
        verdict, note = EV.po_verdict(None, HEADER)
        assert verdict == "absent"
        assert "1520599" in note

    def test_po_prefixes_and_spacing_do_not_matter(self):
        assert EV.po_verdict("PO# 1520599", HEADER)[0] == "match"

    def test_nothing_on_either_side(self):
        verdict, note = EV.po_verdict(None, {})
        assert verdict == "absent"
        assert "No PO number on the received invoice or the invoice copy" in note
