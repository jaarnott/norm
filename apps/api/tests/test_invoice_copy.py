"""Norm's copy of an invoice that came with none (owner, 24 Oct 2026): an
Ordermentum order reaches Loaded as data only, so the receive card, the daily
unit check and the activity page had no copy to show beside its lines."""

import io

from pypdf import PdfReader

from app.services import invoice_copy

DETAIL = {
    "id": "inv-1",
    "referenceNumber": "OMI1112847",
    "issuedAt": "2026-10-25",
    "dueAt": "2026-10-26",
    "receivedAt": "2026-10-25T00:00:00+00:00",
    "subtotal": 52.17,
    "taxAmount": 3.6,
    "total": 55.77,
    "taxNumber": "67095158062",
    "supplierName": "Sonoma Baking Company",
    "billingName": "AppleGum Coffee",
    "purchaseOrderNumber": "505043",
    "fileId": None,
    "source": "Ordermentum",
    "type": "Invoice",
    "lines": [
        {
            "description": "Croissant Plain",
            "code": "30132",
            "quantityReceived": 1.0,
            "unit": "6 Pack",
            "unitCostExclTax": 14.96,
            "totalCostExclTax": 14.96,
            "taxAmount": 1.496,
        },
        {
            "description": "Bread Country Loaf Sliced",
            "code": "10250",
            "quantityReceived": 2.0,
            "unit": "Each",
            "unitCostExclTax": 10.676,
            "totalCostExclTax": 21.352,
            "taxAmount": 0,
        },
        {
            "description": "A line Loaded deleted",
            "quantityReceived": 9.0,
            "unitCostExclTax": 99.0,
            "deletedAt": "2026-10-25T01:00:00Z",
        },
    ],
}


def _text(pdf: bytes) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)


def test_the_copy_shows_the_order_and_says_norm_rebuilt_it():
    pdf = invoice_copy.render(DETAIL, venue_name="Applegum Cafe")
    assert pdf.startswith(b"%PDF")
    text = _text(pdf)
    for shown in (
        "Tax Invoice",
        "OMI1112847",
        "25/10/2026",
        "Sonoma Baking Company",
        "ABN: 67095158062",
        "AppleGum Coffee",
        "505043",
        "* Croissant Plain",  # carries GST
        "$10.676",  # a price kept to a tenth of a cent is not rounded away
        "$21.352",
        "$55.77",
        "Rebuilt by Norm from the order data Ordermentum sent",
    ):
        assert shown in text, shown
    # No unit column: Loaded's unit is the one Norm booked, not one the
    # supplier printed. A deleted line is left out.
    assert "6 Pack" not in text and "A line Loaded deleted" not in text
    assert "* Bread" not in text  # no GST on it
    # Not from Ordermentum: said plainly too.
    other = _text(invoice_copy.render({**DETAIL, "source": "Email"}))
    assert "Rebuilt by Norm from this invoice's data in Loaded" in other
    # One that does have a copy never claims it hasn't.
    attached = _text(invoice_copy.render({**DETAIL, "fileId": "f-1"}))
    assert "The supplier's own copy is attached in Loaded" in attached
    assert "no invoice copy" not in attached


def test_the_routes(client, admin_headers, venue, monkeypatch):
    import app.routers.invoice_fixes as IF

    class FakeLh:
        def invoice(self, invoice_id):
            return {**DETAIL, "id": invoice_id}

        def file_base64(self, file_id):  # pragma: no cover — must not be asked
            raise AssertionError("there is no file to fetch")

    monkeypatch.setattr(IF, "_Loaded", lambda db, cdb, vid: FakeLh())
    r = client.get(
        f"/api/invoice-fixes/norm-copy?venue_id={venue.id}&invoice_id=inv-1",
        headers=admin_headers,
    )
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert "OMI1112847" in _text(r.content)
    # The copy route: a 404 for an invoice with no copy, unless asked for
    # Norm's copy instead (the activity page's invoice with no kept record).
    url = f"/api/invoice-fixes/file?venue_id={venue.id}&invoice_id=inv-1"
    assert client.get(url, headers=admin_headers).status_code == 404
    r = client.get(url + "&or_norm_copy=true", headers=admin_headers)
    assert r.status_code == 200 and "Rebuilt by Norm" in _text(r.content)


def test_an_invoice_gone_from_loaded_is_drawn_from_norms_record(
    client, admin_headers, venue, db_session, monkeypatch
):
    """Sonoma OMI1112847 was removed from Loaded an hour after the unit check
    flagged it, and its copy failed with a server error (24 Oct 2026)."""
    import app.routers.invoice_fixes as IF
    from app.db.models import WorkingDocument

    class GoneLh:
        def invoice(self, invoice_id):
            raise RuntimeError("Loaded GET /1.0/stock/invoices/x → 404: not found")

    monkeypatch.setattr(IF, "_Loaded", lambda db, cdb, vid: GoneLh())
    url = f"/api/invoice-fixes/norm-copy?venue_id={venue.id}&invoice_id=inv-9"
    assert client.get(url, headers=admin_headers).status_code == 404
    db_session.add(
        WorkingDocument(
            doc_type="received_invoice",
            connector_name="loadedhub",
            venue_id=venue.id,
            external_ref={"invoice_id": "inv-9"},
            data={
                "invoice_id": "inv-9",
                "loaded_snapshot": {
                    "header": {
                        "reference_number": "OMI1112847",
                        "issued_at": "2026-10-25",
                        "supplier_name": "Sonoma Baking Company",
                        "subtotal": 14.96,
                        "tax_amount": 1.5,
                        "total": 16.46,
                    },
                    "lines": [
                        {
                            "description": "Croissant Plain",
                            "code": "30132",
                            "quantity_received": 1.0,
                            "unit_cost": 14.96,
                            "total_cost": 14.96,
                            "tax_amount": 1.5,
                        }
                    ],
                },
            },
        )
    )
    db_session.flush()
    text = _text(client.get(url, headers=admin_headers).content)
    assert "OMI1112847" in text and "* Croissant Plain" in text and "$16.46" in text
    assert "The invoice is no longer in Loaded" in text
    # The activity page's request lands on the same copy.
    r = client.get(
        f"/api/invoice-fixes/file?venue_id={venue.id}&invoice_id=inv-9&or_norm_copy=true",
        headers=admin_headers,
    )
    assert r.status_code == 200 and "no longer in Loaded" in _text(r.content)
