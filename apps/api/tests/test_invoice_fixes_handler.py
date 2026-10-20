"""The Receive Invoice editor's server surface and the receive it drives.

Mostly ``do_receive`` against a scripted Loaded fake — no network, no DB —
asserting the exact LoadedHub request sequence each receive produces; plus
the PO-number resolver, the /draft metadata heal, the delete applier, the
supplier endpoints and the heal-review loop guard.

Elsewhere: the PO reconciliation projection in test_invoice_po_reference.py,
the unit rules and the create-unit seam in test_invoice_units.py.
"""

import uuid

import pytest

from app.routers import invoice_fixes as IF
from app.services import received_invoice as RI
from app.services.received_invoice import resolve_po_id


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


def _receive_request(**over):
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
    return RI.ReceiveRequest(**base)


@pytest.fixture()
def req():
    """Builds the ReceiveRequest every receive test sends (inv-1 at v1, no PO,
    no edits) — pass only what the test changes."""
    return _receive_request


class TestDeleteInvoice:
    def test_deletes_the_draft(self):
        # Verified live in the Loaded test env: DELETE .../invoices/{id} -> 204.
        lh = FakeLoaded({}, {})
        msg = IF._apply_delete_invoice(lh, {"invoice_id": "inv-1"}, None)
        assert "deleted" in msg.lower()
        assert lh.writes == [("DELETE", "/1.0/stock/internal/invoices/inv-1", None)]


class TestReceive:
    """The Accept & Receive orchestration: link PO, apply line edits, receive,
    then PATCH changed variants."""

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
                    "linkedItemId": "item-1",  # resolved — passes the receive guard
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

    def test_credit_note_writes_loaded_sign_convention(self, req):
        # Parity with what Loaded actually stores for a credit note (verified
        # on 18 live credits): quantity NEGATIVE, unit cost POSITIVE, line
        # total and header totals NEGATIVE.
        lh = self._lh()
        RI.do_receive(
            lh,
            req(
                total=-252.75,
                subtotal=-219.78,
                tax_amount=-32.97,
                lines=[
                    {
                        "id": "ln-1",
                        "quantity_received": -4.95,
                        "unit_cost": 44.4,
                        "total_cost": -219.78,
                    }
                ],
            ),
        )
        put = [w for w in lh.writes if w[0] == "PUT"][0][2]
        ln = put["lines"][0]
        assert ln["quantityReceived"] == -4.95
        assert ln["unitCost"] == 44.4 and ln["unitCostExclTax"] == 44.4
        assert ln["totalCost"] == -219.78 and ln["totalCostExclTax"] == -219.78
        assert put["total"] == -252.75 and put["subtotal"] == -219.78

    def test_a_rounding_line_on_an_invoice_that_adds_up_receives(self, req):
        # Blue Seas SI655075: a $13.26 sauce, an "Invoice Rounding" -0.01, no
        # GST, $13.25 — the rounding line runs the other way, and that's fine.
        lh = self._lh()
        lh._invoices["inv-1"]["lines"].append(
            {
                "id": "ln-2",
                "code": None,
                "description": "Invoice Rounding",
                "unit": "Each",
                "linkedUnitId": "u-each",
                "linkedItemId": "item-2",
                "quantityReceived": 1,
                "unitCost": -0.01,
            }
        )
        out = RI.do_receive(
            lh,
            req(
                total=13.25,
                lines=[
                    {
                        "id": "ln-1",
                        "quantity_received": 1,
                        "unit_cost": 13.26,
                        "total_cost": 13.26,
                        "sale_tax_rate": 0.0,
                    },
                    {
                        "id": "ln-2",
                        "quantity_received": 1,
                        "unit_cost": -0.01,
                        "total_cost": -0.01,
                        "sale_tax_rate": 0.0,
                    },
                ],
            ),
        )
        assert out["received"] is True

    def test_mixed_sign_lines_are_refused(self, req):
        # Accepting the sign flip on only SOME lines of a credit produces a
        # document Loaded rejects opaquely — refuse it in our own words.
        from fastapi import HTTPException

        lh = self._lh()
        lh._invoices["inv-1"]["lines"].append(
            {
                "id": "ln-2",
                "code": "CUP",
                "unit": "Each",
                "linkedUnitId": "u-each",
                "linkedItemId": "item-2",
                "quantityReceived": 1,
                "unitCost": 5.0,
            }
        )
        with pytest.raises(HTTPException) as exc:
            RI.do_receive(
                lh,
                req(
                    total=-252.75,
                    lines=[
                        {
                            "id": "ln-1",
                            "quantity_received": -4.95,
                            "unit_cost": 44.4,
                            "total_cost": -219.78,
                        },
                        {
                            "id": "ln-2",
                            "quantity_received": 1,
                            "unit_cost": 5.0,
                            "total_cost": 5.0,
                        },
                    ],
                ),
            )
        assert exc.value.status_code == 400
        assert "run the other way" in exc.value.detail
        assert "don't make the total" in exc.value.detail

    def test_unlink_purchase_order_clears_link(self, req):
        # Accepted "unlink this order" (PO belongs to another supplier): the
        # fetched invoice carries the stale link; the flag must clear BOTH
        # fields on the PUT — a null po_id alone means "don't touch".
        lh = self._lh()
        lh._invoices["inv-1"]["linkedPurchaseOrderId"] = "po-oops"
        lh._invoices["inv-1"]["purchaseOrderNumber"] = "4041451-1"
        RI.do_receive(lh, req(unlink_purchase_order=True))
        put = [w for w in lh.writes if w[0] == "PUT"][0]
        assert put[2]["linkedPurchaseOrderId"] is None
        assert put[2]["purchaseOrderNumber"] is None

    def test_loaded_rejection_surfaces_as_502_with_detail(self, req):
        # Loaded's own validations (invoice-totals-mismatch, Allied
        # TLC-686713, 08 Aug 2026) must reach the card as a detailed error,
        # never an opaque 500 ("X Error 500" was all the user saw).
        from fastapi import HTTPException

        lh = self._lh()

        real_request = lh.request

        def rejecting(method, path, body=None):
            if method == "PUT":
                raise RuntimeError(
                    "Loaded PUT /invoices/inv-1 → 400: "
                    '{"type":"invoice-totals-mismatch"}'
                )
            return real_request(method, path, body)

        lh.request = rejecting
        with pytest.raises(HTTPException) as exc:
            RI.do_receive(lh, req())
        assert exc.value.status_code == 502
        assert "invoice-totals-mismatch" in str(exc.value.detail)

    def test_purchase_order_number_reference_written_through(self, req):
        # Split order accepted from the copy: the order-number REFERENCE
        # rides the header write-through (never the 1:1 link).
        lh = self._lh()
        RI.do_receive(lh, req(purchase_order_number="1520987"))
        put = [w for w in lh.writes if w[0] == "PUT"][0]
        assert put[2]["purchaseOrderNumber"] == "1520987"
        assert put[2]["linkedPurchaseOrderId"] is None

    def test_link_path_owns_the_number_when_linking(self, req):
        # A stale reference field from the card must never overwrite the
        # number the link path resolved.
        lh = self._lh()
        RI.do_receive(
            lh,
            req(linked_purchase_order_id="po-77", purchase_order_number="STALE"),
        )
        put = [w for w in lh.writes if w[0] == "PUT"][0]
        assert put[2]["linkedPurchaseOrderId"] == "po-77"
        assert put[2]["purchaseOrderNumber"] != "STALE"

    def test_split_notes_stamped_on_po_and_sibling(self, req):
        # Receiving a split invoice stamps cross-reference notes on the PO
        # and the sibling invoice (best-effort; verified live 08 Aug 2026).
        lh = self._lh()
        # More specific path FIRST — FakeLoaded matches by prefix, in order.
        lh._gets = {
            "/1.0/stock/internal/purchase-orders/po-split": {
                "id": "po-split",
                "orderNumber": "1520987",
                "notes": "",
            },
            **lh._gets,
        }
        lh._invoices["sib-9"] = {"id": "sib-9", "notes": ""}
        RI.do_receive(
            lh,
            req(
                split_po_id="po-split",
                split_sibling_invoice_id="sib-9",
                reference_number="IN-2ND",
            ),
        )
        po_put = [w for w in lh.writes if w[1].endswith("/po-split")][0]
        assert "Split order: also invoiced on IN-2ND" in po_put[2]["notes"]
        sib_put = [w for w in lh.writes if w[1].endswith("/invoices/sib-9")][0]
        assert "Split order: order 1520987 also covers IN-2ND" in sib_put[2]["notes"]

    def test_receive_survives_note_failures(self, req):
        # A note-write failure must never fail the receive itself.
        lh = self._lh()
        lh._gets = {
            "/1.0/stock/internal/purchase-orders/po-split": {
                "id": "po-split",
                "orderNumber": "1520987",
                "notes": "",
            },
            **lh._gets,
        }
        out = RI.do_receive(
            lh,
            req(
                split_po_id="po-split",
                split_sibling_invoice_id="sib-missing",  # KeyError in the fake
            ),
        )
        assert out["ok"] is True
        by_target = {n["target"]: n for n in out["split_notes"]}
        assert by_target["purchase_order"]["ok"] is True
        assert by_target["sibling_invoice"]["ok"] is False

    def test_unlink_then_relink_links_the_new_po(self, req):
        # Unlink + a new PO in the same receive: the unlink clears the stale
        # link first, then the chosen order links — the user unlinked the
        # wrong supplier's PO and picked the right one before receiving.
        lh = self._lh()
        lh._invoices["inv-1"]["linkedPurchaseOrderId"] = "po-oops"
        RI.do_receive(
            lh,
            req(unlink_purchase_order=True, linked_purchase_order_id="po-77"),
        )
        put = [w for w in lh.writes if w[0] == "PUT"][0]
        assert put[2]["linkedPurchaseOrderId"] == "po-77"

    def test_receive_with_no_lines_rejected(self, req):
        # An empty draft (a letter/statement uploaded as an invoice — live:
        # the Air Liquide surcharge notice) must never be received; deleting
        # the draft is the action. Also covers an all-lines-struck receive.
        from fastapi import HTTPException

        inv = self._inv()
        inv["lines"] = []
        lh = FakeLoaded(
            {"/1.0/stock/internal/purchase-orders": PO_LIST}, {"inv-1": inv}
        )
        try:
            RI.do_receive(lh, req())
            raise AssertionError("expected 400")
        except HTTPException as e:
            assert e.status_code == 400
            assert "nothing to receive" in str(e.detail)
        assert lh.writes == []

    def test_receive_without_supplier_rejected(self, req):
        # Loaded's server 500s (opaque internal-error — seen live on Sawmill
        # 201458) when receiving an invoice with NO linked supplier. We must
        # 400 with a clear message before anything is written.
        from fastapi import HTTPException

        inv = self._inv()
        inv["linkedSupplierId"] = None
        lh = FakeLoaded(
            {"/1.0/stock/internal/purchase-orders": PO_LIST}, {"inv-1": inv}
        )
        try:
            RI.do_receive(lh, req())
            raise AssertionError("expected 400")
        except HTTPException as e:
            assert e.status_code == 400
            assert "supplier" in str(e.detail)
        assert lh.writes == []

        # A supplier picked in the editor (body.linked_supplier_id) satisfies
        # the guard even though Loaded's own field is empty.
        lh2 = FakeLoaded(
            {"/1.0/stock/internal/purchase-orders": PO_LIST},
            {"inv-1": dict(self._inv(), linkedSupplierId=None)},
        )
        RI.do_receive(lh2, req(linked_supplier_id="sup-1"))
        assert [w for w in lh2.writes if w[0] == "PUT"]

    def test_links_po_edits_line_receives_and_patches_variant(self, req):
        lh = self._lh()
        body = req(
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
        out = RI.do_receive(lh, body)
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

    def test_editable_header_fields_persist(self, req):
        """Every header edit lands on the PUT. Folded in: the accepted copy
        totals (a feed left the header $0) and the accepted "Invoice number
        X → Y (per the invoice copy)" — the copy-number suggestion's write
        path is referenceNumber."""
        lh = self._lh()
        RI.do_receive(
            lh,
            req(
                reference_number="INV-EDITED",
                issued_at="2026-07-30",
                due_at="2026-08-20",
                total=1189.27,
                subtotal=1034.15,
                tax_amount=155.12,
                linked_supplier_id="sup-2",
                unit_cost_includes_tax=True,
                notes="Short delivery on line 2",
            ),
        )
        body = [w for w in lh.writes if w[0] == "PUT"][0][2]
        assert body["referenceNumber"] == "INV-EDITED"
        assert body["issuedAt"] == "2026-07-30"
        assert body["dueAt"] == "2026-08-20"
        assert body["total"] == 1189.27
        assert body["subtotal"] == 1034.15
        assert body["taxAmount"] == 155.12
        assert body["linkedSupplierId"] == "sup-2"
        assert body["unitCostIncludesTax"] is True
        assert body["notes"] == "Short delivery on line 2"

    def test_received_date_from_header_is_not_overwritten(self, req):
        lh = self._lh()
        RI.do_receive(lh, req(received_at="2026-07-31T19:57:00Z"))
        body = [w for w in lh.writes if w[0] == "PUT"][0][2]
        assert body["isReceived"] is True
        assert body["receivedAt"] == "2026-07-31T19:57:00Z"

    def test_unsupplied_header_fields_leave_the_invoice_untouched(self, req):
        lh = self._lh()
        RI.do_receive(lh, req())  # no header edits
        body = [w for w in lh.writes if w[0] == "PUT"][0][2]
        # linkedSupplierId came from the fetched invoice, not the request.
        assert body["linkedSupplierId"] == "sup-1"
        assert "notes" not in body

    def test_receive_false_leaves_draft(self, req):
        lh = self._lh()
        out = RI.do_receive(lh, req(receive=False))
        assert out["received"] is False
        put = [w for w in lh.writes if w[0] == "PUT"][0]
        assert "isReceived" not in put[2]

    def test_unknown_po_number_raises(self, req):
        lh = self._lh()
        try:
            RI.do_receive(lh, req(po_number="9999"))
            assert False, "expected failure"
        except Exception as e:
            assert "not found" in str(e)
        assert [w for w in lh.writes if w[0] == "PUT"] == []

    def test_receive_blocked_by_a_new_stock_item(self, req):
        # A line with no linkedItemId (NEW stock item) must block receive so it
        # can't be created silently — nothing is PUT.
        inv = self._inv()
        inv["lines"][0].pop("linkedItemId")
        lh = FakeLoaded(
            {"/1.0/stock/internal/purchase-orders": PO_LIST}, {"inv-1": inv}
        )
        try:
            RI.do_receive(lh, req())
            assert False, "expected the receive guard to block"
        except Exception as e:
            assert "must be created in Loaded before receiving" in str(e)
        assert [w for w in lh.writes if w[0] == "PUT"] == []

    def test_receive_blocked_by_a_new_unit(self, req):
        inv = self._inv()
        inv["lines"][0].pop("linkedUnitId")
        lh = FakeLoaded(
            {"/1.0/stock/internal/purchase-orders": PO_LIST}, {"inv-1": inv}
        )
        try:
            RI.do_receive(lh, req())
            assert False, "expected the receive guard to block"
        except Exception as e:
            assert "must be created in Loaded before receiving" in str(e)
        assert [w for w in lh.writes if w[0] == "PUT"] == []

    def test_receive_blocked_by_a_brand_loaded_does_not_know(self, req):
        """A brand named on the line with no Loaded record still blocks —
        Loaded won't receive a line naming a brand it doesn't have."""
        inv = self._inv()
        inv["lines"][0]["brand"] = "FARMBYNATURE"
        lh = FakeLoaded(
            {"/1.0/stock/internal/purchase-orders": PO_LIST}, {"inv-1": inv}
        )
        try:
            RI.do_receive(lh, req())
            assert False, "expected the receive guard to block"
        except Exception as e:
            assert "brand 'FARMBYNATURE'" in str(e)
        assert [w for w in lh.writes if w[0] == "PUT"] == []

    def test_a_created_brand_travels_to_loaded_and_unblocks_the_receive(self, req):
        """Bidfood 109944512, 15 Aug 2026 — the regression this exists for.

        The user accepted 'create this brand', Loaded created it, and the
        working document held the new id. But the id had no way onto Loaded's
        invoice line, so the guard read the untouched line (brand name, no
        linkedBrandId) and refused — every time, forever, on an invoice whose
        brand had been created minutes earlier.
        """
        inv = self._inv()
        inv["lines"][0]["brand"] = "FARMBYNATURE"  # Loaded knows the NAME only
        lh = FakeLoaded(
            {"/1.0/stock/internal/purchase-orders": PO_LIST}, {"inv-1": inv}
        )
        RI.do_receive(
            lh,
            req(lines=[{"id": "ln-1", "linked_brand_id": "brand-new-1"}]),
        )
        put = next(w for w in lh.writes if w[0] == "PUT")
        assert put[2]["lines"][0]["linkedBrandId"] == "brand-new-1"

    def _received_brand(self, edit):
        inv = self._inv()
        # Loaded's scan read the supplier's name as a brand.
        inv["lines"][0].update({"brand": "BIDFOOD", "linkedBrandId": None})
        lh = FakeLoaded(
            {
                "/1.0/stock/internal/purchase-orders": PO_LIST,
                "/1.0/stock/internal/items/": {"suppliers": []},
            },
            {"inv-1": inv},
        )
        RI.do_receive(lh, _receive_request(lines=[{"id": "ln-1", **edit}]))
        return next(w for w in lh.writes if w[0] == "PUT")[2]["lines"][0]

    def test_a_brand_cleared_in_the_card_clears_on_loadeds_line(self):
        """Only the record id used to travel, so a misread brand could never be
        cleared — the line kept its name with no record and the guard refused
        the receive for good."""
        line = self._received_brand(
            {"brand": None, "linked_brand_id": None, "brand_set": True}
        )
        assert (line["brand"], line["linkedBrandId"]) == (None, None)

    def test_a_brand_picked_in_the_card_replaces_the_name_too(self):
        line = self._received_brand(
            {"brand": "Bundaberg", "linked_brand_id": "b-1", "brand_set": True}
        )
        assert (line["brand"], line["linkedBrandId"]) == ("Bundaberg", "b-1")

    def test_an_untouched_misread_brand_still_blocks(self):
        with pytest.raises(Exception, match="brand 'BIDFOOD'"):
            self._received_brand({})

    def test_an_unbranded_line_is_unaffected(self, req):
        lh = self._lh()
        RI.do_receive(lh, req())
        put = next(w for w in lh.writes if w[0] == "PUT")
        assert "linkedBrandId" not in put[2]["lines"][0]

    def test_an_added_line_carries_an_id_loaded_will_accept(self, req):
        """Red and White Cellars INV562277, 17 Aug 2026 — freight went missing.

        The append used to drop the editor's temp id, on the belief that
        "Loaded assigns the real one". It does not: a line with no id is
        SILENTLY ignored by the invoice PUT — no error, no line, nothing in the
        response to notice. Verified live against the same payload, which
        persisted with a client-generated GUID and vanished without one. That
        invoice went to Loaded $12 light with its own freight line deleted and
        nothing in its place.

        Folded in: an added line appends after the original with its fields
        mapped, and carries the tax rate the engine stamped on the add-line
        suggestion.
        """
        lh = self._lh()
        RI.do_receive(
            lh,
            req(
                lines=[
                    {
                        "id": "new-tmp-1",  # the editor's temp id
                        "code": "OILG3",
                        "description": "OIL GRAPESEED",
                        "linked_item_id": "item-oil",
                        "linked_unit_id": "u-3l",
                        "unit": "3 L",
                        "unit_ratio": 3000.0,
                        "quantity_received": 1,
                        "unit_cost": 49.95,
                        "total_cost": 49.95,
                        "sale_tax_rate": 0.15,
                    },
                    {
                        # named only by its item: no code, no description
                        "id": "new-tmp-2",
                        "linked_item_id": "item-salt",
                        "linked_unit_id": "u-3l",
                        "unit": "3 L",
                        "unit_ratio": 3000.0,
                        "quantity_received": 2,
                        "unit_cost": 3.5,
                        "total_cost": 7.0,
                    },
                ]
            ),
        )
        put = next(w for w in lh.writes if w[0] == "PUT")
        assert len(put[2]["lines"]) == 3  # original + the two added
        item_only = put[2]["lines"][2]
        assert item_only["linkedItemId"] == "item-salt"
        assert "code" not in item_only and "description" not in item_only
        uuid.UUID(str(item_only["id"]))
        added = put[2]["lines"][1]
        assert added["code"] == "OILG3"
        assert added["linkedItemId"] == "item-oil"
        assert added["quantityReceived"] == 1
        assert added["unitCost"] == 49.95
        assert added["unitCostExclTax"] == 49.95
        assert added["saleTaxRate"] == 0.15
        assert added.get("id"), "Loaded drops a line with no id, without saying so"
        # The id is OURS to generate: Loaded does not assign one. An assertion
        # here used to say the opposite, which is how the belief survived
        # (INV562277, 17 Aug 2026). A real GUID, not the editor's temp id —
        # Loaded's own line ids are uuids and the temp one means nothing to it.
        uuid.UUID(str(added["id"]))
        assert added["id"] != "new-tmp-1"

    def test_an_empty_added_row_is_still_not_appended(self, req):
        """A row with no code/item must not create a rubbish line — and the id
        is generated AFTER that "names a real item" guard, so giving every
        appended line an id must not start creating rubbish."""
        lh = self._lh()
        RI.do_receive(lh, req(lines=[{"id": "rep-9", "quantity_received": 1}]))
        put = next(w for w in lh.writes if w[0] == "PUT")
        assert len(put[2]["lines"]) == 1  # the original line only

    def test_receive_not_blocked_when_only_saving(self, req):
        # The guard is receive-only: a save (receive=False) of an unresolved line
        # still PUTs (the editor persists work-in-progress before resolving).
        inv = self._inv()
        inv["lines"][0].pop("linkedItemId")
        lh = FakeLoaded(
            {"/1.0/stock/internal/purchase-orders": PO_LIST}, {"inv-1": inv}
        )
        RI.do_receive(lh, req(receive=False))
        assert [w for w in lh.writes if w[0] == "PUT"]


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
            # one resolved line — an EMPTY draft is (correctly) unreceivable
            "lines": [
                {
                    "id": "ln-1",
                    "code": "NAP",
                    "unit": "Each",
                    "linkedUnitId": "u-each",
                    "linkedItemId": "item-1",
                }
            ],
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

    def test_supplier_number_is_replaced_with_the_linked_po(self, req):
        # Also the 1:1 guard's open case (TestConditionalPoWriteback): a PO
        # linked to no invoice is linked, and no skip is reported.
        lh = self._lh()
        out = RI.do_receive(lh, req(linked_purchase_order_id="po-1"))
        body = [w for w in lh.writes if w[0] == "PUT"][0][2]
        assert body["linkedPurchaseOrderId"] == "po-1"
        assert body["purchaseOrderNumber"] == "1520387"
        assert out["linked_purchase_order"] == "1520387"
        assert out["po_link_skipped"] is False

    def test_explicit_number_still_wins(self, req):
        lh = self._lh()
        RI.do_receive(lh, req(linked_purchase_order_id="po-1", po_number="1520999"))
        body = [w for w in lh.writes if w[0] == "PUT"][0][2]
        assert body["purchaseOrderNumber"] == "1520999"

    def test_lookup_failure_does_not_block_the_receive(self, req):
        # A PO that can't be read must still link and receive — only the
        # display number is best-effort.
        lh = FakeLoaded(
            {"/1.0/stock/internal/purchase-orders": PO_LIST},
            {"inv-1": self._inv()},
        )
        out = RI.do_receive(lh, req(linked_purchase_order_id="po-1"))
        body = [w for w in lh.writes if w[0] == "PUT"][0][2]
        assert out["ok"] and body["linkedPurchaseOrderId"] == "po-1"
        assert body["purchaseOrderNumber"] == "12195941-1"  # untouched


class TestRefreshMetadata:
    """/draft heals read-only metadata on an existing draft — so a draft made
    before a shaper field existed (e.g. sale_tax_rate) is corrected on re-open —
    WITHOUT clobbering the user's edits or the review's fields."""

    def test_heals_missing_tax_rate_keeps_edits(self, monkeypatch):
        import sqlalchemy.orm.attributes as attrs
        from types import SimpleNamespace

        monkeypatch.setattr(attrs, "flag_modified", lambda *a, **k: None)
        stale = {
            "unit_cost_includes_tax": None,  # missing new header field
            "total": 999.0,  # a user-edited header value — must be kept
            "lines": [
                {
                    "id": "ln-1",
                    "quantity_received": 5,  # user edit — must be kept
                    "unit_cost": 2.0,
                    "reference_cost": 7.0,  # review field — must be kept
                    # note: no sale_tax_rate (the bug)
                }
            ],
        }
        doc = SimpleNamespace(data=stale)
        detail = {
            "subtotal": 100,
            "taxAmount": 15,
            "discountAmount": None,
            "unitCostIncludesTax": True,
            "lines": [
                {
                    "id": "ln-1",
                    "brand": "MEADOWFRESH",
                    "taxAmount": 1.5,
                    "saleTaxRate": 0.15,
                    "quantityReceived": 99,  # live value must NOT overwrite the edit
                    "unitCost": 99,
                }
            ],
        }
        IF._refresh_metadata(doc, detail)
        ln = doc.data["lines"][0]
        assert ln["sale_tax_rate"] == 0.15  # healed
        assert ln["brand"] == "MEADOWFRESH"  # metadata refreshed
        assert ln["quantity_received"] == 5  # user edit preserved
        assert ln["unit_cost"] == 2.0
        assert ln["reference_cost"] == 7.0  # review field preserved
        assert doc.data["total"] == 999.0  # editable header preserved
        assert doc.data["unit_cost_includes_tax"] is True  # metadata refreshed


class TestTheRouterAttachesThePoReference:
    """``IF._attach_po_reference`` is the router's thin delegate to
    services/invoice_po_reference: fetch the linked order, project it onto the
    draft. The projection's rules (pairing by stock item, substitutes,
    "ordered, not delivered", split orders) are pinned in
    test_invoice_po_reference.py; this checks the delegate reaches them."""

    def test_the_linked_order_is_fetched_and_projected(self):
        po = {
            "createdAt": "2026-07-20",
            "lines": [
                {
                    "itemCode": "BROC",
                    "itemName": "BROCCOLINI",
                    "unitName": "Each",
                    "quantityOrdered": 4,
                    "unitCost": 2.5,
                    "itemId": "item-broc",
                },
            ],
        }
        data = {
            "linked_purchase_order_id": "po-1",
            "lines": [
                {
                    "id": "ln-b",
                    "code": " broc ",
                    "linked_item_id": "item-broc",
                    "unit_cost": 2.5,
                },
            ],
        }
        lh = FakeLoaded({"/1.0/stock/internal/purchase-orders/po-1": po}, {})
        IF._attach_po_reference(data, lh)
        broc = data["lines"][0]
        assert broc["on_order"] is True
        assert broc["quantity_ordered"] == 4
        assert broc["reference_cost"] == 2.5
        assert data["order_date"] == "2026-07-20"
        assert data["ordered_not_received"] == []


class TestResolvePoId:
    """Resolving a PO NUMBER to an id — the open-PO list first, then the
    received-invoice sibling fallback for older/received POs."""

    def test_open_list_unique_match(self):
        lh = FakeLoaded(
            gets={
                "/1.0/stock/internal/purchase-orders?": [
                    {"id": "po-1", "orderNumber": "1520987", "linkedInvoiceId": None}
                ]
            },
            invoices={},
        )
        r = resolve_po_id(lh, "PO#1520987")
        assert r == {
            "id": "po-1",
            "order_number": "1520987",
            "linked_invoice_id": None,
            "supplier_id": None,
        }

    def test_open_list_ambiguous_returns_none(self):
        lh = FakeLoaded(
            gets={
                "/1.0/stock/internal/purchase-orders?": [
                    {"id": "po-1", "orderNumber": "1520987"},
                    {"id": "po-2", "orderNumber": "1520987"},
                ]
            },
            invoices={},
        )
        assert resolve_po_id(lh, "1520987") is None

    def test_supplier_preference_disambiguates(self):
        lh = FakeLoaded(
            gets={
                "/1.0/stock/internal/purchase-orders?": [
                    {"id": "po-1", "orderNumber": "1520987", "supplierId": "sup-a"},
                    {"id": "po-2", "orderNumber": "1520987", "supplierId": "sup-b"},
                ]
            },
            invoices={},
        )
        r = resolve_po_id(lh, "1520987", supplier_id="sup-b")
        assert r["id"] == "po-2"

    def test_sibling_fallback_resolves_a_received_po(self):
        # Not in the open list; found via a received invoice carrying the number.
        lh = FakeLoaded(
            gets={
                "/1.0/stock/internal/purchase-orders/po-x": {
                    "orderNumber": "1520272",
                    "linkedInvoiceId": "inv-recv",
                },
                "/1.0/stock/internal/purchase-orders?": [],  # open list: no match
                "/1.0/stock/internal/stock-received": [
                    {"id": "inv-recv", "purchaseOrderNumber": "1520272"}
                ],
            },
            invoices={"inv-recv": {"linkedPurchaseOrderId": "po-x"}},
        )
        r = resolve_po_id(lh, "1520272")
        assert r == {
            "id": "po-x",
            "order_number": "1520272",
            "linked_invoice_id": "inv-recv",
            "supplier_id": None,
        }

    def test_unresolved_returns_none(self):
        lh = FakeLoaded(
            gets={
                "/1.0/stock/internal/purchase-orders?": [],
                "/1.0/stock/internal/stock-received": [],
            },
            invoices={},
        )
        assert resolve_po_id(lh, "9999") is None

    def test_drafts_pass_reaches_a_po_claimed_by_a_draft(self):
        # Live-verified gap (Tamar 1521145): a PO linked to a DRAFT invoice is
        # in NEITHER the open list nor the received feed — only the drafts
        # list, whose rows carry the number AND the PO id.
        lh = FakeLoaded(
            gets={
                "/1.0/stock/internal/purchase-orders/po-t": {
                    "orderNumber": "1521145",
                    "linkedInvoiceId": "inv-draft",
                },
                "/1.0/stock/internal/purchase-orders?": [],
                "/1.0/stock/internal/invoices": [
                    {
                        "id": "inv-draft",
                        "purchaseOrderNumber": "1521145",
                        "linkedPurchaseOrderId": "po-t",
                        "isReceived": False,
                    }
                ],
                "/1.0/stock/internal/stock-received": [],
            },
            invoices={},
        )
        r = resolve_po_id(lh, "po#1521145")
        assert r == {
            "id": "po-t",
            "order_number": "1521145",
            "linked_invoice_id": "inv-draft",
            "supplier_id": None,
        }

    def test_drafts_pass_ignores_other_numbers(self):
        # A draft holding a DIFFERENT order never satisfies the lookup.
        lh = FakeLoaded(
            gets={
                "/1.0/stock/internal/purchase-orders?": [],
                "/1.0/stock/internal/invoices": [
                    {
                        "id": "inv-draft",
                        "purchaseOrderNumber": "1521145",
                        "linkedPurchaseOrderId": "po-t",
                    }
                ],
                "/1.0/stock/internal/stock-received": [],
            },
            invoices={},
        )
        assert resolve_po_id(lh, "9999999") is None


class TestConditionalPoWriteback:
    """do_receive writes the PO link back to Loaded only when the PO isn't
    already linked to a DIFFERENT invoice (Loaded is 1:1 — don't steal a split
    order's sibling link). The unlinked-PO case is pinned in
    TestLinkedPoNumberIsWrittenForDisplay."""

    def _lh(self, linked_invoice_id):
        inv = {
            "id": "inv-1",
            "linkedSupplierId": "sup-1",
            "linkedPurchaseOrderId": None,
            "purchaseOrderNumber": None,
            # one resolved line — an EMPTY draft is (correctly) unreceivable
            "lines": [
                {
                    "id": "ln-1",
                    "code": "NAP",
                    "unit": "Each",
                    "linkedUnitId": "u-each",
                    "linkedItemId": "item-1",
                }
            ],
        }
        return FakeLoaded(
            gets={
                "/1.0/stock/internal/purchase-orders/po-1": {
                    "id": "po-1",
                    "orderNumber": "1520272",
                    "linkedInvoiceId": linked_invoice_id,
                }
            },
            invoices={"inv-1": inv},
        )

    def test_skips_writeback_when_po_linked_to_another_invoice(self, req):
        lh = self._lh("inv-OTHER")
        out = RI.do_receive(lh, req(linked_purchase_order_id="po-1"))
        body = [w for w in lh.writes if w[0] == "PUT"][0][2]
        assert body.get("linkedPurchaseOrderId") is None  # link NOT written
        assert out["po_link_skipped"] is True
        assert out["linked_purchase_order"] is None
        assert body["isReceived"] is True  # still received

    def test_links_when_po_already_points_at_this_invoice(self, req):
        lh = self._lh("inv-1")
        out = RI.do_receive(lh, req(linked_purchase_order_id="po-1"))
        body = [w for w in lh.writes if w[0] == "PUT"][0][2]
        assert body["linkedPurchaseOrderId"] == "po-1"
        assert out["po_link_skipped"] is False


class TestListSuppliersEndpoint:
    """GET /invoice-fixes/suppliers filters deleted suppliers.

    Loaded renamed the supplier delete marker datestampDeleted -> removedAt
    (Aug 2026); the old filter silently passed deleted suppliers through.
    Both vintages must be honoured.
    """

    def _call(self, client, headers, monkeypatch, rows):
        def fake_execute(component, action, fields, venue_id, db, config_db):
            assert (component, action) == ("purchase_order_editor", "get_suppliers")
            return {"data": rows}

        monkeypatch.setattr(
            "app.services.component_api.execute_component_action", fake_execute
        )
        r = client.get("/api/invoice-fixes/suppliers?venue_id=v-1", headers=headers)
        assert r.status_code == 200
        return r.json()["suppliers"]

    def test_filters_both_delete_marker_vintages(
        self, client, admin_headers, monkeypatch
    ):
        rows = [
            {"id": "s-1", "name": "Active"},
            {"id": "s-2", "name": "New-style deleted", "removedAt": "2026-08-01"},
            {
                "id": "s-3",
                "name": "Old-style deleted",
                "datestampDeleted": "2025-01-01",
            },
            {"id": "s-4", "name": "Alive", "removedAt": None, "datestampDeleted": None},
        ]
        out = self._call(client, admin_headers, monkeypatch, rows)
        assert [s["name"] for s in out] == ["Active", "Alive"]


class TestCreateSupplierEndpoint:
    """POST /invoice-fixes/create-supplier: resolve-first against the venue's
    list (normalized containment — never a duplicate record), else the one
    Loaded write (POST /suppliers, verified live 08 Aug 2026: 201 + the
    created object). The invoice links the supplier LOCALLY in the editor."""

    def _fake(self, monkeypatch, rows, created=None):
        calls = []

        class FakeLh:
            def get(self, path):
                assert path == "/1.0/stock/internal/suppliers"
                return rows

            def request(self, method, path, body=None):
                calls.append((method, path, body))
                return created if created is not None else {}

        monkeypatch.setattr(IF, "_Loaded", lambda db, cdb, vid: FakeLh())
        return calls

    def test_resolves_existing_record_without_writing(
        self, client, admin_headers, monkeypatch
    ):
        # "EuroVintage Ltd" on the copy, "Eurovintage" in Loaded: containment
        # resolves the existing record — no write, no duplicate supplier.
        calls = self._fake(
            monkeypatch, [{"id": "sup-e", "name": "Eurovintage", "removedAt": None}]
        )
        r = client.post(
            "/api/invoice-fixes/create-supplier",
            headers=admin_headers,
            json={"venue_id": "v-1", "name": "EuroVintage Ltd"},
        )
        assert r.status_code == 200
        out = r.json()
        assert out["created"] is False
        assert out["supplier_id"] == "sup-e"
        assert out["supplier_name"] == "Eurovintage"
        assert calls == []

    def test_creates_when_no_record_covers_it(self, client, admin_headers, monkeypatch):
        calls = self._fake(
            monkeypatch,
            [{"id": "sup-1", "name": "Bidfood"}],
            created={"id": "sup-new", "name": "EuroVintage Ltd"},
        )
        r = client.post(
            "/api/invoice-fixes/create-supplier",
            headers=admin_headers,
            json={"venue_id": "v-1", "name": "EuroVintage Ltd"},
        )
        assert r.status_code == 200
        out = r.json()
        assert out["created"] is True
        assert out["supplier_id"] == "sup-new"
        assert calls == [
            (
                "POST",
                "/1.0/stock/internal/suppliers",
                {"name": "EuroVintage Ltd"},
            )
        ]

    def test_removed_record_does_not_resolve(self, client, admin_headers, monkeypatch):
        # A soft-deleted record must not swallow the create.
        self._fake(
            monkeypatch,
            [{"id": "sup-x", "name": "EuroVintage Ltd", "removedAt": "2025-01-01"}],
            created={"id": "sup-new2", "name": "EuroVintage Ltd"},
        )
        r = client.post(
            "/api/invoice-fixes/create-supplier",
            headers=admin_headers,
            json={"venue_id": "v-1", "name": "EuroVintage Ltd"},
        )
        assert r.status_code == 200
        assert r.json()["created"] is True

    def test_502_when_loaded_returns_no_id(self, client, admin_headers, monkeypatch):
        self._fake(monkeypatch, [], created={})
        r = client.post(
            "/api/invoice-fixes/create-supplier",
            headers=admin_headers,
            json={"venue_id": "v-1", "name": "New Supplier"},
        )
        assert r.status_code == 502


class TestStruckLines:
    """An accepted remove suggestion rides the receive: the struck line is
    soft-deleted in Loaded (deletedAt). (The added line's tax rate is pinned in
    TestReceive::test_an_added_line_carries_an_id_loaded_will_accept.)"""

    def _lh(self):
        return FakeLoaded(
            {"/1.0/stock/internal/purchase-orders": PO_LIST},
            {
                "inv-1": {
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
                            "linkedItemId": "item-1",
                            "quantityReceived": 1,
                            "unitCost": 3.99,
                        },
                    ],
                }
            },
        )

    def test_struck_line_is_soft_deleted(self, req):
        lh = self._lh()
        out = RI.do_receive(
            lh, req(lines=[{"id": "ln-1", "struck": True}], receive=False)
        )
        assert out["ok"]
        body = [w for w in lh.writes if w[0] == "PUT"][0][2]
        assert body["lines"][0]["deletedAt"]


class TestReshapeCarryOver:
    """A rebuild of the draft from Loaded keeps what a person did on it: a
    struck line and a line added by hand come back by replay of the edit
    record (invoice_edits) — for a card from before that record, read once
    from its lines."""

    def test_struck_and_added_lines_survive_reshape(self):
        old = {
            "lines": [
                {"id": "ln-1", "description": "OLD", "struck": True},
                {
                    "id": "new-1754424000000",
                    "description": "LOCAL ADD",
                    "sale_tax_rate": 0.15,
                },
            ],
            "actioned_suggestions": [{"key": "remove:ln-1", "summary": "removed"}],
        }
        fresh = RI.build_received_invoice_data(
            {
                "id": "inv-1",
                "referenceNumber": "R-1",
                "linkedSupplierId": "sup-1",
                "lines": [{"id": "ln-1", "description": "FRESH", "linkedItemId": None}],
            }
        )
        from app.services.invoice_edits import rebuild_from_decisions

        rebuild_from_decisions(old, fresh)
        by_id = {ln.get("id"): ln for ln in fresh["lines"]}
        assert by_id["ln-1"].get("struck") is True
        assert by_id["ln-1"]["description"] == "FRESH"  # Loaded's, not copied
        assert "new-1754424000000" in by_id
        assert by_id["new-1754424000000"]["description"] == "LOCAL ADD"
        assert fresh["actioned_suggestions"] == [
            {"key": "remove:ln-1", "summary": "removed"}
        ]
        # The edits are on the card now, so the next rebuild replays them.
        assert {e.get("field") or "added" for e in fresh["edits"]} == {
            "struck",
            "added",
        }


class TestReceiveTimeLinking:
    """Item links are LOCAL draft edits: the receive PUT carries linkedItemId
    and do_receive registers the missing supplier variant at receive time —
    nothing touches Loaded when the user merely accepts a match suggestion."""

    def _inv(self):
        return {
            "id": "inv-1",
            "linkedSupplierId": "sup-1",
            "linkedPurchaseOrderId": None,
            "purchaseOrderNumber": None,
            "lines": [
                {
                    "id": "ln-1",
                    "code": "FEE94870",
                    "description": "FEE BROTHERS CHERRY BITTERS",
                    "unit": "EA",
                    "linkedItemId": None,
                    "linkedUnitId": None,
                    "unitCostExclTax": 22.61,
                }
            ],
        }

    def test_newly_linked_line_registers_variant_and_links(self, req):
        item = {
            "id": "item-2",
            "name": "FEE BROTHERS CHERRY BITTERS",
            "orderingUnitId": "u-150",
            "suppliers": [],
        }
        lh = FakeLoaded(
            {
                "/1.0/stock/internal/purchase-orders": PO_LIST,
                "/1.0/stock/internal/items/item-2": item,
            },
            {"inv-1": self._inv()},
        )
        out = RI.do_receive(
            lh,
            req(
                lines=[
                    {
                        "id": "ln-1",
                        "linked_item_id": "item-2",
                        "linked_unit_id": "u-150",
                        "unit": "150 mL",
                        "unit_ratio": 0.15,
                        "quantity_received": 1,
                        "unit_cost": 22.61,
                    }
                ]
            ),
        )
        assert out["ok"]
        # variant registered on the item BEFORE the invoice PUT
        item_put = [w for w in lh.writes if w[0] == "PUT" and "/items/item-2" in w[1]][
            0
        ]
        variant = item_put[2]["suppliers"][0]
        assert variant["supplierId"] == "sup-1"
        assert variant["stockCode"] == "FEE94870"
        assert variant["unitId"] == "u-150"
        assert variant["unitCost"] == 22.61
        # invoice PUT carries the link + unit on the line
        inv_put = [w for w in lh.writes if w[0] == "PUT" and "/invoices/inv-1" in w[1]][
            0
        ]
        ln = inv_put[2]["lines"][0]
        assert ln["linkedItemId"] == "item-2"
        assert ln["linkedUnitId"] == "u-150"
        assert lh.writes.index(item_put) < lh.writes.index(inv_put)

    def test_already_linked_line_registers_nothing(self, req):
        inv = self._inv()
        inv["lines"][0]["linkedItemId"] = "item-2"
        inv["lines"][0]["linkedUnitId"] = "u-150"
        lh = FakeLoaded(
            {"/1.0/stock/internal/purchase-orders": PO_LIST}, {"inv-1": inv}
        )
        RI.do_receive(
            lh,
            req(
                lines=[
                    {"id": "ln-1", "linked_item_id": "item-2", "quantity_received": 1}
                ]
            ),
        )
        assert not [w for w in lh.writes if "/items/" in w[1]]

    def test_registration_failure_never_blocks_receive(self, req):
        # No canned item route -> FakeLoaded raises inside registration; the
        # receive itself must still succeed (link still lands via the PUT).
        lh = FakeLoaded(
            {"/1.0/stock/internal/purchase-orders": PO_LIST}, {"inv-1": self._inv()}
        )
        out = RI.do_receive(
            lh,
            req(
                lines=[
                    {
                        "id": "ln-1",
                        "linked_item_id": "item-2",
                        "linked_unit_id": "u-150",
                        "quantity_received": 1,
                    }
                ]
            ),
        )
        assert out["ok"]
        inv_put = [w for w in lh.writes if w[0] == "PUT" and "/invoices/inv-1" in w[1]][
            0
        ]
        assert inv_put[2]["lines"][0]["linkedItemId"] == "item-2"


def test_heal_review_never_triggers_autostudy(monkeypatch):
    """A study's own heal must NOT start another study — re-triggering autostudy
    from the heal is the infinite loop an orphaned spec caused (ATOMIC, 29 Aug
    2026): study -> heal -> autostudy can't match the renamed spec -> re-study."""
    captured = {}
    monkeypatch.setattr(IF, "_open_docs_for", lambda db, v, i: [object()])
    monkeypatch.setattr(
        IF,
        "_Loaded",
        lambda db, cdb, v: type("L", (), {"invoice": lambda self, i: {}})(),
    )
    monkeypatch.setattr(IF, "_wipe_validation_caches", lambda db, inv: None)
    monkeypatch.setattr(
        IF, "_squash_review_into_drafts", lambda *a, **k: captured.update(k)
    )
    assert IF.heal_review(None, None, "v1", "inv-1") is True
    assert captured.get("trigger_autostudy") is False


def test_heal_review_clears_studying_flag_and_does_not_loop(
    db_session, venue, monkeypatch
):
    """E2E on the review+draft path against a real WorkingDocument: a study's
    heal re-reviews the card, DROPS the 'studying' flag, and triggers NO new
    study — the two halves of the ATOMIC loop fix. Before e711b8c the heal
    re-ran autostudy, which on an orphaned spec re-studied forever."""
    import datetime as dt

    from app.db.models import WorkingDocument

    doc = WorkingDocument(
        doc_type="received_invoice",
        venue_id=venue.id,
        connector_name="loadedhub",
        sync_mode="submit",
        external_ref={"invoice_id": "inv-1"},
        data={"doc_schema": "replica_v1", "sensei_studying": True},
        version=5,
        created_at=dt.datetime.now(dt.timezone.utc),
    )
    db_session.add(doc)
    db_session.flush()

    fresh = {
        "doc_schema": "replica_v1",
        "reviewed_at": "2026-08-29T00:00:00+00:00",
        "supplier_name": "Feed Co",
        "lines": [],
    }
    monkeypatch.setattr(
        "app.services.invoice_review.review_invoice", lambda *a, **k: dict(fresh)
    )
    monkeypatch.setattr(
        IF,
        "_Loaded",
        lambda db, cdb, v: type("L", (), {"invoice": lambda self, i: {}})(),
    )
    monkeypatch.setattr(IF, "_wipe_validation_caches", lambda db, inv: None)
    monkeypatch.setattr(IF, "_attach_po_reference", lambda *a, **k: None)
    monkeypatch.setattr(IF, "attach_item_names", lambda *a, **k: None)
    monkeypatch.setattr(IF, "enrich_loaded_snapshot", lambda *a, **k: None)
    n = {"autostudy": 0}
    monkeypatch.setattr(
        "app.services.spec_dojo.autostudy_if_spec_less",
        lambda *a, **k: n.__setitem__("autostudy", n["autostudy"] + 1),
    )

    assert IF.heal_review(db_session, db_session, venue.id, "inv-1") is True
    db_session.refresh(doc)
    assert "sensei_studying" not in (doc.data or {})  # flag cleared
    assert doc.version == 6  # draft re-written
    assert n["autostudy"] == 0  # NO new study triggered


class TestTheInteractiveReviewPrefetchesReferenceOnce:
    """The path a human waits on must not re-fetch the catalogue three times.

    Without a ``reference``, ``build_replica`` self-fetches: the stock
    catalogue is pulled once by ``loaded_reference`` (TTL-cached), again in
    ``invoice_replica`` (bypassing that cache) and a third time inside
    ``suggest_item_matches`` — plus the 400-day received feed twice. The batch
    path has always prefetched once and passed it down; the interactive path
    never did, and it is the only one with a person watching.
    """

    def test_squash_forwards_reference_to_review_invoice(self, monkeypatch):
        import app.services.invoice_review as ir
        from app.routers import invoice_fixes as IF

        seen: dict = {}

        def fake_review_invoice(db, config_db, venue_id, invoice_id, **kw):
            seen["reference"] = kw.get("reference")
            return {"invoice_id": invoice_id, "lines": [], "doc_schema": "replica_v1"}

        monkeypatch.setattr(ir, "review_invoice", fake_review_invoice)
        monkeypatch.setattr(ir, "carry_forward_decisions", lambda *a, **k: None)
        monkeypatch.setattr(
            IF,
            "_Loaded",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no loaded")),
        )

        class _Db:
            def commit(self):
                pass

        sentinel = {"catalogue": [{"id": "i-1"}], "units": [], "suppliers": []}
        IF._squash_review_into_drafts(
            _Db(), None, "v-1", "inv-1", [], reference=sentinel
        )
        assert seen["reference"] is sentinel

    def test_the_review_endpoint_prefetches(self):
        """The endpoint must actually CALL the prefetch — a reference kwarg
        nothing populates is the silent way this optimisation dies."""
        import inspect

        from app.routers import invoice_fixes as IF

        src = inspect.getsource(IF.review_receive_draft)
        assert "prefetch_replica_reference" in src
        assert "reference=reference" in src
