"""Norm's supplier-product catalogue — global physical facts about products.

The accuracy engine behind unit resolution (see SupplierProduct in
config_models for the doctrine). This module is deliberately boring: harvest
printed evidence from extractions, keep provenance honest, answer lookups.
Nothing here calls a model — enrichment is a later, separate phase.

Two rules carry the whole design:
- Authority order: an admin's dojo-verified unit ('human') is the top truth,
  then the resolver's worked-out verdict ('enriched'); a raw extraction read
  ('printed') is only provisional, so a poisoned read never buries a verdict. A
  venue receive ('practice') is kept as evidence but never wins the ranking
  (28 Aug 2026 — enriched lifted above printed; see docs/unit-resolution.md).
- Conflict is data, not a vote: two different sizes at one tier leave
  ``unit_name`` empty with both sightings kept — a question for the resolver,
  never a majority decision.
"""

from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.config_models import SupplierProduct
from app.services.invoice_units import (
    _unit_norm,
    is_multipack,
    is_packaging_word,
    parse_unit,
    units_equivalent,
)

logger = logging.getLogger(__name__)

# Authority order for a product's unit: an admin's dojo-verified unit ('human')
# is the top truth, then the resolver's worked-out verdict ('enriched'); a raw
# extraction read ('printed') is only provisional and is outranked by both.
# 'practice' (a receive) is recorded as evidence but is NOT a ranking source — a
# receive can carry a user's mistake and must never bury a read or a verdict. A
# provenance absent here scores -1 (never answers). 28 Aug 2026: 'enriched'
# lifted ABOVE 'printed' (was printed>enriched), 'practice' dropped from the
# ranking; 'human' stays on top (dojo baselines write it — config_models.py).
_RANK = {"human": 2, "enriched": 1, "printed": 0}

# Bare weight words = random-weight billing (meat/produce priced per kg).
# A SIZED weight ('1kg', '500g') is a fixed pack — the digit is the tell.
_RANDOM_WEIGHT_WORDS = {"kilo", "kilos", "kg", "kgs", "kilogram", "kilograms"}

_MAX_INVOICES_KEPT = 20
_MAX_DESCRIPTIONS_KEPT = 5


def supplier_key_for(printed_name: object, config_db: Session) -> str | None:
    """Printed supplier name → the spec registry's name, or None.

    The SupplierInvoiceSpec registry is deliberately the identity gate: a
    supplier nobody has written a spec for is not catalogued (there is no
    stable cross-venue key to file it under).
    """
    from app.services.invoice_extraction import find_spec_for_supplier

    if not printed_name:
        return None
    spec = find_spec_for_supplier(config_db, str(printed_name))
    return spec.name if spec else None


def _classify_uom(uom: object) -> tuple[str, str, str] | None:
    """A line's unit_of_measure → (pack_type, canonical_key, unit_type) or
    None when it carries no physical-size information.

    - sized measure ('700ml', '5L', '500g') → fixed
    - multipack ('6x1000ml', '4x6 pack') → fixed
    - bare weight word ('Kilo') → random_weight (billed per kg; the unit IS
      Kilo, there is no fixed pack)
    - count/packaging words ('each', 'PACK') → None: how it was charged,
      never what it is (the EA trap this catalogue exists to beat)
    """
    s = " ".join(str(uom or "").strip().lower().split())
    if not s or is_packaging_word(s):
        return None
    if s in _RANDOM_WEIGHT_WORDS:
        return ("random_weight", "kilo", "weight")
    if is_multipack(s):
        return ("fixed", _unit_norm(s), "")
    parsed = parse_unit(s)
    if parsed and parsed[0] in ("weight", "volume"):
        return ("fixed", _unit_norm(s), parsed[0])
    # counted packs ('12 pack') are a real fixed pack; bare counts are not
    if parsed and parsed[0] == "count" and any(ch.isdigit() for ch in s):
        return ("fixed", _unit_norm(s), "count")
    return None


def _merge_sighting(
    bucket: dict, key: str, display: str, invoice_no: str | None
) -> None:
    """Add one sighting, folding equivalent spellings ('0.7 L' ≡ '700ml')
    into the first-seen key so notation never manufactures a conflict.

    Deduped per invoice number: re-observing the same invoice (backfill
    re-runs, the live write-back seeing a re-review) accumulates nothing.
    Past the kept-list cap the dedupe degrades to count inflation only —
    counts are evidence weight, never a vote, so that is harmless.
    """
    for existing_key, entry in bucket.items():
        if existing_key == key or units_equivalent(display, entry.get("name")):
            invs = entry.setdefault("invoices", [])
            if invoice_no and invoice_no in invs:
                return  # already counted this invoice's sighting
            entry["count"] = int(entry.get("count") or 0) + 1
            if invoice_no and len(invs) < _MAX_INVOICES_KEPT:
                invs.append(invoice_no)
            return
    bucket[key] = {
        "name": display,
        "count": 1,
        "invoices": [invoice_no] if invoice_no else [],
    }


def _recompute_current(row: SupplierProduct) -> None:
    """Set unit_name/pack_type/unit_type/provenance from the HIGHEST tier
    that has size evidence. One distinct unit there → the answer; more than
    one → an open question (conflict): unit stays empty, evidence keeps all.

    A conflict CAN be arbitrated from below: when a lower tier (typically
    ``enriched`` — the enrichment job exists for exactly this) holds a single
    answer that matches ONE of the conflicting sightings, that sighting wins
    and the arbiter's tier is recorded as provenance — the decider owns the
    answer. An arbiter matching none of the sightings decides nothing:
    provenance still ranks, so a lower tier never overrules a higher one, it
    only breaks the higher tier's tie.

    Practice entries carry count words by design (they are the divergence
    detector) — count-word sightings never produce an ANSWER, only evidence.
    """
    ev = row.evidence or {}
    tiers = sorted(_RANK, key=_RANK.get, reverse=True)
    for pos, tier in enumerate(tiers):
        bucket = ev.get(tier) or {}
        sized = {k: v for k, v in bucket.items() if not v.get("count_word")}
        if not sized:
            continue
        if len(sized) == 1:
            ((key, entry),) = sized.items()
            row.unit_name = entry.get("name")
            row.pack_type = entry.get("pack_type") or "fixed"
            row.unit_type = entry.get("unit_type") or None
            row.provenance = tier
            return
        # Conflict — look below for a single-answer arbiter agreeing with
        # exactly one of the sightings.
        for lower in tiers[pos + 1 :]:
            lbucket = ev.get(lower) or {}
            lsized = {k: v for k, v in lbucket.items() if not v.get("count_word")}
            if len(lsized) != 1:
                continue
            ((_, arb),) = lsized.items()
            agreed = [
                v
                for v in sized.values()
                if units_equivalent(arb.get("name"), v.get("name"))
            ]
            if len(agreed) == 1:
                row.unit_name = agreed[0].get("name")
                row.pack_type = agreed[0].get("pack_type") or "fixed"
                row.unit_type = agreed[0].get("unit_type") or None
                row.provenance = lower
                return
        row.unit_name = None
        row.unit_type = None
        row.pack_type = "unknown"
        row.provenance = tier
        return


def observe_extraction(
    config_db: Session,
    extraction: dict,
    *,
    provenance: str = "printed",
    supplier_key: str | None = None,
) -> dict:
    """Harvest one extraction's printed size evidence into the catalogue.

    Best-effort by contract: callers (the live review path, the backfill)
    must never fail because of this. Returns {"observed": N, "skipped": why}
    stats for logging. The caller owns the commit.
    """
    if not isinstance(extraction, dict):
        return {"observed": 0, "skipped": "not a dict"}
    key = supplier_key or supplier_key_for(extraction.get("supplier_name"), config_db)
    if not key:
        return {"observed": 0, "skipped": "no supplier spec"}
    if provenance not in _RANK:
        return {"observed": 0, "skipped": f"unknown provenance {provenance!r}"}
    invoice_no = str(extraction.get("invoice_number") or "") or None
    now = datetime.now(timezone.utc)
    observed = 0
    for ln in extraction.get("lines") or []:
        if not isinstance(ln, dict):
            continue
        code = str(ln.get("code") or "").strip()
        if not code:
            continue  # codeless lines need description fingerprints (later)
        row = (
            config_db.query(SupplierProduct)
            .filter(
                SupplierProduct.supplier_key == key,
                SupplierProduct.code == code,
            )
            .first()
        )
        if row is None:
            row = SupplierProduct(supplier_key=key, code=code)
            config_db.add(row)
        # Deep copy before ANY mutation: the JSON column's committed snapshot
        # shares the nested dicts, so mutating an entry in place makes old
        # and new compare equal and SQLAlchemy emits NO UPDATE — the change
        # exists in memory and silently never reaches the database (found
        # the hard way, 18 Aug 2026).
        ev = copy.deepcopy(row.evidence or {})
        desc = str(ln.get("description") or "").strip()
        if desc:
            row.description = desc
            descs = list(ev.get("descriptions") or [])
            if desc not in descs:
                descs = (descs + [desc])[-_MAX_DESCRIPTIONS_KEPT:]
            ev["descriptions"] = descs
        row.last_seen = now
        classified = _classify_uom(ln.get("unit_of_measure"))
        if classified:
            pack_type, ukey, unit_type = classified
            bucket = ev.setdefault(provenance, {})
            _merge_sighting(
                bucket,
                ukey,
                str(ln.get("unit_of_measure")).strip(),
                invoice_no,
            )
            # carry the classification with the sighting so recompute can
            # restore pack/unit type from evidence alone
            entry = next(
                (
                    v
                    for k, v in bucket.items()
                    if k == ukey
                    or units_equivalent(ln.get("unit_of_measure"), v.get("name"))
                ),
                None,
            )
            if entry is not None:
                entry["pack_type"] = pack_type
                entry["unit_type"] = unit_type or entry.get("unit_type") or ""
            observed += 1
        row.evidence = ev
        if classified:
            _recompute_current(row)
        row.updated_at = now
    return {"observed": observed, "skipped": None}


def observe_practice(
    config_db: Session,
    supplier_key: str | None,
    invoice_no: str | None,
    lines: list[dict],
) -> dict:
    """Record what a venue actually RECEIVED — kept as evidence only.

    ``lines`` carry {code, description, unit} where ``unit`` is the unit the
    receive used. Practice is NOT a ranking source (28 Aug 2026): a receive can
    carry a user's mistake and must never win over a resolver verdict or bury a
    read. It is still recorded — tagged ``count_word`` for bare words — because
    it is the divergence detector the hygiene report reads ('every venue
    receives this syrup as Each' against a verdict that says Litre).
    Best-effort; caller owns the commit.
    """
    if not supplier_key:
        return {"observed": 0, "skipped": "no supplier key"}
    now = datetime.now(timezone.utc)
    observed = 0
    for ln in lines or []:
        if not isinstance(ln, dict):
            continue
        code = str(ln.get("code") or "").strip()
        unit_name = str(ln.get("unit") or "").strip()
        if not code or not unit_name:
            continue
        row = lookup(config_db, supplier_key, code)
        if row is None:
            # A row born from practice says so — 'printed' (the column
            # default) would overstate what we know about it.
            row = SupplierProduct(
                supplier_key=supplier_key, code=code, provenance="practice"
            )
            if ln.get("description"):
                row.description = str(ln["description"]).strip()
            config_db.add(row)
        ev = copy.deepcopy(row.evidence or {})
        bucket = ev.setdefault("practice", {})
        _merge_sighting(bucket, _unit_norm(unit_name), unit_name, invoice_no)
        entry = next(
            (
                v
                for k, v in bucket.items()
                if k == _unit_norm(unit_name)
                or units_equivalent(unit_name, v.get("name"))
            ),
            None,
        )
        if entry is not None:
            classified = _classify_uom(unit_name)
            if classified:
                entry["pack_type"] = classified[0]
                entry["unit_type"] = classified[2] or entry.get("unit_type") or ""
            else:
                entry["count_word"] = True
        row.evidence = ev
        _recompute_current(row)
        row.last_seen = now
        row.updated_at = now
        observed += 1
    return {"observed": observed, "skipped": None}


def observe_practice_from_doc(config_db: Session, doc: dict) -> None:
    """Record a received working document's line units as practice evidence.

    The one hook every receive path calls (autopilot, the card's Receive
    button, the MCP card). Fully best-effort with its OWN commit — a receive
    that Loaded already accepted must never fail on bookkeeping.
    """
    try:
        if not isinstance(doc, dict):
            return
        key = supplier_key_for(doc.get("supplier_name"), config_db)
        if not key:
            return
        lines = [
            {
                "code": ln.get("code"),
                "description": ln.get("description"),
                "unit": ln.get("unit"),
            }
            for ln in doc.get("lines") or []
            if isinstance(ln, dict)
        ]
        observe_practice(
            config_db,
            key,
            str(doc.get("reference_number") or "") or None,
            lines,
        )
        config_db.commit()
    except Exception as exc:  # noqa: BLE001 — bookkeeping never blocks
        logger.info("practice observe failed: %s", exc)
        try:
            config_db.rollback()
        except Exception:  # noqa: BLE001
            pass


def apply_enrichment(
    config_db: Session,
    row: SupplierProduct,
    *,
    unit_name: str | None,
    pack_type: str,
    unit_type: str | None,
    category: str | None,
    why: str,
    confidence: str = "high",
) -> None:
    """Write one enrichment verdict (provenance ``enriched``) — the resolver's
    (or the offline enrichment's) worked-out answer.

    The enriched tier now OUTRANKS a raw extraction read, so a decisive verdict
    is the authority (28 Aug 2026). ``confidence`` is recorded with the verdict
    so the caller/receive path can re-run a low-confidence one; category lands
    on the row directly (it is classification, not size evidence). Caller
    validates category rules BEFORE calling; caller owns the commit.
    """
    ev = copy.deepcopy(row.evidence or {})
    if unit_name:
        bucket = ev.setdefault("enriched", {})
        # One verdict per entry: enrichment re-runs replace, never accumulate
        # (the model agreeing with itself is not more evidence).
        bucket.clear()
        bucket[_unit_norm(unit_name)] = {
            "name": unit_name,
            "count": 1,
            "invoices": [],
            "pack_type": pack_type,
            "unit_type": unit_type or "",
            "why": why,
            "confidence": confidence,
        }
    ev["enriched_note"] = why
    row.evidence = ev
    if category and category != "unknown":
        row.category = category
    _recompute_current(row)
    row.updated_at = datetime.now(timezone.utc)


def learn_from_resolver(config_db: Session, supplier_key: str | None, lines) -> int:
    """Record resolver verdicts as enrichment — the self-healing write.

    The batched unit resolver reasons from richer evidence than the offline
    enrichment script (sibling lines, venue stocking, cross-supplier rows),
    so a decisive verdict IS an enrichment verdict — recording it means the
    NEXT invoice for this (supplier, code) resolves from the catalogue tier
    without spending a model call ('HIGHLAND PARK 15 YEAR OLD GIFT BOX (1X7',
    4366904, 19 Aug 2026). We record HIGH and MEDIUM verdicts (with their
    confidence); LOW is left unrecorded so the line is put through the resolver
    again on its next sighting. Because the enriched tier now outranks a raw
    read, this verdict wins over an earlier printed read — that is how a
    poisoned row heals (28 Aug 2026). Count words never become an answer, and a
    row that already carries a verdict is left alone. Caller owns the commit.
    """
    if not supplier_key:
        return 0
    written = 0
    for ln in lines or []:
        if not isinstance(ln, dict):
            continue
        ur = ln.get("unit_resolved")
        if not isinstance(ur, dict) or ur.get("confidence") not in ("high", "medium"):
            continue
        code = str(ln.get("code") or "").strip()
        unit_name = str(ur.get("unit_name") or ur.get("create_name") or "").strip()
        classified = _classify_uom(unit_name)
        if not code or not unit_name or classified is None:
            continue  # a count word must never become an answer
        if catalog_unit_for_line(config_db, supplier_key, code) is not None:
            continue  # a resolver verdict already answers — leave it alone
        row = lookup(config_db, supplier_key, code)
        if row is None:
            row = SupplierProduct(
                supplier_key=supplier_key, code=code, provenance="enriched"
            )
            if ln.get("description"):
                row.description = str(ln["description"]).strip()
            config_db.add(row)
        apply_enrichment(
            config_db,
            row,
            unit_name=unit_name,
            pack_type=classified[0],
            unit_type=classified[2],
            category=None,
            why=str(ur.get("why") or "").strip() + " (invoice unit resolver)",
            confidence=str(ur.get("confidence") or "high"),
        )
        written += 1
    return written


_STOP_WORDS = {"the", "and", "with", "for", "pack", "each", "carton", "box"}


def related_evidence(
    config_db: Session,
    description: object,
    *,
    exclude: tuple[str, str] | None = None,
    limit: int = 3,
) -> list[str]:
    """Cross-supplier catalogue evidence for a description — the 'Bidfood
    stocks SYRUP BUTTERSCOTCH SHOTT at Litre' signal, fed to the unit
    resolver as evidence (never authority). Matches on the description's
    distinctive words; answered entries only."""
    from sqlalchemy import or_

    words = [
        w
        for w in str(description or "").upper().split()
        if len(w) >= 4 and w.lower() not in _STOP_WORDS and w.isalpha()
    ][:4]
    if not words:
        return []
    # OR-match then rank by overlap: brand words are often the SHORTEST
    # ('SHOTT'), so demanding every word would miss the exact cross-supplier
    # hit this exists for ('SYRUP BUTTERSCOTCH SHOTT' shares two of four
    # words with 'SHOTT NATURAL SYRUP ELDERF').
    q = (
        config_db.query(SupplierProduct)
        .filter(SupplierProduct.unit_name.isnot(None))
        .filter(or_(*(SupplierProduct.description.ilike(f"%{w}%") for w in words)))
        .limit(50)
    )
    scored: list[tuple[int, SupplierProduct]] = []
    for row in q:
        if exclude and (row.supplier_key, row.code) == exclude:
            continue
        desc_u = str(row.description or "").upper()
        score = sum(1 for w in words if w in desc_u)
        if score:
            scored.append((score, row))
    scored.sort(key=lambda t: -t[0])
    return [
        f"Norm's catalogue: {row.supplier_key} '{row.description}' is "
        f"{row.unit_name} ({row.provenance})"
        for _, row in scored[:limit]
    ]


def lookup(
    config_db: Session, supplier_key: str | None, code: object
) -> SupplierProduct | None:
    if not supplier_key or not code:
        return None
    return (
        config_db.query(SupplierProduct)
        .filter(
            SupplierProduct.supplier_key == supplier_key,
            SupplierProduct.code == str(code).strip(),
        )
        .first()
    )


def catalog_unit_for_line(
    config_db: Session, supplier_key: str | None, code: object
) -> dict | None:
    """The catalogue's ANSWER for a line, or None when it has a question.

    Speaks ONLY for a resolver verdict (provenance ``enriched`` — the analyser
    has signed the unit off), as a fixed pack with a unit or random weight
    (→ Kilo). A raw extraction read (``printed``), a conflict, 'variable' /
    'unknown', and practice-only entries answer nothing: an unverified unit is
    provisional, so the line is put through the resolver instead (28 Aug 2026).
    """
    row = lookup(config_db, supplier_key, code)
    if row is None:
        return None
    # Only a resolver verdict answers. A raw read is provisional and must be
    # verified, so it is NOT handed back here — the receive routes it to the
    # resolver. (_RANK: printed < enriched, so a printed row scores below.)
    if _RANK.get(row.provenance, -1) < _RANK["enriched"]:
        return None
    if row.pack_type == "random_weight":
        return {
            "unit_name": "Kilo",
            "pack_type": "random_weight",
            "provenance": row.provenance,
        }
    if row.pack_type == "fixed" and row.unit_name:
        return {
            "unit_name": row.unit_name,
            "pack_type": "fixed",
            "provenance": row.provenance,
        }
    return None
