"""The money rules — one implementation, in integer cents.

* **Menu food value.** A set menu is ``price_per_person x guests`` (its dishes
  are unpriced — Orbit's reports summed line totals and read $0 for every set
  menu); priced lines add ``quantity x unit price`` on top (a la carte dishes,
  and extras added to a set menu).
* **Option total** = max(minimum spend, food at that venue). A minimum spend
  is a floor, not a surcharge.
* **Alternatives are never summed.** Two options are two possible bookings;
  an enquiry is worth its best option, not their sum.
* **Event expected spend** = max(confirmed minimum spend, food + bar tab).

``event_money_select`` is the SQL form of the event rule for lists and
reports; ``tests/test_functions_foundation.py`` holds it to the Python one.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from sqlalchemy import and_, case, func, select, true
from sqlalchemy.orm import Session

from app.db.functions_models import (
    FnEvent,
    FnMinSpend,
    FnPreorderMenu,
    FnPreorderMenuItem,
)


def _get(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def menu_food_cents(menu, items: Iterable) -> int:
    """One menu's value. ``menu`` and ``items`` may be ORM rows or dicts."""
    total = 0
    if _get(menu, "is_set_menu"):
        total += int(_get(menu, "price_per_person_cents") or 0) * int(
            _get(menu, "guest_count") or 0
        )
    for item in items:
        if (_get(item, "kind") or "item") == "header":
            continue
        price = _get(item, "unit_price_cents")
        if price is None:
            continue
        total += int(price) * int(_get(item, "quantity") or 0)
    return total


def food_cents(menus_with_items: Iterable[tuple]) -> int:
    """Sum of ``menu_food_cents`` over ``(menu, items)`` pairs."""
    return sum(menu_food_cents(menu, items) for menu, items in menus_with_items)


def option_total_cents(min_spend_cents: int | None, food: int) -> int:
    return max(int(min_spend_cents or 0), int(food or 0))


def enquiry_value_cents(option_totals: Sequence[int], food: int = 0) -> int:
    """What an open enquiry is worth: its best option, or — before any option
    exists — its food. Never the sum of alternatives."""
    if option_totals:
        return max(int(t) for t in option_totals)
    return int(food or 0)


def event_expected_spend_cents(
    min_spend_cents: int | None, food: int, bar_tab_cents: int | None
) -> int:
    return max(int(min_spend_cents or 0), int(food or 0) + int(bar_tab_cents or 0))


def largest_remainder(weights: Sequence[int], total: int) -> list[int]:
    """Split ``total`` whole portions by ``weights`` so the parts sum exactly
    to ``total`` — Orbit's set-menu split (``largestRemainderDistribute``),
    in exact integer arithmetic.

    Floors first, then one extra portion to the largest remainders, ties to
    the earlier dish. Weights are normalised by their sum, which is identical
    to Orbit's percentages when they add to 100 and stays correct when a
    section's do not (Orbit indexed past the end of the list in that case).
    All-zero weights split evenly.
    """
    n = len(weights)
    if n == 0 or total <= 0:
        return [0] * n
    w = [max(0, int(x)) for x in weights]
    denom = sum(w)
    if denom == 0:
        w, denom = [1] * n, n
    floors = [x * total // denom for x in w]
    remainders = [x * total % denom for x in w]
    order = sorted(range(n), key=lambda i: (-remainders[i], i))
    for i in order[: total - sum(floors)]:
        floors[i] += 1
    return floors


# ---------------------------------------------------------------------------
# Loading — the same rules over the database
# ---------------------------------------------------------------------------


def _menus_with_items(db: Session, org_id: str, menu_filter) -> list[tuple]:
    menus = (
        db.query(FnPreorderMenu)
        .filter(FnPreorderMenu.organization_id == org_id, menu_filter)
        .order_by(FnPreorderMenu.sort_order, FnPreorderMenu.created_at)
        .all()
    )
    if not menus:
        return []
    items_by_menu: dict[str, list] = {m.id: [] for m in menus}
    for item in (
        db.query(FnPreorderMenuItem)
        .filter(
            FnPreorderMenuItem.organization_id == org_id,
            FnPreorderMenuItem.menu_id.in_(list(items_by_menu)),
        )
        .order_by(FnPreorderMenuItem.sort_order)
        .all()
    ):
        items_by_menu[item.menu_id].append(item)
    return [(m, items_by_menu[m.id]) for m in menus]


def enquiry_food_cents(
    db: Session, org_id: str, enquiry_id: str, venue_id: str | None = None
) -> int:
    """Food on an enquiry — at one venue when given (an option's food is the
    food at the option's venue)."""
    cond = FnPreorderMenu.enquiry_id == enquiry_id
    if venue_id:
        cond = and_(cond, FnPreorderMenu.venue_id == venue_id)
    return food_cents(_menus_with_items(db, org_id, cond))


def event_food_cents(db: Session, org_id: str, event_id: str) -> int:
    return food_cents(
        _menus_with_items(db, org_id, FnPreorderMenu.event_id == event_id)
    )


def event_expected_spend(db: Session, event: FnEvent) -> dict:
    """``{min_spend_cents, food_cents, bar_tab_cents, expected_spend_cents}``."""
    ms = (
        db.query(FnMinSpend.amount_cents)
        .filter(
            FnMinSpend.organization_id == event.organization_id,
            FnMinSpend.event_id == event.id,
        )
        .first()
    )
    min_spend = ms[0] if ms else None
    food = event_food_cents(db, event.organization_id, event.id)
    return {
        "min_spend_cents": min_spend,
        "food_cents": food,
        "bar_tab_cents": event.bar_tab_cents or 0,
        "expected_spend_cents": event_expected_spend_cents(
            min_spend, food, event.bar_tab_cents
        ),
    }


def event_money_select(org_id: str, *, event_ids=None):
    """SQL twin of ``event_expected_spend`` for the org's events (or those in
    ``event_ids``, e.g. a list page): columns ``event_id, min_spend_cents,
    food_cents, bar_tab_cents, expected_spend_cents``. Join it to fn_events
    on ``event_id``.

    The food parts are grouped derived tables, each computed once and
    hash-joined — not a correlated subquery per event, which Postgres repeats
    for every reference once it flattens the layers."""
    ev, ms = FnEvent, FnMinSpend
    pm, it = FnPreorderMenu, FnPreorderMenuItem

    def only(column):
        return column.in_(list(event_ids)) if event_ids is not None else true()

    set_food = (
        select(
            pm.event_id,
            func.sum(
                case(
                    (
                        pm.is_set_menu.is_(True),
                        func.coalesce(pm.price_per_person_cents, 0) * pm.guest_count,
                    ),
                    else_=0,
                )
            ).label("set_cents"),
        )
        .where(
            pm.organization_id == org_id, pm.event_id.is_not(None), only(pm.event_id)
        )
        .group_by(pm.event_id)
        .subquery("set_food")
    )
    item_food = (
        select(
            pm.event_id,
            func.sum(it.quantity * it.unit_price_cents).label("item_cents"),
        )
        .select_from(it)
        .join(pm, and_(pm.organization_id == it.organization_id, pm.id == it.menu_id))
        .where(
            pm.organization_id == org_id,
            pm.event_id.is_not(None),
            only(pm.event_id),
            it.kind == "item",
            it.unit_price_cents.is_not(None),
        )
        .group_by(pm.event_id)
        .subquery("item_food")
    )
    inner = (
        select(
            ev.id.label("event_id"),
            # At most one: uq_fn_min_spends_event.
            ms.amount_cents.label("min_spend_cents"),
            (
                func.coalesce(set_food.c.set_cents, 0)
                + func.coalesce(item_food.c.item_cents, 0)
            ).label("food_cents"),
            ev.bar_tab_cents.label("bar_tab_cents"),
        )
        .select_from(ev)
        .outerjoin(
            ms, and_(ms.organization_id == ev.organization_id, ms.event_id == ev.id)
        )
        .outerjoin(set_food, set_food.c.event_id == ev.id)
        .outerjoin(item_food, item_food.c.event_id == ev.id)
        .where(ev.organization_id == org_id, only(ev.id))
        .subquery("event_money_inner")
    )
    return select(
        inner.c.event_id,
        inner.c.min_spend_cents,
        inner.c.food_cents,
        inner.c.bar_tab_cents,
        func.greatest(
            func.coalesce(inner.c.min_spend_cents, 0),
            inner.c.food_cents + func.coalesce(inner.c.bar_tab_cents, 0),
        ).label("expected_spend_cents"),
    )
