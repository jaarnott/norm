"""Tests for the review_and_receive_invoices consolidator function_code.

The canonical code lives in config/consolidators/review_and_receive_invoices.py
and is synced into the config DB. These tests exec it under the REAL sandbox
namespace (_SAFE_BUILTINS/_SAFE_MODULES) so any use of a builtin the sandbox
doesn't provide fails here instead of in production.

Fixtures mirror the live LoadedHub JSON shapes captured on 16 Jul 2026.
"""

import pathlib

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

FUNCTION_CODE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "review_and_receive_invoices.py"
).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class Api:
    """Scriptable fake for call_api / extract_document with call recording."""

    def __init__(
        self,
        invoices,
        details,
        pos=None,
        pdfs=None,
        receive_error=None,
        po_list=None,
        received_feed=None,
        item_matches=None,
    ):
        self.invoices = invoices
        self.details = details
        self.pos = pos or {}
        self.pdfs = pdfs or {}
        self.receive_error = receive_error
        # For PO-number resolution (owned by the consolidator): the open-PO list
        # and the received-invoice feed. Default empty → a number resolves to no
        # Loaded PO (po_unresolved), matching an invoice with no matchable PO.
        self.po_list = po_list or []
        self.received_feed = received_feed or []
        # Scripted result for the norm.match_stock_items LLM function (like the
        # extract_document fake): {line_id: {matched_item, suggested_name,
        # suggested_group_id}}. Default empty = matcher found nothing.
        self.item_matches = item_matches or {}
        self.match_calls = []
        self.received_bodies = []

    def call_api(self, connector, action, params=None):
        params = params or {}
        if action == "list_stock_invoices":
            return self.invoices
        if action == "get_invoice_detail":
            return self.details[params["invoice_id"]]
        if action == "get_stock_purchase_order":
            return self.pos[params["purchase_order_id"]]
        if action == "list_purchase_orders":
            return self.po_list
        if action == "list_received_invoices":
            return self.received_feed
        if action == "match_stock_items":
            self.match_calls.append(params.get("lines") or [])
            return {"suggestions": self.item_matches}
        if action == "receive_invoice":
            if self.receive_error:
                return {"error": self.receive_error}
            self.received_bodies.append(params["invoice"])
            return dict(params["invoice"])
        raise AssertionError(f"unexpected call_api action: {action}")

    def extract_document(
        self, connector, action, params=None, schema=None, instructions=None
    ):
        assert action == "download_invoice_file"
        return self.pdfs[(params or {})["file_id"]]


def run_consolidator(api, **params):
    namespace = {
        "__builtins__": _SAFE_BUILTINS,
        **_SAFE_MODULES,
        "extract_document": api.extract_document,
    }
    exec(FUNCTION_CODE, namespace)
    # Default to approve_fixes (the pre-modes behaviour) so existing assertions
    # about auto-receiving hold; mode-specific tests override this.
    defaults = {
        "today": "2026-07-16",
        "venue": "Bessie",
        "mode": "approve_fixes",
        **params,
    }
    return namespace["run"](defaults, api.call_api, lambda m: None)


# ---------------------------------------------------------------------------
# Fixtures — modelled on the verified Akaroa/Ocean's North shapes
# ---------------------------------------------------------------------------

PO_ID = "4c69ac57-b8b2-4524-d301-08ded2d852f5"
INV_ID = "277c9b6e-6d88-492e-8194-08ded2d24c70"
FILE_ID = "1fcc07c5-eebf-4b0f-9c1d-6ed59eae5894"
ITEM_SALMON = "53de28f9-b7b7-4794-930b-a8b0f650db63"
UNIT_KILO = "df535968-bab0-4f07-86e2-07354483935d"


def make_line(**over):
    line = {
        "id": "line-1",
        "code": "PBO0.7-0.99",
        "description": "SALMON FILLET",
        "unit": "Kilo",
        "brand": None,
        "linkedBrandId": None,
        "quantityOrdered": 5.0,
        "quantityReceived": 4.95,
        "unitCost": 44.40,
        "totalCost": 219.78,
        "taxAmount": 32.967,
        "linkedItemId": ITEM_SALMON,
        "linkedUnitId": UNIT_KILO,
        "linkedUnitRatio": 1,
        "itemMatchedOn": "Code",
        "deletedAt": None,
    }
    line.update(over)
    return line


def make_invoice(**over):
    inv = {
        "id": INV_ID,
        "referenceNumber": "F55755100",
        "supplierName": "Akaroa Salmon",
        "linkedSupplierId": "supplier-akaroa",
        "purchaseOrderNumber": "PO#1520987",
        "linkedPurchaseOrderId": PO_ID,
        "isReceived": False,
        "deletedAt": None,
        "subtotal": 219.78,
        "taxAmount": 32.97,
        "total": 252.75,
        "fileId": FILE_ID,
        "source": "Email",
        "lines": [make_line()],
    }
    inv.update(over)
    return inv


def make_po_line(**over):
    line = {
        "id": "po-line-1",
        "itemId": ITEM_SALMON,
        "itemName": "SALMON FILLET",
        "itemCode": "PBO0.7-0.99",
        "unitId": UNIT_KILO,
        "unitName": "Kilo",
        "unitRatio": 1,
        "unitCost": 44.40,
        "unitCostOrdered": 44.40,
        "quantityOrdered": 5.0,
        "quantityReceived": 5.0,
        "taxPercent": 0.15,
    }
    line.update(over)
    return line


def make_po(**over):
    po = {
        "id": PO_ID,
        "orderNumber": "1520987",
        "supplierId": "supplier-akaroa",
        "supplierName": "Akaroa Salmon",
        "isReceived": False,
        "status": "Outstanding",
        "lines": [make_po_line()],
    }
    po.update(over)
    return po


def make_pdf(**over):
    pdf = {
        "supplier_name": "Ahi Mokopuna Limited Partnership",
        "invoice_number": "F55755100",
        "invoice_date": "07 Jul 2026",
        "purchase_order_number": "1520987",
        "lines": [
            {
                "code": "PBO0.7-0.99",
                "description": "Chilled Skin On Fillet Bone Out 0.7-0.99kg",
                "quantity": 4.95,
                "unit": "Kg",
                "unit_of_measure": "Kilo",
                "unit_price_ex_tax": 44.40,
                "line_total_ex_tax": 219.78,
            }
        ],
        "charges": [],
        "subtotal_ex_tax": 219.78,
        "tax_amount": 32.97,
        "total_incl_tax": 252.75,
    }
    pdf.update(over)
    return pdf


def api_for(invoice, po=None, pdf=None, **api_kwargs):
    return Api(
        invoices=[invoice],
        details={invoice["id"]: invoice},
        pos={PO_ID: po if po is not None else make_po()},
        pdfs={FILE_ID: pdf if pdf is not None else make_pdf()},
        **api_kwargs,
    )


def sole_skip(result):
    assert result["summary"] == {"received": 0, "skipped": 1}, result
    return result["skipped"][0]


# ---------------------------------------------------------------------------
# Receiving path
# ---------------------------------------------------------------------------


class TestReceives:
    def test_perfect_invoice_is_received(self):
        api = api_for(make_invoice())
        result = run_consolidator(api)
        assert result["summary"] == {"received": 1, "skipped": 0}
        assert result["received"][0]["outcome"] == "received"
        assert len(api.received_bodies) == 1
        body = api.received_bodies[0]
        assert body["isReceived"] is True
        assert body["receivedAt"]

    def test_approve_all_never_writes(self):
        api = api_for(make_invoice())
        result = run_consolidator(api, mode="approve_all")
        assert result["received"][0]["outcome"] == "awaiting your approval"
        assert api.received_bodies == []

    def test_quantity_variance_allowed_when_pdf_confirms(self):
        # PO ordered 5.000, invoice billed 4.950 (catch weight) — PDF says 4.95.
        api = api_for(make_invoice())
        result = run_consolidator(api)
        assert result["summary"]["received"] == 1

    def test_two_cent_total_difference_is_tolerated(self):
        # Loaded itself shows a "Rounding" line — ≤2c counts as matching.
        api = api_for(make_invoice(total=252.77))
        pdf_api = api_for(
            make_invoice(total=252.77), pdf=make_pdf(total_incl_tax=252.77)
        )
        assert run_consolidator(pdf_api)["summary"]["received"] == 1
        del api

    def test_already_received_and_deleted_are_not_reviewed(self):
        api = Api(
            invoices=[
                make_invoice(id="a", isReceived=True),
                make_invoice(id="b", deletedAt="2026-07-01T00:00:00Z"),
            ],
            details={},
        )
        result = run_consolidator(api)
        assert result["reviewed"] == 0
        assert result["summary"] == {"received": 0, "skipped": 0}


# ---------------------------------------------------------------------------
# Skip gates — each failure must carry the exact numbers
# ---------------------------------------------------------------------------


class TestSkips:
    def test_credit_note_is_skipped(self):
        api = api_for(make_invoice(total=-25.30, subtotal=-22.0, taxAmount=-3.30))
        verdict = sole_skip(run_consolidator(api))
        assert any("Credit note" in r for r in verdict["reasons"])
        assert api.received_bodies == []

    def test_unlinked_invoice_is_skipped_with_po_hint(self):
        api = api_for(make_invoice(linkedPurchaseOrderId=None))
        verdict = sole_skip(run_consolidator(api))
        reason = " ".join(verdict["reasons"])
        assert "Not linked to a purchase order" in reason
        assert "PO#1520987" in reason

    def _freight_case(self, charge_amt=6.50):
        freight = make_line(
            id="line-2", code="FGT001", description="FREIGHT - FOOD",
            linkedItemId="item-freight", linkedUnitId="unit-each",
            quantityReceived=1, unitCost=6.50, totalCost=6.50,
        )
        inv = make_invoice(lines=[make_line(), freight], subtotal=226.28, taxAmount=33.94, total=260.22)
        pdf = make_pdf(
            charges=[{"description": "Freight (ex GST)", "amount_ex_tax": charge_amt}],
            subtotal_ex_tax=226.28, tax_amount=33.95, total_incl_tax=260.23,
        )
        return api_for(inv, pdf=pdf)

    def test_freight_line_reconciles_with_the_copy_charge(self):
        # A freight line on the Loaded invoice is billed as a separate CHARGE on
        # the copy ("Freight (ex GST)") — they reconcile by the shared word
        # "freight", so the one amount is NOT double-flagged (neither "line not
        # found" nor "charge with no matching invoice line") and it receives.
        result = run_consolidator(self._freight_case())
        assert result["summary"] == {"received": 1, "skipped": 0}

    def test_freight_amount_discrepancy_flags_once(self):
        # If the freight line's amount differs from the copy's charge, that's a
        # real discrepancy — flagged ONCE (not as two separate problems).
        verdict = sole_skip(run_consolidator(self._freight_case(charge_amt=9.00)))
        freight_reasons = [
            r for r in verdict["reasons"] if "FREIGHT" in r.upper() or "Freight" in r
        ]
        assert len(freight_reasons) == 1
        assert "does not equal" in freight_reasons[0]

    def test_po_price_difference_does_not_block(self):
        # User decision: PO prices move between ordering and invoicing — the
        # attached invoice document is the source of truth for what's billed.
        api = api_for(make_invoice(), po=make_po(lines=[make_po_line(unitCost=42.00)]))
        result = run_consolidator(api)
        assert result["summary"] == {"received": 1, "skipped": 0}

    def test_po_unit_difference_does_not_block(self):
        # The PO is not compared line-by-line at all — only the copy is.
        api = api_for(
            make_invoice(),
            po=make_po(lines=[make_po_line(unitId="unit-gram", unitName="Gram")]),
        )
        result = run_consolidator(api)
        assert result["summary"] == {"received": 1, "skipped": 0}

    def test_invoice_lines_not_on_po_do_not_block(self):
        # Invoice and PO may legitimately differ — an empty PO line set is fine
        # as long as the copy confirms every line.
        api = api_for(make_invoice(), po=make_po(lines=[]))
        result = run_consolidator(api)
        assert result["summary"] == {"received": 1, "skipped": 0}

    def test_delivered_unit_differs_from_copy_blocks(self):
        # The copy's DELIVERED unit (guideline-derived) differs from Loaded's — a
        # real mismatch that blocks. The literal printed unit column is not compared.
        pdf = make_pdf()
        pdf["lines"][0] = dict(pdf["lines"][0], unit_of_measure="Litre")  # inv is Kilo
        api = api_for(make_invoice(), pdf=pdf)
        verdict = sole_skip(run_consolidator(api))
        assert any("delivered unit is 'Litre'" in r for r in verdict["reasons"])

    def test_unrecognised_copy_unit_is_not_checked(self):
        # "5.6 KG" is a pack descriptor, not a recognisable unit — a confident
        # comparison is impossible, so it must not block.
        pdf = make_pdf()
        pdf["lines"][0] = dict(pdf["lines"][0], unit="5.6 KG")
        api = api_for(make_invoice(), pdf=pdf)
        result = run_consolidator(api)
        assert result["summary"] == {"received": 1, "skipped": 0}

    def test_po_supplier_mismatch(self):
        api = api_for(
            make_invoice(),
            po=make_po(supplierId="someone-else", supplierName="Wrong Supplier Ltd"),
        )
        verdict = sole_skip(run_consolidator(api))
        assert any("does not match invoice supplier" in r for r in verdict["reasons"])

    def test_new_stock_item_blocks(self):
        api = api_for(make_invoice(lines=[make_line(linkedItemId=None)]))
        verdict = sole_skip(run_consolidator(api))
        assert any("would be created as NEW" in r for r in verdict["reasons"])
        assert any(
            "stock item on line 'SALMON FILLET'" in r for r in verdict["reasons"]
        )

    def test_new_unit_blocks(self):
        api = api_for(make_invoice(lines=[make_line(linkedUnitId=None)]))
        verdict = sole_skip(run_consolidator(api))
        assert any("unit 'Kilo' on line" in r for r in verdict["reasons"])

    def test_new_brand_blocks(self):
        api = api_for(
            make_invoice(lines=[make_line(brand="Sneaky Brand", linkedBrandId=None)])
        )
        verdict = sole_skip(run_consolidator(api))
        assert any("brand 'Sneaky Brand' on line" in r for r in verdict["reasons"])

    def test_known_brand_passes(self):
        api = api_for(
            make_invoice(
                lines=[make_line(brand="Akaroa", linkedBrandId="brand-akaroa")]
            )
        )
        result = run_consolidator(api)
        assert result["summary"] == {"received": 1, "skipped": 0}

    def test_line_arithmetic_failure(self):
        api = api_for(make_invoice(lines=[make_line(totalCost=200.00)]))
        verdict = sole_skip(run_consolidator(api))
        assert any("$219.78" in r and "$200.00" in r for r in verdict["reasons"])

    def test_subtotal_mismatch_reports_difference(self):
        # The real freight-missing case: lines 252.75-ish vs total 260.23.
        api = api_for(
            make_invoice(subtotal=226.28, taxAmount=33.95, total=260.23),
            pdf=make_pdf(total_incl_tax=260.23),
        )
        verdict = sole_skip(run_consolidator(api))
        assert any(
            "Line items sum to $219.78" in r and "$226.28" in r
            for r in verdict["reasons"]
        )

    def test_three_cent_difference_fails(self):
        api = api_for(
            make_invoice(total=252.78),
            pdf=make_pdf(total_incl_tax=252.78),
        )
        verdict = sole_skip(run_consolidator(api))
        assert any("$252.75" in r and "$252.78" in r for r in verdict["reasons"])

    def test_missing_copy_blocks_immediately(self):
        # Early gate: no copy attached → stop reviewing; nothing else is
        # reported even when other problems exist.
        api = api_for(
            make_invoice(fileId=None, linkedPurchaseOrderId=None, subtotal=999.0)
        )
        verdict = sole_skip(run_consolidator(api))
        assert len(verdict["reasons"]) == 1
        assert "No invoice copy attached" in verdict["reasons"][0]

    def test_unreadable_pdf_blocks(self):
        api = api_for(make_invoice(), pdf={"error": "corrupt file"})
        verdict = sole_skip(run_consolidator(api))
        assert any(
            "Could not read the attached invoice" in r for r in verdict["reasons"]
        )

    def test_pdf_quantity_mismatch_blocks(self):
        pdf = make_pdf()
        pdf["lines"][0] = dict(pdf["lines"][0], quantity=5.0)
        api = api_for(make_invoice(), pdf=pdf)
        verdict = sole_skip(run_consolidator(api))
        assert any("document's quantity" in r for r in verdict["reasons"])

    def test_pdf_unit_price_mismatch_blocks(self):
        pdf = make_pdf()
        pdf["lines"][0] = dict(pdf["lines"][0], unit_price_ex_tax=45.40)
        api = api_for(make_invoice(), pdf=pdf)
        verdict = sole_skip(run_consolidator(api))
        assert any("document's unit price" in r for r in verdict["reasons"])

    def test_pdf_extra_line_blocks(self):
        pdf = make_pdf()
        pdf["lines"].append(
            {
                "code": "XX1",
                "description": "MYSTERY ITEM",
                "quantity": 1,
                "unit_price_ex_tax": 10.0,
                "line_total_ex_tax": 10.0,
            }
        )
        api = api_for(make_invoice(), pdf=pdf)
        verdict = sole_skip(run_consolidator(api))
        assert any(
            "'MYSTERY ITEM'" in r and "no matching invoice line" in r
            for r in verdict["reasons"]
        )

    def test_pdf_charge_blocks(self):
        api = api_for(
            make_invoice(),
            pdf=make_pdf(
                charges=[{"description": "Freight (ex GST)", "amount_ex_tax": 6.50}]
            ),
        )
        verdict = sole_skip(run_consolidator(api))
        assert any("charge 'Freight (ex GST)' $6.50" in r for r in verdict["reasons"])

    def test_pdf_total_mismatch_blocks(self):
        api = api_for(make_invoice(), pdf=make_pdf(total_incl_tax=260.23))
        verdict = sole_skip(run_consolidator(api))
        assert any(
            "$252.75" in r and "$260.23" in r and "document total" in r
            for r in verdict["reasons"]
        )

    def test_receive_failure_demotes_to_skipped(self):
        api = api_for(make_invoice(), receive_error="API error 500: boom")
        verdict = sole_skip(run_consolidator(api))
        assert any(r.startswith("Receive failed:") for r in verdict["reasons"])


class TestLayeredReporting:
    """Reasons short-circuit at the first failing layer: an unlinked invoice
    reports ONLY that, and the expensive PDF extraction never runs for it."""

    def test_unlinked_invoice_reports_only_the_po_reason(self):
        api = api_for(
            make_invoice(
                linkedPurchaseOrderId=None,
                subtotal=999.0,  # would previously add totals noise too
            )
        )
        verdict = sole_skip(run_consolidator(api))
        assert len(verdict["reasons"]) == 1
        assert "Not linked to a purchase order" in verdict["reasons"][0]

    def test_credit_note_reports_only_the_credit_reason(self):
        api = api_for(
            make_invoice(
                total=-25.30,
                subtotal=-22.0,
                taxAmount=-3.30,
                linkedPurchaseOrderId=None,  # would previously pile on
            )
        )
        verdict = sole_skip(run_consolidator(api))
        assert len(verdict["reasons"]) == 1
        assert "Credit note" in verdict["reasons"][0]

    def test_blocked_invoices_skip_full_pdf_extraction(self):
        # An invoice blocked before Layer 6 with NO PO reference runs no
        # extraction at all (no link_po fix, so not even the PO header read).
        schemas = []

        class SpyApi(Api):
            def extract_document(
                self, connector, action, params=None, schema=None, instructions=None
            ):
                schemas.append(schema)
                return super().extract_document(
                    connector, action, params, schema, instructions
                )

        inv = make_invoice(linkedPurchaseOrderId=None, purchaseOrderNumber=None)
        api = SpyApi(
            invoices=[inv],
            details={inv["id"]: inv},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: make_pdf()},
        )
        run_consolidator(api)
        assert schemas == [], "extraction ran for an invoice with no PO reference"

    def test_link_po_does_only_a_header_extraction(self):
        # An unlinked invoice that references a PO does ONE lightweight header
        # extraction (to read the buyer PO), not the full line-by-line Layer 6.
        schemas = []

        class SpyApi(Api):
            def extract_document(
                self, connector, action, params=None, schema=None, instructions=None
            ):
                schemas.append(schema)
                return super().extract_document(
                    connector, action, params, schema, instructions
                )

        inv = make_invoice(linkedPurchaseOrderId=None)  # references PO#1520987
        api = SpyApi(
            invoices=[inv],
            details={inv["id"]: inv},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: make_pdf()},
        )
        run_consolidator(api)
        assert len(schemas) == 1  # exactly the PO header read
        assert (
            "customer_purchase_order_number" in schemas[0]
        )  # header schema, not lines
        assert "lines" not in schemas[0]

    def test_same_layer_failures_are_all_reported(self):
        # Two independent problems in the same layer (vs the copy) both show.
        pdf = make_pdf()
        pdf["lines"][0] = dict(pdf["lines"][0], quantity=5.0, unit_price_ex_tax=45.40)
        api = api_for(make_invoice(), pdf=pdf)
        verdict = sole_skip(run_consolidator(api))
        text = " | ".join(verdict["reasons"])
        assert "document's quantity" in text
        assert "document's unit price" in text

    def test_internal_totals_block_before_pdf_runs(self):
        extractions = []

        class SpyApi(Api):
            def extract_document(
                self, connector, action, params=None, schema=None, instructions=None
            ):
                extractions.append(params)
                return super().extract_document(
                    connector, action, params, schema, instructions
                )

        inv = make_invoice(subtotal=226.28, taxAmount=33.95, total=260.23)
        api = SpyApi(
            invoices=[inv],
            details={inv["id"]: inv},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: make_pdf()},
        )
        verdict = sole_skip(run_consolidator(api))
        assert extractions == []
        assert any("Line items sum to" in r for r in verdict["reasons"])

    def test_notes_are_bullet_joined(self):
        api = api_for(make_invoice(linkedPurchaseOrderId=None))
        row = run_consolidator(api)["results"][0]
        assert ";" not in row["reasons"]


class TestChecklist:
    """Every verdict carries an ordered tick/cross checklist of all gates."""

    def test_perfect_invoice_collapses_to_all_passed_string(self):
        api = api_for(make_invoice())
        verdict = run_consolidator(api)["received"][0]
        # All-pass checklists collapse to a single string — keeps the report
        # payload under the tool-result slim threshold on large runs.
        assert verdict["checklist"] == "All 12 checks passed ✓"

    def test_unlinked_invoice_shows_cross_then_unchecked(self):
        api = api_for(make_invoice(linkedPurchaseOrderId=None))
        verdict = run_consolidator(api)["skipped"][0]
        by_label = {c["check"]: c["result"] for c in verdict["checklist"]}
        assert by_label["Not a credit note or statement"] == "✓"
        assert by_label["Invoice copy attached"] == "✓"  # checked EARLY now
        assert by_label["Linked to a purchase order"] == "✗"
        # everything after the failing layer is explicitly "not checked"
        assert by_label["Lines match the invoice copy"] == "—"
        assert by_label["Total matches the invoice copy"] == "—"

    def test_pdf_failure_shows_earlier_ticks(self):
        api = api_for(make_invoice(), pdf={"error": "corrupt"})
        verdict = run_consolidator(api)["skipped"][0]
        by_label = {c["check"]: c["result"] for c in verdict["checklist"]}
        assert by_label["Linked to a purchase order"] == "✓"
        assert by_label["Invoice copy attached"] == "✓"
        assert by_label["Invoice copy readable"] == "✗"
        assert by_label["Lines match the invoice copy"] == "—"

    def test_rows_carry_checks_summary(self):
        good = make_invoice()
        bad = make_invoice(
            id="inv-2", referenceNumber="X-1", linkedPurchaseOrderId=None
        )
        api = Api(
            invoices=[good, bad],
            details={good["id"]: good, "inv-2": bad},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: make_pdf()},
        )
        rows = {r["reference"]: r for r in run_consolidator(api)["results"]}
        assert rows["F55755100"]["checks"] == "12✓"
        # unlinked invoice: credit ✓, copy attached ✓, po_linked ✗, rest unchecked
        assert rows["X-1"]["checks"] == "3✓ 1✗ 8 not checked"


class TestAuditDetails:
    """Every verdict carries the invoice's actual values with the compared
    PO / invoice-copy values and per-item ✓/✗, for manual review."""

    def test_header_shows_all_sources_on_perfect_invoice(self):
        api = api_for(make_invoice())
        header = run_consolidator(api)["received"][0]["details"]["header"]
        by_field = {h["field"]: h for h in header}
        inv_no = by_field["Invoice number"]
        assert inv_no["invoice"] == "F55755100"
        assert inv_no["copy"] == "F55755100"
        assert inv_no["result"] == "✓"
        po_row = by_field["PO number"]
        assert po_row["invoice"] == "PO#1520987"
        assert po_row["po"] == "1520987"
        total_row = by_field["Total incl tax"]
        assert total_row["invoice"] == "$252.75"
        assert total_row["copy"] == "$252.75"
        assert total_row["result"] == "✓"
        supplier_row = by_field["Supplier"]
        assert supplier_row["po"] == "Akaroa Salmon"
        assert supplier_row["result"] == "✓"

    def test_line_detail_full_comparison_on_perfect_invoice(self):
        api = api_for(make_invoice())
        lines = run_consolidator(api)["received"][0]["details"]["lines"]
        assert len(lines) == 1
        rec = lines[0]
        assert rec["line"] == "SALMON FILLET"
        assert rec["stock_item"] == "✓"
        assert rec["on_copy"] == "✓"
        # Cells are display-ready comparison strings (payload compactness)
        assert rec["unit"] == "inv Kilo / copy Kg / rec Kilo ✓"  # normalised
        assert rec["quantity"] == "inv 4.95 / copy 4.95 ✓"
        assert rec["unit_cost"] == "inv $44.40 / copy $44.40 ✓"
        assert rec["line_total"] == "inv $219.78 / copy $219.78 ✓"
        assert "po_line" not in rec and "arithmetic" not in rec  # trimmed columns

    def test_blocked_invoice_has_header_but_no_line_detail(self):
        api = api_for(make_invoice(linkedPurchaseOrderId=None))
        verdict = run_consolidator(api)["skipped"][0]
        # No comparison ran, so no line records — their absence tells the
        # playbook to render reason bullets instead of audit tables.
        assert "lines" not in verdict["details"]
        # Loaded's own header values remain available on request.
        header = {h["field"]: h for h in verdict["details"]["header"]}
        assert header["Total incl tax"]["invoice"] == "$252.75"
        assert header["Total incl tax"]["copy"] == "—"

    def test_po_lines_not_compared_or_displayed(self):
        # PO lines are neither compared nor shown — an empty PO line set
        # changes nothing, and the trimmed columns are gone.
        api = api_for(make_invoice(), po=make_po(lines=[]))
        result = run_consolidator(api)
        assert result["summary"] == {"received": 1, "skipped": 0}
        rec = result["received"][0]["details"]["lines"][0]
        assert "po_line" not in rec
        assert "ord" not in rec["quantity"]

    def test_price_mismatch_vs_copy_marks_the_cell(self):
        pdf = make_pdf()
        pdf["lines"][0] = dict(pdf["lines"][0], unit_price_ex_tax=45.40)
        api = api_for(make_invoice(), pdf=pdf)
        rec = run_consolidator(api)["skipped"][0]["details"]["lines"][0]
        assert rec["unit_cost"] == "inv $44.40 / copy $45.40 ✗"
        assert rec["quantity"].endswith("✓")  # other cells unaffected

    def test_copy_only_lines_and_charges_appear_in_detail(self):
        pdf = make_pdf(
            charges=[{"description": "Freight (ex GST)", "amount_ex_tax": 6.50}]
        )
        api = api_for(make_invoice(), pdf=pdf)
        lines = run_consolidator(api)["skipped"][0]["details"]["lines"]
        charge_rows = [r for r in lines if "charge on copy only" in r["line"]]
        assert len(charge_rows) == 1
        assert charge_rows[0]["line_total"] == "copy $6.50 ✗"
        assert charge_rows[0]["on_copy"] == "✗"

    def test_wrong_attachment_fails_invoice_number_check(self):
        api = api_for(make_invoice(), pdf=make_pdf(invoice_number="9999"))
        verdict = run_consolidator(api)["skipped"][0]
        by_label = {c["check"]: c["result"] for c in verdict["checklist"]}
        assert by_label["Invoice number matches the copy"] == "✗"
        assert any(
            "Attached copy is for invoice '9999'" in r for r in verdict["reasons"]
        )

    def test_long_invoices_cap_line_detail(self):
        # PO-linked so line comparison runs (unlinked invoices report no lines)
        many = [make_line(id=f"line-{i}", description=f"ITEM {i}") for i in range(30)]
        api = api_for(make_invoice(lines=many))
        lines = run_consolidator(api)["skipped"][0]["details"]["lines"]
        assert len(lines) == 26  # 25 detail rows + omission marker
        assert "5 more lines checked but omitted" in lines[-1]["line"]


class TestReport:
    def test_display_rows_cover_all_invoices(self):
        good = make_invoice()
        bad = make_invoice(
            id="inv-2", referenceNumber="X-1", linkedPurchaseOrderId=None
        )
        api = Api(
            invoices=[good, bad],
            details={good["id"]: good, "inv-2": bad},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: make_pdf()},
        )
        result = run_consolidator(api)
        assert result["summary"] == {"received": 1, "skipped": 1}
        rows = result["results"]
        assert {r["reference"] for r in rows} == {"F55755100", "X-1"}
        outcomes = {r["reference"]: r["outcome"] for r in rows}
        assert outcomes["F55755100"] == "received"
        assert outcomes["X-1"] == "skipped"
        skipped_row = next(r for r in rows if r["reference"] == "X-1")
        assert "Not linked to a purchase order" in skipped_row["reasons"]


class TestPayloadSize:
    """The full report must survive the tool-result slim threshold, or the
    LLM sees a "_too_large" stub instead of the audit detail (the prod
    "less detail coming through" bug of 18 Jul 2026)."""

    def make_run(self, count):
        import json

        invoices, details = [], {}
        for i in range(count):
            lines = [
                make_line(id=f"l{i}-{j}", description=f"ITEM {i}-{j}") for j in range(4)
            ]
            inv = make_invoice(
                id=f"inv-{i}",
                referenceNumber=f"INV-{1000 + i}",
                linkedPurchaseOrderId=(PO_ID if i % 2 == 0 else None),
                lines=lines,
            )
            invoices.append(inv)
            details[inv["id"]] = inv
        api = Api(
            invoices=invoices,
            details=details,
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: make_pdf()},
        )
        result = run_consolidator(api, mode="approve_all")
        return len(json.dumps({"success": True, "data": result}))

    def test_modest_run_fits_default_slim_threshold(self):
        # 8 invoices × 4 lines must fit the 30k default cap, so environments
        # without the max_result_chars override still get the full report.
        size = self.make_run(8)
        assert size < 30_000, f"report payload {size} chars would be slimmed"

    def test_large_run_fits_the_configured_override(self):
        # 15 invoices × 4 lines must fit the 100k max_result_chars the sync
        # script sets on the tool (clamped by HARD_MAX_TOOL_RESULT_CHARS).
        size = self.make_run(15)
        assert size < 100_000, f"report payload {size} chars would be slimmed"


class TestUnitParser:
    """parse_unit implements the venue's unit-of-measure guidelines."""

    def parse(self, text):
        namespace = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
        exec(FUNCTION_CODE, namespace)
        return namespace["parse_unit"](text)

    def test_good_examples_from_guidelines(self):
        assert self.parse("2.5kg") == ("weight", 2500)
        assert self.parse("5L") == ("volume", 5000)
        assert self.parse("750ml") == ("volume", 750)
        assert self.parse("12 pack") == ("count", 12)
        assert self.parse("500g") == ("weight", 500)
        assert self.parse("100 piece") == ("count", 100)
        assert self.parse("24 pack") == ("count", 24)

    def test_bad_examples_are_not_confidently_parseable(self):
        for bad in ("pkt", "box", "carton", "unit", "outer", "CTN", "ctn8"):
            assert self.parse(bad) is None, bad

    def test_base_unit_equivalences(self):
        assert self.parse("Kilo") == self.parse("1kg") == self.parse("KG")
        assert self.parse("Litre") == self.parse("1L") == self.parse("l")
        assert self.parse("5.6 KG") == self.parse("5.6kg") == ("weight", 5600)
        assert self.parse("each") == ("count", 1)
        assert self.parse("dozen") == ("count", 12)

    def test_junk_returns_none(self):
        assert self.parse(None) is None
        assert self.parse("") is None
        assert self.parse("150x200mm piece") is None  # compound — LLM's job
        assert self.parse("2x5L") is None


class TestUnitOfMeasureGate:
    """Loaded's unit vs the guideline-derived delivered unit from the copy."""

    def pdf_with_uom(self, uom):
        pdf = make_pdf()
        pdf["lines"][0] = dict(pdf["lines"][0], unit_of_measure=uom)
        return pdf

    def test_matching_uom_passes(self):
        api = api_for(make_invoice(), pdf=self.pdf_with_uom("Kilo"))
        result = run_consolidator(api)
        assert result["summary"] == {"received": 1, "skipped": 0}
        assert result["received"][0]["checklist"] == "All 12 checks passed ✓"

    def test_count_mismatch_blocks_with_fix_advice(self):
        # The real napkins case: Loaded says Each, copy is a 100-pack.
        api = api_for(
            make_invoice(lines=[make_line(unit="Each")]),
            pdf=self.pdf_with_uom("100 piece"),
        )
        verdict = sole_skip(run_consolidator(api))
        reason = next(r for r in verdict["reasons"] if "delivered unit" in r)
        assert "Loaded unit 'Each'" in reason
        assert "'100 piece'" in reason
        assert "correct the unit in Loaded" in reason
        rec = verdict["details"]["lines"][0]
        assert "rec 100 piece" in rec["unit"]
        assert rec["unit"].endswith("✗")

    def test_type_conflict_blocks(self):
        api = api_for(make_invoice(), pdf=self.pdf_with_uom("750ml"))
        verdict = sole_skip(run_consolidator(api))
        assert any("delivered unit" in r for r in verdict["reasons"])

    def test_underivable_uom_is_not_checked(self):
        api = api_for(make_invoice(), pdf=self.pdf_with_uom(None))
        result = run_consolidator(api)
        assert result["summary"] == {"received": 1, "skipped": 0}
        # not all-pass: the uom check honestly reads "—"
        by_label = {c["check"]: c["result"] for c in result["received"][0]["checklist"]}
        assert by_label["Unit of measure matches the copy"] == "—"
        assert by_label["Lines match the invoice copy"] == "✓"


class TestFixDerivation:
    """Skipped invoices carry structured one-click fixes for the card."""

    def test_unlinked_with_po_number_yields_link_po_fix(self):
        api = api_for(make_invoice(linkedPurchaseOrderId=None))  # references PO#1520987
        result = run_consolidator(api)
        fixes = result["fixes"]
        assert len(fixes) == 1
        fx = fixes[0]
        assert fx["type"] == "link_po"
        assert fx["po_number"] == "PO#1520987"
        assert fx["invoice_id"] == INV_ID
        assert fx["id"]  # stable id present

    def test_unlinked_without_po_number_yields_no_fix(self):
        api = api_for(
            make_invoice(linkedPurchaseOrderId=None, purchaseOrderNumber=None)
        )
        assert run_consolidator(api)["fixes"] == []

    def test_link_po_prefers_buyer_po_from_copy(self):
        # Loaded's field holds the supplier's O/N; the copy shows the buyer PO.
        # The fix suggests the buyer PO and records the referenced one.
        pdf = make_pdf(customer_purchase_order_number="1520999")
        api = api_for(
            make_invoice(linkedPurchaseOrderId=None, purchaseOrderNumber="12195941-1"),
            pdf=pdf,
        )
        fx = run_consolidator(api)["fixes"][0]
        assert fx["type"] == "link_po"
        assert fx["po_number"] == "1520999"  # buyer PO from the copy
        assert fx["copy_po"] == "1520999"
        assert fx["referenced_po"] == "12195941-1"  # Loaded's (supplier) number

    def test_unit_mismatch_yields_unit_fix_with_variant_context(self):
        api = api_for(
            make_invoice(lines=[make_line(unit="Each")]),
            pdf=self._pdf_uom("100 piece"),
        )
        fixes = run_consolidator(api)["fixes"]
        assert len(fixes) == 1
        fx = fixes[0]
        assert fx["type"] == "unit"
        assert fx["current_unit"] == "Each"
        assert fx["proposed_unit"] == "100 piece"
        assert fx["line_code"] == "PBO0.7-0.99"
        assert fx["linked_item_id"] == ITEM_SALMON
        assert fx["linked_supplier_id"] == "supplier-akaroa"

    def test_received_invoice_has_no_fixes(self):
        assert run_consolidator(api_for(make_invoice()))["fixes"] == []

    def test_outer_multipack_derives_and_proposes_the_pack_name(self):
        # OUTER-delivered multipack: the copy's delivered unit is the whole pack
        # '5x3kg'. Loaded's '15 KG' is ratio-equal but a different pack, so it is
        # flagged (by NAME) and the fix proposes the multipack name.
        api = api_for(
            make_invoice(lines=[make_line(unit="15 KG")]), pdf=self._pdf_uom("5x3kg")
        )
        fixes = [f for f in run_consolidator(api)["fixes"] if f["type"] == "unit"]
        assert len(fixes) == 1
        assert fixes[0]["proposed_unit"] == "5x3kg"

    def test_line_already_on_the_multipack_unit_is_not_flagged(self):
        # Loaded's unit IS the multipack name → matches by name → no unit fix.
        api = api_for(
            make_invoice(lines=[make_line(unit="5x3kg")]), pdf=self._pdf_uom("5x3kg")
        )
        assert not any(f["type"] == "unit" for f in run_consolidator(api)["fixes"])

    def _pdf_uom(self, uom):
        pdf = make_pdf()
        pdf["lines"][0] = dict(pdf["lines"][0], unit_of_measure=uom)
        return pdf


class TestFixInvoicesPayload:
    """fix_invoices carries raw editable data for the Receive Invoice card."""

    def test_link_po_invoice_has_raw_lines_no_copy(self):
        api = api_for(make_invoice(linkedPurchaseOrderId=None))
        fi = run_consolidator(api)["fix_invoices"]
        assert len(fi) == 1
        inv = fi[0]
        assert inv["invoice_id"] == INV_ID
        assert inv["purchase_order_number"] == "PO#1520987"
        assert [s["type"] for s in inv["suggestions"]] == ["link_po"]
        ln = inv["lines"][0]
        # raw numeric values, not strings
        assert ln["quantity_received"] == 4.95
        assert ln["unit_cost"] == 44.40
        assert ln["linked_item_id"] == ITEM_SALMON
        assert ln["copy_unit"] is None  # no pdf for link_po skips

    def test_unit_invoice_pairs_copy_and_recommendation(self):
        pdf = make_pdf()
        pdf["lines"][0] = dict(pdf["lines"][0], unit_of_measure="100 piece")
        api = api_for(make_invoice(lines=[make_line(unit="Each")]), pdf=pdf)
        inv = run_consolidator(api)["fix_invoices"][0]
        assert [s["type"] for s in inv["suggestions"]] == ["unit"]
        ln = inv["lines"][0]
        assert ln["unit"] == "Each"
        assert ln["recommended_unit"] == "100 piece"
        assert ln["copy_unit"] == "Kg"
        assert ln["copy_quantity"] == 4.95

    def test_clean_invoice_has_no_fix_invoices(self):
        assert run_consolidator(api_for(make_invoice()))["fix_invoices"] == []


class TestRunModes:
    """Per-user run mode gates auto-receive and card behaviour."""

    def test_approve_all_receives_nothing_and_cards_every_invoice(self):
        good = make_invoice()
        bad = make_invoice(
            id="inv-2", referenceNumber="X-1", linkedPurchaseOrderId=None
        )
        api = Api(
            invoices=[good, bad],
            details={good["id"]: good, "inv-2": bad},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: make_pdf()},
        )
        result = run_consolidator(api, mode="approve_all")
        assert api.received_bodies == []  # nothing written
        assert result["mode"] == "approve_all"
        assert result["auto_submit"] is False
        # both the perfect and the fixable invoice get a card
        refs = {fi["reference_number"] for fi in result["fix_invoices"]}
        assert refs == {"F55755100", "X-1"}
        perfect = next(
            fi for fi in result["fix_invoices"] if fi["reference_number"] == "F55755100"
        )
        assert perfect["suggestions"] == []  # no changes, just approve & receive

    def test_unset_behaves_like_approve_all_and_flags(self):
        api = api_for(make_invoice())
        result = run_consolidator(api, mode="unset")
        assert api.received_bodies == []
        assert result["mode_unset"] is True
        assert len(result["fix_invoices"]) == 1  # perfect invoice as approval card

    def test_approve_fixes_auto_receives_perfect(self):
        api = api_for(make_invoice())
        result = run_consolidator(api, mode="approve_fixes")
        assert len(api.received_bodies) == 1
        assert result["auto_submit"] is False
        assert result["fix_invoices"] == []  # perfect ones not carded

    def test_autopilot_auto_receives_and_signals_auto_submit(self):
        good = make_invoice()  # uses FILE_ID → clean make_pdf()
        bad = make_invoice(
            id="inv-2",
            referenceNumber="X-1",
            fileId="file-2",
            lines=[make_line(unit="Each")],
        )
        pdf2 = make_pdf()
        pdf2["lines"][0] = dict(pdf2["lines"][0], unit_of_measure="100 piece")
        api = Api(
            invoices=[good, bad],
            details={good["id"]: good, "inv-2": bad},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: make_pdf(), "file-2": pdf2},
        )
        result = run_consolidator(api, mode="autopilot")
        assert len(api.received_bodies) == 1  # the perfect one auto-received
        assert result["auto_submit"] is True
        # the unit-fix invoice still gets a card (auto-applied client-side)
        assert any(fi["reference_number"] == "X-1" for fi in result["fix_invoices"])


class TestCardChecks:
    """The card carries the review's own checklist, packed one char per check.

    Without it the card could only re-derive the few checks it computes
    client-side, and a card whose review stopped early rendered as "all checks
    pass" while every line read "copy not compared" — a contradiction with no
    way to see which check actually failed.
    """

    # Must match CHECK_LABELS order in the consolidator and CHECK_ORDER in
    # apps/web/app/components/display/InvoiceFixesCard.tsx.
    ORDER = [
        "credit_note",
        "pdf_present",
        "po_linked",
        "po_supplier",
        "items_matched",
        "totals",
        "pdf_readable",
        "pdf_invoice_number",
        "pdf_lines",
        "unit_of_measure",
        "pdf_total",
        "duplicate",
    ]

    def decode(self, packed):
        return dict(zip(self.ORDER, packed))

    def test_packed_checks_are_one_char_per_check(self):
        api = api_for(make_invoice(linkedPurchaseOrderId=None))
        inv = run_consolidator(api)["fix_invoices"][0]
        assert len(inv["checks"]) == len(self.ORDER)
        assert set(inv["checks"]) <= {"p", "f", "-"}

    def test_short_circuit_marks_later_checks_not_reached(self):
        # An unlinked invoice fails at the PO gate, so every check after it
        # never runs — those must read "-" (not reached), never "p".
        api = api_for(make_invoice(linkedPurchaseOrderId=None))
        inv = run_consolidator(api)["fix_invoices"][0]
        checks = self.decode(inv["checks"])
        assert checks["credit_note"] == "p"
        assert checks["pdf_present"] == "p"
        assert checks["po_linked"] == "f"
        for key in self.ORDER[self.ORDER.index("po_linked") + 1 :]:
            if key == "duplicate":
                # Packed LAST (positional stability of the checks string) but
                # evaluated FIRST (Layer 0) — checked even on early failure.
                continue
            assert checks[key] == "-", f"{key} should not have been reached"

    def test_unit_fix_invoice_reaches_the_copy_checks(self):
        # An invoice that gets as far as the unit comparison must show the
        # copy-dependent checks as actually run, not skipped.
        pdf = make_pdf()
        pdf["lines"][0] = dict(pdf["lines"][0], unit_of_measure="100 piece")
        api = api_for(make_invoice(lines=[make_line(unit="Each")]), pdf=pdf)
        checks = self.decode(run_consolidator(api)["fix_invoices"][0]["checks"])
        assert checks["po_linked"] == "p"
        assert checks["pdf_readable"] == "p"
        assert checks["unit_of_measure"] == "f"


class TestSingleInvoiceMode:
    """params.invoice_id reviews ONE invoice for the Invoices page.

    It must reuse the exact gates (so the checklist never drifts from the batch
    playbook), present-only (never auto-write), and skip the window list — the
    one draft is fetched by id. Proven by an EMPTY invoice list: if the mode
    still leaned on the list it would review nothing.
    """

    def _api(self, invoice, **kw):
        return Api(
            invoices=[],  # empty: single-invoice mode must not use the list
            details={invoice["id"]: invoice},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: make_pdf()},
            **kw,
        )

    def test_reviews_the_named_invoice_despite_empty_list(self):
        api = self._api(make_invoice())
        result = run_consolidator(api, invoice_id=INV_ID, mode="autopilot")
        # present-only regardless of the requested mode — nothing written
        assert api.received_bodies == []
        assert len(result["fix_invoices"]) == 1
        fi = result["fix_invoices"][0]
        assert fi["invoice_id"] == INV_ID
        # a perfect invoice: all 11 checks pass, packed one char per check
        assert len(fi["checks"]) == 12
        assert set(fi["checks"]) == {"p"}

    def test_carries_copy_comparison_for_the_one_invoice(self):
        pdf = make_pdf()
        pdf["lines"][0] = dict(pdf["lines"][0], unit_of_measure="100 piece")
        api = Api(
            invoices=[],
            details={INV_ID: make_invoice(lines=[make_line(unit="Each")])},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: pdf},
        )
        fi = run_consolidator(api, invoice_id=INV_ID)["fix_invoices"][0]
        ln = fi["lines"][0]
        assert ln["copy_unit"] == "Kg"
        assert ln["recommended_unit"] == "100 piece"
        assert [s["type"] for s in fi["suggestions"]] == ["unit"]


class TestSingleInvoiceCarding:
    """Single-invoice review ALWAYS surfaces the editable card, even when the
    invoice fails a gate — so the editor's validation is populated. The batch
    flow must NOT card a skipped invoice (force_card is False there).
    """

    def _freight_invoice(self):
        # A freight line has no stock item, so items_matched fails ("NEW").
        product = make_line()
        freight = make_line(
            id="ln-freight", code=None, description="FREIGHT - FOOD", linkedItemId=None
        )
        return make_invoice(lines=[product, freight])

    def test_single_invoice_cards_even_when_a_gate_fails(self):
        api = Api(
            invoices=[],
            details={INV_ID: self._freight_invoice()},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: make_pdf()},
        )
        result = run_consolidator(api, invoice_id=INV_ID)
        assert len(result["fix_invoices"]) == 1  # carded despite the failure
        fi = result["fix_invoices"][0]
        assert fi["invoice_id"] == INV_ID
        assert "f" in fi["checks"]  # items_matched failed → recorded on the card

    def test_batch_still_does_not_card_a_skipped_invoice(self):
        # Guard: the force_card change must not leak into the batch flow.
        api = api_for(self._freight_invoice())
        result = run_consolidator(api, mode="approve_all")
        assert result["fix_invoices"] == []


class TestSingleInvoicePoOverride:
    """A PO resolved on the Norm draft but not yet linked in Loaded is passed as
    ``purchase_order_id``; single-invoice mode injects it into the fetched detail
    so the PO-dependent gates run against it (validate-without-writeback). Batch
    mode must ignore it.
    """

    def _api(self, inv):
        return Api(
            invoices=[],
            details={INV_ID: inv},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: make_pdf()},
        )

    def test_override_runs_the_po_gates_without_writeback(self):
        # Loaded shows the invoice unlinked; without the override po_linked fails.
        base = run_consolidator(
            self._api(make_invoice(linkedPurchaseOrderId=None)), invoice_id=INV_ID
        )["fix_invoices"][0]
        assert base["checks"][2] == "f"  # po_linked failed, later gates not reached

        # With the override it validates against the resolved PO — all gates run.
        api = self._api(make_invoice(linkedPurchaseOrderId=None))
        fi = run_consolidator(api, invoice_id=INV_ID, purchase_order_id=PO_ID)[
            "fix_invoices"
        ][0]
        assert len(fi["checks"]) == 12
        # po_linked is a SUGGESTED change (auto-matched, not originally linked),
        # not a clean pass; every OTHER gate runs and passes.
        assert fi["checks"][2] == "s"
        assert set(fi["checks"]) == {"p", "s"}
        # a link_po suggestion is emitted so the user sees the matched PO
        assert any(s["type"] == "link_po" for s in fi["suggestions"])
        assert api.received_bodies == []  # present-only: never written back

    def test_editor_resolves_the_referenced_po_itself(self):
        # No PO id passed in: the consolidator resolves the referenced number to a
        # Loaded PO id ITSELF (the retrieval that used to live in the draft), then
        # validates against it and suggests linking — without writing back.
        api = Api(
            invoices=[],
            details={INV_ID: make_invoice(linkedPurchaseOrderId=None)},  # refs PO#1520987
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: make_pdf()},
            po_list=[
                {
                    "id": PO_ID,
                    "orderNumber": "1520987",
                    "supplierId": "supplier-akaroa",
                    "linkedInvoiceId": None,
                }
            ],
        )
        fi = run_consolidator(api, invoice_id=INV_ID)["fix_invoices"][0]
        assert fi["checks"][2] == "s"  # resolved → suggested change, not a clean pass
        assert set(fi["checks"]) == {"p", "s"}
        link = next(s for s in fi["suggestions"] if s["type"] == "link_po")
        # The RESOLVED id rides on the suggestion so the editor's Order Number
        # picker can show it in place (pre-filled, marked suggested).
        assert link["purchase_order_id"] == PO_ID
        assert api.received_bodies == []  # present-only, never written back

    def test_editor_resolves_a_received_po_via_the_feed(self):
        # The referenced PO isn't in the open-PO list (already received), so
        # resolution falls back to the received-invoice feed: find a sibling
        # invoice carrying the number and read its linkedPurchaseOrderId. Exercises
        # Pass 2 — which the sandbox must run without next()/iter().
        target = make_invoice(linkedPurchaseOrderId=None, purchaseOrderNumber="1520272")
        api = Api(
            invoices=[],
            details={INV_ID: target, "sib-1": {"linkedPurchaseOrderId": "po-sib"}},
            pos={"po-sib": make_po(id="po-sib", orderNumber="1520272")},
            pdfs={FILE_ID: make_pdf()},
            po_list=[],  # not in the open list
            received_feed=[{"id": "sib-1", "purchaseOrderNumber": "1520272"}],
        )
        fi = run_consolidator(api, invoice_id=INV_ID)["fix_invoices"][0]
        assert fi["checks"][2] == "s"  # resolved via the feed → suggested change
        assert any(s["type"] == "link_po" for s in fi["suggestions"])

    def test_unresolvable_reference_suggests_nothing(self):
        # The referenced number matches no Loaded PO (a supplier's own ref): the
        # editor withholds the "Link PO" suggestion rather than surfacing noise.
        api = self._api(make_invoice(linkedPurchaseOrderId=None))  # empty po_list/feed
        fi = run_consolidator(api, invoice_id=INV_ID)["fix_invoices"][0]
        assert fi["checks"][2] == "f"  # po_linked still fails
        assert not any(s["type"] == "link_po" for s in fi["suggestions"])

    def test_batch_ignores_the_override(self):
        api = api_for(make_invoice(linkedPurchaseOrderId=None))
        result = run_consolidator(api, mode="approve_all", purchase_order_id=PO_ID)
        # The override is single-invoice only — batch still sees it unlinked and
        # never auto-receives it as valid; any card keeps po_linked failed.
        assert api.received_bodies == []
        for fi in result["fix_invoices"]:
            assert fi["checks"][2] != "p"


class TestSingleInvoiceRunsAllChecks:
    """Single-invoice review runs every check it can even AFTER an earlier
    failure (no short-circuit), so a line-vs-copy mismatch (e.g. a qty that
    doesn't match the copy) isn't hidden behind an earlier totals failure.
    Batch mode still short-circuits (covered by the batch short-circuit test).

    Check positions in CHECK_LABELS order: 5=totals, 6=pdf_readable,
    8=pdf_lines, 10=pdf_total.
    """

    def test_totals_failure_does_not_hide_the_copy_checks(self):
        # Inflate the subtotal so line-sum != subtotal → the totals check fails.
        api = Api(
            invoices=[],
            details={INV_ID: make_invoice(subtotal=999.0)},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: make_pdf()},
        )
        fi = run_consolidator(api, invoice_id=INV_ID)["fix_invoices"][0]
        assert fi["checks"][5] == "f"  # totals failed
        # ...but the Invoice Copy checks STILL ran (were not short-circuited):
        assert fi["checks"][6] == "p"  # pdf_readable ran
        assert fi["checks"][8] != "-"  # pdf_lines ran (p or f, not "not reached")
        assert fi["checks"][10] != "-"  # pdf_total ran

    def test_qty_mismatch_surfaces_even_with_a_totals_failure(self):
        # The real 109759592 shape: a line's Loaded qty differs from the copy,
        # which also makes the internal totals inconsistent. The qty mismatch
        # must be reported (via pdf_lines), not swallowed by the totals failure.
        pdf = make_pdf()
        pdf["lines"][0] = dict(pdf["lines"][0], quantity=99.0)  # copy says 99
        api = Api(
            invoices=[],
            details={INV_ID: make_invoice(subtotal=999.0)},  # totals also off
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: pdf},
        )
        fi = run_consolidator(api, invoice_id=INV_ID)["fix_invoices"][0]
        assert fi["checks"][5] == "f"  # totals failed
        assert fi["checks"][8] == "f"  # pdf_lines ran AND caught the qty mismatch
        assert any("quantity" in r for r in fi["check_reasons"])

    def test_non_derivable_copy_unit_is_not_flagged_or_suggested(self):
        # A bogus / non-derivable delivered unit (a bare packaging word like "pkt")
        # must NOT be flagged as a mismatch and must NOT be surfaced as "use X" —
        # the whole point of gating: never tell the user to switch to a packaging
        # word the extraction mis-read into unit_of_measure.
        pdf = make_pdf()
        pdf["lines"][0] = dict(pdf["lines"][0], unit_of_measure="pkt")
        api = Api(
            invoices=[],
            details={INV_ID: make_invoice()},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: pdf},
        )
        fi = run_consolidator(api, invoice_id=INV_ID)["fix_invoices"][0]
        ln = fi["lines"][0]
        assert not ln.get("copy_unit_mismatch")  # not flagged as a mismatch
        assert ln.get("recommended_unit") is None  # not surfaced as "use X"
        assert not any(s.get("type") == "unit" for s in fi["suggestions"])

    def test_quantity_mismatch_sets_the_copy_quantity_mismatch_flag(self):
        # The review DECIDES a qty mismatch (copy qty != received) and exposes it as
        # a per-line copy_quantity_mismatch flag; the component renders the "use copy
        # qty" edit from it — the component does not decide the mismatch itself.
        pdf = make_pdf()
        pdf["lines"][0] = dict(pdf["lines"][0], quantity=99)  # copy says 99, invoice 4.95
        api = Api(
            invoices=[],
            details={INV_ID: make_invoice()},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: pdf},
        )
        fi = run_consolidator(api, invoice_id=INV_ID)["fix_invoices"][0]
        ln = fi["lines"][0]
        assert ln.get("copy_quantity_mismatch") is True
        assert ln.get("copy_quantity") == 99
        # (No accept-able suggestion here: 99 x 44.40 != 219.78, so the guard
        # withholds it — the self-consistent case below asserts the suggestion.)
        assert not any(s.get("type") == "quantity" for s in fi["suggestions"])

    def test_self_consistent_copy_quantity_is_suggested(self):
        # qty x unit price = line total → the extracted qty is trustworthy and
        # the one-click suggestion is emitted. (99 x 44.40 = 4395.60)
        pdf = make_pdf()
        pdf["lines"][0] = dict(
            pdf["lines"][0], quantity=99, unit_price_ex_tax=44.40,
            line_total_ex_tax=4395.60,
        )
        api = Api(
            invoices=[], details={INV_ID: make_invoice()},
            pos={PO_ID: make_po()}, pdfs={FILE_ID: pdf},
        )
        fi = run_consolidator(api, invoice_id=INV_ID)["fix_invoices"][0]
        qfix = next(s for s in fi["suggestions"] if s.get("type") == "quantity")
        assert qfix["line_id"] == "line-1"
        assert qfix["proposed_quantity"] == 99
        assert "4.95 → 99" in qfix["summary"]

    def test_statement_document_suggests_deleting_the_draft(self):
        # A supplier STATEMENT uploaded as a draft invoice (rows of prior
        # invoices/payments/balances, no products — live: Southern Hospitality).
        # The review flags the document type, suggests deleting the draft, and
        # produces NO line-vs-copy noise (statement rows aren't product lines).
        pdf = make_pdf(document_type="statement")
        api = Api(
            invoices=[],
            details={INV_ID: make_invoice(linkedPurchaseOrderId=None)},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: pdf},
        )
        fi = run_consolidator(api, invoice_id=INV_ID)["fix_invoices"][0]
        assert fi["checks"][0] == "f"  # document-type check (credit_note slot)
        assert any(
            "STATEMENT" in r and "deleted" in r for r in fi["check_reasons"]
        )
        assert any(s.get("type") == "delete_invoice" for s in fi["suggestions"])
        # No copy-comparison noise from statement rows:
        assert not any(
            s.get("type") in ("quantity", "unit") for s in fi["suggestions"]
        )
        ln = fi["lines"][0]
        assert ln.get("copy_quantity") is None  # line comparison skipped

    def test_duplicate_of_received_invoice_suggests_deleting_the_draft(self):
        # The received feed holds a sibling with the SAME invoice number and
        # supplier (live: CN-19980, already received as a −$20 credit). Loaded's
        # API carries no duplicate marker on the detail — the check is ours.
        api = Api(
            invoices=[],
            details={INV_ID: make_invoice()},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: make_pdf()},
            received_feed=[{
                "id": "other-received-id",
                "invoiceNumber": "F55755100",
                "supplierName": "Akaroa Salmon",
                "receivedAt": "2026-06-29T22:26:47Z",
                "total": -20.0,
            }],
        )
        fi = run_consolidator(api, invoice_id=INV_ID)["fix_invoices"][0]
        assert fi["checks"][11] == "f"  # the duplicate check (appended last)
        assert any("already received on 2026-06-29" in r for r in fi["check_reasons"])
        assert any(s.get("type") == "delete_invoice" for s in fi["suggestions"])

    def test_duplicate_batch_mode_skips_and_never_receives(self):
        api = api_for(
            make_invoice(),
            received_feed=[{
                "id": "other-received-id",
                "invoiceNumber": "F55755100",
                "supplierName": "Akaroa Salmon",
                "receivedAt": "2026-06-29T22:26:47Z",
                "total": -20.0,
            }],
        )
        result = run_consolidator(api)
        assert result["summary"] == {"received": 0, "skipped": 1}
        assert api.received_bodies == []

    def test_own_feed_echo_or_other_supplier_is_not_a_duplicate(self):
        # The invoice's own id in the feed, or the same number from a DIFFERENT
        # supplier, must not read as a duplicate.
        api = api_for(
            make_invoice(),
            received_feed=[
                {"id": INV_ID, "invoiceNumber": "F55755100", "supplierName": "Akaroa Salmon"},
                {"id": "x", "invoiceNumber": "F55755100", "supplierName": "Someone Else"},
            ],
        )
        assert run_consolidator(api)["summary"] == {"received": 1, "skipped": 0}

    def test_invoice_document_type_does_not_trigger_the_statement_gate(self):
        # An ordinary invoice with document_type present stays fully receivable.
        api = api_for(make_invoice(), pdf=make_pdf(document_type="invoice"))
        assert run_consolidator(api)["summary"] == {"received": 1, "skipped": 0}

    def test_self_contradicting_copy_quantity_is_flagged_but_not_suggested(self):
        # The extraction contradicts ITSELF: qty 4 x $4.53 = $18.12 but the line
        # total reads $72.48 (the live BUTTERMILK misread — the real qty was 16,
        # printed as a carton/singles split). One of the numbers is provably
        # wrong, so the mismatch is FLAGGED but no one-click Accept is offered
        # for a number we can prove is a misread.
        pdf = make_pdf()
        pdf["lines"][0] = dict(
            pdf["lines"][0], quantity=4, unit_price_ex_tax=4.53,
            line_total_ex_tax=72.48,
        )
        api = Api(
            invoices=[], details={INV_ID: make_invoice()},
            pos={PO_ID: make_po()}, pdfs={FILE_ID: pdf},
        )
        fi = run_consolidator(api, invoice_id=INV_ID)["fix_invoices"][0]
        ln = fi["lines"][0]
        assert ln.get("copy_quantity_mismatch") is True  # still flagged
        assert not any(s.get("type") == "quantity" for s in fi["suggestions"])

    def test_zero_dollar_duplicate_line_is_flagged_for_striking(self):
        # Two lines share a code; one carries the real amount, the other is an
        # empty $0 twin (a re-scan / split-that-zeroed artifact). The copy has the
        # item once, so the $0 twin must NOT be flagged "not found on the document"
        # — it's flagged copy_duplicate and a strike suggestion is emitted, while
        # the real line still reconciles against the copy. (Live: 109738996.)
        real = make_line(
            id="l-real", code="666756", description="CHEESE PARMESAN WEDGE",
            quantityReceived=2.0, unitCost=50.0, totalCost=100.0,
            linkedItemId="item-cheese",
        )
        dupe = make_line(
            id="l-dupe", code="666756", description="CHEESE PARMESAN WEDGE",
            quantityReceived=0.0, unitCost=0.0, totalCost=0.0,
            linkedItemId="item-cheese",
        )
        inv = make_invoice(
            lines=[real, dupe], linkedPurchaseOrderId=None,
            subtotal=100.0, taxAmount=15.0, total=115.0,
        )
        pdf = make_pdf(
            lines=[{
                "code": "666756", "description": "CHEESE PARMESAN WEDGE",
                "quantity": 2.0, "unit": "Each", "unit_of_measure": "Each",
                "unit_price_ex_tax": 50.0, "line_total_ex_tax": 100.0,
            }],
            subtotal_ex_tax=100.0, tax_amount=15.0, total_incl_tax=115.0,
        )
        # Single-invoice review forces the card and runs the full copy comparison,
        # so the "not found" path is actually exercised.
        fi = run_consolidator(api_for(inv, pdf=pdf), invoice_id=INV_ID)["fix_invoices"][0]
        by_id = {ln["id"]: ln for ln in fi["lines"]}
        assert by_id["l-dupe"].get("copy_duplicate") is True
        assert by_id["l-real"].get("copy_duplicate") is None  # real line untouched
        assert any(
            s.get("type") == "strike" and s.get("line_id") == "l-dupe"
            for s in fi["suggestions"]
        )
        # The $0 twin is NOT double-flagged as missing from the document.
        assert not any(
            "not found on the attached invoice" in r
            for r in fi.get("check_reasons") or []
        )

    def test_new_item_lines_carry_item_match_suggestions(self):
        # The engine's artifact is COMPLETE: for NEW (unlinked) lines it calls the
        # norm.match_stock_items LLM function and bakes the link-or-create
        # suggestion onto the card lines — every surface renders the same thing.
        new_line = make_line(
            id="l-new", code="XX1", description="MYSTERY SAUCE",
            linkedItemId=None, linkedUnitId=None,
        )
        inv = make_invoice(lines=[make_line(), new_line])
        api = api_for(
            inv,
            item_matches={
                "l-new": {
                    "matched_item": {"id": "i-77", "name": "SAUCE MYSTERY"},
                    "suggested_name": None,
                    "suggested_group_id": None,
                }
            },
        )
        fi = run_consolidator(api, invoice_id=INV_ID)["fix_invoices"][0]
        by_id = {ln["id"]: ln for ln in fi["lines"]}
        assert by_id["l-new"]["matched_item"] == {"id": "i-77", "name": "SAUCE MYSTERY"}
        # only the unlinked line was sent to the matcher
        assert api.match_calls and [x["id"] for x in api.match_calls[0]] == ["l-new"]
        # the linked line is untouched (no match fields set)
        assert "matched_item" not in by_id["line-1"]

    def test_fully_linked_invoice_never_calls_the_matcher(self):
        # No NEW lines → zero LLM-function calls (the "no LLM unless it earns
        # its keep" rule).
        api = api_for(make_invoice())
        run_consolidator(api, invoice_id=INV_ID)
        assert api.match_calls == []

    def test_matcher_failure_leaves_lines_bare(self):
        # A failed/error matcher result degrades to plain create — no fields, no
        # crash. (call_api surfaces errors as {"error": ...} dicts.)
        new_line = make_line(id="l-new", code="XX1", linkedItemId=None)
        api = api_for(make_invoice(lines=[make_line(), new_line]))
        api.item_matches = None  # scripted: handler returned nothing usable

        real_call = api.call_api

        def flaky(connector, action, params=None):
            if action == "match_stock_items":
                return {"error": "LLM down"}
            return real_call(connector, action, params)

        api.call_api = flaky
        fi = run_consolidator(api, invoice_id=INV_ID)["fix_invoices"][0]
        by_id = {ln["id"]: ln for ln in fi["lines"]}
        assert "matched_item" not in by_id["l-new"]

    def test_split_po_suggestion_notes_it_is_not_relinked(self):
        # The matched PO is already invoiced on another invoice (a split order):
        # the suggestion says it was used to validate, not re-linked.
        po = make_po()
        po["linkedInvoiceId"] = "some-other-invoice"
        api = Api(
            invoices=[],
            details={INV_ID: make_invoice(linkedPurchaseOrderId=None)},
            pos={PO_ID: po},
            pdfs={FILE_ID: make_pdf()},
        )
        fi = run_consolidator(api, invoice_id=INV_ID, purchase_order_id=PO_ID)[
            "fix_invoices"
        ][0]
        sug = next(s for s in fi["suggestions"] if s["type"] == "link_po")
        assert sug["already_linked_elsewhere"] is True
        assert "not re-linked" in sug["summary"]

    def test_failure_reasons_ride_on_the_card(self):
        # A line-vs-copy mismatch: the specific reason must reach the card so the
        # editor can show WHAT didn't match, not just which check failed.
        pdf = make_pdf()
        pdf["lines"][0] = dict(pdf["lines"][0], quantity=99.0)  # mismatch
        api = Api(
            invoices=[],
            details={INV_ID: make_invoice()},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: pdf},
        )
        fi = run_consolidator(api, invoice_id=INV_ID)["fix_invoices"][0]
        assert fi["checks"][8] == "f"  # pdf_lines failed
        assert any("quantity" in r for r in fi["check_reasons"])
