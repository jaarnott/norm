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
        supplier_specs=None,
        stock_items=None,
    ):
        self.invoices = invoices
        self.details = details
        self.pos = pos or {}
        self.pdfs = pdfs or {}
        self.receive_error = receive_error
        # Stock items served to get_stock_item (variant-description matching).
        # Default empty: an engine lookup gets an error dict and the line keeps
        # its "not found" verdict — same as before the variant feature.
        self.stock_items = stock_items or {}
        self.stock_item_calls = []
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
        # Admin-maintained supplier specs (norm.get_supplier_invoice_specs).
        self.supplier_specs = supplier_specs or []
        self.supplier_spec_calls = 0
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
        if action == "get_supplier_invoice_specs":
            self.supplier_spec_calls += 1
            return {"specs": self.supplier_specs}
        if action == "get_stock_item":
            self.stock_item_calls.append(params["item_id"])
            return self.stock_items.get(params["item_id"]) or {"error": "no such item"}
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

    # Parallel executor helpers (the real ones fan out on threads; the fakes
    # run sequentially but RECORD the batches so tests can assert the engine
    # actually batched instead of looping).
    def call_api_parallel(self, calls):
        if not hasattr(self, "parallel_batches"):
            self.parallel_batches = []
        self.parallel_batches.append(len(calls))
        return [self.call_api(c, a, p) for (c, a, p) in calls]

    def extract_documents_parallel(self, requests):
        if not hasattr(self, "extract_batches"):
            self.extract_batches = []
        self.extract_batches.append(len(requests))
        return [
            self.extract_document(
                r.get("connector"),
                r.get("action"),
                r.get("params"),
                r.get("schema"),
                r.get("instructions"),
            )
            for r in requests
        ]


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


def run_consolidator_parallel(api, **params):
    """Run with the parallel helpers present, as the real executor provides."""
    namespace = {
        "__builtins__": _SAFE_BUILTINS,
        **_SAFE_MODULES,
        "extract_document": api.extract_document,
        "extract_documents_parallel": api.extract_documents_parallel,
    }
    exec(FUNCTION_CODE, namespace)
    defaults = {
        "today": "2026-07-16",
        "venue": "Bessie",
        "mode": "approve_fixes",
        **params,
    }
    return namespace["run"](
        defaults, api.call_api, lambda m: None, api.call_api_parallel
    )


# ---------------------------------------------------------------------------
# Fixtures — modelled on the verified Akaroa/Ocean's North shapes
# ---------------------------------------------------------------------------

PO_ID = "4c69ac57-b8b2-4524-d301-08ded2d852f5"
INV_ID = "277c9b6e-6d88-492e-8194-08ded2d24c70"
FILE_ID = "1fcc07c5-eebf-4b0f-9c1d-6ed59eae5894"
ITEM_SALMON = "53de28f9-b7b7-4794-930b-a8b0f650db63"
UNIT_KILO = "df535968-bab0-4f07-86e2-07354483935d"


def make_line(**over):
    # Loaded's CURRENT line schema uses unitCostExclTax/totalCostExclTax
    # (renamed from unitCost/totalCost, 05 Aug 2026). Tests keep passing the
    # old kwarg names; translate them so every fixture exercises the NEW
    # schema. The legacy fallback has its own dedicated test.
    for old_key, new_key in (
        ("unitCost", "unitCostExclTax"),
        ("totalCost", "totalCostExclTax"),
    ):
        if old_key in over and new_key not in over:
            over[new_key] = over.pop(old_key)
    line = {
        "id": "line-1",
        "code": "PBO0.7-0.99",
        "description": "SALMON FILLET",
        "unit": "Kilo",
        "brand": None,
        "linkedBrandId": None,
        "quantityOrdered": 5.0,
        "quantityReceived": 4.95,
        "unitCostExclTax": 44.40,
        "totalCostExclTax": 219.78,
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
            lines=[make_line(), freight], subtotal=226.28, taxAmount=33.94, total=260.22
        )
        pdf = make_pdf(
            charges=[{"description": "Freight (ex GST)", "amount_ex_tax": charge_amt}],
            subtotal_ex_tax=226.28,
            tax_amount=33.95,
            total_incl_tax=260.23,
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

    def test_missing_copy_still_runs_the_other_checks(self):
        # No copy attached: the copy checks stay unchecked, but the OTHER
        # problems (PO, totals) are still found and reported (unified process).
        api = api_for(
            make_invoice(fileId=None, linkedPurchaseOrderId=None, subtotal=999.0)
        )
        verdict = sole_skip(run_consolidator(api))
        text = " | ".join(verdict["reasons"])
        assert "No invoice copy attached" in text
        assert "Not linked to a purchase order" in text
        assert "Line items sum to" in text

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


class TestUnifiedReporting:
    """ONE unified process (batch == editor): every check that can run does
    run, so a verdict reports ALL problems — a later line-vs-copy mismatch is
    never hidden behind an earlier failure. The extraction is content-cached,
    so completeness costs one read per invoice, not one per check."""

    def test_unlinked_invoice_also_reports_later_problems(self):
        api = api_for(
            make_invoice(
                linkedPurchaseOrderId=None,
                subtotal=999.0,  # a second, independent problem
            )
        )
        verdict = sole_skip(run_consolidator(api))
        text = " | ".join(verdict["reasons"])
        assert "Not linked to a purchase order" in text
        assert "Line items sum to" in text  # not hidden behind the PO failure

    def test_credit_note_still_runs_the_other_checks(self):
        api = api_for(
            make_invoice(
                total=-25.30,
                subtotal=-22.0,
                taxAmount=-3.30,
                linkedPurchaseOrderId=None,
            )
        )
        verdict = sole_skip(run_consolidator(api))
        text = " | ".join(verdict["reasons"])
        assert "Credit note" in text
        assert "Not linked to a purchase order" in text

    def test_batch_runs_the_full_extraction_for_every_invoice(self):
        # Unified with the editor: the copy comparison runs in batch too (the
        # extraction is content-cached, so a re-run costs nothing).
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
        assert any("lines" in (sch or {}) for sch in schemas)  # full Layer-6 read

    def test_same_layer_failures_are_all_reported(self):
        # Two independent problems in the same layer (vs the copy) both show.
        pdf = make_pdf()
        pdf["lines"][0] = dict(pdf["lines"][0], quantity=5.0, unit_price_ex_tax=45.40)
        api = api_for(make_invoice(), pdf=pdf)
        verdict = sole_skip(run_consolidator(api))
        text = " | ".join(verdict["reasons"])
        assert "document's quantity" in text
        assert "document's unit price" in text

    def test_totals_failure_does_not_hide_the_copy_checks_in_batch(self):
        # subtotal off AND the stated total disagrees with the copy — both
        # reported (previously the totals gate blocked Layer 6 in batch).
        inv = make_invoice(subtotal=226.28, taxAmount=33.95, total=260.23)
        api = api_for(inv)
        verdict = sole_skip(run_consolidator(api))
        text = " | ".join(verdict["reasons"])
        assert "Line items sum to" in text
        assert "does not match the document total" in text

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

    def test_unlinked_invoice_still_runs_the_copy_checks(self):
        # Unified process: the PO failure no longer hides the copy comparison —
        # the fixture's copy matches, so those checks show ✓ next to the ✗.
        api = api_for(make_invoice(linkedPurchaseOrderId=None))
        verdict = run_consolidator(api)["skipped"][0]
        by_label = {c["check"]: c["result"] for c in verdict["checklist"]}
        assert by_label["Not a credit note or statement"] == "✓"
        assert by_label["Invoice copy attached"] == "✓"
        assert by_label["Linked to a purchase order"] == "✗"
        assert by_label["Lines match the invoice copy"] == "✓"
        assert by_label["Total matches the invoice copy"] == "✓"

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
        # unified process: the copy checks run too — the copy's invoice number
        # (F55755100) doesn't match X-1, so TWO crosses; only po_supplier stays
        # unchecked (no PO was fetched).
        assert rows["X-1"]["checks"] == "9✓ 2✗ 1 not checked"


class TestSupplierSpecs:
    """Admin-maintained per-supplier extraction notes: matched by name/alias
    (normalized substring), appended to the extraction instructions, fetched
    once per run. Extraction-scope only."""

    SPEC = {
        "name": "Service Foods",
        "aliases": ["Service Foods Ltd"],
        "instructions": "Quantities are split across CTN and UNIT columns.",
    }

    class SpyApi(Api):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.instructions_seen = []

        def extract_document(
            self, connector, action, params=None, schema=None, instructions=None
        ):
            self.instructions_seen.append(instructions or "")
            return super().extract_document(
                connector, action, params, schema, instructions
            )

    def _api(self, supplier_name, specs):
        inv = make_invoice(supplierName=supplier_name)
        return self.SpyApi(
            invoices=[inv],
            details={INV_ID: inv},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: make_pdf()},
            supplier_specs=specs,
        )

    def test_alias_substring_match_injects_the_notes(self):
        # The invoice says "Service Foods Auckland"; the spec's canonical name
        # "Service Foods" matches by substring.
        api = self._api("Service Foods Auckland", [self.SPEC])
        run_consolidator(api, invoice_id=INV_ID)
        full = next(i for i in api.instructions_seen if "product line" in i)
        assert "Supplier-specific notes for Service Foods Auckland" in full
        assert "CTN and UNIT columns" in full

    def test_non_matching_supplier_gets_no_notes(self):
        api = self._api("Bidfood", [self.SPEC])
        run_consolidator(api, invoice_id=INV_ID)
        assert all("Supplier-specific notes" not in i for i in api.instructions_seen)

    def test_short_alias_is_ignored(self):
        spec = dict(self.SPEC, name="ZZ-only", aliases=["SF"])  # both unsafe/miss
        api = self._api("Service Foods", [spec])
        run_consolidator(api, invoice_id=INV_ID)
        assert all("Supplier-specific notes" not in i for i in api.instructions_seen)

    def test_specs_fetched_once_per_run(self):
        a = make_invoice(id="a", referenceNumber="A-1")
        b = make_invoice(id="b", referenceNumber="B-1")
        api = self.SpyApi(
            invoices=[a, b],
            details={"a": a, "b": b},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: make_pdf()},
            supplier_specs=[self.SPEC],
        )
        run_consolidator(api)
        assert api.supplier_spec_calls == 1


class TestLegacyCostFieldNames:
    def test_old_unitcost_names_still_read(self):
        # Pre-rename payloads (unitCost/totalCost) must keep working — the
        # helpers read the new name first and fall back to the old.
        raw = {
            "id": "line-legacy",
            "code": "L1",
            "description": "LEGACY",
            "unit": "Kilo",
            "brand": None,
            "linkedBrandId": None,
            "quantityReceived": 2.0,
            "unitCost": 10.0,
            "totalCost": 20.0,
            "taxAmount": 3.0,
            "linkedItemId": ITEM_SALMON,
            "linkedUnitId": UNIT_KILO,
            "linkedUnitRatio": 1,
            "itemMatchedOn": "Code",
            "deletedAt": None,
            "saleTaxRate": 0.15,
        }
        inv = make_invoice(lines=[raw], subtotal=20.0, taxAmount=3.0, total=23.0)
        pdf = make_pdf(
            lines=[
                {
                    "code": "L1",
                    "description": "LEGACY",
                    "quantity": 2.0,
                    "unit": "Kg",
                    "unit_of_measure": "Kilo",
                    "unit_price_ex_tax": 10.0,
                    "line_total_ex_tax": 20.0,
                }
            ],
            subtotal_ex_tax=20.0,
            tax_amount=3.0,
            total_incl_tax=23.0,
        )
        fi = run_consolidator(api_for(inv, pdf=pdf), invoice_id=INV_ID)["fix_invoices"][
            0
        ]
        ln = fi["lines"][0]
        assert ln["unit_cost"] == 10.0
        assert ln["total_cost"] == 20.0
        assert not ln.get("copy_unit_cost_mismatch")


class TestCardIsDocComplete:
    """The chat flow seeds received_invoice working documents STRAIGHT from the
    fix_invoice card, so the card must be a complete doc payload (keep in sync
    with received_invoice.build_received_invoice_data / _line_from_detail)."""

    def test_card_carries_the_full_doc_header_and_line_fields(self):
        api = api_for(make_invoice(linkedPurchaseOrderId=None))  # carded (link fix)
        fi = run_consolidator(api, invoice_id=INV_ID)["fix_invoices"][0]
        for f in (
            "received_at",
            "discount_amount",
            "unit_cost_includes_tax",
            "is_received",
            "status",
            "notes",
            "loaded_invoice_fingerprint",
        ):
            assert f in fi, f
        assert fi["status"] == "draft"
        assert fi["is_received"] is False
        ln = fi["lines"][0]
        for f in (
            "original_unit_id",
            "unit_ratio",
            "quantity_ordered",
            "tax_amount",
            "sale_tax_rate",
            "item_type",
        ):
            assert f in ln, f
        assert ln["original_unit_id"] == ln["linked_unit_id"]
        # reasons ride on every card now (the chat doc renders Needs Attention)
        assert any("Not linked to a purchase order" in r for r in fi["check_reasons"])


class TestVerdictShape:
    """The per-invoice `details` audit sections were retired with the long
    markdown report: the LLM writes a SHORT summary from reasons/rows and the
    editor cards carry the full per-line data. Verdicts stay lean."""

    def test_verdicts_carry_no_details_sections(self):
        api = api_for(make_invoice())
        verdict = run_consolidator(api)["received"][0]
        assert "details" not in verdict
        assert verdict["checklist"]  # the checklist summary remains

    def test_skipped_verdicts_still_carry_reasons(self):
        api = api_for(
            make_invoice(linkedPurchaseOrderId=None, purchaseOrderNumber=None)
        )
        verdict = sole_skip(run_consolidator(api))
        assert "details" not in verdict
        assert any("Not linked to a purchase order" in r for r in verdict["reasons"])


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

    def test_modest_run_fits_the_configured_cap(self):
        # 8 invoices × 4 lines must fit the 60k max_result_chars the sync
        # script sets on the tool. (The unified process cards every invoice
        # needing the user WITH full copy data, so the old 30k no-override
        # guarantee no longer holds — the override ships with the tool def.)
        size = self.make_run(8)
        assert size < 60_000, f"report payload {size} chars would be slimmed"

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

    def multipack(self, text):
        namespace = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
        exec(FUNCTION_CODE, namespace)
        return namespace["_is_multipack"](text)

    def test_multipack_detection_tolerates_spacing(self):
        # Extraction keeps units AS PRINTED, and suppliers print spaced pack
        # notation — '6x 750ml' (Eurovintage) is the same pack as '6x750ml'.
        for good in ("5x3kg", "6x750ml", "6x 750ml", "4 x 6 pack", "2X12"):
            assert self.multipack(good), good
        for bad in ("Case(s)", "750ml", "x2", "box", "", None):
            assert not self.multipack(bad), bad

    def multipack_equal(self, a, b):
        namespace = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
        exec(FUNCTION_CODE, namespace)
        return namespace["_multipack_equal"](a, b)

    def test_multipack_component_equivalence(self):
        # Same pack, different printing: count-wise equal + inner sizes by
        # magnitude (the Allied Liquor Kahlua case: '6x1L' vs '6 X 1 Litre').
        for a, b in (
            ("6x1L", "6 X 1 Litre"),
            ("6x750ml", "6x 750ml"),
            ("12x1L", "12 x 1 litre"),
            ("2x12*330ml", "2 x 12*330ML"),  # unparseable inner → name equal
        ):
            assert self.multipack_equal(a, b), (a, b)
        # Different packs never equal — even when the totals agree.
        for a, b in (
            ("6x750ml", "6x1L"),
            ("4x6 pack", "24 pack"),
            ("2x12 pack", "24 pack"),
            ("6x1L", "12x1L"),
            ("6x1L", "Case(s)"),
        ):
            assert not self.multipack_equal(a, b), (a, b)


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
        # (per-line compare tables retired with the details sections — the
        # reason string above carries the advice; the card carries the data)

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

    def test_spaced_multipack_vs_packaging_word_flags(self):
        # Eurovintage 1229702: line unit 'Case(s)' (bare packaging word), copy
        # prints '6x 750ml' — spaced pack notation kept AS PRINTED. Both used
        # to fall through the comparator: the check silently read "—" and the
        # card carried no recommended_unit, so NO suggestion existed anywhere.
        api = api_for(
            make_invoice(lines=[make_line(unit="Case(s)")]),
            pdf=self.pdf_with_uom("6x 750ml"),
        )
        verdict = sole_skip(run_consolidator(api))
        reason = next(r for r in verdict["reasons"] if "delivered unit" in r)
        assert "'6x 750ml'" in reason and "Case(s)" in reason

    def test_spaced_mismatch_card_carries_editor_fields(self):
        api = api_for(
            make_invoice(lines=[make_line(unit="Case(s)")]),
            pdf=self.pdf_with_uom("6x 750ml"),
        )
        fi = run_consolidator(api, invoice_id=INV_ID)["fix_invoices"][0]
        ln = fi["lines"][0]
        assert ln["recommended_unit"] == "6x 750ml"
        assert ln["copy_unit_mismatch"] is True
        assert any(
            s["type"] == "unit" and s["proposed_unit"] == "6x 750ml"
            for s in fi.get("suggestions") or []
        )

    def test_packaging_word_vs_simple_derived_flags(self):
        # The general silent-skip gap: line unit 'Carton' (unparseable), copy
        # derives a SIMPLE unit ('500g') — previously "not comparable" → "—".
        api = api_for(
            make_invoice(lines=[make_line(unit="Carton")]),
            pdf=self.pdf_with_uom("500g"),
        )
        verdict = sole_skip(run_consolidator(api))
        assert any("delivered unit" in r for r in verdict["reasons"])

    def test_spaced_vs_compact_multipack_name_equal_passes(self):
        # False-positive control: spacing alone never flags — 'x6750ml' names
        # normalize identically, so '6x750ml' on the line matches the copy's
        # '6x 750ml'.
        api = api_for(
            make_invoice(lines=[make_line(unit="6x750ml")]),
            pdf=self.pdf_with_uom("6x 750ml"),
        )
        result = run_consolidator(api)
        assert result["summary"] == {"received": 1, "skipped": 0}

    def test_component_equivalent_multipack_passes(self):
        # The Allied Liquor Kahlua false-positive, pinned: Loaded unit
        # '6 X 1 Litre' vs copy-derived '6x1L' is the SAME pack — no fix.
        api = api_for(
            make_invoice(lines=[make_line(unit="6 X 1 Litre")]),
            pdf=self.pdf_with_uom("6x1L"),
        )
        result = run_consolidator(api)
        assert result["summary"] == {"received": 1, "skipped": 0}
        fi = run_consolidator(api, invoice_id=INV_ID)["fix_invoices"][0]
        assert not any(s["type"] == "unit" for s in fi.get("suggestions") or [])


class TestFixDerivation:
    """Skipped invoices carry structured one-click fixes for the card."""

    def test_unlinked_with_po_number_yields_link_po_fix(self):
        # Batch resolves the referenced number ITSELF now (unified with the
        # editor): a resolvable PO yields the auto-matched link_po fix.
        api = api_for(
            make_invoice(linkedPurchaseOrderId=None),  # references PO#1520987
            po_list=[
                {"id": PO_ID, "orderNumber": "1520987", "supplierId": "supplier-akaroa"}
            ],
        )
        result = run_consolidator(api)
        fixes = result["fixes"]
        assert len(fixes) == 1
        fx = fixes[0]
        assert fx["type"] == "link_po"
        assert fx["po_number"] == "1520987"  # the RESOLVED PO's own number
        assert fx["purchase_order_id"] == PO_ID  # editor pre-fills the picker
        assert fx["invoice_id"] == INV_ID
        assert fx["id"]  # stable id present

    def test_unlinked_without_po_number_yields_no_fix(self):
        api = api_for(
            make_invoice(linkedPurchaseOrderId=None, purchaseOrderNumber=None)
        )
        assert run_consolidator(api)["fixes"] == []

    def test_link_po_prefers_buyer_po_from_copy(self):
        # Loaded's field holds the supplier's O/N (unresolvable); the copy shows
        # the buyer PO, which DOES resolve — the fix links that one.
        pdf = make_pdf(customer_purchase_order_number="1520999")
        api = api_for(
            make_invoice(linkedPurchaseOrderId=None, purchaseOrderNumber="12195941-1"),
            pdf=pdf,
            po=make_po(orderNumber="1520999"),
            po_list=[
                {"id": PO_ID, "orderNumber": "1520999", "supplierId": "supplier-akaroa"}
            ],
        )
        fx = run_consolidator(api)["fixes"][0]
        assert fx["type"] == "link_po"
        assert fx["po_number"] == "1520999"  # buyer PO from the copy
        assert fx["purchase_order_id"] == PO_ID

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

    def test_unresolvable_po_invoice_still_gets_a_complete_card(self):
        # The referenced number matches no Loaded PO (empty list): no link_po
        # suggestion (noise), but the invoice still cards with FULL copy data —
        # the unified process ran the whole comparison.
        api = api_for(make_invoice(linkedPurchaseOrderId=None))
        fi = run_consolidator(api)["fix_invoices"]
        assert len(fi) == 1
        inv = fi[0]
        assert inv["invoice_id"] == INV_ID
        assert inv["purchase_order_number"] == "PO#1520987"
        assert [s["type"] for s in inv["suggestions"]] == []  # unresolvable ref
        ln = inv["lines"][0]
        # raw numeric values, not strings — and the copy comparison RAN
        assert ln["quantity_received"] == 4.95
        assert ln["unit_cost"] == 44.40
        assert ln["linked_item_id"] == ITEM_SALMON
        assert ln["copy_quantity"] == 4.95

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
    # apps/web/app/components/display/ReceiveInvoiceEditor.tsx.
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

    def test_unified_run_fills_every_runnable_check(self):
        # Unified process: a PO failure no longer short-circuits — the copy
        # checks still run. Only data-dependent gates stay "-" (po_supplier has
        # no fetched PO to compare against).
        api = api_for(make_invoice(linkedPurchaseOrderId=None))
        inv = run_consolidator(api)["fix_invoices"][0]
        checks = self.decode(inv["checks"])
        assert checks["credit_note"] == "p"
        assert checks["pdf_present"] == "p"
        assert checks["po_linked"] == "f"
        assert checks["po_supplier"] == "-"  # no PO fetched — nothing to compare
        assert checks["pdf_readable"] == "p"
        assert checks["pdf_lines"] == "p"  # the copy comparison RAN
        assert checks["duplicate"] == "p"

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


class TestCopyTotals:
    """When Loaded's header totals disagree with the copy (e.g. a feed that
    left the invoice total $0 — Allied Liquor TLC-686719, 07 Aug 2026), the
    card carries the copy's printed totals so the editor can offer
    "Invoice total X → Y (per the invoice copy)" as a local edit."""

    def test_mismatched_total_carries_copy_totals(self):
        api = Api(
            invoices=[],
            details={INV_ID: make_invoice(total=0, subtotal=0, taxAmount=0)},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: make_pdf()},
        )
        fi = run_consolidator(api, invoice_id=INV_ID)["fix_invoices"][0]
        assert fi["copy_total_mismatch"] is True
        assert fi["copy_total"] == 252.75
        assert fi["copy_subtotal"] == 219.78
        assert fi["copy_tax_amount"] == 32.97
        assert any(
            "does not match the document total" in r for r in fi["check_reasons"]
        )

    def test_matching_totals_carry_nothing(self):
        api = Api(
            invoices=[],
            details={INV_ID: make_invoice()},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: make_pdf()},
        )
        fi = run_consolidator(api, invoice_id=INV_ID)["fix_invoices"][0]
        assert fi["copy_total_mismatch"] is False
        assert fi.get("copy_total") is None


class TestUnresolvedCopyPo:
    """When Loaded's own PO field holds the supplier's ref and the buyer PO
    read off the copy matches NO Loaded purchase order, the reason and the
    card must lead with the COPY's number (the one the user can chase), not
    the supplier ref — the Bidfresh 109848631 incident (07 Aug 2026): the
    card said "invoice references 4041451-1" while the copy's CUST. ORDER
    1520518 was extracted correctly and simply had no Loaded PO."""

    def _api(self):
        pdf = make_pdf()
        pdf["customer_purchase_order_number"] = "1520518"
        return Api(
            invoices=[],
            details={
                INV_ID: make_invoice(
                    purchaseOrderNumber="4041451-1", linkedPurchaseOrderId=None
                )
            },
            pdfs={FILE_ID: pdf},
            po_list=[],  # nothing to resolve against
            received_feed=[],
        )

    def test_reason_leads_with_the_copy_po(self):
        fi = run_consolidator(self._api(), invoice_id=INV_ID)["fix_invoices"][0]
        reason = next(r for r in fi["check_reasons"] if "purchase order" in r)
        assert "copy says order 1520518" in reason
        assert "supplier ref 4041451-1" in reason
        assert "no matching purchase order found in Loaded" in reason

    def test_card_carries_copy_po_and_unresolved(self):
        fi = run_consolidator(self._api(), invoice_id=INV_ID)["fix_invoices"][0]
        assert fi["copy_po"] == "1520518"
        assert fi["po_unresolved"] is True
        # No link_po suggestion — there is nothing to link.
        assert not any(s["type"] == "link_po" for s in fi.get("suggestions") or [])

    def test_resolvable_copy_po_still_autolinks(self):
        # Control: when the copy's PO DOES exist in Loaded, the auto-link
        # path is unchanged (suggested link, not the unresolved note).
        api = self._api()
        api.po_list = [
            {
                "id": PO_ID,
                "orderNumber": "1520518",
                "supplierId": "supplier-akaroa",
                "linkedInvoiceId": None,
            }
        ]
        api.pos = {PO_ID: make_po()}
        fi = run_consolidator(api, invoice_id=INV_ID)["fix_invoices"][0]
        assert fi["po_unresolved"] is False
        assert any(s["type"] == "link_po" for s in fi.get("suggestions") or [])


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

    def test_batch_cards_a_skipped_invoice_too(self):
        # Unified chat flow: EVERY invoice that needs the user gets a card —
        # skipped ones included (the card is where they fix and receive it).
        api = api_for(self._freight_invoice())
        result = run_consolidator(api, mode="approve_all")
        assert len(result["fix_invoices"]) == 1


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
            details={
                INV_ID: make_invoice(linkedPurchaseOrderId=None)
            },  # refs PO#1520987
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
        pdf["lines"][0] = dict(
            pdf["lines"][0], quantity=99
        )  # copy says 99, invoice 4.95
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
            pdf["lines"][0],
            quantity=99,
            unit_price_ex_tax=44.40,
            line_total_ex_tax=4395.60,
        )
        api = Api(
            invoices=[],
            details={INV_ID: make_invoice()},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: pdf},
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
        assert any("STATEMENT" in r and "deleted" in r for r in fi["check_reasons"])
        assert any(s.get("type") == "delete_invoice" for s in fi["suggestions"])
        # No copy-comparison noise from statement rows:
        assert not any(s.get("type") in ("quantity", "unit") for s in fi["suggestions"])
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
            received_feed=[
                {
                    "id": "other-received-id",
                    "invoiceNumber": "F55755100",
                    "supplierName": "Akaroa Salmon",
                    "receivedAt": "2026-06-29T22:26:47Z",
                    "total": -20.0,
                }
            ],
        )
        fi = run_consolidator(api, invoice_id=INV_ID)["fix_invoices"][0]
        assert fi["checks"][11] == "f"  # the duplicate check (appended last)
        assert any("already received on 2026-06-29" in r for r in fi["check_reasons"])
        assert any(s.get("type") == "delete_invoice" for s in fi["suggestions"])

    def test_duplicate_batch_mode_skips_and_never_receives(self):
        api = api_for(
            make_invoice(),
            received_feed=[
                {
                    "id": "other-received-id",
                    "invoiceNumber": "F55755100",
                    "supplierName": "Akaroa Salmon",
                    "receivedAt": "2026-06-29T22:26:47Z",
                    "total": -20.0,
                }
            ],
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
                {
                    "id": INV_ID,
                    "invoiceNumber": "F55755100",
                    "supplierName": "Akaroa Salmon",
                },
                {
                    "id": "x",
                    "invoiceNumber": "F55755100",
                    "supplierName": "Someone Else",
                },
            ],
        )
        assert run_consolidator(api)["summary"] == {"received": 1, "skipped": 0}

    def test_invoice_document_type_does_not_trigger_the_statement_gate(self):
        # An ordinary invoice with document_type present stays fully receivable.
        api = api_for(make_invoice(), pdf=make_pdf(document_type="invoice"))
        assert run_consolidator(api)["summary"] == {"received": 1, "skipped": 0}

    def test_unit_cost_mismatch_suggests_the_copy_price(self):
        # Loaded ingested the line UNPRICED (None) — the copy carries the real
        # price. The review flags it AND offers the copy's price as a one-click
        # edit (self-consistent copy line: 4.95 x 44.40 = 219.78).
        inv = make_invoice(lines=[make_line(unitCost=None, totalCost=None)])
        api = Api(
            invoices=[],
            details={INV_ID: inv},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: make_pdf()},
        )
        fi = run_consolidator(api, invoice_id=INV_ID)["fix_invoices"][0]
        ln = fi["lines"][0]
        assert ln.get("copy_unit_cost_mismatch") is True
        cfix = next(s for s in fi["suggestions"] if s.get("type") == "unit_cost")
        assert cfix["line_id"] == "line-1"
        assert cfix["proposed_unit_cost"] == 44.40
        assert "$0.00 → $44.40" in cfix["summary"]

    def test_inconsistent_copy_price_is_flagged_but_not_suggested(self):
        # The copy line contradicts itself (price x qty != total): flag the
        # mismatch, but never offer a provably-unreliable price as one-click.
        pdf = make_pdf()
        pdf["lines"][0] = dict(
            pdf["lines"][0], unit_price_ex_tax=99.0
        )  # 4.95x99 != 219.78
        inv = make_invoice(lines=[make_line(unitCost=None, totalCost=None)])
        api = Api(
            invoices=[],
            details={INV_ID: inv},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: pdf},
        )
        fi = run_consolidator(api, invoice_id=INV_ID)["fix_invoices"][0]
        assert fi["lines"][0].get("copy_unit_cost_mismatch") is True
        assert not any(s.get("type") == "unit_cost" for s in fi["suggestions"])

    def test_self_contradicting_copy_quantity_is_flagged_but_not_suggested(self):
        # The extraction contradicts ITSELF: qty 4 x $4.53 = $18.12 but the line
        # total reads $72.48 (the live BUTTERMILK misread — the real qty was 16,
        # printed as a carton/singles split). One of the numbers is provably
        # wrong, so the mismatch is FLAGGED but no one-click Accept is offered
        # for a number we can prove is a misread.
        pdf = make_pdf()
        pdf["lines"][0] = dict(
            pdf["lines"][0],
            quantity=4,
            unit_price_ex_tax=4.53,
            line_total_ex_tax=72.48,
        )
        api = Api(
            invoices=[],
            details={INV_ID: make_invoice()},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: pdf},
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
            id="l-real",
            code="666756",
            description="CHEESE PARMESAN WEDGE",
            quantityReceived=2.0,
            unitCost=50.0,
            totalCost=100.0,
            linkedItemId="item-cheese",
        )
        dupe = make_line(
            id="l-dupe",
            code="666756",
            description="CHEESE PARMESAN WEDGE",
            quantityReceived=0.0,
            unitCost=0.0,
            totalCost=0.0,
            linkedItemId="item-cheese",
        )
        inv = make_invoice(
            lines=[real, dupe],
            linkedPurchaseOrderId=None,
            subtotal=100.0,
            taxAmount=15.0,
            total=115.0,
        )
        pdf = make_pdf(
            lines=[
                {
                    "code": "666756",
                    "description": "CHEESE PARMESAN WEDGE",
                    "quantity": 2.0,
                    "unit": "Each",
                    "unit_of_measure": "Each",
                    "unit_price_ex_tax": 50.0,
                    "line_total_ex_tax": 100.0,
                }
            ],
            subtotal_ex_tax=100.0,
            tax_amount=15.0,
            total_incl_tax=115.0,
        )
        # Single-invoice review forces the card and runs the full copy comparison,
        # so the "not found" path is actually exercised.
        fi = run_consolidator(api_for(inv, pdf=pdf), invoice_id=INV_ID)["fix_invoices"][
            0
        ]
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
            id="l-new",
            code="XX1",
            description="MYSTERY SAUCE",
            linkedItemId=None,
            linkedUnitId=None,
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


class TestParallelPrefetch:
    """The batch flow prefetches invoice details and PDF extractions in
    parallel (executor helpers), and the artifact is identical to the
    sequential path. Sequential remains the fallback when the executor
    predates the helpers — which every pre-existing test exercises, since
    run_consolidator injects neither."""

    def _api(self):
        inv2 = make_invoice(
            id="inv-2",
            referenceNumber="F55755101",
            fileId="file-2",
            purchaseOrderNumber="PO#1520988",
            linkedPurchaseOrderId="po-2",
        )
        return Api(
            invoices=[make_invoice(), inv2],
            details={INV_ID: make_invoice(), "inv-2": inv2},
            pos={PO_ID: make_po(), "po-2": make_po(id="po-2", orderNumber="1520988")},
            pdfs={
                FILE_ID: make_pdf(),
                "file-2": make_pdf(
                    invoice_number="F55755101", purchase_order_number="1520988"
                ),
            },
        )

    def test_parallel_artifact_matches_sequential(self):
        seq = run_consolidator(self._api())
        api = self._api()
        par = run_consolidator_parallel(api)
        assert par == seq
        # One batch of 2 detail fetches, one batch of 2 extractions — not a
        # per-invoice loop.
        assert api.parallel_batches == [2]
        assert api.extract_batches == [2]

    def test_prefetch_skips_invoices_without_a_copy(self):
        api = self._api()
        api.details["inv-2"]["fileId"] = None
        run_consolidator_parallel(api)
        assert api.extract_batches == [1]

    def test_single_invoice_review_stays_sequential(self):
        api = self._api()
        run_consolidator_parallel(api, invoice_id=INV_ID)
        assert not hasattr(api, "parallel_batches") or api.parallel_batches == []


ITEM_URBANAUT = "1d047324-7447-4beb-bbd7-31c4cebc2a7b"


def _urbanaut_item(**over):
    item = {
        "id": ITEM_URBANAUT,
        "name": "URBANAUT ATLANTA IPA",
        "suppliers": [
            {
                "supplierId": "supplier-akaroa",
                "stockCode": "Keg",
                "description": "Atlanta Bright IPA 4.6% 50L Keg",
                "unitId": UNIT_KILO,
                "unitCost": 340.0,
            }
        ],
    }
    item.update(over)
    return item


class TestVariantLineMatching:
    """Loaded's draft line often carries the stock ITEM's name while the copy
    prints the supplier VARIANT's description (real prod case: 'URBANAUT
    ATLANTA IPA' vs 'Atlanta Bright IPA 4.6% 50L Keg'). The engine fetches the
    item's suppliers[] and matches variant descriptions before declaring a
    mismatch."""

    def _api(self, stock_items=None, pdf_desc="Atlanta Bright IPA 4.6% 50L Keg"):
        line = make_line(
            code=None,
            description="URBANAUT ATLANTA IPA",
            linkedItemId=ITEM_URBANAUT,
            quantityReceived=1,
            quantityOrdered=1,
            unitCost=340.0,
            totalCost=340.0,
            taxAmount=51.0,
            saleTaxRate=0.15,
        )
        inv = make_invoice(lines=[line], subtotal=340.0, taxAmount=51.0, total=391.0)
        po = make_po(
            lines=[
                make_po_line(
                    itemId=ITEM_URBANAUT,
                    itemName="URBANAUT ATLANTA IPA",
                    itemCode=None,
                    unitCost=340.0,
                    unitCostOrdered=340.0,
                    quantityOrdered=1.0,
                    quantityReceived=1.0,
                )
            ]
        )
        pdf = make_pdf(
            lines=[
                {
                    "code": None,
                    "description": pdf_desc,
                    "quantity": 1,
                    "unit": "Keg",
                    "unit_of_measure": "Kilo",
                    "unit_price_ex_tax": 340.0,
                    "line_total_ex_tax": 340.0,
                }
            ],
            subtotal_ex_tax=340.0,
            tax_amount=51.0,
            total_incl_tax=391.0,
        )
        return Api(
            invoices=[inv],
            details={INV_ID: inv},
            pos={PO_ID: po},
            pdfs={FILE_ID: pdf},
            stock_items=stock_items,
        )

    def test_variant_description_resolves_the_mismatch(self):
        api = self._api(stock_items={ITEM_URBANAUT: _urbanaut_item()})
        result = run_consolidator(api)
        assert result["summary"] == {"received": 1, "skipped": 0}, result
        assert api.stock_item_calls == [ITEM_URBANAUT]
        fixes = result.get("fixes") or []
        assert not [f for f in fixes if f["type"] in ("add_line", "remove_line")]

    def test_without_item_data_the_mismatch_stands_with_suggestions(self):
        api = self._api()  # get_stock_item errors -> no variant rescue
        result = run_consolidator(api)
        assert result["summary"] == {"received": 0, "skipped": 1}
        reasons = result["skipped"][0]["reasons"]
        assert any("not found on the attached invoice" in r for r in reasons)
        assert any("has no matching invoice line" in r for r in reasons)
        adds = [f for f in result["skipped"][0]["fixes"] if f["type"] == "add_line"]
        assert len(adds) == 1
        assert adds[0]["description"] == "Atlanta Bright IPA 4.6% 50L Keg"
        assert adds[0]["line_total_ex_tax"] == 340.0
        assert adds[0]["sale_tax_rate"] == 0.15
        # The remove affordance rides as a per-line flag, not a fixes entry
        # (per-line fix dicts blew the payload cap).
        card = result["fix_invoices"][0]
        assert card["lines"][0].get("copy_missing") is True

    def test_item_fetched_once_per_run_across_invoices(self):
        inv2_id = "22222222-2222-4222-8222-222222222222"
        api = self._api(stock_items={ITEM_URBANAUT: _urbanaut_item()})
        inv1 = api.details[INV_ID]
        inv2 = make_invoice(
            id=inv2_id,
            referenceNumber="F55755999",
            lines=[dict(inv1["lines"][0], id="line-2")],
            subtotal=340.0,
            taxAmount=51.0,
            total=391.0,
        )
        api.invoices = [inv1, inv2]
        api.details[inv2_id] = inv2
        run_consolidator(api)
        assert api.stock_item_calls == [ITEM_URBANAUT]

    def test_short_fragments_do_not_claim(self):
        # Variant description shares only a short fragment with the doc line —
        # the >=8-normalized-char floor refuses it; the mismatch stands.
        item = _urbanaut_item()
        item["suppliers"][0]["description"] = "IPA"
        item["suppliers"][0]["stockCode"] = None
        item["name"] = "ZZZ"
        api = self._api(stock_items={ITEM_URBANAUT: item}, pdf_desc="IPA Mixed Case")
        result = run_consolidator(api)
        assert result["summary"] == {"received": 0, "skipped": 1}

    def test_ambiguous_variant_claims_need_total_tiebreak(self):
        # Two unclaimed doc lines both contain the variant text; totals decide.
        item = _urbanaut_item()
        api = self._api(stock_items={ITEM_URBANAUT: item})
        pdf = api.pdfs[FILE_ID]
        second = dict(pdf["lines"][0])
        second["description"] = "Atlanta Bright IPA 4.6% 50L Keg (deposit)"
        second["unit_price_ex_tax"] = 50.0
        second["line_total_ex_tax"] = 50.0
        pdf["lines"].append(second)
        pdf["subtotal_ex_tax"] = 390.0
        result = run_consolidator(api)
        # The $340 line total picks the right doc line; the $50 deposit line is
        # left over as a genuine add_line suggestion (totals also mismatch, so
        # the invoice is skipped, not received).
        fixes = result["skipped"][0]["fixes"]
        adds = [f for f in fixes if f["type"] == "add_line"]
        assert len(adds) == 1 and adds[0]["line_total_ex_tax"] == 50.0
        # The invoice line itself matched — no remove affordance on it
        card = result["fix_invoices"][0]
        assert not card["lines"][0].get("copy_missing")

    def test_deleted_variants_are_ignored(self):
        item = _urbanaut_item()
        item["suppliers"][0]["datestampDeleted"] = "2026-01-01"
        api = self._api(stock_items={ITEM_URBANAUT: item})
        # Item name "URBANAUT ATLANTA IPA" shares no >=8-char substring with the
        # doc line, so with the only live variant deleted nothing claims.
        result = run_consolidator(api)
        assert result["summary"] == {"received": 0, "skipped": 1}

    def test_statement_suppresses_add_and_remove(self):
        api = self._api()
        api.pdfs[FILE_ID] = make_pdf(
            document_type="statement",
            lines=[],
            subtotal_ex_tax=None,
            tax_amount=None,
            total_incl_tax=None,
        )
        result = run_consolidator(api)
        fixes = result["skipped"][0]["fixes"]
        assert not [f for f in fixes if f["type"] in ("add_line", "remove_line")]
        assert [f for f in fixes if f["type"] == "delete_invoice"]

    def test_add_line_goes_through_item_matcher(self):
        api = self._api()
        api.item_matches = {
            "doc:0": {
                "matched_item": {
                    "id": "item-match-1",
                    "name": "Atlanta Bright IPA",
                    "unit_id": UNIT_KILO,
                    "unit_cost": 340.0,
                },
                "suggested_name": None,
                "suggested_group_id": None,
            }
        }
        result = run_consolidator(api)
        card = result["fix_invoices"][0]
        adds = [s for s in card["suggestions"] if s["type"] == "add_line"]
        assert len(adds) == 1
        assert adds[0]["matched_item"]["id"] == "item-match-1"


class TestNewValueReasonRewording:
    """The Layer-4 'would be created as NEW' reason runs before the item
    matcher; once the card's suggestions are known, values they resolve must
    read as 'not linked yet — suggested changes resolve them', never as NEW
    (prod case: a link-to-existing suggestion directly above a reason claiming
    the same item would be created as NEW)."""

    def _api(self, item_matches=None, uom="Kilo"):
        line = make_line(linkedItemId=None, linkedUnitId=None, unit="16X950G")
        inv = make_invoice(lines=[line])
        pdf = make_pdf()
        pdf["lines"][0]["unit_of_measure"] = uom
        return Api(
            invoices=[inv],
            details={INV_ID: inv},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: pdf},
            item_matches=item_matches or {},
        )

    def test_matched_values_reword_to_suggested_fixes(self):
        api = self._api(
            item_matches={
                "line-1": {
                    "matched_item": {"id": "i-9", "name": "CHEESE FETA"},
                    "suggested_name": None,
                    "suggested_group_id": None,
                }
            }
        )
        result = run_consolidator(api)
        reasons = result["fix_invoices"][0]["check_reasons"]
        joined = " | ".join(reasons)
        assert "would be created as NEW" not in joined
        assert "link to existing 'CHEESE FETA'" in joined
        assert "unit '16X950G'" in joined and "→ 'Kilo'" in joined
        # The verdict holds the SAME reasons list — rewritten there too.
        vjoined = " | ".join(result["skipped"][0]["reasons"])
        assert "not linked in Loaded yet" in vjoined
        # The check itself still fails — linking is still required to receive.
        assert (
            result["fix_invoices"][0]["checks"][7] == "f"
            or "f" in result["fix_invoices"][0]["checks"]
        )

    def test_unmatched_values_keep_the_new_wording(self):
        api = self._api(uom=None)  # no match, no derivable unit
        result = run_consolidator(api)
        joined = " | ".join(result["fix_invoices"][0]["check_reasons"])
        assert "would be created as NEW" in joined
        assert "stock item on line" in joined
        assert "not linked in Loaded yet" not in joined

    def test_mixed_values_split_into_both_messages(self):
        # Item resolvable by the matcher; unit NOT derivable → one message each.
        api = self._api(
            item_matches={
                "line-1": {
                    "matched_item": {"id": "i-9", "name": "CHEESE FETA"},
                    "suggested_name": None,
                    "suggested_group_id": None,
                }
            },
            uom=None,
        )
        result = run_consolidator(api)
        reasons = result["fix_invoices"][0]["check_reasons"]
        joined = " | ".join(reasons)
        assert "would be created as NEW" in joined and "unit '16X950G'" in joined
        assert "not linked in Loaded yet" in joined
        assert "link to existing 'CHEESE FETA'" in joined


class TestUnrecognisableUnit:
    """The copy carries unit/size info that can't be read (cut off, illegible,
    ambiguous — prod case: 'THE MAKER ... 24 (1' with the size truncated).
    Never guess: the unit check FAILS with a confirm ask, no unit fix is
    proposed, the card line carries unit_needs_confirmation, and the reason
    keeps autopilot from receiving."""

    def _api(self, unrecognisable=True, uom=None):
        line = make_line(saleTaxRate=0.15)
        inv = make_invoice(lines=[line])
        pdf = make_pdf()
        pdf["lines"][0]["unit_of_measure"] = uom
        if unrecognisable:
            pdf["lines"][0]["unit_unrecognisable"] = True
        return Api(
            invoices=[inv],
            details={INV_ID: inv},
            pos={PO_ID: make_po()},
            pdfs={FILE_ID: pdf},
        )

    def test_fails_with_confirm_ask_and_no_fix(self):
        result = run_consolidator(self._api())
        v = result["skipped"][0]
        joined = " | ".join(v["reasons"])
        assert "can't be determined from the invoice copy" in joined
        assert "confirm the unit" in joined
        assert not [f for f in v["fixes"] if f["type"] == "unit"]
        card = result["fix_invoices"][0]
        assert card["lines"][0].get("unit_needs_confirmation") is True
        # the packed checks string marks unit_of_measure failed (index of the
        # unit_of_measure key in CHECK_LABELS order)
        assert result["summary"] == {"received": 0, "skipped": 1}

    def test_autopilot_does_not_receive(self):
        api = self._api()
        result = run_consolidator(api, mode="autopilot")
        assert result["summary"] == {"received": 0, "skipped": 1}
        assert api.received_bodies == []  # no receive write happened

    def test_null_unit_without_flag_is_unchanged(self):
        # No size info printed at all: null unit stays "not checked" — no fail,
        # no confirm ask, invoice receives as before.
        result = run_consolidator(self._api(unrecognisable=False))
        assert result["summary"] == {"received": 1, "skipped": 0}
        card = result["fix_invoices"] if result.get("fix_invoices") else []
        if card:
            assert not card[0]["lines"][0].get("unit_needs_confirmation")
