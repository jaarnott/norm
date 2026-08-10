"""Unit tests for the invoice-fixes appliers (link_po, unit + variant).

Exercises the orchestration logic with a scripted _Loaded fake — no network,
no DB — asserting the exact LoadedHub request sequence each fix produces.
"""

import re

import pytest

from app.routers import invoice_fixes as IF
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
        msg = IF._apply_link_po(
            lh, {"invoice_id": "inv-1", "po_number": "PO#1520987"}, None
        )
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
            IF._apply_link_po(lh, {"invoice_id": "inv-1", "po_number": "9999"}, None)
            assert False, "expected failure"
        except RuntimeError as e:
            assert "not found" in str(e)
        assert lh.writes == []  # nothing written on failure


class TestDeleteInvoice:
    def test_deletes_the_draft(self):
        # Verified live in the Loaded test env: DELETE .../invoices/{id} -> 204.
        lh = FakeLoaded({}, {})
        msg = IF._apply_delete_invoice(lh, {"invoice_id": "inv-1"}, None)
        assert "deleted" in msg.lower()
        assert lh.writes == [("DELETE", "/1.0/stock/internal/invoices/inv-1", None)]


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
        msg = IF._apply_unit(lh, self._fix("100 piece"), None)
        assert "100 piece" in msg and "variant" in msg
        put = [w for w in lh.writes if w[0] == "PUT"][0]
        assert put[2]["lines"][0]["linkedUnitId"] == "u-100pc"
        patch = [w for w in lh.writes if w[0] == "PATCH"][0]
        assert patch[1].endswith("/item-supplier-variant/var-1")
        assert patch[2] == {"unitId": "u-100pc"}

    def test_guideline_equivalent_unit_resolves(self):
        # "1 each" is guideline-equivalent to the Loaded "Each" unit.
        lh = self._lh()
        IF._apply_unit(lh, self._fix("1 each"), None)
        put = [w for w in lh.writes if w[0] == "PUT"][0]
        assert put[2]["lines"][0]["linkedUnitId"] == "u-each"

    def test_unresolvable_unit_writes_nothing(self, monkeypatch):
        # A bare packaging word ("carton") isn't a real unit; the LLM spec resolve
        # declines → we raise and write nothing (no bad unit created).
        monkeypatch.setattr(IF, "_resolve_unit_spec", lambda name, db: None)
        lh = self._lh()
        try:
            IF._apply_unit(lh, self._fix("carton"), None)
            assert False
        except RuntimeError as e:
            assert "could not resolve a unit definition" in str(e)
        assert lh.writes == []

    def test_no_variant_still_updates_line(self):
        # Variant not found (different supplier) → line updated, no PATCH.
        lh = self._lh()
        msg = IF._apply_unit(
            lh, {**self._fix("Each"), "linked_supplier_id": "sup-X"}, None
        )
        assert "variant" not in msg
        assert [w for w in lh.writes if w[0] == "PATCH"] == []
        assert [w for w in lh.writes if w[0] == "PUT"]

    def test_creates_missing_multipack_unit(self, monkeypatch):
        # An OUTER multipack ("5x3kg") isn't in the catalogue → create it with the
        # LLM-resolved ratio/type, then set it on the line + variant.
        monkeypatch.setattr(
            IF,
            "_resolve_unit_spec",
            lambda name, db: {"ratio": 15.0, "stock_unit_type": "Weight"},
        )

        class CreatingLoaded(FakeLoaded):
            def request(self, method, path, body=None):
                self.writes.append((method, path, body))
                if method == "POST" and path.endswith("/units"):
                    return {
                        "id": "u-5x3kg",
                        "name": body["name"],
                        "ratio": body["ratio"],
                        "stockUnitType": body["stockUnitType"],
                    }
                return {}

        item = {
            "suppliers": [{"id": "var-1", "supplierId": "sup-1", "stockCode": "NAP"}]
        }
        lh = CreatingLoaded(
            {"/1.0/stock/internal/units": UNITS, "/1.0/stock/internal/items/": item},
            {"inv-1": _invoice()},
        )
        msg = IF._apply_unit(lh, self._fix("5x3kg"), None)
        assert "Created" in msg
        post = [w for w in lh.writes if w[0] == "POST"][0]
        assert post[1].endswith("/units")
        assert post[2] == {"name": "5x3kg", "ratio": 15.0, "stockUnitType": "Weight"}
        put = [w for w in lh.writes if w[0] == "PUT"][0]
        assert put[2]["lines"][0]["linkedUnitId"] == "u-5x3kg"
        patch = [w for w in lh.writes if w[0] == "PATCH"][0]
        assert patch[2] == {"unitId": "u-5x3kg"}

    def test_multipack_matches_exact_name_only(self):
        # A multipack proposed name resolves by EXACT name; it is NOT fuzzily
        # reused for a same-ratio simple unit — an unmatched multipack → None
        # (so _apply_unit creates it).
        units = UNITS + [
            {"id": "u-15kg", "name": "15 KG", "ratio": 15.0, "stockUnitType": "Weight"}
        ]
        lh = FakeLoaded({"/1.0/stock/internal/units": units}, {})
        assert IF._resolve_unit(lh, "5x3kg") is None
        units2 = units + [
            {"id": "u-5x3", "name": "5x3kg", "ratio": 15.0, "stockUnitType": "Weight"}
        ]
        lh2 = FakeLoaded({"/1.0/stock/internal/units": units2}, {})
        assert IF._resolve_unit(lh2, "5x3kg")["id"] == "u-5x3"


def test_is_multipack_mirror_tolerates_spacing():
    # Mirror of the consolidator's _is_multipack (kept in sync deliberately):
    # spaced pack notation is the same pack — '6x 750ml' == '6x750ml'.
    from app.services.invoice_units import is_multipack

    for good in ("5x3kg", "6x750ml", "6x 750ml", "4 x 6 pack", "2X12"):
        assert is_multipack(good), good
    for bad in ("Case(s)", "750ml", "x2", "box", "", None):
        assert not is_multipack(bad), bad


def test_multipack_equal_mirror():
    # Mirror of the consolidator's _multipack_equal: component-wise pack
    # equality ('6x1L' == '6 X 1 Litre'), never across pack shapes.
    from app.services.invoice_units import multipack_equal

    assert multipack_equal("6x1L", "6 X 1 Litre")
    assert multipack_equal("6x750ml", "6x 750ml")
    assert not multipack_equal("6x750ml", "6x1L")
    assert not multipack_equal("4x6 pack", "24 pack")
    assert not multipack_equal("2x12 pack", "24 pack")
    # Case/whitespace never distinguish; digits and DOTS do (user rule,
    # 08 Aug 2026 — the alnum norm let '1.9 KG' name-match '19 KG').
    assert multipack_equal("Each", " each")
    assert not multipack_equal("6x1.5L", "6x15L")


def test_units_equivalent_mirror():
    # Mirror of the consolidator's _units_equivalent (Hancocks 4358010,
    # 08 Aug 2026): a copy printing only a pack COUNT names the same pack as
    # the sized multipack, and EA names a single sized bottle — neither may
    # churn a unit suggestion. Counts still gate everything else.
    from app.services.invoice_units import units_equivalent

    # Pack "6 PK" vs the Loaded multipack it denotes
    assert units_equivalent("6x750mL", "6 pack")
    assert units_equivalent("6 pk", "6x 750ml")
    assert units_equivalent("dozen", "12x330ml")
    # EA vs the single-bottle unit
    assert units_equivalent("375 mL", "each")
    assert units_equivalent("750 mL", "EA")
    # Magnitude equality still holds
    assert units_equivalent("0.7 L", "700 mL")
    # Counts must actually match
    assert not units_equivalent("6x750mL", "12 pack")
    assert not units_equivalent("each", "12 pack")
    assert not units_equivalent("24 pack", "4x6 pack")
    # 'each' never absorbs weight-priced units (quantities mean kilos)
    assert not units_equivalent("1.9 KG", "each")
    # Vague packaging words still aren't equivalence material
    assert not units_equivalent("Case(s)", "6x750ml")
    assert not units_equivalent("Case(s)", "750ml")


def test_resolve_unit_keeps_dots_distinct():
    # '1.9 KG' must never NAME-match '19 KG' (the old alnum norm merged them);
    # with no name hit and different magnitudes, resolution correctly fails.
    units = UNITS + [
        {"id": "u-19kg", "name": "19 KG", "ratio": 19.0, "stockUnitType": "Weight"}
    ]
    lh = FakeLoaded({"/1.0/stock/internal/units": units}, {})
    assert IF._resolve_unit(lh, "1.9 KG") is None
    # Case/whitespace variants of the SAME name still resolve.
    assert IF._resolve_unit(lh, " 19 kg ")["id"] == "u-19kg"


def test_resolve_unit_component_equivalent_multipack():
    # Accepting '6x1L' must RESOLVE to the existing '6 X 1 Litre' unit rather
    # than creating a near-duplicate; a differently-shaped pack still creates.
    units = UNITS + [
        {"id": "u-6x1l", "name": "6 X 1 Litre", "ratio": 6.0, "stockUnitType": "Volume"}
    ]
    lh = FakeLoaded({"/1.0/stock/internal/units": units}, {})
    assert IF._resolve_unit(lh, "6x1L")["id"] == "u-6x1l"
    assert IF._resolve_unit(lh, "6x 1 litre")["id"] == "u-6x1l"
    assert IF._resolve_unit(lh, "12x1L") is None  # different count → create


class TestGetOrCreateUnit:
    """The shared resolve-or-create seam behind _apply_unit AND the explicit
    /invoice-fixes/create-unit endpoint (the editor's "use 49.5L (new unit)"
    accept for a copy-delivered unit the catalogue lacks)."""

    def test_existing_unit_short_circuits_without_writes(self):
        lh = FakeLoaded({"/1.0/stock/internal/units": UNITS}, {})
        unit, created = IF._get_or_create_unit(lh, "100 piece", None)
        assert unit["id"] == "u-100pc"
        assert created is False
        assert lh.writes == []

    def test_missing_unit_created_with_llm_spec(self, monkeypatch):
        monkeypatch.setattr(
            IF,
            "_resolve_unit_spec",
            lambda name, db: {"ratio": 49.5, "stock_unit_type": "Volume"},
        )

        class CreatingLoaded(FakeLoaded):
            def request(self, method, path, body=None):
                self.writes.append((method, path, body))
                if method == "POST" and path.endswith("/units"):
                    return {
                        "id": "u-49-5l",
                        "name": body["name"],
                        "ratio": body["ratio"],
                        "stockUnitType": body["stockUnitType"],
                    }
                return {}

        lh = CreatingLoaded({"/1.0/stock/internal/units": UNITS}, {})
        unit, created = IF._get_or_create_unit(lh, "49.5L", None)
        assert created is True
        assert unit["id"] == "u-49-5l"
        assert lh.writes == [
            (
                "POST",
                "/1.0/stock/internal/units",
                {"name": "49.5L", "ratio": 49.5, "stockUnitType": "Volume"},
            )
        ]

    def test_unresolvable_spec_raises_and_writes_nothing(self, monkeypatch):
        monkeypatch.setattr(IF, "_resolve_unit_spec", lambda name, db: None)
        lh = FakeLoaded({"/1.0/stock/internal/units": UNITS}, {})
        try:
            IF._get_or_create_unit(lh, "carton", None)
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "could not resolve a unit definition" in str(e)
        assert lh.writes == []


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

    def test_header_totals_written_through(self):
        # Accepted copy totals (feed left the header $0) land on the PUT.
        lh = self._lh()
        IF._do_receive(
            lh,
            self._req(total=1189.27, subtotal=1034.15, tax_amount=155.12),
        )
        put = [w for w in lh.writes if w[0] == "PUT"][0]
        assert put[2]["total"] == 1189.27
        assert put[2]["subtotal"] == 1034.15
        assert put[2]["taxAmount"] == 155.12

    def test_reference_number_written_through(self):
        # Accepted "Invoice number X → Y (per the invoice copy)" lands on the
        # PUT as referenceNumber — the copy-number suggestion's write path.
        lh = self._lh()
        IF._do_receive(lh, self._req(reference_number="INV-9999"))
        put = [w for w in lh.writes if w[0] == "PUT"][0]
        assert put[2]["referenceNumber"] == "INV-9999"

    def test_unlink_purchase_order_clears_link(self):
        # Accepted "unlink this order" (PO belongs to another supplier): the
        # fetched invoice carries the stale link; the flag must clear BOTH
        # fields on the PUT — a null po_id alone means "don't touch".
        lh = self._lh()
        lh._invoices["inv-1"]["linkedPurchaseOrderId"] = "po-oops"
        lh._invoices["inv-1"]["purchaseOrderNumber"] = "4041451-1"
        IF._do_receive(lh, self._req(unlink_purchase_order=True))
        put = [w for w in lh.writes if w[0] == "PUT"][0]
        assert put[2]["linkedPurchaseOrderId"] is None
        assert put[2]["purchaseOrderNumber"] is None

    def test_loaded_rejection_surfaces_as_502_with_detail(self):
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
            IF._do_receive(lh, self._req())
        assert exc.value.status_code == 502
        assert "invoice-totals-mismatch" in str(exc.value.detail)

    def test_purchase_order_number_reference_written_through(self):
        # Split order accepted from the copy: the order-number REFERENCE
        # rides the header write-through (never the 1:1 link).
        lh = self._lh()
        IF._do_receive(lh, self._req(purchase_order_number="1520987"))
        put = [w for w in lh.writes if w[0] == "PUT"][0]
        assert put[2]["purchaseOrderNumber"] == "1520987"
        assert put[2]["linkedPurchaseOrderId"] is None

    def test_link_path_owns_the_number_when_linking(self):
        # A stale reference field from the card must never overwrite the
        # number the link path resolved.
        lh = self._lh()
        IF._do_receive(
            lh,
            self._req(linked_purchase_order_id="po-77", purchase_order_number="STALE"),
        )
        put = [w for w in lh.writes if w[0] == "PUT"][0]
        assert put[2]["linkedPurchaseOrderId"] == "po-77"
        assert put[2]["purchaseOrderNumber"] != "STALE"

    def test_split_notes_stamped_on_po_and_sibling(self):
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
        IF._do_receive(
            lh,
            self._req(
                split_po_id="po-split",
                split_sibling_invoice_id="sib-9",
                reference_number="IN-2ND",
            ),
        )
        po_put = [w for w in lh.writes if w[1].endswith("/po-split")][0]
        assert "Split order: also invoiced on IN-2ND" in po_put[2]["notes"]
        sib_put = [w for w in lh.writes if w[1].endswith("/invoices/sib-9")][0]
        assert "Split order: order 1520987 also covers IN-2ND" in sib_put[2]["notes"]

    def test_receive_survives_note_failures(self):
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
        out = IF._do_receive(
            lh,
            self._req(
                split_po_id="po-split",
                split_sibling_invoice_id="sib-missing",  # KeyError in the fake
            ),
        )
        assert out["ok"] is True
        by_target = {n["target"]: n for n in out["split_notes"]}
        assert by_target["purchase_order"]["ok"] is True
        assert by_target["sibling_invoice"]["ok"] is False

    def test_unlink_then_relink_links_the_new_po(self):
        # Unlink + a new PO in the same receive: the unlink clears the stale
        # link first, then the chosen order links — the user unlinked the
        # wrong supplier's PO and picked the right one before receiving.
        lh = self._lh()
        lh._invoices["inv-1"]["linkedPurchaseOrderId"] = "po-oops"
        IF._do_receive(
            lh,
            self._req(unlink_purchase_order=True, linked_purchase_order_id="po-77"),
        )
        put = [w for w in lh.writes if w[0] == "PUT"][0]
        assert put[2]["linkedPurchaseOrderId"] == "po-77"

    def test_receive_with_no_lines_rejected(self):
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
            IF._do_receive(lh, self._req())
            raise AssertionError("expected 400")
        except HTTPException as e:
            assert e.status_code == 400
            assert "nothing to receive" in str(e.detail)
        assert lh.writes == []

    def test_receive_without_supplier_rejected(self):
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
            IF._do_receive(lh, self._req())
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
        IF._do_receive(lh2, self._req(linked_supplier_id="sup-1"))
        assert [w for w in lh2.writes if w[0] == "PUT"]

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

    def test_editable_header_fields_persist(self):
        lh = self._lh()
        req = self._req(
            reference_number="INV-EDITED",
            issued_at="2026-07-30",
            due_at="2026-08-20",
            total=169.60,
            linked_supplier_id="sup-2",
            unit_cost_includes_tax=True,
            notes="Short delivery on line 2",
        )
        IF._do_receive(lh, req)
        body = [w for w in lh.writes if w[0] == "PUT"][0][2]
        assert body["referenceNumber"] == "INV-EDITED"
        assert body["issuedAt"] == "2026-07-30"
        assert body["dueAt"] == "2026-08-20"
        assert body["total"] == 169.60
        assert body["linkedSupplierId"] == "sup-2"
        assert body["unitCostIncludesTax"] is True
        assert body["notes"] == "Short delivery on line 2"

    def test_received_date_from_header_is_not_overwritten(self):
        lh = self._lh()
        IF._do_receive(lh, self._req(received_at="2026-07-31T19:57:00Z"))
        body = [w for w in lh.writes if w[0] == "PUT"][0][2]
        assert body["isReceived"] is True
        assert body["receivedAt"] == "2026-07-31T19:57:00Z"

    def test_unsupplied_header_fields_leave_the_invoice_untouched(self):
        lh = self._lh()
        IF._do_receive(lh, self._req())  # no header edits
        body = [w for w in lh.writes if w[0] == "PUT"][0][2]
        # linkedSupplierId came from the fetched invoice, not the request.
        assert body["linkedSupplierId"] == "sup-1"
        assert "notes" not in body

    def test_add_item_appends_a_new_line(self):
        lh = self._lh()
        req = self._req(
            lines=[
                {
                    "id": "new-tmp-1",  # a client temp id, not on the invoice
                    "code": "OILG3",
                    "description": "OIL GRAPESEED",
                    "linked_item_id": "item-oil",
                    "linked_unit_id": "u-3l",
                    "unit": "3 L",
                    "unit_ratio": 3000.0,
                    "quantity_received": 1,
                    "unit_cost": 49.95,
                    "total_cost": 49.95,
                }
            ],
        )
        IF._do_receive(lh, req)
        body = [w for w in lh.writes if w[0] == "PUT"][0][2]
        assert len(body["lines"]) == 2  # original + the added one
        added = body["lines"][1]
        assert added["code"] == "OILG3"
        assert added["linkedItemId"] == "item-oil"
        assert added["quantityReceived"] == 1
        assert added["unitCost"] == 49.95
        assert "id" not in added  # temp id dropped; Loaded assigns the real one

    def test_add_item_ignores_an_empty_row(self):
        lh = self._lh()
        # A row with no code/item must not create a rubbish line.
        IF._do_receive(lh, self._req(lines=[{"id": "new-x", "quantity_received": 2}]))
        body = [w for w in lh.writes if w[0] == "PUT"][0][2]
        assert len(body["lines"]) == 1

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

    def test_receive_blocked_by_a_new_stock_item(self):
        # A line with no linkedItemId (NEW stock item) must block receive so it
        # can't be created silently — nothing is PUT.
        inv = self._inv()
        inv["lines"][0].pop("linkedItemId")
        lh = FakeLoaded(
            {"/1.0/stock/internal/purchase-orders": PO_LIST}, {"inv-1": inv}
        )
        try:
            IF._do_receive(lh, self._req())
            assert False, "expected the receive guard to block"
        except Exception as e:
            assert "must be created in Loaded before receiving" in str(e)
        assert [w for w in lh.writes if w[0] == "PUT"] == []

    def test_receive_blocked_by_a_new_unit(self):
        inv = self._inv()
        inv["lines"][0].pop("linkedUnitId")
        lh = FakeLoaded(
            {"/1.0/stock/internal/purchase-orders": PO_LIST}, {"inv-1": inv}
        )
        try:
            IF._do_receive(lh, self._req())
            assert False, "expected the receive guard to block"
        except Exception as e:
            assert "must be created in Loaded before receiving" in str(e)
        assert [w for w in lh.writes if w[0] == "PUT"] == []

    def test_receive_not_blocked_when_only_saving(self):
        # The guard is receive-only: a save (receive=False) of an unresolved line
        # still PUTs (the editor persists work-in-progress before resolving).
        inv = self._inv()
        inv["lines"][0].pop("linkedItemId")
        lh = FakeLoaded(
            {"/1.0/stock/internal/purchase-orders": PO_LIST}, {"inv-1": inv}
        )
        IF._do_receive(lh, self._req(receive=False))
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


class TestAttachPoReference:
    """The linked PO's ordered qty / reference cost / order date and the
    substitution + un-received flags, matched to invoice lines by STOCK CODE
    (not item id) so a same-name product swap surfaces rather than hides."""

    # A PO ordering GARLIC (VEGS251) and BROCCOLINI (BROC).
    PO = {
        "createdAt": "2026-07-20",
        "lines": [
            {
                "itemCode": "VEGS251",
                "itemName": "GARLIC PEELED",
                "unitName": "1kg",
                "quantityOrdered": 3,
                "unitCost": 8.0,
                "itemId": "item-garlic",  # same item id as the substitute below
            },
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

    def _fake(self):
        return FakeLoaded(
            gets={"/1.0/stock/internal/purchase-orders/po-1": self.PO},
            invoices={},
        )

    def _data(self):
        return {
            "linked_purchase_order_id": "po-1",
            "lines": [
                # A substitute: same item id as the ordered GARLIC, DIFFERENT code.
                {
                    "id": "ln-g",
                    "code": "VEGF251",
                    "linked_item_id": "item-garlic",
                    "unit_cost": 9.0,
                },
                # Matches the PO by code (lower-case + spacing prove normalisation).
                {
                    "id": "ln-b",
                    "code": " broc ",
                    "linked_item_id": "item-broc",
                    "unit_cost": 2.5,
                },
            ],
        }

    def test_code_match_populates_ordered_qty_and_reference_cost(self):
        data = self._data()
        IF._attach_po_reference(data, self._fake())
        broc = data["lines"][1]
        assert broc["on_order"] is True
        assert broc["quantity_ordered"] == 4
        assert broc["reference_cost"] == 2.5
        assert data["order_date"] == "2026-07-20"

    def test_coded_line_delivered_under_different_code_is_a_substitute(self):
        # VEGF251 delivered, code not on the PO, but the SAME item was ordered as
        # VEGS251 → a substitute: matched, ordered qty borrowed, substitute_for set
        # to the original ordered line, and VEGS251 NOT listed as not-delivered
        # (it WAS delivered, under a different code). Mirrors BROCCOLI VEGF0223←165618.
        data = self._data()
        IF._attach_po_reference(data, self._fake())
        g = data["lines"][0]  # VEGF251 delivered
        assert g["on_order"] is True
        assert g["quantity_ordered"] == 3
        assert g["substitute_for"] == {
            "code": "VEGS251",
            "description": "GARLIC PEELED",
            "unit": "1kg",
            "quantity_ordered": 3,
            "unit_cost": 8.0,
        }
        assert data["ordered_not_received"] == []

    def test_substitute_and_not_delivered_coexist(self):
        # The old bug: pairing a substitute dropped genuinely-not-delivered items.
        # Here garlic is delivered under a different code (substitute) AND parsley
        # is never delivered — BOTH must show: garlic as a substitute, parsley as
        # ordered-not-delivered.
        po = {
            "createdAt": "x",
            "lines": [
                {
                    "itemCode": "VEGS251",
                    "itemName": "GARLIC",
                    "unitName": "1kg",
                    "quantityOrdered": 3,
                    "unitCost": 8.0,
                    "itemId": "item-garlic",
                },
                {
                    "itemCode": "PARS1",
                    "itemName": "PARSLEY",
                    "unitName": "bunch",
                    "quantityOrdered": 2,
                    "unitCost": 1.5,
                    "itemId": "item-parsley",
                },
            ],
        }
        lh = FakeLoaded({"/1.0/stock/internal/purchase-orders/po-1": po}, {})
        data = {
            "linked_purchase_order_id": "po-1",
            "lines": [
                {
                    "id": "ln-g",
                    "code": "VEGF251",
                    "linked_item_id": "item-garlic",
                    "unit_cost": 9.0,
                }
            ],
        }
        IF._attach_po_reference(data, lh)
        assert (
            data["lines"][0]["substitute_for"]["code"] == "VEGS251"
        )  # substitute shown
        assert [o["code"] for o in data["ordered_not_received"]] == [
            "PARS1"
        ]  # not-delivered survives

    def test_substitute_without_item_match_stays_blank_and_ordered_line_shows(self):
        # A genuinely different product (no PO line shares its itemId): nothing to
        # borrow, no link, and the ordered GARLIC still shows as unreceived.
        data = self._data()
        data["lines"][0]["linked_item_id"] = "item-unknown"
        IF._attach_po_reference(data, self._fake())
        sub = data["lines"][0]
        assert sub["on_order"] is False
        assert sub["quantity_ordered"] is None
        assert sub["reference_cost"] is None
        assert sub["substitute_for"] is None
        assert [o["code"] for o in data["ordered_not_received"]] == ["VEGS251"]

    def test_codeless_line_matches_po_by_item_id_and_borrows_the_code(self):
        # A line with no stock code still matches the PO by itemId (mirroring
        # Loaded) — ordered qty populated and display_code falls back to the PO
        # line's itemCode (the item's code Loaded shows).
        data = self._data()
        data["lines"][0]["code"] = None
        IF._attach_po_reference(data, self._fake())
        g = data["lines"][0]
        assert g["on_order"] is True
        assert g["quantity_ordered"] == 3
        assert g["display_code"] == "VEGS251"  # borrowed from the PO line
        assert data["ordered_not_received"] == []

    def test_fully_received_order_has_no_unreceived_lines(self):
        data = self._data()
        # Make the first line match the ordered GARLIC by code too.
        data["lines"][0]["code"] = "VEGS251"
        IF._attach_po_reference(data, self._fake())
        assert data["ordered_not_received"] == []
        assert all(ln["on_order"] for ln in data["lines"])

    def test_unlinked_draft_clears_reference_data(self):
        data = {
            "linked_purchase_order_id": None,
            "order_date": "old",
            "ordered_not_received": [{"code": "X"}],
            "lines": [{"id": "ln-1", "code": "A", "quantity_ordered": 5}],
        }
        IF._attach_po_reference(data, self._fake())
        assert "ordered_not_received" not in data
        assert "order_date" not in data
        ln = data["lines"][0]
        assert ln["quantity_ordered"] is None
        assert ln["reference_cost"] is None
        assert ln["on_order"] is None

    def test_ordered_not_received_lists_all_unmatched_po_lines(self):
        # Mirror Loaded: every PO item with no matching invoice line shows below
        # with its FULL ordered qty (Loaded surfaces them as receivable rows,
        # received 0) — we no longer subtract the PO's cumulative received.
        po = {
            "createdAt": "2026-07-20",
            "lines": [
                {
                    "itemCode": "FULL1",
                    "itemName": "FULLY RECEIVED",
                    "unitName": "Each",
                    "quantityOrdered": 3,
                    "quantityReceived": 3,
                    "unitCost": 1.0,
                },
                {
                    "itemCode": "PART1",
                    "itemName": "PARTLY RECEIVED",
                    "unitName": "Each",
                    "quantityOrdered": 5,
                    "quantityReceived": 2,
                    "unitCost": 2.0,
                },
            ],
        }
        lh = FakeLoaded(
            gets={"/1.0/stock/internal/purchase-orders/po-1": po}, invoices={}
        )
        data = {
            "linked_purchase_order_id": "po-1",
            "lines": [{"id": "ln-x", "code": "OTHER", "unit_cost": 1.0}],
        }
        IF._attach_po_reference(data, lh)
        onr = {o["code"]: o for o in data["ordered_not_received"]}
        assert onr["FULL1"]["quantity_ordered"] == 3  # full ordered qty, shown
        assert onr["PART1"]["quantity_ordered"] == 5  # full ordered qty, shown

    def test_attach_runs_from_split_po_id_without_a_link(self):
        # Split order: no Loaded link on THIS invoice (the sibling holds it),
        # but the card carries split_po_id — reconcile against that order
        # (QTY ORDERED / ordered-not-delivered) without touching link fields.
        po = {
            "createdAt": "2026-07-20",
            "lines": [
                {
                    "itemCode": "SPLIT1",
                    "itemName": "OTHER DELIVERY ITEM",
                    "unitName": "Each",
                    "quantityOrdered": 4,
                    "unitCost": 2.5,
                },
            ],
        }
        lh = FakeLoaded(
            gets={"/1.0/stock/internal/purchase-orders/po-split": po}, invoices={}
        )
        data = {
            "split_po_id": "po-split",
            "lines": [{"id": "ln-x", "code": "OTHER", "unit_cost": 1.0}],
        }
        IF._attach_po_reference(data, lh)
        assert [o["code"] for o in data["ordered_not_received"]] == ["SPLIT1"]
        assert not data.get("linked_purchase_order_id")

    def test_attach_runs_even_before_split_acceptance(self):
        # Scenario 3 (number found on the copy only): the order's reference
        # data shows from the FIRST open — the engine already validated
        # against this order, and waiting for the accept left the card
        # without QTY ORDERED until a reopen (Bidvest 109827538, 09 Aug).
        po = {"createdAt": "2026-07-20", "lines": []}
        data = {
            "split_po_id": "po-split",
            "split_po_suggested": True,
            "lines": [{"id": "ln-x"}],
        }
        IF._attach_po_reference(
            data,
            FakeLoaded(
                gets={"/1.0/stock/internal/purchase-orders/po-split": po},
                invoices={},
            ),
        )
        assert data["ordered_not_received"] == []
        assert data["order_date"] == "2026-07-20"

    def test_split_sibling_receipts_partition_out_of_not_delivered(self):
        # Split order: a PO line the SIBLING invoice received is "Ordered,
        # received on <sibling>", not "not delivered"; a line on neither
        # invoice stays under "not delivered". Sibling unfetchable → all
        # stay "not delivered" (best-effort).
        po = {
            "createdAt": "2026-07-20",
            "lines": [
                {
                    "itemCode": "VIN1",
                    "itemName": "CHARDONNAY VINEGAR",
                    "unitName": "Each",
                    "quantityOrdered": 3,
                    "unitCost": 40.0,
                },
                {
                    "itemCode": "MISS1",
                    "itemName": "NEVER DELIVERED",
                    "unitName": "Each",
                    "quantityOrdered": 1,
                    "unitCost": 5.0,
                },
            ],
        }
        lh = FakeLoaded(
            gets={"/1.0/stock/internal/purchase-orders/po-split": po},
            invoices={
                "sib-1": {
                    "id": "sib-1",
                    "lines": [{"id": "s1", "code": "VIN1", "quantityReceived": 3.0}],
                }
            },
        )
        data = {
            "split_po_id": "po-split",
            "split_sibling_invoice_id": "sib-1",
            "lines": [{"id": "ln-x", "code": "OTHER", "unit_cost": 1.0}],
        }
        IF._attach_po_reference(data, lh)
        assert [o["code"] for o in data["ordered_received_elsewhere"]] == ["VIN1"]
        assert data["ordered_received_elsewhere"][0]["quantity_received"] == 3.0
        assert [o["code"] for o in data["ordered_not_received"]] == ["MISS1"]

        # Sibling fetch failure → everything stays under "not delivered".
        lh2 = FakeLoaded(
            gets={"/1.0/stock/internal/purchase-orders/po-split": po}, invoices={}
        )
        data2 = {
            "split_po_id": "po-split",
            "split_sibling_invoice_id": "sib-missing",
            "lines": [{"id": "ln-x", "code": "OTHER", "unit_cost": 1.0}],
        }
        IF._attach_po_reference(data2, lh2)
        assert len(data2["ordered_not_received"]) == 2
        assert data2["ordered_received_elsewhere"] == []


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
    order's sibling link)."""

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

    def test_skips_writeback_when_po_linked_to_another_invoice(self):
        lh = self._lh("inv-OTHER")
        out = IF._do_receive(lh, self._req())
        body = [w for w in lh.writes if w[0] == "PUT"][0][2]
        assert body.get("linkedPurchaseOrderId") is None  # link NOT written
        assert out["po_link_skipped"] is True
        assert out["linked_purchase_order"] is None
        assert body["isReceived"] is True  # still received

    def test_links_when_po_unlinked(self):
        lh = self._lh(None)
        out = IF._do_receive(lh, self._req())
        body = [w for w in lh.writes if w[0] == "PUT"][0][2]
        assert body["linkedPurchaseOrderId"] == "po-1"
        assert body["purchaseOrderNumber"] == "1520272"
        assert out["po_link_skipped"] is False

    def test_links_when_po_already_points_at_this_invoice(self):
        lh = self._lh("inv-1")
        out = IF._do_receive(lh, self._req())
        body = [w for w in lh.writes if w[0] == "PUT"][0][2]
        assert body["linkedPurchaseOrderId"] == "po-1"
        assert out["po_link_skipped"] is False


class TestApplyLinkPoGuard:
    """The batch link_po fix applier reuses the resolver and the same 1:1 guard."""

    def test_skips_when_po_already_linked_elsewhere(self):
        lh = FakeLoaded(
            gets={
                "/1.0/stock/internal/purchase-orders?": [
                    {
                        "id": "po-1",
                        "orderNumber": "1520272",
                        "linkedInvoiceId": "inv-OTHER",
                    }
                ]
            },
            invoices={},
        )
        msg = IF._apply_link_po(
            lh, {"invoice_id": "inv-1", "po_number": "1520272"}, None
        )
        assert "already linked" in msg
        assert [w for w in lh.writes if w[0] == "PUT"] == []  # no write

    def test_links_when_po_unlinked(self):
        lh = FakeLoaded(
            gets={
                "/1.0/stock/internal/purchase-orders?": [
                    {"id": "po-1", "orderNumber": "1520272", "linkedInvoiceId": None}
                ]
            },
            invoices={"inv-1": {"id": "inv-1", "lines": []}},
        )
        msg = IF._apply_link_po(
            lh, {"invoice_id": "inv-1", "po_number": "1520272"}, None
        )
        assert "Linked purchase order 1520272" in msg
        body = [w for w in lh.writes if w[0] == "PUT"][0][2]
        assert body["linkedPurchaseOrderId"] == "po-1"


def _ids_in(prompt: str) -> list[str]:
    """The line_ids the LLM was shown, in order (for scripted-mock replies)."""
    return re.findall(r"line_id=(\S+?)\]", prompt)


class TestItemMatch:
    """The LLM match-before-create step: classify → department-filtered match →
    suggest a link (existing item) or a normalized name+group (create)."""

    def test_new_item_lines_skips_linked_and_deleted(self):
        inv = {
            "lines": [
                {"id": "a", "description": "NEW", "code": "N", "linkedItemId": None},
                {"id": "b", "description": "OLD", "linkedItemId": "item-1"},
                {"id": "c", "description": "GONE", "deletedAt": "x"},
            ]
        }
        out = IF._new_item_lines(inv)
        assert [ln["id"] for ln in out] == ["a"]

    def test_classify_labels_lines(self, monkeypatch):
        def fake(**kw):
            assert "Classify each supplier invoice line" in kw["system_prompt"]
            ids = _ids_in(kw["user_prompt"])
            label = {"L1": "food", "L2": "beverage", "L3": "cleaning"}
            return ({"classes": [{"line_id": i, "class": label[i]} for i in ids]}, None)

        monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm", fake)
        lines = [
            {"id": "L1", "description": "Chicken", "brand": ""},
            {"id": "L2", "description": "Coke", "brand": ""},
            {"id": "L3", "description": "Bleach", "brand": ""},
        ]
        out = IF._classify_item_lines(lines, db=None)
        # unknown labels collapse to "other"
        assert out == {"L1": "food", "L2": "beverage", "L3": "other"}

    def test_classify_empty_on_error(self, monkeypatch):
        def boom(**kw):
            raise RuntimeError("breaker open")

        monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm", boom)
        assert (
            IF._classify_item_lines(
                [{"id": "L1", "description": "x", "brand": ""}], None
            )
            == {}
        )

    def test_match_subset_maps_index_and_suggestion(self, monkeypatch):
        cands = [
            {
                "id": "i0",
                "name": "SPIANATA PICCANTE",
                "groupName": "Meats",
                "orderingUnitId": "u-kilo",
                "suppliers": [
                    {
                        "supplierId": "s",
                        "unitId": "u-kilo",
                        "unitCost": 10,
                        "defaultForSupplier": True,
                    }
                ],
            },
            {"id": "i1", "name": "OIL CANOLA", "groupName": "Oils"},
        ]
        groups = [
            {"id": "g-meat", "name": "Meats", "category": "Food"},
            {"id": "g-oil", "name": "Oils", "category": "Food"},
        ]
        lines = [
            {
                "id": "L1",
                "description": "Spianata Piccante 2kg C6",
                "code": "CHS.009",
                "brand": "",
                "unit": "KGM",
            },
            {
                "id": "L2",
                "description": "Mystery Product",
                "code": "X",
                "brand": "",
                "unit": "Each",
            },
        ]

        def fake(**kw):
            return (
                {
                    "matches": [
                        {
                            "line_id": "L1",
                            "match_index": 0,
                            "suggested_name": None,
                            "suggested_group_index": None,
                        },
                        {
                            "line_id": "L2",
                            "match_index": None,
                            "suggested_name": "Mystery Product",
                            "suggested_group_index": 1,
                        },
                    ]
                },
                None,
            )

        monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm", fake)
        out = IF._match_subset(lines, groups, cands, db=None)
        assert out["L1"]["matched_item"]["id"] == "i0"
        assert out["L1"]["matched_item"]["unit_id"] == "u-kilo"
        assert out["L1"]["matched_item"]["unit_cost"] == 10
        assert out["L2"]["matched_item"] is None
        assert out["L2"]["suggested_name"] == "Mystery Product"
        assert out["L2"]["suggested_group_id"] == "g-oil"  # index 1

    def test_match_subset_ignores_out_of_range_index(self, monkeypatch):
        cands = [{"id": "i0", "name": "A", "groupName": "G"}]
        groups = [{"id": "g", "name": "G", "category": "Food"}]
        lines = [{"id": "L1", "description": "A", "code": "", "brand": "", "unit": ""}]

        def fake(**kw):
            return (
                {
                    "matches": [
                        {
                            "line_id": "L1",
                            "match_index": 9,
                            "suggested_name": "A",
                            "suggested_group_index": 9,
                        }
                    ]
                },
                None,
            )

        monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm", fake)
        out = IF._match_subset(lines, groups, cands, db=None)
        assert out["L1"]["matched_item"] is None
        assert out["L1"]["suggested_group_id"] is None

    def test_match_routes_by_department(self, monkeypatch):
        # food0 is a food item, bev0 a beverage item. A food-classified line must
        # only ever see the food slice, so its index-0 match resolves to food0.
        cands = [
            {
                "id": "food0",
                "name": "CHICKEN",
                "groupId": "g-meat",
                "groupName": "Meats",
            },
            {"id": "bev0", "name": "COKE", "groupId": "g-bot", "groupName": "Bottled"},
        ]
        groups = [
            {"id": "g-meat", "name": "Meats", "category": "Food"},
            {"id": "g-bot", "name": "Bottled", "category": "Beverage"},
        ]
        lines = [
            {
                "id": "L1",
                "description": "Chicken Breast",
                "code": "",
                "brand": "",
                "unit": "",
            },
            {
                "id": "L2",
                "description": "Coca Cola",
                "code": "",
                "brand": "",
                "unit": "",
            },
        ]

        def fake(**kw):
            ids = _ids_in(kw["user_prompt"])
            if "Classify each supplier invoice line" in kw["system_prompt"]:
                cls = {"L1": "food", "L2": "beverage"}
                return (
                    {"classes": [{"line_id": i, "class": cls[i]} for i in ids]},
                    None,
                )
            # each subset call sees exactly one line; index 0 of its own slice
            return (
                {
                    "matches": [
                        {
                            "line_id": i,
                            "match_index": 0,
                            "suggested_name": None,
                            "suggested_group_index": None,
                        }
                        for i in ids
                    ]
                },
                None,
            )

        monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm", fake)
        out = IF._match_stock_items(lines, groups, cands, db=None)
        assert out["L1"]["matched_item"]["id"] == "food0"
        assert out["L2"]["matched_item"]["id"] == "bev0"

    def test_match_empty_on_error(self, monkeypatch):
        def boom(**kw):
            raise RuntimeError("down")

        monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm", boom)
        cands = [{"id": "i0", "name": "A", "groupId": "g", "groupName": "G"}]
        groups = [{"id": "g", "name": "G", "category": "Food"}]
        lines = [{"id": "L1", "description": "A", "code": "", "brand": "", "unit": ""}]
        # classify fails → all "other" → match fails → {}
        assert IF._match_stock_items(lines, groups, cands, db=None) == {}


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


class TestStruckAndAddedLines:
    """Accepted remove/add suggestions ride the receive: a struck line is
    soft-deleted in Loaded (deletedAt) and an added line carries the tax rate
    the engine stamped on the suggestion."""

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

    def _req(self, **over):
        import app.routers.invoice_fixes as IF2

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
        return IF2.ReceiveRequest(**base)

    def test_struck_line_is_soft_deleted(self):
        lh = self._lh()
        out = IF._do_receive(
            lh, self._req(lines=[{"id": "ln-1", "struck": True}], receive=False)
        )
        assert out["ok"]
        body = [w for w in lh.writes if w[0] == "PUT"][0][2]
        assert body["lines"][0]["deletedAt"]

    def test_added_line_carries_sale_tax_rate(self):
        lh = self._lh()
        IF._do_receive(
            lh,
            self._req(
                lines=[
                    {
                        "id": "new-1754424000000",
                        "code": None,
                        "description": "Atlanta Bright IPA 4.6% 50L Keg",
                        "linked_item_id": "item-2",
                        "linked_unit_id": "u-each",
                        "unit": "Each",
                        "unit_ratio": 1,
                        "quantity_received": 1,
                        "unit_cost": 340.0,
                        "total_cost": 340.0,
                        "sale_tax_rate": 0.15,
                    }
                ]
            ),
        )
        body = [w for w in lh.writes if w[0] == "PUT"][0][2]
        added = body["lines"][1]
        assert "id" not in added  # Loaded assigns the real id
        assert added["saleTaxRate"] == 0.15
        assert added["linkedItemId"] == "item-2"
        assert added["unitCostExclTax"] == 340.0


class TestReshapeCarryOver:
    """_reshape_draft_after_write must preserve local line state: struck flags
    (accepted remove/strike suggestions) and locally-added 'new-' lines."""

    def test_struck_and_added_lines_survive_reshape(self, db_session, admin_user):
        import uuid

        from app.db.models import Venue, WorkingDocument

        venue = Venue(id=str(uuid.uuid4()), name=f"Venue {uuid.uuid4().hex[:6]}")
        db_session.add(venue)
        db_session.flush()
        doc = WorkingDocument(
            doc_type="received_invoice",
            connector_name="loadedhub",
            venue_id=venue.id,
            external_ref={"invoice_id": "inv-1"},
            data={
                "lines": [
                    {"id": "ln-1", "description": "OLD", "struck": True},
                    {
                        "id": "new-1754424000000",
                        "description": "LOCAL ADD",
                        "sale_tax_rate": 0.15,
                    },
                ],
                "actioned_suggestions": [{"key": "remove:ln-1", "summary": "removed"}],
            },
        )
        db_session.add(doc)
        db_session.flush()

        lh = FakeLoaded(
            {},
            {
                "inv-1": {
                    "id": "inv-1",
                    "referenceNumber": "R-1",
                    "linkedSupplierId": "sup-1",
                    "lines": [
                        {"id": "ln-1", "description": "FRESH", "linkedItemId": None}
                    ],
                }
            },
        )
        out = IF._reshape_draft_after_write(db_session, lh, venue.id, "inv-1")
        lines = out["data"]["lines"]
        by_id = {ln.get("id"): ln for ln in lines}
        assert by_id["ln-1"].get("struck") is True
        assert "new-1754424000000" in by_id
        assert by_id["new-1754424000000"]["description"] == "LOCAL ADD"
        assert out["data"]["actioned_suggestions"] == [
            {"key": "remove:ln-1", "summary": "removed"}
        ]


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

    def test_newly_linked_line_registers_variant_and_links(self):
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
        out = IF._do_receive(
            lh,
            self._req(
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

    def test_already_linked_line_registers_nothing(self):
        inv = self._inv()
        inv["lines"][0]["linkedItemId"] = "item-2"
        inv["lines"][0]["linkedUnitId"] = "u-150"
        lh = FakeLoaded(
            {"/1.0/stock/internal/purchase-orders": PO_LIST}, {"inv-1": inv}
        )
        IF._do_receive(
            lh,
            self._req(
                lines=[
                    {"id": "ln-1", "linked_item_id": "item-2", "quantity_received": 1}
                ]
            ),
        )
        assert not [w for w in lh.writes if "/items/" in w[1]]

    def test_registration_failure_never_blocks_receive(self):
        # No canned item route -> FakeLoaded raises inside registration; the
        # receive itself must still succeed (link still lands via the PUT).
        lh = FakeLoaded(
            {"/1.0/stock/internal/purchase-orders": PO_LIST}, {"inv-1": self._inv()}
        )
        out = IF._do_receive(
            lh,
            self._req(
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
