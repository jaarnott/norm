"""Enrich the supplier-product catalogue's open questions with the model.

    python -m scripts.enrich_supplier_catalog [--dry-run] [--limit N]

The offline half of unit accuracy: entries the pages could not answer —
conflicts, practice-only rows, unknowns with sightings — get ONE reasoned
verdict each, written at provenance ``enriched``. That tier sits BELOW
printed and human, so enrichment can only answer where they are silent, or
break a printed tie by agreeing with one side (see
supplier_catalog._recompute_current). It also classifies ``category``
(beverage/food/packaging/fee) for every processed row — the input the
category rules and the hygiene report run on.

Verdicts are validated before they land: only HIGH confidence writes a unit,
category rules (beverage ⇒ volume) reject violating verdicts, and 'variable'
is a legitimate answer ("read this one off the page each time"). Batched per
supplier (~15 entries a call); latency does not matter here and one answer
serves every venue forever.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter, defaultdict

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("scripts.enrich_supplier_catalog")

_BATCH = 15

_SYSTEM_PROMPT = (
    "You are enriching a hospitality supplier-product catalogue. For each "
    "PRODUCT (a supplier's stock code with its printed descriptions and any "
    "conflicting or advisory unit evidence), determine what ONE delivered "
    "item physically IS.\n\n"
    "Answer per product:\n"
    "- unit_name: the physical pack as it would print — '700ml', '1L', "
    "'500g', '5x3kg', '12 pack' — or null when you cannot be confident or "
    "the pack varies.\n"
    "- pack_type: 'fixed' (one physical size), 'random_weight' "
    "(meat/seafood/produce billed per kg — unit_name must be 'Kilo'), "
    "'variable' (case/box sizes genuinely change shipment to shipment — "
    "unit_name null), or 'unknown'.\n"
    "- category: 'beverage' (anything drunk or poured, syrups included), "
    "'food', 'packaging' (containers, wraps, disposables), 'fee' (freight, "
    "levies, charges), or 'unknown'.\n"
    "- confidence: 'high' only when the evidence or the product itself is "
    "unambiguous; 'medium' when likely; 'low' when guessing.\n"
    "- why: ONE sentence naming the evidence. A person reads this.\n\n"
    "Rules:\n"
    "- Beverages are ALWAYS volumes. A venue's practice of counting them as "
    "'Each' is a setup error, never evidence of the pack.\n"
    "- Evidence marked 'practice' is what venues chose at receive time — "
    "advisory only, often wrong. Evidence marked 'printed' came off real "
    "invoice pages — strong. Cross-supplier catalogue lines are strong.\n"
    "- Conflicting printed sizes usually mean the supplier changed pack or "
    "sells both — pick one ONLY when the descriptions/evidence make it "
    "clear; otherwise 'variable' or null.\n"
    "- Never invent precision: a bare packaging word is not a size.\n\n"
    'Return ONLY JSON: {"products": [{"code": "<code>", "unit_name": '
    '"<size or null>", "pack_type": "fixed|random_weight|variable|unknown", '
    '"category": "beverage|food|packaging|fee|unknown", "confidence": '
    '"high|medium|low", "why": "<evidence>"}]} — one entry per product.'
)


def _entry_payload(row, related: list[str]) -> dict:
    ev = row.evidence or {}
    sightings = {}
    for tier in ("human", "printed", "enriched", "practice"):
        bucket = ev.get(tier) or {}
        if bucket:
            sightings[tier] = [
                {"unit": v.get("name"), "count": v.get("count")}
                for v in bucket.values()
            ]
    return {
        "code": row.code,
        "supplier": row.supplier_key,
        "descriptions": (ev.get("descriptions") or [row.description])[:5],
        "evidence": sightings,
        "cross_supplier": related,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.enrich_supplier_catalog")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args(argv)

    # The live ANTHROPIC key lives in the config DB (same as the sensei job).
    from app.config import settings
    from app.db.config_models import SupplierProduct
    from app.db.engine import SessionLocal, _ConfigSessionLocal
    from app.db.models import SystemSecret
    from app.interpreter.llm_interpreter import call_llm
    from app.services import supplier_catalog as sc
    from app.services.invoice_units import unit_type_allowed
    from app.services.supplier_catalog import _classify_uom

    db = SessionLocal()
    config_db = (_ConfigSessionLocal or SessionLocal)()
    for secret in config_db.query(SystemSecret).all():
        if secret.key == "ANTHROPIC_API_KEY" and secret.value:
            settings.ANTHROPIC_API_KEY = secret.value

    stats: Counter = Counter()
    try:
        # Open questions: no answer yet, or advisory-only, or uncategorised.
        rows = (
            config_db.query(SupplierProduct)
            .filter(
                (SupplierProduct.unit_name.is_(None))
                | (SupplierProduct.provenance == "practice")
                | (SupplierProduct.category == "unknown")
            )
            # True open questions (no unit at all) first — they are the
            # reason this job exists; category-only enrichment fills the rest
            # of the budget.
            .order_by(
                SupplierProduct.unit_name.isnot(None),
                SupplierProduct.supplier_key,
                SupplierProduct.code,
            )
            .limit(args.limit)
            .all()
        )
        by_supplier: dict[str, list] = defaultdict(list)
        for r in rows:
            by_supplier[r.supplier_key].append(r)

        for supplier, batch_rows in by_supplier.items():
            for i in range(0, len(batch_rows), _BATCH):
                chunk = batch_rows[i : i + _BATCH]
                payload = {
                    "supplier": supplier,
                    "products": [
                        _entry_payload(
                            r,
                            sc.related_evidence(
                                config_db,
                                r.description,
                                exclude=(r.supplier_key, r.code),
                            ),
                        )
                        for r in chunk
                    ],
                }
                try:
                    parsed, _ = call_llm(
                        system_prompt=_SYSTEM_PROMPT,
                        user_prompt=json.dumps(payload),
                        db=db,
                        call_type="extraction",
                        max_tokens=2500,
                    )
                    db.commit()  # llm_calls row
                except Exception as exc:  # noqa: BLE001
                    logger.warning("enrichment call failed for %s: %s", supplier, exc)
                    stats["call_failed"] += 1
                    continue
                verdicts = {
                    str(v.get("code")): v
                    for v in (parsed or {}).get("products") or []
                    if isinstance(v, dict)
                }
                for r in chunk:
                    v = verdicts.get(r.code)
                    if not v:
                        stats["no_verdict"] += 1
                        continue
                    conf = str(v.get("confidence") or "").lower()
                    category = str(v.get("category") or "unknown").lower()
                    pack_type = str(v.get("pack_type") or "unknown").lower()
                    unit_name = v.get("unit_name")
                    why = str(v.get("why") or "").strip()
                    # Only HIGH confidence writes a unit; the category rules
                    # reject violating verdicts outright — and a verdict that
                    # contradicts itself ('beverage' at '1kg') is wholly
                    # untrustworthy, so its category is dropped too.
                    if unit_name and conf == "high":
                        classified = _classify_uom(unit_name)
                        utype = classified[2] if classified else None
                        if not unit_type_allowed(category, utype):
                            logger.info(
                                "rejected %s %s: %r violates %s rules",
                                supplier,
                                r.code,
                                unit_name,
                                category,
                            )
                            stats["rule_rejected"] += 1
                            unit_name = None
                            category = "unknown"
                    else:
                        unit_name = None
                    if unit_name:
                        sc.apply_enrichment(
                            config_db,
                            r,
                            unit_name=str(unit_name),
                            pack_type=pack_type if pack_type != "unknown" else "fixed",
                            unit_type=(
                                _classify_uom(unit_name)[2]
                                if _classify_uom(unit_name)
                                else None
                            ),
                            category=category,
                            why=why,
                        )
                        stats["unit_written"] += 1
                    else:
                        # Category and a 'variable' verdict are still worth
                        # keeping — classification, not size.
                        if category != "unknown" and r.category == "unknown":
                            r.category = category
                            stats["category_written"] += 1
                        if pack_type == "variable" and r.unit_name is None:
                            r.pack_type = "variable"
                            ev = dict(r.evidence or {})
                            ev["enriched_note"] = why
                            r.evidence = ev
                            stats["variable_marked"] += 1
                stats["processed"] += len(chunk)

        print(dict(stats))
        if args.dry_run:
            config_db.rollback()
            print("dry run — rolled back")
        else:
            config_db.commit()
            print("committed")
        return 0
    finally:
        db.close()
        config_db.close()


if __name__ == "__main__":
    sys.exit(main())
