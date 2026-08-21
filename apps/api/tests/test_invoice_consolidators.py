"""The invoice-surface consolidators: get_invoices, get_purchase_orders, and
the received-items group rollups.

Exec'd under the REAL sandbox namespace. The facts pinned: list modes return
HEADERS ONLY (an agent can no longer pull every invoice with every line over
a period); lines exist per single document or AGGREGATED; group/super_group
rollups answer "how much did we buy" in ~25 rows; PO list rows pass the
summary transform's camelCase fields through untouched (the orders dashboard
parses that exact shape via show_orders replay).
"""

import pathlib

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES

_DIR = pathlib.Path(__file__).resolve().parent.parent / "config" / "consolidators"
INVOICES_CODE = (_DIR / "get_invoices.py").read_text()
POS_CODE = (_DIR / "get_purchase_orders.py").read_text()
ITEMS_CODE = (_DIR / "received_items_for_period.py").read_text()

WINDOW = {
    "start": "2026-08-01T07:00:00+12:00",
    "end": "2026-08-08T06:59:59+12:00",
    "trading_aligned": True,
    "description": "the business week",
}

OUTSTANDING = [
    {
        "id": "inv-1",
        "supplierName": "Bidfood",
        "referenceNumber": "109848",
        "issuedAt": "2026-08-05",
        "total": 512.4,
        "purchaseOrderNumber": "4041451-1",
        "fileId": "f-1",
        "deletedAt": None,
        "lines": [],
    },
    {
        "id": "inv-2",
        "supplierName": "Trents",
        "referenceNumber": "2001",
        "issuedAt": "2026-08-06",
        "total": 88.0,
        "purchaseOrderNumber": None,
        "fileId": None,
        "deletedAt": None,
        "lines": [],
    },
    {"id": "inv-dead", "supplierName": "Gone", "deletedAt": "2026-01-01"},
]

RECEIVED = [
    {
        "id": "ri-1",
        "supplierName": "Bidfood",
        "invoiceNumber": "109700",
        "invoicedAt": "2026-08-03T00:00:00+12:00",
        "total": 300.0,
        "purchaseOrderNumber": "4041000",
        "reconciled": False,
        "creditRequest": False,
        "deletedAt": None,
        "lines": [{"itemId": "should-never-surface"}],
    }
]

STATEMENTS = [
    {
        "id": "st-1",
        "supplierName": "Bidfood",
        "statementNumber": "JULY",
        "startAt": "2026-07-01T00:00:00+12:00",
        "endAt": "2026-07-31T00:00:00+12:00",
        "statementAmount": 4200.5,
        "reconciledAmount": 4000.0,
        "reconciledStockReceivedItems": ["a", "b"],
        "deletedAt": None,
    }
]

DETAIL = {
    "id": "inv-1",
    "supplierName": "Bidfood",
    "referenceNumber": "109848",
    "issuedAt": "2026-08-05",
    "purchaseOrderNumber": "4041451-1",
    "subtotal": 445.57,
    "taxAmount": 66.83,
    "total": 512.4,
    "isReceived": False,
    "reconciled": False,
    "fileId": "f-1",
    "lines": [
        {
            "id": "l-1",
            "code": "165097",
            "description": "Ginger Root",
            "unit": "Kilo",
            "quantityReceived": 0.58,
            "unitCostExclTax": 9.35,
            "totalCostExclTax": 5.423,
            "linkedItemId": "item-ginger",
            "deletedAt": None,
        },
        {"id": "l-dead", "description": "gone", "deletedAt": "2026-01-01"},
    ],
}

PO_SUMMARY = [
    {
        "id": "po-1",
        "orderNumber": "1520538",
        "supplierName": "Aitkens",
        "orderedBy": "Arthur",
        "status": "Sent",
        "createdAt": "2026-08-06T21:56:27+00:00",
        "subtotal": 132.25,
        "tax": 19.84,
        "total": 152.09,
        "isReceived": False,
    },
    {
        "id": "po-2",
        "orderNumber": "1520000",
        "supplierName": "Bidfood",
        "orderedBy": "Jim",
        "status": "Received",
        "createdAt": "2026-08-01T00:00:00+00:00",
        "subtotal": 10.0,
        "tax": 1.5,
        "total": 11.5,
        "isReceived": True,
    },
]

PO_DETAIL = {
    "id": "po-1",
    "orderNumber": "1520538",
    "supplierName": "Aitkens",
    "status": "Sent",
    "createdAt": "2026-08-06T21:56:27+00:00",
    "orderedBy": "Arthur",
    "subtotal": 132.25,
    "tax": 19.84,
    "total": 152.09,
    "isReceived": False,
    "invoiceNumber": "173670",
    # Live shape (21 Aug 2026): PO lines carry NO line total — the
    # consolidator computes quantity × unitCost.
    "lines": [
        {
            "itemName": "Butter",
            "quantityOrdered": 4,
            "unitName": "5 KG",
            "unitCost": 33.06,
            "deletedAt": None,
        }
    ],
}


class Api:
    def call_api(self, connector, action, params=None):
        p = dict(params or {})
        if action == "resolve_dates":
            return {"window": dict(WINDOW)}
        if action == "list_stock_invoices":
            assert p.get("status") == "NotReceived"
            import copy

            return copy.deepcopy(OUTSTANDING)
        if action == "list_received_invoices":
            assert p["from_date"] == "2026-08-01" and p["to_date"] == "2026-08-08"
            import copy

            return copy.deepcopy(RECEIVED)
        if action == "list_supplier_statements":
            assert "T" in p["from_iso"]
            import copy

            return copy.deepcopy(STATEMENTS)
        if action == "get_invoice_detail":
            if p.get("invoice_id") != "inv-1":
                return {"error": "not found"}
            import copy

            return copy.deepcopy(DETAIL)
        if action == "get_purchase_orders_summary":
            import copy

            return copy.deepcopy(PO_SUMMARY)
        if action == "get_purchase_order_detail":
            if p.get("order_id") != "po-1":
                return {"error": "not found"}
            import copy

            return copy.deepcopy(PO_DETAIL)
        raise AssertionError(f"unexpected action {action}")


def run(code, api, **params):
    ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(code, ns)
    return ns["run"]({"venue": "La Zeppa", **params}, api.call_api, lambda m: None)


class TestGetInvoices:
    def test_outstanding_default_headers_only(self):
        out = run(INVOICES_CODE, Api())
        assert out["kind"] == "outstanding"
        assert out["total_matches"] == 2  # deleted row dropped
        row = next(r for r in out["rows"] if r["id"] == "inv-1")
        assert row == {
            "id": "inv-1",
            "supplier": "Bidfood",
            "invoice_number": "109848",
            "date": "2026-08-05",
            "total": 512.4,
            "po_number": "4041451-1",
            "has_file": True,
        }
        assert all("lines" not in r for r in out["rows"])

    def test_received_requires_period(self):
        out = run(INVOICES_CODE, Api(), kind="received")
        assert "period" in out["error"]

    def test_received_headers_only_with_agg_note(self):
        out = run(INVOICES_CODE, Api(), kind="received", period="last week")
        assert out["rows"][0]["invoice_number"] == "109700"
        assert all("lines" not in r for r in out["rows"])
        assert "get_received_items_for_period" in out["note"]
        assert out["window"]["start"] == WINDOW["start"]

    def test_statements_kind(self):
        out = run(INVOICES_CODE, Api(), kind="statements", period="july")
        s = out["rows"][0]
        assert s["statement_number"] == "JULY"
        assert s["statement_amount"] == 4200.5
        assert s["invoices_reconciled"] == 2

    def test_supplier_query_filter(self):
        out = run(INVOICES_CODE, Api(), query="trents")
        assert [r["supplier"] for r in out["rows"]] == ["Trents"]

    def test_one_invoice_summary(self):
        out = run(INVOICES_CODE, Api(), invoice_id="inv-1")
        inv = out["invoice"]
        assert inv["supplier"] == "Bidfood"
        assert inv["line_count"] == 1  # deleted line dropped
        assert inv["lines"][0] == {
            "description": "Ginger Root",
            "code": "165097",
            "quantity": 0.58,
            "unit": "Kilo",
            "unit_cost": 9.35,
            "line_total": 5.42,
            "item_id": "item-ginger",
        }

    def test_one_invoice_full_passthrough(self):
        out = run(INVOICES_CODE, Api(), invoice_id="inv-1", detail="full")
        assert out["invoice"]["referenceNumber"] == "109848"

    def test_unknown_invoice_errors(self):
        assert run(INVOICES_CODE, Api(), invoice_id="nope") == {"error": "not found"}


class TestGetPurchaseOrders:
    def test_list_passes_transform_fields_through(self):
        out = run(POS_CODE, Api())
        assert out["total_matches"] == 2
        # camelCase pass-through — the orders dashboard parses this shape.
        assert out["rows"][0]["orderNumber"] == "1520538"
        assert "lines" not in out["rows"][0]

    def test_status_and_query_filters(self):
        out = run(POS_CODE, Api(), status="received")
        assert [r["id"] for r in out["rows"]] == ["po-2"]
        out = run(POS_CODE, Api(), query="aitk")
        assert [r["id"] for r in out["rows"]] == ["po-1"]

    def test_one_order_summary_lines(self):
        out = run(POS_CODE, Api(), order_id="po-1")
        po = out["purchase_order"]
        assert po["order_number"] == "1520538"
        assert po["linked_invoice_number"] == "173670"
        assert po["lines"] == [
            {
                "item": "Butter",
                "quantity": 4,
                "unit": "5 KG",
                "unit_cost": 33.06,
                "line_total": 132.24,  # computed 4 × 33.06
            }
        ]

    def test_unknown_order_errors(self):
        assert run(POS_CODE, Api(), order_id="nope") == {"error": "not found"}


# ── received-items group rollups ─────────────────────────────────────────

RECEIVED_FEED = [
    {
        "id": "ri-1",
        "supplierName": "Bidfood",
        "invoiceNumber": "109700",
        "invoicedAt": "2026-08-03T00:00:00+12:00",
        "creditRequest": False,
        "lines": [
            {
                "StockItemId": "i-flour",
                "quantityReceived": 2,
                "unitCost": 20.0,
                "unitRatio": 10.0,
                "unitName": "10 KG",
            },
            {
                "StockItemId": "i-oil",
                "quantityReceived": 1,
                "unitCost": 30.0,
                "unitRatio": 5.0,
                "unitName": "5 L",
            },
            {
                "StockItemId": "i-gin",
                "quantityReceived": 6,
                "unitCost": 45.0,
                "unitRatio": 0.7,
                "unitName": "700 mL",
            },
        ],
    }
]

CATALOGUE = [
    {"id": "i-flour", "name": "FLOUR HIGH GRADE", "groupId": "g-dry", "groupName": "Dry Goods"},
    {"id": "i-oil", "name": "OIL CANOLA", "groupId": "g-dry", "groupName": "Dry Goods"},
    {"id": "i-gin", "name": "GIN LONDON DRY", "groupId": "g-spirits", "groupName": "Spirits"},
]

SUBCATS = [
    {"id": "g-dry", "categoryId": "c-food", "categoryName": "Food", "name": "Dry Goods"},
    {"id": "g-spirits", "categoryId": "c-bev", "categoryName": "Beverage", "name": "Spirits"},
]


class ItemsApi:
    def call_api(self, connector, action, params=None):
        if action == "resolve_dates":
            return {"window": dict(WINDOW)}
        if action == "get_received_invoices":
            import copy

            return copy.deepcopy(RECEIVED_FEED)
        if action == "get_stock_items_raw":
            import copy

            return copy.deepcopy(CATALOGUE)
        if action == "get_stock_units":
            return []
        if action == "get_stock_item_groups":
            import copy

            return copy.deepcopy(SUBCATS)
        raise AssertionError(f"unexpected action {action}")


def run_items(**params):
    ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(ITEMS_CODE, ns)
    return ns["run"](
        {"venue": "La Zeppa", "period": "last week", **params},
        ItemsApi().call_api,
        lambda m: None,
    )


class TestReceivedItemsRollups:
    def test_group_rollup(self):
        out = run_items(group_by="group")
        rows = {r["group"]: r for r in out["rows"]}
        assert rows["Dry Goods"]["spend"] == 70.0  # 2*20 + 1*30
        assert rows["Dry Goods"]["distinct_items"] == 2
        assert rows["Spirits"]["spend"] == 270.0  # 6*45
        assert rows["Spirits"]["top_items"] == [
            {"item": "GIN LONDON DRY", "spend": 270.0}
        ]
        assert out["summary"]["net_spend"] == 340.0

    def test_super_group_rollup(self):
        out = run_items(group_by="super_group")
        rows = {r["super_group"]: r for r in out["rows"]}
        assert rows["Food"]["spend"] == 70.0
        assert rows["Beverage"]["spend"] == 270.0

    def test_item_query_filter(self):
        out = run_items(query="oil")
        assert len(out["rows"]) == 1
        assert out["rows"][0]["item_name"] == "OIL CANOLA"
        assert out["rows"][0]["quantity_base"] == 5.0

    def test_group_filter(self):
        out = run_items(group="dry")
        assert {r["item_name"] for r in out["rows"]} == {
            "FLOUR HIGH GRADE",
            "OIL CANOLA",
        }

    def test_limit_rolls_up_others(self):
        out = run_items(limit=1)
        assert len(out["rows"]) == 2  # top row + (others)
        assert out["rows"][0]["item_name"] == "GIN LONDON DRY"
        others = out["rows"][1]
        assert others["item_name"] == "(others)"
        assert others["rows_rolled_up"] == 2
        assert others["spend"] == 70.0

    def test_item_mode_unchanged(self):
        out = run_items()
        gin = next(r for r in out["rows"] if r["item_name"] == "GIN LONDON DRY")
        assert gin["quantity_base"] == 4.2  # 6 × 0.7
        assert gin["unit_cost_avg"] == round(45.0 / 0.7, 4)
