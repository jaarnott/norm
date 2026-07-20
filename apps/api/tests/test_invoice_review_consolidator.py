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

    def __init__(self, invoices, details, pos=None, pdfs=None, receive_error=None):
        self.invoices = invoices
        self.details = details
        self.pos = pos or {}
        self.pdfs = pdfs or {}
        self.receive_error = receive_error
        self.received_bodies = []

    def call_api(self, connector, action, params=None):
        params = params or {}
        if action == "list_stock_invoices":
            return self.invoices
        if action == "get_invoice_detail":
            return self.details[params["invoice_id"]]
        if action == "get_stock_purchase_order":
            return self.pos[params["purchase_order_id"]]
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

    def test_freight_line_missing_from_copy_blocks(self):
        # A freight line on the Loaded invoice that only shows as a separate
        # charge on the copy: the product-line match fails on the copy side
        # (the PO no longer matters for line membership).
        freight = make_line(
            id="line-2",
            code="FGT001",
            description="FREIGHT - FOOD",
            linkedItemId="item-freight",
            linkedUnitId="unit-each",
            quantityReceived=1,
            unitCost=6.50,
            totalCost=6.50,
        )
        inv = make_invoice(
            lines=[make_line(), freight],
            subtotal=226.28,
            taxAmount=33.94,
            total=260.22,
        )
        pdf = make_pdf(
            charges=[{"description": "Freight (ex GST)", "amount_ex_tax": 6.50}],
            subtotal_ex_tax=226.28,
            tax_amount=33.95,
            total_incl_tax=260.23,
        )
        api = api_for(inv, pdf=pdf)
        verdict = sole_skip(run_consolidator(api))
        assert any(
            "'FREIGHT - FOOD' not found on the attached invoice document" in r
            for r in verdict["reasons"]
        )

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

    def test_unit_differs_from_copy_blocks(self):
        pdf = make_pdf()
        pdf["lines"][0] = dict(pdf["lines"][0], unit="each")
        api = api_for(make_invoice(), pdf=pdf)  # invoice unit is Kilo
        verdict = sole_skip(run_consolidator(api))
        assert any(
            "unit Kilo does not match the document's unit each" in r
            for r in verdict["reasons"]
        )

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
        assert "customer_purchase_order_number" in schemas[0]  # header schema, not lines
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
        assert verdict["checklist"] == "All 11 checks passed ✓"

    def test_unlinked_invoice_shows_cross_then_unchecked(self):
        api = api_for(make_invoice(linkedPurchaseOrderId=None))
        verdict = run_consolidator(api)["skipped"][0]
        by_label = {c["check"]: c["result"] for c in verdict["checklist"]}
        assert by_label["Not a credit note"] == "✓"
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
        assert rows["F55755100"]["checks"] == "11✓"
        # unlinked invoice: credit ✓, copy attached ✓, po_linked ✗, rest unchecked
        assert rows["X-1"]["checks"] == "2✓ 1✗ 8 not checked"


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
        assert result["received"][0]["checklist"] == "All 11 checks passed ✓"

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
