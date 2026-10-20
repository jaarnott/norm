"""Match supplier-invoice lines to a venue's stock catalogue — an LLM function.

One schema-constrained ``call_llm`` per call: typed input → one bounded model
call → typed output. No conversation, no tool choice, no writes. The replica
(``invoice_replica``) calls ``suggest_item_matches`` / ``suggest_supplier_match``
directly; the engine tools ``norm.match_stock_items`` / ``norm.match_supplier``
that wrapped them for the old sandboxed review engine were removed in Oct 2026
— nothing called them once the review moved server-side.
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

    Raises on a misconfigured/unreachable catalogue and on an error reply: a
    failed fetch is a failure, never an empty catalogue (an empty list made
    the matcher skip the model silently).
    """
    from app.services.component_api import execute_component_action

    result = execute_component_action(
        "purchase_order_editor", "get_stock_items_detail", {}, venue_id, db, config_db
    )
    if isinstance(result, dict) and result.get("error"):
        raise RuntimeError(
            f"stock items fetch failed (HTTP {result.get('status_code')})"
        )
    items = result.get("data") if isinstance(result, dict) else result
    if isinstance(items, dict) and isinstance(items.get("data"), list):
        items = items["data"]
    if not isinstance(items, list):
        raise RuntimeError("stock items fetch returned no list")
    return [i for i in items if isinstance(i, dict) and i.get("id")]


def _fetch_stock_groups(lh) -> list[dict]:
    """Loaded stock groups (subcategories) as ``{id, name, category}``. Raises
    on a reply that isn't a list: no groups means no new item can be placed."""
    g = lh.get("/1.0/stock/internal/subcategories")
    if isinstance(g, dict) and isinstance(g.get("data"), list):
        g = g["data"]
    if not isinstance(g, list):
        raise RuntimeError("stock groups fetch returned no list")
    rows = g
    return [
        {"id": r.get("id"), "name": r.get("name"), "category": r.get("categoryName")}
        for r in rows
        if isinstance(r, dict) and r.get("id")
    ]


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
    "Include one entry for every invoice line. Every line with no match needs a "
    "suggested_name and a suggested_group_index."
)

#: Said to the model when it is asked again for the lines it left without an
#: answer (owner, 19 Oct 2026: Norm always answers; never a guess in its place).
_ASK_AGAIN = (
    "These lines came back without a usable answer last time. For each one, "
    "either give the match_index of the same product, or give BOTH a "
    "suggested_name and the suggested_group_index of the closest group in "
    "GROUPS. A discount, fee or charge is still a line: name it and place it in "
    "the closest group."
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
    note: str | None = None,
) -> dict:
    """One LLM match call over a candidate subset. Sends the subset as a numbered
    list and the model returns INDEXES (never UUIDs); indexes are subset-relative.
    Returns ``{line_id: {matched_item, suggested_name, suggested_group_id}}``; ``{}``
    on any failure — ``suggest_item_matches`` asks again for what's missing.
    An empty catalogue is still asked: the model names and groups new items.
    """
    if not lines:
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
        + (cat or "(none: the venue has no stock items yet)")
        + (f"\n\n{note}" if note else "")
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
    note: str | None = None,
) -> dict:
    """Match each NEW-item line to an existing catalogue item (to link), else
    suggest a normalized name + group (to create). Classifies each line
    food/beverage/other and matches a CONSUMABLE line (food or beverage) against
    every consumable item — food, beverage AND uncategorised — holding back only
    cleaning/packaging ('other').

    A consumable is a consumable whichever department a venue happens to file it
    under: a bar shelves limes (a food/produce item) as a BEVERAGE for cocktails,
    so a food-classified 'LIMES' line must still see that beverage-filed Lime. The
    old split (a food line saw only food-category items) hid exactly those
    cross-filed items and forced a spurious 'create it' (Service Foods VEGF390,
    28 Aug 2026). Only non-consumables stay out — a lime must never match a mop —
    so the candidate list stays smaller than the full catalogue.
    """
    if not lines:
        return {}
    classes = _classify_item_lines(lines, db)
    cat_by_group = {g["id"]: (g.get("category") or "").lower() for g in groups}

    def _cat(it: dict) -> str:
        return cat_by_group.get(it.get("groupId"), "")

    consumable = [
        c for c in candidates if "food" in _cat(c) or "bev" in _cat(c) or not _cat(c)
    ]

    buckets: dict[str, list] = {"food": [], "beverage": [], "other": []}
    for ln in lines:
        buckets[classes.get(ln["id"], "other")].append(ln)

    out: dict = {}
    for bucket, cands in (
        ("food", consumable),
        ("beverage", consumable),
        ("other", candidates),
    ):
        if buckets[bucket]:
            out.update(
                _match_subset(buckets[bucket], groups, cands, db, supplier_name, note)
            )
    return out


def _answered(ans: object) -> bool:
    """An answer Norm can act on: an existing item to link, or a new item's
    name and the group to create it in."""
    if not isinstance(ans, dict):
        return False
    m = ans.get("matched_item")
    if isinstance(m, dict) and m.get("id"):
        return True
    return bool(ans.get("suggested_name") and ans.get("suggested_group_id"))


def _twice(fetch, what: str):
    """``fetch()``, tried once more on failure; None when both fail."""
    for attempt in (1, 2):
        try:
            return fetch()
        except Exception as exc:  # noqa: BLE001 — reported as the line's reason
            logger.warning("%s fetch failed (try %d): %s", what, attempt, exc)
    return None


def suggest_item_matches(
    venue_id: str,
    lines: list[dict],
    db: Session,
    config_db: Session,
    *,
    lh=None,
    supplier_name: str | None = None,
    candidates: list[dict] | None = None,
) -> dict:
    """Public entry: per-line match/create suggestions for NEW-item lines.

    ``lines`` are ``_new_item_lines``-shaped dicts (id/description/code/brand/
    unit); ``candidates`` is the venue's raw stock catalogue when the caller
    already has it (else it is fetched). Every line gets an entry: an existing
    item, a new item's name and group, or ``{"failed": reason}`` when Norm's
    answer didn't come through — the fetches and the model are each tried once
    more first (owner, 19 Oct 2026: retry, then mark the line so the next review
    asks again; never guess a group). Never raises.
    """
    if not lines:
        return {}

    def failed(reason: str) -> dict:
        return {ln["id"]: {"failed": reason} for ln in lines}

    try:
        if lh is None:
            from app.services.received_invoice import LoadedInvoiceClient

            lh = LoadedInvoiceClient(db, config_db, venue_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("suggest_item_matches: no Loaded client: %s", exc)
        return failed("Loaded couldn't be reached")
    stock = candidates or _twice(
        lambda: _fetch_raw_stock_items(venue_id, db, config_db), "stock items"
    )
    if stock is None:
        return failed("Loaded's stock items couldn't be fetched")
    groups = _twice(lambda: _fetch_stock_groups(lh), "stock groups")
    if groups is None:
        return failed("Loaded's stock groups couldn't be fetched")
    if not groups:
        return failed("the venue has no stock groups in Loaded to place a new item in")

    def ask(todo: list[dict], note: str | None = None) -> dict:
        try:
            return _match_stock_items(todo, groups, stock, db, supplier_name, note)
        except Exception as exc:  # noqa: BLE001 — asked again, then reported
            logger.warning("suggest_item_matches failed: %s", exc)
            return {}

    out = ask(lines)
    todo = [ln for ln in lines if not _answered(out.get(ln["id"]))]
    if todo:
        # Once more, for just those lines. call_llm has already retried a
        # dropped connection; this covers an answer that was unreadable,
        # skipped a line, or left out the group.
        again = ask(todo, _ASK_AGAIN)
        for ln in todo:
            if _answered(again.get(ln["id"])) or ln["id"] not in out:
                if ln["id"] in again:
                    out[ln["id"]] = again[ln["id"]]
    result: dict = {}
    for ln in lines:
        ans = out.get(ln["id"])
        if _answered(ans):
            result[ln["id"]] = ans
        elif isinstance(ans, dict) and ans.get("suggested_group_id"):
            # A group but no name, twice: the invoice's own words name it.
            name = str(ln.get("description") or "").strip()
            result[ln["id"]] = (
                {**ans, "suggested_name": name}
                if name
                else {"failed": "no name for the new item"}
            )
        elif isinstance(ans, dict):
            result[ln["id"]] = {"failed": "no stock group was picked for the new item"}
        else:
            result[ln["id"]] = {"failed": "the stock-item match didn't come through"}
    return result


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
