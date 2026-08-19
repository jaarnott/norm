"""Match supplier-invoice lines to a venue's stock catalogue — an LLM function.

An "LLM function" in Norm is an internal handler whose body is one
schema-constrained ``call_llm``: typed input → one bounded model call → typed
output. No conversation, no tool choice, no writes. ``norm.resolve_dates`` is
the precedent; this module backs the second one, ``norm.match_stock_items``,
so the review engine (``review_and_receive_invoices``) can attach item-match
suggestions to its artifact via the same ``call_api`` it uses for everything
else. See docs/tool-architecture-strategy.md — this replaces the bespoke
``/invoice-fixes/match-items`` reasoning endpoint rather than adding a new
mechanism.

Moved verbatim from ``routers/invoice_fixes.py`` (which re-imports the private
names so its endpoints and tests keep working) — one matcher, every surface.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _default_variant(item: dict) -> dict | None:
    """The variant the PO/receive editors default to: the default-supplier's
    default variant, else any default variant, else the first."""
    variants = [v for v in (item.get("suppliers") or []) if isinstance(v, dict)]
    ds = str(item.get("defaultSupplierId") or "")
    return (
        next(
            (
                v
                for v in variants
                if str(v.get("supplierId")) == ds and v.get("defaultForSupplier")
            ),
            None,
        )
        or next((v for v in variants if v.get("defaultForSupplier")), None)
        or (variants[0] if variants else None)
    )


def _fetch_raw_stock_items(
    venue_id: str, db: Session, config_db: Session
) -> list[dict]:
    """The venue's full stock catalogue (raw component-api shape, unpaged) — keeps
    ``groupName`` and ``suppliers[]`` which the slimmed ``list_stock_items`` drops.

    Raises ComponentApiError on a misconfigured/unreachable catalogue — the
    public ``suggest_item_matches`` swallows it into ``{}``.
    """
    from app.services.component_api import execute_component_action

    result = execute_component_action(
        "purchase_order_editor", "get_stock_items_detail", {}, venue_id, db, config_db
    )
    items = result.get("data") if isinstance(result, dict) else result
    items = items if isinstance(items, list) else (items or {}).get("data") or []
    return [i for i in items if isinstance(i, dict) and i.get("id")]


def _fetch_stock_groups(lh) -> list[dict]:
    """Loaded stock groups (subcategories) as ``{id, name, category}``."""
    g = lh.get("/1.0/stock/internal/subcategories")
    rows = g if isinstance(g, list) else (g or {}).get("data") or []
    return [
        {"id": r.get("id"), "name": r.get("name"), "category": r.get("categoryName")}
        for r in rows
        if isinstance(r, dict) and r.get("id")
    ]


def _new_item_lines(inv: dict) -> list[dict]:
    """The invoice's live lines with no ``linkedItemId`` (a NEW/unmatched item)."""
    out = []
    for ln in inv.get("lines") or []:
        if ln.get("deletedAt") or ln.get("linkedItemId"):
            continue
        out.append(
            {
                "id": ln.get("id"),
                "description": ln.get("description") or "",
                "code": ln.get("code") or "",
                "brand": ln.get("brand") or "",
                "unit": ln.get("unit") or "",
            }
        )
    return out


def _as_int(x) -> int | None:
    """Coerce an LLM-returned index to int (json may hand back int/float/str)."""
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, float) and x.is_integer():
        return int(x)
    if isinstance(x, str) and x.strip().lstrip("-").isdigit():
        return int(x)
    return None


_MATCH_SYSTEM_PROMPT = (
    "You match a supplier invoice line to a venue's EXISTING stock catalogue, or "
    "say it is new. For each invoice line, decide whether the SAME core product is "
    "already in the CATALOGUE below.\n\n"
    "MATCHING RULES — be strict; a wrong match and a needless duplicate both cause "
    "problems:\n"
    "- Match ONLY when it is the same core product.\n"
    "- FOOD: ignore brand, size and packaging (invoice 'Spianata Piccante 2kg C6' "
    "matches catalogue 'SPIANATA PICCANTE').\n"
    "- BEVERAGE: the brand matters — a different brand is a different product; "
    "ignore size/packaging.\n"
    "- Naming VARIATIONS of the same branded product ARE a match: invoice "
    "'SAILOR JERRY SPICED RUM' matches catalogue 'SAILOR JERRY RUM' (same "
    "brand, same product, fuller name).\n"
    "- WINE: the brand/label matters; ignore vintage, size and packaging.\n"
    "- Freight/delivery/courier/fuel/fee lines are ORDINARY lines — match "
    "them to the venue's existing item for that service like any other "
    "product: 'Minimum Freight' matches 'FREIGHT - BEVERAGE' or 'Freight'. "
    "When several exist, pick the one appropriate to the SUPPLIER's "
    "department (a liquor supplier's freight → the beverage freight item; a "
    "food supplier's → the food one; else the generic one). Different "
    "services never match (a card fee is not freight).\n"
    "- Delivery-container and packaging CHARGES (crate/carton/pallet/keg "
    "charges and deposits, e.g. 'CARTONS VEGE-PACKING', 'CRATE CHARGE') are "
    "ORDINARY lines too — match them to the venue's existing item for that "
    "delivery container even when the wording or material differs: 'CARTONS "
    "VEGE-PACKING' matches 'Plastic Crate' (the charge names what the "
    "container is FOR, the catalogue names what it IS — bridge that gap). "
    "Never match a delivery-container charge to takeaway or service "
    "packaging (cups, takeaway containers, pizza boxes).\n"
    "- If you are not confident the same product exists, return match_index: null. "
    "Do NOT force a match.\n\n"
    "When there is no match, propose how a NEW item would be named and grouped:\n"
    "- suggested_name: core ingredient first, then variations. FOOD: the core "
    "product only, no brand/size (e.g. 'Flour All Purpose', 'Chicken Breast', "
    "'Butter Unsalted'). BEVERAGE/WINE: keep the brand, drop sizes/packaging (and "
    "vintage for wine).\n"
    "- suggested_group_index: the single best-fitting group from GROUPS. Never "
    "invent a group.\n\n"
    "Return ONLY a JSON object of this exact shape:\n"
    '{"matches": [{"line_id": "<id>", "match_index": <int index into CATALOGUE or '
    'null>, "suggested_name": "<string>", "suggested_group_index": <int index into '
    "GROUPS or null>}]}\n"
    "Include one entry for every invoice line."
)


_CLASSIFY_SYSTEM_PROMPT = (
    "Classify each supplier invoice line by department so we can search the right "
    "part of the stock catalogue. Reply 'food', 'beverage', or 'other'.\n"
    "- food: anything eaten — produce, meat, dairy, dry goods, condiments.\n"
    "- beverage: drinks — beer, wine, spirits, soft drinks, juice, coffee, water.\n"
    "- other: cleaning, packaging, disposables, sundries, or if you are unsure.\n"
    "Return ONLY a JSON object: "
    '{"classes": [{"line_id": "<id>", "class": "food|beverage|other"}]}, one per line.'
)


def _classify_item_lines(lines: list[dict], db: Session) -> dict[str, str]:
    """Cheap first pass: label each NEW-item line food/beverage/other so the match
    call only sends the relevant slice of the ~1,100-item catalogue. Uses the fast
    router model; any failure → empty (each line falls back to 'other', i.e. the
    full catalogue).
    """
    from app.interpreter.llm_interpreter import call_llm
    from app.services.models import router_model

    line_txt = "\n".join(
        f'[line_id={ln["id"]}] description="{ln["description"]}" brand="{ln["brand"]}"'
        for ln in lines
    )
    try:
        parsed, _ = call_llm(
            system_prompt=_CLASSIFY_SYSTEM_PROMPT,
            user_prompt="LINES:\n" + line_txt,
            db=db,
            model=router_model(db),
            call_type="extraction",
            max_tokens=500,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("stock-item classify failed: %s", exc)
        return {}
    rows = parsed.get("classes") if isinstance(parsed, dict) else None
    out: dict[str, str] = {}
    if isinstance(rows, list):
        for r in rows:
            if isinstance(r, dict) and r.get("line_id"):
                c = str(r.get("class") or "").strip().lower()
                out[r["line_id"]] = c if c in {"food", "beverage"} else "other"
    return out


def _match_subset(
    lines: list[dict],
    groups: list[dict],
    candidates: list[dict],
    db: Session,
    supplier_name: str | None = None,
) -> dict:
    """One LLM match call over a candidate subset. Sends the subset as a numbered
    list and the model returns INDEXES (never UUIDs); indexes are subset-relative.
    Returns ``{line_id: {matched_item, suggested_name, suggested_group_id}}``; ``{}``
    on any failure so the editor falls back to plain create.
    """
    if not lines or not candidates:
        return {}
    from app.interpreter.llm_interpreter import call_llm

    cat = "\n".join(
        f"{i} · {str(it.get('name') or '').strip()}"
        + (
            f" · {str(it.get('groupName') or '').strip()}"
            if it.get("groupName")
            else ""
        )
        for i, it in enumerate(candidates)
    )
    grp = "\n".join(
        f"{i} · {str(g.get('name') or '').strip()}"
        + (f" · {str(g.get('category') or '').strip()}" if g.get("category") else "")
        for i, g in enumerate(groups)
    )
    line_txt = "\n".join(
        f'[line_id={ln["id"]}] description="{ln["description"]}" code="{ln["code"]}" '
        f'brand="{ln["brand"]}" unit="{ln["unit"]}"'
        for ln in lines
    )
    user_prompt = (
        (f"SUPPLIER: {supplier_name}\n\n" if supplier_name else "")
        + "INVOICE LINES (resolve each):\n"
        + line_txt
        + "\n\nGROUPS (index · name · category):\n"
        + grp
        + "\n\nCATALOGUE (index · name · group):\n"
        + cat
    )
    try:
        parsed, _ = call_llm(
            system_prompt=_MATCH_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            db=db,
            call_type="extraction",
            max_tokens=2000,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("stock-item match failed: %s", exc)
        return {}
    matches = parsed.get("matches") if isinstance(parsed, dict) else None
    if not isinstance(matches, list):
        return {}
    valid_ids = {ln["id"] for ln in lines}
    out: dict = {}
    for m in matches:
        if not isinstance(m, dict) or m.get("line_id") not in valid_ids:
            continue
        matched_item = None
        mi = _as_int(m.get("match_index"))
        if mi is not None and 0 <= mi < len(candidates):
            it = candidates[mi]
            v = _default_variant(it) or {}
            matched_item = {
                "id": it.get("id"),
                "name": it.get("name"),
                "group": it.get("groupName"),
                "unit_id": v.get("unitId") or it.get("orderingUnitId"),
                "unit_cost": v.get("unitCost") or 0,
            }
        gi = _as_int(m.get("suggested_group_index"))
        group_id = (
            groups[gi]["id"] if gi is not None and 0 <= gi < len(groups) else None
        )
        name = m.get("suggested_name")
        out[m["line_id"]] = {
            "matched_item": matched_item,
            "suggested_name": name.strip()
            if isinstance(name, str) and name.strip()
            else None,
            "suggested_group_id": group_id,
        }
    return out


def _match_stock_items(
    lines: list[dict],
    groups: list[dict],
    candidates: list[dict],
    db: Session,
    supplier_name: str | None = None,
) -> dict:
    """Match each NEW-item line to an existing catalogue item (to link), else
    suggest a normalized name + group (to create). First classifies each line
    food/beverage/other and matches it against only that department's items — an
    invoice is usually all one department, so this is typically ONE small match
    call. Uncategorised items are included in every department so nothing is hidden.
    """
    if not lines or not candidates:
        return {}
    classes = _classify_item_lines(lines, db)
    cat_by_group = {g["id"]: (g.get("category") or "").lower() for g in groups}

    def _cat(it: dict) -> str:
        return cat_by_group.get(it.get("groupId"), "")

    food = [c for c in candidates if "food" in _cat(c) or not _cat(c)]
    bev = [c for c in candidates if "bev" in _cat(c) or not _cat(c)]

    buckets: dict[str, list] = {"food": [], "beverage": [], "other": []}
    for ln in lines:
        buckets[classes.get(ln["id"], "other")].append(ln)

    out: dict = {}
    if buckets["food"]:
        out.update(_match_subset(buckets["food"], groups, food, db, supplier_name))
    if buckets["beverage"]:
        out.update(_match_subset(buckets["beverage"], groups, bev, db, supplier_name))
    if buckets["other"]:
        out.update(
            _match_subset(buckets["other"], groups, candidates, db, supplier_name)
        )
    return out


def suggest_item_matches(
    venue_id: str,
    lines: list[dict],
    db: Session,
    config_db: Session,
    *,
    lh=None,
    supplier_name: str | None = None,
) -> dict:
    """Public entry: per-line match/create suggestions for NEW-item lines.

    ``lines`` are ``_new_item_lines``-shaped dicts (id/description/code/brand/
    unit). Never raises — any failure (catalogue unreachable, LLM error) returns
    ``{}`` so every caller degrades to plain create.
    """
    if not lines:
        return {}
    try:
        if lh is None:
            from app.services.received_invoice import LoadedInvoiceClient

            lh = LoadedInvoiceClient(db, config_db, venue_id)
        candidates = _fetch_raw_stock_items(venue_id, db, config_db)
        groups = _fetch_stock_groups(lh)
        return _match_stock_items(lines, groups, candidates, db, supplier_name)
    except Exception as exc:  # noqa: BLE001 — suggestions are best-effort
        logger.warning("suggest_item_matches failed: %s", exc)
        return {}


def suggest_item_matches_for_invoice(
    venue_id: str, invoice_id: str, db: Session, config_db: Session
) -> dict:
    """Fetch the live invoice, extract its NEW-item lines, and match them."""
    try:
        from app.services.received_invoice import LoadedInvoiceClient

        lh = LoadedInvoiceClient(db, config_db, venue_id)
        inv = lh.invoice(invoice_id)
        lines = _new_item_lines(inv)
        return suggest_item_matches(
            venue_id,
            lines,
            db,
            config_db,
            lh=lh,
            supplier_name=inv.get("supplierName") if isinstance(inv, dict) else None,
        )
    except Exception as exc:  # noqa: BLE001 — suggestions are best-effort
        logger.warning("suggest_item_matches_for_invoice failed: %s", exc)
        return {}


_SUPPLIER_MATCH_SYSTEM_PROMPT = (
    "You match a supplier name printed on an invoice to a venue's supplier "
    "records. Naming VARIATIONS of the same business match ('Hancocks' vs "
    "'Hancock Ltd' vs 'Hancocks Family Merchants'); a DIFFERENT business "
    "never matches. The list is numbered — return ONLY a JSON object "
    '{"index": <number of the matching supplier, or null when none is the '
    "same business>}."
)


def suggest_supplier_match(
    venue_id: str, supplier_name: str, db: Session, config_db: Session
) -> dict:
    """Match a copy-printed supplier name to ONE Loaded supplier record.

    The review engine's supplier gate calls this (via ``norm.match_supplier``)
    only when the invoice has no linked supplier or the copy names a different
    business — rare, so one bounded LLM call over the full list. Admin
    spec-row aliases ride along as hints ("Tasman Liquor Company" is Allied
    Liquor). Returns ``{"supplier_id", "supplier_name"}`` or ``{}`` when no
    supplier is confidently the same business — a miss degrades to "pick one
    manually", never a guess.
    """
    from app.db.config_models import SupplierInvoiceSpec
    from app.interpreter.llm_interpreter import call_llm
    from app.services.component_api import execute_component_action

    try:
        result = execute_component_action(
            "purchase_order_editor", "get_suppliers", {}, venue_id, db, config_db
        )
    except Exception as exc:  # noqa: BLE001 — degrade to no match
        logger.warning("supplier match: suppliers fetch failed: %s", exc)
        return {}
    rows = result.get("data") if isinstance(result, dict) else result
    rows = rows if isinstance(rows, list) else (rows or {}).get("data") or []
    from app.services.supplier_identity import is_placeholder_supplier_name

    suppliers = [
        {"id": s.get("id"), "name": s.get("name") or s.get("supplierName")}
        for s in rows
        if isinstance(s, dict)
        and s.get("id")
        and not (s.get("removedAt") or s.get("datestampDeleted"))
        # Loaded's unnamed-supplier placeholder is never a candidate — the
        # model matching a real name to "[Unnamed Supplier]" is the same
        # non-answer the deterministic tiers now refuse (19 Aug 2026).
        and not is_placeholder_supplier_name(s.get("name") or s.get("supplierName"))
    ]
    if not suppliers:
        return {}
    alias_lines = []
    try:
        for sp in (
            config_db.query(SupplierInvoiceSpec)
            .filter(SupplierInvoiceSpec.enabled.is_(True))
            .all()
        ):
            for a in sp.aliases or []:
                alias_lines.append(f"'{a}' is another name for '{sp.name}'")
    except Exception:  # noqa: BLE001 — hints only, never fatal
        pass
    listing = "\n".join(f"[{i}] {s['name']}" for i, s in enumerate(suppliers))
    user_prompt = f'INVOICE SUPPLIER: "{supplier_name}"\n\nSUPPLIERS:\n{listing}' + (
        "\n\nKNOWN ALIASES:\n" + "\n".join(alias_lines) if alias_lines else ""
    )
    try:
        parsed, _ = call_llm(
            system_prompt=_SUPPLIER_MATCH_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            db=db,
            call_type="extraction",
            max_tokens=200,
        )
    except Exception as exc:  # noqa: BLE001 — degrade to no match
        logger.warning("supplier match failed: %s", exc)
        return {}
    idx = parsed.get("index") if isinstance(parsed, dict) else None
    if isinstance(idx, int) and 0 <= idx < len(suppliers):
        return {
            "supplier_id": suppliers[idx]["id"],
            "supplier_name": suppliers[idx]["name"],
        }
    return {}
