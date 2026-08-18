"""catalog_hygiene — venue setups checked against truth and rules.

Findings are read-only: a wrong stocking unit converts recipes and stock
history inside Loaded, so the report names the error and a person repairs it.
"""

import pytest

from app.db.config_models import SupplierInvoiceSpec
from app.services import supplier_catalog as sc


class _FakeLoaded:
    def __init__(self):
        self.units = [
            {"id": "u-each", "name": "Each"},
            {"id": "u-litre", "name": "Litre"},
        ]
        self.suppliers = [{"id": "sup-1", "name": "Trents Wholesale Limited"}]

    def get(self, path):
        if "units" in path:
            return self.units
        if "suppliers" in path:
            return self.suppliers
        return []


ITEMS = [
    {
        "id": "item-shott",
        "name": "SHOTT ELDERFLOWER",
        "groupId": "g-bev",
        "orderingUnitId": "u-each",
        "suppliers": [{"supplierId": "sup-1", "stockCode": "SH1", "unitId": "u-each"}],
    },
    {
        "id": "item-ok",
        "name": "HOUSE GIN",
        "groupId": "g-bev",
        "orderingUnitId": "u-litre",
        "suppliers": [{"supplierId": "sup-1", "stockCode": "G1", "unitId": "u-litre"}],
    },
]
GROUPS = [{"id": "g-bev", "name": "Spirits & Syrups", "category": "Beverage"}]


@pytest.fixture()
def hygiene_env(monkeypatch, db_session):
    from app.routers import catalog_hygiene as CH  # noqa: F401
    from app.services import item_match, received_invoice

    monkeypatch.setattr(
        received_invoice, "LoadedInvoiceClient", lambda *a, **k: _FakeLoaded()
    )
    monkeypatch.setattr(item_match, "_fetch_raw_stock_items", lambda *a, **k: ITEMS)
    monkeypatch.setattr(item_match, "_fetch_stock_groups", lambda lh: GROUPS)
    db_session.add(SupplierInvoiceSpec(name="Trents", aliases=[], instructions="x"))
    db_session.flush()
    return db_session


class TestVenueHygiene:
    def test_catalogue_disagreement_and_category_rule_both_flag(
        self, client, admin_headers, hygiene_env
    ):
        # Truth: the SHOTT product is 1L (printed) — the venue stocks Each.
        sc.observe_extraction(
            hygiene_env,
            {
                "supplier_name": "Trents Wholesale Limited",
                "invoice_number": "A",
                "lines": [
                    {
                        "code": "SH1",
                        "description": "SHOTT ELDERFLOWER",
                        "unit_of_measure": "1L",
                    }
                ],
            },
            provenance="printed",
        )
        res = client.get("/api/supplier-catalog/hygiene/v-1", headers=admin_headers)
        assert res.status_code == 200
        body = res.json()
        assert body["items_scanned"] == 2
        kinds = {(f["kind"], f["item_name"]) for f in body["findings"]}
        # the Shott flag, both ways: truth disagreement AND the beverage rule
        assert ("catalogue_disagrees", "SHOTT ELDERFLOWER") in kinds
        assert ("category_rule", "SHOTT ELDERFLOWER") in kinds
        # a beverage correctly stocked in Litre raises nothing
        assert not any(f["item_name"] == "HOUSE GIN" for f in body["findings"])
        dis = next(f for f in body["findings"] if f["kind"] == "catalogue_disagrees")
        assert dis["current_unit"] == "Each"
        assert dis["expected_unit"] == "1L"

    def test_no_truth_no_disagreement_but_rules_still_apply(
        self, client, admin_headers, hygiene_env
    ):
        res = client.get("/api/supplier-catalog/hygiene/v-1", headers=admin_headers)
        body = res.json()
        kinds = {f["kind"] for f in body["findings"]}
        assert kinds == {"category_rule"}  # beverages-as-Each still flagged


class TestSummary:
    def test_summary_counts(self, client, admin_headers, db_session):
        db_session.add(SupplierInvoiceSpec(name="Trents", aliases=[], instructions="x"))
        db_session.flush()
        sc.observe_extraction(
            db_session,
            {
                "supplier_name": "Trents X",
                "invoice_number": "A",
                "lines": [
                    {"code": "1", "description": "A 700ML", "unit_of_measure": "700ml"}
                ],
            },
            provenance="printed",
        )
        res = client.get("/api/supplier-catalog/summary", headers=admin_headers)
        assert res.status_code == 200
        body = res.json()
        assert body["products"] == 1
        assert body["answered"] == 1
        assert body["by_provenance"] == {"printed": 1}
