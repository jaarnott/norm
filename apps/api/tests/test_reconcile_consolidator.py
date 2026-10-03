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
        self,
        statements,
        received,
        pdfs=None,
        update_error=None,
        create_error=None,
        evidence_error=None,
        splits=None,
    ):
        self.evidence_error = evidence_error
        self.splits = splits or {}
        self.statements = statements
        self.received = received
        self.pdfs = pdfs or {}
        self.update_error = update_error
        self.create_error = create_error
        self.split_writes = []  # norm.record_split_order calls
        self.updated = []  # (statement_id, body)
        self.created = []  # body
        self.seen = []
        self.resolved = None

    def call_api(self, connector, action, params=None):
        params = params or {}
        self.seen.append((action, dict(params)))
        if action == "resolve_dates":
            return dict(self.resolved) if self.resolved else {"error": "offline"}
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
        if action == "invoice_copy_evidence":
            return self.copy_evidence(params)
        if action == "record_split_order":
            self.split_writes.append(params)
            return {
                str(i["id"]): {"ok": True, "noted": True, "referenced": True}
                for i in params.get("invoices") or []
            }
        raise AssertionError(f"unexpected action {action}")

    def copy_evidence(self, params):
        """Stand in for norm.invoice_copy_evidence.

        Deliberately calls the REAL `po_verdict` rather than re-deciding here:
        the rule for telling OUR purchase order from the supplier's own order
        number is the thing these tests exist to protect, so a fake that
        reimplemented it would prove nothing.
        """
        from app.services.invoice_evidence import SOURCE_EXTRACTED, po_verdict

        if self.evidence_error:
            # What the SANDBOX hands the consolidator on failure, not what the
            # handler returns: _do_api_call raises on success=False and call_api
            # turns that into {"error": ...}.
            return {"error": self.evidence_error}
        out = {}
        for inv in params.get("invoices") or []:
            iid, file_id = str(inv.get("id")), inv.get("fileId")
            if not file_id:
                out[iid] = {"error": "no invoice copy attached"}
                continue
            pdf = self.pdfs.get(file_id)
            if not isinstance(pdf, dict) or pdf.get("error"):
                out[iid] = {
                    "error": (pdf or {}).get("error", "unreadable")
                    if isinstance(pdf, dict)
                    else "unreadable"
                }
                continue
            header = {**pdf, "_source": SOURCE_EXTRACTED}
            state, note = po_verdict(inv.get("purchaseOrderNumber"), header)
            header["_po_verdict"], header["_po_note"] = state, note
            # A confirmed split, as the real service stamps it when Loaded's PO
            # field is empty because a sibling invoice holds the 1:1 link.
            if iid in self.splits:
                header["_split"] = {"kind": "split", **self.splits[iid]}
                header["_po_verdict"] = "match"
                header["_po_note"] = "split delivery"
            out[iid] = header
        # Flat, exactly as function_executor._do_api_call delivers it:
        # `return handler_result.get("data")`. Returning the handler's
        # {"success", "data"} envelope here is what let a double-unwrap in the
        # consolidator pass green while reading nothing in production.
        return out

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
        # The receive path's schema splits these two on purpose.
        "customer_purchase_order_number": "PO#1521021",
        "supplier_order_number": None,
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
            make_received(), pdf=make_pdf(customer_purchase_order_number="po# 1521021")
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
        api = api_for(
            make_received(), pdf=make_pdf(customer_purchase_order_number="1520999")
        )
        verdict = sole_fail(run_consolidator(api))
        assert any("1521021" in r and "1520999" in r for r in verdict["reasons"])

    def test_po_missing_on_loaded_side_fails_and_names_what_was_found(self):
        """Still a failure, but the report now carries the number the copy
        shows — that is the whole value of having read the copy."""
        api = api_for(make_received(purchaseOrderNumber=None))
        verdict = sole_fail(run_consolidator(api))
        assert any(
            "has no PO number" in r and "1521021" in r for r in verdict["reasons"]
        )

    def test_po_missing_on_pdf_side_fails_strict(self):
        api = api_for(
            make_received(), pdf=make_pdf(customer_purchase_order_number=None)
        )
        verdict = sole_fail(run_consolidator(api))
        assert any("PO number mismatch" in r for r in verdict["reasons"])

    def test_the_suppliers_own_order_number_is_not_a_mismatch(self):
        """Service Foods, 17 Aug 2026 — 27 of 67 failures. The copy prints the
        supplier's ORD… number beside our PO; Loaded holds ours. Reading a
        single `purchase_order_number` made those look like a conflict."""
        api = api_for(
            make_received(),
            pdf=make_pdf(
                customer_purchase_order_number="1521021",
                supplier_order_number="ORD10658598",
            ),
        )
        out = run_consolidator(api)
        assert out["summary"]["reconciled"] == 1, out["not_reconciled"]

    def test_loaded_holding_the_suppliers_number_reconciles_and_says_so(self):
        """Loaded's purchaseOrderNumber is often the supplier's own number
        rather than a Loaded order — both sides then name the same document."""
        api = api_for(
            make_received(purchaseOrderNumber="ORD10658598"),
            pdf=make_pdf(
                customer_purchase_order_number="1521021",
                supplier_order_number="ORD10658598",
            ),
        )
        out = run_consolidator(api)
        assert out["summary"]["reconciled"] == 1, out["not_reconciled"]
        assert any(
            "supplier's own order number" in (r.get("notes") or "")
            for r in out["results"]
        )

    def test_po_missing_both_sides_fails_strict(self):
        api = api_for(
            make_received(purchaseOrderNumber=None),
            pdf=make_pdf(customer_purchase_order_number=None),
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
        api = api_for(
            make_received(), pdf=make_pdf(customer_purchase_order_number=None)
        )
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


class TestPrintedDateFormats:
    """The copy keeps dates AS PRINTED; Loaded is ISO. A raw string compare
    failed every differently-formatted date — the 21 Aug 2026 daily run
    reconciled 0 of 100 invoices, the vast majority blocked only by format.
    Every shape here was observed on a real supplier invoice or in that
    thread's report."""

    def _verdict_for(self, printed):
        api = api_for(make_received(), pdf=make_pdf(invoice_date=printed))
        return api, run_consolidator(api)

    def test_every_printed_shape_of_the_same_date_reconciles(self):
        for printed in (
            "13/07/26",
            "13/07/2026",
            "13 Jul 26",
            "13 Jul 2026",
            "13 July 2026",
            "Jul 13, 26",
            "Jul 13, 2026",
            "13.07.2026",
            "13-07-26",
        ):
            api, result = self._verdict_for(printed)
            assert result["summary"]["reconciled"] == 1, (printed, result)
            row = result["reconciled"][0]
            assert row["checks"]["date_match"] == "pass", printed
            # the report's table shows the resolved ISO form, agreeing with ✓
            assert row["comparison"]["invoice_date"]["document"] == "2026-07-13"

    def test_a_genuinely_different_date_still_fails_whatever_the_format(self):
        api, result = self._verdict_for("12/07/26")
        verdict = sole_fail(result)
        assert any("2026-07-13" in r and "2026-07-12" in r for r in verdict["reasons"])

    def test_unparseable_text_stays_an_honest_failure(self):
        api, result = self._verdict_for("next Tuesday")
        verdict = sole_fail(result)
        assert verdict["checks"]["date_match"] == "fail"


class TestPeriodResolution:
    def test_period_resolves_to_calendar_dates(self):
        api = Api(statements=[], received=[])
        api.resolved = {
            "window": {
                "start": "2026-07-01T07:00:00+12:00",
                "end": "2026-08-01T06:59:59+12:00",
                "trading_aligned": True,
            }
        }
        run_consolidator(api, period="last month")
        stmt = next(p for a, p in api.seen if a == "list_supplier_statements")
        assert stmt["from_iso"].startswith("2026-07-01T00:00:00")
        # the trading window ends 06:59 on 1 Aug — that day is not July (it
        # used to be included: consolidator review, 1 Oct 2026)
        assert stmt["to_iso"].startswith("2026-07-31T23:59:59")

    def test_no_period_keeps_the_thirty_day_default(self):
        api = Api(statements=[], received=[])
        run_consolidator(api)
        stmt = next(p for a, p in api.seen if a == "list_supplier_statements")
        assert stmt["from_iso"].startswith("2026-06-17")  # today (17 Jul) - 30 days
        assert not [x for x in api.seen if x[0] == "resolve_dates"]


class TestSplitOrdersAreRecorded:
    """The write half, at the consolidator level.

    A split is already RECONCILED by the evidence service — the write only
    persists why, so the run mode governs the write and never the outcome.
    Loaded is 1:1 PO<->invoice, so a split delivery leaves every invoice after
    the first with an empty PO field; 13 of 18 blocked invoices at Bessie &
    Engineers were exactly this (23 Aug 2026).
    """

    SPLIT = {"order_number": "1521191", "sibling_reference": "109958939"}

    def _api(self):
        inv = make_received(purchaseOrderNumber=None)
        return api_for(inv, splits={"recv-1": self.SPLIT})

    def test_approve_fixes_records_it_on_the_invoice(self):
        api = self._api()
        out = run_consolidator(api, mode="approve_fixes")
        assert len(api.split_writes) == 1
        sent = api.split_writes[0]["invoices"][0]
        assert sent["order_number"] == "1521191"
        assert sent["sibling_reference"] == "109958939"
        assert out["split_orders_recorded"] == ["1008102"]

    def test_approve_all_suggests_the_fix_and_writes_nothing(self):
        api = self._api()
        out = run_consolidator(api, mode="approve_all")
        assert api.split_writes == []
        assert out["split_orders_recorded"] == []
        assert len(out["split_orders_suggested"]) == 1
        assert "1521191" in out["split_orders_suggested"][0]["fix"]

    def test_the_split_reconciles_regardless_of_the_write(self):
        """Correctness must not depend on a write that a user's Save can undo."""
        for mode in ("approve_all", "approve_fixes"):
            out = run_consolidator(self._api(), mode=mode)
            assert out["summary"]["reconciled"] == 1, mode

    def test_a_failed_write_does_not_unreconcile_anything(self):
        api = self._api()

        def boom(connector, action, params=None):
            if action == "record_split_order":
                return {"error": "Loaded 502"}
            return Api.call_api(api, connector, action, params)

        api.call_api = boom
        out = run_consolidator(api, mode="approve_fixes")
        assert out["summary"]["reconciled"] == 1
        assert out["split_orders_recorded"] == []

    def test_nothing_is_written_when_there_is_no_split(self):
        api = api_for(make_received())
        run_consolidator(api, mode="approve_fixes")
        assert api.split_writes == []


class TestTheReportTheEmailIsWrittenFrom:
    """The compact `report` — what a person reads, beside the card's detail.

    The 29 Aug 2026 run across six venues rendered a four-row comparison table
    for EVERY invoice, ticks included, and buried the four lines that needed
    someone at the very bottom. Worse, one of those lines — three invoices
    where Loaded holds no PO though the copy shows one — was a single job
    scattered across three venue sections.

    So the report groups by CAUSE, because the cause is the job, and it is
    classified from `checks`/`comparison` rather than the reason prose: the
    wording changes, the structure does not. Replayed over eight real
    production runs, every exception classified and none fell through.
    """

    def _causes(self, result):
        return {e["cause"]: e for e in result["report"]["exceptions"]}

    def test_loaded_missing_a_po_the_copy_shows_is_its_own_cause(self):
        """The fixable one, and the most common: someone adds the number in
        Loaded. It must be separable from a genuine disagreement."""
        inv = make_received(purchaseOrderNumber=None)
        out = run_consolidator(
            api_for(inv, make_pdf(customer_purchase_order_number="3459273"))
        )
        ex = self._causes(out)["po_missing_in_loaded"]
        assert ex["invoices"][0]["detail"] == "copy shows 3459273"
        assert ex["invoices"][0]["invoice"] == "1008102"

    def test_two_real_po_numbers_are_a_disagreement_not_a_gap(self):
        out = run_consolidator(
            api_for(
                make_received(purchaseOrderNumber="1520600"),
                make_pdf(customer_purchase_order_number="1519999"),
            )
        )
        causes = self._causes(out)
        assert "po_mismatch" in causes and "po_missing_in_loaded" not in causes

    def test_a_credit_is_filed_as_a_credit_even_with_no_copy(self):
        """It reads as both; a credit needs a person whatever else is true, so
        the credit is the job and it must not be double-counted."""
        out = run_consolidator(
            api_for(make_received(total=-51.37, fileId=None), pdf=None)
        )
        causes = self._causes(out)
        assert "credit_manual" in causes and "no_copy" not in causes
        assert sum(len(e["invoices"]) for e in out["report"]["exceptions"]) == 1

    def test_every_exception_carries_what_the_email_needs(self):
        out = run_consolidator(api_for(make_received(fileId=None), pdf=None))
        entry = out["report"]["exceptions"][0]["invoices"][0]
        assert set(entry) == {
            "venue",
            "invoice",
            "supplier",
            "total",
            "detail",
            "comparison",
        }

    def test_a_reconciled_invoice_raises_no_exception(self):
        out = run_consolidator(api_for(make_received()))
        assert out["report"]["counts"]["reconciled"] == 1
        assert out["report"]["exceptions"] == []

    def test_a_statement_not_yet_issued_is_counted_not_listed(self):
        """A month in progress reads as "$0.00 vs reconciled", which is not a
        discrepancy. La Zeppa returned 67 such rows for two invoices of real
        work; listing them is what drowned the report."""
        out = run_consolidator(
            api_for(
                make_received(),
                # The live shape: no statement issued yet (amount 0), but
                # invoices already reconciled against the period.
                statement=make_statement(statementAmount=0, reconciledAmount=182.09),
            )
        )
        assert out["report"]["statements_not_yet_issued"] == 1
        assert out["report"]["statement_differences"] == []

    def test_a_real_difference_is_listed(self):
        out = run_consolidator(
            api_for(make_received(), statement=make_statement(statementAmount=1600.00))
        )
        diffs = out["report"]["statement_differences"]
        assert len(diffs) == 1 and diffs[0]["supplier"] == "Angus Meats"

    def test_a_statement_that_balances_is_neither(self):
        out = run_consolidator(
            api_for(
                make_received(),
                statement=make_statement(statementAmount=0, reconciledAmount=0),
            )
        )
        assert out["report"]["statement_differences"] == []
        assert out["report"]["statements_not_yet_issued"] == 0

    def test_a_failing_invoice_carries_its_comparison(self):
        """What shows WHICH field disagreed. Stripping every comparison left
        the report long and useless at once — per-venue sections rebuilt from
        the exception list, with none of the detail that justified them."""
        inv = make_received(purchaseOrderNumber=None)
        out = run_consolidator(
            api_for(inv, make_pdf(customer_purchase_order_number="3459273"))
        )
        comp = out["report"]["exceptions"][0]["invoices"][0]["comparison"]
        assert comp["po_number"]["loaded"] in (None, "")
        assert comp["po_number"]["document"] == "3459273"
        assert comp["po_number"]["match"] is False
        # and the fields that DID agree still say so, so a reader can see the
        # failure is isolated to one field
        assert comp["total_incl_tax"]["match"] is True
        assert comp["invoice_number"]["match"] is True

    def test_a_reconciled_invoice_never_reaches_the_report(self):
        """Four ticks under an invoice that is already fine is exactly what
        buried the ones that were not — eight of them on the 29 Aug run."""
        out = run_consolidator(api_for(make_received()))
        assert out["report"]["exceptions"] == []
        assert out["report"]["counts"]["reconciled"] == 1

    def test_a_no_copy_failure_still_carries_its_comparison(self):
        """Even when every copy-side cell is a sentinel — the reader needs to
        see that Loaded's side exists and the copy's does not."""
        out = run_consolidator(api_for(make_received(fileId=None), pdf=None))
        comp = out["report"]["exceptions"][0]["invoices"][0]["comparison"]
        # Loaded's side per field; the copy's side once, because it is the
        # same sentinel on every field.
        assert comp["loaded"]["total_incl_tax"]
        assert "no copy" in str(comp["document"]).lower()

    def test_a_rounding_difference_is_counted_not_listed(self):
        """A cent across a month of invoices is rounding. The old report put
        "$5,377.56 vs $5,377.55 -> $0.01" beside a real $4,045 crossover, which
        is how a real one gets missed."""
        out = run_consolidator(
            api_for(
                make_received(),
                statement=make_statement(
                    statementAmount=182.10, reconciledAmount=182.09
                ),
            )
        )
        assert out["report"]["statement_differences"] == []
        assert out["report"]["statements_off_by_rounding"] == 1

    def test_a_difference_worth_acting_on_survives_the_filter(self):
        """The Dunedin Bidfood crossover is ~$4,045 — it must never be filtered."""
        out = run_consolidator(
            api_for(
                make_received(),
                statement=make_statement(
                    statementAmount=4227.09, reconciledAmount=182.09
                ),
            )
        )
        diffs = out["report"]["statement_differences"]
        assert len(diffs) == 1 and diffs[0]["difference"] == "$4,045.00"
        assert out["report"]["statements_off_by_rounding"] == 0

    def test_the_card_keeps_the_detail_the_report_leaves_out(self):
        """The report is a projection, never a replacement — the comparisons
        still ship for the card and for anyone checking one invoice."""
        out = run_consolidator(api_for(make_received()))
        assert out["reconciled"][0]["comparison"]["po_number"]["match"] is True
        assert out["results"] and out["statements"]


class TestReviewFixes:
    """Consolidator review, 1 Oct 2026."""

    def _bidfood(self):
        a = make_received(
            id="r-a", supplierId="s-a", supplierName="Bidfood Foodservice"
        )
        b = make_received(id="r-b", supplierId="s-b", supplierName="Bidfood Fresh")
        return Api(
            statements=[
                make_statement(supplierId="s-a", supplierName="Bidfood Foodservice")
            ],
            received=[a, b],
            pdfs={FILE_ID: make_pdf()},
        )

    def test_a_partial_name_matches_nothing_and_offers_the_real_names(self):
        """Never partial: 'Bidfood' must not mean both Bidfood businesses."""
        api = self._bidfood()
        result = run_consolidator(api, suppliers=["Bidfood"])
        assert result["summary"]["reconciled"] == 0
        assert api.updated == []
        assert result["report"]["unmatched_suppliers"] == [
            {
                "asked": "Bidfood",
                "did_you_mean": ["Bidfood Foodservice", "Bidfood Fresh"],
            }
        ]

    def test_an_exact_name_still_matches_case_and_punctuation_aside(self):
        api = self._bidfood()
        result = run_consolidator(api, suppliers=["BIDFOOD FOODSERVICE"])
        assert "unmatched_suppliers" not in result["report"]
        assert result["summary"]["reconciled"] == 1

    def test_a_credit_is_not_also_a_total_mismatch(self):
        """Loaded stores a credit negative, the copy prints it positive."""
        api = api_for(
            make_received(creditRequest=True, total=-12.65),
            pdf=make_pdf(total_incl_tax=12.65),
        )
        verdict = sole_fail(run_consolidator(api))
        assert any("Credit" in r for r in verdict["reasons"])
        assert not any("Total mismatch" in r for r in verdict["reasons"])

    def test_statements_asked_for_under_approve_all_say_none_were_made(self):
        orphan = make_received(supplierId="other", supplierName="Orphan Foods")
        api = Api(statements=[], received=[orphan], pdfs={FILE_ID: make_pdf()})
        result = run_consolidator(
            api, create_missing_statements=True, mode="approve_all"
        )
        assert api.created == []
        assert "no statement was created" in result["report"]["statements_not_created"]


class TestARepeatedCauseIsSaidOnce:
    """33 invoices with the same unreadable copy must not say so 33 times.

    On the 22 Sep 2026 La Zeppa run one exception group — "Invoice copy could
    not be read", 33 invoices — was 27 kB of a 31 kB report: a 377-char
    sentence and a 329-char comparison of None/False flags on every invoice,
    for a model that summarised the whole group in one line. The sentence now
    lives on the group; the per-invoice comparison keeps Loaded's side and the
    copy's side and drops the flags. A mismatch keeps everything, because
    there the comparison IS the evidence.
    """

    def _many_no_copy(self, n):
        received = [
            make_received(id=f"inv-{i}", invoiceNumber=str(100000 + i), fileId=None)
            for i in range(n)
        ]
        return Api(statements=[make_statement()], received=received, pdfs={})

    def test_the_sentence_appears_once_and_the_group_is_small(self):
        import json

        out = run_consolidator(self._many_no_copy(33))
        group = out["report"]["exceptions"][0]
        assert group["cause"] == "no_copy" and len(group["invoices"]) == 33
        assert group["detail"], "the shared sentence moved to the group"
        assert all("detail" not in i for i in group["invoices"])
        blob = json.dumps(group)
        assert blob.count(group["detail"]) == 1
        assert len(blob) < 10_000, f"33 no-copy invoices came to {len(blob):,} chars"

    def test_a_lone_invoice_keeps_its_own_detail(self):
        """One invoice has nothing to share with — the per-invoice contract
        pinned by test_every_exception_carries_what_the_email_needs holds."""
        out = run_consolidator(self._many_no_copy(1))
        entry = out["report"]["exceptions"][0]["invoices"][0]
        assert "detail" in entry

    def test_a_generic_comparison_keeps_both_sides_without_flags(self):
        out = run_consolidator(self._many_no_copy(2))
        comp = out["report"]["exceptions"][0]["invoices"][0]["comparison"]
        assert comp["loaded"]["total_incl_tax"]
        assert "no copy" in str(comp["document"]).lower()
        assert "match" not in str(comp)
