"""Resolve delivered units for lines where NOTHING says the size — the
batched LLM residue call behind Norm's unit accuracy.

Runs after every deterministic tier has passed (supplier variant, the page's
own derived unit, the Norm supplier-product catalogue): what remains are
lines like 'MALFY GIN ROSA PINK GRAPEF' — truncated description, EA charge
column, no size anywhere. One schema-constrained call per invoice covers all
such lines at once, because the sibling lines ARE the evidence (three Malfy
lines printing 700ML at the identical price answer the fourth).

Doctrine (inherited from unit_guess, which this supersedes for this path):
- CHOOSE from the venue's real units, never invent — an id off the list is
  rejected; ``create_name`` is the one sanctioned way to say "the right unit
  isn't here yet".
- Packaging-word units are never offered as candidates.
- Confidence is part of the answer: low confidence parks the line exactly as
  if this module didn't exist. The model can't make anything worse than the
  status quo.
- Category rules (beverage ⇒ volume) invalidate a type-violating pick where
  the line's category is known.
"""

from __future__ import annotations

import json
import logging

from app.services.invoice_units import (
    is_packaging_word,
    parse_unit,
    unit_type_allowed,
)

logger = logging.getLogger(__name__)

_CONFIDENCE = {"high", "medium", "low"}

_SYSTEM_PROMPT = (
    "You determine the DELIVERED unit (the physical size of ONE item — e.g. "
    "'700 mL', 'Kilo', '5L', '12 pack') for supplier-invoice lines whose "
    "size appears nowhere: not in the unit column (which often shows how the "
    "line is CHARGED — EA/CTN — never the size) and not in the description.\n\n"
    "Evidence, strongest first:\n"
    "- SIBLING LINES on the same invoice: the same brand family at the same "
    "unit price is almost always the same pack size ('MALFY GIN CON LIMONE "
    "700ML' at $54.88 answers 'MALFY GIN ROSA PINK GRAPEF' at $54.88).\n"
    "- CATALOGUE EVIDENCE provided per line (how venues already stock the "
    "matched product) — treat as evidence, not authority: venue setups "
    "contain mistakes.\n"
    "- Product knowledge: standard retail packs for known branded goods.\n\n"
    "Rules:\n"
    "- Beverages are ALWAYS volumes (a spirit, syrup, juice or beer is never "
    "delivered as a bare count) — a venue stocking one as 'Each' is a setup "
    "error, not a convention to copy.\n"
    "- Random-weight goods billed per kg (meat/seafood/produce) → the unit "
    "is the kilo unit.\n"
    "- Pick unit_id FROM THE UNITS LIST ONLY. If the correct physical unit "
    "is not on the list, set unit_id null and put the size in create_name "
    "(e.g. '700ml') — never force a wrong existing unit.\n"
    "- confidence: 'high' only when the evidence is decisive (sibling match "
    "or unambiguous product); 'medium' when likely; 'low' when guessing — "
    "low means a person decides instead.\n"
    "- why: ONE sentence naming the actual evidence used. Refer to lines by "
    "their printed descriptions, never by internal ids — a person reads "
    "this.\n\n"
    'Return ONLY JSON: {"lines": [{"line_id": "<id>", "unit_id": '
    '"<id from UNITS or null>", "create_name": "<size or null>", '
    '"confidence": "high|medium|low", "why": "<evidence>"}]} — one '
    "entry per line in RESOLVE."
)


def _default_ask(db):
    def _ask(payload: dict) -> dict:
        from app.interpreter.llm_interpreter import call_llm

        parsed, _ = call_llm(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=json.dumps(payload),
            db=db,
            call_type="extraction",
            max_tokens=1500,
        )
        return parsed if isinstance(parsed, dict) else {}

    return _ask


def resolve_units(
    all_lines: list[dict],
    qualifying_ids: list[str],
    units: list[dict],
    *,
    supplier_name: str | None = None,
    evidence_by_line: dict[str, str] | None = None,
    category_by_line: dict[str, str] | None = None,
    ask_llm=None,
    db=None,
) -> dict[str, dict]:
    """One batched call → {line_id: {unit, create_name, confidence, why}}.

    ``all_lines`` are dicts carrying at least id/description/code/quantity/
    unit/unit_of_measure/unit_price; ``qualifying_ids`` marks the sizeless
    subset to answer. ``units`` is the venue's live unit list. Returns {} on
    any failure — the caller degrades to unit_missing exactly as today.
    """
    ids = [str(i) for i in qualifying_ids]
    live = [
        u
        for u in units or []
        if isinstance(u, dict)
        and u.get("id")
        and not u.get("datestampDeleted")
        and not is_packaging_word(u.get("name"))
    ]
    if not ids or not live:
        return {}
    by_id = {str(u["id"]): u for u in live}
    payload = {
        "supplier": supplier_name,
        "invoice_lines": [
            {
                k: ln.get(k)
                for k in (
                    "id",
                    "code",
                    "description",
                    "quantity",
                    "unit",
                    "unit_of_measure",
                    "unit_price",
                )
            }
            for ln in all_lines
            if isinstance(ln, dict)
        ],
        "resolve": ids,
        "units": [
            {"id": u["id"], "name": u.get("name"), "ratio": u.get("ratio")}
            for u in live
        ],
        "evidence": evidence_by_line or {},
    }
    try:
        raw = (ask_llm or _default_ask(db))(payload)
    except Exception as exc:  # noqa: BLE001 — an answer is never worth an error
        logger.info("unit resolver failed: %s", exc)
        return {}
    rows = raw.get("lines") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        lid = str(r.get("line_id") or "")
        if lid not in ids or lid in out:
            continue
        conf = str(r.get("confidence") or "").strip().lower()
        if conf not in _CONFIDENCE:
            conf = "low"
        unit = by_id.get(str(r.get("unit_id") or ""))
        # An id off the list would be inventing a unit through the back door.
        if r.get("unit_id") and unit is None:
            unit = None
            conf = "low"
        create_name = r.get("create_name")
        create_name = (
            create_name.strip()
            if isinstance(create_name, str)
            and create_name.strip()
            and not is_packaging_word(create_name)
            else None
        )
        # Category rules trump the model: a count-typed pick for a line whose
        # category forbids counts is invalid, whatever the confidence.
        category = (category_by_line or {}).get(lid)
        if unit is not None and category:
            parsed = parse_unit(unit.get("name"))
            if parsed and not unit_type_allowed(category, parsed[0]):
                logger.info(
                    "unit resolver: %r rejected for %s line %s",
                    unit.get("name"),
                    category,
                    lid,
                )
                unit, conf = None, "low"
        out[lid] = {
            "unit": unit,
            "create_name": create_name if unit is None else None,
            "confidence": conf,
            "why": str(r.get("why") or "").strip(),
        }
    return out
