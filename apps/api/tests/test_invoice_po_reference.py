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
    candidates_for_code,
    claim_row_by_item,
    enrich_loaded_snapshot,
    order_rows_for,
    seed_working_from_loaded,
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

    def test_a_suggested_split_pre_caches_the_siblings_receipts(self):
        # The doc carries NO split fields yet — the reference is still only a
        # SUGGESTION. Pre-caching must still fetch the sibling's quantities,
        # or accepting it would report every row the sibling delivered as
        # "ordered, not delivered": three items claimed never to have arrived
        # when they arrived on the other invoice (Bidfood 109945345).
        data = _doc(linked_purchase_order_id=None)
        data.pop("po_reference")
        lh = _FakeLoaded(
            {"createdAt": "2026-08-07", "lines": PO_LINES},
            sibling={"lines": [{"code": "RICK#-DEEKB", "quantityReceived": 2}]},
        )
        fetch_po_reference(data, lh, po_id="po-1", sibling_invoice_id="sib-1")
        assert data["po_reference"]["po_id"] == "po-1"
        assert data["po_reference"]["sibling_qty"] == {"rickdeekb": 2}
        # Dark until accepted: the doc claims no order yet.
        project_po_reference(data)
        assert "ordered_received_elsewhere" not in data
        # Accepting the reference is what the suggestion applies.
        data.update({"split_po_id": "po-1", "split_sibling_invoice_id": "sib-1"})
        project_po_reference(data)
        assert _not_delivered(data) == []
        assert [o["code"] for o in data["ordered_received_elsewhere"]] == [
            "RICK#-DEEKB"
        ]

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
    """The Loaded X-ray must show what Loaded's SCREEN shows.

    Loaded's API returns no resolved names — even ``/invoices/{id}/initial``,
    which is what the screen loads, gives `description: "Beef Bones"`,
    `linkedItemId: null`. The screen resolves the stock item in the browser
    from the CATALOGUE by supplier code (``resolve_loaded_line``, ported from
    mercury's ``i9e``). The purchase order supplies only the ordered quantity.
    """

    SUP = "sup-angus"
    # The live Angus Meats catalogue: THREE items carry code BONES for this
    # supplier. Order matters — Loaded takes the first and stops.
    CATALOGUE = [
        {
            "id": "item-bones",
            "name": "BONES (STOCK)",
            "suppliers": [
                {
                    "supplierId": SUP,
                    "stockCode": "BONES",
                    "unitId": "u-kilo",
                    "defaultForSupplier": False,
                }
            ],
        },
        {
            "id": "item-marrow-1inch",
            "name": "BONE MARROW 1 INCH",
            "suppliers": [
                {
                    "supplierId": SUP,
                    "stockCode": "BONES",
                    "unitId": "u-kilo",
                    "defaultForSupplier": True,
                }
            ],
        },
        {
            "id": "item-marrow-canoe",
            "name": "BONE MARROW - CANOE CUT",
            "suppliers": [
                {
                    "supplierId": SUP,
                    "stockCode": "BONES",
                    "unitId": "u-kilo",
                    "defaultForSupplier": False,
                }
            ],
        },
    ]
    UNITS = [{"id": "u-kilo", "name": "Kilo"}]

    def _doc_1010951(self, **over):
        """Angus Meats 1010951: two invoice lines, same code, neither linked;
        the PO's BONES row points at BONE MARROW 1 INCH, qty 14."""
        data = {
            "linked_purchase_order_id": "po-1",
            "linked_supplier_id": self.SUP,
            "po_reference": {
                "po_id": "po-1",
                "order_date": "2026-08-10",
                "lines": [
                    {
                        "itemCode": None,
                        "itemName": "BONE MARROW - CANOE CUT",
                        "unitName": "Kilo",
                        "quantityOrdered": 6,
                        "unitCost": 6.39,
                        "itemId": "item-marrow-canoe",
                    },
                    {
                        "itemCode": "BONES",
                        "itemName": "BONE MARROW 1 INCH",
                        "unitName": "Kilo",
                        "quantityOrdered": 14,
                        "unitCost": 6.39,
                        "itemId": "item-marrow-1inch",
                    },
                ],
                "sibling_qty": {},
            },
            "lines": [
                {"id": "ld-1", "code": "BONES", "description": "Beef Bones"},
                {"id": "ld-2", "code": "BONES", "description": "Beef Bones"},
            ],
            "loaded_snapshot": {
                "header": {
                    "linked_purchase_order_id": "po-1",
                    "linked_supplier_id": self.SUP,
                },
                "lines": [
                    {
                        "id": "ld-1",
                        "code": "BONES",
                        "description": "Beef Bones",
                        "unit": "KG",
                        "linked_unit_id": None,
                        "linked_item_id": None,
                        "quantity_received": 6.43,
                    },
                    {
                        "id": "ld-2",
                        "code": "BONES",
                        "description": "Beef Bones",
                        "unit": "KG",
                        "linked_unit_id": None,
                        "linked_item_id": None,
                        "quantity_received": 14.15,
                    },
                ],
            },
        }
        data.update(over)
        return data

    def _enrich(self, data, catalogue=None, units=None):
        enrich_loaded_snapshot(
            data,
            catalogue=self.CATALOGUE if catalogue is None else catalogue,
            units=self.UNITS if units is None else units,
        )
        return data["loaded_snapshot"]["lines"]

    def test_catalogue_order_wins_over_the_po_and_over_a_later_default(self):
        # THE case (Angus Meats 1010951). The PO says BONE MARROW 1 INCH and
        # that item is defaultForSupplier — Loaded still shows BONES (STOCK),
        # because it takes the FIRST catalogue item carrying the code.
        lines = self._enrich(self._doc_1010951())
        assert [ln["item_name"] for ln in lines] == ["BONES (STOCK)"] * 2
        assert all(ln["item_is_new"] is False for ln in lines)

    def test_matching_needs_no_purchase_order(self):
        data = self._doc_1010951()
        data.pop("po_reference")
        data["linked_purchase_order_id"] = None
        data["loaded_snapshot"]["header"]["linked_purchase_order_id"] = None
        lines = self._enrich(data)
        assert [ln["item_name"] for ln in lines] == ["BONES (STOCK)"] * 2
        assert [ln["quantity_ordered"] for ln in lines] == [None, None]

    def test_default_for_supplier_breaks_ties_inside_one_item(self):
        # The flag governs ORDERING, and Loaded only consults it among the
        # variants of the item it already picked — never across items.
        catalogue = [
            {
                "id": "item-one",
                "name": "ONE ITEM, TWO VARIANTS",
                "suppliers": [
                    {"supplierId": self.SUP, "stockCode": "BONES", "unitId": "u-x"},
                    {
                        "supplierId": self.SUP,
                        "stockCode": "BONES",
                        "unitId": "u-kilo",
                        "defaultForSupplier": True,
                    },
                ],
            }
        ]
        lines = self._enrich(self._doc_1010951(), catalogue=catalogue)
        assert lines[0]["item_name"] == "ONE ITEM, TWO VARIANTS"
        assert lines[0]["unit_name"] == "Kilo"  # the default variant's unit

    def test_ordered_quantity_is_claimed_by_the_first_line_only(self):
        # Loaded shows 14 then 0 — one order row cannot be delivered twice.
        lines = self._enrich(self._doc_1010951())
        assert [ln["quantity_ordered"] for ln in lines] == [14, None]

    def test_no_catalogue_never_guesses(self):
        lines = self._enrich(self._doc_1010951(), catalogue=[], units=[])
        assert [ln["item_name"] for ln in lines] == [None, None]
        assert all(ln["item_is_new"] is False for ln in lines)  # unknown, not NEW

    def test_unresolvable_code_is_marked_new(self):
        data = self._doc_1010951()
        for ln in data["loaded_snapshot"]["lines"]:
            ln["code"] = "NOT-IN-CATALOGUE"
        lines = self._enrich(data)
        assert lines[0]["item_name"] is None and lines[0]["item_is_new"] is True
        assert lines[0]["unit_is_new"] is True  # "KG" with nothing behind it

    def test_a_linked_line_shows_its_linked_item(self):
        data = self._doc_1010951()
        data["loaded_snapshot"]["lines"][0]["linked_item_id"] = "item-marrow-canoe"
        lines = self._enrich(data)
        assert lines[0]["item_name"] == "BONE MARROW - CANOE CUT"
        assert lines[1]["item_name"] == "BONES (STOCK)"  # still code-resolved

    def test_user_edits_never_leak_into_the_mirror(self):
        # The whole point: the working doc is Norm's proposal, the mirror is
        # Loaded's truth.
        data = self._doc_1010951()
        data["lines"][0].update(
            {"linked_item_id": "item-marrow-canoe", "item_name": "USER PICKED THIS"}
        )
        lines = self._enrich(data)
        assert lines[0]["item_name"] == "BONES (STOCK)"
        assert lines[0]["linked_item_id"] is None

    def test_pure_and_idempotent(self):
        data = self._doc_1010951()
        first = [dict(ln) for ln in self._enrich(data)]
        assert self._enrich(data) == first

    def test_no_snapshot_is_a_no_op(self):
        data = _doc()
        enrich_loaded_snapshot(data, catalogue=self.CATALOGUE)
        assert "loaded_snapshot" not in data


class TestSeedWorkingFromLoaded:
    """The draft must OPEN where Loaded's screen opens.

    Loaded's API line is unresolved (`linkedItemId` null, unit the supplier's
    raw text); its screen resolves both from the supplier code, and that is
    what a human sees and receives there. Seeding the working line from the
    raw payload instead made every code-matched line raise a "link this item"
    suggestion for a link Loaded already agrees with.
    """

    SUP = TestLoadedMirror.SUP
    CATALOGUE = TestLoadedMirror.CATALOGUE
    UNITS = [{"id": "u-kilo", "name": "Kilo", "ratio": 1}]

    def _doc(self, **line_over):
        ln = {
            "id": "ld-1",
            "code": "BONES",
            "description": "Beef Bones",
            "unit": "KG",
            "linked_unit_id": None,
            "linked_item_id": None,
            "quantity_received": 6.43,
        }
        ln.update(line_over)
        return {"linked_supplier_id": self.SUP, "lines": [ln]}

    def _seed(self, data):
        seed_working_from_loaded(data, catalogue=self.CATALOGUE, units=self.UNITS)
        return data["lines"][0]

    def test_unlinked_line_takes_loadeds_own_resolution(self):
        ln = self._seed(self._doc())
        assert ln["linked_item_id"] == "item-bones"
        assert ln["item_name"] == "BONES (STOCK)"
        assert ln["item_name_for"] == "item-bones"  # no refetch needed
        assert ln["linked_unit_id"] == "u-kilo"
        assert ln["unit"] == "Kilo"
        assert ln["unit_ratio"] == 1

    def test_the_printed_description_is_never_touched(self):
        # The replica's pairing, item matching and create-item prefill all key
        # off the supplier's printed text.
        assert self._seed(self._doc())["description"] == "Beef Bones"

    def test_an_existing_link_is_never_overwritten(self):
        # Loaded's own link, or the user's — either way it wins. A dismissed
        # suggestion must not come back as a seed.
        ln = self._seed(self._doc(linked_item_id="item-marrow-canoe"))
        assert ln["linked_item_id"] == "item-marrow-canoe"
        assert ln["item_name"] == "BONE MARROW - CANOE CUT"  # named, not relinked
        assert ln["linked_unit_id"] == "u-kilo"  # its variant's unit still fills in

    def test_an_existing_unit_link_is_never_overwritten(self):
        ln = self._seed(self._doc(linked_unit_id="u-5l", unit="5L"))
        assert (ln["linked_unit_id"], ln["unit"]) == ("u-5l", "5L")
        assert ln["linked_item_id"] == "item-bones"  # the item still resolves

    def test_no_catalogue_seeds_nothing(self):
        data = self._doc()
        seed_working_from_loaded(data, catalogue=[], units=self.UNITS)
        assert data["lines"][0]["linked_item_id"] is None
        assert data["lines"][0]["unit"] == "KG"

    def test_unresolvable_code_is_left_alone(self):
        ln = self._seed(self._doc(code="NOT-IN-CATALOGUE"))
        assert ln["linked_item_id"] is None and ln["unit"] == "KG"

    def test_no_supplier_means_no_matching(self):
        # resolve_loaded_line keys on (supplierId, stockCode) — without the
        # supplier there is nothing to match on, and guessing would be wrong.
        data = self._doc()
        data["linked_supplier_id"] = None
        seed_working_from_loaded(data, catalogue=self.CATALOGUE, units=self.UNITS)
        assert data["lines"][0]["linked_item_id"] is None

    def test_idempotent(self):
        data = self._doc()
        first = dict(self._seed(data))
        assert self._seed(data) == first


class TestOrderRowPairing:
    """Loaded pairs an invoice line to an order row by LINKED STOCK ITEM.

    Verified against seven human-received invoices at Bessie & Royals: every
    paired line and row shared an ``itemId``, the line carried the row's
    ordered quantity (SI-396252: ordered 25, delivered 33.1), and freight —
    not on the order — carried none.
    """

    ROWS = [
        {"itemId": "item-a", "itemCode": "AAA", "quantityOrdered": 6},
        {"itemId": "item-b", "itemCode": "BBB", "quantityOrdered": 14},
    ]

    def test_claims_by_item_and_only_once(self):
        claimed: set[int] = set()
        first = claim_row_by_item(self.ROWS, claimed, "item-b")
        assert first["quantityOrdered"] == 14
        # A second line linked to the SAME item cannot deliver the row twice.
        assert claim_row_by_item(self.ROWS, claimed, "item-b") is None
        assert claim_row_by_item(self.ROWS, claimed, "item-a")["quantityOrdered"] == 6

    def test_no_link_no_row(self):
        # Freight: on the invoice, never on the order.
        assert claim_row_by_item(self.ROWS, set(), None) is None
        assert claim_row_by_item(self.ROWS, set(), "item-nope") is None

    def test_rows_only_for_the_currently_linked_order(self):
        data = _doc()
        assert len(order_rows_for(data)) == 2
        data["linked_purchase_order_id"] = "po-OTHER"  # PO picker moved on
        assert order_rows_for(data) == []


class TestCandidatesForCode:
    def test_every_item_carrying_the_code_in_catalogue_order(self):
        got = candidates_for_code(
            TestLoadedMirror.CATALOGUE, TestLoadedMirror.SUP, "BONES"
        )
        assert [i["name"] for i in got] == [
            "BONES (STOCK)",
            "BONE MARROW 1 INCH",
            "BONE MARROW - CANOE CUT",
        ]

    def test_another_supplier_or_code_matches_nothing(self):
        assert (
            candidates_for_code(TestLoadedMirror.CATALOGUE, "sup-other", "BONES") == []
        )
        assert (
            candidates_for_code(TestLoadedMirror.CATALOGUE, TestLoadedMirror.SUP, "X")
            == []
        )
        assert candidates_for_code(TestLoadedMirror.CATALOGUE, None, "BONES") == []
