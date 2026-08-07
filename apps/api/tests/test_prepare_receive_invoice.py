"""The prepare_receive_invoice consolidator — opens ONE invoice as a draft.

Exec'd under the REAL sandbox namespace so any use of a builtin the sandbox
doesn't provide fails here. Its output must match
app.services.received_invoice.build_received_invoice_data (the web draft uses
that Python function; this sandbox copy must stay in sync).
"""

import pathlib

import pytest

from app.connectors.function_executor import _SAFE_BUILTINS, _SAFE_MODULES
from app.services.received_invoice import build_received_invoice_data

FUNCTION_CODE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "config"
    / "consolidators"
    / "prepare_receive_invoice.py"
).read_text(encoding="utf-8")

DETAIL = {
    "id": "inv-1",
    "referenceNumber": "F56584601",
    "supplierName": "DAILY BREAD",
    "linkedSupplierId": "sup-db",
    "purchaseOrderNumber": "1520441",
    "linkedPurchaseOrderId": "po-1",
    "issuedAt": "2026-07-29",
    "dueAt": "2026-08-05",
    "subtotal": 100.2,
    "taxAmount": 15.03,
    "total": 115.23,
    "fileId": "file-1",
    "isReceived": False,
    "lines": [
        {
            "id": "ln-1",
            "code": "SB_FocaT_Sli_Sgl",
            "description": "FOCACCIA",
            "unit": "Each",
            "itemType": "Default",
            "quantityOrdered": None,
            "quantityReceived": 15,
            "unitCostExclTax": 5.08,
            "totalCostExclTax": 76.2,
            "linkedUnitId": "u-each",
            "linkedUnitRatio": 1,
            "linkedItemId": "item-1",
        }
    ],
}


class Api:
    def __init__(self, details):
        self.details = details

    def call_api(self, connector, action, params=None):
        assert connector == "loadedhub"
        assert action == "get_invoice_detail"
        return self.details[(params or {})["invoice_id"]]


def _run(api, **params):
    ns = {"__builtins__": _SAFE_BUILTINS, **_SAFE_MODULES}
    exec(FUNCTION_CODE, ns)
    return ns["run"]({"venue": "La Zeppa", **params}, api.call_api, lambda m: None)


def test_shapes_identically_to_the_python_builder():
    out = _run(Api({"inv-1": DETAIL}), invoice_id="inv-1")
    # The sandbox copy and the Python source of truth must agree exactly.
    assert out == build_received_invoice_data(DETAIL)


def test_populates_the_gaps_the_old_card_missed():
    out = _run(Api({"inv-1": DETAIL}), invoice_id="inv-1")
    assert out["total"] == 115.23 and out["subtotal"] == 100.2
    assert out["lines"][0]["quantity_received"] == 15
    assert out["lines"][0]["unit_cost"] == 5.08
    assert out["linked_purchase_order_id"] == "po-1"
    assert out["lines"][0]["original_unit_id"] == "u-each"


def test_raises_without_an_invoice_id():
    # Raising (not returning {"error"}) means execute_function reports data=None,
    # so the tool loop creates NO working document from a bad result.
    with pytest.raises(Exception):
        _run(Api({}), invoice_id=None)


def test_raises_when_already_received():
    received = {**DETAIL, "isReceived": True}
    with pytest.raises(Exception):
        _run(Api({"inv-1": received}), invoice_id="inv-1")
