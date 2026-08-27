"""invoice_review — the replica as the single suggestion engine.

These tests port the old consolidator gate scenarios onto the service:
working values from Loaded's draft, replica sidecar, unified suggestions
with explanations, blocking issues, the recorded accept trail, and the
autopilot policy (auto-accept everything, receive only when ready).
All reference data injected — no network, no LLM.
"""

from app.services import invoice_review as IR
from app.services import venue_autopilot as VA
from app.services.invoice_review import (
    apply_suggestion,
    auto_accept_all,
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
        row = {
            "line_id": "rep-0",
            "unit_id": "u-5l",
            "confidence": "high",
            "why": "sibling lines print 5L at the same price",
        }
        row.update(over)
        return lambda payload: {"lines": [row]}

    def test_resolver_pick_is_a_suggestion_and_the_gate_gets_it(self):
        data = self._sizeless_case(self._resolver_answer())
        s = next(s for s in data["suggestions"] if s["field"] == "unit")
        assert s["apply"]["linked_unit_id"] == "u-5l"
        assert s["confidence"] == "high"
        assert "sibling lines" in s["explanation"]
        # the line itself is untouched — an offer, not a resolution
        assert data["lines"][0]["linked_unit_id"] is None
        issue = next(i for i in data["issues"] if i["code"] == "unit_missing")
        # HIGH confidence rides in the gate action, so autopilot's
        # receive_without_unit gate applies exactly what the suggestion shows
        assert issue["action"]["kind"] == "guess_unit"
        assert issue["action"]["apply"]["linked_unit_id"] == "u-5l"

    def test_medium_confidence_suggests_but_the_gate_gets_no_apply(self):
        data = self._sizeless_case(self._resolver_answer(confidence="medium"))
        s = next(s for s in data["suggestions"] if s["field"] == "unit")
        assert s["confidence"] == "medium"
        issue = next(i for i in data["issues"] if i["code"] == "unit_missing")
        assert "apply" not in issue["action"]

    def test_low_confidence_stays_silent(self):
        data = self._sizeless_case(self._resolver_answer(confidence="low"))
        assert not [s for s in data["suggestions"] if s["field"] == "unit"]
        assert "unit_missing" in _issue_codes(data)

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
        ask = lambda payload: {  # noqa: E731
            "lines": [
                {
                    "line_id": "rep-1",
                    "unit_id": "u-each",
                    "confidence": confidence,
                    "why": "a service charge with no physical size",
                }
            ]
        }
        return _review(
            detail=det, extraction=ext, reference=REFERENCE(unit_resolver_ask=ask)
        )

    def test_a_copy_only_lines_pick_rides_in_the_add_line(self):
        # A copy-only line never meets the paired-line unit walk, so the
        # resolver's decisive pick died as metadata: the added line arrived
        # unitless and its blocker demanded a decision the resolver had
        # already made (Aitkens 173670 freight, 19 Aug 2026).
        data = self._copy_only_freight_case()
        s = next(s for s in data["suggestions"] if s["kind"] == "add_line")
        assert s["payload"]["linked_unit_id"] == "u-each"
        assert s["payload"]["unit"] == "Each"
        assert "delivered as 'Each'" in s["explanation"]
        assert "service charge" in s["explanation"]
        # accepting the suggestion lands the line complete
        from app.services import invoice_review as IR2

        IR2.apply_suggestion(data, s)
        added = next(
            ln for ln in data["lines"] if str(ln.get("id")) == str(s["line_id"])
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
        data = self._sizeless_case(lambda p: {"lines": []}, unit="EA")
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
        # The strike remedy is FOLDED onto the blocker (one row, one Accept)
        # and gated for autopilot; the suggestion list no longer repeats it.
        assert issue["action"]["kind"] == "strike"
        assert issue["action"]["apply"] == {"struck": True}
        assert issue["gate"] == "auto_strike_phantom_lines"
        assert not [s for s in data["suggestions"] if s["kind"] == "strike"]
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
        dup = next(i for i in data["issues"] if i["code"] == "duplicate_invoice")
        # The delete recommendation rides ON the blocker now (one row, one
        # Accept), behind its own gate — autopilot's destructive writes each
        # answer to a separate toggle.
        assert dup["action"]["kind"] == "delete_invoice"
        assert dup["action"]["payload"]["duplicate_of_invoice_id"] == "inv-old"
        assert dup["action"]["payload"]["type"] == "delete_invoice"
        assert dup["gate"] == "auto_delete_duplicates"
        assert not [s for s in data["suggestions"] if s["kind"] == "delete_invoice"]

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
    class _VenueDb:
        """A db stub answering the venue-settings lookup only."""

        class _Venue:
            def __init__(self, settings):
                self.id = "v-1"
                self.invoice_autopilot = settings

        def __init__(self, settings):
            self._venue = self._Venue(settings)

        def query(self, *_a):
            return self

        def filter(self, *_a, **_k):
            return self

        def first(self):
            return self._venue

    def test_no_po_blocks_under_the_batch_default(self):
        det = DETAIL(linkedPurchaseOrderId=None, purchaseOrderNumber=None)
        data = _review(detail=det)
        issue = next(i for i in data["issues"] if i["code"] == "po_missing")
        assert issue["blocking"] is True
        # the card names the toggle that would let autopilot past this
        assert issue["gate"] == "receive_without_po"
        # ...and offers Accept: a record-only decision to receive without a
        # PO (the one-button doctrine — no more instruction with no button)
        assert issue["action"] == {"kind": "receive_without_po", "payload": {}}
        assert data["confidence"] == "needs_review"
        # linking an order clears it (clears_when)
        data["linked_purchase_order_id"] = "po-9"
        assert compute_confidence(data) == "ready"

    def test_the_card_tells_the_story_autopilot_acts_on(self):
        # No explicit policy → derived from the VENUE's receive_without_po
        # gate. Gate off: the card shows blocked-from-auto-receive naming the
        # toggle — no more "worth knowing" while autopilot silently parks
        # (user report, 18 Aug 2026). Gate on: an honest note.
        det = DETAIL(linkedPurchaseOrderId=None, purchaseOrderNumber=None)
        data = _review(detail=det, db=self._VenueDb({}))
        issue = next(i for i in data["issues"] if i["code"] == "po_missing")
        assert issue["blocking"] is True
        assert issue["gate"] == "receive_without_po"

        data = _review(detail=det, db=self._VenueDb({"receive_without_po": True}))
        issue = next(i for i in data["issues"] if i["code"] == "po_missing")
        assert issue["blocking"] is False
        assert data["confidence"] == "ready"

    def test_linked_po_never_flags(self):
        data = _review()
        assert "po_missing" not in _issue_codes(data)

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
        assert issue["action"] == {"kind": "receive_without_po", "payload": {}}


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


class _VenueDb:
    """Just enough session for review_invoices to read the venue's settings.

    The venue now decides how far Norm may go, so a batch test has to say which
    rung the venue is on — passing `mode=` alone can only ever lower it.
    """

    class _Venue:
        def __init__(self, settings):
            self.id = "v-1"
            self.invoice_autopilot = settings

    def __init__(self, settings):
        self._venue = self._Venue(settings)

    def query(self, *_a):
        return self

    def filter(self, *_a, **_k):
        return self

    def first(self):
        return self._venue


class TestBatchModes:
    def _run(self, monkeypatch, details, extractions, mode, received, gates=None):
        # The venue is on the rung the test is exercising; `mode` is the
        # caller's ceiling, which is how production passes it too.
        db = _VenueDb({"mode": mode, **(gates or {})})
        lh = _BatchLh(details)
        monkeypatch.setattr(IR, "LoadedInvoiceClient", lambda db, cdb, vid: lh)
        monkeypatch.setattr(
            IR,
            "extract_invoice_copies_parallel",
            lambda db, lh_, reqs: [extractions[i] for i in range(len(reqs))],
        )
        monkeypatch.setattr(
            IR, "extraction_instructions", lambda cdb, lh_, det, al=None: "INSTR"
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
        return review_invoices(db, None, "v-1", mode=mode)

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

    def test_a_venue_on_approve_all_is_not_talked_into_receiving(self, monkeypatch):
        """The venue decides; the caller can only ask for less.

        Before this the rung came from the USER who happened to trigger the
        run, so a scheduled task or a chat request could receive at a venue
        that had never opted in. Norm creating stock items and receiving
        invoices somewhere nobody enabled it is the failure worth refusing.
        """
        received = []
        db = _VenueDb({"mode": "approve_all"})
        lh = _BatchLh({"inv-1": DETAIL()})
        monkeypatch.setattr(IR, "LoadedInvoiceClient", lambda d, c, v: lh)
        monkeypatch.setattr(
            IR, "extract_invoice_copies_parallel", lambda d, l_, r: [EXTRACTION()]
        )
        monkeypatch.setattr(
            IR, "extraction_instructions", lambda c, l_, d_, al=None: "INSTR"
        )
        monkeypatch.setattr(
            "app.services.spec_dojo.prefetch_replica_reference",
            lambda d, c, v: REFERENCE(),
        )
        monkeypatch.setattr(
            IR, "do_receive", lambda l_, req: received.append(req) or {"received": True}
        )
        monkeypatch.setattr(IR, "invalidate_conflicting_drafts", lambda *a, **k: None)

        review_invoices(db, None, "v-1", mode="autopilot")

        assert received == []

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
        data, _ = self._run()
        s = next(s for s in data["suggestions"] if s["kind"] == "link_po")
        assert s["apply"]["linked_purchase_order_id"] == self.PO_ID
        assert data["po_reference"]["po_id"] == self.PO_ID
        assert data["po_reference"]["lines"]
        # Cached, NOT applied: nothing is linked, so the projection stays dark
        # rather than reconciling against an order the doc doesn't claim.
        assert data.get("linked_purchase_order_id") is None
        assert data.get("order_date") is None
        assert all(ln.get("quantity_ordered") is None for ln in data["lines"])

    def test_accepting_the_link_projects_with_no_further_fetch(self):
        data, lh = self._run()
        before = len(lh.gets)
        apply_suggestion(
            data, next(s for s in data["suggestions"] if s["kind"] == "link_po")
        )
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

    def test_an_unmatched_product_blocks_and_carries_its_own_create(self):
        data = self._unmatched()
        b = _blocker(data, "item_unmatched", "ld-1")
        assert b is not None and b["blocking"] is True
        assert b["action"] == {
            "kind": "create_item",
            "payload": {"name": "ALPHA DOMUS SYRAH 2022", "group_id": "grp-wine"},
        }
        assert "create it" in b["message"]

    def test_it_names_the_toggle_that_would_let_norm_do_it(self):
        assert _blocker(self._unmatched(), "item_unmatched")["gate"] == (
            "auto_create_items"
        )

    def test_it_appears_in_exactly_one_list(self):
        """The whole point: one decision, one row."""
        assert self._create_sugg(self._unmatched()) is None

    def test_no_stock_group_means_no_one_click_create(self):
        # Loaded's create REQUIRES a group; a name alone is not actionable, so
        # the blocker stays but offers no button — the manual form instead.
        data = self._unmatched(group=None)
        assert self._create_sugg(data) is None
        assert _blocker(data, "item_unmatched").get("action") is None

    def test_a_matched_line_is_never_offered_creation(self):
        # The stock salmon invoice: Loaded's own code match linked it.
        assert self._create_sugg(_review()) is None


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
        suggestions, issues = IR.fold_remedies_into_blockers([], self._issues())
        assert [s for s in suggestions if s.get("kind") == "unlink_po"] == []
        assert [i for i in issues if i["code"] == "po_supplier_mismatch"]

    def test_it_does_not_stop_the_receive(self):
        data = {
            **self._mismatched(),
            "issues": self._issues(),
            "suggestion_actions": [],
        }
        assert IR.compute_confidence(data) == "ready"


class TestAFoldedRemedyAppearsOnceAndActuallyClearsIt:
    """`unlink_po` sat in Suggested changes next to its own blocker — and
    because the issue id never equals the suggestion id and the issue carries
    no clears_when, ACCEPTING it did not clear the blocker. Autopilot
    auto-accepted the unlink and then parked the invoice citing the thing it
    had just remedied.
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
        }

    def test_the_remedy_moves_onto_its_blocker(self):
        suggestions, issues = IR.fold_remedies_into_blockers(
            [self._unlink()], self._doubled()
        )
        assert suggestions == []
        blocker = next(i for i in issues if i["code"] == "po_doubled_up")
        assert blocker["action"]["kind"] == "unlink_po"
        assert blocker["action"]["apply"]["linked_purchase_order_id"] is None

    def test_no_toggle_authorises_it(self):
        """An order already invoiced by a sibling is a judgement, not a create
        — no venue setting should be able to wave it through."""
        _s, issues = IR.fold_remedies_into_blockers([self._unlink()], self._doubled())
        assert issues[0].get("gate") is None

    def test_applying_it_clears_the_blocker(self):
        _s, issues = IR.fold_remedies_into_blockers([self._unlink()], self._doubled())
        data = {
            "lines": [],
            "issues": issues,
            "suggestion_actions": [
                {"suggestion_id": "po_doubled_up", "action": "accepted", "by": "user"}
            ],
        }
        assert IR.compute_confidence(data) == "ready"


class TestAUnitBlockerSaysWhichUnitWillBeUsed:
    """The blocker read "no unit could be determined (nothing recognisable on
    the copy and no unit on the Loaded variant) — set the unit before
    receiving" while the dropdown plainly showed 750 mL. Both halves were
    "true" of different objects: the replica's own resolution failed, while the
    working line kept Loaded's unit. The user was asked to do something already
    done, on the strength of a claim that was false for the row in front of
    them.
    """

    def _issue(self, code="unit_missing"):
        return {
            "id": f"{code}:ld-1",
            "code": code,
            "blocking": True,
            "line_id": "ld-1",
            "message": "line 1 'SOHO Harry Rose 2025': no unit could be read from the copy",
        }

    def test_it_names_the_unit_loaded_supplied(self):
        data = {"lines": [{"id": "ld-1", "unit": "750 mL", "linked_unit_id": "u-750"}]}
        issues = [self._issue()]
        IR.name_the_unit_in_use(data, issues)
        assert issues[0]["message"].endswith("— using Loaded's '750 mL'")
        assert "no unit on the Loaded variant" not in issues[0]["message"]

    def test_it_becomes_its_own_decision(self):
        """A unit Loaded supplied is not "no unit" — it is a different question
        with a different answer, so it gets its own toggle."""
        data = {"lines": [{"id": "ld-1", "unit": "750 mL", "linked_unit_id": "u-750"}]}
        issues = [self._issue()]
        IR.name_the_unit_in_use(data, issues)
        assert issues[0]["gate"] == VA.RECEIVE_WITH_UNCONFIRMED_UNIT

    def test_with_nothing_anywhere_the_instruction_is_the_honest_ending(self):
        data = {"lines": [{"id": "ld-1", "unit": None, "linked_unit_id": None}]}
        issues = [self._issue()]
        IR.name_the_unit_in_use(data, issues)
        assert issues[0]["message"].endswith("— set one before receiving")

    def test_an_unreadable_copy_unit_is_named_too(self):
        data = {"lines": [{"id": "ld-1", "unit": "Kilo", "linked_unit_id": "u-kilo"}]}
        issues = [self._issue("unit_unconfirmed")]
        IR.name_the_unit_in_use(data, issues)
        assert "using Loaded's 'Kilo'" in issues[0]["message"]

    def test_a_catalogue_supplied_unit_is_credited_to_the_catalogue(self):
        # "using Loaded's '700 mL'" was false when Norm's own catalogue
        # answered the unit (HIGHLAND PARK 15, 4366904, 19 Aug 2026) — the
        # replica now stamps the tier that supplied the stand-in.
        data = {"lines": [{"id": "ld-1", "unit": "700 mL", "linked_unit_id": "u-700"}]}
        issue = self._issue("unit_unconfirmed")
        issue["data"] = {"unit_chosen_by": "catalogue", "unit_id": "u-700"}
        issues = [issue]
        IR.name_the_unit_in_use(data, issues)
        assert issues[0]["message"].endswith("— Norm's catalogue answers '700 mL'")

    def test_the_stamp_is_dropped_when_the_working_line_shows_another_unit(self):
        # The replica's stand-in was catalogue-sourced, but the WORKING line
        # shows a different unit — that one is Loaded's, and crediting the
        # catalogue for it would be the same lie in the other direction.
        data = {"lines": [{"id": "ld-1", "unit": "Each", "linked_unit_id": "u-each"}]}
        issue = self._issue("unit_unconfirmed")
        issue["data"] = {"unit_chosen_by": "catalogue", "unit_id": "u-700"}
        issues = [issue]
        IR.name_the_unit_in_use(data, issues)
        assert issues[0]["message"].endswith("— using Loaded's 'Each'")

    def test_a_printed_charge_word_is_named_as_the_fallback_it_is(self):
        data = {"lines": [{"id": "ld-1", "unit": "each", "linked_unit_id": "u-each"}]}
        issue = self._issue("unit_unconfirmed")
        issue["data"] = {"unit_chosen_by": "printed", "unit_id": "u-each"}
        issues = [issue]
        IR.name_the_unit_in_use(data, issues)
        assert issues[0]["message"].endswith(
            "— falling back to the printed column's 'each'"
        )


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

    def test_an_unticked_gate_leaves_the_blocker_standing(self, monkeypatch):
        from app.routers import invoice_fixes as IF

        # Recorded rather than raised: apply_open_gates swallows exceptions by
        # design (a failed create must park the invoice, not crash the batch),
        # so an assertion thrown in here would be caught and the test would
        # pass for the wrong reason.
        calls: list = []
        monkeypatch.setattr(
            IF, "create_stock_brand", lambda *a, **k: calls.append(a) or {}
        )
        data, settings = self._data()

        assert IR.apply_open_gates(None, None, "v-1", "inv-1", data, settings) == []

        assert calls == [], "autopilot created a brand the venue had not allowed"
        assert IR.compute_confidence(data) == "needs_review"

    def test_a_ticked_gate_creates_it_and_the_invoice_becomes_ready(self, monkeypatch):
        from app.routers import invoice_fixes as IF

        monkeypatch.setattr(
            IF,
            "create_stock_brand",
            lambda body, db, cdb, user: {
                "brand_id": "brand-new",
                "brand_name": body.name,
            },
        )
        data, settings = self._data(VA.AUTO_CREATE_BRANDS)

        done = IR.apply_open_gates(None, None, "v-1", "inv-1", data, settings)

        assert done == ["created brand 'BIOZYME'"]
        line = next(ln for ln in data["lines"] if ln["id"] == "ld-1")
        assert line["linked_brand_id"] == "brand-new"
        assert IR.compute_confidence(data) == "ready"

    def test_norm_signs_its_own_work(self, monkeypatch):
        """The action log is what the card shows and what the readiness report
        reads, so an unattended create has to appear in it as Norm's."""
        from app.routers import invoice_fixes as IF

        monkeypatch.setattr(
            IF,
            "create_stock_brand",
            lambda body, db, cdb, user: {"brand_id": "b-1", "brand_name": body.name},
        )
        data, settings = self._data(VA.AUTO_CREATE_BRANDS)
        IR.apply_open_gates(None, None, "v-1", "inv-1", data, settings)

        act = next(
            a
            for a in data["suggestion_actions"]
            if a["suggestion_id"] == "brand_unknown:ld-1"
        )
        assert act["by"] == "norm" and act["action"] == "accepted"

    def test_a_failed_create_parks_the_invoice_rather_than_receiving_it(
        self, monkeypatch
    ):
        """Half-built is the one outcome worse than stopping: the line would
        reach Loaded without the brand it names and be refused there anyway."""
        from app.routers import invoice_fixes as IF

        def _boom(*a, **k):
            raise RuntimeError("Loaded said no")

        monkeypatch.setattr(IF, "create_stock_brand", _boom)
        data, settings = self._data(VA.AUTO_CREATE_BRANDS)

        assert IR.apply_open_gates(None, None, "v-1", "inv-1", data, settings) == []
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
        IR.apply_open_gates(None, None, "v-1", "inv-1", data, settings)
        assert IR.compute_confidence(data) == "needs_review"


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
            "suggestions": [],
            "suggestion_actions": [],
            "issues": [
                {
                    "id": "supplier_unresolved",
                    "code": "supplier_unresolved",
                    "blocking": True,
                    "gate": VA.AUTO_CREATE_SUPPLIERS,
                    "message": "no Loaded supplier matches 'SERVICE FOODS LTD'",
                    "action": {
                        "kind": "create_supplier",
                        "payload": {"supplier_name": "SERVICE FOODS LTD"},
                    },
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
        class _Lh:
            def get(self, _path):
                return suppliers

        monkeypatch.setattr(IR, "LoadedInvoiceClient", lambda d, c, v: _Lh())

    def test_a_near_match_is_a_human_decision(self, monkeypatch):
        from app.routers import invoice_fixes as IF

        calls: list = []
        self._lh(monkeypatch, [{"id": "sup-1", "name": "SERVICE FOODS AUCKLAND"}])
        monkeypatch.setattr(
            IF, "create_supplier", lambda *a, **k: calls.append(a) or {}
        )
        data, settings = self._data()

        assert IR.apply_open_gates(None, None, "v-1", "inv-1", data, settings) == []

        assert calls == [], "autopilot created a supplier that already exists"
        assert data.get("linked_supplier_id") is None

    def test_a_genuinely_new_supplier_is_created(self, monkeypatch):
        from app.routers import invoice_fixes as IF

        self._lh(monkeypatch, [{"id": "sup-9", "name": "Hancocks Wine & Spirits"}])
        monkeypatch.setattr(
            IF,
            "create_supplier",
            lambda body, db, cdb, user: {
                "supplier_id": "sup-new",
                "supplier_name": body.name,
            },
        )
        data, settings = self._data()

        done = IR.apply_open_gates(None, None, "v-1", "inv-1", data, settings)

        assert done == ["created supplier 'SERVICE FOODS LTD'"]
        assert data["linked_supplier_id"] == "sup-new"


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
        assert b["action"] == {
            "kind": "create_brand",
            "payload": {"brand_name": "BIOZYME"},
        }
        assert b["gate"] == "auto_create_brands"

    def test_it_appears_in_exactly_one_list(self):
        assert self._brand_sugg(self._with_brand()) is None

    def test_an_unknown_brand_is_not_ready_to_receive(self):
        """The bug this closes: confidence read "ready" with an unknown brand,
        so autopilot called do_receive and collected a 400 every time."""
        assert self._with_brand()["confidence"] == "needs_review"

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
        data = _review(detail=DETAIL(fileId=None))
        issue = next(i for i in data["issues"] if i["code"] == "no_copy_attached")
        assert issue["gate"] == "auto_delete_unreadable"
        assert issue["action"]["kind"] == "delete_unreadable"
        assert issue["action"]["payload"]["type"] == "delete_invoice"
        assert issue["action"]["payload"]["invoice_id"] == "inv-1"

        data = _review(extraction={"error": "LLM down"})
        issue = next(i for i in data["issues"] if i["code"] == "copy_unreadable")
        assert issue["gate"] == "auto_delete_unreadable"
        assert issue["action"]["kind"] == "delete_unreadable"
        assert "delete the draft" in issue["message"]

    def test_not_an_invoice_carries_its_delete_action(self):
        ext = EXTRACTION(document_type="statement", lines=[])
        data = _review(extraction=ext)
        issue = next(i for i in data["issues"] if i["code"] == "not_an_invoice")
        assert issue["gate"] == "auto_delete_non_invoices"
        assert issue["action"]["kind"] == "delete_non_invoice"
        assert issue["action"]["payload"]["invoice_id"] == "inv-1"
        assert "delete the draft" in issue["message"]

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
        issue = next(i for i in data["issues"] if i["code"] == "totals_inconsistent")
        # fallback Accept + gate on the blocker itself
        assert issue["action"]["kind"] == "receive_unreconciled_totals"
        assert issue["gate"] == "receive_unreconciled_totals"
        s = next(
            s
            for s in data["suggestions"]
            if s["field"] == "total_cost" and s.get("resolves")
        )
        assert s["proposed"] == 20.0
        assert s["resolves"] == issue["id"]
        assert s["confidence"] == "high"
        assert "2 x 10.00" in s["explanation"]

    def test_totals_diagnosis_failure_leaves_the_fallback_only(self):
        data = self._misread_totals_case(lambda p: {})
        issue = next(i for i in data["issues"] if i["code"] == "totals_inconsistent")
        assert issue["action"]["kind"] == "receive_unreconciled_totals"
        assert not [s for s in data["suggestions"] if s.get("resolves")]

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

    def test_indecisive_pairing_keeps_the_blockers(self):
        data = self._ambiguous_case(lambda p: {})
        assert "ambiguous_pairing" in _issue_codes(data)


class TestDestructiveGates:
    """The delete gates: autopilot's only destructive writes — never without
    the venue's toggle, always short-circuiting, always recorded."""

    def _data(self, code, kind, gate):
        return {
            "invoice_id": "inv-1",
            "lines": [],
            "suggestions": [],
            "suggestion_actions": [],
            "issues": [
                {
                    "id": code,
                    "code": code,
                    "blocking": True,
                    "gate": gate,
                    "action": {
                        "kind": kind,
                        "payload": {
                            "type": "delete_invoice",
                            "invoice_id": "inv-1",
                            "summary": "junk draft",
                        },
                    },
                    "message": "x",
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
            IR.apply_open_gates(None, None, "v-1", "inv-1", data, self._settings())
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
            IF, "create_stock_brand", lambda *a, **k: creates.append(a) or {}
        )
        monkeypatch.setattr(IR, "LoadedInvoiceClient", lambda *a, **k: object())
        data = self._data(
            "duplicate_invoice", "delete_invoice", VA.AUTO_DELETE_DUPLICATES
        )
        # a create the walk would normally run — must be skipped by the delete
        data["issues"].append(
            {
                "id": "brand_unknown:ld-1",
                "code": "brand_unknown",
                "blocking": True,
                "line_id": "ld-1",
                "gate": VA.AUTO_CREATE_BRANDS,
                "action": {"kind": "create_brand", "payload": {"brand_name": "X"}},
                "message": "x",
            }
        )
        data["lines"] = [{"id": "ld-1"}]
        done = IR.apply_open_gates(
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
        # recorded against the issue, actor norm
        rec = data["suggestion_actions"][-1]
        assert rec["suggestion_id"] == "duplicate_invoice" and rec["by"] == "norm"

    def test_strike_and_record_only_gates(self):
        data = {
            "invoice_id": "inv-1",
            "lines": [{"id": "ld-9", "description": "Phantom"}],
            "suggestions": [],
            "suggestion_actions": [],
            "issues": [
                {
                    "id": "loaded_line_not_on_copy:ld-9",
                    "code": "loaded_line_not_on_copy",
                    "blocking": True,
                    "line_id": "ld-9",
                    "gate": VA.AUTO_STRIKE_PHANTOM_LINES,
                    "action": {"kind": "strike", "apply": {"struck": True}},
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
                    "gate": VA.RECEIVE_UNRECONCILED_TOTALS,
                    "action": {"kind": "receive_unreconciled_totals", "payload": {}},
                    "message": "x",
                },
            ],
        }
        done = IR.apply_open_gates(
            None,
            None,
            "v-1",
            "inv-1",
            data,
            self._settings(
                VA.AUTO_STRIKE_PHANTOM_LINES, VA.RECEIVE_UNRECONCILED_TOTALS
            ),
        )
        assert data["lines"][0]["struck"] is True
        assert any("struck" in d for d in done)
        assert any("receive unreconciled totals" in d for d in done)
        assert IR.compute_confidence(data) == "ready"


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
        """49.48 / 0.92 = 53.7826… -> 53.78, and 0.92 x 53.78 restores 49.48."""
        s = next(s for s in self._doc()["suggestions"] if s.get("field") == "unit_cost")
        assert s["proposed"] == 53.78
        assert "$49.48" in s["explanation"] and "$49.32" in s["explanation"]
        assert s["apply"] == {"unit_cost": 53.78}

    def test_accepting_the_fix_makes_the_line_match_the_invoice(self):
        data = self._doc()
        s = next(s for s in data["suggestions"] if s.get("field") == "unit_cost")
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
