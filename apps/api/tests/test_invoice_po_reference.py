"""The PO reconciliation projection — server-owned, pure, one writer.

The projection (per-line ordered qty / substitutes, "ordered, not delivered")
is derived from the cached order rows + the CURRENT working lines. It used to
be patched by the editor too; the two writers drifted the moment an accepted
item link was undone — the list stayed empty next to an unlinked line
(INV-958, 10 Aug 2026). These tests pin the derivation and its purity.
"""

import uuid

import pytest

from app.db.models import Venue
from app.services.invoice_po_reference import (
    enrich_loaded_snapshot,
    fetch_po_reference,
    project_po_reference,
)


@pytest.fixture()
def venue(db_session):
    v = Venue(id=str(uuid.uuid4()), name=f"Venue {uuid.uuid4().hex[:6]}")
    db_session.add(v)
    db_session.flush()
    return v


# The live INV-958 shape: a codeless line matched by item id, and a second
# ordered row (RICK#-DEEKB) whose invoice line is not linked to anything.
PO_LINES = [
    {
        "itemId": "item-hazy",
        "itemCode": None,
        "itemName": "Noisy Hazy Pale Ale",
        "unitName": "50 L",
        "quantityOrdered": 2,
        "unitCost": 420.0,
    },
    {
        "itemId": "item-ginger",
        "itemCode": "RICK#-DEEKB",
        "itemName": "Rick# Ginger Beer",
        "unitName": "50 L",
        "quantityOrdered": 2,
        "unitCost": 380.0,
    },
]


def _doc(**over):
    data = {
        "linked_purchase_order_id": "po-1",
        "po_reference": {
            "po_id": "po-1",
            "order_date": "2026-08-07T04:20:28Z",
            "lines": [dict(pl) for pl in PO_LINES],
            "sibling_qty": {},
        },
        "lines": [
            {
                "id": "ld-1",
                "code": None,
                "description": "Noisy Hazy Pale Ale",
                "linked_item_id": "item-hazy",
            },
            {
                "id": "ld-2",
                "code": None,
                "description": "Rick - Ginger Beer -",
                "linked_item_id": None,
            },
        ],
    }
    data.update(over)
    return data


def _not_delivered(data):
    return [o["code"] for o in data.get("ordered_not_received") or []]


class TestProjection:
    def test_unlinked_line_leaves_its_order_row_outstanding(self):
        data = _doc()
        project_po_reference(data)
        assert _not_delivered(data) == ["RICK#-DEEKB"]
        # the matched line takes the order's numbers
        assert data["lines"][0]["quantity_ordered"] == 2
        assert data["lines"][0]["reference_cost"] == 420.0
        assert data["lines"][0]["on_order"] is True
        # this order row is itself codeless, so there is no code to borrow
        assert data["lines"][0]["display_code"] is None
        assert data["lines"][1]["quantity_ordered"] is None
        assert data["order_date"] == "2026-08-07T04:20:28Z"

    def test_linking_the_item_resolves_the_row(self):
        # THE BUG: accepting the item suggestion must clear the row — and it
        # must do so because the projection recomputed, not because a client
        # patched the list.
        data = _doc()
        project_po_reference(data)
        data["lines"][1]["linked_item_id"] = "item-ginger"
        project_po_reference(data)
        assert _not_delivered(data) == []
        assert data["lines"][1]["quantity_ordered"] == 2
        assert data["lines"][1]["display_code"] == "RICK#-DEEKB"  # borrowed

    def test_undoing_the_link_brings_the_row_back(self):
        # The exact regression: undo restores only the line, and the list must
        # follow from it — never stay stale.
        data = _doc()
        data["lines"][1]["linked_item_id"] = "item-ginger"
        project_po_reference(data)
        assert _not_delivered(data) == []
        data["lines"][1]["linked_item_id"] = None  # undo
        project_po_reference(data)
        assert _not_delivered(data) == ["RICK#-DEEKB"]
        assert data["lines"][1]["on_order"] is False

    def test_projection_is_pure(self):
        # No client, no db, no network — it may be called on every patch.
        data = _doc()
        project_po_reference(data)
        first = data["ordered_not_received"]
        project_po_reference(data)
        assert data["ordered_not_received"] == first  # idempotent

    def test_coded_line_delivered_under_another_code_is_a_substitute(self):
        data = _doc()
        data["lines"][1].update({"code": "GINGER-ALT", "linked_item_id": "item-ginger"})
        project_po_reference(data)
        sub = data["lines"][1]["substitute_for"]
        assert sub["code"] == "RICK#-DEEKB" and sub["quantity_ordered"] == 2
        # delivered under another code — NOT also listed as not-delivered
        assert _not_delivered(data) == []

    def test_no_order_clears_everything(self):
        data = _doc(linked_purchase_order_id=None)
        data.pop("po_reference")
        project_po_reference(data)
        assert "ordered_not_received" not in data
        assert "order_date" not in data
        assert all(ln["quantity_ordered"] is None for ln in data["lines"])

    def test_switching_orders_clears_rather_than_lying(self):
        # The PO picker changed the link; the cached rows are another order's.
        data = _doc()
        project_po_reference(data)
        data["linked_purchase_order_id"] = "po-OTHER"
        project_po_reference(data)
        assert "ordered_not_received" not in data
        assert all(ln["quantity_ordered"] is None for ln in data["lines"])

    def test_uncached_order_keeps_last_known_good(self):
        # A legacy draft (or a failed fetch): recomputing is impossible, so
        # the previous projection must survive rather than be destroyed.
        data = _doc()
        project_po_reference(data)
        data.pop("po_reference")
        project_po_reference(data)
        assert _not_delivered(data) == ["RICK#-DEEKB"]


class _FakeLoaded:
    def __init__(self, po, sibling=None):
        self.po = po
        self.sibling = sibling
        self.gets: list[str] = []

    def get(self, path):
        self.gets.append(path)
        return self.po

    def invoice(self, invoice_id):
        if self.sibling is None:
            raise KeyError(invoice_id)
        return self.sibling


class TestFetch:
    def test_caches_rows_for_the_projection(self):
        data = _doc()
        data.pop("po_reference")
        lh = _FakeLoaded({"createdAt": "2026-08-07", "lines": PO_LINES})
        fetch_po_reference(data, lh)
        assert lh.gets == ["/1.0/stock/internal/purchase-orders/po-1"]
        assert data["po_reference"]["po_id"] == "po-1"
        assert len(data["po_reference"]["lines"]) == 2
        project_po_reference(data)
        assert _not_delivered(data) == ["RICK#-DEEKB"]

    def test_bad_fetch_keeps_last_known_good(self):
        data = _doc()
        fetch_po_reference(data, _FakeLoaded(None))
        assert data["po_reference"]["po_id"] == "po-1"  # untouched

    def test_split_order_partitions_the_sibling_receipts(self):
        data = _doc(
            linked_purchase_order_id=None,
            split_po_id="po-1",
            split_sibling_invoice_id="sib-1",
        )
        data.pop("po_reference")
        lh = _FakeLoaded(
            {"createdAt": "2026-08-07", "lines": PO_LINES},
            sibling={
                "lines": [{"code": "RICK#-DEEKB", "quantityReceived": 2}],
            },
        )
        fetch_po_reference(data, lh)
        project_po_reference(data)
        assert _not_delivered(data) == []
        elsewhere = data["ordered_received_elsewhere"]
        assert [o["code"] for o in elsewhere] == ["RICK#-DEEKB"]
        assert elsewhere[0]["quantity_received"] == 2

    def test_unreadable_sibling_never_raises(self):
        data = _doc(
            linked_purchase_order_id=None,
            split_po_id="po-1",
            split_sibling_invoice_id="missing",
        )
        data.pop("po_reference")
        fetch_po_reference(data, _FakeLoaded({"createdAt": "x", "lines": PO_LINES}))
        project_po_reference(data)
        assert _not_delivered(data) == ["RICK#-DEEKB"]  # nothing partitioned


class TestPatchRecomputes:
    """The PATCH path is where the drift happened: the editor patched the
    derived list itself, and undo left it stale. Now every mutation
    recomputes it server-side, on all three patch surfaces."""

    def _doc_row(self, db_session, venue):
        from app.db.models import WorkingDocument

        doc = WorkingDocument(
            doc_type="received_invoice",
            connector_name="loadedhub",
            venue_id=venue.id,
            external_ref={"invoice_id": "inv-1"},
            sync_mode="submit",
            data=_doc(invoice_id="inv-1"),
            version=1,
        )
        db_session.add(doc)
        db_session.flush()
        return doc

    def test_linking_then_undoing_keeps_the_list_honest(
        self, client, db_session, admin_user, admin_headers, venue
    ):
        doc = self._doc_row(db_session, venue)
        db_session.commit()

        # Link the item — the ordered row must resolve WITHOUT the client
        # touching ordered_not_received.
        res = client.patch(
            f"/api/working-documents/{doc.id}",
            json={
                "ops": [
                    {
                        "op": "update_line",
                        "line_id": "ld-2",
                        "index": 1,
                        "fields": {"linked_item_id": "item-ginger"},
                    }
                ],
                "version": doc.version,
            },
            headers=admin_headers,
        )
        assert res.status_code == 200
        body = res.json()["data"]
        assert body["ordered_not_received"] == []
        assert body["lines"][1]["quantity_ordered"] == 2

        # Undo restores only the line — the list must follow (the exact bug).
        res = client.patch(
            f"/api/working-documents/{doc.id}",
            json={
                "ops": [
                    {
                        "op": "update_line",
                        "line_id": "ld-2",
                        "index": 1,
                        "fields": {"linked_item_id": None},
                    }
                ],
                "version": res.json()["version"],
            },
            headers=admin_headers,
        )
        assert res.status_code == 200
        body = res.json()["data"]
        assert [o["code"] for o in body["ordered_not_received"]] == ["RICK#-DEEKB"]

    def test_a_client_patch_cannot_leave_the_list_wrong(
        self, client, db_session, admin_user, admin_headers, venue
    ):
        # Even a doctored patch that empties the list is corrected on the way
        # in — the projection is server-owned, full stop.
        doc = self._doc_row(db_session, venue)
        db_session.commit()
        res = client.patch(
            f"/api/working-documents/{doc.id}",
            json={
                "ops": [
                    {"op": "update_header", "fields": {"ordered_not_received": []}}
                ],
                "version": doc.version,
            },
            headers=admin_headers,
        )
        assert res.status_code == 200
        assert [o["code"] for o in res.json()["data"]["ordered_not_received"]] == [
            "RICK#-DEEKB"
        ]

    def test_other_doc_types_are_untouched(
        self, client, db_session, admin_user, admin_headers, venue
    ):
        from app.db.models import WorkingDocument

        doc = WorkingDocument(
            doc_type="order",
            connector_name="loadedhub",
            venue_id=venue.id,
            external_ref={},
            sync_mode="submit",
            data={"lines": [{"id": "l-1", "quantity": 1}], "ordered_not_received": []},
            version=1,
        )
        db_session.add(doc)
        db_session.commit()
        res = client.patch(
            f"/api/working-documents/{doc.id}",
            json={
                "ops": [{"op": "update_line", "index": 0, "fields": {"quantity": 5}}],
                "version": doc.version,
            },
            headers=admin_headers,
        )
        assert res.status_code == 200
        assert res.json()["data"]["ordered_not_received"] == []  # not an invoice


class TestLoadedMirror:
    """The Loaded X-ray must show what Loaded's SCREEN shows, not what its API
    returns. Loaded sends no item/unit names and a null quantityOrdered — its
    screen resolves them against the linked order (verified live on Angus
    Meats 1010821: PO line BONES → "BONES (STOCK)" / "Kilo" / 10)."""

    @staticmethod
    def _doc_with_snapshot(**over):
        data = _doc(**over)
        # Loaded's raw payload: supplier text, raw unit, no ordered qty. The
        # BONES line is genuinely unlinked in Loaded.
        data["loaded_snapshot"] = {
            "header": {"linked_purchase_order_id": "po-1", "total": 949.17},
            "lines": [
                {
                    "id": "ld-1",
                    "code": None,
                    "description": "Noisy Hazy Pale Ale",
                    "unit": "Kilo",
                    "linked_unit_id": "u-kilo",
                    "linked_item_id": "item-hazy",
                    "item_name": None,
                },
                {
                    "id": "ld-2",
                    "code": "RICK#-DEEKB",
                    "description": "Beef Bones",
                    "unit": "KG",
                    "linked_unit_id": None,
                    "linked_item_id": None,
                    "item_name": None,
                },
            ],
        }
        data["lines"] = [
            {
                "id": "ld-1",
                "code": None,
                "description": "Noisy Hazy Pale Ale",
                "linked_item_id": "item-hazy",
                "item_name": "NOISY HAZY PALE ALE",
            },
            {"id": "ld-2", "code": "RICK#-DEEKB", "linked_item_id": None},
        ]
        return data

    def test_resolves_name_unit_and_ordered_qty_from_the_order(self):
        data = self._doc_with_snapshot()
        enrich_loaded_snapshot(data)
        bones = data["loaded_snapshot"]["lines"][1]
        # the unlinked line resolves through the PO, exactly as Loaded's screen
        assert bones["item_name"] == "Rick# Ginger Beer"
        assert bones["unit_name"] == "50 L"
        assert bones["quantity_ordered"] == 2
        assert bones["item_is_new"] is False
        assert bones["unit_is_new"] is False

    def test_linked_line_keeps_loadeds_own_unit_label(self):
        data = self._doc_with_snapshot()
        enrich_loaded_snapshot(data)
        hazy = data["loaded_snapshot"]["lines"][0]
        assert hazy["item_name"] == "NOISY HAZY PALE ALE"  # borrowed by id
        assert hazy["unit_name"] is None  # raw `unit` is already the label
        assert hazy["quantity_ordered"] == 2

    def test_unresolvable_line_is_marked_new(self):
        data = self._doc_with_snapshot()
        data["loaded_snapshot"]["lines"][1].update(
            {"code": "NOT-ON-THE-ORDER", "unit": "CTN"}
        )
        enrich_loaded_snapshot(data)
        bones = data["loaded_snapshot"]["lines"][1]
        assert bones["item_name"] is None and bones["item_is_new"] is True
        assert bones["unit_is_new"] is True
        assert bones["quantity_ordered"] is None

    def test_user_edits_never_leak_into_the_mirror(self):
        # The whole point of the mirror: the user links the item in the
        # working doc, and Loaded's column still shows Loaded's truth.
        data = self._doc_with_snapshot()
        data["lines"][1].update(
            {"linked_item_id": "item-ginger", "item_name": "USER PICKED THIS"}
        )
        enrich_loaded_snapshot(data)
        bones = data["loaded_snapshot"]["lines"][1]
        assert bones["item_name"] == "Rick# Ginger Beer"  # from the PO, not the edit
        assert bones["linked_item_id"] is None  # snapshot ids untouched

    def test_a_relinked_working_line_cannot_rename_the_mirror(self):
        data = self._doc_with_snapshot()
        # the working line now points at a DIFFERENT item than the snapshot
        data["lines"][0].update(
            {"linked_item_id": "item-other", "item_name": "SOMETHING ELSE"}
        )
        enrich_loaded_snapshot(data)
        # The borrow is refused (ids no longer agree) and the mirror falls back
        # to the ORDER's name for the item the snapshot itself links.
        assert data["loaded_snapshot"]["lines"][0]["item_name"] == "Noisy Hazy Pale Ale"

    def test_rows_from_another_order_are_never_used(self):
        data = self._doc_with_snapshot()
        data["loaded_snapshot"]["header"]["linked_purchase_order_id"] = "po-OTHER"
        enrich_loaded_snapshot(data)
        for ln in data["loaded_snapshot"]["lines"]:
            assert ln["quantity_ordered"] is None

    def test_pure_and_idempotent(self):
        data = self._doc_with_snapshot()
        enrich_loaded_snapshot(data)
        first = [dict(ln) for ln in data["loaded_snapshot"]["lines"]]
        enrich_loaded_snapshot(data)
        assert data["loaded_snapshot"]["lines"] == first

    def test_no_snapshot_is_a_no_op(self):
        data = _doc()
        enrich_loaded_snapshot(data)  # must not raise
        assert "loaded_snapshot" not in data
