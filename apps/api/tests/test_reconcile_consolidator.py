"""Tests for the reconcile_received_invoices consolidator function_code.

Same harness as test_invoice_review_consolidator: the canonical code from
config/consolidators/ is exec'd under the REAL sandbox namespace, with a
scriptable fake for call_api / extract_document.

Fixtures mirror the live LoadedHub shapes captured 16-17 Jul 2026 (statement
create/update contract exercised in the test env).
"""

import pathlib

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

FUNCTION_CODE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "reconcile_received_invoices.py"
).read_text(encoding="utf-8")


class Api:
    def __init__(
        self, statements, received, pdfs=None, update_error=None, create_error=None
    ):
        self.statements = statements
        self.received = received
        self.pdfs = pdfs or {}
        self.update_error = update_error
        self.create_error = create_error
        self.updated = []  # (statement_id, body)
        self.created = []  # body

    def call_api(self, connector, action, params=None):
        params = params or {}
        if action == "list_supplier_statements":
            return self.statements
        if action == "list_received_invoices":
            return self.received
        if action == "update_supplier_statement":
            if self.update_error:
                return {"error": self.update_error}
            self.updated.append((params["statement_id"], params["statement"]))
            return dict(params["statement"])
        if action == "create_supplier_statement":
            if self.create_error:
                return {"error": self.create_error}
            self.created.append(params["statement"])
            return dict(params["statement"], id="new-stmt-1")
        raise AssertionError(f"unexpected action {action}")

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
    # about auto-reconciling hold; mode-specific tests override this.
    defaults = {
        "today": "2026-07-17",
        "venue": "Bessie",
        "mode": "approve_fixes",
        **params,
    }
    return namespace["run"](defaults, api.call_api, lambda m: None)


SUPPLIER = "8fa8e731-23d0-4cb9-be56-9fa7010e0d50"
STMT_ID = "36580aa5-6336-45db-28a0-08ded3077371"
FILE_ID = "file-1"


def make_statement(**over):
    s = {
        "id": STMT_ID,
        "supplierId": SUPPLIER,
        "supplierName": "Angus Meats",
        "statementNumber": "July 2026",
        "statementAmount": 1528.70,
        "startAt": "2026-06-30T18:00:00+00:00",
        "endAt": "2026-07-30T18:00:00+00:00",
        "deletedAt": None,
        "reconciledAmount": 0,
        "reconciledCount": 0,
        "reconciledStockReceivedItems": [],
    }
    s.update(over)
    return s


def make_received(**over):
    inv = {
        "id": "recv-1",
        "type": "Invoice",
        "creditRequest": False,
        "purchaseOrderNumber": "1521021",
        "invoiceNumber": "1008102",
        "fileId": FILE_ID,
        "receivedAt": "2026-07-13T02:00:00+00:00",
        "invoicedAt": "2026-07-13",
        "lines": [],
        "subtotal": 158.34,
        "total": 182.09,
        "supplierId": SUPPLIER,
        "supplierName": "Angus Meats",
        "statementId": None,
        "reconciled": False,
        "deletedAt": None,
    }
    inv.update(over)
    return inv


def make_pdf(**over):
    pdf = {
        "supplier_name": "Angus Meats Ltd",
        "invoice_number": "1008102",
        "purchase_order_number": "PO#1521021",
        "invoice_date": "2026-07-13",
        "total_incl_tax": 182.09,
    }
    pdf.update(over)
    return pdf


def api_for(invoice, pdf=None, statement=None, **kw):
    return Api(
        statements=[statement if statement is not None else make_statement()],
        received=[invoice],
        pdfs={invoice.get("fileId") or FILE_ID: pdf if pdf is not None else make_pdf()},
        **kw,
    )


def sole_fail(result):
    assert result["summary"]["reconciled"] == 0, result
    assert result["summary"]["not_reconciled"] == 1, result
    return result["not_reconciled"][0]


class TestReconciles:
    def test_perfect_invoice_is_reconciled(self):
        api = api_for(make_received())
        result = run_consolidator(api)
        assert result["summary"] == {
            "reconciled": 1,
            "not_reconciled": 0,
            "needs_statement": 0,
        }
        assert result["reconciled"][0]["outcome"] == "reconciled"
        assert len(api.updated) == 1
        stmt_id, body = api.updated[0]
        assert stmt_id == STMT_ID
        items = body["reconciledStockReceivedItems"]
        assert len(items) == 1
        assert items[0]["reconciled"] is True
        assert items[0]["id"] == "recv-1"

    def test_approve_all_never_writes(self):
        api = api_for(make_received())
        result = run_consolidator(api, mode="approve_all")
        assert result["reconciled"][0]["outcome"] == "awaiting your approval"
        assert api.updated == [] and api.created == []

    def test_existing_statement_items_are_preserved(self):
        existing_item = {"id": "old-1", "reconciled": True}
        api = api_for(
            make_received(),
            statement=make_statement(reconciledStockReceivedItems=[existing_item]),
        )
        run_consolidator(api)
        _, body = api.updated[0]
        ids = [i["id"] for i in body["reconciledStockReceivedItems"]]
        assert ids == ["old-1", "recv-1"]

    def test_po_normalisation_matches(self):
        # Loaded "1521021" vs PDF "PO#1521021" — must match after normalisation.
        api = api_for(
            make_received(), pdf=make_pdf(purchase_order_number="po# 1521021")
        )
        assert run_consolidator(api)["summary"]["reconciled"] == 1

    def test_two_cent_total_difference_tolerated(self):
        api = api_for(make_received(), pdf=make_pdf(total_incl_tax=182.11))
        assert run_consolidator(api)["summary"]["reconciled"] == 1

    def test_already_reconciled_excluded(self):
        api = api_for(make_received(reconciled=True))
        result = run_consolidator(api)
        assert result["summary"] == {
            "reconciled": 0,
            "not_reconciled": 0,
            "needs_statement": 0,
        }

    def test_supplier_filter_restricts_run(self):
        api = api_for(make_received())
        result = run_consolidator(api, suppliers=["Someone Else"])
        assert result["summary"] == {
            "reconciled": 0,
            "not_reconciled": 0,
            "needs_statement": 0,
        }
        assert api.updated == []


class TestFailures:
    def test_missing_file_fails_check_1(self):
        api = api_for(make_received(fileId=None))
        verdict = sole_fail(run_consolidator(api))
        assert any("No invoice copy attached" in r for r in verdict["reasons"])
        assert api.updated == []

    def test_unreadable_pdf_fails(self):
        api = api_for(make_received(), pdf={"error": "corrupt"})
        verdict = sole_fail(run_consolidator(api))
        assert any(
            "Could not read the attached invoice copy" in r for r in verdict["reasons"]
        )

    def test_wrong_attachment_fails_sanity(self):
        api = api_for(make_received(), pdf=make_pdf(invoice_number="9999"))
        verdict = sole_fail(run_consolidator(api))
        assert any("Attached copy is for invoice" in r for r in verdict["reasons"])
        assert verdict["checks"]["invoice_number_match"] == "fail"

    def test_unreadable_invoice_number_on_copy_fails(self):
        api = api_for(make_received(), pdf=make_pdf(invoice_number=None))
        verdict = sole_fail(run_consolidator(api))
        assert any(
            "Could not read the invoice number from the invoice copy" in r
            for r in verdict["reasons"]
        )
        assert verdict["checks"]["invoice_number_match"] == "fail"

    def test_po_conflict_fails_with_both_values(self):
        api = api_for(make_received(), pdf=make_pdf(purchase_order_number="1520999"))
        verdict = sole_fail(run_consolidator(api))
        assert any("1521021" in r and "1520999" in r for r in verdict["reasons"])

    def test_po_missing_on_loaded_side_fails_strict(self):
        api = api_for(make_received(purchaseOrderNumber=None))
        verdict = sole_fail(run_consolidator(api))
        assert any("Received invoice has no PO number" in r for r in verdict["reasons"])

    def test_po_missing_on_pdf_side_fails_strict(self):
        api = api_for(make_received(), pdf=make_pdf(purchase_order_number=None))
        verdict = sole_fail(run_consolidator(api))
        assert any("Invoice copy shows no PO number" in r for r in verdict["reasons"])

    def test_po_missing_both_sides_fails_strict(self):
        api = api_for(
            make_received(purchaseOrderNumber=None),
            pdf=make_pdf(purchase_order_number=None),
        )
        verdict = sole_fail(run_consolidator(api))
        assert any(
            "No PO number on the received invoice or the invoice copy" in r
            for r in verdict["reasons"]
        )

    def test_date_mismatch_reports_both_dates(self):
        api = api_for(make_received(), pdf=make_pdf(invoice_date="2026-07-12"))
        verdict = sole_fail(run_consolidator(api))
        assert any("2026-07-13" in r and "2026-07-12" in r for r in verdict["reasons"])

    def test_three_cent_total_difference_fails_with_both_totals(self):
        api = api_for(make_received(), pdf=make_pdf(total_incl_tax=182.12))
        verdict = sole_fail(run_consolidator(api))
        assert any("$182.09" in r and "$182.12" in r for r in verdict["reasons"])

    def test_unreadable_total_fails(self):
        api = api_for(make_received(), pdf=make_pdf(total_incl_tax=None))
        verdict = sole_fail(run_consolidator(api))
        assert any("Could not read the total" in r for r in verdict["reasons"])

    def test_credit_is_never_auto_reconciled(self):
        api = api_for(
            make_received(creditRequest=True, total=-50.0),
            pdf=make_pdf(total_incl_tax=-50.0),
        )
        verdict = sole_fail(run_consolidator(api))
        assert any("Credit" in r for r in verdict["reasons"])
        assert api.updated == []

    def test_update_failure_demotes_all_statement_invoices(self):
        api = api_for(make_received(), update_error="API error 500: boom")
        verdict = sole_fail(run_consolidator(api))
        assert any(r.startswith("Statement update failed:") for r in verdict["reasons"])

    def test_multiple_failures_all_reported(self):
        api = api_for(
            make_received(purchaseOrderNumber=None),
            pdf=make_pdf(invoice_date="2026-07-01", total_incl_tax=99.0),
        )
        verdict = sole_fail(run_consolidator(api))
        assert len(verdict["reasons"]) >= 3


class TestStatementMatching:
    def test_invoice_outside_statement_period_needs_statement(self):
        api = api_for(make_received(invoicedAt="2026-09-15"))
        result = run_consolidator(api)
        assert result["summary"]["needs_statement"] == 1
        assert result["needs_statement"][0]["supplier_name"] == "Angus Meats"
        assert api.updated == []

    def test_end_date_plus_one_is_inclusive(self):
        # endAt 2026-07-30T18:00Z — the UI scopes to 2026-07-31, so a 31 Jul
        # invoice still belongs to this statement.
        api = api_for(
            make_received(invoicedAt="2026-07-31"),
            pdf=make_pdf(invoice_date="2026-07-31"),
        )
        assert run_consolidator(api)["summary"]["reconciled"] == 1

    def test_needs_statement_reports_would_reconcile_count(self):
        good = make_received(
            id="a", invoiceNumber="A-1", supplierId="other", supplierName="Orphan Foods"
        )
        bad = make_received(
            id="b",
            invoiceNumber="B-1",
            supplierId="other",
            supplierName="Orphan Foods",
            fileId="file-b",
        )
        api = Api(
            statements=[make_statement()],  # only covers Angus Meats
            received=[good, bad],
            pdfs={
                FILE_ID: make_pdf(invoice_number="A-1"),
                "file-b": {"error": "corrupt"},
            },
        )
        result = run_consolidator(api)
        ns = result["needs_statement"][0]
        assert ns["invoice_count"] == 2
        assert ns["would_reconcile"] == 1
        outcomes = {r["invoice"]: r["outcome"] for r in result["results"]}
        assert outcomes["A-1"] == "needs statement (all checks pass)"
        assert outcomes["B-1"] == "needs statement (fails checks)"

    def test_create_missing_statements_only_when_asked(self):
        orphan = make_received(supplierId="other", supplierName="Orphan Foods")
        api = Api(statements=[], received=[orphan], pdfs={FILE_ID: make_pdf()})
        result = run_consolidator(api)
        assert api.created == []
        assert result["summary"]["needs_statement"] == 1

    def test_create_missing_statements_creates_and_reconciles(self):
        orphan = make_received(supplierId="other", supplierName="Orphan Foods")
        api = Api(statements=[], received=[orphan], pdfs={FILE_ID: make_pdf()})
        result = run_consolidator(api, create_missing_statements=True)
        assert len(api.created) == 1
        body = api.created[0]
        assert body["supplierName"] == "Orphan Foods"
        assert body["statementAmount"] == 0
        assert body["reconciledStockReceivedItems"][0]["reconciled"] is True
        assert result["reconciled"][0]["outcome"] == "reconciled (new statement)"
        assert result["summary"]["needs_statement"] == 0

    def test_create_missing_is_a_noop_in_dry_run(self):
        orphan = make_received(supplierId="other", supplierName="Orphan Foods")
        api = Api(statements=[], received=[orphan], pdfs={FILE_ID: make_pdf()})
        result = run_consolidator(
            api, create_missing_statements=True, mode="approve_all"
        )
        assert api.created == []
        assert result["summary"]["needs_statement"] == 1

    def test_failing_invoices_never_join_a_created_statement(self):
        good = make_received(
            id="a", invoiceNumber="A-1", supplierId="other", supplierName="Orphan Foods"
        )
        bad = make_received(
            id="b",
            invoiceNumber="B-1",
            supplierId="other",
            supplierName="Orphan Foods",
            fileId="file-b",
        )
        api = Api(
            statements=[],
            received=[good, bad],
            pdfs={FILE_ID: make_pdf(invoice_number="A-1"), "file-b": {"error": "x"}},
        )
        run_consolidator(api, create_missing_statements=True)
        assert len(api.created) == 1
        ids = [i["id"] for i in api.created[0]["reconciledStockReceivedItems"]]
        assert ids == ["a"]


class TestComparisonEvidence:
    """Every verdict must carry the ACTUAL values read from each side, so the
    report can prove what the checks compared."""

    def test_comparison_holds_both_sides_values(self):
        api = api_for(make_received())
        verdict = run_consolidator(api)["reconciled"][0]
        c = verdict["comparison"]
        assert c["po_number"] == {
            "loaded": "1521021",
            "document": "PO#1521021",
            "match": True,
        }
        assert c["invoice_date"] == {
            "loaded": "2026-07-13",
            "document": "2026-07-13",
            "match": True,
        }
        assert c["total_incl_tax"] == {
            "loaded": "$182.09",
            "document": "$182.09",
            "match": True,
        }
        assert c["invoice_number"]["document"] == "1008102"
        assert c["invoice_number"]["match"] is True

    def test_mismatch_fields_are_marked_false(self):
        api = api_for(make_received(), pdf=make_pdf(total_incl_tax=99.0))
        verdict = run_consolidator(api)["not_reconciled"][0]
        c = verdict["comparison"]
        assert c["total_incl_tax"]["match"] is False
        assert c["po_number"]["match"] is True  # other checks unaffected

    def test_unrun_checks_are_marked_none(self):
        api = api_for(make_received(fileId=None))
        verdict = run_consolidator(api)["not_reconciled"][0]
        assert all(f["match"] is None for f in verdict["comparison"].values()), verdict[
            "comparison"
        ]

    def test_invoice_number_check_passes_and_is_reported(self):
        api = api_for(make_received())
        verdict = run_consolidator(api)["reconciled"][0]
        assert verdict["checks"]["invoice_number_match"] == "pass"

    def test_display_rows_show_side_by_side_values_with_ticks(self):
        api = api_for(make_received(), pdf=make_pdf(total_incl_tax=182.12))
        row = run_consolidator(api)["results"][0]
        assert row["invno_doc"] == "1008102 ✓"
        assert row["po_loaded"] == "1521021"
        assert row["po_doc"] == "PO#1521021 ✓"
        assert row["date_loaded"] == "2026-07-13"
        assert row["date_doc"] == "2026-07-13 ✓"
        assert row["total_loaded"] == "$182.09"
        assert row["total_doc"] == "$182.12 ✗"

    def test_missing_copy_marks_document_side(self):
        api = api_for(make_received(fileId=None))
        verdict = run_consolidator(api)["not_reconciled"][0]
        assert (
            verdict["comparison"]["total_incl_tax"]["document"] == "(no copy attached)"
        )
        row = run_consolidator(api_for(make_received(fileId=None)))["results"][0]
        assert row["total_doc"] == "(no copy attached)"

    def test_unreadable_copy_marks_document_side(self):
        api = api_for(make_received(), pdf={"error": "corrupt"})
        verdict = run_consolidator(api)["not_reconciled"][0]
        assert verdict["comparison"]["po_number"]["document"] == "(unreadable)"

    def test_missing_document_value_renders_dash_with_cross(self):
        # Strict PO policy: PDF showing no PO number is a failed check.
        api = api_for(make_received(), pdf=make_pdf(purchase_order_number=None))
        row = run_consolidator(api)["results"][0]
        assert row["po_doc"] == "— ✗"
        assert row["po_loaded"] == "1521021"

    def test_unverifiable_copy_gets_no_symbols(self):
        # No copy attached — the field checks never ran, so no ✓/✗ is claimed.
        api = api_for(make_received(fileId=None))
        row = run_consolidator(api)["results"][0]
        assert row["total_doc"] == "(no copy attached)"
        assert row["po_doc"] == "(no copy attached)"


class TestReport:
    def test_statement_summary_reports_difference(self):
        api = api_for(make_received())
        result = run_consolidator(api, mode="approve_all")
        s = result["statements"][0]
        assert s["statement_amount"] == "$1,528.70"
        assert s["reconciled_amount"] == "$0.00"
        assert s["difference"] == "$1,528.70"

    def test_display_rows_cover_every_candidate(self):
        good = make_received()
        bad = make_received(id="recv-2", invoiceNumber="1008103", fileId=None)
        api = Api(
            statements=[make_statement()],
            received=[good, bad],
            pdfs={FILE_ID: make_pdf()},
        )
        result = run_consolidator(api)
        assert {r["invoice"] for r in result["results"]} == {"1008102", "1008103"}
        outcomes = {r["invoice"]: r["outcome"] for r in result["results"]}
        assert outcomes["1008102"] == "reconciled"
        assert outcomes["1008103"] == "not reconciled"


class TestRunModes:
    def test_approve_all_is_dry_run(self):
        api = api_for(make_received())
        result = run_consolidator(api, mode="approve_all")
        assert result["dry_run"] is True
        assert result["mode"] == "approve_all"
        assert api.updated == [] and api.created == []

    def test_unset_is_dry_run_and_flagged(self):
        api = api_for(make_received())
        result = run_consolidator(api, mode="unset")
        assert result["dry_run"] is True
        assert result["mode_unset"] is True
        assert api.updated == []

    def test_approve_fixes_reconciles_but_no_auto_create(self):
        # A supplier with a received invoice but NO covering statement: in
        # approve_fixes we must NOT auto-create a statement.
        inv = make_received(invoiceNumber="NOPE", purchaseOrderNumber="9999")
        api = Api(statements=[], received=[inv], pdfs={FILE_ID: make_pdf()})
        run_consolidator(api, mode="approve_fixes")
        assert api.created == []

    def test_autopilot_auto_creates_missing_statements(self):
        # Same setup, autopilot → statements auto-created for the passing invoice.
        inv = make_received()
        api = Api(statements=[], received=[inv], pdfs={FILE_ID: make_pdf()})
        run_consolidator(api, mode="autopilot")
        assert api.created  # created without the LLM passing create_missing
