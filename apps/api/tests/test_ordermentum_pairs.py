"""One Ordermentum delivery, two copies: which received copy is the other half
(services/ordermentum_pairs, docs/plans/ordermentum-pairing.md).

Built after 400 days of real receipts at Applegum, Somewhere North and Little
West (21 Oct 2026): today's rule paired a Sonoma standing order with the wrong
day's copy at Little West and missed copies whose totals differ by Ordermentum's
freight; these rules made no wrong pair and missed none.
"""

import datetime as dt
import uuid

import pytest

from app.services import ordermentum_pairs as OP

D = dt.date


def _feed_row(
    number, day, lines, *, ordermentum, order=None, total=50.0, sup="sup-1", **over
):
    """A row of Loaded's received feed (``stock-received``): it carries lines."""
    return {
        "id": over.pop("id", f"r-{number}"),
        "invoiceNumber": number,
        "purchaseOrderNumber": order,
        "fileId": None if ordermentum else f"f-{number}",
        "supplierId": sup,
        "supplierName": "Sonoma Baking Company",
        "invoicedAt": day,
        "receivedAt": f"{day}T00:00:00",
        "total": total,
        "lines": lines,
        **over,
    }


def _feed_lines(*items):
    return [
        {"itemId": i, "itemCode": c, "quantityReceived": q, "unitCost": p}
        for c, i, q, p in items
    ]


def _detail_lines(*items, freight=None):
    out = [
        {
            "code": c,
            "description": f"item {c}",
            "linkedItemId": i,
            "quantityReceived": q,
            "unitCostExclTax": p,
        }
        for c, i, q, p in items
    ]
    if freight is not None:
        out.append(
            {
                "code": None,
                "description": "Freight",
                "quantityReceived": 1.0,
                "unitCostExclTax": freight,
            }
        )
    return out


CROISSANT = ("30132", "i-croissant", 2.0, 14.96)
BUN = ("30157", "i-bun", 1.0, 22.21)
LOAF_4DP = ("10250", "i-loaf", 4.0, 10.676)  # Ordermentum keeps four decimals
LOAF_2DP = ("10250", "i-loaf", 4.0, 10.68)


def _me(day, *, ordermentum=False, number="OMO1102729", lines=None, total=57.34):
    return OP.Copy(
        invoice_id="me",
        ordermentum=ordermentum,
        supplier_id="sup-1",
        supplier_name="Sonoma Baking Company",
        number=OP.number_of(
            ordermentum=ordermentum,
            reference=number if ordermentum else "3739965",
            order=None if ordermentum else number,
        ),
        date=D.fromisoformat(day),
        lines=lines if lines is not None else _detail_lines(CROISSANT, BUN),
        total=total,
    )


class TestTheSameGoods:
    def test_by_code_or_stock_item_rounding_and_freight_aside(self):
        # Sonoma 3739965 and Ordermentum OMI1102279 (8–9 Oct 2026): the same
        # croissants and buns; Ordermentum's copy adds $7 freight.
        ours = _detail_lines(CROISSANT, BUN, freight=0.0)
        theirs = _feed_lines(CROISSANT, BUN)
        assert OP.same_goods(ours, theirs)
        assert OP.same_goods(_detail_lines(LOAF_4DP), _feed_lines(LOAF_2DP))
        # By stock item when a side has no codes (a reading of the copy).
        reading = [
            {
                "linked_item_id": "i-croissant",
                "quantity_received": 2,
                "unit_cost": 14.96,
                "description": "Croissant Plain 6 PCK",
            }
        ]
        assert OP.same_goods(reading, _feed_lines(CROISSANT))

    def test_what_differs_is_not_the_same(self):
        ours = _detail_lines(CROISSANT, BUN)
        assert not OP.same_goods(ours, _feed_lines(CROISSANT))  # a line short
        assert not OP.same_goods(
            ours, _feed_lines(CROISSANT, ("30157", "i-bun", 2.0, 22.21))
        )
        assert not OP.same_goods(
            ours, _feed_lines(CROISSANT, ("30157", "i-bun", 1.0, 22.25))
        )
        assert not OP.same_goods([], [])


class TestANewSupplierIsJudgedOnItsGoods:
    def test_the_supplier_copy_finds_ordermentums_a_day_earlier(self):
        om = _feed_row(
            "OMI1102279",
            "2026-10-08",
            _feed_lines(CROISSANT, BUN),
            ordermentum=True,
            total=65.04,
        )
        found = OP.decide(_me("2026-10-09"), [om], history=[], taken=set())
        assert found.kind == "pair" and found.twin.row is om
        assert found.gap == 1102729 - 1102279

    def test_a_tie_or_a_different_order_deletes_nothing(self):
        a = _feed_row(
            "OMI1", "2026-10-08", _feed_lines(CROISSANT, BUN), ordermentum=True
        )
        b = _feed_row(
            "OMI2", "2026-10-08", _feed_lines(CROISSANT, BUN), ordermentum=True
        )
        assert OP.decide(_me("2026-10-09"), [a, b], history=[], taken=set()) is None
        other = _feed_row(
            "OMI3", "2026-10-08", _feed_lines(CROISSANT), ordermentum=True
        )
        assert OP.decide(_me("2026-10-09"), [other], history=[], taken=set()) is None
        late = _feed_row(
            "OMI4", "2026-10-03", _feed_lines(CROISSANT, BUN), ordermentum=True
        )
        assert OP.decide(_me("2026-10-09"), [late], history=[], taken=set()) is None

    def test_never_the_same_kind_a_credit_or_a_copy_already_paired(self):
        twin = _feed_row(
            "OMI1102279", "2026-10-08", _feed_lines(CROISSANT, BUN), ordermentum=True
        )
        emailed = _feed_row(
            "3739000",
            "2026-10-08",
            _feed_lines(CROISSANT, BUN),
            ordermentum=False,
            order="OMO1102000",
        )
        assert OP.decide(_me("2026-10-09"), [emailed], history=[], taken=set()) is None
        assert (
            OP.decide(_me("2026-10-09"), [twin], history=[], taken={twin["id"]}) is None
        )
        credit = _me("2026-10-09", total=-5.0)
        assert OP.decide(credit, [twin], history=[], taken=set()) is None


SONOMA_STEADY = [(D(2026, 9, d), 450) for d in (1, 3, 5, 8, 10, 12)]


class TestASteadySupplierIsJudgedOnItsNumber:
    def test_the_exact_gap_wins_over_a_closer_same_goods_order(self):
        # Two Sonoma standing orders placed minutes apart sit four numbers
        # apart with the same goods (Somewhere North, 18–19 Jul 2026).
        right = _feed_row(
            "OMI1102279", "2026-10-07", _feed_lines(CROISSANT, BUN), ordermentum=True
        )
        near = _feed_row(
            "OMI1102283", "2026-10-08", _feed_lines(CROISSANT, BUN), ordermentum=True
        )
        found = OP.decide(
            _me("2026-10-09"), [near, right], history=SONOMA_STEADY, taken=set()
        )
        assert found.kind == "pair" and found.twin.row is right
        assert found.how == "the number lines up"

    def test_its_own_copy_missing_holds_instead_of_pairing_the_wrong_one(self):
        # The right copy hasn't arrived (OMI1102279 reached Loaded eight days
        # late): the near one is a different order, so nothing is deleted.
        near = _feed_row(
            "OMI1102283", "2026-10-08", _feed_lines(CROISSANT, BUN), ordermentum=True
        )
        found = OP.decide(_me("2026-10-09"), [near], history=SONOMA_STEADY, taken=set())
        assert found.kind == "hold"


class TestADriftingSupplier:
    # M H Luxe: 63 until July 2026, then about one a day (152 by 16 Oct).
    LUXE = [(D(2026, 10, d), -140 - d) for d in (1, 3, 6, 9, 12)]

    def test_the_same_goods_near_its_recent_gap_pair(self):
        om = _feed_row(
            "OMI1091831", "2026-10-17", _feed_lines(CROISSANT, BUN), ordermentum=True
        )
        me = _me("2026-10-18", number="OMO1091677")
        found = OP.decide(me, [om], history=self.LUXE, taken=set())
        assert found.kind == "pair" and found.gap == -154

    def test_the_same_goods_far_from_its_gap_do_not(self):
        om = _feed_row(
            "OMI1095000", "2026-10-17", _feed_lines(CROISSANT, BUN), ordermentum=True
        )
        me = _me("2026-10-18", number="OMO1091677")
        assert OP.decide(me, [om], history=self.LUXE, taken=set()) is None


@pytest.fixture
def key():
    """A supplier of its own, its records removed afterwards."""
    name = f"Test Bakery {uuid.uuid4().hex[:8]}"
    yield name
    from app.db.engine import SessionLocal
    from app.db.models import OrdermentumPair

    s = SessionLocal()
    s.query(OrdermentumPair).filter(
        OrdermentumPair.supplier_key == OP.supplier_key(name)
    ).delete()
    s.commit()
    s.close()


def _named(copy, name):
    copy.supplier_name = name
    return copy


class TestTheRecord:
    def test_a_pair_is_kept_and_its_copies_are_taken(self, key):
        om = _feed_row(
            "OMI1102279",
            "2026-10-08",
            _feed_lines(CROISSANT, BUN),
            ordermentum=True,
            supplierName=key,
            id=f"om-{key}",
        )
        me = _named(_me("2026-10-09"), key)
        me.invoice_id = f"me-{key}"
        dup = OP.check(me, [om], venue_id=None)
        assert dup is not None and dup.twin_row is om and dup.also_remove == []
        history, taken = OP._load(OP.supplier_key(key), None)
        assert history == [(D(2026, 10, 9), 450)]
        assert {f"om-{key}", f"me-{key}"} <= taken

    def test_two_holds_in_a_row_prove_a_drift(self, key, monkeypatch):
        # A steady supplier's number stops lining up: the first same-goods copy
        # is held (nothing deleted); the second confirms both, and the first
        # pair's later copy goes with this one.
        monkeypatch.setattr(OP, "_load", lambda k, v: (SONOMA_STEADY, set()))
        first = _feed_row(
            "OMI2000000",
            "2026-10-08",
            _feed_lines(CROISSANT, BUN),
            ordermentum=True,
            supplierName=key,
            id=f"om1-{key}",
        )
        a = _named(_me("2026-10-09", number="OMO2000452"), key)
        a.invoice_id = f"a-{key}"
        assert OP.check(a, [first], venue_id=None) is None  # held
        second = _feed_row(
            "OMI2000100",
            "2026-10-10",
            _feed_lines(CROISSANT, BUN),
            ordermentum=True,
            supplierName=key,
            id=f"om2-{key}",
        )
        b = _named(_me("2026-10-11", number="OMO2000553"), key)
        b.invoice_id = f"b-{key}"
        dup = OP.check(b, [second], venue_id=None)
        assert dup is not None and dup.twin_row is second
        assert dup.also_remove == [f"a-{key}"]
