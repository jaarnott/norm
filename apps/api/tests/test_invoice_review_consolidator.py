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
    defaults = {"today": "2026-07-16", "venue": "Bessie", **params}
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

    def test_dry_run_never_writes(self):
        api = api_for(make_invoice())
        result = run_consolidator(api, dry_run=True)
        assert result["received"][0]["outcome"] == "would receive (dry run)"
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

    def test_freight_line_not_on_po_blocks(self):
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
            "'FREIGHT - FOOD' has no matching purchase-order line" in r
            for r in verdict["reasons"]
        )

    def test_po_price_difference_does_not_block(self):
        # User decision: PO prices move between ordering and invoicing — the
        # attached invoice document is the source of truth for what's billed.
        api = api_for(make_invoice(), po=make_po(lines=[make_po_line(unitCost=42.00)]))
        result = run_consolidator(api)
        assert result["summary"] == {"received": 1, "skipped": 0}

    def test_unit_differs_from_po(self):
        api = api_for(
            make_invoice(),
            po=make_po(lines=[make_po_line(unitId="unit-gram", unitName="Gram")]),
        )
        verdict = sole_skip(run_consolidator(api))
        assert any("unit differs from PO" in r for r in verdict["reasons"])

    def test_po_supplier_mismatch(self):
        api = api_for(
            make_invoice(),
            po=make_po(supplierId="someone-else", supplierName="Wrong Supplier Ltd"),
        )
        verdict = sole_skip(run_consolidator(api))
        assert any("does not match invoice supplier" in r for r in verdict["reasons"])

    def test_unmatched_stock_item(self):
        api = api_for(make_invoice(lines=[make_line(linkedItemId=None)]))
        verdict = sole_skip(run_consolidator(api))
        assert any("not matched to stock items" in r for r in verdict["reasons"])

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

    def test_missing_pdf_blocks(self):
        api = api_for(make_invoice(fileId=None))
        verdict = sole_skip(run_consolidator(api))
        assert any("No invoice document attached" in r for r in verdict["reasons"])

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
                fileId=None,  # would previously add PDF noise too
                subtotal=999.0,  # and totals noise
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

    def test_blocked_invoices_skip_pdf_extraction(self):
        extractions = []

        class SpyApi(Api):
            def extract_document(
                self, connector, action, params=None, schema=None, instructions=None
            ):
                extractions.append(params)
                return super().extract_document(
                    connector, action, params, schema, instructions
                )

        inv = make_invoice(linkedPurchaseOrderId=None)
        api = SpyApi(
            invoices=[inv],
            details={inv["id"]: inv},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: make_pdf()},
        )
        run_consolidator(api)
        assert extractions == [], "PDF extraction ran for a blocked invoice"

    def test_same_layer_failures_are_all_reported(self):
        # Two independent line problems in the same layer (vs the PO) both show.
        extra = make_line(
            id="line-2",
            code="XTRA",
            description="EXTRA THING",
            linkedItemId="item-extra",
            linkedUnitId="unit-each",
        )
        bad_unit = make_line(id="line-3", description="WRONG UNIT ITEM")
        api = api_for(
            make_invoice(lines=[extra, bad_unit]),
            po=make_po(lines=[make_po_line(unitId="unit-gram", unitName="Gram")]),
        )
        verdict = sole_skip(run_consolidator(api))
        text = " | ".join(verdict["reasons"])
        assert "'EXTRA THING' has no matching purchase-order line" in text
        assert "unit differs from PO" in text

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
        assert verdict["checklist"] == "All 14 checks passed ✓"

    def test_unlinked_invoice_shows_cross_then_unchecked(self):
        api = api_for(make_invoice(linkedPurchaseOrderId=None))
        verdict = run_consolidator(api)["skipped"][0]
        by_label = {c["check"]: c["result"] for c in verdict["checklist"]}
        assert by_label["Not a credit note"] == "✓"
        assert by_label["Linked to a purchase order"] == "✗"
        # everything after the failing layer is explicitly "not checked"
        assert by_label["Purchase order retrieved"] == "—"
        assert by_label["Lines match the invoice copy"] == "—"
        assert by_label["Total matches the invoice copy"] == "—"

    def test_pdf_failure_shows_earlier_ticks(self):
        api = api_for(make_invoice(), pdf={"error": "corrupt"})
        verdict = run_consolidator(api)["skipped"][0]
        by_label = {c["check"]: c["result"] for c in verdict["checklist"]}
        assert by_label["Linked to a purchase order"] == "✓"
        assert by_label["Purchase order retrieved"] == "✓"
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
        assert rows["F55755100"]["checks"] == "14✓"
        assert rows["X-1"]["checks"] == "1✓ 1✗ 12 not checked"


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
        assert rec["po_line"] == "✓"
        assert rec["on_copy"] == "✓"
        # Cells are display-ready comparison strings (payload compactness)
        assert rec["unit"] == "inv Kilo / po Kilo ✓"
        assert rec["quantity"] == "ord 5.0 / inv 4.95 / copy 4.95 ✓"
        assert rec["unit_cost"] == "inv $44.40 / copy $44.40 ✓"
        assert rec["line_total"] == "inv $219.78 / copy $219.78 ✓"
        assert rec["arithmetic"] == "✓"

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

    def test_po_fetched_failure_keeps_unchecked_line_detail(self):
        # PO fetched but a line has no PO line — comparison started, so line
        # records are reported, with "—" marking cells never checked.
        api = api_for(make_invoice(), po=make_po(lines=[]))
        verdict = run_consolidator(api)["skipped"][0]
        rec = verdict["details"]["lines"][0]
        assert rec["quantity"].startswith("ord") or rec["quantity"].startswith("inv")
        assert rec["po_line"] == "✗"

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
        result = run_consolidator(api, dry_run=True)
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
