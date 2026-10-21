"""One Ordermentum delivery, two copies: finding the pair.

A delivery ordered through Ordermentum reaches Loaded twice under different
numbers: Ordermentum's own copy (Loaded ``source = Ordermentum``, no file, its
invoice number OMI…) and the supplier's emailed invoice, which prints
Ordermentum's ORDER number (OMO…) as its purchase order or as its own number.
Whichever is received first stays; the other is a duplicate (owner, 16 and
21 Oct 2026). This module decides which received copy, if any, is the other
half of the copy being reviewed. Plan and tests: docs/plans/ordermentum-pairing.md.

The rules, each earned on 400 days of real receipts at the three Ordermentum
venues (12 suppliers):

- **One pair per copy.** Every settled pair is kept (``OrdermentumPair``); a
  copy already in one is never offered as anyone else's twin. That alone
  stopped most wrong pairs when a copy arrived late (a standing order paired
  with the wrong day's copy).
- **The number gap.** For each supplier the order number minus Ordermentum's
  invoice number is fixed (Sonoma 450 in 174 of 174 pairs, Somage 0) or
  drifts slowly (M H Luxe 63 until July 2026, then about one a day). It is
  learned from the settled pairs. A supplier is *steady* when its last five
  pairs share one gap: then only that exact gap counts — two of Sonoma's
  standing orders placed minutes apart sit four numbers apart with the same
  goods, so a near miss is a different order.
- **Hold, then confirm.** A steady supplier whose exact gap finds nothing but
  whose same goods turn up is held: nothing is deleted and the pair is noted
  as suspected. A second one before the next exact match means the gap has
  started to drift: both are confirmed, this copy is the duplicate, and the
  held pair's later copy goes with it.
- **The same goods** (a drifting or new supplier): the same lines — by the
  supplier's product code when both copies carry one on every line, else by
  Loaded's stock item — at the same quantities and prices to the cent
  (Ordermentum keeps four decimals, suppliers print two), freight and
  delivery lines left out (Ordermentum adds $7 freight to Sonoma's copies);
  dated up to four days apart (two before to ten after once the supplier has
  a gap history, its gap then close to its recent ones); the nearest to one
  day apart; a tie deletes nothing. Credit notes are never paired.

Nothing here reads Loaded: the received feed rows already carry each copy's
lines, number and date.
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
import statistics
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: Pairs that must share one gap before only that exact gap counts.
STEADY_PAIRS = 5
#: Suspected pairs (in a row, no exact match between) that prove a drift.
HOLD_LIMIT = 2
#: Days from Ordermentum's copy to the supplier's, by how the pair is judged.
NUMBER_WINDOW = (-2, 30)
GOODS_WINDOW_NEW = (0, 4)
GOODS_WINDOW_KNOWN = (-2, 10)
#: How far a drifting supplier's gap may be from its recent ones: this, plus
#: GAP_DRIFT_PER_DAY for each day since its last pair.
GAP_ALLOWANCE = 5
GAP_DRIFT_PER_DAY = 2
PRICE_TOLERANCE = 0.011

_FREIGHT = re.compile(r"freight|delivery", re.I)
_DIGITS = re.compile(r"(\d{3,})")


def supplier_key(name: object) -> str:
    """The supplier, folded: its gap is the same at every venue."""
    return re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()


def _digits(text: object) -> int | None:
    m = _DIGITS.search(str(text or ""))
    return int(m.group(1)) if m else None


def number_of(
    *, ordermentum: bool, reference: object, order: object = None
) -> int | None:
    """The number a copy is paired on: Ordermentum's invoice number for its
    own copy; for the supplier's, Ordermentum's order number wherever it is
    printed, else its own number (Made of Many and City Meats use one number
    on both copies)."""
    if ordermentum:
        return _digits(reference)
    from app.services.invoice_extraction import ordermentum_order_number

    omo = ordermentum_order_number(order, reference)
    return _digits(omo) if omo else _digits(reference)


def _day(value: object) -> _dt.date | None:
    try:
        return _dt.date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _f(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _first(ln: dict, *keys: str) -> object:
    for k in keys:
        if ln.get(k) is not None:
            return ln.get(k)
    return None


def goods(lines: list | None) -> list[tuple]:
    """``(code, stock item, quantity, price)`` per line, in any of the three
    shapes a line comes in (Loaded's detail, its received feed, Norm's
    reading); freight, delivery and struck lines left out."""
    out = []
    for ln in lines or []:
        if not isinstance(ln, dict) or ln.get("deletedAt") or ln.get("struck"):
            continue
        if _FREIGHT.search(str(ln.get("description") or ln.get("item_name") or "")):
            continue
        code = _first(ln, "code", "itemCode")
        out.append(
            (
                str(code).strip() or None if code is not None else None,
                _first(ln, "linkedItemId", "itemId", "linked_item_id"),
                _f(_first(ln, "quantityReceived", "quantity_received")),
                _f(_first(ln, "unitCostExclTax", "unitCost", "unit_cost")),
            )
        )
    return out


def same_goods(a: list | None, b: list | None) -> bool:
    """The same lines at the same quantities and prices (to the cent)."""
    ga, gb = goods(a), goods(b)
    if not ga or len(ga) != len(gb):
        return False
    if any(q is None or p is None for _, _, q, p in ga + gb):
        return False
    by_code = all(g[0] for g in ga) and all(g[0] for g in gb)
    idx = 0 if by_code else 1
    if not by_code and not (all(g[1] for g in ga) and all(g[1] for g in gb)):
        return False  # neither a code nor a stock item on every line
    sa = sorted((g[idx], g[2], g[3]) for g in ga)
    sb = sorted((g[idx], g[2], g[3]) for g in gb)
    return all(
        x[0] == y[0] and abs(x[1] - y[1]) < 1e-6 and abs(x[2] - y[2]) <= PRICE_TOLERANCE
        for x, y in zip(sa, sb)
    )


@dataclass
class Copy:
    """One copy of a delivery, as the pairing sees it."""

    invoice_id: str | None
    ordermentum: bool
    supplier_id: str | None
    supplier_name: str | None
    number: int | None
    date: _dt.date | None
    lines: list = field(default_factory=list)
    total: float | None = None
    row: dict | None = None

    @classmethod
    def from_feed_row(cls, row: dict) -> Copy:
        om = not row.get("fileId")
        return cls(
            invoice_id=row.get("id"),
            ordermentum=om,
            supplier_id=row.get("supplierId") or row.get("linkedSupplierId"),
            supplier_name=row.get("supplierName"),
            number=number_of(
                ordermentum=om,
                reference=row.get("invoiceNumber"),
                order=row.get("purchaseOrderNumber"),
            ),
            date=_day(row.get("invoicedAt") or row.get("receivedAt")),
            lines=row.get("lines") or [],
            total=_f(row.get("total")),
            row=row,
        )

    @classmethod
    def from_detail(cls, detail: dict, invoice_id: str | None) -> Copy:
        """The copy under review, from Loaded's own record of it."""
        from app.services.invoice_extraction import is_feed_invoice

        om = is_feed_invoice(detail)
        return cls(
            invoice_id=invoice_id or detail.get("id"),
            ordermentum=om,
            supplier_id=detail.get("linkedSupplierId"),
            supplier_name=detail.get("supplierName"),
            number=number_of(
                ordermentum=om,
                reference=detail.get("referenceNumber"),
                order=detail.get("purchaseOrderNumber"),
            ),
            date=_day(detail.get("issuedAt")),
            lines=detail.get("lines") or [],
            total=_f(detail.get("total")),
        )

    @classmethod
    def from_reading(
        cls,
        *,
        invoice_id: str | None,
        supplier_id: str | None,
        supplier_name: str | None,
        reference: object,
        order: object,
        date: object,
        lines: list,
        total: object,
    ) -> Copy:
        """The supplier's copy as Norm read it (the order number only the
        read found)."""
        return cls(
            invoice_id=invoice_id,
            ordermentum=False,
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            number=number_of(ordermentum=False, reference=reference, order=order),
            date=_day(date),
            lines=lines or [],
            total=_f(total),
        )


@dataclass
class Found:
    """What the pairing decided for the copy under review."""

    kind: str  # "pair" | "hold"
    twin: Copy
    how: str
    gap: int | None
    days: int


def _gap_days(me: Copy, other: Copy) -> tuple[int | None, int | None]:
    supplier, om = (other, me) if me.ordermentum else (me, other)
    gap = (
        supplier.number - om.number
        if supplier.number is not None and om.number is not None
        else None
    )
    days = (supplier.date - om.date).days if supplier.date and om.date else None
    return gap, days


def _nearest(fits: list[tuple[int, object]]):
    fits = sorted(fits, key=lambda f: f[0])
    if not fits or (len(fits) > 1 and fits[0][0] == fits[1][0]):
        return None  # two equally likely: never delete on a coin toss
    return fits[0][1]


def decide(
    me: Copy,
    rows: list | None,
    *,
    history: list[tuple[_dt.date, int]],
    taken: set[str],
) -> Found | None:
    """The received copy that is this one's other half, or None. Pure: the
    caller supplies the supplier's gap history (date, gap — oldest first) and
    the copies already paired."""
    if me.date is None or (me.total is not None and me.total < 0):
        return None
    key = supplier_key(me.supplier_name)
    cands: list[tuple[Copy, int | None, int]] = []
    for row in rows or []:
        if not isinstance(row, dict) or row.get("type") == "PurchaseOrder":
            continue
        if row.get("id") in taken or row.get("id") == me.invoice_id:
            continue
        other = Copy.from_feed_row(row)
        if other.ordermentum == me.ordermentum:
            continue  # the same kind of copy is never the other half
        if me.supplier_id and other.supplier_id:
            if other.supplier_id != me.supplier_id:
                continue
        elif supplier_key(other.supplier_name) != key or not key:
            continue
        if other.total is not None and other.total < 0:
            continue
        gap, days = _gap_days(me, other)
        if days is None:
            continue
        cands.append((other, gap, days))
    if not cands:
        return None
    h = [(d, g) for d, g in history if d <= me.date]
    steady = len(h) >= STEADY_PAIRS and len({g for _, g in h[-STEADY_PAIRS:]}) == 1
    if steady:
        exact = [
            c
            for c in cands
            if c[1] == h[-1][1] and NUMBER_WINDOW[0] <= c[2] <= NUMBER_WINDOW[1]
        ]
        if len(exact) == 1:
            c = exact[0]
            return Found("pair", c[0], "the number lines up", c[1], c[2])
    window = GOODS_WINDOW_KNOWN if h else GOODS_WINDOW_NEW
    near = [
        c
        for c in cands
        if window[0] <= c[2] <= window[1] and same_goods(me.lines, c[0].lines)
    ]
    if h:
        expect = statistics.median(g for _, g in h[-3:])
        allow = GAP_ALLOWANCE + GAP_DRIFT_PER_DAY * abs((me.date - h[-1][0]).days)
        near = [c for c in near if c[1] is not None and abs(c[1] - expect) <= allow]
    best = _nearest([(abs(c[2] - 1), c) for c in near])
    if best is None:
        return None
    if steady:
        return Found(
            "hold", best[0], "the same goods, the number off", best[1], best[2]
        )
    return Found("pair", best[0], "the same goods", best[1], best[2])


# ---------------------------------------------------------------------------
# The record: settled pairs, the gap history, suspected pairs
# ---------------------------------------------------------------------------


def _session():
    from app.db.engine import SessionLocal

    return SessionLocal()


def _load(key: str, venue_id: str | None) -> tuple[list, set]:
    """(gap history for the supplier, ids already paired at the venue)."""
    from app.db.models import OrdermentumPair as P

    s = _session()
    try:
        rows = (
            s.query(P.delivered_on, P.gap)
            .filter(
                P.supplier_key == key,
                P.status.in_(("paired", "confirmed")),
                P.gap.isnot(None),
                P.delivered_on.isnot(None),
            )
            .order_by(P.delivered_on, P.created_at)
            .all()
        )
        q = s.query(P.ordermentum_invoice_id, P.supplier_invoice_id).filter(
            P.status.in_(("paired", "confirmed"))
        )
        if venue_id:
            q = q.filter(P.venue_id == venue_id)
        taken = {i for pair in q.all() for i in pair}
        return [(d, g) for d, g in rows], taken
    finally:
        s.close()


def _save(me: Copy, found: Found, venue_id: str | None, status: str) -> list[str]:
    """Record the pair; for a hold, return the later copies of every
    suspected pair once enough have built up to prove a drift (they are
    confirmed and go with this one). Best-effort: never fails a review."""
    from app.db.models import OrdermentumPair as P

    supplier, om = (found.twin, me) if me.ordermentum else (me, found.twin)
    key = supplier_key(me.supplier_name)
    s = _session()
    try:
        row = (
            s.query(P)
            .filter(
                P.ordermentum_invoice_id == om.invoice_id,
                P.supplier_invoice_id == supplier.invoice_id,
            )
            .first()
        )
        if row is None:
            row = P(
                ordermentum_invoice_id=om.invoice_id,
                supplier_invoice_id=supplier.invoice_id,
            )
            s.add(row)
        row.venue_id = venue_id
        row.supplier_key = key
        row.supplier_name = me.supplier_name
        row.ordermentum_number = om.number
        row.supplier_order_number = supplier.number
        row.gap = found.gap
        row.delivered_on = supplier.date
        row.status = status
        row.how = found.how
        row.later_invoice_id = me.invoice_id
        also: list[str] = []
        if status == "paired" and found.how == "the number lines up":
            # The steady gap held again: earlier holds were other orders.
            s.query(P).filter(P.supplier_key == key, P.status == "suspected").update(
                {"status": "cleared"}
            )
        if status == "suspected":
            s.flush()
            held = (
                s.query(P).filter(P.supplier_key == key, P.status == "suspected").all()
            )
            if len(held) >= HOLD_LIMIT:
                for h in held:
                    h.status = "confirmed"
                    if h.later_invoice_id and h.later_invoice_id != me.invoice_id:
                        also.append(h.later_invoice_id)
        s.commit()
        return also
    except Exception:  # noqa: BLE001 — a record must never sink a review
        logger.warning("ordermentum pair not recorded", exc_info=True)
        s.rollback()
        return []
    finally:
        s.close()


@dataclass
class Duplicate:
    """This copy is the second of a pair: delete it (and, after a proven
    drift, the held pairs' later copies too)."""

    twin_row: dict
    how: str
    also_remove: list[str]


def check(me: Copy, rows: list | None, *, venue_id: str | None) -> Duplicate | None:
    """Decide, record, and say whether ``me`` is a duplicate."""
    try:
        history, taken = _load(supplier_key(me.supplier_name), venue_id)
    except Exception:  # noqa: BLE001 — no record: judge on the goods alone
        logger.info("ordermentum pair history unavailable", exc_info=True)
        history, taken = [], set()
    found = decide(me, rows, history=history, taken=taken)
    if found is None:
        return None
    if found.kind == "pair":
        _save(me, found, venue_id, "paired")
        return Duplicate(found.twin.row or {}, found.how, [])
    also = _save(me, found, venue_id, "suspected")
    if also:
        # The supplier's gap has moved: this pair and the held ones are real.
        return Duplicate(
            found.twin.row or {}, "the supplier's number has moved on", also
        )
    return None
