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

        # Patch where it is USED: invoice_review binds pdf_instructions_for at
        # import, so patching invoice_extraction's copy rebinds nothing (and
        # leaks a stale fake into the module for every later test).
        monkeypatch.setattr("app.services.invoice_review.pdf_instructions_for", _instr)
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
            "app.services.invoice_review.pdf_instructions_for",
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


class _Lh:
    """Minimal LoadedHub stand-in: only the invoice-detail read matters here."""

    def __init__(self, notes_by_id):
        self.notes = notes_by_id
        self.fetched = []

    def invoice(self, invoice_id):
        self.fetched.append(invoice_id)
        if invoice_id not in self.notes:
            raise RuntimeError("boom")
        return {"id": invoice_id, "notes": self.notes[invoice_id]}


class TestSplitNotesAreReadOnlyWhereTheyCanHelp:
    """End to end: the note lives on the invoice DETAIL, which the received
    feed does not carry, so copy_headers has to go and get it."""

    def _docs(self, po):
        return _Db([_Doc("inv-1", {"customer_purchase_order_number": po})])

    def test_a_split_note_turns_absent_into_reconciled(self):
        lh = _Lh({"inv-1": "Split order: order 1521169 also covers IN11411819"})
        out = EV.copy_headers(
            self._docs("1521169"),
            None,
            lh,
            "v-1",
            [{"id": "inv-1", "purchaseOrderNumber": None}],
        )
        assert out["inv-1"]["_po_verdict"] == "match"
        assert "split delivery" in out["inv-1"]["_po_note"]

    def test_only_the_blocked_invoices_cost_a_detail_read(self):
        """A matched invoice must not pay for a note it cannot use — this runs
        over every unreconciled invoice in six venues every morning."""
        db = _Db(
            [
                _Doc("inv-1", {"customer_purchase_order_number": "1521169"}),
                _Doc("inv-2", {"customer_purchase_order_number": "1520599"}),
            ]
        )
        lh = _Lh({"inv-1": "Split order: order 1521169 also covers X"})
        EV.copy_headers(
            db,
            None,
            lh,
            "v-1",
            [
                {
                    "id": "inv-1",
                    "purchaseOrderNumber": None,
                },  # absent -> needs the note
                {"id": "inv-2", "purchaseOrderNumber": "1520599"},  # already a match
            ],
        )
        assert lh.fetched == ["inv-1"]

    def test_an_unreadable_note_leaves_the_verdict_alone(self):
        lh = _Lh({})  # every fetch raises
        out = EV.copy_headers(
            self._docs("1521169"),
            None,
            lh,
            "v-1",
            [{"id": "inv-1", "purchaseOrderNumber": None}],
        )
        assert out["inv-1"]["_po_verdict"] == "absent"


class _SplitLh:
    """`lh` for the split classifier: serves the sibling's detail and nothing
    else. resolve_po_id is a LOOKUP and is stubbed; `_sibling_doubled_up` is
    the RULE and runs for real, which is the part worth protecting."""

    def __init__(self, sibling):
        self.sibling = sibling

    def invoice(self, invoice_id):
        return self.sibling

    def get(self, path):
        return []


SIB_LINES = [
    {
        "code": "A1",
        "description": "Limes",
        "quantityReceived": 3.0,
        "unitCostExclTax": 7.42,
    }
]


class TestTheSplitClassifier:
    """Loaded is 1:1 PO<->invoice, so a split delivery leaves every invoice
    after the first with an empty PO field. 13 of 18 blocked invoices at
    Bessie & Engineers were splits (23 Aug 2026)."""

    def _run(self, monkeypatch, *, resolved, sibling, own_lines):
        monkeypatch.setattr(
            "app.services.received_invoice.resolve_po_id",
            lambda lh, number, supplier=None: resolved,
        )
        invoice = {"id": "inv-own", "linkedSupplierId": "sup-1", "lines": own_lines}
        header = {"customer_purchase_order_number": "1521169", "total_incl_tax": 295.09}
        return EV.split_verdict(_SplitLh(sibling), invoice, header)

    def test_an_unresolvable_number_is_not_a_split(self, monkeypatch):
        """Ocean's North prints 'Standing Order PO#631518146', which is no
        Loaded order at all. Nothing to explain, and nothing to reconcile on."""
        assert self._run(monkeypatch, resolved=None, sibling={}, own_lines=[]) is None

    def test_the_orders_own_invoice_is_not_a_split(self, monkeypatch):
        resolved = {
            "id": "po-1",
            "order_number": "1521169",
            "linked_invoice_id": "inv-own",
        }
        assert (
            self._run(monkeypatch, resolved=resolved, sibling={}, own_lines=[]) is None
        )

    def test_a_sibling_with_different_lines_is_a_split(self, monkeypatch):
        resolved = {
            "id": "po-1",
            "order_number": "1521169",
            "linked_invoice_id": "inv-sib",
        }
        sibling = {"referenceNumber": "IN11411819", "total": 175.47, "lines": SIB_LINES}
        own = [
            {
                "code": "B2",
                "description": "Lemons",
                "quantityReceived": 9.0,
                "unitCostExclTax": 2.0,
            }
        ]
        kind, data = self._run(
            monkeypatch, resolved=resolved, sibling=sibling, own_lines=own
        )
        assert kind == "split"
        assert data["order_number"] == "1521169"
        assert data["sibling_reference"] == "IN11411819"

    def test_an_identical_sibling_is_doubled_up_not_a_split(self, monkeypatch):
        """Same lines AND same total: the copy's number is bogus and this may
        be a duplicate invoice. Must never reconcile on that basis."""
        resolved = {
            "id": "po-1",
            "order_number": "1521169",
            "linked_invoice_id": "inv-sib",
        }
        sibling = {"referenceNumber": "IN11411819", "total": 295.09, "lines": SIB_LINES}
        own = [
            {
                "code": "A1",
                "description": "Limes",
                "quantityReceived": 3.0,
                "unitCostExclTax": 7.42,
            }
        ]
        kind, _ = self._run(
            monkeypatch, resolved=resolved, sibling=sibling, own_lines=own
        )
        assert kind == "doubled_up"


class TestSplitDetectionReachesTheVerdict:
    def _headers(self, monkeypatch, *, resolved, sibling, own_lines, notes=""):
        monkeypatch.setattr(
            "app.services.received_invoice.resolve_po_id",
            lambda lh, number, supplier=None: resolved,
        )

        class Lh(_SplitLh):
            def invoice(self, invoice_id):
                if invoice_id == "inv-1":
                    return {
                        "id": "inv-1",
                        "notes": notes,
                        "lines": own_lines,
                        "linkedSupplierId": "sup-1",
                    }
                return self.sibling

        db = _Db(
            [
                _Doc(
                    "inv-1",
                    {
                        "customer_purchase_order_number": "1521169",
                        "total_incl_tax": 295.09,
                    },
                )
            ]
        )
        return EV.copy_headers(
            db, None, Lh(sibling), "v-1", [{"id": "inv-1", "purchaseOrderNumber": None}]
        )

    def test_a_confirmed_split_reconciles_without_any_note(self, monkeypatch):
        out = self._headers(
            monkeypatch,
            resolved={
                "id": "po-1",
                "order_number": "1521169",
                "linked_invoice_id": "inv-sib",
            },
            sibling={
                "referenceNumber": "IN11411819",
                "total": 175.47,
                "lines": SIB_LINES,
            },
            own_lines=[
                {
                    "code": "B2",
                    "description": "Lemons",
                    "quantityReceived": 9.0,
                    "unitCostExclTax": 2.0,
                }
            ],
        )
        assert out["inv-1"]["_po_verdict"] == "match"
        assert "split delivery" in out["inv-1"]["_po_note"]
        assert out["inv-1"]["_split"]["kind"] == "split"

    def test_a_doubled_up_is_reported_but_never_reconciled(self, monkeypatch):
        out = self._headers(
            monkeypatch,
            resolved={
                "id": "po-1",
                "order_number": "1521169",
                "linked_invoice_id": "inv-sib",
            },
            sibling={
                "referenceNumber": "IN11411819",
                "total": 295.09,
                "lines": SIB_LINES,
            },
            own_lines=[
                {
                    "code": "A1",
                    "description": "Limes",
                    "quantityReceived": 3.0,
                    "unitCostExclTax": 7.42,
                }
            ],
        )
        assert out["inv-1"]["_po_verdict"] == "absent"  # NOT reconciled
        assert "possible duplicate" in out["inv-1"]["_po_note"]

    def test_a_number_that_is_no_loaded_order_stays_blocked(self, monkeypatch):
        out = self._headers(monkeypatch, resolved=None, sibling={}, own_lines=[])
        assert out["inv-1"]["_po_verdict"] == "absent"


class _WriteLh:
    def __init__(self, detail):
        self.detail = detail
        self.puts = []

    def invoice(self, invoice_id):
        return dict(self.detail)

    def request(self, method, path, body=None):
        self.puts.append((method, path, body))
        return body


class TestRecordingASplit:
    """The write half. Its whole design rests on one measured fact: a user's
    Save on a received invoice KEEPS the note and WIPES the PO reference
    (verified in the live Loaded UI, 25 Aug 2026). So the note is the record
    and the reference is only a courtesy."""

    BASE = {
        "id": "inv-1",
        "notes": None,
        "purchaseOrderNumber": None,
        "linkedPurchaseOrderId": None,
        "total": 295.09,
        "lines": [{"id": "l1"}],
    }

    def test_it_writes_the_note_and_the_reference(self):
        lh = _WriteLh(self.BASE)
        out = EV.record_split(lh, "inv-1", "1521169", "IN11411819")
        body = lh.puts[0][2]
        assert out == {"ok": True, "noted": True, "referenced": True}
        assert body["notes"] == "Split order: order 1521169 also covers IN11411819"
        assert body["purchaseOrderNumber"] == "1521169"

    def test_it_never_links_the_order(self):
        """Loaded is 1:1 — the link belongs to the sibling. Writing it here
        would steal it from the invoice that legitimately holds it."""
        lh = _WriteLh(self.BASE)
        EV.record_split(lh, "inv-1", "1521169", "IN11411819")
        assert lh.puts[0][2]["linkedPurchaseOrderId"] is None

    def test_it_disturbs_nothing_else(self):
        lh = _WriteLh(self.BASE)
        EV.record_split(lh, "inv-1", "1521169", "IN11411819")
        body = lh.puts[0][2]
        assert body["total"] == 295.09 and body["lines"] == [{"id": "l1"}]

    def test_it_is_idempotent(self):
        """A daily run must not rewrite the same invoice forever."""
        done = {
            **self.BASE,
            "notes": "Split order: order 1521169 also covers IN11411819",
            "purchaseOrderNumber": "1521169",
        }
        lh = _WriteLh(done)
        out = EV.record_split(lh, "inv-1", "1521169", "IN11411819")
        assert out == {"ok": True, "unchanged": True}
        assert lh.puts == []

    def test_a_wiped_reference_is_restored_without_duplicating_the_note(self):
        """The expected steady state: the user saved, the reference went, the
        note stayed."""
        lh = _WriteLh(
            {**self.BASE, "notes": "Split order: order 1521169 also covers IN11411819"}
        )
        out = EV.record_split(lh, "inv-1", "1521169", "IN11411819")
        assert out["referenced"] is True and out["noted"] is False
        assert lh.puts[0][2]["notes"].count("Split order") == 1

    def test_an_existing_human_note_is_kept(self):
        lh = _WriteLh({**self.BASE, "notes": "Driver left it out back"})
        EV.record_split(lh, "inv-1", "1521169", "IN11411819")
        notes = lh.puts[0][2]["notes"]
        assert notes.startswith("Driver left it out back")
        assert "Split order: order 1521169" in notes

    def test_the_note_it_writes_is_the_one_the_rule_reads(self):
        """Receiving writes this sentence too; both must parse."""
        lh = _WriteLh(self.BASE)
        EV.record_split(lh, "inv-1", "1521169", "IN11411819")
        assert EV.split_order_number(lh.puts[0][2]["notes"]) == "1521169"

    def test_an_unreadable_invoice_writes_nothing(self):
        lh = _WriteLh({})
        out = EV.record_split(lh, "inv-1", "1521169", "IN11411819")
        assert out["ok"] is False and lh.puts == []


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

    def test_a_split_delivery_reconciles_on_the_note(self):
        """The live case, Bessie & Engineers 23 Aug 2026: IN11410669 has an
        empty PO field because order 1521169 is linked to IN11411819, and
        receiving recorded exactly that. 13 of 18 blocked invoices there were
        splits, so this is the common shape, not an edge case."""
        header = {"customer_purchase_order_number": "1521169"}
        verdict, note = EV.po_verdict(
            None, header, "Split order: order 1521169 also covers IN11411819"
        )
        assert verdict == "match"
        assert "split delivery" in note and "1521169" in note

    def test_a_split_note_for_a_DIFFERENT_order_does_not_reconcile(self):
        """The note proves a split happened; the match proves it is THIS
        invoice's split. Without the second half, any invoice carrying any
        split note would reconcile against any copy."""
        header = {"customer_purchase_order_number": "1521169"}
        verdict, _ = EV.po_verdict(
            None, header, "Split order: order 9999999 also covers IN11411819"
        )
        assert verdict == "absent"

    def test_a_note_cannot_rescue_an_invoice_whose_po_loaded_holds(self):
        """The split route is only for an EMPTY Loaded PO. A genuine mismatch
        must never be talked round by a note."""
        verdict, _ = EV.po_verdict(
            "1520600", HEADER, "Split order: order 1520599 also covers X"
        )
        assert verdict == "mismatch"

    def test_the_suppliers_number_also_satisfies_the_split_match(self):
        header = {"supplier_order_number": "ORD10658598"}
        verdict, _ = EV.po_verdict(
            None, header, "Split order: order ORD10658598 also covers X"
        )
        assert verdict == "match"

    def test_unrelated_notes_change_nothing(self):
        for note in (
            "",
            None,
            "Delivered to back door",
            "Split order: also invoiced on X",
        ):
            assert EV.po_verdict(None, HEADER, note)[0] == "absent"

    def test_the_note_parser_reads_the_format_receiving_writes(self):
        # services/received_invoice.py: f"Split order: order {n} also covers {ref}"
        assert (
            EV.split_order_number("Split order: order 1521169 also covers IN11411819")
            == "1521169"
        )
        assert (
            EV.split_order_number("note\nSplit order: order PO#123 also covers Y")
            == "PO#123"
        )
        assert EV.split_order_number("Split order: also invoiced on IN123") is None
        assert EV.split_order_number(None) is None

    def test_nothing_on_either_side(self):
        verdict, note = EV.po_verdict(None, {})
        assert verdict == "absent"
        assert "No PO number on the received invoice or the invoice copy" in note


class TestItComposesInstructionsExactlyLikeReceiving:
    """The Kaans regression, pinned.

    For three weeks every Kaans invoice failed on "no PO number" while the
    receive card read it correctly off the same PDF. Reconciliation composed
    from Loaded's feed spelling alone — 'Kaans Catering' matches no spec, so it
    ran the generic prompt while receiving ran the one carrying that supplier's
    'External Document No.' rule.

    Instructions are cache-key material, which made it self-sealing: with the
    spec absent from the key, fixing the spec in the dojo could not invalidate
    the stale extraction. So this pins BOTH halves — that the composer is
    receiving's own, and that the aliases reach it, because dropping either one
    silently restores the generic prompt and no assertion elsewhere notices.
    """

    class _AliasLh:
        def __init__(self):
            self.asked = []

        def get(self, path):
            self.asked.append(path)
            return [{"name": "CATERING SUPPLIES LTD"}, {"name": "Kaan's Catering"}]

        def invoice(self, invoice_id):  # pragma: no cover - not reached here
            raise AssertionError("no detail read is needed for a matched PO")

    def _run(self, monkeypatch, invoices, sensei=None):
        seen = {}

        def _fake_extract(_db, _lh, requests):
            seen["requests"] = requests
            return [{"invoice_number": "SI1"} for _ in requests]

        monkeypatch.setattr(
            "app.services.invoice_extraction.extract_invoice_copies_parallel",
            _fake_extract,
        )
        # The composed instructions ARE the cache key, so render the identity
        # and the aliases that went into them — that is what these assert on.
        monkeypatch.setattr(
            "app.services.invoice_review.extraction_instructions",
            lambda config_db, lh, detail, aliases=None: (
                "INSTR:" + repr(sorted(detail.items())) + repr(aliases)
            ),
        )
        # Training is stubbed unless a test is watching it: the real one would
        # reach a config DB these tests do not have.
        monkeypatch.setattr(
            "app.services.invoice_review._maybe_sensei",
            sensei if sensei is not None else (lambda *_a, **_k: False),
        )
        lh = self._AliasLh()
        EV.copy_headers(_Db([]), None, lh, "v-1", invoices)
        return seen.get("requests", []), lh

    def test_the_supplier_identity_reaches_the_composer(self, monkeypatch):
        """Name AND id: the id is what buys the aliases, and an alias is how a
        global spec is found under the account's own spelling."""
        requests, _lh = self._run(
            monkeypatch,
            [
                {
                    "id": "inv-1",
                    "fileId": "f-1",
                    "supplierName": "Kaans Catering",
                    "supplierId": "sup-9",
                }
            ],
        )
        assert "'supplierName', 'Kaans Catering'" in requests[0]["instructions"]
        assert "'linkedSupplierId', 'sup-9'" in requests[0]["instructions"]

    def test_two_suppliers_sharing_a_name_do_not_share_instructions(self, monkeypatch):
        """The cache key is per identity, not per printed name."""
        requests, _lh = self._run(
            monkeypatch,
            [
                {
                    "id": "i1",
                    "fileId": "f1",
                    "supplierName": "Service Foods",
                    "supplierId": "s1",
                },
                {
                    "id": "i2",
                    "fileId": "f2",
                    "supplierName": "Service Foods",
                    "supplierId": "s2",
                },
            ],
        )
        assert requests[0]["instructions"] != requests[1]["instructions"]

    @staticmethod
    def _sensei_spy(calls, trained=True):
        """Records what the sensei was asked, and with which identity hints."""

        def _spy(_db, _cdb, _venue, invoice_id, supplier, *aliases):
            calls.append((invoice_id, supplier, list(aliases)))
            return trained

        return _spy

    def test_it_trains_a_spec_less_supplier_before_composing(self, monkeypatch):
        """A spec-less supplier is a spec-less supplier — sister venue or not.
        Training must land BEFORE the instructions are composed, because a
        fresh spec is part of this pass's cache key, not the next one's."""
        calls = []
        self._run(
            monkeypatch,
            [
                {
                    "id": "inv-1",
                    "fileId": "f-1",
                    "supplierName": "The Glass Goose",
                    "supplierId": "s-1",
                }
            ],
            sensei=self._sensei_spy(calls),
        )
        assert [c[1] for c in calls] == ["The Glass Goose"]

    def test_the_sensei_is_asked_with_the_same_aliases_extraction_uses(
        self, monkeypatch
    ):
        """THE bug: asked under the bare feed name, a supplier filed under a
        different spelling looks spec-less for ever, so the guard never fires
        and a working spec is re-analysed on every invoice."""
        calls = []
        self._run(
            monkeypatch,
            [
                {
                    "id": "inv-1",
                    "fileId": "f-1",
                    "supplierName": "Kaans Catering",
                    "supplierId": "s-1",
                }
            ],
            sensei=self._sensei_spy(calls),
        )
        assert calls[0][2] == ["CATERING SUPPLIES LTD", "Kaan's Catering"]

    def test_the_budget_is_spent_only_on_suppliers_it_trained(self, monkeypatch):
        """Suppliers that already have a spec cost nothing: `_maybe_sensei`
        returns False and the budget survives for one that needs it."""
        calls = []
        self._run(
            monkeypatch,
            [
                {"id": f"i{n}", "fileId": "f", "supplierName": f"S{n}", "supplierId": n}
                for n in range(EV.MAX_SENSEI_PER_RUN + 3)
            ],
            sensei=self._sensei_spy(calls, trained=False),
        )
        assert len(calls) == EV.MAX_SENSEI_PER_RUN + 3

    def test_a_run_meeting_many_new_suppliers_stops_at_the_budget(self, monkeypatch):
        """Training reads a PDF and calls the LLM. The rest wait for the next
        run rather than landing in one morning."""
        calls = []
        self._run(
            monkeypatch,
            [
                {"id": f"i{n}", "fileId": "f", "supplierName": f"S{n}", "supplierId": n}
                for n in range(EV.MAX_SENSEI_PER_RUN + 3)
            ],
            sensei=self._sensei_spy(calls, trained=True),
        )
        assert len(calls) == EV.MAX_SENSEI_PER_RUN

    def test_an_invoice_with_no_copy_is_never_sent_for_training(self, monkeypatch):
        """The sensei learns from the PDF; there is nothing to learn from."""
        calls = []
        self._run(
            monkeypatch,
            [{"id": "inv-1", "supplierName": "Nobody Ltd", "supplierId": "s-1"}],
            sensei=self._sensei_spy(calls),
        )
        assert calls == []
