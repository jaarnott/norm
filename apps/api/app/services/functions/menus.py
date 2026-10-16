"""Food pre-orders — menus on enquiries and events.

A menu is built on the enquiry (usually from one of the venue's templates)
and, when the booking is confirmed, gains the event's id: the same rows, now
the event's food. From then on every change is written to the event's change
log (section ``food``) — a client changing quantities in the portal is what
"in progress" and "edited since confirmed" are made of.

Set menus: the dishes are unpriced and carry quantities only; the menu's
value is price per person x guests (money.py). ``distribute_set_menu``
fills the quantities from each dish's default % of its section, with the
largest-remainder rule so a section always adds up to the guest count.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.functions_models import (
    MENU_ITEM_KINDS,
    FnEnquiry,
    FnEvent,
    FnPreorderMenu,
    FnPreorderMenuItem,
    FnPreorderTemplate,
    FnPreorderTemplateItem,
)
from app.db.models import _now
from app.services.functions import enquiries as enquiries_svc
from app.services.functions import events as events_svc
from app.services.functions import status as status_svc
from app.services.functions._rows import (
    apply_fields,
    check_version,
    jsonable,
    load,
    require_text,
)
from app.services.functions.ctx import (
    FunctionsCtx,
    require_live,
    require_staff,
    require_venue,
)
from app.services.functions.errors import Conflict, Forbidden, Invalid, flush
from app.services.functions.money import largest_remainder, menu_food_cents

#: A client changes quantities, nothing else (Orbit's portal saved only
#: ``{id, items}`` per menu): adding or removing menus, set-menu guests and
#: prices change the money, which is staff's.
MENU_FIELDS = (
    "name",
    "is_set_menu",
    "price_per_person_cents",
    "guest_count",
    "notes",
    "sort_order",
)
ITEM_FIELDS = (
    "template_item_id",
    "kind",
    "name",
    "description",
    "quantity",
    "unit_price_cents",
    "default_percent",
    "dietaries",
    "loaded_recipe_id",
    "loaded_stock_item_id",
)
_ITEM_SNAPSHOT = ("kind", "name", "quantity", "unit_price_cents")


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------


def sections(items) -> list[list[int]]:
    """Indices of the dishes in each section. A header starts a section;
    dishes before the first header are a section of their own."""
    out: list[list[int]] = []
    current: list[int] | None = None
    for i, item in enumerate(items):
        if (_get(item, "kind") or "item") == "header":
            if current:
                out.append(current)
            current = []
        else:
            if current is None:
                current = []
            current.append(i)
    if current:
        out.append(current)
    return out


def _get(obj, name):
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def set_menu_quantities(items, guest_count: int) -> dict[int, int]:
    """``{index: quantity}`` for the distributable dishes — the unpriced ones
    (priced extras are left as they are). Each section is split
    independently over ``guest_count`` by its dishes' ``default_percent``."""
    out: dict[int, int] = {}
    for section in sections(items):
        shares = [i for i in section if _get(items[i], "unit_price_cents") is None]
        if not shares:
            continue
        weights = [int(_get(items[i], "default_percent") or 0) for i in shares]
        for i, qty in zip(shares, largest_remainder(weights, int(guest_count or 0))):
            out[i] = qty
    return out


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _items(db: Session, ctx: FunctionsCtx, menu_id: str) -> list[FnPreorderMenuItem]:
    return (
        db.query(FnPreorderMenuItem)
        .filter(
            FnPreorderMenuItem.organization_id == ctx.org_id,
            FnPreorderMenuItem.menu_id == menu_id,
        )
        .order_by(FnPreorderMenuItem.sort_order, FnPreorderMenuItem.created_at)
        .all()
    )


def get_menu(db: Session, ctx: FunctionsCtx, menu_id: str, *, for_update: bool = False):
    """``(menu, items)``."""
    menu = load(db, ctx, FnPreorderMenu, menu_id, label="Menu", for_update=for_update)
    return menu, _items(db, ctx, menu.id)


def list_menus(
    db: Session,
    ctx: FunctionsCtx,
    *,
    enquiry_id: str | None = None,
    event_id: str | None = None,
) -> list[tuple[FnPreorderMenu, list[FnPreorderMenuItem]]]:
    q = db.query(FnPreorderMenu).filter(
        FnPreorderMenu.organization_id == ctx.org_id,
        ctx.venue_clause(FnPreorderMenu.venue_id),
    )
    if event_id:
        events_svc.get_event(db, ctx, event_id)
        q = q.filter(FnPreorderMenu.event_id == event_id)
    elif enquiry_id:
        enquiries_svc.get_enquiry(db, ctx, enquiry_id)
        q = q.filter(FnPreorderMenu.enquiry_id == enquiry_id)
    else:
        raise Invalid("Name an enquiry or an event.")
    menus = q.order_by(FnPreorderMenu.sort_order, FnPreorderMenu.created_at).all()
    return [(m, _items(db, ctx, m.id)) for m in menus]


def menu_value_cents(menu: FnPreorderMenu, items) -> int:
    return menu_food_cents(menu, items)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _parent(db, ctx, enquiry_id, event_id) -> tuple[FnEnquiry | None, FnEvent | None]:
    if event_id:
        event = events_svc.get_event(db, ctx, event_id)
        if event.cancelled_at is not None:
            raise Conflict("This event is cancelled — reinstate it first.")
        enquiry = (
            db.query(FnEnquiry)
            .filter(
                FnEnquiry.organization_id == ctx.org_id,
                FnEnquiry.id == event.enquiry_id,
            )
            .first()
            if event.enquiry_id
            else None
        )
        return enquiry, event
    if enquiry_id:
        if ctx.actor_kind == "client":
            raise Forbidden("Clients change menus on their booking.")
        enquiry = enquiries_svc.get_enquiry(db, ctx, enquiry_id)
        enquiries_svc.require_open_enquiry(db, ctx, enquiry)
        return enquiry, None
    raise Invalid("A menu belongs to an enquiry or an event.")


def _guard_menu(db, ctx, menu) -> FnEvent | None:
    """The event a menu is booked on (for logging), refusing a client who is
    not on that event and changes to a lost enquiry's menus."""
    if menu.event_id:
        event = events_svc.get_event(db, ctx, menu.event_id)
        if event.cancelled_at is not None:
            raise Conflict("This event is cancelled — reinstate it first.")
        return event
    if ctx.actor_kind == "client":
        raise Forbidden("Clients change menus on their booking.")
    if menu.enquiry_id:
        enquiry = enquiries_svc.get_enquiry(db, ctx, menu.enquiry_id)
        enquiries_svc.require_open_enquiry(db, ctx, enquiry)
    return None


def _snapshot(items) -> list[dict]:
    return [{k: jsonable(_get(i, k)) for k in _ITEM_SNAPSHOT} for i in items]


def _log_food(db, ctx, event: FnEvent | None, changes: dict, note: str | None):
    """Menu changes on a booked menu go in the event's log, as section food."""
    if event is None or not changes:
        return None
    confirmed = (
        status_svc.event_status_for(db, ctx.org_id, event.id).status == "confirmed"
    )
    row = events_svc._log(
        db,
        ctx,
        event,
        "edited",
        section="food",
        changes=changes,
        after_confirmation=confirmed,
        note=note,
    )
    flush(db)
    return row


def _clean_item(raw: dict, *, client: bool = False) -> dict:
    unknown = set(raw) - set(ITEM_FIELDS)
    if unknown:
        raise Invalid(f"Cannot set: {', '.join(sorted(unknown))}.")
    item = dict(raw)
    item.setdefault("kind", "item")
    if item["kind"] not in MENU_ITEM_KINDS:
        raise Invalid(f"Kind must be one of {', '.join(MENU_ITEM_KINDS)}.")
    item["name"] = require_text(item.get("name"), "A dish name")
    if int(item.get("quantity") or 0) < 0:
        raise Invalid("Quantities cannot be negative.")
    if item.get("unit_price_cents") is not None and int(item["unit_price_cents"]) < 0:
        raise Invalid("Prices cannot be negative.")
    item["quantity"] = int(item.get("quantity") or 0)
    item["dietaries"] = list(item.get("dietaries") or [])
    return item


def add_menu_from_template(
    db: Session,
    ctx: FunctionsCtx,
    *,
    template_id: str,
    enquiry_id: str | None = None,
    event_id: str | None = None,
    guest_count: int = 0,
    note: str | None = None,
) -> FnPreorderMenu:
    """Copy one of the venue's templates onto an enquiry or event — a
    snapshot of its dishes, quantities zero (set menus: run
    ``distribute_set_menu`` once the guest count is known)."""
    require_live(ctx)
    require_staff(ctx)
    enquiry, event = _parent(db, ctx, enquiry_id, event_id)
    template = load(db, ctx, FnPreorderTemplate, template_id, label="Menu template")
    if not template.active:
        raise Conflict("That menu template has been retired.")
    if event is not None and template.venue_id != event.venue_id:
        raise Invalid("That template is from another venue.")
    if (
        enquiry is not None
        and enquiry.venue_id
        and template.venue_id != enquiry.venue_id
    ):
        raise Invalid("That template is from another venue.")
    menu = FnPreorderMenu(
        organization_id=ctx.org_id,
        enquiry_id=enquiry.id if enquiry is not None else None,
        event_id=event.id if event is not None else None,
        venue_id=template.venue_id,
        template_id=template.id,
        name=template.name,
        is_set_menu=template.is_set_menu,
        price_per_person_cents=template.price_per_person_cents,
        guest_count=max(0, int(guest_count or 0)),
    )
    db.add(menu)
    flush(db)
    for i, t in enumerate(
        db.query(FnPreorderTemplateItem)
        .filter(
            FnPreorderTemplateItem.organization_id == ctx.org_id,
            FnPreorderTemplateItem.template_id == template.id,
        )
        .order_by(FnPreorderTemplateItem.sort_order, FnPreorderTemplateItem.created_at)
    ):
        db.add(
            FnPreorderMenuItem(
                organization_id=ctx.org_id,
                menu_id=menu.id,
                template_item_id=t.id,
                kind=t.kind,
                name=t.name,
                description=t.description,
                quantity=0,
                unit_price_cents=None if template.is_set_menu else t.unit_price_cents,
                default_percent=t.default_percent,
                dietaries=list(t.dietaries or []),
                loaded_recipe_id=t.loaded_recipe_id,
                loaded_stock_item_id=t.loaded_stock_item_id,
                sort_order=i,
            )
        )
    flush(db)
    _log_food(db, ctx, event, {"menus": [None, {"added": menu.name}]}, note)
    return menu


def create_menu(
    db: Session,
    ctx: FunctionsCtx,
    *,
    venue_id: str,
    name: str,
    enquiry_id: str | None = None,
    event_id: str | None = None,
    is_set_menu: bool = False,
    price_per_person_cents: int | None = None,
    guest_count: int = 0,
    notes: str | None = None,
    items: list[dict] | None = None,
    note: str | None = None,
) -> FnPreorderMenu:
    """A menu built by hand (no template)."""
    require_live(ctx)
    require_staff(ctx)
    require_venue(ctx, venue_id)
    enquiry, event = _parent(db, ctx, enquiry_id, event_id)
    if event is not None and venue_id != event.venue_id:
        raise Invalid("A booked menu must be at the event's venue.")
    if price_per_person_cents is not None and int(price_per_person_cents) < 0:
        raise Invalid("Prices cannot be negative.")
    menu = FnPreorderMenu(
        organization_id=ctx.org_id,
        enquiry_id=enquiry.id if enquiry is not None else None,
        event_id=event.id if event is not None else None,
        venue_id=venue_id,
        name=require_text(name, "A menu name"),
        is_set_menu=bool(is_set_menu),
        price_per_person_cents=price_per_person_cents,
        guest_count=max(0, int(guest_count or 0)),
        notes=notes,
    )
    db.add(menu)
    flush(db)
    for i, raw in enumerate(items or []):
        db.add(
            FnPreorderMenuItem(
                organization_id=ctx.org_id,
                menu_id=menu.id,
                sort_order=i,
                **_clean_item(raw),
            )
        )
    flush(db)
    _log_food(db, ctx, event, {"menus": [None, {"added": menu.name}]}, note)
    return menu


def update_menu(
    db: Session,
    ctx: FunctionsCtx,
    menu_id: str,
    *,
    expected_version: int | None = None,
    note: str | None = None,
    **fields,
) -> dict:
    """Edit name / set-menu pricing / guest count / notes (staff)."""
    require_live(ctx)
    require_staff(ctx)
    menu = load(db, ctx, FnPreorderMenu, menu_id, label="Menu", for_update=True)
    check_version(menu, expected_version)
    event = _guard_menu(db, ctx, menu)
    if "name" in fields:
        fields["name"] = require_text(fields["name"], "A menu name")
    if fields.get("guest_count") is not None and int(fields["guest_count"]) < 0:
        raise Invalid("Guests cannot be negative.")
    if (
        fields.get("price_per_person_cents") is not None
        and int(fields["price_per_person_cents"]) < 0
    ):
        raise Invalid("Prices cannot be negative.")
    changes = apply_fields(menu, fields, MENU_FIELDS)
    flush(db)
    _log_food(db, ctx, event, {f"menu.{k}": v for k, v in changes.items()}, note)
    return changes


def replace_menu_items(
    db: Session,
    ctx: FunctionsCtx,
    menu_id: str,
    items: list[dict],
    *,
    expected_version: int | None = None,
    note: str | None = None,
) -> list[FnPreorderMenuItem]:
    """Replace every line (staff: this can change prices)."""
    require_live(ctx)
    require_staff(ctx)
    menu = load(db, ctx, FnPreorderMenu, menu_id, label="Menu", for_update=True)
    check_version(menu, expected_version)
    event = _guard_menu(db, ctx, menu)
    clean = [_clean_item(raw) for raw in items]
    existing = _items(db, ctx, menu.id)
    before = _snapshot(existing)
    for x in existing:
        db.delete(x)
    flush(db)
    for i, item in enumerate(clean):
        db.add(
            FnPreorderMenuItem(
                organization_id=ctx.org_id, menu_id=menu.id, sort_order=i, **item
            )
        )
    menu.updated_at = _now()
    flush(db)
    rows = _items(db, ctx, menu.id)
    after = _snapshot(rows)
    if before != after:
        _log_food(db, ctx, event, {f"menu.{menu.name}.items": [before, after]}, note)
    return rows


def set_item_quantities(
    db: Session,
    ctx: FunctionsCtx,
    menu_id: str,
    quantities: dict[str, int],
    *,
    expected_version: int | None = None,
    note: str | None = None,
) -> list[FnPreorderMenuItem]:
    """Set quantities by item id — the portal's edit (clients may)."""
    require_live(ctx)
    menu = load(db, ctx, FnPreorderMenu, menu_id, label="Menu", for_update=True)
    check_version(menu, expected_version)
    event = _guard_menu(db, ctx, menu)
    rows = _items(db, ctx, menu.id)
    by_id = {r.id: r for r in rows}
    unknown = set(quantities) - set(by_id)
    if unknown:
        raise Invalid("Some of those dishes are not on this menu.")
    changes = {}
    for item_id, qty in quantities.items():
        qty = int(qty)
        if qty < 0:
            raise Invalid("Quantities cannot be negative.")
        row = by_id[item_id]
        if row.kind == "header":
            raise Invalid("Section headings have no quantity.")
        if row.quantity != qty:
            changes[f"{menu.name}: {row.name}"] = [row.quantity, qty]
            row.quantity = qty
    if changes:
        menu.updated_at = _now()
    flush(db)
    _log_food(db, ctx, event, changes, note)
    return rows


def distribute_set_menu(
    db: Session,
    ctx: FunctionsCtx,
    menu_id: str,
    *,
    guest_count: int | None = None,
    expected_version: int | None = None,
    note: str | None = None,
) -> list[FnPreorderMenuItem]:
    """Fill a set menu's quantities from the dishes' default % split (and
    set the guest count first, when given)."""
    require_live(ctx)
    require_staff(ctx)
    menu = load(db, ctx, FnPreorderMenu, menu_id, label="Menu", for_update=True)
    check_version(menu, expected_version)
    if not menu.is_set_menu:
        raise Invalid("Only a set menu has a default split.")
    if guest_count is not None:
        update_menu(db, ctx, menu.id, guest_count=guest_count, note=note)
    rows = _items(db, ctx, menu.id)
    plan = set_menu_quantities(rows, menu.guest_count)
    return set_item_quantities(
        db, ctx, menu.id, {rows[i].id: q for i, q in plan.items()}, note=note
    )


def delete_menu(
    db: Session, ctx: FunctionsCtx, menu_id: str, *, note: str | None = None
) -> None:
    require_live(ctx)
    require_staff(ctx)
    menu = load(db, ctx, FnPreorderMenu, menu_id, label="Menu", for_update=True)
    event = _guard_menu(db, ctx, menu)
    name = menu.name
    db.delete(menu)
    flush(db)
    _log_food(db, ctx, event, {"menus": [{"removed": name}, None]}, note)
