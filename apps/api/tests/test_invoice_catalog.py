"""Creating what an invoice line needs, by hand — services/invoice_catalog and
the web card's endpoints over it.

The card used to create only what the AI proposed: an item only on a line with
no suggestion and a unit already resolved, a unit only when the copy named one
(sized by the LLM, never by the person), a brand only when suggested — and no
way to pick, change or clear a brand at all. These tests hold the manual paths
open:

- an item can be made for ANY line the draft holds, including one Loaded's
  scan missed, under the supplier the person picked in the card;
- a same-named item already in Loaded is linked, never duplicated;
- a brand the person picks or clears reaches Loaded's line, name and record.
"""

import uuid

import pytest

from app.db.models import WorkingDocument
from app.routers import invoice_fixes as IF
from app.services import invoice_catalog as CAT
from app.services import received_invoice as RI

UNITS = [
    {"id": "u-each", "name": "Each", "ratio": 1.0, "stockUnitType": "Count"},
    {"id": "u-kilo", "name": "Kilo", "ratio": 1.0, "stockUnitType": "Weight"},
    {"id": "u-6pk", "name": "6 pack", "ratio": 6.0, "stockUnitType": "Count"},
]


class FakeLoaded:
    """Loaded's catalogue + one invoice; records every write."""

    def __init__(self, invoice=None, brands=(), created=None):
        self._invoice = invoice or {"id": "inv-1", "lines": []}
        self._brands = list(brands)
        self._created = created
        self.writes = []

    def get(self, path):
        if path == "/1.0/stock/internal/units":
            return UNITS
        if path == "/1.0/stock/internal/brands":
            return self._brands
        raise AssertionError(f"unexpected GET {path}")

    def invoice(self, invoice_id):
        assert invoice_id == self._invoice["id"]
        return self._invoice

    def request(self, method, path, body=None):
        self.writes.append((method, path, body))
        if self._created is not None:
            return self._created
        return {"id": "new-1", "name": (body or {}).get("name")}


def _draft(db, venue_id, invoice_id, data):
    doc = WorkingDocument(
        id=str(uuid.uuid4()),
        thread_id=None,
        doc_type="received_invoice",
        connector_name="loadedhub",
        venue_id=venue_id,
        sync_mode="submit",
        data={"invoice_id": invoice_id, "is_received": False, **data},
        external_ref={"invoice_id": invoice_id, "venue_id": venue_id},
        sync_status="synced",
        version=1,
    )
    db.add(doc)
    db.flush()
    return doc


@pytest.fixture
def vid(db_session):
    """A real venue — the drafts reference it."""
    from app.db.models import Venue

    v = Venue(id=str(uuid.uuid4()), name="La Zeppa", timezone="Pacific/Auckland")
    db_session.add(v)
    db_session.flush()
    return v.id


@pytest.fixture
def no_catalogue(monkeypatch):
    """An empty stock catalogue (the resolve-first lookup)."""
    monkeypatch.setattr(CAT, "stock_items", lambda venue_id, db, cdb: [])


def _post_items(lh):
    return [w for w in lh.writes if w[0] == "POST" and w[1].endswith("/items")]


class TestCreateItem:
    def _create(self, db, lh, vid, **kw):
        args = {
            "venue_id": vid,
            "invoice_id": "inv-1",
            "line_id": "rep-3",
            "name": "Lime Juice",
            "group_id": "g-1",
            "unit_id": "u-6pk",
        }
        args.update(kw)
        return CAT.create_item(lh, db, None, **args)

    def test_a_line_loadeds_scan_missed_can_get_an_item(
        self, db_session, vid, no_catalogue
    ):
        # 'rep-3' exists only in the draft — the copy has it, Loaded's OCR did
        # not. This used to 404 "invoice line not found": the line was read
        # from Loaded's invoice.
        _draft(
            db_session,
            vid,
            "inv-1",
            {
                "linked_supplier_id": "sup-card",
                "lines": [
                    {
                        "id": "rep-3",
                        "code": "LJ6",
                        "description": "LIME JUICE 6PK",
                        "unit_cost": 23.0,
                        "sale_tax_rate": 0.15,
                    }
                ],
            },
        )
        lh = FakeLoaded()
        out = self._create(db_session, lh, vid)

        assert out["created"] is True and out["item_id"] == "new-1"
        [(_, _, body)] = _post_items(lh)
        variant = body["suppliers"][0]
        assert (variant["stockCode"], variant["description"]) == (
            "LJ6",
            "LIME JUICE 6PK",
        )
        assert variant["unitCost"] == 23.0
        assert variant["unitId"] == "u-6pk"

    def test_the_supplier_picked_in_the_card_is_the_items_supplier(
        self, db_session, vid, no_catalogue
    ):
        # Picked in the card, the supplier reaches Loaded's invoice only on
        # receive — Loaded's header is still empty here.
        _draft(
            db_session,
            vid,
            "inv-1",
            {
                "linked_supplier_id": "sup-card",
                "lines": [{"id": "rep-3", "code": "LJ6", "unit_cost": 1}],
            },
        )
        lh = FakeLoaded(invoice={"id": "inv-1", "linkedSupplierId": None})
        self._create(db_session, lh, vid)
        [(_, _, body)] = _post_items(lh)
        assert body["defaultSupplierId"] == "sup-card"
        assert body["suppliers"][0]["supplierId"] == "sup-card"

    def test_an_inclusive_cost_is_stored_ex_tax(self, db_session, vid, no_catalogue):
        _draft(
            db_session,
            vid,
            "inv-1",
            {
                "linked_supplier_id": "sup-1",
                "unit_cost_includes_tax": True,
                "lines": [{"id": "rep-3", "unit_cost": 23.0, "sale_tax_rate": 0.15}],
            },
        )
        lh = FakeLoaded()
        self._create(db_session, lh, vid)
        [(_, _, body)] = _post_items(lh)
        assert body["suppliers"][0]["unitCost"] == pytest.approx(20.0)

    def test_without_a_draft_loadeds_line_and_supplier_are_used(
        self, db_session, vid, no_catalogue
    ):
        lh = FakeLoaded(
            invoice={
                "id": "inv-1",
                "linkedSupplierId": "sup-loaded",
                "lines": [
                    {
                        "id": "ln-1",
                        "code": "NAP",
                        "description": "Napkins",
                        "unitCostExclTax": 4.5,
                    }
                ],
            }
        )
        self._create(db_session, lh, vid, line_id="ln-1")
        [(_, _, body)] = _post_items(lh)
        assert body["defaultSupplierId"] == "sup-loaded"
        assert body["suppliers"][0]["unitCost"] == 4.5

    def test_no_supplier_anywhere_is_refused_before_any_write(
        self, db_session, vid, no_catalogue
    ):
        _draft(db_session, vid, "inv-1", {"lines": [{"id": "rep-3"}]})
        lh = FakeLoaded(invoice={"id": "inv-1", "linkedSupplierId": None})
        with pytest.raises(CAT.CatalogError, match="supplier"):
            self._create(db_session, lh, vid)
        assert lh.writes == []

    def test_a_unit_is_required(self, db_session, vid, no_catalogue):
        _draft(db_session, vid, "inv-1", {"lines": [{"id": "rep-3"}]})
        lh = FakeLoaded()
        with pytest.raises(CAT.CatalogError, match="unit"):
            self._create(db_session, lh, vid, unit_id=None)
        assert lh.writes == []

    def test_a_same_named_item_is_linked_not_duplicated(
        self, db_session, vid, monkeypatch
    ):
        monkeypatch.setattr(
            CAT,
            "stock_items",
            lambda venue_id, db, cdb: [{"id": "item-9", "name": "Lime  juice"}],
        )
        _draft(
            db_session,
            vid,
            "inv-1",
            {"linked_supplier_id": "sup-1", "lines": [{"id": "rep-3"}]},
        )
        lh = FakeLoaded()
        out = self._create(db_session, lh, vid)
        assert out["created"] is False
        assert (out["item_id"], out["item_name"]) == ("item-9", "Lime  juice")
        assert lh.writes == []

    def test_autopilot_mid_pass_reads_the_draft_it_holds(
        self, db_session, vid, no_catalogue
    ):
        # A supplier autopilot created a moment ago is in memory, not yet saved.
        held = {"linked_supplier_id": "sup-just-made", "lines": [{"id": "rep-3"}]}
        lh = FakeLoaded()
        CAT.create_item(
            lh,
            db_session,
            None,
            venue_id="v-1",
            invoice_id="inv-1",
            line_id="rep-3",
            name="Limes",
            group_id="g-1",
            unit_id="u-each",
            doc_data=held,
        )
        [(_, _, body)] = _post_items(lh)
        assert body["defaultSupplierId"] == "sup-just-made"


class TestBrand:
    def test_an_existing_brand_is_returned(self):
        lh = FakeLoaded(brands=[{"id": "b-1", "name": "Bundaberg"}])
        brand, created = CAT.get_or_create_brand(lh, " bundaberg ")
        assert (brand, created) == ({"id": "b-1", "name": "Bundaberg"}, False)
        assert lh.writes == []

    def test_a_new_brand_is_created(self):
        lh = FakeLoaded()
        brand, created = CAT.get_or_create_brand(lh, "Bundaberg")
        assert created is True and brand["id"] == "new-1"
        assert lh.writes == [
            ("POST", "/1.0/stock/internal/brands", {"name": "Bundaberg"})
        ]


class TestSuggestUnit:
    def test_an_existing_unit_comes_back_instead_of_a_spec(self):
        out = CAT.suggest_unit(FakeLoaded(), "6 Pack", None)
        assert out["existing"]["id"] == "u-6pk"
        assert out["spec"] is None

    def test_a_new_name_comes_back_read(self, monkeypatch):
        monkeypatch.setattr(
            CAT, "llm_unit_spec", lambda n, db: pytest.fail("LLM asked")
        )
        out = CAT.suggest_unit(FakeLoaded(), "6x700ml", None)
        assert out == {
            "existing": None,
            "spec": {"ratio": 4.2, "stock_unit_type": "Volume"},
        }


class TestBrandChoiceTravels:
    """A brand picked or cleared in the card reaches the receive request and
    survives a reshape (do_receive's side: test_invoice_fixes_handler)."""

    def test_the_choice_travels_from_the_draft(self):
        req = RI.receive_request_from_doc(
            {
                "lines": [
                    {
                        "id": "ln-1",
                        "brand": None,
                        "linked_brand_id": None,
                        "brand_set": True,
                    }
                ]
            },
            "v-1",
            "inv-1",
        )
        assert req.lines[0]["brand_set"] is True

    def test_a_reshape_keeps_the_choice(self):
        fresh = {"lines": [{"id": "ln-1", "brand": "BIDFOOD", "linked_brand_id": None}]}
        old = {
            "lines": [
                {
                    "id": "ln-1",
                    "brand": None,
                    "linked_brand_id": None,
                    "brand_set": True,
                }
            ]
        }
        RI.carry_local_state(fresh, old)
        assert fresh["lines"][0]["brand"] is None
        assert fresh["lines"][0]["brand_set"] is True


class TestEndpoints:
    def _fake(self, monkeypatch, lh):
        monkeypatch.setattr(IF, "_Loaded", lambda db, cdb, vid: lh)

    def test_create_item_passes_the_cards_supplier(
        self, client, admin_headers, monkeypatch
    ):
        seen = {}

        def _create(lh, db, cdb, **kw):
            seen.update(kw)
            return {
                "created": True,
                "item_id": "i-1",
                "item_name": kw["name"],
                "unit_id": kw["unit_id"],
                "unit_name": "6 pack",
                "unit_ratio": 6.0,
            }

        self._fake(monkeypatch, FakeLoaded())
        monkeypatch.setattr(CAT, "create_item", _create)
        r = client.post(
            "/api/invoice-fixes/create-item",
            headers=admin_headers,
            json={
                "venue_id": "v-1",
                "invoice_id": "inv-1",
                "line_id": "rep-3",
                "group_id": "g-1",
                "name": "Lime Juice",
                "unit_id": "u-6pk",
                "supplier_id": "sup-card",
            },
        )
        assert r.status_code == 200, r.text
        assert seen["supplier_id"] == "sup-card"
        assert r.json()["created"] is True

    def test_a_catalogue_refusal_is_the_persons_to_read(
        self, client, admin_headers, monkeypatch
    ):
        def _refuse(*a, **k):
            raise CAT.CatalogError(400, "choose the invoice's supplier first")

        self._fake(monkeypatch, FakeLoaded())
        monkeypatch.setattr(CAT, "create_item", _refuse)
        r = client.post(
            "/api/invoice-fixes/create-item",
            headers=admin_headers,
            json={
                "venue_id": "v-1",
                "invoice_id": "inv-1",
                "line_id": "x",
                "group_id": "g",
            },
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "choose the invoice's supplier first"

    def test_create_unit_takes_the_persons_size(
        self, client, admin_headers, monkeypatch
    ):
        lh = FakeLoaded(
            created={
                "id": "u-case",
                "name": "Case",
                "ratio": 24.0,
                "stockUnitType": "Count",
            }
        )
        self._fake(monkeypatch, lh)
        r = client.post(
            "/api/invoice-fixes/create-unit",
            headers=admin_headers,
            json={
                "venue_id": "v-1",
                "invoice_id": "inv-1",
                "name": "Case",
                "ratio": 24,
                "unit_type": "Count",
            },
        )
        assert r.status_code == 200, r.text
        assert lh.writes == [
            (
                "POST",
                "/1.0/stock/internal/units",
                {"name": "Case", "ratio": 24.0, "stockUnitType": "Count"},
            )
        ]
        assert r.json()["unit_id"] == "u-case"

    def test_unit_spec_and_brands_reads(self, client, admin_headers, monkeypatch):
        self._fake(monkeypatch, FakeLoaded(brands=[{"id": "b-1", "name": "Bundy"}]))
        r = client.get(
            "/api/invoice-fixes/unit-spec?venue_id=v-1&name=500g", headers=admin_headers
        )
        assert r.json() == {
            "existing": None,
            "spec": {"ratio": 0.5, "stock_unit_type": "Weight"},
        }
        r = client.get("/api/invoice-fixes/brands?venue_id=v-1", headers=admin_headers)
        assert r.json() == {"brands": [{"id": "b-1", "name": "Bundy"}]}


class _SupplierLoaded:
    """Loaded's supplier list; records every write."""

    def __init__(self, rows):
        self._rows = rows
        self.writes = []

    def get(self, path):
        assert path == "/1.0/stock/internal/suppliers"
        return self._rows

    def request(self, method, path, body=None):
        self.writes.append((method, path, body))
        return {"id": "s-new", "name": (body or {}).get("name")}


class TestANewSupplierTakesItsInvoiceDetails:
    """A supplier Norm creates from an invoice carries the address and
    contacts that invoice prints. Onboarding a venue onto Loaded through
    invoice autopilot (14 Oct 2026) creates most of its suppliers this way,
    and a bare name left every record to be filled in by hand."""

    DETAILS = {
        "address_lines": ["Unit 4", "12 Wharf Road"],
        "city": "Auckland",
        "postcode": "1010",
        "phone": "09 555 0101",
        "email": "orders@tickety.co.nz",
        "contact_name": "Mere Smith",
        "account_number": "CB-0042",
    }

    def _suggestion(self, details):
        return {
            "id": "create_supplier:linked_supplier_id",
            "kind": "create_supplier",
            "payload": {"supplier_name": "Tickety-Boo Ltd", "details": details},
        }

    def test_the_details_land_in_loadeds_fields(self):
        assert CAT.supplier_body("Tickety-Boo Ltd", self.DETAILS) == {
            "name": "Tickety-Boo Ltd",
            "address1": "Unit 4",
            "address2": "12 Wharf Road",
            "city": "Auckland",
            "postcode": "1010",
            "phone1": "09 555 0101",
            "contactName": "Mere Smith",
            "customerNumber": "CB-0042",
            "emails": [{"email": "orders@tickety.co.nz", "isPrimary": True}],
        }

    def test_nothing_read_is_a_bare_name(self):
        assert CAT.supplier_body("X Ltd") == {"name": "X Ltd"}
        assert CAT.supplier_body("X Ltd", {}) == {"name": "X Ltd"}

    def test_purchase_orders_are_never_switched_to_email(self):
        # emailEnabled makes Loaded EMAIL every purchase order to that address
        # — and an invoice prints the accounts address as often as the
        # orders one. Costs stay ex tax: Norm reads every invoice that way.
        body = CAT.supplier_body("X Ltd", self.DETAILS)
        assert "emailEnabled" not in body
        assert "unitCostIncludesTax" not in body

    def test_an_existing_record_is_never_touched(self):
        lh = _SupplierLoaded([{"id": "s-1", "name": "Tickety-Boo"}])
        sup, created = CAT.get_or_create_supplier(lh, "Tickety-Boo Ltd", self.DETAILS)
        assert (sup["id"], created) == ("s-1", False)
        assert lh.writes == []

    def test_the_card_create_takes_what_the_review_read(
        self, client, admin_headers, monkeypatch, db_session, vid
    ):
        _draft(
            db_session,
            vid,
            "inv-1",
            {"lines": [], "suggestions": [self._suggestion(self.DETAILS)]},
        )
        lh = _SupplierLoaded([{"id": "s-9", "name": "Bidfood"}])
        monkeypatch.setattr(IF, "_Loaded", lambda db, cdb, v: lh)
        r = client.post(
            "/api/invoice-fixes/create-supplier",
            headers=admin_headers,
            json={"venue_id": vid, "invoice_id": "inv-1", "name": "Tickety-Boo Ltd"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["created"] is True
        assert lh.writes == [
            (
                "POST",
                "/1.0/stock/internal/suppliers",
                CAT.supplier_body("Tickety-Boo Ltd", self.DETAILS),
            )
        ]

    def test_another_venues_draft_lends_nothing(self, db_session, vid):
        _draft(
            db_session,
            vid,
            "inv-1",
            {"lines": [], "suggestions": [self._suggestion(self.DETAILS)]},
        )
        assert CAT.draft_supplier_details(db_session, vid, "inv-1") == self.DETAILS
        assert CAT.draft_supplier_details(db_session, "other-venue", "inv-1") == {}
        assert CAT.draft_supplier_details(db_session, vid, "inv-2") == {}
        assert CAT.draft_supplier_details(db_session, vid, "") == {}
