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
    def test_not_switched_on_means_nothing_happens(self, db_session):
        """The person's switch decides — a connected venue is not enough."""
        v = _venue(db_session)
        _xero_row(db_session, v, {"accounts": {"default": "51200"}})
        with patch("app.connectors.tool_executor.execute_connector_tool") as run:
            out = xero_bills.push_bill_after_receive(
                db_session, db_session, v.id, "l1", dict(DOC)
            )
        assert out is None and run.call_count == 0

    def test_no_xero_row_means_nothing_happens(self, db_session):
        v = _venue(db_session)
        with patch("app.connectors.tool_executor.execute_connector_tool") as run:
            out = xero_bills.push_bill_after_receive(
                db_session, db_session, v.id, "l1", dict(DOC), enabled=True
            )
        assert out is None and run.call_count == 0

    def test_a_reconnect_needed_row_is_off(self, db_session):
        v = _venue(db_session)
        _xero_row(db_session, v, {}, needs_reconnect=True)
        assert xero_bills.mapping_for(db_session, v.id) is None
        with patch("app.connectors.tool_executor.execute_connector_tool") as run:
            out = xero_bills.push_bill_after_receive(
                db_session, db_session, v.id, "l1", dict(DOC), enabled=True
            )
        assert out is None and run.call_count == 0


class TestSwitch:
    """The switch lives in the person's receiving preferences, beside the
    gates, and applies at every level."""

    def test_it_defaults_off_and_reads_back_when_set(self):
        from app.services import venue_autopilot as VA

        assert VA.settings_from(None)[VA.CREATE_XERO_BILL] is False
        assert (
            VA.settings_from({VA.CREATE_XERO_BILL: True})[VA.CREATE_XERO_BILL] is True
        )
        # Only a real True counts, as for every switch.
        assert (
            VA.settings_from({VA.CREATE_XERO_BILL: "yes"})[VA.CREATE_XERO_BILL] is False
        )

    def test_it_is_not_a_gate(self):
        from app.services import venue_autopilot as VA

        assert VA.CREATE_XERO_BILL not in VA.GATES


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
                db_session, db_session, v.id, "loaded-1", data, enabled=True
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
        _xero_row(db_session, v, {})
        data = {**DOC, "xero_bill": {"created": True, "invoice_id": "inv-old"}}
        with patch("app.connectors.tool_executor.execute_connector_tool") as run:
            out = xero_bills.push_bill_after_receive(
                db_session, db_session, v.id, "loaded-1", data, enabled=True
            )
        assert out == {"created": True, "invoice_id": "inv-old"} and run.call_count == 0

    def test_a_xero_failure_is_recorded_not_raised(self, db_session):
        v = _venue(db_session)
        _xero_row(db_session, v, {})
        data = dict(DOC)
        with patch(
            "app.connectors.tool_executor.execute_connector_tool",
            return_value=ToolResult(
                success=True, payload={"error": "no account code for: Delivery"}
            ),
        ):
            out = xero_bills.push_bill_after_receive(
                db_session, db_session, v.id, "loaded-1", data, enabled=True
            )
        assert out["created"] is False and "no account code" in out["error"]
        assert data["xero_bill"]["created"] is False

    def test_an_exception_is_swallowed_and_recorded(self, db_session):
        v = _venue(db_session)
        _xero_row(db_session, v, {})
        data = dict(DOC)
        with patch(
            "app.connectors.tool_executor.execute_connector_tool",
            side_effect=RuntimeError("boom"),
        ):
            out = xero_bills.push_bill_after_receive(
                db_session, db_session, v.id, "loaded-1", data, enabled=True
            )
        assert out["created"] is False and out["error"] == "boom"

    def test_nothing_priced_is_an_error_without_a_xero_call(self, db_session):
        v = _venue(db_session)
        _xero_row(db_session, v, {})
        data = {**DOC, "lines": [{"description": "x", "struck": True}]}
        with patch("app.connectors.tool_executor.execute_connector_tool") as run:
            out = xero_bills.push_bill_after_receive(
                db_session, db_session, v.id, "loaded-1", data, enabled=True
            )
        assert (
            out["created"] is False
            and "nothing to bill" in out["error"]
            and run.call_count == 0
        )


# ---------------------------------------------------------------------------
# The supplier's file on the bill
# ---------------------------------------------------------------------------

import base64  # noqa: E402

from app.db.config_models import ConnectionSpec  # noqa: E402


class _Resp:
    def __init__(self, status=200, text="{}"):
        self.status_code = status
        self.text = text


def _xero_spec(db_session):
    db_session.add(
        ConnectionSpec(
            connector_name="xero",
            display_name="Xero",
            execution_mode="template",
            auth_type="oauth2",
            auth_config={},
            oauth_config={"token_url": "https://example.test/t", "client_id": "x"},
            tools=[],
        )
    )
    db_session.flush()


class _Loaded:
    """LoadedInvoiceClient stand-in serving one PDF."""

    def __init__(self, *a, **k):
        pass

    def file_base64(self, file_id):
        assert file_id == "file-1"
        return base64.b64encode(b"%PDF-1.4 fake").decode(), "application/pdf"


class TestAttachment:
    def test_names_are_safe_and_typed(self):
        assert (
            xero_bills.attachment_name("INV 1023/A", "l1", "application/pdf")
            == "INV_1023_A.pdf"
        )
        assert (
            xero_bills.attachment_name(None, "abc-123", "image/jpeg") == "abc-123.jpg"
        )
        assert xero_bills.attachment_name("///", "", "text/weird") == "invoice.pdf"
        assert len(xero_bills.attachment_name("x" * 300, "", "application/pdf")) <= 84

    def test_uploads_the_loaded_file_to_the_bill(self, db_session):
        v = _venue(db_session)
        _xero_row(db_session, v, {})
        _xero_spec(db_session)
        sent = {}

        def fake_put(url, headers=None, content=None, timeout=None):
            sent.update(url=url, headers=headers, content=content)
            return _Resp(200)

        with (
            patch("app.services.received_invoice.LoadedInvoiceClient", _Loaded),
            patch(
                "app.services.oauth_service.get_valid_access_token", return_value="tok"
            ),
            patch("httpx.put", fake_put),
        ):
            out = xero_bills.attach_file(
                db_session,
                db_session,
                v.id,
                "xero-inv-1",
                "file-1",
                invoice_number="FF-100",
                loaded_invoice_id="loaded-1",
            )
        assert out == {"uploaded": True, "filename": "FF-100.pdf", "bytes": 13}
        assert (
            sent["url"]
            == "https://api.xero.com/api.xro/2.0/Invoices/xero-inv-1/Attachments/FF-100.pdf"
        )
        assert sent["content"] == b"%PDF-1.4 fake"
        assert sent["headers"]["Authorization"] == "Bearer tok"
        assert sent["headers"]["xero-tenant-id"] == "t1"
        assert sent["headers"]["Content-Type"] == "application/pdf"

    def test_a_file_already_on_the_bill_is_replaced(self, db_session):
        v = _venue(db_session)
        _xero_row(db_session, v, {})
        _xero_spec(db_session)
        calls = []

        def fake_put(url, **k):
            calls.append("PUT")
            return _Resp(400, "an attachment with this name already exists")

        def fake_post(url, **k):
            calls.append("POST")
            return _Resp(200)

        with (
            patch("app.services.received_invoice.LoadedInvoiceClient", _Loaded),
            patch(
                "app.services.oauth_service.get_valid_access_token", return_value="tok"
            ),
            patch("httpx.put", fake_put),
            patch("httpx.post", fake_post),
        ):
            out = xero_bills.attach_file(db_session, db_session, v.id, "x1", "file-1")
        assert out["uploaded"] is True and calls == ["PUT", "POST"]

    def test_a_xero_error_is_reported_not_raised(self, db_session):
        v = _venue(db_session)
        _xero_row(db_session, v, {})
        _xero_spec(db_session)
        with (
            patch("app.services.received_invoice.LoadedInvoiceClient", _Loaded),
            patch(
                "app.services.oauth_service.get_valid_access_token", return_value="tok"
            ),
            patch("httpx.put", lambda *a, **k: _Resp(403, "forbidden")),
            patch("httpx.post", lambda *a, **k: _Resp(403, "forbidden")),
        ):
            out = xero_bills.attach_file(db_session, db_session, v.id, "x1", "file-1")
        assert out["uploaded"] is False and "Xero 403" in out["error"]

    def test_the_file_follows_a_created_bill_and_is_recorded(self, db_session):
        v = _venue(db_session)
        _xero_row(db_session, v, {})
        data = {**DOC, "file_id": "file-1"}
        created = {"created": True, "invoice_id": "inv-9", "number": "FF-100"}
        with (
            patch(
                "app.connectors.tool_executor.execute_connector_tool",
                return_value=ToolResult(success=True, payload=created),
            ),
            patch(
                "app.services.xero_bills.attach_file",
                return_value={"uploaded": True, "filename": "FF-100.pdf"},
            ) as attach,
        ):
            out = xero_bills.push_bill_after_receive(
                db_session, db_session, v.id, "loaded-1", data, enabled=True
            )
        assert out["attachment"]["uploaded"] is True and out["attachment"]["at"]
        assert data["xero_bill"]["attachment"]["filename"] == "FF-100.pdf"
        args = attach.call_args
        assert args.args[3:5] == ("inv-9", "file-1")
        assert args.kwargs["invoice_number"] == "FF-100"

    def test_a_bill_without_a_file_is_untouched(self, db_session):
        v = _venue(db_session)
        _xero_row(db_session, v, {})
        created = {"created": True, "invoice_id": "inv-9"}
        with (
            patch(
                "app.connectors.tool_executor.execute_connector_tool",
                return_value=ToolResult(success=True, payload=created),
            ),
            patch("app.services.xero_bills.attach_file") as attach,
        ):
            out = xero_bills.push_bill_after_receive(
                db_session, db_session, v.id, "loaded-1", dict(DOC), enabled=True
            )
        assert out["created"] is True and "attachment" not in out
        attach.assert_not_called()

    def test_a_later_call_retries_only_the_missing_file(self, db_session):
        v = _venue(db_session)
        _xero_row(db_session, v, {})
        data = {
            **DOC,
            "file_id": "file-1",
            "xero_bill": {
                "created": True,
                "invoice_id": "inv-9",
                "attachment": {"uploaded": False, "error": "Xero 503"},
            },
        }
        with (
            patch("app.connectors.tool_executor.execute_connector_tool") as create,
            patch(
                "app.services.xero_bills.attach_file",
                return_value={"uploaded": True, "filename": "FF-100.pdf"},
            ) as attach,
        ):
            out = xero_bills.push_bill_after_receive(
                db_session, db_session, v.id, "loaded-1", data, enabled=True
            )
        create.assert_not_called()  # the bill is never created twice
        attach.assert_called_once()
        assert out["attachment"]["uploaded"] is True

    def test_an_uploaded_file_is_not_sent_again(self, db_session):
        v = _venue(db_session)
        _xero_row(db_session, v, {})
        data = {
            **DOC,
            "file_id": "file-1",
            "xero_bill": {
                "created": True,
                "invoice_id": "inv-9",
                "attachment": {"uploaded": True},
            },
        }
        with patch("app.services.xero_bills.attach_file") as attach:
            xero_bills.push_bill_after_receive(
                db_session, db_session, v.id, "loaded-1", data, enabled=True
            )
        attach.assert_not_called()
