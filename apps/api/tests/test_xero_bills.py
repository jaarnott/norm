"""Bills in Xero for invoices Norm receives into Loaded (services/xero_bills.py).

Pins: the parameters built from a received invoice's working values (struck
lines out, total-only lines as one unit, per-line GST, the Loaded invoice id
as the idempotency key); the gate (no Xero row, or a row that doesn't push
bills, means nothing happens); and that a Xero failure is recorded on the
document and never raised into the receive.
"""

import uuid
from unittest.mock import patch

from app.connectors.tool_executor import ToolResult
from app.db.models import Connection, Venue
from app.services import xero_bills

DOC = {
    "supplier_name": "Fresh Fish Co",
    "reference_number": "FF-100",
    "issued_at": "2026-10-01",
    "due_at": "2026-10-20",
    "unit_cost_includes_tax": False,
    "lines": [
        {
            "description": "Snapper fillets",
            "quantity_received": 10,
            "unit_cost": 25.0,
            "total_cost": 250.0,
            "tax_amount": 37.5,
            "category": "Food",
        },
        {
            "description": "Delivery",
            "quantity_received": 1,
            "unit_cost": 50.0,
            "total_cost": 50.0,
            "tax_amount": 0,
        },
        {
            "description": "Struck",
            "quantity_received": 2,
            "unit_cost": 9.0,
            "struck": True,
        },
        {
            "description": "Total only",
            "quantity_received": None,
            "unit_cost": None,
            "total_cost": 12.5,
        },
        {
            "description": "Nothing",
            "quantity_received": 0,
            "unit_cost": None,
            "total_cost": None,
        },
    ],
}


class TestParams:
    def test_lines_and_header_from_the_working_values(self):
        p = xero_bills.bill_params_from_doc("v1", "loaded-1", DOC)
        assert p["venue_id"] == "v1"
        assert p["supplier"] == "Fresh Fish Co"
        assert p["invoice_number"] == "FF-100"
        assert p["date"] == "2026-10-01" and p["due_date"] == "2026-10-20"
        assert p["line_amount_types"] == "Exclusive"
        assert p["idempotency_key"] == "norm:loaded:loaded-1"
        assert p["lines"] == [
            {
                "description": "Snapper fillets",
                "quantity": 10.0,
                "unit_amount": 25.0,
                "gst": "yes",
                "category": "Food",
            },
            {
                "description": "Delivery",
                "quantity": 1.0,
                "unit_amount": 50.0,
                "gst": "no",
            },
            {
                "description": "Total only",
                "quantity": 1.0,
                "unit_amount": 12.5,
                "gst": "yes",
            },
        ]

    def test_inclusive_costs_and_a_missing_due_date(self):
        p = xero_bills.bill_params_from_doc(
            "v1", "l2", {**DOC, "unit_cost_includes_tax": True, "due_at": None}
        )
        assert p["line_amount_types"] == "Inclusive"
        assert "due_date" not in p


def _venue(db_session, name="Glass Goose"):
    v = Venue(id=str(uuid.uuid4()), name=name)
    db_session.add(v)
    db_session.flush()
    return v


def _xero_row(db_session, venue, mapping=None, **kw):
    row = Connection(
        connector_name="xero",
        venue_id=venue.id,
        enabled="true",
        config={"xero_tenant_id": "t1", "xero_mapping": mapping or {}},
        access_token="at",
        refresh_token="rt",
        **kw,
    )
    db_session.add(row)
    db_session.flush()
    return row


class TestGate:
    def test_no_xero_row_means_nothing_happens(self, db_session):
        v = _venue(db_session)
        with patch("app.connectors.tool_executor.execute_connector_tool") as run:
            out = xero_bills.push_bill_after_receive(
                db_session, db_session, v.id, "l1", dict(DOC)
            )
        assert out is None and run.call_count == 0

    def test_a_row_that_does_not_push_means_nothing_happens(self, db_session):
        v = _venue(db_session)
        _xero_row(db_session, v, {"accounts": {"default": "51200"}})
        with patch("app.connectors.tool_executor.execute_connector_tool") as run:
            out = xero_bills.push_bill_after_receive(
                db_session, db_session, v.id, "l1", dict(DOC)
            )
        assert out is None and run.call_count == 0

    def test_a_reconnect_needed_row_is_off(self, db_session):
        v = _venue(db_session)
        _xero_row(db_session, v, {"push_bills": True}, needs_reconnect=True)
        assert xero_bills.mapping_for(db_session, v.id) is None


class TestPush:
    def test_creates_the_bill_through_create_bill_and_records_it(self, db_session):
        v = _venue(db_session)
        _xero_row(
            db_session,
            v,
            {
                "push_bills": True,
                "bill_status": "AUTHORISED",
                "accounts": {"default": "51200"},
            },
        )
        data = dict(DOC)
        created = {
            "created": True,
            "invoice_id": "inv-9",
            "number": "FF-100",
            "status": "AUTHORISED",
            "total": 345.0,
        }
        with patch(
            "app.connectors.tool_executor.execute_connector_tool",
            return_value=ToolResult(success=True, payload=created),
        ) as run:
            out = xero_bills.push_bill_after_receive(
                db_session, db_session, v.id, "loaded-1", data
            )
        assert out["created"] is True and out["invoice_id"] == "inv-9" and out["at"]
        assert data["xero_bill"] == out
        kwargs = run.call_args.kwargs
        assert kwargs["connector_name"] == "xero" and kwargs["action"] == "create_bill"
        assert kwargs["venue_id"] == v.id and kwargs["strict_venue"] is True
        assert kwargs["params"]["status"] == "AUTHORISED"
        assert kwargs["params"]["idempotency_key"] == "norm:loaded:loaded-1"
        assert len(kwargs["params"]["lines"]) == 3

    def test_an_already_created_bill_is_not_sent_again(self, db_session):
        v = _venue(db_session)
        _xero_row(db_session, v, {"push_bills": True})
        data = {**DOC, "xero_bill": {"created": True, "invoice_id": "inv-old"}}
        with patch("app.connectors.tool_executor.execute_connector_tool") as run:
            out = xero_bills.push_bill_after_receive(
                db_session, db_session, v.id, "loaded-1", data
            )
        assert out == {"created": True, "invoice_id": "inv-old"} and run.call_count == 0

    def test_a_xero_failure_is_recorded_not_raised(self, db_session):
        v = _venue(db_session)
        _xero_row(db_session, v, {"push_bills": True})
        data = dict(DOC)
        with patch(
            "app.connectors.tool_executor.execute_connector_tool",
            return_value=ToolResult(
                success=True, payload={"error": "no account code for: Delivery"}
            ),
        ):
            out = xero_bills.push_bill_after_receive(
                db_session, db_session, v.id, "loaded-1", data
            )
        assert out["created"] is False and "no account code" in out["error"]
        assert data["xero_bill"]["created"] is False

    def test_an_exception_is_swallowed_and_recorded(self, db_session):
        v = _venue(db_session)
        _xero_row(db_session, v, {"push_bills": True})
        data = dict(DOC)
        with patch(
            "app.connectors.tool_executor.execute_connector_tool",
            side_effect=RuntimeError("boom"),
        ):
            out = xero_bills.push_bill_after_receive(
                db_session, db_session, v.id, "loaded-1", data
            )
        assert out["created"] is False and out["error"] == "boom"

    def test_nothing_priced_is_an_error_without_a_xero_call(self, db_session):
        v = _venue(db_session)
        _xero_row(db_session, v, {"push_bills": True})
        data = {**DOC, "lines": [{"description": "x", "struck": True}]}
        with patch("app.connectors.tool_executor.execute_connector_tool") as run:
            out = xero_bills.push_bill_after_receive(
                db_session, db_session, v.id, "loaded-1", data
            )
        assert (
            out["created"] is False
            and "nothing to bill" in out["error"]
            and run.call_count == 0
        )
