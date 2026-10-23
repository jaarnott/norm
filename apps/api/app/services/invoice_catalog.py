"""What an invoice line needs from Loaded's catalogue, and making it when it
isn't there: stock items, units, brands and suppliers — plus the lists the
invoice card picks from.

ONE implementation for every caller: the web card (routers/invoice_fixes.py),
the claude.ai card (mcp/app_tools.py) and autopilot's open gates
(services/invoice_review.apply_open_gates). Callers own access checks and the
audit record; nothing here knows who is asking. That split is deliberate:
autopilot used to call the web endpoints with ``user=None``, and when those
endpoints gained a per-person venue check (3 Oct 2026) every autopilot create
started failing inside it — silently, behind its own ``except``.

Every create is resolve-first: an existing record with the same name (units:
the same pack) is returned with ``created=False`` instead of a duplicate, so a
double click or a retry never litters the catalogue. Each create is the one
Loaded write; the invoice LINE takes the new record as a local draft edit and
lands in Loaded on receive.
"""

from __future__ import annotations

import json
import logging
import math

from sqlalchemy.orm import Session

from app.services.received_invoice import _norm

logger = logging.getLogger(__name__)

UNIT_TYPES = ("Weight", "Volume", "Count")

# Loaded's base unit per type + the unitType enum used on a stock item.
_BASE_UNIT_BY_TYPE = {"Weight": "kilo", "Volume": "litre", "Count": "each"}
_UNIT_TYPE_INDEX = {"Weight": 0, "Volume": 1, "Count": 2}
_PARSED_TYPE = {"weight": "Weight", "volume": "Volume", "count": "Count"}


class CatalogError(Exception):
    """A create or read that can't be done as asked. ``status`` follows HTTP so
    the web router can raise it as-is; the claude.ai card shows the message."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _name_key(text: object) -> str:
    """Case and spacing never distinguish two catalogue names."""
    return " ".join(str(text or "").lower().split())


# ── Reference lists ──────────────────────────────────────────────────────


def units(lh) -> list[dict]:
    rows = lh.get("/1.0/stock/internal/units")
    return [
        {
            "id": u.get("id"),
            "name": u.get("name"),
            "type": u.get("stockUnitType"),
            "ratio": u.get("ratio"),
        }
        for u in (rows or [])
        if isinstance(u, dict) and not u.get("datestampDeleted")
    ]


def brands(lh) -> list[dict]:
    from app.services.invoice_replica import fetch_brands

    return [
        {"id": b.get("id"), "name": b.get("name")}
        for b in fetch_brands(lh)
        if b.get("id") and b.get("name")
    ]


def stock_groups(lh) -> list[dict]:
    from app.services.item_match import _fetch_stock_groups

    return _fetch_stock_groups(lh)


def _component_rows(action: str, venue_id: str, db: Session, config_db: Session):
    from app.services.component_api import ComponentApiError, execute_component_action

    try:
        result = execute_component_action(
            "purchase_order_editor", action, {}, venue_id, db, config_db
        )
    except ComponentApiError as e:
        raise CatalogError(502, str(e)) from e
    rows = result.get("data") if isinstance(result, dict) else result
    return rows if isinstance(rows, list) else (rows or {}).get("data") or []


def suppliers(venue_id: str, db: Session, config_db: Session) -> list[dict]:
    """Reuses the purchase-order editor's configured ``get_suppliers`` so no
    Loaded path is hardcoded here."""
    return [
        {"id": s.get("id"), "name": s.get("name") or s.get("supplierName")}
        for s in _component_rows("get_suppliers", venue_id, db, config_db)
        # Loaded renamed the supplier delete marker datestampDeleted -> removedAt
        # (Aug 2026); read both so either payload vintage filters correctly.
        if isinstance(s, dict) and not (s.get("removedAt") or s.get("datestampDeleted"))
    ]


def stock_items(venue_id: str, db: Session, config_db: Session) -> list[dict]:
    """The stock catalogue, each item with its default supplier variant's code,
    unit and cost resolved (the same way po_display._resolve_lines does) — so a
    line added or linked from it carries what Loaded needs."""
    from app.services.item_match import _default_variant

    out = []
    for i in _component_rows("get_stock_items_detail", venue_id, db, config_db):
        if not isinstance(i, dict):
            continue
        v = _default_variant(i) or {}
        out.append(
            {
                "id": i.get("id"),
                "name": i.get("name"),
                "code": v.get("stockCode"),
                "unit_id": v.get("unitId") or i.get("orderingUnitId"),
                "unit_ratio": v.get("unitRatio") or i.get("orderingUnitRatio") or 1,
                "unit_cost": v.get("unitCost") or 0,
            }
        )
    return out


# ── Units ────────────────────────────────────────────────────────────────


def resolve_unit(lh, proposed: str) -> dict | None:
    """The Loaded unit that IS the proposed unit, read from a fresh unit list
    (creates and the read-back after one need it fresh): the one shared check,
    ``invoice_units.match_unit_record``. A pack ('5x3kg') matches only the same
    pack, never a single size of the same total ('15 KG'), so an unmatched pack
    is created."""
    from app.services.invoice_units import match_unit_record

    rows = lh.get("/1.0/stock/internal/units")
    return match_unit_record(proposed, rows if isinstance(rows, list) else [])


def parsed_unit_spec(name: str) -> dict | None:
    """{ratio, stock_unit_type} read straight off the name, no LLM: '500g' →
    Weight 0.5 (kg), '6x700ml' → Volume 4.2 (litres), '4x6 pack' → Count 24.
    None when the name doesn't parse ('carton', '8x300m')."""
    from app.services.invoice_units import is_multipack, parse_unit

    count = 1.0
    inner = str(name or "")
    if is_multipack(inner):
        s = "".join(inner.lower().split())
        i = s.find("x")
        try:
            count = float(s[:i])
        except ValueError:
            return None
        inner = s[i + 1 :]
    parsed = parse_unit(inner)
    if not parsed:
        return None
    kind, magnitude = parsed
    utype = _PARSED_TYPE.get(kind)
    if not utype:
        return None
    ratio = count * (magnitude if utype == "Count" else magnitude / 1000)
    if ratio <= 0:
        return None
    return {"ratio": round(ratio, 6), "stock_unit_type": utype}


def llm_unit_spec(name: str, db: Session) -> dict | None:
    """Resolve a unit NAME to Loaded's create fields {ratio, stock_unit_type} via
    the LLM. Only used when a unit must be CREATED and its name doesn't parse,
    so the cost is paid rarely; robust to naming conventions ('8x300m',
    '2x2.3KG'). Returns None if no confident spec.
    """
    from app.interpreter.llm_interpreter import call_llm

    schema = {
        "ratio": (
            "number — the unit's size as a multiple of the BASE unit of its "
            "type: Weight is in KILOGRAMS, Volume in LITRES, Count in items. A "
            "multipack 'NxM' means N of M (multiply): '5x3kg' → 15 (5 x 3kg), "
            "'6x700ml' → 4.2, '12x1L' → 12, '4x6 pack' → 24 (4 packs of 6), "
            "'2kg' → 2, '500g' → 0.5, '12 pack' → 12, 'Each' → 1."
        ),
        "stock_unit_type": "one of 'Weight', 'Volume', 'Count'",
    }
    system_prompt = (
        "You convert a stock unit name into its base-unit size and type for a "
        "stock system (Weight→kilograms, Volume→litres, Count→items). Return "
        "ONLY a JSON object matching this schema:\n"
        + json.dumps(schema, indent=1)
        + "\nDimensions that are NOT weight, volume or count — length/metres, "
        "sheets, ply, micron — are DESCRIPTIVE, not the stock dimension: the "
        "unit counts ITEMS. '8x300m' (8 rolls of 300 metres) → "
        '{"ratio": 8, "stock_unit_type": "Count"}; \'2x50m\' → 2.'
        "\nNever guess — if the name carries no size at all (a bare packaging "
        'word like \'carton\'), return {"ratio": null, "stock_unit_type": null}.'
    )
    try:
        parsed, _ = call_llm(
            system_prompt=system_prompt,
            user_prompt=f"Unit name: {name}",
            db=db,
            call_type="extraction",
            max_tokens=200,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("unit-spec resolve failed for %s: %s", name, exc)
        return None
    parsed = parsed if isinstance(parsed, dict) else {}
    try:
        ratio = float(parsed.get("ratio"))
    except (TypeError, ValueError):
        return None
    utype = parsed.get("stock_unit_type")
    if ratio <= 0 or utype not in UNIT_TYPES:
        return None
    return {"ratio": ratio, "stock_unit_type": utype}


def unit_spec(name: str, db: Session) -> dict | None:
    """The name's own reading first; the LLM only when it can't be parsed."""
    return parsed_unit_spec(name) or llm_unit_spec(name, db)


def _unit_out(u: dict) -> dict:
    return {
        "id": u.get("id"),
        "name": u.get("name"),
        "type": u.get("stockUnitType") or u.get("type"),
        "ratio": u.get("ratio"),
    }


def suggest_unit(lh, name: str, db: Session) -> dict:
    """What the card's new-unit form opens with: the unit Loaded already has
    for this name (so the person uses it instead of making a twin), else
    Norm's reading of the name's type and size, for them to check."""
    name = (name or "").strip()
    if not name:
        return {"existing": None, "spec": None}
    existing = resolve_unit(lh, name)
    if existing:
        return {"existing": _unit_out(existing), "spec": None}
    return {"existing": None, "spec": unit_spec(name, db)}


def _manual_spec(ratio: object, unit_type: object) -> dict | None:
    """A person's own type and size, validated. None when they gave neither."""
    if ratio is None and not unit_type:
        return None
    try:
        r = float(ratio)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise CatalogError(400, "the unit's size must be a number") from None
    if not math.isfinite(r) or r <= 0:
        raise CatalogError(400, "the unit's size must be more than zero")
    if unit_type not in UNIT_TYPES:
        raise CatalogError(400, "the unit's type must be Weight, Volume or Count")
    return {"ratio": r, "stock_unit_type": unit_type}


def get_or_create_unit(
    lh,
    name: str,
    db: Session,
    *,
    ratio: object = None,
    unit_type: object = None,
) -> tuple[dict, bool]:
    """The Loaded unit for ``name`` — resolved from the catalogue, else CREATED.

    The size and type are the person's when they gave them (the card's
    new-unit form); otherwise read off the name, and only then asked of the
    LLM (accepting a create_unit suggestion, autopilot). Returns (unit,
    created). Raises CatalogError when no confident definition exists — a junk
    unit is never created.
    """
    name = (name or "").strip()
    if not name:
        raise CatalogError(400, "a unit name is required")
    manual = _manual_spec(ratio, unit_type)
    unit = resolve_unit(lh, name)
    if unit:
        return unit, False
    spec = manual or unit_spec(name, db)
    if not spec:
        raise CatalogError(
            422,
            f"couldn't work out the size of '{name}' — enter its type and size",
        )
    # Spelt as Loaded spells its own units ('720 Grams', '5x3 KG'), not as
    # the invoice printed it ('720g'), unless a person named it.
    from app.services.unit_resolver import unit_name_for

    unit = lh.request(
        "POST",
        "/1.0/stock/internal/units",
        {
            "name": name if manual else (unit_name_for(name) or name),
            "ratio": spec["ratio"],
            "stockUnitType": spec["stock_unit_type"],
        },
    )
    # Loaded returns the created unit; re-fetch by name if the POST response
    # isn't the object itself.
    if not isinstance(unit, dict) or not unit.get("id"):
        unit = resolve_unit(lh, name)
    if not isinstance(unit, dict) or not unit.get("id"):
        raise CatalogError(502, f"created unit '{name}' but could not read it back")
    return unit, True


# ── Brands and suppliers ─────────────────────────────────────────────────


def get_or_create_brand(lh, name: str) -> tuple[dict, bool]:
    """The Loaded brand record for ``name``.

    Loaded refuses to receive a line that names a brand it has no record for.
    ``POST /1.0/stock/internal/brands {name}`` → 201 ``{id, name, masterId,
    datestampDeleted}`` (verified live on the test venue, 11 Aug 2026).
    """
    from app.services.invoice_replica import fetch_brands

    name = (name or "").strip()
    if not name:
        raise CatalogError(400, "a brand name is required")
    existing = next(
        (b for b in fetch_brands(lh) if _norm(b.get("name")) == _norm(name)), None
    )
    if existing:
        return {"id": existing.get("id"), "name": existing.get("name")}, False
    created = lh.request("POST", "/1.0/stock/internal/brands", {"name": name})
    brand_id = created.get("id") if isinstance(created, dict) else None
    if not brand_id:
        raise CatalogError(502, "Loaded did not return the created brand")
    return {"id": brand_id, "name": created.get("name") or name}, True


def supplier_body(name: str, details: dict | None = None) -> dict:
    """The POST /suppliers body: the name, plus the details the invoice
    prints (invoice_extraction.read_supplier_details) in Loaded's fields.
    Only what was read is sent. Two fields are deliberately left at Loaded's
    defaults: ``emailEnabled`` (it makes Loaded EMAIL purchase orders to that
    address, and an invoice prints the accounts address as often as the
    orders one) and ``unitCostIncludesTax`` (Norm reads every cost ex tax)."""
    body: dict = {"name": name}
    details = details if isinstance(details, dict) else {}
    lines = [str(x) for x in details.get("address_lines") or [] if x][:3]
    for i, line in enumerate(lines):
        body[f"address{i + 1}"] = line
    for src, dst in (
        ("city", "city"),
        ("postcode", "postcode"),
        ("phone", "phone1"),
        ("contact_name", "contactName"),
        ("account_number", "customerNumber"),
    ):
        if details.get(src):
            body[dst] = str(details[src])
    if details.get("email"):
        body["emails"] = [{"email": str(details["email"]), "isPrimary": True}]
    return body


def draft_supplier_details(db: Session, venue_id: str, invoice_id: str) -> dict:
    """The details the review read for the supplier this invoice's draft
    proposes creating (the create_supplier suggestion's payload) — so a
    create from the card takes them without the card carrying them."""
    from app.services.received_invoice import open_drafts

    if not invoice_id:
        return {}
    for doc in open_drafts(db, venue_id, invoice_id):
        for sg in (doc.data or {}).get("suggestions") or []:
            if isinstance(sg, dict) and sg.get("kind") == "create_supplier":
                details = (sg.get("payload") or {}).get("details")
                if isinstance(details, dict) and details:
                    return details
    return {}


def get_or_create_supplier(
    lh, name: str, details: dict | None = None
) -> tuple[dict, bool]:
    """The Loaded supplier for ``name`` — resolved if a record already covers it
    (normalized containment, the supplier-matching convention — never a
    duplicate record), else created (POST /suppliers, verified live 08 Aug
    2026: 201 + the created object) with ``details`` (``supplier_body``). An
    existing record is never touched."""
    name = (name or "").strip()
    if not name:
        raise CatalogError(400, "a supplier name is required")
    rows = lh.get("/1.0/stock/internal/suppliers")
    rows = rows if isinstance(rows, list) else []
    target = _norm(name)
    for s in rows:
        if not isinstance(s, dict) or s.get("removedAt") or s.get("datestampDeleted"):
            continue
        cand = _norm(s.get("name"))
        if (
            len(cand) >= 3
            and len(target) >= 3
            and (cand == target or cand in target or target in cand)
        ):
            return {"id": s.get("id"), "name": s.get("name")}, False
    created = lh.request(
        "POST", "/1.0/stock/internal/suppliers", supplier_body(name, details)
    )
    if not isinstance(created, dict) or not created.get("id"):
        raise CatalogError(502, "Loaded did not return the created supplier")
    return {"id": created.get("id"), "name": created.get("name") or name}, True


# ── Stock items ──────────────────────────────────────────────────────────


def _line_source(
    db: Session,
    lh,
    venue_id: str,
    invoice_id: str,
    line_id: str,
    doc_data: dict | None = None,
) -> tuple[dict, dict]:
    """(line, header) the new item is described from: the draft first — it
    holds lines Loaded's scan missed, and the supplier the person picked —
    else Loaded's invoice. Each as {code, description, unit_cost (ex tax),
    tax_rate} and {supplier_id}. ``tax_rate`` is the line's rate as Norm
    decided it from the invoice, or None: Loaded's own line rate is its
    default, not the invoice's. ``doc_data`` is a draft the caller already
    holds (autopilot mid-pass); otherwise the invoice's open draft is read."""
    from app.services.received_invoice import open_drafts

    if doc_data is None:
        docs = open_drafts(db, venue_id, invoice_id)
        doc_data = (docs[0].data or {}) if docs else {}
    ln = next(
        (
            x
            for x in doc_data.get("lines") or []
            if isinstance(x, dict) and str(x.get("id")) == str(line_id)
        ),
        None,
    )
    if ln is not None:
        cost = ln.get("unit_cost")
        rate = ln.get("sale_tax_rate")
        if doc_data.get("unit_cost_includes_tax") and cost is not None and rate:
            try:
                cost = float(cost) / (1 + float(rate))
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        return (
            {
                "code": ln.get("code"),
                "description": ln.get("description"),
                "unit_cost": cost,
                "tax_rate": rate,
            },
            {"supplier_id": doc_data.get("linked_supplier_id")},
        )
    inv = lh.invoice(invoice_id)
    ln = next((x for x in inv.get("lines") or [] if x.get("id") == line_id), None)
    if not ln:
        raise CatalogError(404, "invoice line not found")
    return (
        {
            "code": ln.get("code"),
            "description": ln.get("description"),
            # invoice-line cost: Loaded renamed unitCost → unitCostExclTax
            "unit_cost": (
                ln.get("unitCostExclTax")
                if ln.get("unitCostExclTax") is not None
                else ln.get("unitCost")
            ),
            "tax_rate": None,
        },
        {"supplier_id": inv.get("linkedSupplierId")},
    )


def tax_sort_order(lh, rate: object) -> int:
    """The venue's tax setting (Loaded's ``globalSalesTaxSortOrder``) for a
    line charged at ``rate``: the setting with that rate in the venue's own tax
    table, so a GST-free line makes a GST-free item. GST (1) when the rate or
    the table is unknown — what every new item got before (21 Oct 2026: 138
    Grange Gully Bistro food items were set to GST; basic food in Australia is
    GST-free)."""
    from app.services.invoice_replica import sales_tax_rates

    try:
        wanted = float(rate)
    except (TypeError, ValueError):
        return 1
    table = sales_tax_rates(lh)
    hits = [order for order, r in table.items() if abs(r - wanted) < 0.0005]
    return min(hits) if hits else 1


def create_item(
    lh,
    db: Session,
    config_db: Session,
    *,
    venue_id: str,
    invoice_id: str,
    line_id: str,
    name: str | None,
    group_id: str,
    unit_id: str | None,
    brand_id: str | None = None,
    supplier_id: str | None = None,
    doc_data: dict | None = None,
) -> dict:
    """Create a NEW stock item (+ its supplier variant) for an invoice line.

    A same-named item already in the catalogue is returned instead
    (``created=False``) and the card links it — the variant for this supplier
    is registered on receive, as for any linked line. Verified live: POST
    /1.0/stock/internal/items with the variant embedded in ``suppliers[]``
    returns the created item.
    """
    line, header = _line_source(db, lh, venue_id, invoice_id, line_id, doc_data)
    name = (name or line.get("description") or line.get("code") or "").strip()
    if not name or not group_id:
        raise CatalogError(400, "a name and a stock group are required")
    if not unit_id:
        raise CatalogError(400, "choose the unit it comes in")
    supplier_id = supplier_id or header.get("supplier_id")
    if not supplier_id:
        # A draft that never carried the header link: Loaded's own invoice.
        supplier_id = (lh.invoice(invoice_id) or {}).get("linkedSupplierId")
    if not supplier_id:
        raise CatalogError(400, "choose the invoice's supplier first")

    all_units = lh.get("/1.0/stock/internal/units") or []
    unit = next((u for u in all_units if u.get("id") == unit_id), None)
    if not unit:
        raise CatalogError(400, "unit not found in Loaded")
    unit_out = {
        "unit_id": unit_id,
        "unit_name": unit.get("name"),
        "unit_ratio": unit.get("ratio"),
    }

    key = _name_key(name)
    same = next(
        (
            i
            for i in stock_items(venue_id, db, config_db)
            if i.get("id") and _name_key(i.get("name")) == key
        ),
        None,
    )
    if same:
        return {
            "created": False,
            "item_id": same["id"],
            "item_name": same.get("name") or name,
            **unit_out,
        }

    utype = unit.get("stockUnitType") or "Weight"
    base = (
        next(
            (
                u
                for u in all_units
                if _norm(u.get("name")) == _BASE_UNIT_BY_TYPE.get(utype, "")
                and not u.get("datestampDeleted")
            ),
            None,
        )
        or unit
    )
    payload = {
        "name": name,
        "groupId": group_id,
        "unitType": _UNIT_TYPE_INDEX.get(utype, 0),
        "countingUnitId": base.get("id"),
        "countingUnitRatio": base.get("ratio") or 1.0,
        "orderingUnitId": unit_id,
        "orderingUnitRatio": unit.get("ratio") or 1.0,
        "globalSalesTaxSortOrder": tax_sort_order(lh, line.get("tax_rate")),
        "defaultSupplierId": supplier_id,
        "defaultBrandId": brand_id,
        "itemType": "Default",
        "suppliers": [
            {
                "supplierId": supplier_id,
                "stockCode": line.get("code"),
                "unitId": unit_id,
                "unitCost": line.get("unit_cost"),
                "brandId": brand_id,
                "defaultForSupplier": True,
                "description": line.get("description"),
            }
        ],
    }
    created = lh.request("POST", "/1.0/stock/internal/items", payload)
    item_id = created.get("id") if isinstance(created, dict) else None
    if not item_id:
        raise CatalogError(502, "Loaded did not return the created stock item")
    return {"created": True, "item_id": item_id, "item_name": name, **unit_out}
