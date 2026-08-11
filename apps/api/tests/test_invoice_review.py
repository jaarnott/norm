"""invoice_review — the replica as the single suggestion engine.

These tests port the old consolidator gate scenarios onto the service:
working values from Loaded's draft, replica sidecar, unified suggestions
with explanations, blocking issues, the recorded accept trail, and the
autopilot policy (auto-accept everything, receive only when ready).
All reference data injected — no network, no LLM.
"""

from app.services import invoice_review as IR
from app.services.invoice_review import (
    apply_suggestion,
    auto_accept_all,
    compute_confidence,
    pair_lines,
    review_invoice,
    review_invoices,
)
from app.services.received_invoice import receive_request_from_doc

CATALOGUE = [
    {
        "id": "item-salmon",
        "name": "SALMON FILLET",
        "globalSalesTaxSortOrder": 1,
        "defaultBrandId": None,
        "suppliers": [
            {
                "supplierId": "sup-akaroa",
                "stockCode": "PBO0.7",
                "unitId": "u-kilo",
                "unitCost": 44.4,
                "brandId": "brand-akaroa",
                "defaultForSupplier": True,
                "description": "Salmon Fillet Skin On",
            }
        ],
    },
    {
        "id": "item-freight",
        "name": "FREIGHT - FOOD",
        "globalSalesTaxSortOrder": 1,
        "suppliers": [
            {"supplierId": "sup-akaroa", "stockCode": "FGT001", "unitId": "u-each"}
        ],
    },
]
UNITS = [
    {"id": "u-kilo", "name": "Kilo", "ratio": 1, "stockUnitType": "Weight"},
    {"id": "u-each", "name": "Each", "ratio": 1, "stockUnitType": "Count"},
    {"id": "u-5l", "name": "5L", "ratio": 5, "stockUnitType": "Volume"},
]
SUPPLIERS = [
    {"id": "sup-akaroa", "name": "Akaroa Salmon"},
    {"id": "sup-other", "name": "Totally Different Ltd"},
]
TAX = {0: 0.0, 1: 0.15}


def REFERENCE(**over):
    ref = dict(
        catalogue=CATALOGUE,
        units=UNITS,
        suppliers=SUPPLIERS,
        tax_rates=TAX,
        aliases_by_id={},
        received_feed=[],
        item_matcher=lambda *a, **k: {},
    )
    ref.update(over)
    return ref


def DETAIL(**over):
    det = {
        "id": "inv-1",
        "referenceNumber": "F100",
        "supplierName": "Akaroa Salmon",
        "linkedSupplierId": "sup-akaroa",
        "purchaseOrderNumber": "1520001",
        "linkedPurchaseOrderId": "po-1",
        "issuedAt": "2026-08-07T00:00:00",
        "total": 252.75,
        "subtotal": 219.78,
        "taxAmount": 32.97,
        "fileId": "file-1",
        "isReceived": False,
        "lines": [
            {
                "id": "ld-1",
                "code": "PBO0.7",
                "description": "Salmon Fillet Skin On",
                "unit": "Kilo",
                "linkedUnitId": "u-kilo",
                "linkedUnitRatio": 1,
                "quantityReceived": 4.95,
                "unitCostExclTax": 44.4,
                "totalCostExclTax": 219.78,
                "saleTaxRate": 0.15,
                "linkedItemId": "item-salmon",
                "linkedBrandId": "brand-akaroa",
                "itemType": "Default",
            }
        ],
    }
    det.update(over)
    return det


def EXTRACTION(**over):
    ext = {
        "document_type": "invoice",
        "invoice_number": "F100",
        "invoice_date": "7 Aug 2026",
        "supplier_name": "Akaroa Salmon NZ Ltd",
        "customer_purchase_order_number": None,
        "subtotal_ex_tax": 219.78,
        "tax_amount": 32.97,
        "total_incl_tax": 252.75,
        "lines": [
            {
                "code": "PBO0.7",
                "description": "Salmon Fillet Skin On",
                "quantity": 4.95,
                "unit": "Kilo",
                "unit_of_measure": "Kilo",
                "unit_price_ex_tax": 44.4,
                "line_total_ex_tax": 219.78,
            }
        ],
    }
    ext.update(over)
    return ext


def _review(detail=None, extraction=None, reference=None, lh=None, **kw):
    return review_invoice(
        None,
        None,
        "v-1",
        "inv-1",
        lh=lh if lh is not None else object(),
        detail=detail if detail is not None else DETAIL(),
        extraction=extraction if extraction is not None else EXTRACTION(),
        reference=reference if reference is not None else REFERENCE(),
        **kw,
    )


def _sugg_ids(data):
    return {s["id"] for s in data.get("suggestions") or []}


def _issue_codes(data):
    return {i["code"] for i in data.get("issues") or []}


class TestCleanInvoice:
    def test_agreement_yields_no_suggestions_and_ready(self):
        data = _review()
        assert data["doc_schema"] == "replica_v1"
        assert data["suggestions"] == []
        assert data["issues"] == []
        assert data["confidence"] == "ready"
        # sidecars present for the X-ray and future re-pairing
        assert data["replica"]["replica"] is True
        assert data["extracted_snapshot"]["header"]["invoice_number"] == "F100"
        assert data["loaded_snapshot"]["header"]["reference_number"] == "F100"
        # cache stamp: the review ran against this exact Loaded state
        assert data["reviewed_at"]
        assert (
            data["reviewed_invoice_fingerprint"] == data["loaded_invoice_fingerprint"]
        )

    def test_working_values_are_loadeds_draft(self):
        # The doc opens decorated with LOADED's data — the copy's values ride
        # as suggestions, never as silent replacements.
        det = DETAIL()
        det["lines"][0]["quantityReceived"] = 5.0
        data = _review(detail=det)
        assert data["lines"][0]["quantity_received"] == 5.0  # Loaded's value


class TestLineSuggestions:
    def test_quantity_diff_becomes_suggestion_with_explanation(self):
        det = DETAIL()
        det["lines"][0]["quantityReceived"] = 5.0
        data = _review(detail=det)
        s = next(s for s in data["suggestions"] if s["field"] == "quantity_received")
        assert s["line_id"] == "ld-1"
        assert s["current"] == 5.0 and s["proposed"] == 4.95
        assert "the copy bills quantity 4.95" in s["explanation"]
        assert s["apply"] == {"quantity_received": 4.95}
        # suggestions never block
        assert data["confidence"] == "ready"

    def test_cost_diff_becomes_suggestion(self):
        det = DETAIL()
        det["lines"][0]["unitCostExclTax"] = 40.0
        data = _review(detail=det)
        s = next(s for s in data["suggestions"] if s["field"] == "unit_cost")
        assert s["proposed"] == 44.4 and "prices" in s["explanation"]

    def test_unit_diff_proposes_the_replica_unit(self):
        # Loaded read "5L" off the paper; the variant (and the copy) say Kilo.
        det = DETAIL()
        det["lines"][0].update({"unit": "5L", "linkedUnitId": "u-5l"})
        data = _review(detail=det)
        s = next(s for s in data["suggestions"] if s["field"] == "unit")
        assert s["apply"] == {
            "unit": "Kilo",
            "linked_unit_id": "u-kilo",
            "unit_ratio": 1,
        }

    def test_an_unlinked_line_adopts_loadeds_own_code_match(self):
        # Loaded's API says linkedItemId null; its SCREEN resolves the item
        # from the supplier code and shows SALMON FILLET. The draft therefore
        # opens there too — and raises NO suggestion, because there is nothing
        # to disagree about (Angus Meats 1010951: two BONES lines were each
        # proposing the very item Loaded already displays).
        det = DETAIL()
        det["lines"][0]["linkedItemId"] = None
        data = _review(detail=det)
        ln = data["lines"][0]
        assert ln["linked_item_id"] == "item-salmon"
        assert ln["item_name"] == "SALMON FILLET"
        assert not [s for s in data["suggestions"] if s["field"] == "linked_item_id"]
        assert "item_unmatched" not in _issue_codes(data)
        assert data["confidence"] == "ready"
        # The mirror keeps Loaded's literal payload: unlinked, resolved for
        # display only. Seeding the working line must never edit it.
        assert data["loaded_snapshot"]["lines"][0]["linked_item_id"] is None
        assert data["loaded_snapshot"]["lines"][0]["item_name"] == "SALMON FILLET"

    def test_the_printed_description_survives_the_seed(self):
        # The replica's pairing, item matching and create-item prefill all key
        # off the supplier's printed text — the resolved name rides on
        # item_name, exactly as attach_item_names does it.
        det = DETAIL()
        det["lines"][0].update({"linkedItemId": None, "description": "Salmon Fil SKIN"})
        data = _review(detail=det)
        assert data["lines"][0]["description"] == "Salmon Fil SKIN"

    def test_a_different_match_is_a_suggestion_against_loadeds(self):
        # Loaded links the line to the wrong item; the copy resolves to
        # another. Before the draft opened on Loaded's resolution this case
        # was SILENT (the suggestion only fired for unlinked lines), which
        # would have let every bad code match through unchallenged.
        det = DETAIL()
        det["lines"][0]["linkedItemId"] = "item-freight"
        data = _review(detail=det)
        s = next(s for s in data["suggestions"] if s["field"] == "linked_item_id")
        assert s["current"] == "FREIGHT - FOOD"
        assert s["apply"]["linked_item_id"] == "item-salmon"
        assert "Loaded has 'FREIGHT - FOOD'" in s["explanation"]

    def test_an_unresolvable_code_still_asks_to_link(self):
        det = DETAIL()
        det["lines"][0].update({"linkedItemId": None, "code": "NOT-A-CODE"})
        data = _review(detail=det)
        s = next(s for s in data["suggestions"] if s["field"] == "linked_item_id")
        assert s["current"] is None
        assert s["apply"]["linked_item_id"] == "item-salmon"

    def test_apply_suggestion_recomputes_line_total(self):
        det = DETAIL()
        det["lines"][0]["quantityReceived"] = 5.0
        data = _review(detail=det)
        s = next(s for s in data["suggestions"] if s["field"] == "quantity_received")
        before = apply_suggestion(data, s)
        assert data["lines"][0]["quantity_received"] == 4.95
        assert data["lines"][0]["total_cost"] == round(4.95 * 44.4, 4)
        assert before["quantity_received"] == 5.0  # the undo payload


class TestCoverage:
    def test_loaded_zero_line_not_on_copy_gets_strike_only(self):
        det = DETAIL()
        det["lines"].append(
            {
                "id": "ld-9",
                "code": "GHOST",
                "description": "Phantom line",
                "quantityReceived": 1,
                "unitCostExclTax": 0.0,
                "totalCostExclTax": 0.0,
            }
        )
        data = _review(detail=det)
        s = next(s for s in data["suggestions"] if s["kind"] == "strike")
        assert s["line_id"] == "ld-9" and s["apply"] == {"struck": True}
        assert "loaded_line_not_on_copy" not in _issue_codes(data)
        assert data["confidence"] == "ready"

    def test_loaded_money_line_not_on_copy_blocks(self):
        det = DETAIL()
        det["lines"].append(
            {
                "id": "ld-9",
                "code": "GHOST",
                "description": "Phantom line",
                "quantityReceived": 1,
                "unitCostExclTax": 12.0,
                "totalCostExclTax": 12.0,
            }
        )
        data = _review(detail=det)
        issue = next(
            i for i in data["issues"] if i["code"] == "loaded_line_not_on_copy"
        )
        assert issue["blocking"] is True and issue["line_id"] == "ld-9"
        assert data["confidence"] == "needs_review"
        # striking the line clears it (the clears_when predicate)
        strike = next(s for s in data["suggestions"] if s["kind"] == "strike")
        apply_suggestion(data, strike)
        assert compute_confidence(data) == "ready"

    def test_copy_line_missing_in_loaded_becomes_add_line(self):
        ext = EXTRACTION()
        ext["lines"].append(
            {
                "code": "FGT001",
                "description": "Freight",
                "quantity": 1,
                "unit": None,
                "unit_of_measure": None,
                "unit_price_ex_tax": 9.5,
                "line_total_ex_tax": 9.5,
            }
        )
        ext["subtotal_ex_tax"] = 229.28
        ext["tax_amount"] = 34.39
        ext["total_incl_tax"] = 263.67
        data = _review(extraction=ext)
        s = next(s for s in data["suggestions"] if s["kind"] == "add_line")
        assert s["payload"]["code"] == "FGT001"
        assert s["payload"]["linked_item_id"] == "item-freight"
        assert "Loaded's draft has no such line" in s["explanation"]
        # accepting appends the line; the receive request then carries it as
        # an append (synthetic id, code+item present — do_receive's contract)
        apply_suggestion(data, s)
        added = data["lines"][-1]
        assert str(added["id"]).startswith("rep-")
        req = receive_request_from_doc(data, "v-1", "inv-1")
        assert any(
            ln["id"] == added["id"] and ln["code"] == "FGT001" for ln in req.lines
        )

    def test_ambiguous_pairing_blocks_instead_of_double_adding(self):
        # Two copy lines both claim the single Loaded salmon line.
        ext = EXTRACTION()
        ext["lines"].append(dict(ext["lines"][0], line_total_ex_tax=100.0))
        data = _review(extraction=ext)
        assert "ambiguous_pairing" in _issue_codes(data)
        assert not [s for s in data["suggestions"] if s["kind"] == "add_line"]
        assert data["confidence"] == "needs_review"


class TestHeaderSuggestions:
    def test_reference_and_date_and_total_diffs(self):
        det = DETAIL(referenceNumber="WRONG-1", issuedAt=None, total=250.00)
        data = _review(detail=det)
        by_field = {s["field"]: s for s in data["suggestions"]}
        assert by_field["reference_number"]["proposed"] == "F100"
        assert by_field["issued_at"]["proposed"] == "2026-08-07"
        assert by_field["total"]["proposed"] == 252.75
        assert "the copy's total is 252.75" in by_field["total"]["explanation"]

    def test_supplier_fill_when_loaded_has_none(self):
        det = DETAIL(linkedSupplierId=None, supplierName=None)
        data = _review(detail=det)
        s = next(s for s in data["suggestions"] if s["kind"] == "supplier")
        assert s["apply"]["linked_supplier_id"] == "sup-akaroa"
        # supplier_unresolved must NOT fire — the replica resolved it; the
        # working doc just needs the link applied.
        issue = next(
            (i for i in data["issues"] if i["code"] == "supplier_unresolved"), None
        )
        assert issue is None
        # and accepting the suggestion keeps the doc ready
        apply_suggestion(data, s)
        assert compute_confidence(data) == "ready"


class TestBlockingIssues:
    def test_statement_blocks(self):
        data = _review(extraction=EXTRACTION(document_type="statement"))
        issue = next(i for i in data["issues"] if i["code"] == "not_an_invoice")
        assert issue["blocking"] is True
        assert data["confidence"] == "needs_review"

    def test_duplicate_carries_delete_suggestion(self):
        feed = [
            {
                "id": "inv-old",
                "type": "Invoice",
                "invoiceNumber": "F100",
                "supplierName": "Akaroa Salmon",
                "receivedAt": "2026-07-30T00:00:00",
                "fileId": "file-9",
            }
        ]
        data = _review(reference=REFERENCE(received_feed=feed))
        assert "duplicate_invoice" in _issue_codes(data)
        s = next(s for s in data["suggestions"] if s["kind"] == "delete_invoice")
        assert s["payload"]["duplicate_of_invoice_id"] == "inv-old"
        assert s["payload"]["type"] == "delete_invoice"
        # a delete is a Loaded write — never applied locally, never auto
        assert apply_suggestion(data, s) is None

    def test_no_copy_attached_blocks(self):
        data = _review(detail=DETAIL(fileId=None))
        assert "no_copy_attached" in _issue_codes(data)
        assert data["confidence"] == "needs_review"
        assert "replica" not in data

    def test_unreadable_copy_blocks(self):
        data = _review(extraction={"error": "LLM down"})
        issue = next(i for i in data["issues"] if i["code"] == "copy_unreadable")
        assert "LLM down" in issue["message"]
        assert data["confidence"] == "needs_review"

    def test_unit_missing_is_not_cleared_by_loadeds_own_value(self):
        # The copy's unit is unreadable and there is no variant unit — Loaded's
        # line-level unit is Loaded's OCR of the same paper and must NOT clear
        # the issue; only an explicit confirm/dismiss does.
        ext = EXTRACTION()
        ext["lines"] = [
            {
                "code": "ZZZ9",
                "description": "Mystery Product",
                "quantity": 1,
                "unit": None,
                "unit_of_measure": None,
                "unit_unrecognisable": True,
                "unit_price_ex_tax": 219.78,
                "line_total_ex_tax": 219.78,
            }
        ]
        det = DETAIL()
        det["lines"][0].update(
            {
                "code": "ZZZ9",
                "description": "Mystery Product",
                "linkedItemId": None,
                "linkedUnitId": "u-5l",  # Loaded's own OCR guess
                "unit": "5L",
            }
        )
        data = _review(detail=det, extraction=ext)
        unit_issue = next(i for i in data["issues"] if i["code"] == "unit_missing")
        # remapped onto the paired working line
        assert unit_issue["line_id"] == "ld-1"
        assert "clears_when" not in unit_issue
        assert data["confidence"] == "needs_review"
        # a recorded dismissal (the human checked) clears it
        data["suggestion_actions"].append(
            {"suggestion_id": unit_issue["id"], "action": "dismissed", "by": "user"}
        )
        # item_unmatched also fired; it clears via linking (clears_when)
        item_issue = next(i for i in data["issues"] if i["code"] == "item_unmatched")
        assert item_issue["clears_when"]["field"] == "linked_item_id"
        data["lines"][0]["linked_item_id"] = "item-salmon"
        # unit_unconfirmed needs its own confirm too
        confirm = next(i for i in data["issues"] if i["code"] == "unit_unconfirmed")
        data["suggestion_actions"].append(
            {"suggestion_id": confirm["id"], "action": "dismissed", "by": "user"}
        )
        assert compute_confidence(data) == "ready"


class TestPoPolicy:
    def test_no_po_blocks_under_the_batch_default(self):
        det = DETAIL(linkedPurchaseOrderId=None, purchaseOrderNumber=None)
        data = _review(detail=det)
        issue = next(i for i in data["issues"] if i["code"] == "po_missing")
        assert issue["blocking"] is True
        assert data["confidence"] == "needs_review"
        # linking an order clears it (clears_when)
        data["linked_purchase_order_id"] = "po-9"
        assert compute_confidence(data) == "ready"

    def test_interactive_review_gets_a_note_not_a_block(self):
        det = DETAIL(linkedPurchaseOrderId=None, purchaseOrderNumber=None)
        data = _review(detail=det, require_valid_po=False)
        issue = next(i for i in data["issues"] if i["code"] == "po_missing")
        assert issue["blocking"] is False
        assert data["confidence"] == "ready"

    def test_linked_po_never_flags(self):
        data = _review()
        assert "po_missing" not in _issue_codes(data)


class TestAutoAccept:
    def test_every_action_is_recorded_with_actor_norm(self):
        det = DETAIL(referenceNumber="WRONG-1")
        det["lines"][0]["quantityReceived"] = 5.0
        data = _review(detail=det)
        n = auto_accept_all(data, actor="norm")
        assert n == len(data["suggestions"]) == 2
        assert data["lines"][0]["quantity_received"] == 4.95
        assert data["reference_number"] == "F100"
        actions = data["suggestion_actions"]
        assert len(actions) == 2
        assert all(a["by"] == "norm" and a["action"] == "accepted" for a in actions)
        assert all(a["at"] and "before" in a for a in actions)


class TestReceiveRequestFromDoc:
    def test_full_request_shape(self):
        data = _review()
        # simulate an accepted unit change so variant_updates derive
        ln = data["lines"][0]
        ln["linked_unit_id"] = "u-each"
        ln["unit"] = "Each"
        data["notes"] = "checked"
        req = receive_request_from_doc(data, "v-1", "inv-1")
        assert req.venue_id == "v-1" and req.invoice_id == "inv-1"
        assert req.receive is True
        assert req.reference_number == "F100"
        assert req.total == 252.75 and req.subtotal == 219.78
        assert req.linked_supplier_id == "sup-akaroa"
        assert req.notes == "checked"
        line = req.lines[0]
        assert line["id"] == "ld-1" and line["struck"] is False
        assert line["total_cost"] == round(4.95 * 44.4, 4)
        # the unit changed from its born value → the variant update derives
        assert req.variant_updates == [
            {
                "linked_item_id": "item-salmon",
                "line_code": "PBO0.7",
                "unit_id": "u-each",
            }
        ]

    def test_split_reference_written_without_link(self):
        data = _review(detail=DETAIL(linkedPurchaseOrderId=None))
        data["split_po_id"] = "po-7"
        data["split_sibling_invoice_id"] = "inv-sib"
        data["purchase_order_number"] = "1520001"
        req = receive_request_from_doc(data, "v-1", "inv-1")
        assert req.purchase_order_number == "1520001"
        assert req.linked_purchase_order_id is None
        assert req.split_po_id == "po-7"

    def test_unlink_flag(self):
        data = _review()
        data["po_unlinked"] = True
        data["linked_purchase_order_id"] = None
        req = receive_request_from_doc(data, "v-1", "inv-1")
        assert req.unlink_purchase_order is True


class _BatchLh:
    """Scriptable Loaded client for review_invoices."""

    def __init__(self, details):
        self.details = details

    def invoice(self, iid):
        return self.details[iid]

    def get(self, path):
        if "/invoices?" in path:
            return [
                {"id": iid, "isReceived": False, "createdAt": "2026-08-01"}
                for iid in self.details
            ]
        return []


class TestBatchModes:
    def _run(self, monkeypatch, details, extractions, mode, received):
        lh = _BatchLh(details)
        monkeypatch.setattr(IR, "LoadedInvoiceClient", lambda db, cdb, vid: lh)
        monkeypatch.setattr(
            IR,
            "extract_invoice_copies_parallel",
            lambda db, lh_, reqs: [extractions[i] for i in range(len(reqs))],
        )
        monkeypatch.setattr(
            IR, "extraction_instructions", lambda cdb, lh_, det: "INSTR"
        )
        monkeypatch.setattr(
            "app.services.spec_dojo.prefetch_replica_reference",
            lambda db, cdb, vid: REFERENCE(),
        )
        monkeypatch.setattr(
            IR,
            "do_receive",
            lambda lh_, req: received.append(req) or {"received": True},
        )
        monkeypatch.setattr(IR, "invalidate_conflicting_drafts", lambda *a, **k: None)
        return review_invoices(None, None, "v-1", mode=mode)

    def test_autopilot_accepts_and_receives_despite_diffs(self, monkeypatch):
        # "Trust the replica now": a qty diff never blocks autopilot — it is
        # auto-accepted (recorded) and the REPLICA's value is received.
        det = DETAIL()
        det["lines"][0]["quantityReceived"] = 5.0
        received = []
        out = self._run(
            monkeypatch, {"inv-1": det}, [EXTRACTION()], "autopilot", received
        )
        assert len(received) == 1
        assert received[0].lines[0]["quantity_received"] == 4.95
        assert out["received"][0]["outcome"] == "received"
        card_less = out["cards"]
        assert card_less == []  # received cleanly → no card
        # the record shows what Norm changed unattended
        assert out["verdicts"][0]["suggestions"] == 1

    def test_autopilot_skips_blocking_issue(self, monkeypatch):
        det = DETAIL(fileId=None)
        received = []
        out = self._run(monkeypatch, {"inv-1": det}, [], "autopilot", received)
        assert received == []
        assert out["skipped"][0]["outcome"] == "needs review"
        assert out["cards"]  # surfaced for the human

    def test_approve_fixes_receives_only_untouched_ready(self, monkeypatch):
        # agreement → receive; any suggestion → card
        received = []
        out = self._run(
            monkeypatch, {"inv-1": DETAIL()}, [EXTRACTION()], "approve_fixes", received
        )
        assert len(received) == 1 and out["received"]

        det = DETAIL()
        det["lines"][0]["quantityReceived"] = 5.0
        received2 = []
        out2 = self._run(
            monkeypatch, {"inv-1": det}, [EXTRACTION()], "approve_fixes", received2
        )
        assert received2 == [] and out2["cards"]

    def test_approve_all_never_receives(self, monkeypatch):
        received = []
        out = self._run(
            monkeypatch, {"inv-1": DETAIL()}, [EXTRACTION()], "approve_all", received
        )
        assert received == []
        assert out["skipped"][0]["outcome"] == "ready to receive — awaiting approval"


class TestPairLines:
    def test_item_then_code_then_description(self):
        reps = [
            {"id": "rep-0", "linked_item_id": "i-1", "code": None, "description": "A"},
            {"id": "rep-1", "linked_item_id": None, "code": "C2", "description": "B"},
            {
                "id": "rep-2",
                "linked_item_id": None,
                "code": None,
                "description": "Chicken Breast",
            },
        ]
        docs = [
            {"id": "ld-a", "linked_item_id": "i-1", "code": "X", "description": "?"},
            {"id": "ld-b", "linked_item_id": None, "code": "c2", "description": "?"},
            {
                "id": "ld-c",
                "linked_item_id": None,
                "code": None,
                "description": "CHICKEN BREAST skin off",
            },
        ]
        pairs, ambiguous, unpaired_rep, unpaired_loaded = pair_lines(reps, docs)
        assert pairs == {"rep-0": "ld-a", "rep-1": "ld-b", "rep-2": "ld-c"}
        assert not ambiguous and not unpaired_rep and not unpaired_loaded

    def test_second_claim_is_ambiguous_not_added(self):
        reps = [
            {"id": "rep-0", "linked_item_id": None, "code": "C1", "description": "A"},
            {"id": "rep-1", "linked_item_id": None, "code": "C1", "description": "A2"},
        ]
        docs = [
            {"id": "ld-a", "linked_item_id": None, "code": "C1", "description": "A"}
        ]
        pairs, ambiguous, unpaired_rep, _ = pair_lines(reps, docs)
        assert pairs == {"rep-0": "ld-a"}
        assert [r["id"] for r in ambiguous] == ["rep-1"]
        assert unpaired_rep == []


class TestSuggestionSummaryEmbedded:
    def test_summary_reads_confidence_issues_and_suggestions(self):
        from app.mcp.receive_display import _suggestion_summary

        det = DETAIL(fileId=None)
        data = _review(detail=det)
        s = _suggestion_summary(data)
        assert "needs review" in s and "1 blocking issue(s)" in s
        assert "no invoice copy is attached" in s

        clean = _review()
        s2 = _suggestion_summary(clean)
        assert "ready to receive" in s2

    def test_nothing_reviewed_returns_none(self):
        from app.mcp.receive_display import _suggestion_summary

        assert _suggestion_summary({"lines": []}) is None


class TestDerivedSubtotal:
    def test_subtotal_is_never_suggested(self):
        # subtotal is derived from the lines; only tax/discount/total are
        # header suggestions.
        det = DETAIL(subtotal=0.0, total=0.0, taxAmount=0.0)
        data = _review(detail=det)
        fields = {s.get("field") for s in data["suggestions"]}
        assert "subtotal" not in fields
        assert "total" in fields  # the printed total still rides as one

    def test_receive_writes_the_derived_subtotal(self):
        # Loaded's stored subtotal (0 here) is ignored — the receive carries
        # the sum of the non-struck lines.
        det = DETAIL(subtotal=0.0)
        data = _review(detail=det)
        req = receive_request_from_doc(data, "v-1", "inv-1")
        assert req.subtotal == round(4.95 * 44.4, 2)

    def test_struck_lines_leave_the_subtotal(self):
        data = _review()
        data["lines"][0]["struck"] = True
        data["lines"].append(
            {
                "id": "rep-9",
                "code": "X",
                "description": "Added",
                "quantity_received": 2,
                "unit_cost": 10.0,
                "total_cost": 20.0,
            }
        )
        req = receive_request_from_doc(data, "v-1", "inv-1")
        assert req.subtotal == 20.0


class TestDateSuggestionHygiene:
    def test_equal_dates_in_different_prints_never_suggest(self):
        # "Aug 08 2026" (no comma) must parse to the SAME day as Loaded's ISO
        # — a false diff here proposed a verbatim string into a date field.
        ext = EXTRACTION(invoice_date="Aug 08 2026")
        det = DETAIL(issuedAt="2026-08-08T00:00:00")
        data = _review(detail=det, extraction=ext)
        assert not any(s["field"] == "issued_at" for s in data["suggestions"])

    def test_unparseable_copy_date_is_never_proposed(self):
        ext = EXTRACTION(invoice_date="8th of Augustish")
        data = _review(extraction=ext)
        assert not any(s["field"] == "issued_at" for s in data["suggestions"])


class TestCreditNote:
    """A credit note is a receivable document that REVERSES stock and cost.

    User decision (2026-08-10): a fully-validated credit note auto-receives
    exactly like a fully-validated invoice, so the credit marker is
    informational and never gates. The hard guards live at receive
    (sign coherence) and in the replica (zero-quantity value credits).
    """

    CREDIT_EXT = dict(EXTRACTION(), document_type="credit_note")

    def _credit_detail(self):
        # Loaded's OCR reads a credit note as an ordinary positive draft —
        # which is exactly why every line will carry a sign-flip suggestion.
        det = DETAIL()
        det["linkedPurchaseOrderId"] = None
        det["purchaseOrderNumber"] = None
        return det

    def test_marked_and_negated_on_the_document(self):
        data = _review(detail=self._credit_detail(), extraction=self.CREDIT_EXT)
        assert data["is_credit_note"] is True
        assert data["replica"]["total"] == -252.75
        assert data["replica"]["lines"][0]["quantity_received"] == -4.95
        assert data["replica"]["lines"][0]["unit_cost"] == 44.4

    def test_the_credit_issue_never_blocks(self):
        data = _review(detail=self._credit_detail(), extraction=self.CREDIT_EXT)
        credit = next(i for i in data["issues"] if i["code"] == "credit_note")
        assert credit["blocking"] is False

    def test_no_purchase_order_is_demanded(self):
        # A credit legitimately has no PO of its own — po_missing would block
        # every credit note under the batch policy.
        data = _review(
            detail=self._credit_detail(),
            extraction=self.CREDIT_EXT,
            require_valid_po=True,
        )
        assert "po_missing" not in _issue_codes(data)

    def test_a_clean_credit_reaches_ready(self):
        # The signs disagree with Loaded's positive draft, so there are
        # suggestions — but nothing BLOCKS, which is what ready means.
        data = _review(detail=self._credit_detail(), extraction=self.CREDIT_EXT)
        assert data["confidence"] == "ready"

    def test_receive_request_carries_the_negatives(self):
        data = _review(detail=self._credit_detail(), extraction=self.CREDIT_EXT)
        for s in data["suggestions"]:
            apply_suggestion(data, s)
        req = receive_request_from_doc(data, "v-1", "inv-1")
        assert req.lines[0]["quantity_received"] == -4.95
        assert req.lines[0]["unit_cost"] == 44.4  # a price, never negative
        assert req.lines[0]["total_cost"] == round(-4.95 * 44.4, 4)
        assert req.subtotal == round(-4.95 * 44.4, 4)  # derived from the lines
        assert req.total == -252.75


class TestOrderBreaksAmbiguousCode:
    """One supplier code, three cuts, one printed description.

    Angus Meats sells BONES (STOCK), BONE MARROW 1 INCH and BONE MARROW -
    CANOE CUT under code BONES and prints "Beef Bones" on every line. Loaded's
    matcher takes the first in catalogue order, so a delivery against an order
    for 6 kg canoe cut + 14 kg 1-inch booked 20.58 kg to a third item
    (1010951, received 10 Aug 2026). The copy cannot break the tie — it says
    the same words on both lines — so the ORDER decides.
    """

    SUP = "sup-akaroa"
    CATALOGUE = [
        {
            "id": "item-bones",
            "name": "BONES (STOCK)",
            "globalSalesTaxSortOrder": 1,
            "suppliers": [
                {"supplierId": SUP, "stockCode": "BONES", "unitId": "u-kilo"}
            ],
        },
        {
            "id": "item-1inch",
            "name": "BONE MARROW 1 INCH",
            "globalSalesTaxSortOrder": 1,
            "suppliers": [
                {
                    "supplierId": SUP,
                    "stockCode": "BONES",
                    "unitId": "u-kilo",
                    "defaultForSupplier": True,
                }
            ],
        },
        {
            "id": "item-canoe",
            "name": "BONE MARROW - CANOE CUT",
            "globalSalesTaxSortOrder": 1,
            "suppliers": [
                {"supplierId": SUP, "stockCode": "BONES", "unitId": "u-kilo"}
            ],
        },
    ]
    PO = {
        "createdAt": "2026-08-10T09:39:54Z",
        "lines": [
            {
                "itemId": "item-canoe",
                "itemName": "BONE MARROW - CANOE CUT",
                "itemCode": None,
                "unitName": "Kilo",
                "quantityOrdered": 6,
                "unitCost": 6.39,
            },
            {
                "itemId": "item-1inch",
                "itemName": "BONE MARROW 1 INCH",
                "itemCode": "BONES",
                "unitName": "Kilo",
                "quantityOrdered": 14,
                "unitCost": 6.39,
            },
        ],
    }

    class _Lh:
        def __init__(self, po):
            self.po = po

        def get(self, path):  # noqa: ARG002
            return self.po

        def invoice(self, invoice_id):
            raise KeyError(invoice_id)

    def _line(self, lid, qty):
        return {
            "id": lid,
            "code": "BONES",
            "description": "Beef Bones",
            "unit": "KG",
            "linkedUnitId": None,
            "quantityReceived": qty,
            "unitCostExclTax": 6.39,
            "totalCostExclTax": round(qty * 6.39, 4),
            "saleTaxRate": 0.15,
            "linkedItemId": None,
            "itemType": "Default",
        }

    def _copy_line(self, qty):
        return {
            "code": "BONES",
            "description": "Beef Bones",
            "quantity": qty,
            "unit": "Kilo",
            "unit_of_measure": "Kilo",
            "unit_price_ex_tax": 6.39,
            "line_total_ex_tax": round(qty * 6.39, 4),
        }

    def _run(self, po=None, **det_over):
        det = DETAIL(lines=[self._line("ld-1", 6.43), self._line("ld-2", 14.15)])
        det.update(det_over)
        ext = EXTRACTION(lines=[self._copy_line(6.43), self._copy_line(14.15)])
        return _review(
            detail=det,
            extraction=ext,
            reference=REFERENCE(catalogue=self.CATALOGUE),
            lh=self._Lh(self.PO if po is None else po),
        )

    def _item_suggs(self, data):
        return [s for s in data["suggestions"] if s["field"] == "linked_item_id"]

    def test_each_line_takes_the_order_row_nearest_its_quantity(self):
        data = self._run()
        by_line = {s["line_id"]: s for s in self._item_suggs(data)}
        assert set(by_line) == {"ld-1", "ld-2"}
        # 6.43 arrived against the 6 ordered, 14.15 against the 14 — never
        # both against the same row.
        assert by_line["ld-1"]["apply"]["linked_item_id"] == "item-canoe"
        assert by_line["ld-2"]["apply"]["linked_item_id"] == "item-1inch"
        # and it says why, naming what Loaded picked
        assert "3 catalogue items" in by_line["ld-1"]["explanation"]
        assert "BONES (STOCK)" in by_line["ld-1"]["explanation"]
        assert by_line["ld-1"]["current"] == "BONES (STOCK)"

    def test_exactly_one_item_proposal_per_line(self):
        # The replica read the same ambiguous words; the order supersedes it.
        assert len(self._item_suggs(self._run())) == 2

    def test_accepting_links_the_ordered_items(self):
        data = self._run()
        for s in list(self._item_suggs(data)):
            apply_suggestion(data, s)
        assert [ln["linked_item_id"] for ln in data["lines"]] == [
            "item-canoe",
            "item-1inch",
        ]
        assert [ln["item_name"] for ln in data["lines"]] == [
            "BONE MARROW - CANOE CUT",
            "BONE MARROW 1 INCH",
        ]

    def test_the_mirror_still_shows_loadeds_own_match(self):
        # The X-ray is Loaded's truth, not Norm's opinion — it must keep
        # resolving by catalogue order however good the order's evidence is.
        snap = self._run()["loaded_snapshot"]["lines"]
        assert [ln["item_name"] for ln in snap] == ["BONES (STOCK)"] * 2

    def test_no_order_no_second_guessing(self):
        data = self._run(po=None, linkedPurchaseOrderId=None)
        assert self._item_suggs(data) == []

    def test_an_unambiguous_code_is_never_second_guessed(self):
        # The stock salmon invoice: PBO0.7 belongs to one item only, so the
        # order has no standing to rename it.
        data = _review(
            reference=REFERENCE(),
            lh=self._Lh(
                {
                    "createdAt": "2026-08-07",
                    "lines": [
                        {
                            "itemId": "item-freight",
                            "itemName": "FREIGHT - FOOD",
                            "itemCode": "PBO0.7",
                            "quantityOrdered": 4.95,
                        }
                    ],
                }
            ),
        )
        assert [s for s in data["suggestions"] if s["field"] == "linked_item_id"] == []

    def test_receive_sends_quantity_ordered_only_for_the_items_on_the_order(self):
        data = self._run()
        # As Loaded resolved it, both lines point at an item the order never
        # names — so nothing is claimed and Loaded is told nothing.
        req = receive_request_from_doc(data, "v-1", "inv-1")
        assert [ln["quantity_ordered"] for ln in req.lines] == [None, None]
        # Accept the order's items and the rows pair up, one each.
        for s in list(self._item_suggs(data)):
            apply_suggestion(data, s)
        req = receive_request_from_doc(data, "v-1", "inv-1")
        assert [ln["quantity_ordered"] for ln in req.lines] == [6, 14]
