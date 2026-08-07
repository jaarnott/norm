"""Unit tests for the received-invoice service: the draft shaper.

The shaper is where the three gaps the old batch card had are fixed by
construction, so these are the tests that pin them:
- the real ``total`` (not ``subtotal``) is carried, with the tax breakdown;
- real per-line ``quantity_received``/``unit_cost`` populate (not blank);
- a linked PO reads as linked.
"""

from app.services.received_invoice import build_received_invoice_data


# The real Loaded get_invoice_detail shape (observed live: F56584601, La Zeppa).
DETAIL = {
    "id": "3eeaf03d",
    "referenceNumber": "F56584601",
    "supplierName": "DAILY BREAD",
    "linkedSupplierId": "sup-db",
    "purchaseOrderNumber": "1520441",
    "linkedPurchaseOrderId": "po-2ba0",
    "issuedAt": "2026-07-29",
    "dueAt": "2026-08-05",
    "receivedAt": None,
    "subtotal": 100.2,
    "taxAmount": 15.03,
    "discountAmount": None,
    "total": 115.23,
    "displayUnitCostInclusiveOfTax": False,
    "fileId": "file-82f",
    "isReceived": False,
    "notes": None,
    "lines": [
        {
            "id": "ln-foc",
            "code": "SB_FocaT_Sli_Sgl",
            "description": "FOCACCIA",
            "brand": "DAILY BREAD",
            "unit": "Each",
            "itemType": "Default",
            "quantityOrdered": 12,
            "quantityReceived": 15,
            "unitCostExclTax": 5.08,
            "totalCostExclTax": 76.2,
            "taxAmount": 11.43,
            "saleTaxRate": 0.15,
            "linkedUnitId": "u-each",
            "linkedUnitRatio": 1,
            "linkedItemId": "item-foc",
        },
    ],
}


class TestBuildReceivedInvoiceData:
    def test_carries_real_total_and_tax_breakdown(self):
        d = build_received_invoice_data(DETAIL)
        # The old card showed subtotal ($100.20) as "Invoice total"; the draft
        # carries the real total plus the pieces to show the breakdown.
        assert d["total"] == 115.23
        assert d["subtotal"] == 100.2
        assert d["tax_amount"] == 15.03

    def test_populates_real_line_qty_and_cost(self):
        d = build_received_invoice_data(DETAIL)
        ln = d["lines"][0]
        assert ln["quantity_received"] == 15
        assert ln["unit_cost"] == 5.08
        assert ln["total_cost"] == 76.2

    def test_carries_loaded_parity_fields(self):
        # The fields the editor's Loaded-parity columns/header/totals read.
        d = build_received_invoice_data(DETAIL)
        assert d["discount_amount"] is None
        assert d["received_at"] is None
        assert d["unit_cost_includes_tax"] is False
        ln = d["lines"][0]
        assert ln["brand"] == "DAILY BREAD"
        assert ln["quantity_ordered"] == 12
        assert ln["tax_amount"] == 11.43
        assert ln["sale_tax_rate"] == 0.15

    def test_carries_the_linked_po(self):
        d = build_received_invoice_data(DETAIL)
        assert d["linked_purchase_order_id"] == "po-2ba0"
        assert d["purchase_order_number"] == "1520441"

    def test_records_original_unit_for_variant_diff(self):
        # original_unit_id lets submit derive variant_updates (unit changed)
        # server-side without the client remembering the starting unit.
        ln = build_received_invoice_data(DETAIL)["lines"][0]
        assert ln["original_unit_id"] == "u-each"
        assert ln["linked_unit_id"] == "u-each"

    def test_starts_as_an_unreceived_draft(self):
        d = build_received_invoice_data(DETAIL)
        assert d["status"] == "draft"
        assert d["is_received"] is False
        assert d["invoice_id"] == "3eeaf03d"

    def test_tolerates_missing_lines(self):
        d = build_received_invoice_data({"id": "x", "referenceNumber": "R"})
        assert d["lines"] == []
        assert d["invoice_id"] == "x"


class TestCredentialHeaderSanitization:
    """User-entered credential values must never reach the wire with stray
    whitespace — a leading space on a stored x-loaded-company-id made httpx
    reject the request as an illegal header value (prod 500, which the
    invoices dashboard rendered as 'No outstanding invoices')."""

    def test_bearer_token_is_stripped(self):
        from app.connectors.spec_executor import _apply_auth

        headers, _ = _apply_auth(
            {}, "bearer", {"token_field": "api_key"}, {"api_key": " tok-123\n"}
        )
        assert headers["Authorization"] == "Bearer tok-123"

    def test_api_key_header_is_stripped(self):
        from app.connectors.spec_executor import _apply_auth

        headers, _ = _apply_auth(
            {},
            "api_key_header",
            {"header_name": "X-API-Key", "key_field": "api_key"},
            {"api_key": "  key-9  "},
        )
        assert headers["X-API-Key"] == "key-9"


class TestAttachItemNames:
    """The mirror's Description column: linked lines show the ITEM's name (as
    Loaded's UI does), resolved once and persisted; raw description untouched."""

    class FakeLh:
        def __init__(self, items):
            self.items = items
            self.calls = []

        def get(self, path):
            self.calls.append(path)
            item_id = path.rsplit("/", 1)[-1]
            if item_id in self.items:
                return {"id": item_id, "name": self.items[item_id]}
            raise RuntimeError("item fetch failed")

    def test_resolves_names_for_linked_lines_only(self):
        from app.services.received_invoice import attach_item_names

        data = {"lines": [
            {"id": "l1", "description": "Spianata Piccante 2kg C6", "linked_item_id": "i-1"},
            {"id": "l2", "description": "FREIGHT - FOOD", "linked_item_id": None},
        ]}
        lh = self.FakeLh({"i-1": "SPIANATA PICCANTE"})
        attach_item_names(data, lh)
        assert data["lines"][0]["item_name"] == "SPIANATA PICCANTE"
        assert data["lines"][0]["item_name_for"] == "i-1"
        assert data["lines"][0]["description"] == "Spianata Piccante 2kg C6"  # raw kept
        assert "item_name" not in data["lines"][1]

    def test_resolved_lines_are_not_refetched(self):
        from app.services.received_invoice import attach_item_names

        data = {"lines": [
            {"id": "l1", "linked_item_id": "i-1", "item_name": "X", "item_name_for": "i-1"},
        ]}
        lh = self.FakeLh({"i-1": "X"})
        attach_item_names(data, lh)
        assert lh.calls == []  # persisted — no refetch on re-open

    def test_relink_refreshes_the_name(self):
        from app.services.received_invoice import attach_item_names

        data = {"lines": [
            {"id": "l1", "linked_item_id": "i-2", "item_name": "OLD", "item_name_for": "i-1"},
        ]}
        lh = self.FakeLh({"i-2": "NEW NAME"})
        attach_item_names(data, lh)
        assert data["lines"][0]["item_name"] == "NEW NAME"
        assert data["lines"][0]["item_name_for"] == "i-2"

    def test_failed_fetch_leaves_raw_description(self):
        from app.services.received_invoice import attach_item_names

        data = {"lines": [{"id": "l1", "description": "RAW", "linked_item_id": "i-x"}]}
        attach_item_names(data, self.FakeLh({}))  # fetch raises
        assert "item_name" not in data["lines"][0]
