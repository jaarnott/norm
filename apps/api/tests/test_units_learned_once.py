"""What Norm learns about a product's size on one invoice serves every later
invoice — and on the invoice itself, each product is asked about once.

15 Oct 2026, Applegum's onboarding (production): Alsco's monthly rental
invoice lists each item once per week, so the same welcome mat sat on four
lines and Norm asked the web-searching estimator four times — 34 questions
for 10 products, on each of three invoices in a row. Delivery fees, freight
and levies were sent to it too, and the answer is always "each": a charge is
one charge.
"""

from app.services import supplier_catalog as sc
from app.services.invoice_replica import build_replica
from tests.test_invoice_replica import CATALOGUE, SUPPLIERS, TAX, UNITS

ALSCO = "Alsco"


def _mat(week: int) -> dict:
    # The weekly lines of a rental invoice are word for word the same.
    return {
        "code": "81AJ",
        "description": "5x3 Welcome Mat",
        "quantity": 1,
        "unit": None,
        "unit_of_measure": None,
        "unit_price_ex_tax": 14.03,
        "line_total_ex_tax": 14.03,
    }


def _charge(description: str, code: str | None = None, price: float = 4.5) -> dict:
    return {
        "code": code,
        "description": description,
        "quantity": 1,
        "unit": None,
        "unit_of_measure": None,
        "unit_price_ex_tax": price,
        "line_total_ex_tax": price,
    }


def _invoice(lines: list[dict], number: str = "A1") -> dict:
    return {
        "invoice_number": number,
        "supplier_name": "Alsco Pty Ltd",
        "customer_purchase_order_number": None,
        "invoice_date": "2026-10-01",
        "lines": lines,
    }


class _Asker:
    """The estimator, faked: counts the questions, answers 'each'."""

    def __init__(self):
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> dict:
        self.prompts.append(prompt)
        return {"size": "1 each", "confidence": "high", "why": "a rental item"}


def _seed(db):
    from app.db.config_models import SupplierInvoiceSpec

    db.add(
        SupplierInvoiceSpec(
            name=ALSCO, aliases=["Alsco Pty Ltd"], instructions="x", enabled=True
        )
    )
    db.flush()


def _review(db, ext, ask):
    """One invoice through the replica, then the catalogue learns from it —
    the two steps review_invoice runs for every invoice, in order."""
    doc = build_replica(
        None,
        db,
        "v-1",
        ext,
        lh=object(),
        catalogue=CATALOGUE,
        units=UNITS,
        suppliers=SUPPLIERS,
        tax_rates=TAX,
        aliases_by_id={},
        item_matcher=lambda *a, **k: {},
        unit_resolver_ask=ask,
    )
    sc.learn_from_resolver(
        db, sc.supplier_key_for(ext["supplier_name"], db), doc["lines"]
    )
    db.flush()
    return doc


def _asked_about(ask: _Asker) -> list[str]:
    import json

    return [json.loads(p.split("Line: ", 1)[1])["description"] for p in ask.prompts]


class TestOncePerProduct:
    def test_four_lines_of_one_mat_are_one_question(self, db_session):
        _seed(db_session)
        ask = _Asker()
        doc = _review(db_session, _invoice([_mat(w) for w in (1, 2, 3, 4)]), ask)
        assert len(ask.prompts) == 1
        # ...and every one of the four lines gets the answer.
        units = [ln.get("unit_resolved", {}).get("unit_name") for ln in doc["lines"]]
        assert units == ["Each"] * 4

    def test_case_and_punctuation_dont_make_a_different_product(self, db_session):
        _seed(db_session)
        ask = _Asker()
        a, b = _mat(1), dict(_mat(2), description="5X3 WELCOME MAT.")
        _review(db_session, _invoice([a, b]), ask)
        assert len(ask.prompts) == 1

    def test_one_code_with_different_descriptions_is_asked_about_each(
        self, db_session
    ):
        """Angus Meats bills four different cuts under the code BONES."""
        _seed(db_session)
        ask = _Asker()
        cuts = [
            dict(_mat(1), code="BONES", description="Beef Bones Marrow 1 Inch"),
            dict(_mat(1), code="BONES", description="Beef Bones Canoe Cut"),
        ]
        _review(db_session, _invoice(cuts), ask)
        assert sorted(_asked_about(ask)) == [
            "Beef Bones Canoe Cut",
            "Beef Bones Marrow 1 Inch",
        ]


class TestChargesAreNotEstimated:
    def test_fees_freight_and_levies_are_one_charge_each(self, db_session):
        _seed(db_session)
        ask = _Asker()
        charges = [
            _charge("DELIVERY FEE"),
            _charge("Freight"),
            _charge("Fuel Levy"),
            _charge("Environment & Energy Charge", code="CENVLEVY"),
            _charge("Surcharge (direct)"),
            _charge("Total Admin Fee for Invoice"),
            _charge("03/01/26 Service 660l comingle bin"),
            _charge("Waste Service - 660L General Waste"),
            _charge("Invoice Rounding"),
            _charge("Delivery - Order Under $200"),
        ]
        doc = _review(db_session, _invoice(charges), ask)
        assert ask.prompts == []
        assert [ln.get("unit_resolved", {}).get("unit_name") for ln in doc["lines"]] == [
            "Each"
        ] * len(charges)

    def test_a_product_that_only_mentions_delivery_is_still_asked_about(
        self, db_session
    ):
        _seed(db_session)
        ask = _Asker()
        products = [
            _charge("Pizza Delivery Box 12in", "PDB12"),
            _charge("Hot Cross Bun Classic Fruit 12 PCK - Food Service", "HCB12"),
        ]
        _review(db_session, _invoice(products), ask)
        assert sorted(_asked_about(ask)) == [
            "Hot Cross Bun Classic Fruit 12 PCK - Food Service",
            "Pizza Delivery Box 12in",
        ]


class TestLearnedOnceServesTheNextInvoice:
    def test_the_next_invoice_from_the_supplier_asks_nothing(self, db_session):
        _seed(db_session)
        first, second = _Asker(), _Asker()
        lines = [_mat(w) for w in (1, 2, 3, 4)] + [
            _charge("Inventory Maintenance & Recycling Charge", code="INVMAINT")
        ]
        _review(db_session, _invoice(lines, "A1"), first)
        assert first.prompts  # the mat was new
        _review(db_session, _invoice(lines, "A2"), second)
        assert second.prompts == []
