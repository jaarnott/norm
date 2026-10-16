"""Templates — the venue's pre-order menus and its canned replies.

**Pre-order menu templates** are what ``menus.add_menu_from_template`` copies
onto an enquiry or event: sections (``kind='header'`` rows) and dishes. A set
menu is priced per person and its dishes are unpriced, each carrying a default
% of its section that ``distribute_set_menu`` turns into quantities; an à la
carte template prices every dish. Dishes link to LoadedHub by recipe or stock
item id (text references: Loaded owns them), which is what lets the kitchen
cost and prep a pre-order — a dish with no link is allowed, but reported, so
the gap is visible rather than silent.

Templates are rewritten in place (items replaced as a list); menus already
built from one keep their own snapshot. A template that menus point at is
retired (``active=false``), never deleted.

**Cloning a Loaded menu** reads it through Norm's own ``loadedhub.get_menus``
tool (the same executor charts and MCP use), so the menu, its sections and
each line's recipe / stock link come straight from Loaded.

**Reply templates** are the canned first replies and follow-ups, per booking
type (or any) and per venue (or all).
"""

from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.functions_models import (
    BOOKING_TYPES,
    MENU_ITEM_KINDS,
    REPLY_TEMPLATE_STAGES,
    FnPreorderMenu,
    FnPreorderTemplate,
    FnPreorderTemplateItem,
    FnReplyTemplate,
)
from app.db.models import _now
from app.services.functions._rows import apply_fields, check_version, load, require_text
from app.services.functions.ctx import (
    FunctionsCtx,
    require_live,
    require_staff,
    require_venue,
)
from app.services.functions.errors import Forbidden, Invalid, flush
from app.services.functions.menus import sections as menu_sections
from app.services.functions.proposals import (
    MAX_LINE_CHARS,
    cap_text,
    sanitise_fragment,
)

TEMPLATE_FIELDS = (
    "name",
    "description",
    "is_set_menu",
    "price_per_person_cents",
    "active",
    "sort_order",
)
TEMPLATE_ITEM_FIELDS = (
    "kind",
    "name",
    "description",
    "unit_price_cents",
    "default_percent",
    "dietaries",
    "loaded_recipe_id",
    "loaded_stock_item_id",
)
REPLY_FIELDS = (
    "venue_id",
    "booking_type",
    "stage",
    "name",
    "subject",
    "body_html",
    "active",
    "sort_order",
)
#: Bounds on what a template stores (a real one has a few dozen dishes).
MAX_ITEMS = 1_000
MAX_ITEM_TEXT = 10_000
MAX_ID_CHARS = 200
#: The dietary codes Orbit's menus used (GF, DF, V, VG, NF). Any short tag is
#: accepted — imported menus carry others — these are what the editor offers.
DIETARY_CODES = ("GF", "DF", "V", "VG", "NF", "H")


# ---------------------------------------------------------------------------
# Pre-order templates
# ---------------------------------------------------------------------------


def list_templates(
    db: Session,
    ctx: FunctionsCtx,
    *,
    venue_id: str | None = None,
    include_inactive: bool = False,
) -> list[FnPreorderTemplate]:
    q = db.query(FnPreorderTemplate).filter(
        FnPreorderTemplate.organization_id == ctx.org_id,
        ctx.venue_clause(FnPreorderTemplate.venue_id),
    )
    if venue_id:
        require_venue(ctx, venue_id)
        q = q.filter(FnPreorderTemplate.venue_id == venue_id)
    if not include_inactive:
        q = q.filter(FnPreorderTemplate.active.is_(True))
    return q.order_by(FnPreorderTemplate.sort_order, FnPreorderTemplate.name).all()


def template_items(
    db: Session, ctx: FunctionsCtx, template_ids
) -> dict[str, list[FnPreorderTemplateItem]]:
    ids = list(template_ids)
    out: dict[str, list] = {t: [] for t in ids}
    if not ids:
        return out
    for item in (
        db.query(FnPreorderTemplateItem)
        .filter(
            FnPreorderTemplateItem.organization_id == ctx.org_id,
            FnPreorderTemplateItem.template_id.in_(ids),
        )
        .order_by(FnPreorderTemplateItem.sort_order, FnPreorderTemplateItem.created_at)
    ):
        out[item.template_id].append(item)
    return out


def get_template(db: Session, ctx: FunctionsCtx, template_id: str):
    """``(template, items)``."""
    t = load(db, ctx, FnPreorderTemplate, template_id, label="Menu template")
    return t, template_items(db, ctx, [t.id])[t.id]


def _clean_dietaries(value) -> list[str]:
    out: list[str] = []
    if len(value or []) > 30:
        raise Invalid("A dish carries at most 30 dietary tags.")
    for raw in value or []:
        tag = str(raw or "").strip()
        if not tag:
            continue
        if len(tag) > 24:
            raise Invalid("Dietary tags are short codes like GF, DF, V, VG, NF.")
        if tag.upper() in DIETARY_CODES:
            tag = tag.upper()
        if tag not in out:
            out.append(tag)
    return out


def _clean_template_items(items, *, is_set_menu: bool) -> list[dict]:
    if not isinstance(items, list):
        raise Invalid("Items must be a list.")
    if len(items) > MAX_ITEMS:
        raise Invalid(f"A menu template holds at most {MAX_ITEMS:,} lines.")
    out = []
    for raw in items:
        if not isinstance(raw, dict):
            raise Invalid("Each item needs a kind and a name.")
        unknown = set(raw) - set(TEMPLATE_ITEM_FIELDS) - {"id"}
        if unknown:
            raise Invalid(f"Cannot set: {', '.join(sorted(unknown))}.")
        kind = raw.get("kind") or "item"
        if kind not in MENU_ITEM_KINDS:
            raise Invalid(f"Kind must be one of {', '.join(MENU_ITEM_KINDS)}.")
        what = "A section name" if kind == "header" else "A dish name"
        item = {
            "kind": kind,
            "name": cap_text(require_text(raw.get("name"), what), what, MAX_LINE_CHARS),
            "description": cap_text(
                (raw.get("description") or "").strip() or None,
                "A dish description",
                MAX_ITEM_TEXT,
            ),
            "unit_price_cents": None,
            "default_percent": None,
            "dietaries": [],
            "loaded_recipe_id": None,
            "loaded_stock_item_id": None,
        }
        if kind == "item":
            price = raw.get("unit_price_cents")
            if price is not None and int(price) < 0:
                raise Invalid("Prices cannot be negative.")
            # A set menu's dishes are unpriced — the menu is priced per
            # person (menus.add_menu_from_template drops them anyway).
            item["unit_price_cents"] = (
                None if is_set_menu or price is None else int(price)
            )
            pct = raw.get("default_percent")
            if pct is not None and not 0 <= int(pct) <= 100:
                raise Invalid("A default % is between 0 and 100.")
            item["default_percent"] = None if pct is None else int(pct)
            item["dietaries"] = _clean_dietaries(raw.get("dietaries"))
            item["loaded_recipe_id"] = cap_text(
                str(raw.get("loaded_recipe_id") or "").strip() or None,
                "A Loaded recipe id",
                MAX_ID_CHARS,
            )
            item["loaded_stock_item_id"] = cap_text(
                str(raw.get("loaded_stock_item_id") or "").strip() or None,
                "A Loaded stock item id",
                MAX_ID_CHARS,
            )
        out.append(item)
    return out


def template_warnings(template: FnPreorderTemplate, items) -> list[str]:
    """What a person should fix but may save: dishes with no Loaded link,
    and set-menu sections whose default % don't add up to 100 (the split
    still works — weights are normalised — but the editor's numbers lie)."""
    warnings = []
    unlinked = [
        i.name
        for i in items
        if i.kind == "item" and not (i.loaded_recipe_id or i.loaded_stock_item_id)
    ]
    if unlinked:
        warnings.append(
            f"{len(unlinked)} dish{'es' if len(unlinked) != 1 else ''} not linked "
            "to a Loaded recipe or stock item: " + ", ".join(unlinked[:5])
        )
    if template.is_set_menu:
        for section in menu_sections(items):
            dishes = [items[i] for i in section]
            total = sum(int(d.default_percent or 0) for d in dishes)
            if dishes and total not in (0, 100):
                warnings.append(
                    f"The default % in the section with {dishes[0].name} add up to "
                    f"{total}, not 100."
                )
    if template.is_set_menu and template.price_per_person_cents is None:
        warnings.append("This set menu has no price per person.")
    return warnings


def _write_items(db: Session, ctx: FunctionsCtx, template, items: list[dict]) -> None:
    db.query(FnPreorderTemplateItem).filter(
        FnPreorderTemplateItem.organization_id == ctx.org_id,
        FnPreorderTemplateItem.template_id == template.id,
    ).delete(synchronize_session="fetch")
    flush(db)
    for i, item in enumerate(items):
        db.add(
            FnPreorderTemplateItem(
                organization_id=ctx.org_id,
                template_id=template.id,
                sort_order=i,
                **item,
            )
        )
    flush(db)


def create_template(
    db: Session,
    ctx: FunctionsCtx,
    *,
    venue_id: str,
    name: str,
    description: str | None = None,
    is_set_menu: bool = False,
    price_per_person_cents: int | None = None,
    sort_order: int = 0,
    items: list[dict] | None = None,
    loaded_menu_id: str | None = None,
) -> FnPreorderTemplate:
    require_live(ctx)
    require_staff(ctx)
    require_venue(ctx, venue_id)
    from app.services.functions.venue_profile import get_venue

    get_venue(db, ctx, venue_id)
    if price_per_person_cents is not None and int(price_per_person_cents) < 0:
        raise Invalid("Prices cannot be negative.")
    clean_items = _clean_template_items(items or [], is_set_menu=bool(is_set_menu))
    template = FnPreorderTemplate(
        organization_id=ctx.org_id,
        venue_id=venue_id,
        name=cap_text(require_text(name, "A menu name"), "A menu name", MAX_LINE_CHARS),
        description=cap_text((description or "").strip() or None, "A menu description"),
        is_set_menu=bool(is_set_menu),
        price_per_person_cents=(
            int(price_per_person_cents) if price_per_person_cents is not None else None
        ),
        loaded_menu_id=loaded_menu_id,
        sort_order=int(sort_order or 0),
    )
    db.add(template)
    flush(db)
    _write_items(db, ctx, template, clean_items)
    return template


def update_template(
    db: Session,
    ctx: FunctionsCtx,
    template_id: str,
    *,
    expected_version: int | None = None,
    items: list[dict] | None = None,
    **fields,
) -> tuple[FnPreorderTemplate, dict]:
    """Edit the template (and, with ``items``, replace its dishes in the same
    version step). Returns ``(template, changes)``."""
    require_live(ctx)
    require_staff(ctx)
    template = load(
        db, ctx, FnPreorderTemplate, template_id, label="Menu template", for_update=True
    )
    check_version(template, expected_version)
    clean = dict(fields)
    if "name" in clean:
        clean["name"] = cap_text(
            require_text(clean["name"], "A menu name"), "A menu name", MAX_LINE_CHARS
        )
    if "description" in clean:
        clean["description"] = cap_text(
            (clean["description"] or "").strip() or None, "A menu description"
        )
    if (
        clean.get("price_per_person_cents") is not None
        and int(clean["price_per_person_cents"]) < 0
    ):
        raise Invalid("Prices cannot be negative.")
    for flag in ("is_set_menu", "active"):
        if flag in clean:
            if clean[flag] is None:
                raise Invalid(f"{flag} must be true or false.")
            clean[flag] = bool(clean[flag])
    changes = apply_fields(template, clean, TEMPLATE_FIELDS)
    if items is not None:
        _write_items(
            db,
            ctx,
            template,
            _clean_template_items(items, is_set_menu=template.is_set_menu),
        )
        changes["items"] = [None, len(items)]
    elif "is_set_menu" in changes and template.is_set_menu:
        # Became a set menu: its dishes lose their prices.
        for item in template_items(db, ctx, [template.id])[template.id]:
            item.unit_price_cents = None
    if changes:
        # Touch the row so its version moves even when only the items did.
        template.updated_at = _now()
    flush(db)
    return template, changes


def delete_template(db: Session, ctx: FunctionsCtx, template_id: str) -> str:
    """Delete a template no menu was built from; retire one that menus point
    at. Returns ``"deleted"`` or ``"retired"``."""
    require_live(ctx)
    require_staff(ctx)
    template = load(db, ctx, FnPreorderTemplate, template_id, label="Menu template")
    used = (
        db.query(FnPreorderMenu.id)
        .filter(
            FnPreorderMenu.organization_id == ctx.org_id,
            FnPreorderMenu.template_id == template.id,
        )
        .first()
    )
    if used:
        template.active = False
        flush(db)
        return "retired"
    db.delete(template)
    flush(db)
    return "deleted"


# ---------------------------------------------------------------------------
# LoadedHub — menus to clone, recipes to link
# ---------------------------------------------------------------------------


def loaded_menus(db: Session, config_db: Session, ctx: FunctionsCtx, venue_id: str):
    """The venue's live Loaded menus (``{id, name, sections, lines}``), or
    ``(None, reason)`` when Loaded can't be reached for this venue."""
    from app.services.functions.reports import loaded_call
    from app.services.functions.venue_profile import get_venue

    venue = get_venue(db, ctx, venue_id)
    payload, error = loaded_call(db, config_db, venue, "get_menus", {})
    if error:
        return None, error
    menus = payload.get("menus") if isinstance(payload, dict) else None
    if not isinstance(menus, list):
        return None, "Loaded returned no menu list for this venue."
    return [
        {
            "id": m.get("id"),
            "name": m.get("name"),
            "sections": m.get("sections"),
            "lines": m.get("lines"),
            "created": m.get("created"),
        }
        for m in menus
        if isinstance(m, dict) and m.get("id")
    ], None


def _price_cents(value) -> int | None:
    try:
        return None if value is None else int(round(float(value) * 100))
    except (TypeError, ValueError):
        return None


def items_from_loaded_menu(menu: dict, *, is_set_menu: bool) -> list[dict]:
    """Template items from Loaded's raw MenuModel: each group becomes a
    section header, each line a dish carrying its price (à la carte) and the
    recipe or stock item it sells."""
    items: list[dict] = []
    groups = [g for g in (menu.get("groups") or []) if isinstance(g, dict)]
    groups.sort(key=lambda g: g.get("groupOrder") or 0)
    for g in groups:
        lines = [
            ln
            for ln in (g.get("lines") or [])
            if isinstance(ln, dict) and not ln.get("deletedAt") and ln.get("name")
        ]
        if not lines:
            continue
        items.append({"kind": "header", "name": str(g.get("name") or "Menu")})
        lines.sort(key=lambda ln: ln.get("lineOrder") or 0)
        for ln in lines:
            items.append(
                {
                    "kind": "item",
                    "name": str(ln.get("name")).strip(),
                    "description": (ln.get("description") or None),
                    "unit_price_cents": None
                    if is_set_menu
                    else _price_cents(ln.get("workingPrice")),
                    "loaded_recipe_id": ln.get("recipeId") or None,
                    "loaded_stock_item_id": ln.get("stockItemId") or None,
                }
            )
    return items


def clone_loaded_menu(
    db: Session,
    config_db: Session,
    ctx: FunctionsCtx,
    *,
    venue_id: str,
    loaded_menu_id: str,
    name: str | None = None,
    is_set_menu: bool = False,
    price_per_person_cents: int | None = None,
) -> FnPreorderTemplate:
    """A new template from one of the venue's Loaded menus. Raises
    ``Invalid`` (with Loaded's reason) when the menu can't be read."""
    require_live(ctx)
    require_staff(ctx)
    from app.services.functions.reports import loaded_call
    from app.services.functions.venue_profile import get_venue

    venue = get_venue(db, ctx, venue_id)
    menu_id = require_text(loaded_menu_id, "A Loaded menu")
    payload, error = loaded_call(
        db, config_db, venue, "get_menus", {"menu_id": menu_id, "detail": "full"}
    )
    if error or not isinstance(payload, dict):
        raise Invalid(f"Couldn't read that Loaded menu: {error or 'no data'}.")
    items = items_from_loaded_menu(payload, is_set_menu=is_set_menu)
    if not items:
        raise Invalid("That Loaded menu has no dishes to copy.")
    return create_template(
        db,
        ctx,
        venue_id=venue.id,
        name=(name or "").strip() or str(payload.get("name") or "Loaded menu"),
        is_set_menu=is_set_menu,
        price_per_person_cents=price_per_person_cents,
        items=items,
        loaded_menu_id=menu_id,
    )


def loaded_recipes(
    db: Session, config_db: Session, ctx: FunctionsCtx, venue_id: str, query: str
):
    """Recipes matching ``query`` at the venue (``[{id, name}]``) for linking
    a dish, or ``(None, reason)``."""
    from app.services.functions.reports import loaded_call
    from app.services.functions.venue_profile import get_venue

    venue = get_venue(db, ctx, venue_id)
    text = (query or "").strip()
    if len(text) < 2:
        return [], None
    payload, error = loaded_call(
        db, config_db, venue, "get_recipes", {"query": text, "limit": 25}
    )
    if error:
        return None, error
    rows = payload
    if isinstance(payload, dict):
        rows = (
            payload.get("matches") or payload.get("recipes") or payload.get("results")
        )
        if rows is None and payload.get("id"):
            rows = [payload]
    if not isinstance(rows, list):
        return [], None
    return [
        {"id": r.get("id") or r.get("recipe_id"), "name": r.get("name")}
        for r in rows
        if isinstance(r, dict) and (r.get("id") or r.get("recipe_id"))
    ], None


# ---------------------------------------------------------------------------
# Reply templates
# ---------------------------------------------------------------------------


def list_reply_templates(
    db: Session,
    ctx: FunctionsCtx,
    *,
    venue_id: str | None = None,
    booking_type: str | None = None,
    stage: str | None = None,
    include_inactive: bool = False,
) -> list[FnReplyTemplate]:
    """Templates that apply: a venue's own and the all-venue ones; a booking
    type's own and the any-type ones."""
    q = db.query(FnReplyTemplate).filter(
        FnReplyTemplate.organization_id == ctx.org_id,
        ctx.venue_clause(FnReplyTemplate.venue_id, allow_null=True),
    )
    if venue_id:
        require_venue(ctx, venue_id)
        q = q.filter(
            or_(
                FnReplyTemplate.venue_id.is_(None), FnReplyTemplate.venue_id == venue_id
            )
        )
    if booking_type:
        q = q.filter(
            or_(
                FnReplyTemplate.booking_type.is_(None),
                FnReplyTemplate.booking_type == booking_type,
            )
        )
    if stage:
        q = q.filter(FnReplyTemplate.stage == stage)
    if not include_inactive:
        q = q.filter(FnReplyTemplate.active.is_(True))
    return q.order_by(FnReplyTemplate.sort_order, FnReplyTemplate.name).all()


def _clean_reply(ctx: FunctionsCtx, fields: dict) -> dict:
    unknown = set(fields) - set(REPLY_FIELDS)
    if unknown:
        raise Invalid(f"Cannot set: {', '.join(sorted(unknown))}.")
    clean = dict(fields)
    if clean.get("venue_id") is not None:
        require_venue(ctx, clean["venue_id"])
    if clean.get("booking_type") is not None and clean["booking_type"] not in (
        BOOKING_TYPES
    ):
        raise Invalid(f"Booking type must be one of {', '.join(BOOKING_TYPES)}.")
    if "stage" in clean and clean["stage"] not in REPLY_TEMPLATE_STAGES:
        raise Invalid(f"Stage must be one of {', '.join(REPLY_TEMPLATE_STAGES)}.")
    if "name" in clean:
        clean["name"] = cap_text(
            require_text(clean["name"], "A template name"),
            "A template name",
            MAX_LINE_CHARS,
        )
    if "subject" in clean:
        clean["subject"] = cap_text(
            (clean["subject"] or "").strip() or None, "A subject", MAX_LINE_CHARS
        )
    if "body_html" in clean:
        # It goes into the composer and out by email: sanitised like a
        # proposal (no script, handlers or forms), capped first.
        clean["body_html"] = sanitise_fragment(clean["body_html"], "A reply template")
    if "active" in clean:
        if clean["active"] is None:
            raise Invalid("active must be true or false.")
        clean["active"] = bool(clean["active"])
    return clean


def create_reply_template(db: Session, ctx: FunctionsCtx, **fields) -> FnReplyTemplate:
    require_live(ctx)
    require_staff(ctx)
    clean = _clean_reply(ctx, {"stage": fields.get("stage"), **fields})
    if clean.get("venue_id") is None and not ctx.is_owner:
        # An all-venue template shows at venues the author can't see.
        raise Forbidden("Choose a venue — only an owner can add an all-venue template.")
    row = FnReplyTemplate(organization_id=ctx.org_id, **clean)
    db.add(row)
    flush(db)
    return row


def _reply(db: Session, ctx: FunctionsCtx, template_id: str) -> FnReplyTemplate:
    return load(
        db,
        ctx,
        FnReplyTemplate,
        template_id,
        label="Reply template",
        allow_null_venue=True,
    )


def get_reply_template(db: Session, ctx: FunctionsCtx, template_id: str):
    return _reply(db, ctx, template_id)


def update_reply_template(
    db: Session,
    ctx: FunctionsCtx,
    template_id: str,
    *,
    expected_version: int | None = None,
    **fields,
) -> tuple[FnReplyTemplate, dict]:
    require_live(ctx)
    require_staff(ctx)
    row = _reply(db, ctx, template_id)
    check_version(row, expected_version)
    clean = _clean_reply(ctx, fields)
    if row.venue_id is None and not ctx.is_owner:
        raise Forbidden("Only an owner can change an all-venue template.")
    if "venue_id" in clean and clean["venue_id"] is None and not ctx.is_owner:
        raise Forbidden("Only an owner can make a template apply to every venue.")
    changes = apply_fields(row, clean, REPLY_FIELDS)
    flush(db)
    return row, changes


def delete_reply_template(db: Session, ctx: FunctionsCtx, template_id: str) -> None:
    require_live(ctx)
    require_staff(ctx)
    row = _reply(db, ctx, template_id)
    if row.venue_id is None and not ctx.is_owner:
        raise Forbidden("Only an owner can delete an all-venue template.")
    db.delete(row)
    flush(db)
