"""Unit tests for the invoice-fixes appliers (link_po, unit + variant).

Exercises the orchestration logic with a scripted _Loaded fake — no network,
no DB — asserting the exact LoadedHub request sequence each fix produces.
"""

from app.routers import invoice_fixes as IF


class FakeLoaded:
    """Records requests; serves canned GET responses by path prefix."""

    def __init__(self, gets: dict, invoices: dict):
        self._gets = gets
        self._invoices = invoices
        self.writes = []  # (method, path, body)

    def get(self, path):
        for prefix, val in self._gets.items():
            if path.startswith(prefix):
                return val
        raise AssertionError(f"unexpected GET {path}")

    def invoice(self, invoice_id):
        return self._invoices[invoice_id]

    def request(self, method, path, body=None):
        self.writes.append((method, path, body))
        return {}


PO_LIST = [
    {"id": "po-1", "orderNumber": "1520987"},
    {"id": "po-2", "orderNumber": "1520988"},
]
UNITS = [
    {"id": "u-each", "name": "Each", "ratio": 1.0, "stockUnitType": "Count"},
    {"id": "u-kilo", "name": "Kilo", "ratio": 1.0, "stockUnitType": "Weight"},
    {"id": "u-100pc", "name": "100 piece", "ratio": 100.0, "stockUnitType": "Count"},
]


def _invoice():
    return {
        "id": "inv-1",
        "linkedPurchaseOrderId": None,
        "purchaseOrderNumber": None,
        "lines": [
            {"id": "ln-1", "code": "NAP", "unit": "Each", "linkedUnitId": "u-each"}
        ],
    }


class TestLinkPo:
    def test_links_matching_po(self):
        lh = FakeLoaded(
            {"/1.0/stock/internal/purchase-orders": PO_LIST},
            {"inv-1": _invoice()},
        )
        msg = IF._apply_link_po(lh, {"invoice_id": "inv-1", "po_number": "PO#1520987"})
        assert "1520987" in msg
        method, path, body = lh.writes[-1]
        assert method == "PUT" and path.endswith("/invoices/inv-1")
        assert body["linkedPurchaseOrderId"] == "po-1"
        assert body["purchaseOrderNumber"] == "1520987"

    def test_missing_po_raises(self):
        lh = FakeLoaded(
            {"/1.0/stock/internal/purchase-orders": PO_LIST},
            {"inv-1": _invoice()},
        )
        try:
            IF._apply_link_po(lh, {"invoice_id": "inv-1", "po_number": "9999"})
            assert False, "expected failure"
        except RuntimeError as e:
            assert "not found" in str(e)
        assert lh.writes == []  # nothing written on failure


class TestUnit:
    def _lh(self):
        item = {
            "suppliers": [
                {"id": "var-1", "supplierId": "sup-1", "stockCode": "NAP"},
                {"id": "var-2", "supplierId": "sup-1", "stockCode": "OTHER"},
            ]
        }
        return FakeLoaded(
            {
                "/1.0/stock/internal/units": UNITS,
                "/1.0/stock/internal/items/": item,
            },
            {"inv-1": _invoice()},
        )

    def _fix(self, proposed="100 piece"):
        return {
            "invoice_id": "inv-1",
            "line_id": "ln-1",
            "line_code": "NAP",
            "linked_item_id": "item-1",
            "linked_supplier_id": "sup-1",
            "proposed_unit": proposed,
        }

    def test_updates_line_then_variant(self):
        lh = self._lh()
        msg = IF._apply_unit(lh, self._fix("100 piece"))
        assert "100 piece" in msg and "variant" in msg
        put = [w for w in lh.writes if w[0] == "PUT"][0]
        assert put[2]["lines"][0]["linkedUnitId"] == "u-100pc"
        patch = [w for w in lh.writes if w[0] == "PATCH"][0]
        assert patch[1].endswith("/item-supplier-variant/var-1")
        assert patch[2] == {"unitId": "u-100pc"}

    def test_guideline_equivalent_unit_resolves(self):
        # "1 each" is guideline-equivalent to the Loaded "Each" unit.
        lh = self._lh()
        IF._apply_unit(lh, self._fix("1 each"))
        put = [w for w in lh.writes if w[0] == "PUT"][0]
        assert put[2]["lines"][0]["linkedUnitId"] == "u-each"

    def test_unresolvable_unit_writes_nothing(self):
        lh = self._lh()
        try:
            IF._apply_unit(lh, self._fix("carton"))
            assert False
        except RuntimeError as e:
            assert "does not exist in Loaded" in str(e)
        assert lh.writes == []

    def test_no_variant_still_updates_line(self):
        # Variant not found (different supplier) → line updated, no PATCH.
        lh = self._lh()
        msg = IF._apply_unit(lh, {**self._fix("Each"), "linked_supplier_id": "sup-X"})
        assert "variant" not in msg
        assert [w for w in lh.writes if w[0] == "PATCH"] == []
        assert [w for w in lh.writes if w[0] == "PUT"]


class TestReceive:
    """The Accept & Receive orchestration: link PO, apply line edits, receive,
    then PATCH changed variants."""

    def _req(self, **over):
        base = dict(
            venue_id="v1",
            invoice_id="inv-1",
            linked_purchase_order_id=None,
            po_number=None,
            lines=[],
            variant_updates=[],
            receive=True,
        )
        base.update(over)
        return IF.ReceiveRequest(**base)

    def _inv(self):
        return {
            "id": "inv-1",
            "linkedSupplierId": "sup-1",
            "linkedPurchaseOrderId": None,
            "purchaseOrderNumber": None,
            "lines": [
                {
                    "id": "ln-1",
                    "code": "NAP",
                    "unit": "Each",
                    "linkedUnitId": "u-each",
                    "quantityReceived": 1,
                    "unitCost": 3.99,
                },
            ],
        }

    def _lh(self):
        item = {
            "suppliers": [{"id": "var-1", "supplierId": "sup-1", "stockCode": "NAP"}]
        }
        return FakeLoaded(
            {
                "/1.0/stock/internal/purchase-orders": PO_LIST,
                "/1.0/stock/internal/items/": item,
            },
            {"inv-1": self._inv()},
        )

    def test_links_po_edits_line_receives_and_patches_variant(self):
        lh = self._lh()
        req = self._req(
            po_number="1520987",
            lines=[
                {
                    "id": "ln-1",
                    "unit": "100 piece",
                    "linked_unit_id": "u-100pc",
                    "unit_ratio": 100.0,
                    "quantity_received": 1,
                    "unit_cost": 3.99,
                    "total_cost": 3.99,
                }
            ],
            variant_updates=[
                {"linked_item_id": "item-1", "line_code": "NAP", "unit_id": "u-100pc"}
            ],
        )
        out = IF._do_receive(lh, req)
        assert out["ok"] and out["received"]
        assert out["linked_purchase_order"] == "1520987"
        put = [w for w in lh.writes if w[0] == "PUT"][0]
        body = put[2]
        assert body["linkedPurchaseOrderId"] == "po-1"
        assert body["isReceived"] is True and body["receivedAt"]
        assert body["lines"][0]["linkedUnitId"] == "u-100pc"
        assert body["lines"][0]["quantityReceived"] == 1
        patch = [w for w in lh.writes if w[0] == "PATCH"][0]
        assert patch[1].endswith("/item-supplier-variant/var-1")
        assert patch[2] == {"unitId": "u-100pc"}
        assert out["variant_updates"] == [{"code": "NAP", "ok": True}]

    def test_receive_false_leaves_draft(self):
        lh = self._lh()
        out = IF._do_receive(lh, self._req(receive=False))
        assert out["received"] is False
        put = [w for w in lh.writes if w[0] == "PUT"][0]
        assert "isReceived" not in put[2]

    def test_unknown_po_number_raises(self):
        lh = self._lh()
        try:
            IF._do_receive(lh, self._req(po_number="9999"))
            assert False, "expected failure"
        except Exception as e:
            assert "not found" in str(e)
        assert [w for w in lh.writes if w[0] == "PUT"] == []


class TestLinkedPoNumberIsWrittenForDisplay:
    """Linking by id must also correct the invoice's displayed PO number.

    Loaded's invoice LIST renders invoice.purchaseOrderNumber, which the
    supplier feed fills with the SUPPLIER's own order number (e.g. Bidfood
    "12195941-1"). The card links by id and sends no number, so without an
    explicit lookup the invoice ends up linked to the right purchase order
    while the list shows a number matching no Loaded PO — and disagreeing with
    the invoice's own detail screen.
    """

    def _inv(self):
        return {
            "id": "inv-1",
            "linkedSupplierId": "sup-1",
            "linkedPurchaseOrderId": None,
            # what the supplier feed put there: Bidfood's own order number
            "purchaseOrderNumber": "12195941-1",
            "lines": [],
        }

    def _lh(self):
        # More specific path first — FakeLoaded matches by prefix, in order.
        return FakeLoaded(
            {
                "/1.0/stock/internal/purchase-orders/po-1": {
                    "id": "po-1",
                    "orderNumber": "1520387",
                },
                "/1.0/stock/internal/purchase-orders": PO_LIST,
            },
            {"inv-1": self._inv()},
        )

    def _req(self, **over):
        base = dict(
            venue_id="v1",
            invoice_id="inv-1",
            linked_purchase_order_id="po-1",
            po_number=None,
            lines=[],
            variant_updates=[],
            receive=True,
        )
        base.update(over)
        return IF.ReceiveRequest(**base)

    def test_supplier_number_is_replaced_with_the_linked_po(self):
        lh = self._lh()
        out = IF._do_receive(lh, self._req())
        body = [w for w in lh.writes if w[0] == "PUT"][0][2]
        assert body["linkedPurchaseOrderId"] == "po-1"
        assert body["purchaseOrderNumber"] == "1520387"
        assert out["linked_purchase_order"] == "1520387"

    def test_explicit_number_still_wins(self):
        lh = self._lh()
        IF._do_receive(lh, self._req(po_number="1520999"))
        body = [w for w in lh.writes if w[0] == "PUT"][0][2]
        assert body["purchaseOrderNumber"] == "1520999"

    def test_lookup_failure_does_not_block_the_receive(self):
        # A PO that can't be read must still link and receive — only the
        # display number is best-effort.
        lh = FakeLoaded(
            {"/1.0/stock/internal/purchase-orders": PO_LIST},
            {"inv-1": self._inv()},
        )
        out = IF._do_receive(lh, self._req())
        body = [w for w in lh.writes if w[0] == "PUT"][0][2]
        assert out["ok"] and body["linkedPurchaseOrderId"] == "po-1"
        assert body["purchaseOrderNumber"] == "12195941-1"  # untouched
