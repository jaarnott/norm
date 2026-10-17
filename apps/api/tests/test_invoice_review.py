"""invoice_review — the replica as the single suggestion engine.

These tests port the old consolidator gate scenarios onto the service:
working values from Loaded's draft, replica sidecar, unified suggestions
with explanations, blocking issues, the recorded accept trail, and the
autopilot policy (auto-accept everything, receive only when ready).
All reference data injected — no network, no LLM.
"""

import pytest

from app.services import invoice_review as IR
from app.services import venue_autopilot as VA
from app.services.invoice_review import (
    apply_suggestion,
    autopilot_accept,
    compute_confidence,
    pair_lines,
    review_invoice,
    review_invoices,
)
from app.services.invoice_po_reference import project_po_reference
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


def _review(detail=None, extraction=None, reference=None, lh=None, db=None, **kw):
    return review_invoice(
        db,
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
        assert data["doc_schema"] == "replica_v2"
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


class TestLineSuggestions:
    def test_quantity_diff_becomes_suggestion_with_explanation(self):
        """Folded in: the working values are Loaded's draft, and applying the
        suggestion recomputes the line total and returns the undo payload."""
        det = DETAIL()
        det["lines"][0]["quantityReceived"] = 5.0
        data = _review(detail=det)
        # The doc opens decorated with LOADED's data — the copy's values ride
        # as suggestions, never as silent replacements.
        assert data["lines"][0]["quantity_received"] == 5.0  # Loaded's value
        s = next(s for s in data["suggestions"] if s["field"] == "quantity_received")
        assert s["line_id"] == "ld-1"
        assert s["current"] == 5.0 and s["proposed"] == 4.95
        assert "the invoice bills quantity 4.95" in s["explanation"]
        assert s["apply"] == {"quantity_received": 4.95}
        # As Loaded has it, 5 kg doesn't come to the invoice's total — Loaded
        # would refuse the receive — so the totals stand in the way until the
        # quantity is accepted.
        totals = next(i for i in data["issues"] if i["code"] == "totals_inconsistent")
        assert totals["fix"] == [s["id"]]
        assert data["confidence"] == "needs_review"
        before = apply_suggestion(data, s)
        assert data["lines"][0]["quantity_received"] == 4.95
        assert data["lines"][0]["total_cost"] == round(4.95 * 44.4, 4)
        assert before["quantity_received"] == 5.0  # the undo payload
        assert IR.compute_confidence(data) == "ready"

    def test_a_quantity_loaded_cannot_store_is_not_suggested(self):
        """Loaded keeps quantities to 2dp — measured across all 868 it holds in
        production, none finer. SI03448887 (26 Aug 2026) carried suggestions for
        0.565 and 0.216 against Loaded's 0.57 and 0.22: accept, PUT, Loaded
        rounds it back, the next open regenerates the suggestion. Permanently
        pending, and every receive read 'autopilot would have differed'."""
        det, ext = DETAIL(), EXTRACTION()
        det["lines"][0]["quantityReceived"] = 0.57
        ext["lines"][0]["quantity"] = 0.565
        data = _review(detail=det, extraction=ext)
        assert not [
            s for s in data["suggestions"] if s.get("field") == "quantity_received"
        ]

    def test_a_real_difference_still_is(self):
        """The precision rule must only silence sub-cent noise."""
        det, ext = DETAIL(), EXTRACTION()
        det["lines"][0]["quantityReceived"] = 2.0
        ext["lines"][0]["quantity"] = 3.0
        data = _review(detail=det, extraction=ext)
        s = next(s for s in data["suggestions"] if s["field"] == "quantity_received")
        assert s["proposed"] == 3.0

    def test_what_it_proposes_is_a_value_loaded_can_keep(self):
        """A rounded-away difference that is still real proposes the STORABLE
        number — applying 0.565 would be rounded to 0.57 by Loaded and the
        suggestion would return on the next open."""
        det, ext = DETAIL(), EXTRACTION()
        det["lines"][0]["quantityReceived"] = 1.0
        ext["lines"][0]["quantity"] = 0.565
        data = _review(detail=det, extraction=ext)
        s = next(s for s in data["suggestions"] if s["field"] == "quantity_received")
        assert s["proposed"] == 0.57
        assert s["apply"] == {"quantity_received": 0.57}

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

    def _sizeless_case(self, ask, unit=None):
        """A sizeless, unlinked line (the Trents Malfy shape): nothing on the
        page, in the catalogue or on a variant says the size — the batched
        resolver is the only voice, and it may only ever OFFER."""
        det = DETAIL(total=63.11, subtotal=54.88, taxAmount=8.23)
        det["lines"] = [
            {
                "id": "ld-1",
                "code": "4230513",
                "description": "MALFY GIN ROSA PINK GRAPEF",
                "unit": None,
                "linkedUnitId": None,
                "quantityReceived": 1.0,
                "unitCostExclTax": 54.88,
                "totalCostExclTax": 54.88,
                "saleTaxRate": 0.15,
                "linkedItemId": None,
                "itemType": "Default",
            }
        ]
        ext = EXTRACTION(
            subtotal_ex_tax=54.88,
            tax_amount=8.23,
            total_incl_tax=63.11,
            lines=[
                {
                    "code": "4230513",
                    "description": "MALFY GIN ROSA PINK GRAPEF",
                    "quantity": 1,
                    "unit": unit,
                    "unit_of_measure": None,
                    "unit_price_ex_tax": 54.88,
                    "line_total_ex_tax": 54.88,
                }
            ],
        )
        return _review(
            detail=det,
            extraction=ext,
            reference=REFERENCE(unit_resolver_ask=ask),
        )

    def _resolver_answer(self, **over):
        answer = {
            "size": "5 L",
            "confidence": "high",
            "why": "sibling lines print 5L at the same price",
        }
        answer.update(over)
        return lambda prompt: answer

    def test_resolver_pick_is_the_blockers_fix_behind_its_switch(self):
        # The review's wiring of the unit-blocker rule. Medium and low answers
        # are proposed the same way — Norm always proposes a fix (user, 11 Oct
        # 2026) — pinned on the rule itself in test_every_blocker_has_a_fix.py
        # (test_the_resolvers_answer_is_proposed_at_any_confidence).
        data = self._sizeless_case(self._resolver_answer())
        s = next(s for s in data["suggestions"] if s["field"] == "unit")
        assert s["apply"]["linked_unit_id"] == "u-5l"
        assert s["confidence"] == "high"
        assert "sibling lines" in s["explanation"]
        # the line itself is untouched — an offer, not a resolution
        assert data["lines"][0]["linked_unit_id"] is None
        issue = next(i for i in data["issues"] if i["code"] == "unit_missing")
        # ONE decision: the blocker points at the suggestion, and picking a
        # unit is what receive_without_unit authorises Norm to do alone.
        assert issue["fix"] == [s["id"]]
        assert s["gate"] == "receive_without_unit"
        assert s["sure"] is False

    def _copy_only_freight_case(self, confidence="high"):
        # Aitkens 173670: the freight lines exist only on the copy. Loaded's
        # draft has one line; the extraction bills two.
        det = DETAIL()
        ext = EXTRACTION(
            lines=list(EXTRACTION()["lines"])
            + [
                {
                    "code": None,
                    "description": "Freight",
                    "quantity": 1,
                    "unit": None,
                    "unit_of_measure": None,
                    "unit_price_ex_tax": 9.0,
                    "line_total_ex_tax": 9.0,
                }
            ],
            subtotal_ex_tax=228.78,
            tax_amount=34.32,
            total_incl_tax=263.10,
        )

        def ask(prompt):
            # Only the copy-only Freight line is asked about.
            assert "Freight" in prompt.split("Line:")[1]
            return {
                "size": "each",
                "confidence": confidence,
                "why": "a service charge with no physical size",
            }

        return _review(
            detail=det, extraction=ext, reference=REFERENCE(unit_resolver_ask=ask)
        )

    def test_a_copy_only_lines_pick_arrives_with_the_line(self):
        # A copy-only line never meets the paired-line unit walk, so the
        # resolver's decisive pick died as metadata: the added line arrived
        # unitless and its blocker demanded a decision the resolver had
        # already made (Aitkens 173670 freight, 19 Aug 2026). The pick is the
        # added line's own unit suggestion — the card's Accept on the add
        # lands both — and it stays behind receive_without_unit, because
        # adding a line is applied unattended and a unit pick is not.
        data = self._copy_only_freight_case()
        add = next(s for s in data["suggestions"] if s["kind"] == "add_line")
        unit = next(
            s
            for s in data["suggestions"]
            if s["field"] == "unit" and s["line_id"] == add["line_id"]
        )
        assert unit["apply"]["linked_unit_id"] == "u-each"
        assert unit["apply"]["unit"] == "Each"
        assert "service charge" in unit["explanation"]
        assert unit["gate"] == "receive_without_unit"
        issue = next(
            i
            for i in data["issues"]
            if i["code"] == "unit_missing" and i["line_id"] == add["line_id"]
        )
        assert issue["fix"] == [unit["id"]]
        # accepting both lands the line complete
        from app.services import invoice_review as IR2

        IR2.apply_suggestion(data, add)
        IR2.apply_suggestion(data, unit)
        added = next(
            ln for ln in data["lines"] if str(ln.get("id")) == str(add["line_id"])
        )
        assert added["linked_unit_id"] == "u-each"

    def test_a_medium_pick_never_rides_along(self):
        # add_line is auto-accepted wherever autopilot runs, and the gate
        # doctrine forbids acting on a "likely" — medium stays out.
        data = self._copy_only_freight_case(confidence="medium")
        s = next(s for s in data["suggestions"] if s["kind"] == "add_line")
        assert not s["payload"].get("linked_unit_id")
        assert "delivered as" not in s["explanation"]

    def test_charge_word_upgrade_replaces_the_each_suggestion(self):
        # The Trents shape proper: 'EA' printed → the replica resolves Each
        # (how the line is CHARGED); the resolver names the real pack. ONE
        # unit suggestion — the upgrade — not two competing ones, and no
        # blocker (Each is honest, just vague).
        data = self._sizeless_case(self._resolver_answer(), unit="EA")
        unit_suggs = [s for s in data["suggestions"] if s["field"] == "unit"]
        assert len(unit_suggs) == 1
        s = unit_suggs[0]
        assert s["apply"]["linked_unit_id"] == "u-5l"
        assert "is charged" in s["explanation"]
        assert s["confidence"] == "high"
        assert "unit_missing" not in _issue_codes(data)

    def test_without_a_resolver_answer_the_each_suggestion_stands(self):
        data = self._sizeless_case(lambda p: {}, unit="EA")
        s = next(s for s in data["suggestions"] if s["field"] == "unit")
        assert s["apply"]["linked_unit_id"] == "u-each"

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
        # ONE decision: the blocker points at the strike, which autopilot may
        # apply only with its switch on.
        strike = next(s for s in data["suggestions"] if s["kind"] == "strike")
        assert strike["apply"] == {"struck": True}
        assert strike["gate"] == "auto_strike_phantom_lines"
        assert issue["fix"] == [strike["id"]]
        assert issue["gate"] == "auto_strike_phantom_lines"
        # striking the line clears it (the clears_when predicate)
        ln = next(line for line in data["lines"] if line["id"] == "ld-9")
        ln["struck"] = True
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

    def test_a_second_line_of_the_same_product_is_added_not_blocked(self):
        """Two copy lines of one product, one in Loaded's draft: the copy is
        the truth, so the second is added. It used to block "reconcile by
        hand" and Autopilot could never pass it (Alsco, 16 Oct 2026)."""
        ext = EXTRACTION(
            subtotal_ex_tax=319.78, tax_amount=47.97, total_incl_tax=367.75
        )
        ext["lines"].append(
            dict(
                ext["lines"][0],
                quantity=1,
                unit_price_ex_tax=100.0,
                line_total_ex_tax=100.0,
            )
        )
        data = _review(extraction=ext)
        assert "ambiguous_pairing" not in _issue_codes(data)
        assert [s for s in data["suggestions"] if s["kind"] == "add_line"]


class TestHeaderSuggestions:
    def test_reference_and_date_and_total_diffs(self):
        det = DETAIL(referenceNumber="WRONG-1", issuedAt=None, total=250.00)
        data = _review(detail=det)
        by_field = {s["field"]: s for s in data["suggestions"]}
        assert by_field["reference_number"]["proposed"] == "F100"
        assert by_field["issued_at"]["proposed"] == "2026-08-07"
        assert by_field["total"]["proposed"] == 252.75
        assert "the invoice's total is 252.75" in by_field["total"]["explanation"]

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
        dup = next(i for i in data["issues"] if i["code"] == "duplicate_invoice")
        # The Replica's answer is "delete this draft" — the blocker's fix,
        # behind its own switch: autopilot's destructive writes each answer to
        # a separate toggle.
        delete = next(s for s in data["suggestions"] if s["kind"] == "delete_invoice")
        assert delete["payload"]["duplicate_of_invoice_id"] == "inv-old"
        assert delete["payload"]["type"] == "delete_invoice"
        assert delete["gate"] == "auto_delete_duplicates"
        assert dup["fix"] == [delete["id"]]

    def test_a_feed_invoice_is_reviewed_from_loadeds_own_data(self, monkeypatch):
        # Ordermentum sends Loaded the supplier's own data and no PDF (Sonoma
        # Baking at Applegum Cafe, 15 Oct 2026). Nothing to read, nothing to
        # delete: Loaded's figures stand in for the read.
        monkeypatch.setattr(
            IR,
            "extract_invoice_copy",
            lambda *a, **k: pytest.fail("a feed invoice has no copy to read"),
        )
        data = review_invoice(
            None,
            None,
            "v-1",
            "inv-1",
            lh=object(),
            # Ordermentum invoices carry no order number (OMI1107031).
            detail=DETAIL(fileId=None, source="Ordermentum", purchaseOrderNumber=None),
            extraction=None,
            reference=REFERENCE(),
        )
        assert "no_copy_attached" not in _issue_codes(data)
        assert not [s for s in data["suggestions"] if s["kind"] == "delete_invoice"]
        assert data["replica"]["replica"] is True
        assert data["extracted_snapshot"]["header"]["invoice_number"] == "F100"
        assert data["confidence"] == "ready"

    def test_no_copy_from_an_order_or_typed_in_is_still_offered_for_deletion(self):
        for source in ("Po", "Manual", None):
            data = _review(detail=DETAIL(fileId=None, source=source))
            assert "no_copy_attached" in _issue_codes(data), source

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


class TestOrdermentumDuplicates:
    """One Ordermentum delivery reaches Loaded as Ordermentum's copy (OMI…, no
    file) and the supplier's emailed invoice (which carries the order number,
    OMO…). Norm received both for 260 deliveries on 14–16 Oct 2026. Whichever
    is received first stays; the other is a duplicate, settled before a copy
    is read."""

    OM_ROW = {
        "id": "inv-om",
        "invoiceNumber": "OMI1072575",
        "fileId": None,
        "supplierId": "sup-akaroa",
        "supplierName": "Akaroa Salmon",
        "invoicedAt": "2026-08-06",
        "receivedAt": "2026-08-06T00:00:00",
        "total": 252.75,
    }
    COPY_ROW = {
        "id": "inv-copy",
        "invoiceNumber": "3693436",
        "purchaseOrderNumber": "OMO1073025",
        "fileId": "f-copy",
        "supplierId": "sup-akaroa",
        "supplierName": "Akaroa Salmon",
        "invoicedAt": "2026-08-08",
        "receivedAt": "2026-08-08T00:00:00",
        "total": 252.75,
    }

    def _no_read(self, monkeypatch):
        monkeypatch.setattr(
            IR,
            "extract_invoice_copy",
            lambda *a, **k: pytest.fail("a duplicate was read"),
        )

    def _assert_duplicate_of(self, data, twin_id):
        issue = next(i for i in data["issues"] if i["code"] == "duplicate_invoice")
        delete = next(s for s in data["suggestions"] if s["kind"] == "delete_invoice")
        assert issue["gate"] == delete["gate"] == VA.AUTO_DELETE_DUPLICATES
        assert delete["payload"]["reason"] == "duplicate"
        assert delete["payload"]["duplicate_of_invoice_id"] == twin_id
        assert issue["fix"] == [delete["id"]]
        assert "replica" not in data  # stopped before the review proper
        assert data["confidence"] == "needs_review"

    def test_ordermentums_copy_when_the_supplier_copy_is_received(self, monkeypatch):
        self._no_read(monkeypatch)
        data = _review(
            detail=DETAIL(
                fileId=None,
                source="Ordermentum",
                referenceNumber="OMI1072575",
                purchaseOrderNumber=None,
                issuedAt="2026-08-07",
            ),
            extraction=None,
            reference=REFERENCE(received_feed=[self.COPY_ROW]),
        )
        self._assert_duplicate_of(data, "inv-copy")
        assert "supplier's own invoice 3693436" in data["issues"][0]["message"]

    def test_the_supplier_copy_when_ordermentums_is_received(self, monkeypatch):
        # Loaded's draft already carries the order number: no read needed.
        self._no_read(monkeypatch)
        data = _review(
            detail=DETAIL(purchaseOrderNumber="OMO1073025", issuedAt="2026-08-07"),
            extraction=None,
            reference=REFERENCE(received_feed=[self.OM_ROW]),
        )
        self._assert_duplicate_of(data, "inv-om")
        assert (
            "Ordermentum's copy of this order (OMI1072575)"
            in (data["issues"][0]["message"])
        )

    def test_the_order_number_only_the_read_found_still_counts(self):
        # Loaded's draft has no order number; the copy prints "PO No. OMO…".
        data = _review(
            detail=DETAIL(purchaseOrderNumber=None),
            extraction=EXTRACTION(customer_purchase_order_number="OMO1073025"),
            reference=REFERENCE(received_feed=[self.OM_ROW]),
        )
        dup = next(i for i in data["issues"] if i["code"] == "duplicate_invoice")
        assert dup["data"]["duplicate_of_invoice_id"] == "inv-om"
        delete = next(s for s in data["suggestions"] if s["kind"] == "delete_invoice")
        assert delete["payload"]["reason"] == "duplicate"

    def test_no_twin_received_reviews_as_usual(self):
        data = _review(
            detail=DETAIL(purchaseOrderNumber="OMO1073025"),
            reference=REFERENCE(received_feed=[{**self.OM_ROW, "total": 99.0}]),
        )
        assert "duplicate_invoice" not in _issue_codes(data)
        assert data["replica"]["replica"] is True

    def test_an_ordinary_supplier_is_never_matched_on_amount_and_date(self):
        # The same order under a different number a day earlier — a standing
        # order, not a duplicate (Procal at Applegum, Bidfood at Cook Brothers).
        standing = {
            **self.COPY_ROW,
            "invoiceNumber": "F099",
            "purchaseOrderNumber": None,
            "invoicedAt": "2026-08-06",
        }
        data = _review(reference=REFERENCE(received_feed=[standing]))
        assert "duplicate_invoice" not in _issue_codes(data)

    def test_an_old_invoice_sees_its_twin_past_the_400_days(self):
        # The duplicate check read only the last 400 days, so Applegum's
        # backlog from June 2025 was received twice (16 Oct 2026).
        asked: list = []
        first = {
            **self.COPY_ROW,
            "id": "inv-old",
            "invoiceNumber": "F100",
            "purchaseOrderNumber": None,
            "invoicedAt": "2025-06-03",
        }

        class _Lh:
            def get(self, path):
                asked.append(path)
                return [first] if "from=2025-05-27" in path else []

        data = _review(
            detail=DETAIL(issuedAt="2025-06-03"),
            extraction=EXTRACTION(invoice_date="2025-06-03"),
            reference=REFERENCE(received_feed=[]),
            lh=_Lh(),
        )
        assert any("from=2025-05-27&to=2025-06-11" in p for p in asked)
        dup = next(i for i in data["issues"] if i["code"] == "duplicate_invoice")
        assert dup["data"]["duplicate_of_invoice_id"] == "inv-old"

    # -- the same invoice sent more than once, settled before reading --------

    RECEIVED_F100 = {
        "id": "inv-first",
        "invoiceNumber": "F100",
        "fileId": "f-first",
        "supplierId": "sup-akaroa",
        "supplierName": "Akaroa Salmon",
        "invoicedAt": "2026-08-07",
        "receivedAt": "2026-08-07T00:00:00",
        "total": 252.75,
    }

    def test_a_copy_matching_on_number_and_total_is_settled_without_reading(
        self, monkeypatch
    ):
        # PNM sent invoice 4166603 six times; each copy was read before five
        # were deleted (16 Oct 2026).
        self._no_read(monkeypatch)
        data = _review(
            detail=DETAIL(purchaseOrderNumber=None),
            extraction=None,
            reference=REFERENCE(received_feed=[self.RECEIVED_F100]),
        )
        self._assert_duplicate_of(data, "inv-first")
        assert "with the same total" in data["issues"][0]["message"]

    def test_anything_short_of_number_and_total_goes_to_the_read(self):
        cases = [
            # a different total: Loaded may have misread, so read it
            (DETAIL(purchaseOrderNumber=None, total=260.00), self.RECEIVED_F100),
            # a placeholder number isn't an invoice number
            (
                DETAIL(purchaseOrderNumber=None, referenceNumber="adj"),
                {**self.RECEIVED_F100, "invoiceNumber": "adj"},
            ),
            # a credit note that reprints the invoice's number credits it
            (DETAIL(purchaseOrderNumber=None, total=-252.75), self.RECEIVED_F100),
            # goods receipted on the order: the read path words that case
            (
                DETAIL(purchaseOrderNumber=None),
                {**self.RECEIVED_F100, "type": "PurchaseOrder"},
            ),
        ]
        for detail, row in cases:
            data = _review(detail=detail, reference=REFERENCE(received_feed=[row]))
            # the review went on to the copy (the replica is built from it)
            assert "replica" in data, (detail["referenceNumber"], detail["total"])
            assert "with the same total" not in str(data["issues"])


class TestPoPolicy:
    def test_no_po_blocks_under_the_batch_default(self):
        det = DETAIL(linkedPurchaseOrderId=None, purchaseOrderNumber=None)
        data = _review(detail=det)
        issue = next(i for i in data["issues"] if i["code"] == "po_missing")
        assert issue["blocking"] is True
        # the card names the toggle that would let autopilot past this
        assert issue["gate"] == "receive_without_po"
        # ...and offers Accept: a confirm — receive without an order, which
        # changes nothing and records the decision.
        confirm = next(s for s in data["suggestions"] if s["id"] in issue["fix"])
        assert confirm["kind"] == "confirm"
        assert confirm["gate"] == "receive_without_po"
        assert data["confidence"] == "needs_review"
        # linking an order clears it (clears_when)
        data["linked_purchase_order_id"] = "po-9"
        assert compute_confidence(data) == "ready"

    def test_the_card_tells_the_story_autopilot_acts_on(self):
        # No explicit policy → derived from the viewer's receive_without_po
        # switch. Gate off: the card shows blocked-from-auto-receive naming the
        # toggle — no more "worth knowing" while autopilot silently parks
        # (user report, 18 Aug 2026). Gate on: an honest note.
        det = DETAIL(linkedPurchaseOrderId=None, purchaseOrderNumber=None)
        data = _review(detail=det, settings={})
        issue = next(i for i in data["issues"] if i["code"] == "po_missing")
        assert issue["blocking"] is True
        assert issue["gate"] == "receive_without_po"

        data = _review(detail=det, settings={"receive_without_po": True})
        issue = next(i for i in data["issues"] if i["code"] == "po_missing")
        assert issue["blocking"] is False
        assert data["confidence"] == "ready"

    def test_an_unresolved_reference_is_one_row_not_two(self):
        # The copy references '1520518' and nothing in Loaded matches: the
        # replica's po_unresolved row tells that story, names the reference
        # and carries the Accept. A second po_missing row underneath it was
        # the same decision sold twice — two Accepts, one choice (user
        # report, 19 Aug 2026).
        det = DETAIL(linkedPurchaseOrderId=None, purchaseOrderNumber=None)
        ext = EXTRACTION(customer_purchase_order_number="1520518")
        data = _review(detail=det, extraction=ext)
        codes = _issue_codes(data)
        assert "po_unresolved" in codes
        assert "po_missing" not in codes
        # the surviving row still blocks and still carries the Accept
        issue = next(i for i in data["issues"] if i["code"] == "po_unresolved")
        assert issue["blocking"] is True
        assert issue["gate"] == "receive_without_po"
        confirm = next(s for s in data["suggestions"] if s["id"] in issue["fix"])
        assert confirm["kind"] == "confirm"


class TestAutoAccept:
    def test_every_action_is_recorded_with_actor_norm(self):
        det = DETAIL(referenceNumber="WRONG-1")
        det["lines"][0]["quantityReceived"] = 5.0
        data = _review(detail=det)
        assert len(data["suggestions"]) == 2
        autopilot_accept(None, None, "v-1", "inv-1", data, VA.settings_from(None))
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


class _VenueDb:
    """A stand-in session carrying the receiving policy a batch runs under.

    The PERSON's settings decide how far Norm may go (passed to
    review_invoices as ``settings``), so a batch test has to say which rung
    they are on — passing `mode=` alone can only ever lower it.
    """

    def __init__(self, settings):
        self.settings = settings

    def query(self, *_a):
        return self

    def filter(self, *_a, **_k):
        return self

    def first(self):
        return None


@pytest.fixture()
def batch(monkeypatch):
    """``review_invoices`` over a scripted Loaded: the wiring every batch test
    used to copy. ``mode`` is the caller's ceiling, as production passes it;
    the venue sits on ``venue_mode`` (default: the same rung). Extractions are
    read one per invoice in order, or ``EXTRACTION()`` for every invoice when
    none are given. Receives land in ``received``.
    """

    def run(
        details,
        extractions=None,
        mode="autopilot",
        received=None,
        *,
        venue_mode=None,
        gates=None,
        lh=None,
    ):
        db = _VenueDb({"mode": venue_mode or mode, **(gates or {})})
        client = lh if lh is not None else _BatchLh(details)
        monkeypatch.setattr(IR, "LoadedInvoiceClient", lambda db, cdb, vid: client)
        if extractions is None:
            read = lambda *a, **k: EXTRACTION()  # noqa: E731 — one-line stub
        else:
            reads = iter(extractions)  # one read per invoice, in order
            read = lambda *a, **k: next(reads)  # noqa: E731 — one-line stub
        monkeypatch.setattr(IR, "extract_invoice_copy", read)
        monkeypatch.setattr(
            IR, "extraction_instructions", lambda cdb, lh_, det, al=None: "INSTR"
        )
        monkeypatch.setattr(
            "app.services.spec_dojo.prefetch_replica_reference",
            lambda db, cdb, vid: REFERENCE(),
        )
        sink = received if received is not None else []
        monkeypatch.setattr(
            IR, "do_receive", lambda lh_, req: sink.append(req) or {"received": True}
        )
        monkeypatch.setattr(IR, "invalidate_conflicting_drafts", lambda *a, **k: None)
        return review_invoices(db, None, "v-1", mode=mode, settings=db.settings)

    return run


class TestBatchModes:
    def test_an_invoice_loaded_fails_to_return_is_reported_not_dropped(self, batch):
        """It vanished from received AND skipped — the count came up short and
        a bad invoice_id was an empty success (consolidator review, 1 Oct)."""

        class _Flaky(_BatchLh):
            def invoice(self, iid):
                if iid == "inv-bad":
                    raise RuntimeError("Loaded 502")
                return super().invoice(iid)

        out = batch(
            None,
            mode="autopilot",
            venue_mode="approve_all",
            lh=_Flaky({"inv-1": DETAIL(), "inv-bad": {}}),
        )
        bad = [v for v in out["skipped"] if v["invoice_id"] == "inv-bad"]
        assert bad and bad[0]["outcome"] == "could not fetch from Loaded"
        assert "Loaded 502" in bad[0]["reasons"][0]

    def test_the_applied_mode_is_returned(self, batch):
        """The venue's rung, lowered by the caller's ceiling — chat reported
        "unset" over a run that received 50 invoices (6 Sep 2026)."""
        out = batch({"inv-1": DETAIL()}, [EXTRACTION()], "autopilot")
        assert out["mode"] == "autopilot"
        out = batch({}, mode="approve_all", venue_mode="autopilot")
        assert out["mode"] == "approve_all"

    def test_autopilot_accepts_and_receives_despite_diffs(self, batch):
        # "Trust the replica now": a qty diff never blocks autopilot — it is
        # auto-accepted (recorded) and the REPLICA's value is received.
        det = DETAIL()
        det["lines"][0]["quantityReceived"] = 5.0
        received = []
        out = batch({"inv-1": det}, [EXTRACTION()], "autopilot", received)
        assert len(received) == 1
        assert received[0].lines[0]["quantity_received"] == 4.95
        assert out["received"][0]["outcome"] == "received"
        card_less = out["cards"]
        assert card_less == []  # received cleanly → no card
        # the record shows what Norm changed unattended
        assert out["verdicts"][0]["suggestions"] == 1

    def test_autopilot_skips_blocking_issue(self, batch):
        det = DETAIL(fileId=None)
        received = []
        out = batch({"inv-1": det}, [], "autopilot", received)
        assert received == []
        assert out["skipped"][0]["outcome"] == "needs review"
        assert out["cards"]  # surfaced for the human

    def test_a_deleted_draft_is_recorded_with_its_reason(self, batch, monkeypatch):
        """A delete left no trace but the run's count, so checking 84 of them
        meant re-reading every document (Little West, 16 Oct 2026)."""
        from app.routers import invoice_fixes as IF

        monkeypatch.setattr(IF, "_apply_delete_invoice", lambda lh, fix, db: None)
        rows: list = []
        monkeypatch.setattr(
            IR, "record_receive_outcome", lambda db, **kw: rows.append(kw)
        )
        out = batch(
            {"inv-1": DETAIL(fileId=None)},
            [],
            "autopilot",
            gates={VA.AUTO_DELETE_UNREADABLE: True},
        )
        (gone,) = out["received"]
        assert gone["outcome"] == "deleted" and gone["deleted_kind"] == "no_copy"
        (row,) = rows
        assert row["outcome_override"] == "deleted" and row["received"] is False
        assert row["actor"] == "norm"
        assert row["extra_detail"]["deleted"]["kind"] == "no_copy"
        assert "no invoice attached" in row["extra_detail"]["deleted"]["reason"]

    def test_approve_fixes_receives_only_untouched_ready(self, batch):
        # agreement → receive; any suggestion → card
        received = []
        out = batch({"inv-1": DETAIL()}, [EXTRACTION()], "approve_fixes", received)
        assert len(received) == 1 and out["received"]

        det = DETAIL()
        det["lines"][0]["quantityReceived"] = 5.0
        received2 = []
        out2 = batch({"inv-1": det}, [EXTRACTION()], "approve_fixes", received2)
        assert received2 == [] and out2["cards"]

    def test_a_venue_on_approve_all_is_not_talked_into_receiving(self, batch):
        """The venue decides; the caller can only ask for less.

        Before this the rung came from the USER who happened to trigger the
        run, so a scheduled task or a chat request could receive at a venue
        that had never opted in. Norm creating stock items and receiving
        invoices somewhere nobody enabled it is the failure worth refusing.

        Folded in: approve_all never receives — a ready invoice waits for
        approval.
        """
        received = []
        out = batch(
            {"inv-1": DETAIL()},
            mode="autopilot",
            venue_mode="approve_all",
            received=received,
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

    def test_repeated_lines_of_one_product_pair_in_order(self):
        """Alsco's monthly invoice lists each rental item once a week: four
        identical lines, four in Loaded. Same product, so pair them in order
        (they used to be refused as "not unique")."""
        reps = [
            {
                "id": f"rep-{n}",
                "linked_item_id": None,
                "code": "81AJ",
                "description": "Laundry Bag",
                "quantity_received": 1,
                "unit_cost": 0.94,
            }
            for n in range(4)
        ]
        docs = [
            {
                "id": f"ld-{n}",
                "linked_item_id": None,
                "code": "81AJ",
                "description": "Laundry Bag",
                "quantity_received": 1,
                "unit_cost": 0.94,
            }
            for n in range(4)
        ]
        pairs, ambiguous, unpaired_rep, unpaired_loaded = pair_lines(reps, docs)
        assert pairs == {f"rep-{n}": f"ld-{n}" for n in range(4)}
        assert not ambiguous and not unpaired_rep and not unpaired_loaded

    def test_of_several_same_product_lines_the_matching_quantity_wins(self):
        reps = [
            {
                "id": "rep-0",
                "linked_item_id": None,
                "code": "TK",
                "description": "Towel",
                "quantity_received": 15,
                "unit_cost": 0.87,
            }
        ]
        docs = [
            {
                "id": "ld-a",
                "linked_item_id": None,
                "code": "TK",
                "description": "Towel",
                "quantity_received": 0,
                "unit_cost": 0.0,
            },
            {
                "id": "ld-b",
                "linked_item_id": None,
                "code": "TK",
                "description": "Towel",
                "quantity_received": 15,
                "unit_cost": 0.87,
            },
        ]
        pairs, _, _, unpaired_loaded = pair_lines(reps, docs)
        assert pairs == {"rep-0": "ld-b"}
        assert [ln["id"] for ln in unpaired_loaded] == ["ld-a"]

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
        assert "no invoice is attached" in s

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

    def test_a_printed_discount_is_never_a_header_suggestion(self):
        # The receive never sends Loaded a discount, so accepting one only
        # took it off the card's total (Fresh Connection 30049130, $41 short).
        data = _review(extraction=EXTRACTION(discount_amount=5.5))
        assert "discount_amount" not in {s.get("field") for s in data["suggestions"]}

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

    def test_no_purchase_order_is_demanded(self):
        # A credit legitimately has no PO of its own — po_missing would block
        # every credit note under the batch policy.
        data = _review(
            detail=self._credit_detail(),
            extraction=self.CREDIT_EXT,
            require_valid_po=True,
        )
        assert "po_missing" not in _issue_codes(data)

    def test_a_clean_credit_reaches_ready_and_receives_negative(self):
        """One clean credit, read once. Folded in: it is marked and negated on
        the document, its credit issue never blocks, and the receive request
        carries the negatives."""
        data = _review(detail=self._credit_detail(), extraction=self.CREDIT_EXT)
        assert data["is_credit_note"] is True
        assert data["replica"]["total"] == -252.75
        assert data["replica"]["lines"][0]["quantity_received"] == -4.95
        assert data["replica"]["lines"][0]["unit_cost"] == 44.4
        credit = next(i for i in data["issues"] if i["code"] == "credit_note")
        assert credit["blocking"] is False
        # The signs disagree with Loaded's positive draft, so there are
        # suggestions — but nothing BLOCKS, which is what ready means.
        assert data["confidence"] == "ready"
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
        """Folded in: exactly one proposal per line, the Replica holds the
        same answer, and the Loaded mirror still shows Loaded's own match."""
        data = self._run()
        # The replica read the same ambiguous words; the order supersedes it.
        assert len(self._item_suggs(data)) == 2
        by_line = {s["line_id"]: s for s in self._item_suggs(data)}
        assert set(by_line) == {"ld-1", "ld-2"}
        # 6.43 arrived against the 6 ordered, 14.15 against the 14 — never
        # both against the same row.
        assert by_line["ld-1"]["apply"]["linked_item_id"] == "item-canoe"
        assert by_line["ld-2"]["apply"]["linked_item_id"] == "item-1inch"
        # and it says why, naming what Loaded picked
        assert "covers 3 stock items" in by_line["ld-1"]["explanation"]
        assert "BONES (STOCK)" in by_line["ld-1"]["explanation"]
        assert by_line["ld-1"]["current"] == "BONES (STOCK)"
        # The rule runs INSIDE the replica: the Replica tab shows what the
        # card proposes, so the two can never disagree (IN11656852).
        assert [rl["linked_item_id"] for rl in data["replica"]["lines"]] == [
            "item-canoe",
            "item-1inch",
        ]
        # The X-ray is Loaded's truth, not Norm's opinion — it must keep
        # resolving by catalogue order however good the order's evidence is.
        snap = data["loaded_snapshot"]["lines"]
        assert [ln["item_name"] for ln in snap] == ["BONES (STOCK)"] * 2

    def test_the_invoices_own_wording_beats_the_order(self):
        # IN11656852 (12 Oct 2026): Service Foods sells SHALLOTS and BANANA
        # SHALLOTS under one code; the invoice said "SHALLOTS (PER KG)", the
        # order said BANANA SHALLOTS, and the order won — wrongly. When the
        # invoice's words name exactly one of the items, they decide.
        det = DETAIL(lines=[self._line("ld-1", 6.43), self._line("ld-2", 14.15)])
        named = dict(self._copy_line(6.43), description="Beef Bones Marrow 1 Inch")
        ext = EXTRACTION(lines=[named, self._copy_line(14.15)])
        data = _review(
            detail=det,
            extraction=ext,
            reference=REFERENCE(catalogue=self.CATALOGUE),
            lh=self._Lh(self.PO),
        )
        rep = data["replica"]["lines"]
        # The first line names 1-INCH outright, though the order's nearest row
        # is the canoe cut; the second can't be told apart, so the order
        # decides it.
        assert rep[0]["linked_item_id"] == "item-1inch"
        assert rep[0]["matched_by"] == "invoice wording"
        assert rep[1]["matched_by"] == "order"

    def test_without_an_order_loadeds_own_match_stands(self):
        data = self._run(po={"createdAt": "2026-08-10", "lines": []})
        assert [rl["linked_item_id"] for rl in data["replica"]["lines"]] == [
            "item-bones",
            "item-bones",
        ]
        assert self._item_suggs(data) == []

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
        """Folded in: accepting links the ordered items, names and all."""
        data = self._run()
        # As Loaded resolved it, both lines point at an item the order never
        # names — so nothing is claimed and Loaded is told nothing.
        req = receive_request_from_doc(data, "v-1", "inv-1")
        assert [ln["quantity_ordered"] for ln in req.lines] == [None, None]
        # Accept the order's items and the rows pair up, one each.
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
        req = receive_request_from_doc(data, "v-1", "inv-1")
        assert [ln["quantity_ordered"] for ln in req.lines] == [6, 14]


class TestSuggestedOrderIsPreCached:
    """Linking an order writes only its ID; the order's ROWS are what the
    projection (order date, per-line quantity ordered, "ordered, not
    delivered") is built from. Caching the rows of the order we are about to
    SUGGEST makes accepting instant — the projection is pure and recomputes on
    the accept patch itself, with no round trip and nothing to re-analyse.
    """

    PO_ID = "po-9"
    PO = {
        "id": PO_ID,
        "orderNumber": "1520999",
        "supplierId": "sup-akaroa",
        "createdAt": "2026-08-07T09:39:54Z",
        "lines": [
            {
                "itemId": "item-salmon",
                "itemName": "SALMON FILLET",
                "itemCode": "PBO0.7",
                "quantityOrdered": 5,
                "unitCost": 44.4,
                "unitName": "Kilo",
            }
        ],
    }

    class _Lh:
        """The open-PO list (how resolve_po_id finds a number) and the order
        detail (how its rows are cached) — the two reads this path makes."""

        def __init__(self, po):
            self.po = po
            self.gets: list[str] = []

        def get(self, path):
            self.gets.append(path)
            if path.startswith("/1.0/stock/internal/purchase-orders?"):
                return [self.po]
            if path.startswith("/1.0/stock/internal/purchase-orders/"):
                return self.po
            return []

        def invoice(self, invoice_id):
            raise KeyError(invoice_id)

    def _run(self):
        # Loaded has no order linked; the COPY names one that is open.
        det = DETAIL(linkedPurchaseOrderId=None, purchaseOrderNumber=None)
        ext = EXTRACTION(purchase_order_number="1520999")
        lh = self._Lh(self.PO)
        return _review(detail=det, extraction=ext, lh=lh), lh

    def test_the_suggested_orders_rows_are_cached_before_it_is_linked(self):
        """Folded in: accepting the link then projects with no further fetch."""
        data, lh = self._run()
        s = next(s for s in data["suggestions"] if s["kind"] == "link_po")
        assert s["apply"]["linked_purchase_order_id"] == self.PO_ID
        assert data["po_reference"]["po_id"] == self.PO_ID
        assert data["po_reference"]["lines"]
        # Cached, NOT applied: nothing is linked, so the projection stays dark
        # rather than reconciling against an order the doc doesn't claim.
        assert data.get("linked_purchase_order_id") is None
        assert data.get("order_date") is None
        assert all(ln.get("quantity_ordered") is None for ln in data["lines"])
        # Accepting: the projection is pure, so the cached rows light up at once.
        before = len(lh.gets)
        apply_suggestion(data, s)
        project_po_reference(data)  # what the PATCH endpoint runs, server-side
        assert data["order_date"] == "2026-08-07T09:39:54Z"
        assert data["lines"][0]["quantity_ordered"] == 5
        assert len(lh.gets) == before  # pure — Loaded is not called again

    def test_an_already_linked_order_is_not_overwritten(self):
        # The doc's OWN order was cached by attach_po_reference; a suggestion
        # for a different one must not replace it under the live projection.
        lh = self._Lh(self.PO)
        data = _review(lh=lh)
        assert data["po_reference"]["po_id"] == "po-1"


def _blocker(data, code, line_id=None):
    return next(
        (
            i
            for i in data["issues"]
            if i["code"] == code and (line_id is None or i.get("line_id") == line_id)
        ),
        None,
    )


class TestCreateItemSuggestion:
    """A product the catalogue has never seen must be CREATED before the
    invoice can be received, so it belongs in the blocked list, once.

    It used to be a suggestion AND a blocking `item_unmatched` issue AND a NEW
    badge AND a disabled button — four surfaces for one decision, because the
    suggestion was added later and the issue was never removed.
    """

    def _unmatched(self, **over):
        # The copy bills a wine no catalogue item matches; the replica's
        # matcher proposes a name and a stock group for it.
        det = DETAIL()
        det["lines"][0].update(
            {
                "code": "COSY22",
                "description": "Alpha Domus Syrah 2022",
                "linkedItemId": None,
            }
        )
        ext = EXTRACTION()
        ext["lines"][0].update(
            {"code": "COSY22", "description": "Alpha Domus Syrah 2022"}
        )
        matcher = lambda *a, **k: {  # noqa: E731 — one-line stub
            "rep-0": {
                "suggested_name": over.get("name", "ALPHA DOMUS SYRAH 2022"),
                "suggested_group_id": over.get("group", "grp-wine"),
            }
        }
        return _review(
            detail=det, extraction=ext, reference=REFERENCE(item_matcher=matcher)
        )

    def _create_sugg(self, data):
        return next(
            (s for s in data["suggestions"] if s["kind"] == "create_item"), None
        )

    def test_an_unmatched_product_blocks_and_its_fix_is_the_create(self):
        data = self._unmatched()
        b = _blocker(data, "item_unmatched", "ld-1")
        assert b is not None and b["blocking"] is True
        create = self._create_sugg(data)
        assert create["payload"] == {
            "name": "ALPHA DOMUS SYRAH 2022",
            "group_id": "grp-wine",
        }
        assert create["gate"] == "auto_create_items"
        assert "create it" in create["explanation"]
        assert b["fix"] == [create["id"]]
        # It names the toggle that would let Norm do it.
        assert b["gate"] == "auto_create_items"
        # The whole point: one decision, one row — the blocker carries no
        # action of its own, it points at the create.
        assert len([s for s in data["suggestions"] if s["kind"] == "create_item"]) == 1
        assert "action" not in b

    def test_no_stock_group_means_no_one_click_create(self):
        # Loaded's create REQUIRES a group; a name alone is not actionable, so
        # the blocker stays but offers no button — the manual form instead.
        data = self._unmatched(group=None)
        assert self._create_sugg(data) is None
        assert _blocker(data, "item_unmatched").get("action") is None


class TestANamelessLoadedLineIsTheSameLine:
    """Red and White Cellars INV562277, 17 Aug 2026 — freight went missing.

    Loaded's OCR had produced a line carrying qty 1 and cost 12 and nothing
    else: no code, no description, no item. Every matching tier needs one of
    those, so the copy's freight line paired with nothing, and Norm proposed
    two things at once — strike the Loaded line, add the copy's line.

    The user accepted both. The strike landed; the append did not, because
    Loaded's invoice PUT does not create a line from an entry with no id. Net
    result: Loaded's own freight line deleted, nothing in its place, and an
    invoice $12 light with no error anywhere.

    Money is the evidence those tiers lack. Quantity and cost agreeing, into a
    line that names nothing, is the same line — so it is UPDATED, which Loaded
    honours, instead of being replaced, which it does not.
    """

    def _nameless(self, **over):
        det = DETAIL()
        det["lines"].append(
            {
                "id": "ld-blank",
                "code": None,
                "description": None,
                "linkedItemId": None,
                "linkedUnitId": None,
                "quantityReceived": over.get("qty", 1.0),
                "unitCostExclTax": over.get("cost", 12.0),
                "saleTaxRate": 0.15,
            }
        )
        ext = EXTRACTION()
        ext["lines"].append(
            {
                "code": None,
                "description": "Freight",
                "quantity": 1,
                "unit": None,
                "unit_of_measure": None,
                "unit_price_ex_tax": 12.0,
                "line_total_ex_tax": 12.0,
            }
        )
        ext["subtotal_ex_tax"] = 231.78
        ext["tax_amount"] = 34.77
        ext["total_incl_tax"] = 266.55
        return _review(detail=det, extraction=ext)

    def test_it_updates_the_line_instead_of_replacing_it(self):
        data = self._nameless()
        kinds = {s["kind"] for s in data["suggestions"]}
        assert "add_line" not in kinds, "a replacement line Loaded would silently drop"
        assert "strike" not in kinds, "Loaded's own freight line was being deleted"

    def test_a_different_price_is_not_the_same_line(self):
        """The pairing is only safe because the money agrees; without that it
        must stay two separate findings."""
        data = self._nameless(cost=99.0)
        kinds = {s["kind"] for s in data["suggestions"]}
        assert "add_line" in kinds

    def test_a_named_loaded_line_is_never_paired_on_money_alone(self):
        """A line that names a product and failed every earlier tier is a
        DIFFERENT product that happens to cost the same."""
        det = DETAIL()
        det["lines"].append(
            {
                "id": "ld-other",
                "code": "ZZZ",
                "description": "SOMETHING ELSE ENTIRELY",
                "linkedItemId": None,
                "linkedUnitId": None,
                "quantityReceived": 1.0,
                "unitCostExclTax": 12.0,
                "saleTaxRate": 0.15,
            }
        )
        ext = EXTRACTION()
        ext["lines"].append(
            {
                "code": None,
                "description": "Freight",
                "quantity": 1,
                "unit": None,
                "unit_of_measure": None,
                "unit_price_ex_tax": 12.0,
                "line_total_ex_tax": 12.0,
            }
        )
        ext["subtotal_ex_tax"] = 231.78
        ext["tax_amount"] = 34.77
        ext["total_incl_tax"] = 266.55
        data = _review(detail=det, extraction=ext)
        assert "add_line" in {s["kind"] for s in data["suggestions"]}


class TestAnOrderOwnedByAnotherSupplierIsJustWorthKnowing:
    """Soho 00162798, 17 Aug 2026. Soho is supplied by Procure: ordering from
    Soho in Loaded while the invoice arrives from Procure is the arrangement,
    not a fault. Norm blocked the receive AND offered to unlink the order —
    which would have thrown away a correct link.

    Two more reasons it was wrong. The comparison is against the supplier the
    replica PROPOSES, so it fired for a state that did not exist yet; and
    receiving never touches the order's ownership (do_receive has no PO guard),
    so blocking bought nothing.
    """

    def _mismatched(self):
        return {
            "linked_purchase_order_id": "po-1",
            "purchase_order_number": "1520559",
            "lines": [],
        }

    def _issues(self):
        return [
            {
                "id": "po_supplier_mismatch",
                "code": "po_supplier_mismatch",
                "blocking": False,
                "message": "the copy names Procure wines and order 1520559 belongs to Soho",
                "data": {"po_supplier_name": "Soho"},
            }
        ]

    def test_it_no_longer_offers_to_unlink_the_order(self):
        suggestions: list = []
        issues = self._issues()
        IR.link_fixes(self._mismatched(), None, issues, suggestions)
        assert [s for s in suggestions if s.get("kind") == "unlink_po"] == []
        assert issues[0]["fix"] == []

    def test_it_does_not_stop_the_receive(self):
        data = {
            **self._mismatched(),
            "issues": self._issues(),
            "suggestion_actions": [],
        }
        assert IR.compute_confidence(data) == "ready"


class TestAnUnlinkIsItsBlockersFix:
    """`unlink_po` sat in Suggested changes next to its own blocker — and
    accepting it did not clear the blocker. Autopilot auto-accepted the unlink
    and then parked the invoice citing the thing it had just remedied.
    """

    def _doubled(self):
        return [
            {
                "id": "po_doubled_up",
                "code": "po_doubled_up",
                "blocking": True,
                "message": "order 1520987 is already fully invoiced by INV-2",
            }
        ]

    def _unlink(self):
        return {
            "id": "unlink_po:purchase_order_number",
            "kind": "unlink_po",
            "field": "purchase_order_number",
            "line_id": None,
            "explanation": "order 1520987 is already fully invoiced — remove the reference",
            "apply": {"purchase_order_number": None, "linked_purchase_order_id": None},
            "sure": False,
        }

    def _linked(self):
        issues, suggestions = self._doubled(), [self._unlink()]
        IR.link_fixes({"lines": []}, None, issues, suggestions)
        return issues, suggestions

    def test_no_switch_authorises_it(self):
        """An order already invoiced by a sibling is a judgement, not a create
        — no venue setting may wave it through."""
        issues, suggestions = self._linked()
        assert issues[0].get("gate") is None and suggestions[0].get("gate") is None
        data = {"lines": [], "suggestions": suggestions, "issues": issues}
        settings = VA.settings_from({g: True for g in VA.GATES})
        autopilot_accept(None, None, "v-1", "inv-1", data, settings)
        assert data["suggestion_actions"] == []

    def test_accepting_it_clears_the_blocker(self):
        issues, _s = self._linked()
        assert issues[0]["fix"] == ["unlink_po:purchase_order_number"]  # points at it
        data = {
            "lines": [],
            "issues": issues,
            "suggestion_actions": [
                {
                    "suggestion_id": "unlink_po:purchase_order_number",
                    "action": "accepted",
                    "by": "user",
                }
            ],
        }
        assert IR.compute_confidence(data) == "ready"


class TestAUnitDecisionSaysWhichUnitWillBeUsed:
    """The blocker read "no unit could be determined … set the unit before
    receiving" while the dropdown plainly showed 750 mL (Soho 00162798). When
    Norm agrees with the unit already there, the fix is to KEEP it — and it
    says which unit, and where it came from.
    """

    def _issue(self, code="unit_missing"):
        return {
            "id": f"{code}:ld-1",
            "code": code,
            "blocking": True,
            "line_id": "ld-1",
            "message": "line 1 'SOHO Harry Rose 2025': no unit could be read from the invoice",
        }

    def _confirm(self, data, issue):
        suggestions: list = []
        IR.link_fixes(data, None, [issue], suggestions)
        assert issue["fix"] == [s["id"] for s in suggestions]
        return suggestions[0] if suggestions else None

    def test_it_keeps_the_unit_loaded_supplied(self):
        """And it is its own switch: a unit Loaded supplied is not "no unit" —
        a different question with a different answer, so it has its own
        toggle."""
        data = {"lines": [{"id": "ld-1", "unit": "750 mL", "linked_unit_id": "u-750"}]}
        issue = self._issue()
        c = self._confirm(data, issue)
        assert c["kind"] == "confirm"
        assert c["explanation"].endswith("keep Loaded's '750 mL'")
        assert issue["gate"] == VA.RECEIVE_WITH_UNCONFIRMED_UNIT
        assert c["gate"] == VA.RECEIVE_WITH_UNCONFIRMED_UNIT

    def test_with_nothing_anywhere_there_is_nothing_to_keep(self):
        data = {"lines": [{"id": "ld-1", "unit": None, "linked_unit_id": None}]}
        issue = self._issue()
        assert self._confirm(data, issue) is None
        assert issue["gate"] == VA.RECEIVE_WITHOUT_UNIT

    def test_an_unreadable_unit_is_named_too(self):
        data = {"lines": [{"id": "ld-1", "unit": "Kilo", "linked_unit_id": "u-kilo"}]}
        c = self._confirm(data, self._issue("unit_unconfirmed"))
        assert "keep Loaded's 'Kilo'" in c["explanation"]

    def test_a_catalogue_supplied_unit_is_credited_to_the_catalogue(self):
        # "Loaded's '700 mL'" was false when Norm's own catalogue answered the
        # unit (HIGHLAND PARK 15, 4366904, 19 Aug 2026).
        data = {"lines": [{"id": "ld-1", "unit": "700 mL", "linked_unit_id": "u-700"}]}
        issue = self._issue("unit_unconfirmed")
        issue["data"] = {"unit_chosen_by": "catalogue", "unit_id": "u-700"}
        c = self._confirm(data, issue)
        assert "Norm's catalogue says '700 mL'" in c["explanation"]

    def test_the_stamp_is_dropped_when_the_working_line_shows_another_unit(self):
        data = {"lines": [{"id": "ld-1", "unit": "Each", "linked_unit_id": "u-each"}]}
        issue = self._issue("unit_unconfirmed")
        issue["data"] = {"unit_chosen_by": "catalogue", "unit_id": "u-700"}
        c = self._confirm(data, issue)
        assert c["explanation"].endswith("keep Loaded's 'Each'")

    def test_a_printed_charge_word_is_named_as_the_fallback_it_is(self):
        data = {"lines": [{"id": "ld-1", "unit": "each", "linked_unit_id": "u-each"}]}
        issue = self._issue("unit_unconfirmed")
        issue["data"] = {"unit_chosen_by": "printed", "unit_id": "u-each"}
        c = self._confirm(data, issue)
        assert "the printed column says 'each'" in c["explanation"]


class TestAutopilotHonoursTheVenuesToggles:
    """Autopilot may create things in Loaded — but only the kinds this venue
    has ticked, and a stock item, unit or brand created there cannot be taken
    back from Norm. So the tests that matter are the refusals.

    Before this, autopilot could not create anything at all: create_item /
    create_unit / create_brand carried no `apply`, so auto_accept_all skipped
    them silently. An unknown brand then left confidence reading "ready",
    do_receive was called, and Loaded answered 400 — every time, forever.
    """

    def _data(self, gate_on=None):
        det = DETAIL()
        det["lines"][0].update({"brand": "BIOZYME", "linkedBrandId": None})
        data = _review(detail=det)
        settings = {"mode": "autopilot", **{g: False for g in VA.GATES}}
        if gate_on:
            settings[gate_on] = True
        return data, settings

    def _loaded(self, monkeypatch, fail=False):
        """A fake Loaded behind the REAL create (services/invoice_catalog).
        Not a patched create function: patching the old endpoint is what hid
        that every unattended create had failed since 3 Oct 2026 — it was
        called with no user, and the endpoint's per-person venue check then
        raised inside apply_open_gates' own except."""
        writes: list = []

        class _Lh:
            def get(self, path):
                assert path == "/1.0/stock/internal/brands"
                return []

            def request(self, method, path, body=None):
                if fail:
                    raise RuntimeError("Loaded said no")
                writes.append((method, path, body))
                return {"id": "brand-new", "name": body["name"]}

        monkeypatch.setattr(IR, "LoadedInvoiceClient", lambda d, c, v: _Lh())
        return writes

    def test_an_unticked_gate_leaves_the_blocker_standing(self, monkeypatch):
        writes = self._loaded(monkeypatch)
        data, settings = self._data()

        assert IR.autopilot_accept(None, None, "v-1", "inv-1", data, settings) == []

        assert writes == [], "autopilot created a brand the venue had not allowed"
        assert IR.compute_confidence(data) == "needs_review"

    def test_a_ticked_gate_creates_it_and_the_invoice_becomes_ready(self, monkeypatch):
        writes = self._loaded(monkeypatch)
        data, settings = self._data(VA.AUTO_CREATE_BRANDS)

        # No person is signed in on an unattended run (db=None, no user).
        done = IR.autopilot_accept(None, None, "v-1", "inv-1", data, settings)

        assert writes == [("POST", "/1.0/stock/internal/brands", {"name": "BIOZYME"})]
        assert done == ["created brand 'BIOZYME'"]
        line = next(ln for ln in data["lines"] if ln["id"] == "ld-1")
        assert line["linked_brand_id"] == "brand-new"
        assert IR.compute_confidence(data) == "ready"
        # Norm signs its own work: the action log is what the card shows and
        # what the readiness report reads, so an unattended create has to
        # appear in it as Norm's.
        act = next(
            a
            for a in data["suggestion_actions"]
            if a["suggestion_id"] == "create_brand:linked_brand_id:ld-1"
        )
        assert act["by"] == "norm" and act["action"] == "accepted"

    def test_a_failed_create_parks_the_invoice_rather_than_receiving_it(
        self, monkeypatch
    ):
        """Half-built is the one outcome worse than stopping: the line would
        reach Loaded without the brand it names and be refused there anyway."""
        self._loaded(monkeypatch, fail=True)
        data, settings = self._data(VA.AUTO_CREATE_BRANDS)

        assert IR.autopilot_accept(None, None, "v-1", "inv-1", data, settings) == []
        assert IR.compute_confidence(data) == "needs_review"

    def test_a_blocker_no_toggle_can_authorise_is_never_cleared(self, monkeypatch):
        """Every gate on, and a duplicate invoice still stops. Toggles buy
        creates, not judgement."""
        data, settings = self._data()
        settings.update({g: True for g in VA.GATES})
        data["issues"].append(
            {
                "id": "duplicate_invoice",
                "code": "duplicate_invoice",
                "blocking": True,
                "message": "this looks like a duplicate",
            }
        )
        IR.autopilot_accept(None, None, "v-1", "inv-1", data, settings)
        assert IR.compute_confidence(data) == "needs_review"


class TestAutopilotCountsWhatItCreated:
    """The run's summary says how many stock items, suppliers and units were
    made new in Loaded — so only a create that really made one counts, not a
    get-or-create that found the record already there."""

    def test_only_new_records_count(self, monkeypatch):
        from app.services import invoice_catalog as catalog
        from app.services import supplier_identity

        class _Lh:
            def get(self, path):
                return []

        monkeypatch.setattr(IR, "LoadedInvoiceClient", lambda *a, **k: _Lh())
        monkeypatch.setattr(supplier_identity, "plausible_matches", lambda n, r: [])
        monkeypatch.setattr(
            catalog,
            "get_or_create_supplier",
            lambda lh, name, details=None: ({"id": "s-1", "name": name}, True),
        )
        monkeypatch.setattr(
            catalog,
            "get_or_create_unit",
            lambda lh, name, db: ({"id": f"u-{name}", "name": name}, name == "Box 6"),
        )
        monkeypatch.setattr(
            catalog,
            "create_item",
            lambda *a, **k: {
                "item_id": f"i-{k['line_id']}",
                "item_name": k["name"],
                "created": k["line_id"] == "l1",
            },
        )

        def _s(sid, kind, gate, line=None, **payload):
            return {
                "id": sid,
                "kind": kind,
                "line_id": line,
                "payload": payload,
                "gate": gate,
            }

        data = {
            "invoice_id": "inv-1",
            "lines": [{"id": "l1"}, {"id": "l2"}],
            "suggestions": [
                _s(
                    "cs",
                    "create_supplier",
                    VA.AUTO_CREATE_SUPPLIERS,
                    supplier_name="Acme",
                ),
                _s("cu1", "create_unit", VA.AUTO_CREATE_UNITS, "l1", unit_name="Box 6"),
                _s("cu2", "create_unit", VA.AUTO_CREATE_UNITS, "l2", unit_name="Each"),
                _s("ci1", "create_item", VA.AUTO_CREATE_ITEMS, "l1", name="Oat Milk"),
                _s("ci2", "create_item", VA.AUTO_CREATE_ITEMS, "l2", name="Soy Milk"),
            ],
            "suggestion_actions": [],
            "issues": [],
        }
        settings = {"mode": "autopilot", **{g: True for g in VA.GATES}}
        done = IR.autopilot_accept(None, None, "v-1", "inv-1", data, settings)

        assert data["autopilot_created"] == {
            "stock_items": ["Oat Milk"],
            "suppliers": ["Acme"],
            "units": ["Box 6"],
        }
        assert "used existing unit 'Each'" in done
        assert "linked existing stock item 'Soy Milk'" in done


class TestAutopilotWillNotInventADuplicateSupplier:
    """Supplier identity picks the extraction spec, so a duplicate supplier row
    is not clutter — it is every future invoice from that business being read
    with another supplier's prompt. That is exactly what happened on 10 Aug
    2026 (Service Foods invoices extracted with the Eurovintage wine spec) and
    it took a day to unpick. Autopilot may only create a supplier that nothing
    plausibly matches.
    """

    def _data(self):
        data = {
            "lines": [],
            "suggestions": [
                {
                    "id": "create_supplier:linked_supplier_id",
                    "kind": "create_supplier",
                    "field": "linked_supplier_id",
                    "payload": {"supplier_name": "SERVICE FOODS LTD"},
                    "gate": VA.AUTO_CREATE_SUPPLIERS,
                    "sure": True,
                }
            ],
            "suggestion_actions": [],
            "issues": [
                {
                    "id": "supplier_unresolved",
                    "code": "supplier_unresolved",
                    "blocking": True,
                    "gate": VA.AUTO_CREATE_SUPPLIERS,
                    "message": "no Loaded supplier matches 'SERVICE FOODS LTD'",
                    "fix": ["create_supplier:linked_supplier_id"],
                }
            ],
        }
        settings = {
            "mode": "autopilot",
            **{g: False for g in VA.GATES},
            VA.AUTO_CREATE_SUPPLIERS: True,
        }
        return data, settings

    def _lh(self, monkeypatch, suppliers):
        writes: list = []

        class _Lh:
            def get(self, _path):
                return suppliers

            def request(self, method, path, body=None):
                writes.append((method, path, body))
                return {"id": "sup-new", "name": body["name"]}

        monkeypatch.setattr(IR, "LoadedInvoiceClient", lambda d, c, v: _Lh())
        return writes

    def test_a_near_match_is_a_human_decision(self, monkeypatch):
        writes = self._lh(
            monkeypatch, [{"id": "sup-1", "name": "SERVICE FOODS AUCKLAND"}]
        )
        data, settings = self._data()

        assert IR.autopilot_accept(None, None, "v-1", "inv-1", data, settings) == []

        assert writes == [], "autopilot created a supplier that already exists"
        assert data.get("linked_supplier_id") is None

    def test_a_near_match_norm_ruled_out_does_not_stop_the_create(self, monkeypatch):
        # Norm compared the invoice's details with SERVICE FOODS AUCKLAND and
        # found another business (replica_finish.settle_supplier).
        writes = self._lh(
            monkeypatch, [{"id": "sup-1", "name": "SERVICE FOODS AUCKLAND"}]
        )
        data, settings = self._data()
        data["suggestions"][0]["payload"]["not_these"] = ["sup-1"]

        IR.autopilot_accept(None, None, "v-1", "inv-1", data, settings)

        assert [w[2]["name"] for w in writes] == ["SERVICE FOODS LTD"]

    def test_a_near_match_norm_never_checked_still_stops_it(self, monkeypatch):
        # A record added since the review was not part of Norm's check.
        writes = self._lh(
            monkeypatch,
            [
                {"id": "sup-1", "name": "SERVICE FOODS AUCKLAND"},
                {"id": "sup-2", "name": "Service Foods Christchurch"},
            ],
        )
        data, settings = self._data()
        data["suggestions"][0]["payload"]["not_these"] = ["sup-1"]

        IR.autopilot_accept(None, None, "v-1", "inv-1", data, settings)

        assert writes == []

    def test_trade_words_in_common_dont_stop_the_create(self, monkeypatch):
        # 'PFD Food Services' is not SERVICE FOODS — sharing 'food' and
        # 'service' is not being the same business.
        writes = self._lh(monkeypatch, [{"id": "sup-1", "name": "PFD Food Services"}])
        data, settings = self._data()
        data["suggestions"][0]["payload"]["supplier_name"] = "Blue Seas Food Services"

        IR.autopilot_accept(None, None, "v-1", "inv-1", data, settings)

        assert [w[2]["name"] for w in writes] == ["Blue Seas Food Services"]

    def test_a_genuinely_new_supplier_is_created(self, monkeypatch):
        writes = self._lh(
            monkeypatch, [{"id": "sup-9", "name": "Hancocks Wine & Spirits"}]
        )
        data, settings = self._data()

        done = IR.autopilot_accept(None, None, "v-1", "inv-1", data, settings)

        assert writes == [
            ("POST", "/1.0/stock/internal/suppliers", {"name": "SERVICE FOODS LTD"})
        ]
        assert done == ["created supplier 'SERVICE FOODS LTD'"]
        assert data["linked_supplier_id"] == "sup-new"

    def test_the_new_supplier_takes_its_invoice_details(self, monkeypatch):
        writes = self._lh(monkeypatch, [])
        data, settings = self._data()
        details = {"city": "Auckland", "email": "orders@servicefoods.co.nz"}
        data["suggestions"][0]["payload"]["details"] = details

        IR.autopilot_accept(None, None, "v-1", "inv-1", data, settings)

        assert writes == [
            (
                "POST",
                "/1.0/stock/internal/suppliers",
                {
                    "name": "SERVICE FOODS LTD",
                    "city": "Auckland",
                    "emails": [
                        {"email": "orders@servicefoods.co.nz", "isPrimary": True}
                    ],
                },
            )
        ]


class TestSplitOrderClearsItsOwnRemedy:
    """Loaded's PO↔invoice link is 1:1, so when a supplier splits an order the
    sibling delivery holds the link and this invoice keeps only the REFERENCE.
    That is a validated state — "splits validate against the order and receive
    without re-linking" — so accepting the reference must clear the blocker.
    It carried no clears_when, so the blocker outlived its own remedy and the
    only way to receive was to wave it through by hand.
    """

    ISSUE = {
        "id": "po_split_order",
        "code": "po_split_order",
        "blocking": True,
        "line_id": None,
        "message": "order 1520546: split across deliveries — 109924953 carries the link",
    }

    def _finalised(self, data):
        return IR._finalise_issues([self.ISSUE], [], {}, require_valid_po=False)

    def test_the_blocker_names_the_field_its_remedy_sets(self):
        i = self._finalised({})[0]
        assert i["clears_when"] == {
            "scope": "header",
            "field": "split_po_id",
            "op": "not_null",
        }

    def test_keeping_the_reference_clears_it_and_the_invoice_is_receivable(self):
        data = {"lines": [], "suggestion_actions": [], "issues": self._finalised({})}
        assert compute_confidence(data) == "needs_review"
        # What accepting the split_reference suggestion applies:
        data["split_po_id"] = "po-1"
        data["split_sibling_invoice_id"] = "inv-sib"
        assert compute_confidence(data) == "ready"


class TestBrandSuggestion:
    """Loaded refuses to receive a line naming a brand it has no record for —
    its own client blocks on it and do_receive guards the same way — so before
    this the invoice just failed at submit with nothing to click (Bidfood
    109945346: BIOZYME on CLEANER INDUSTRIAL ENZYME). The name comes from
    LOADED's line; brands are deliberately NOT extracted from the copy.
    """

    def _with_brand(self, **over):
        det = DETAIL()
        det["lines"][0].update(
            {
                "brand": over.get("brand", "BIOZYME"),
                "linkedBrandId": over.get("brand_id"),
            }
        )
        return _review(detail=det)

    def _brand_sugg(self, data):
        return next(
            (s for s in data["suggestions"] if s["kind"] == "create_brand"), None
        )

    def test_an_unknown_brand_blocks_the_receive(self):
        """It always did — at Loaded's own guard, as a 400, after autopilot had
        already decided the invoice was ready. Now it says so first."""
        data = self._with_brand()
        b = _blocker(data, "brand_unknown", "ld-1")
        assert b is not None and b["blocking"] is True
        create = self._brand_sugg(data)
        assert create["payload"] == {"brand_name": "BIOZYME"}
        assert create["gate"] == "auto_create_brands"
        assert b["gate"] == "auto_create_brands"
        assert b["fix"] == [create["id"]]
        # One decision: one create, and the blocker carries no action of its own.
        assert len([s for s in data["suggestions"] if s["kind"] == "create_brand"]) == 1
        assert "action" not in b
        # The bug this closes: confidence read "ready" with an unknown brand,
        # so autopilot called do_receive and collected a 400 every time.
        assert data["confidence"] == "needs_review"

    def test_a_known_brand_is_left_alone(self):
        data = self._with_brand(brand_id="brand-akaroa")
        assert _blocker(data, "brand_unknown") is None

    def test_no_brand_no_blocker(self):
        assert _blocker(self._with_brand(brand=""), "brand_unknown") is None

    def test_the_created_brand_reaches_the_receive_request(self):
        """Bidfood 109944512, 15 Aug 2026. Creating the brand wrote the id
        into the document and nothing else — the receive payload had no field
        for it, so Loaded never heard, its line stayed unlinked, and the guard
        that reads LOADED refused the invoice forever."""
        data = self._with_brand()
        ln = next(ln for ln in data["lines"] if ln["id"] == "ld-1")
        ln["linked_brand_id"] = "brand-new-1"  # what create-brand writes back
        req = receive_request_from_doc(data, "v-1", "inv-1")
        sent = next(x for x in req.lines if x["id"] == "ld-1")
        assert sent["linked_brand_id"] == "brand-new-1"

    def test_a_reshape_does_not_throw_the_created_brand_away(self):
        """create-item reshapes the draft from Loaded, which knows nothing of
        the brand link. Losing it would cost a real Loaded write the user
        already made — and silently put the invoice back in the refused state.
        """
        from app.services.received_invoice import carry_local_state

        fresh = {"lines": [{"id": "ld-1", "brand": "BIOZYME"}]}
        carry_local_state(
            fresh,
            {"lines": [{"id": "ld-1", "brand": "BIOZYME", "linked_brand_id": "b-1"}]},
        )
        assert fresh["lines"][0]["linked_brand_id"] == "b-1"

    def test_a_reshape_does_not_throw_a_created_unit_away_either(self):
        """The same leak one link over. The unit carry used to be nested
        inside the ITEM branch, so a unit created on an already-linked line
        vanished on the next reshape — putting the invoice back into the
        refusal the user had just cleared, at the cost of a Loaded write."""
        from app.services.received_invoice import carry_local_state

        fresh = {"lines": [{"id": "ld-1", "linked_item_id": "item-1"}]}
        carry_local_state(
            fresh,
            {
                "lines": [
                    {
                        "id": "ld-1",
                        "linked_item_id": "item-1",  # unchanged — the old gate
                        "linked_unit_id": "u-new",
                        "unit": "6x1000mL",
                    }
                ]
            },
        )
        assert fresh["lines"][0]["linked_unit_id"] == "u-new"
        assert fresh["lines"][0]["unit"] == "6x1000mL"

    def test_it_reads_loadeds_line_not_the_copy(self):
        # Every line is covered, paired with a copy line or not — an
        # unresolved brand blocks the receive either way.
        det = DETAIL()
        det["lines"].append(
            {
                "id": "ld-2",
                "code": "ZZZ",
                "description": "NOT ON THE COPY",
                "unit": "Kilo",
                "linkedUnitId": "u-kilo",
                "quantityReceived": 1.0,
                "unitCostExclTax": 1.0,
                "saleTaxRate": 0.15,
                "linkedItemId": "item-freight",
                "brand": "GHOST BRAND",
            }
        )
        data = _review(detail=det)
        brands = [i for i in data["issues"] if i["code"] == "brand_unknown"]
        assert [i["line_id"] for i in brands] == ["ld-2"]


class TestDefectRecommendations:
    """Every defect-class blocker now offers Accept (18-19 Aug 2026): deletes
    for junk drafts (each behind its own gate), the strike for phantom lines,
    and LLM arbitration for totals and ambiguous pairing."""

    def test_no_copy_and_unreadable_carry_delete_actions(self):
        """Both block (folded in from TestBlockingIssues) and both carry the
        delete as their fix."""
        data = _review(detail=DETAIL(fileId=None))
        assert data["confidence"] == "needs_review"
        assert "replica" not in data
        issue = next(i for i in data["issues"] if i["code"] == "no_copy_attached")
        delete = next(s for s in data["suggestions"] if s["kind"] == "delete_invoice")
        assert issue["gate"] == delete["gate"] == "auto_delete_unreadable"
        assert delete["payload"]["type"] == "delete_invoice"
        assert delete["payload"]["invoice_id"] == "inv-1"
        assert issue["fix"] == [delete["id"]]

        data = _review(extraction={"error": "LLM down"})
        assert data["confidence"] == "needs_review"
        issue = next(i for i in data["issues"] if i["code"] == "copy_unreadable")
        assert "LLM down" in issue["message"]
        delete = next(s for s in data["suggestions"] if s["kind"] == "delete_invoice")
        assert delete["gate"] == "auto_delete_unreadable"
        assert issue["fix"] == [delete["id"]]
        assert "delete the draft" in delete["explanation"]

    def test_not_an_invoice_carries_its_delete_action(self):
        ext = EXTRACTION(document_type="statement", lines=[])
        data = _review(extraction=ext)
        issue = next(i for i in data["issues"] if i["code"] == "not_an_invoice")
        delete = next(s for s in data["suggestions"] if s["kind"] == "delete_invoice")
        assert issue["gate"] == delete["gate"] == "auto_delete_non_invoices"
        assert delete["payload"]["invoice_id"] == "inv-1"
        assert issue["fix"] == [delete["id"]]
        assert "delete the draft" in delete["explanation"]

    def test_each_delete_says_why(self):
        """The run's summary counts deletes by this reason."""

        def reason(**kw):
            data = _review(**kw)
            (s,) = [s for s in data["suggestions"] if s["kind"] == "delete_invoice"]
            return s["payload"]["reason"]

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
        assert reason(reference=REFERENCE(received_feed=feed)) == "duplicate"
        assert reason(extraction=EXTRACTION(document_type="statement")) == "statement"
        assert (
            reason(extraction=EXTRACTION(document_type="other", lines=[]))
            == "not_an_invoice"
        )
        assert reason(detail=DETAIL(fileId=None)) == "no_copy"
        assert reason(extraction={"error": "LLM down"}) == "unreadable"
        assert set(IR.DELETE_REASONS) == {
            "duplicate",
            "statement",
            "not_an_invoice",
            "no_copy",
            "unreadable",
        }

    def _misread_totals_case(self, totals_ask):
        det = DETAIL(total=23.0, subtotal=20.0, taxAmount=3.0)
        det["lines"] = [
            {
                "id": "ld-1",
                "code": "PBO0.7",
                "description": "Salmon Fillet Skin On",
                "unit": "Kilo",
                "linkedUnitId": "u-kilo",
                "quantityReceived": 2.0,
                "unitCostExclTax": 10.0,
                "totalCostExclTax": 90.0,
                "saleTaxRate": 0.15,
                "linkedItemId": "item-salmon",
                "itemType": "Default",
            }
        ]
        ext = EXTRACTION(
            subtotal_ex_tax=20.0,
            tax_amount=3.0,
            total_incl_tax=23.0,
            lines=[
                {
                    "code": "PBO0.7",
                    "description": "Salmon Fillet Skin On",
                    "quantity": 2,
                    "unit": "Kilo",
                    "unit_of_measure": "Kilo",
                    "unit_price_ex_tax": 10.0,
                    # misread: 2 x 10 = 20, the copy printed 90
                    "line_total_ex_tax": 90.0,
                }
            ],
        )
        return _review(detail=det, extraction=ext, totals_ask=totals_ask)

    def test_totals_diagnosis_becomes_tagged_suggestions(self):
        data = self._misread_totals_case(
            lambda p: {
                "corrections": [
                    {
                        "scope": "line",
                        "line_id": "rep-0",
                        "field": "line_total",
                        "current": 90.0,
                        "proposed": 20.0,
                    }
                ],
                "confidence": "high",
                "why": "2 x 10.00 = 20.00; the printed 90.00 fails the check",
            }
        )
        s = next(s for s in data["suggestions"] if s["field"] == "total_cost")
        assert s["proposed"] == 20.0
        assert s["confidence"] == "high"
        assert "2 x 10.00" in s["explanation"]
        # ...and the corrected figure is IN the replica: the Replica tab shows it
        assert data["replica"]["lines"][0]["total_cost"] == 20.0
        # The check is on what the suggestions leave, and that adds up (2 x
        # 10.00 + 3.00 = 23.00): no totals blocker, whatever the copy printed.
        assert not [i for i in data["issues"] if i["code"] == "totals_inconsistent"]

    def test_totals_diagnosis_failure_leaves_the_fallback_only(self):
        data = self._misread_totals_case(lambda p: {})
        # Loaded's lines already add up, so there is nothing to block and
        # nothing to confirm — the totals match as they stand.
        assert not [i for i in data["issues"] if i["code"] == "totals_inconsistent"]
        assert not [s for s in data["suggestions"] if s.get("fixes")]

    def _ambiguous_case(self, pairing_ask):
        # rep-0 claims ld-1 (unique salmon); rep-1's only salmon hits are
        # already claimed and 'Freight' doesn't plain-match it → AMBIGUOUS,
        # with ld-2 the sole unclaimed candidate for arbitration.
        det = DETAIL(total=80.5, subtotal=70.0, taxAmount=10.5)
        det["lines"] = [
            {
                "id": "ld-1",
                "code": "PBO0.7",
                "description": "Salmon Fillet Skin On",
                "unit": "Kilo",
                "linkedUnitId": "u-kilo",
                "quantityReceived": 2.0,
                "unitCostExclTax": 10.0,
                "totalCostExclTax": 20.0,
                "saleTaxRate": 0.15,
                "linkedItemId": "item-salmon",
                "itemType": "Default",
            },
            {
                "id": "ld-2",
                "code": "FGT001",
                "description": "Freight",
                "unit": "Each",
                "linkedUnitId": "u-each",
                "quantityReceived": 5.0,
                "unitCostExclTax": 10.0,
                "totalCostExclTax": 50.0,
                "saleTaxRate": 0.15,
                "linkedItemId": "item-freight",
                "itemType": "Default",
            },
        ]
        ext = EXTRACTION(
            subtotal_ex_tax=70.0,
            tax_amount=10.5,
            total_incl_tax=80.5,
            lines=[
                {
                    "code": "PBO0.7",
                    "description": "Salmon Fillet Skin On",
                    "quantity": qty,
                    "unit": "Kilo",
                    "unit_of_measure": "Kilo",
                    "unit_price_ex_tax": 10.0,
                    "line_total_ex_tax": qty * 10.0,
                }
                for qty in (2, 5)
            ],
        )
        return _review(detail=det, extraction=ext, pairing_ask=pairing_ask)

    def test_decisive_pairing_unlocks_the_lines(self):
        data = self._ambiguous_case(
            lambda p: {
                "pairs": {"rep-1": "ld-2"},
                "confidence": "high",
                "why": "the quantities disambiguate the lines",
            }
        )
        assert "ambiguous_pairing" not in _issue_codes(data)
        note = next(i for i in data["issues"] if i["code"] == "pairing_arbitrated")
        assert note["blocking"] is False
        assert "quantities disambiguate" in note["message"]

    def test_an_unpaired_line_is_added_not_blocked(self):
        """The arbiter couldn't pair it: Norm still decides — the copy's line
        is added and Loaded's leftover line struck."""
        data = self._ambiguous_case(lambda p: {})
        assert "ambiguous_pairing" not in _issue_codes(data)
        kinds = {s["kind"] for s in data["suggestions"]}
        assert "add_line" in kinds


class TestDestructiveGates:
    """The delete gates: autopilot's only destructive writes — never without
    the venue's toggle, always short-circuiting, always recorded."""

    def _data(self, code, kind, gate):
        return {
            "invoice_id": "inv-1",
            "lines": [],
            "suggestions": [
                {
                    "id": kind,
                    "kind": kind,
                    "payload": {
                        "type": "delete_invoice",
                        "invoice_id": "inv-1",
                        "summary": "junk draft",
                    },
                    "gate": gate,
                    "sure": True,
                }
            ],
            "suggestion_actions": [],
            "issues": [
                {
                    "id": code,
                    "code": code,
                    "blocking": True,
                    "gate": gate,
                    "message": "x",
                    "fix": [kind],
                }
            ],
        }

    def _settings(self, *on):
        return {
            "mode": "autopilot",
            **{g: False for g in VA.GATES},
            **{g: True for g in on},
        }

    def test_gate_off_never_deletes(self, monkeypatch):
        from app.routers import invoice_fixes as IF

        calls: list = []
        monkeypatch.setattr(
            IF, "_apply_delete_invoice", lambda *a, **k: calls.append(a)
        )
        data = self._data(
            "duplicate_invoice", "delete_invoice", VA.AUTO_DELETE_DUPLICATES
        )
        assert (
            IR.autopilot_accept(None, None, "v-1", "inv-1", data, self._settings())
            == []
        )
        assert calls == [], "autopilot deleted a draft the venue had not allowed"
        assert not data.get("is_deleted")

    def test_gate_on_deletes_and_short_circuits(self, monkeypatch):
        from app.routers import invoice_fixes as IF

        deletes: list = []
        creates: list = []
        monkeypatch.setattr(
            IF, "_apply_delete_invoice", lambda lh, fix, db: deletes.append(fix)
        )
        monkeypatch.setattr(
            "app.services.invoice_catalog.get_or_create_brand",
            lambda *a, **k: creates.append(a) or ({}, True),
        )
        monkeypatch.setattr(IR, "LoadedInvoiceClient", lambda *a, **k: object())
        data = self._data(
            "duplicate_invoice", "delete_invoice", VA.AUTO_DELETE_DUPLICATES
        )
        # a create the walk would normally run — must be skipped by the delete
        data["suggestions"].append(
            {
                "id": "create_brand:linked_brand_id:ld-1",
                "kind": "create_brand",
                "line_id": "ld-1",
                "payload": {"brand_name": "X"},
                "gate": VA.AUTO_CREATE_BRANDS,
            }
        )
        data["issues"].append(
            {
                "id": "brand_unknown:ld-1",
                "code": "brand_unknown",
                "blocking": True,
                "line_id": "ld-1",
                "gate": VA.AUTO_CREATE_BRANDS,
                "message": "x",
                "fix": ["create_brand:linked_brand_id:ld-1"],
            }
        )
        data["lines"] = [{"id": "ld-1"}]
        done = IR.autopilot_accept(
            None,
            None,
            "v-1",
            "inv-1",
            data,
            self._settings(VA.AUTO_DELETE_DUPLICATES, VA.AUTO_CREATE_BRANDS),
        )
        assert deletes and deletes[0]["invoice_id"] == "inv-1"
        assert creates == []  # short-circuited
        assert data["is_deleted"] is True and data["status"] == "deleted"
        assert done == ["deleted the draft (junk draft)"]
        # recorded against the delete, actor norm — which settles its blocker
        rec = data["suggestion_actions"][-1]
        assert rec["suggestion_id"] == "delete_invoice" and rec["by"] == "norm"

    def test_a_delete_keeps_its_reason_on_the_draft(self, monkeypatch):
        from app.routers import invoice_fixes as IF

        monkeypatch.setattr(IF, "_apply_delete_invoice", lambda lh, fix, db: None)
        monkeypatch.setattr(IR, "LoadedInvoiceClient", lambda *a, **k: object())
        data = self._data(
            "not_an_invoice", "delete_invoice", VA.AUTO_DELETE_NON_INVOICES
        )
        data["suggestions"][0]["payload"]["reason"] = "statement"
        IR.autopilot_accept(
            None,
            None,
            "v-1",
            "inv-1",
            data,
            self._settings(VA.AUTO_DELETE_NON_INVOICES),
        )
        assert data["deleted_kind"] == "statement"
        assert data["deleted_reason"] == "junk draft"
        # a suggestion from before reasons were recorded still says something
        data = self._data(
            "duplicate_invoice", "delete_invoice", VA.AUTO_DELETE_DUPLICATES
        )
        IR.autopilot_accept(
            None, None, "v-1", "inv-1", data, self._settings(VA.AUTO_DELETE_DUPLICATES)
        )
        assert data["deleted_kind"] == "other"

    def test_strike_and_record_only_gates(self):
        data = {
            "invoice_id": "inv-1",
            "lines": [{"id": "ld-9", "description": "Phantom"}],
            "suggestions": [
                {
                    "id": "strike:ld-9",
                    "kind": "strike",
                    "line_id": "ld-9",
                    "apply": {"struck": True},
                    "gate": VA.AUTO_STRIKE_PHANTOM_LINES,
                }
            ],
            "suggestion_actions": [],
            "issues": [
                {
                    "id": "loaded_line_not_on_copy:ld-9",
                    "code": "loaded_line_not_on_copy",
                    "blocking": True,
                    "line_id": "ld-9",
                    "gate": VA.AUTO_STRIKE_PHANTOM_LINES,
                    "fix": ["strike:ld-9"],
                    "message": "x",
                    "clears_when": {
                        "scope": "line",
                        "line_id": "ld-9",
                        "field": "struck",
                        "op": "truthy",
                    },
                },
                {
                    "id": "totals_inconsistent",
                    "code": "totals_inconsistent",
                    "blocking": True,
                    "line_id": None,
                    "message": "x",
                    "clears_when": {"scope": "header", "op": "balances"},
                },
            ],
        }
        done = IR.autopilot_accept(
            None,
            None,
            "v-1",
            "inv-1",
            data,
            self._settings(VA.AUTO_STRIKE_PHANTOM_LINES),
        )
        assert data["lines"][0]["struck"] is True
        assert any("struck" in d for d in done)
        # The totals blocker stands down because, with the phantom struck,
        # the invoice adds up.
        assert IR.compute_confidence(data) == "ready"

    def test_every_switch_on_never_clears_totals_that_dont_match(self):
        # Loaded won't receive an invoice that doesn't add up, so no switch
        # lets Norm try (the owner, 16 Oct 2026).
        data = {
            "invoice_id": "inv-1",
            "total": 50.0,
            "lines": [
                {
                    "id": "ld-1",
                    "quantity_received": 1,
                    "unit_cost": 10.0,
                    "sale_tax_rate": 0.15,
                }
            ],
            "suggestions": [],
            "suggestion_actions": [],
            "issues": [
                {
                    "id": "totals_inconsistent",
                    "code": "totals_inconsistent",
                    "blocking": True,
                    "message": "x",
                    "clears_when": {"scope": "header", "op": "balances"},
                }
            ],
        }
        IR.autopilot_accept(
            None,
            None,
            "v-1",
            "inv-1",
            data,
            VA.settings_from({"mode": "autopilot", **{g: True for g in VA.GATES}}),
        )
        assert IR.compute_confidence(data) == "needs_review"


class TestDecisionsSurviveARereview:
    """A review REBUILDS the payload from Loaded and clears suggestions,
    issues and suggestion_actions. Without carrying decisions across,
    accepting a suggestion and then re-reviewing (the dojo pass, or simply
    reopening the card) threw the accept away: the working value reverted to
    Loaded's, the suggestion came back, and the receive was recorded as
    "ignored". SI03448887 (26 Aug 2026) was reported EDITED with four ignored
    suggestions the user had in fact actioned.
    """

    def _previous(self, *, action="accepted", proposed=0.57):
        return {
            "lines": [{"id": "ld-1", "quantity_received": proposed}],
            "suggestions": [
                {
                    "id": "line_value:quantity_received:ld-1",
                    "kind": "line_value",
                    "field": "quantity_received",
                    "line_id": "ld-1",
                    "apply": {"quantity_received": proposed},
                }
            ],
            "suggestion_actions": [
                {
                    "suggestion_id": "line_value:quantity_received:ld-1",
                    "action": action,
                    "by": "user",
                    "at": "2026-08-26T08:00:00+00:00",
                }
            ],
        }

    def _fresh(self, *, proposed=0.57):
        return {
            "lines": [{"id": "ld-1", "quantity_received": 5.0}],
            "suggestions": [
                {
                    "id": "line_value:quantity_received:ld-1",
                    "kind": "line_value",
                    "field": "quantity_received",
                    "line_id": "ld-1",
                    "apply": {"quantity_received": proposed},
                }
            ],
            "issues": [],
            "suggestion_actions": [],
        }

    def test_an_accept_survives_and_is_re_applied(self):
        fresh = self._fresh()
        carried = IR.carry_forward_decisions(self._previous(), fresh)
        assert carried == 1
        assert fresh["lines"][0]["quantity_received"] == 0.57  # re-applied
        assert fresh["suggestion_actions"][0]["action"] == "accepted"

    def test_a_dismissal_survives_without_touching_the_values(self):
        fresh = self._fresh()
        IR.carry_forward_decisions(self._previous(action="dismissed"), fresh)
        assert fresh["lines"][0]["quantity_received"] == 5.0  # untouched
        assert fresh["suggestion_actions"][0]["action"] == "dismissed"

    def test_a_changed_proposal_is_asked_again(self):
        """Holding someone to an answer they never gave is worse than asking
        twice — the number moved, so the decision does not transfer."""
        fresh = self._fresh(proposed=9.99)
        carried = IR.carry_forward_decisions(self._previous(proposed=0.57), fresh)
        assert carried == 0
        assert fresh["suggestion_actions"] == []
        assert fresh["lines"][0]["quantity_received"] == 5.0

    def test_a_suggestion_that_no_longer_exists_carries_nothing(self):
        fresh = {"lines": [], "suggestions": [], "issues": [], "suggestion_actions": []}
        assert IR.carry_forward_decisions(self._previous(), fresh) == 0

    def test_a_waved_blocker_survives_while_it_is_still_raised(self):
        previous = {
            "suggestions": [],
            "suggestion_actions": [
                {
                    "suggestion_id": "unit_missing:ld-1",
                    "action": "accepted",
                    "by": "user",
                }
            ],
        }
        fresh = {
            "lines": [],
            "suggestions": [],
            "issues": [{"id": "unit_missing:ld-1", "blocking": True}],
            "suggestion_actions": [],
        }
        assert IR.carry_forward_decisions(previous, fresh) == 1

    def test_a_blocker_no_longer_raised_carries_nothing(self):
        previous = {
            "suggestions": [],
            "suggestion_actions": [
                {
                    "suggestion_id": "unit_missing:ld-1",
                    "action": "accepted",
                    "by": "user",
                }
            ],
        }
        fresh = {"lines": [], "suggestions": [], "issues": [], "suggestion_actions": []}
        assert IR.carry_forward_decisions(previous, fresh) == 0

    def test_a_v1_blocker_decision_carries_onto_its_fix(self):
        """A draft reviewed before 12 Oct 2026 recorded a unit pick against
        the BLOCKER. Re-reviewed, that pick is the blocker's fix suggestion:
        the decision carries and the value is re-applied (the user: keep the
        changes already accepted)."""
        previous = {
            "suggestions": [],
            "suggestion_actions": [
                {
                    "suggestion_id": "unit_missing:ld-1",
                    "action": "accepted",
                    "by": "user",
                    "after": {"linked_unit_id": "u-each"},
                }
            ],
        }
        fresh = {
            "lines": [{"id": "ld-1"}],
            "suggestions": [
                {
                    "id": "line_value:unit:ld-1",
                    "kind": "line_value",
                    "field": "unit",
                    "line_id": "ld-1",
                    "apply": {
                        "unit": "Each",
                        "linked_unit_id": "u-each",
                        "unit_ratio": 1,
                    },
                }
            ],
            "issues": [
                {
                    "id": "unit_missing:ld-1",
                    "blocking": True,
                    "fix": ["line_value:unit:ld-1"],
                }
            ],
            "suggestion_actions": [],
        }
        IR.carry_forward_decisions(previous, fresh)
        assert fresh["lines"][0]["linked_unit_id"] == "u-each"
        assert IR.compute_confidence(fresh) == "ready"

    def test_a_first_review_carries_nothing(self):
        assert IR.carry_forward_decisions({}, self._fresh()) == 0


class TestALineThatDisagreesWithItself:
    """Bidfood 90ea78ed, 26 Aug 2026. The copy printed 0.92 x $53.61 with a
    line total of $49.48 — 16c adrift, because the supplier billed a weight
    Loaded cannot store (0.923 kg becomes 0.92). Loaded then refused the whole
    receive with `invoice-totals-mismatch`, declared 495.47 vs its own lines'
    495.28, and nothing on our side had compared the two.
    """

    #: Loaded holds 0.92 x 53.61 = 49.32; the copy bills 49.48. Tax at 15% of
    #: the BILLED figure, and the invoice total is billed + tax — exactly the
    #: shape of Bidfood 90ea78ed.
    TAX = round(49.48 * 0.15, 2)

    def _doc(self, *, loaded_cost=53.61, printed_total=49.48, declared=None):
        det, ext = DETAIL(), EXTRACTION()
        det["lines"][0]["quantityReceived"] = 0.92
        det["lines"][0]["unitCostExclTax"] = loaded_cost
        det["lines"][0]["totalCostExclTax"] = round(0.92 * loaded_cost, 4)
        det["lines"][0]["taxAmount"] = self.TAX
        det["total"] = (
            declared if declared is not None else round(printed_total + self.TAX, 2)
        )
        det["taxAmount"] = self.TAX
        ext["lines"][0]["quantity"] = 0.92
        ext["lines"][0]["unit_price_ex_tax"] = 53.61
        ext["lines"][0]["line_total_ex_tax"] = printed_total
        return _review(detail=det, extraction=ext)

    def test_the_fix_is_suggested_on_the_line(self):
        """49.48 / 0.92 = 53.7826… -> 53.78, and 0.92 x 53.78 restores 49.48 —
        accepting it makes the line match the invoice."""
        data = self._doc()
        s = next(s for s in data["suggestions"] if s.get("field") == "unit_cost")
        assert s["proposed"] == 53.78
        assert "$49.48" in s["explanation"] and "$49.32" in s["explanation"]
        assert s["apply"] == {"unit_cost": 53.78}
        IR.apply_suggestion(data, s)
        ln = next(x for x in data["lines"] if str(x["id"]) == str(s["line_id"]))
        assert round(ln["quantity_received"] * ln["unit_cost"], 2) == 49.48

    def test_a_copy_that_agrees_with_itself_suggests_no_correction(self):
        data = self._doc(printed_total=49.32, declared=49.32 * 1.15)
        assert not [
            s
            for s in data["suggestions"]
            if s.get("field") == "unit_cost" and "match the invoice" in s["explanation"]
        ]

    def test_a_gap_the_rounding_story_cannot_explain_suggests_nothing(self):
        """2 x $10 billed at $90 implies 9 units, not a rounded 2 — the figure
        that was misread is anyone's guess, and inventing a $45 unit price
        would be worse than saying nothing."""
        data = self._doc(loaded_cost=10.0, printed_total=90.0)
        assert not [
            s
            for s in data["suggestions"]
            if s.get("field") == "unit_cost" and "match the invoice" in s["explanation"]
        ]

    def test_an_ordinary_price_difference_still_reads_as_one(self):
        """Loaded simply holding a different price is not a totals problem and
        must keep its own plain explanation."""
        data = self._doc(loaded_cost=40.0, printed_total=49.32)
        s = next(s for s in data["suggestions"] if s.get("field") == "unit_cost")
        assert s["proposed"] == 53.61
        assert "prices" in s["explanation"]


class TestTheCopyDecidesWhichLayoutReadsIt:
    """Pass 2 — the printed name re-selects the spec.

    Pass 1 has no choice but to pick the layout from Loaded's supplier: the
    printed name does not exist until the copy has been read. That leaves one
    hole nothing could see. An invoice printed by supplier B but filed in
    Loaded under supplier A is read with A's prompt, and stays that way for
    good, because the extraction cache row is keyed to those instructions.

    Production had exactly that: a `Neat Meat` invoice sitting on Loaded's
    `Coca Cola` supplier record, read with Coca Cola's prompt while a
    `NEAT MEAT` spec existed. The model was even asked `supplier_differs` and
    said true — and nothing anywhere read the answer.

    So once the copy has been read, the name printed on it is the higher
    authority (the order `resolve_supplier` already states) and re-selects the
    layout. Once, never a loop, and only when the spec actually changes.
    """

    class _Spec:
        def __init__(self, id, name, instructions="notes"):
            self.id, self.name, self.instructions = id, name, instructions

    def _wire(self, monkeypatch, by_name, second=None):
        """`find_spec_for_supplier` over a tiny roster, and a recording
        extractor standing in for the model."""
        calls = []

        def _find(_cdb, *names):
            for n in names:
                hit = by_name.get(str(n or "").lower())
                if hit:
                    return hit
            return None

        def _extract(_db, _lh, _file_id, *, instructions, venue_key):  # noqa: ARG001
            calls.append(instructions)
            return second if second is not None else {"supplier_name": "unchanged"}

        monkeypatch.setattr(IR, "find_spec_for_supplier", _find)
        monkeypatch.setattr(IR, "extract_invoice_copy", _extract)
        monkeypatch.setattr(
            IR,
            "compose_pdf_instructions",
            lambda _c, **kw: "INSTR:" + str(kw.get("spec_name")),
        )
        return calls

    def test_a_copy_printed_by_another_business_is_re_read(self, monkeypatch):
        """The Neat Meat invoice, on Loaded's Coca Cola record."""
        neat = self._Spec("s-neat", "NEAT MEAT")
        calls = self._wire(
            monkeypatch,
            {"coca cola": self._Spec("s-coke", "Coca Cola"), "neat meat": neat},
            second={"supplier_name": "Neat Meat", "invoice_number": "SECOND"},
        )
        out, used = IR.reread_under_printed_spec(
            None,
            object(),
            None,
            "v-1",
            "f-1",
            {"supplierName": "Coca Cola"},
            [],
            {"supplier_name": "Neat Meat"},
        )
        assert calls == ["INSTR:NEAT MEAT"]
        assert out["invoice_number"] == "SECOND"
        assert used is neat

    def test_the_same_business_spelled_differently_is_not_re_read(self, monkeypatch):
        """77% of production invoices print a different STRING to Loaded's
        ('Bidfood Limited' vs 'Bidfood'). Re-reading those would double the
        extraction bill to learn nothing."""
        bidfood = self._Spec("s-bid", "Bidfood")
        calls = self._wire(
            monkeypatch, {"bidfood": bidfood, "bidfood limited": bidfood}
        )
        out, used = IR.reread_under_printed_spec(
            None,
            object(),
            None,
            "v-1",
            "f-1",
            {"supplierName": "Bidfood"},
            [],
            {"supplier_name": "Bidfood Limited"},
        )
        assert calls == []
        assert out["supplier_name"] == "Bidfood Limited"
        assert used is bidfood

    def test_a_printed_name_with_no_spec_keeps_the_first_read(self, monkeypatch):
        """Nothing to switch TO. Pass 1's spec stands, and the printed name is
        handed to the sensei instead (covered below)."""
        calls = self._wire(
            monkeypatch, {"coca cola": self._Spec("s-coke", "Coca Cola")}
        )
        _out, used = IR.reread_under_printed_spec(
            None,
            object(),
            None,
            "v-1",
            "f-1",
            {"supplierName": "Coca Cola"},
            [],
            {"supplier_name": "Someone Brand New"},
        )
        assert calls == []
        assert used.name == "Coca Cola"

    def test_an_unreadable_second_pass_never_loses_the_first(self, monkeypatch):
        """A failed re-read must not cost us the read we already have."""
        calls = self._wire(
            monkeypatch,
            {
                "coca cola": self._Spec("s-coke", "Coca Cola"),
                "neat meat": self._Spec("s-neat", "NEAT MEAT"),
            },
            second={"error": "model unavailable"},
        )
        out, _used = IR.reread_under_printed_spec(
            None,
            object(),
            None,
            "v-1",
            "f-1",
            {"supplierName": "Coca Cola"},
            [],
            {"supplier_name": "Neat Meat", "invoice_number": "FIRST"},
        )
        assert len(calls) == 1
        assert out["invoice_number"] == "FIRST"

    def test_a_copy_with_no_printed_supplier_is_not_re_read(self, monkeypatch):
        calls = self._wire(
            monkeypatch, {"coca cola": self._Spec("s-coke", "Coca Cola")}
        )
        IR.reread_under_printed_spec(
            None,
            object(),
            None,
            "v-1",
            "f-1",
            {"supplierName": "Coca Cola"},
            [],
            {"supplier_name": None},
        )
        assert calls == []

    def test_it_re_reads_once_not_in_a_loop(self, monkeypatch):
        """The second read names the same supplier again; feeding it back must
        not trigger a third."""
        neat = self._Spec("s-neat", "NEAT MEAT")
        calls = self._wire(
            monkeypatch,
            {"coca cola": self._Spec("s-coke", "Coca Cola"), "neat meat": neat},
            second={"supplier_name": "Neat Meat"},
        )
        out, used = IR.reread_under_printed_spec(
            None,
            object(),
            None,
            "v-1",
            "f-1",
            {"supplierName": "Coca Cola"},
            [],
            {"supplier_name": "Neat Meat"},
        )
        again, _ = IR.reread_under_printed_spec(
            None,
            object(),
            None,
            "v-1",
            "f-1",
            {"supplierName": "Coca Cola"},
            [],
            out,
        )
        assert len(calls) == 2  # one per explicit call, never recursive
        assert used is neat and again["supplier_name"] == "Neat Meat"

    def test_loaded_s_supplier_stays_in_the_second_prompt(self, monkeypatch):
        """The supplier-differs clause is what lets the model say the two
        disagree, and Loaded's name is still a true statement about Loaded."""
        seen = {}
        self._wire(
            monkeypatch,
            {
                "coca cola": self._Spec("s-coke", "Coca Cola"),
                "neat meat": self._Spec("s-neat", "NEAT MEAT"),
            },
            second={"supplier_name": "Neat Meat"},
        )
        monkeypatch.setattr(
            IR,
            "compose_pdf_instructions",
            lambda _c, **kw: seen.update(kw) or "INSTR",
        )
        IR.reread_under_printed_spec(
            None,
            object(),
            None,
            "v-1",
            "f-1",
            {"supplierName": "Coca Cola"},
            ["COKE NZ"],
            {"supplier_name": "Neat Meat"},
        )
        assert seen["loaded_supplier"] == "Coca Cola"
        assert seen["loaded_aliases"] == ["COKE NZ"]
        assert seen["spec_name"] == "NEAT MEAT"


class TestTheUnitGatePicksAUnit:
    """receive_without_unit means: when no unit can be found on the invoice,
    Norm picks one so the invoice can be received — the batched resolver's
    answer when there is one, else Loaded's own unit, else the unit chooser
    (unit_guess) picking from the venue's own units. That fallback is what the
    setting is for (the user, 12 Oct 2026, after it was briefly removed as a
    "second picker"). The pick is made in the Replica at review time and
    applied by autopilot only with the switch on."""

    def _data(self):
        return {
            "invoice_id": "inv-1",
            "lines": [{"id": "ld-1", "description": "MALFY GIN ROSA"}],
            "suggestions": [
                {
                    "id": "line_value:unit:ld-1",
                    "kind": "line_value",
                    "field": "unit",
                    "line_id": "ld-1",
                    "proposed": "700 mL",
                    "apply": {
                        "linked_unit_id": "u-700",
                        "unit": "700 mL",
                        "unit_ratio": 0.7,
                    },
                    "sure": False,
                    "gate": VA.RECEIVE_WITHOUT_UNIT,
                }
            ],
            "suggestion_actions": [],
            "issues": [
                {
                    "id": "unit_missing:ld-1",
                    "code": "unit_missing",
                    "blocking": True,
                    "line_id": "ld-1",
                    "gate": VA.RECEIVE_WITHOUT_UNIT,
                    "fix": ["line_value:unit:ld-1"],
                    "message": "no unit",
                }
            ],
        }

    def _settings(self, on=True):
        return {
            "mode": "autopilot",
            **{g: False for g in VA.GATES},
            VA.RECEIVE_WITHOUT_UNIT: on,
        }

    def test_the_pick_is_applied_with_the_switch_on(self, monkeypatch):
        monkeypatch.setattr(
            IR,
            "LoadedInvoiceClient",
            lambda *a, **k: pytest.fail("applying a pick needs no Loaded call"),
        )
        data = self._data()
        done = IR.autopilot_accept(None, None, "v-1", "inv-1", data, self._settings())
        line = data["lines"][0]
        assert line["linked_unit_id"] == "u-700" and line["unit"] == "700 mL"
        assert any("set unit '700 mL'" in d for d in done)
        assert IR.compute_confidence(data) == "ready"

    def test_never_with_the_switch_off(self):
        data = self._data()
        IR.autopilot_accept(None, None, "v-1", "inv-1", data, self._settings(False))
        assert "linked_unit_id" not in data["lines"][0]
        assert IR.compute_confidence(data) == "needs_review"

    def test_without_a_confident_pick_norm_still_picks_one(self):
        """Fails if the fallback is removed again: no resolver answer and no
        unit on Loaded's line — the venue's chooser picks one."""
        from app.services import replica_finish as RF

        replica = {
            "lines": [{"id": "rep-0", "description": "MALFY GIN ROSA"}],
            "issues": [
                {"id": "unit_missing:rep-0", "code": "unit_missing", "line_id": "rep-0"}
            ],
        }
        data = {"lines": [{"id": "ld-1", "description": "MALFY GIN ROSA"}]}
        each = {"id": "u-each", "name": "Each", "ratio": 1}
        RF.settle_units(data, replica, {"rep-0": "ld-1"}, [each], lambda ln: each, [])
        rl = replica["lines"][0]
        assert rl["linked_unit_id"] == "u-each"
        assert rl["unit_guess"] is True and rl["unit_by"] == "chooser"

    def test_a_last_resort_pick_says_so_on_the_card(self):
        from app.services import replica_finish as RF

        replica = {
            "lines": [{"id": "rep-0", "description": "Pistachio Jam-Can"}],
            "issues": [
                {"id": "unit_missing:rep-0", "code": "unit_missing", "line_id": "rep-0"}
            ],
        }
        forced = {
            "id": "u-each",
            "name": "Each",
            "ratio": 1,
            "why": "'Each' is Norm's best guess from the venue's units; check it",
        }
        RF.settle_units({"lines": []}, replica, {}, [], lambda ln: forced, [])
        rl = replica["lines"][0]
        assert rl["linked_unit_id"] == "u-each" and rl["unit_guess"] is True
        assert rl["unit_why"] == (
            "no unit could be read from the invoice for 'Pistachio Jam-Can' — "
            "'Each' is Norm's best guess from the venue's units; check it"
        )


class TestABatchSavesAsItGoes:
    """A long batch reports each invoice as it finishes, and stops between
    invoices when cancelled or out of time — handing back the ones it never
    reached (14 Oct 2026: a 200-invoice batch inside a chat request was cut
    off at 25 minutes and lost every review it had done)."""

    def _stub(self, monkeypatch):
        class _Lh:
            def invoice(self, iid):
                return {
                    "id": iid,
                    "referenceNumber": f"R{iid}",
                    "supplierName": "Alsco",
                }

            def get(self, path):
                return []

        monkeypatch.setattr(IR, "LoadedInvoiceClient", lambda *a, **k: _Lh())
        monkeypatch.setattr(IR, "account_suppliers", lambda lh: [])
        monkeypatch.setattr(
            "app.services.spec_dojo.prefetch_replica_reference", lambda *a, **k: None
        )
        monkeypatch.setattr(
            IR,
            "review_invoice",
            lambda db, cdb, vid, iid, **kw: {
                "invoice_id": iid,
                "reference_number": f"R{iid}",
                "confidence": "needs_review",
                "issues": [],
                "suggestions": [],
            },
        )

    def test_each_invoice_is_reported_as_it_finishes(self, monkeypatch):
        self._stub(monkeypatch)
        seen = []
        out = IR.review_invoices(
            None,
            None,
            "v-1",
            ["a", "b", "c"],
            on_invoice=lambda iid, rec, data: seen.append(
                (iid, rec["outcome"], data["invoice_id"])
            ),
        )
        assert seen == [
            ("a", "needs review", "a"),
            ("b", "needs review", "b"),
            ("c", "needs review", "c"),
        ]
        assert out["remaining"] == [] and out["stopped"] is None

    def test_a_cancel_stops_between_invoices(self, monkeypatch):
        self._stub(monkeypatch)
        seen = []
        out = IR.review_invoices(
            None,
            None,
            "v-1",
            ["a", "b", "c"],
            on_invoice=lambda iid, rec, data: seen.append(iid),
            should_stop=lambda: len(seen) >= 1,
        )
        assert seen == ["a"]
        assert out["remaining"] == ["b", "c"] and out["stopped"] == "cancelled"

    def test_out_of_time_hands_back_the_rest(self, monkeypatch):
        import time

        self._stub(monkeypatch)
        out = IR.review_invoices(
            None, None, "v-1", ["a", "b"], deadline=time.monotonic() - 1
        )
        assert out["remaining"] == ["a", "b"] and out["stopped"] == "out of time"
        assert out["cards"] == []


class TestEachInvoiceLikeTheCard:
    """A batch is invoices reviewed one after another, each exactly as the
    receive card reviews one — no batch-only steps (16 Oct 2026, the owner:
    "prioritise simplicity over efficiency. less code and less paths"). A
    supplier with no spec is studied as its invoice is reviewed, and the
    venue's reference data is fetched fresh for each invoice, so what one
    invoice created in Loaded is there for the next."""

    def test_one_at_a_time_studying_and_fetching_fresh(self, monkeypatch):
        calls: list = []

        class _Lh:
            def invoice(self, iid):
                calls.append(("fetch", iid))
                return {"id": iid, "supplierName": "Alsco", "fileId": f"f-{iid}"}

        monkeypatch.setattr(IR, "LoadedInvoiceClient", lambda *a, **k: _Lh())
        monkeypatch.setattr(
            "app.services.spec_dojo.prefetch_replica_reference",
            lambda *a, **k: calls.append(("reference",)) or {},
        )

        def review(db, cdb, vid, iid, **kw):
            calls.append(("review", iid, kw.get("allow_sensei")))
            return {"invoice_id": iid, "issues": [], "suggestions": []}

        monkeypatch.setattr(IR, "review_invoice", review)
        IR.review_invoices(None, None, "v-1", ["a", "b"])
        assert calls == [
            ("fetch", "a"),
            ("reference",),
            ("review", "a", True),
            ("fetch", "b"),
            ("reference",),
            ("review", "b", True),
        ]


class TestTheWholeBacklogIsListed:
    """Loaded lists 200 drafts a page. Reading only the first page left 390
    of Applegum's 590 drafts unseen (16 Oct 2026)."""

    def test_every_page_is_read(self):
        pages = {
            0: [{"id": f"a{n}", "createdAt": "2026-01-02"} for n in range(200)],
            1: [{"id": f"b{n}", "createdAt": "2025-06-01"} for n in range(200)],
            2: [{"id": f"c{n}", "createdAt": "2025-05-25"} for n in range(190)],
        }
        asked = []

        class _Lh:
            def get(self, path):
                page = int(path.split("page=")[1].split("&")[0])
                asked.append(page)
                return pages.get(page, [])

        rows = IR.list_window_invoices(_Lh())
        assert len(rows) == 590 and asked == [0, 1, 2]

    def test_a_full_last_page_asks_once_more_and_stops_on_an_empty_one(self):
        class _Lh:
            def get(self, path):
                page = int(path.split("page=")[1].split("&")[0])
                return [{"id": f"p{page}-{n}"} for n in range(200)] if page == 0 else []

        assert len(IR.list_window_invoices(_Lh())) == 200
