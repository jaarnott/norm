"""Norm's supplier-product catalogue — global physical facts about products.

The accuracy engine behind unit resolution (see SupplierProduct in
config_models for the doctrine). This module is deliberately boring: harvest
printed evidence from extractions, keep provenance honest, answer lookups.
Nothing here calls a model — enrichment is a later, separate phase.

Two rules carry the whole design:
- Authority order: the resolver's worked-out verdict ('enriched') is the
  truth; a raw extraction read ('printed') is only provisional, so a poisoned
  read never buries a verdict. Nobody's say-so outranks Norm's verdict: a
  venue receive ('practice') is kept as evidence but never wins, and there is
  no person-confirmed tier (see docs/unit-resolution.md).
- Conflict is data, not a vote: two different sizes at one tier leave
  ``unit_name`` empty with both sightings kept — a question for the resolver,
  never a majority decision.
"""

from __future__ import annotations

import copy
import logging
import re
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

# Authority order for a product's unit: the resolver's worked-out verdict
# ('enriched') outranks a raw extraction read ('printed'), which is only
# provisional. 'practice' (a receive) is recorded as evidence but is NOT a
# ranking source — a receive can carry a user's mistake and must never bury a
# read or a verdict. A provenance absent here scores -1 (never answers).
# 14 Oct 2026: the 'human' tier (an admin's dojo baseline, on top) is gone —
# the user: "the human does not add any value in us getting to autopilot
# across 1000s of venues". Units come from what invoices print and what Norm
# works out; people set them up right about 85% of the time, Norm with search
# about 93% (unit-size test, 14 Oct). Its 74 rows were demoted the same day
# (backup: ~/backups/supplier_products_human_tier_2026-10-14.json on norm-dev).
# Do not add a tier above 'enriched' — test_supplier_catalog guards it.
_RANK = {"enriched": 1, "printed": 0}

# Bare weight words = random-weight billing (meat/produce priced per kg).
# A SIZED weight ('1kg', '500g') is a fixed pack — the digit is the tell.
_RANDOM_WEIGHT_WORDS = {"kilo", "kilos", "kg", "kgs", "kilogram", "kilograms"}

_MAX_INVOICES_KEPT = 20
_MAX_DESCRIPTIONS_KEPT = 5

#: A product its supplier prints no code for is filed under its wording.
_DESC_KEY = "desc:"


def product_key(code: object, description: object = None) -> str | None:
    """The catalogue key for one supplier product: its stock code, else its
    description, normalised so the same wording finds the same row. Some
    suppliers print no codes at all — Factory Group, 0 of 27 lines on
    INV-0446 (14 Oct 2026) — and keyed on codes alone the catalogue could
    learn nothing about them, so every invoice asked again."""
    c = str(code or "").strip()
    if c:
        return c
    words = re.sub(r"[^a-z0-9]+", " ", str(description or "").lower()).strip()
    return f"{_DESC_KEY}{words}" if len(words) >= 3 else None


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
    supplier_key: str | None = None,
) -> dict:
    """Harvest one extraction's printed size evidence into the catalogue —
    always as a ``printed`` read (provisional). There is deliberately no way
    to record a unit as anything stronger: only the resolver's verdict
    (``apply_enrichment``) outranks a read.

    Best-effort by contract: callers (the live review path, the backfill)
    must never fail because of this. Returns {"observed": N, "skipped": why}
    stats for logging. The caller owns the commit.
    """
    if not isinstance(extraction, dict):
        return {"observed": 0, "skipped": "not a dict"}
    key = supplier_key or supplier_key_for(extraction.get("supplier_name"), config_db)
    if not key:
        return {"observed": 0, "skipped": "no supplier spec"}
    invoice_no = str(extraction.get("invoice_number") or "") or None
    now = datetime.now(timezone.utc)
    observed = 0
    for ln in extraction.get("lines") or []:
        if not isinstance(ln, dict):
            continue
        code = product_key(ln.get("code"), ln.get("description"))
        if not code:
            continue
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
            bucket = ev.setdefault("printed", {})
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
        code = product_key(ln.get("code"), ln.get("description"))
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
    """Record what the unit estimator worked out — the self-healing write.

    Every line the estimator was asked about (``unit_asked``) leaves a trace,
    keyed by ``product_key`` so a codeless supplier's products count too:

    - a HIGH answer is Norm's verdict (provenance ``enriched``): the next
      invoice for this product resolves from the catalogue with no model call.
      High was right 65 of 66 times in the 14 Oct 2026 unit-size test.
    - anything less — medium (17 of 22 right, below the ~85% a person
      manages), low, or no answer at all — is recorded as a GUESS
      (``evidence['guess']``). The invoice still uses it, but the next
      invoice for the product asks again (``unsettled_guess``) instead of
      trusting the unit the receive saved on Loaded's supplier product.

    A verdict settles an earlier guess. Count words never become an answer,
    and a product that already has a verdict is left alone. Caller owns the
    commit.
    """
    if not supplier_key:
        return 0
    now = datetime.now(timezone.utc)
    written = 0
    for ln in lines or []:
        if not isinstance(ln, dict):
            continue
        # An answer that only confirmed the unit in use is still an answer.
        ur = next(
            (
                ln[k]
                for k in ("unit_resolved", "unit_agreed")
                if isinstance(ln.get(k), dict)
            ),
            {},
        )
        if not (ln.get("unit_asked") or ur):
            continue
        key = product_key(ln.get("code"), ln.get("description"))
        if not key:
            continue
        unit_name = str(ur.get("unit_name") or ur.get("create_name") or "").strip()
        classified = _classify_uom(unit_name) if unit_name else None
        if classified is None and (parse_unit(unit_name) or ("",))[0] == "count":
            # A printed 'EA' is how a line is charged, never a size — which is
            # why a read can't answer with a count word. The estimator's
            # 'each' is a judgement (a box of straws IS counted as one), and
            # refusing it would re-ask about every box of cups on every invoice.
            classified = ("fixed", _unit_norm(unit_name), "count")
        row = lookup(config_db, supplier_key, key)
        if row is not None and catalog_unit_for_line(config_db, supplier_key, key):
            continue  # a verdict already answers — leave it alone
        if row is None:
            row = SupplierProduct(
                supplier_key=supplier_key,
                code=key,
                provenance="enriched" if ur.get("confidence") == "high" else "printed",
            )
            if ln.get("description"):
                row.description = str(ln["description"]).strip()
            config_db.add(row)
        why = str(ur.get("why") or "").strip()
        if ur.get("confidence") == "high" and classified is not None:
            apply_enrichment(
                config_db,
                row,
                unit_name=unit_name,
                pack_type=classified[0],
                unit_type=classified[2],
                category=None,
                why=f"{why} (unit estimator)".strip(),
                confidence="high",
            )
            ev = copy.deepcopy(row.evidence or {})
            if isinstance(ev.get("guess"), dict):
                ev["guess"]["settled"] = True
                row.evidence = ev
        else:
            ev = copy.deepcopy(row.evidence or {})
            from app.services.unit_resolver import _VERSION

            ev["guess"] = {
                "unit": unit_name or None,
                "confidence": ur.get("confidence") or "none",
                "why": why,
                "at": now.isoformat(),
                "settled": False,
                # The estimator's rules it was made under: an estimate from
                # older rules is never reused (focaccia slabs were estimated
                # "2.1 KG" before bread priced per piece became "each").
                "v": _VERSION,
            }
            row.evidence = ev
            row.updated_at = now
        written += 1
    return written


def record_answer(
    config_db: Session,
    supplier_key: str,
    key: str,
    unit_name: str,
    *,
    description: str | None,
    why: str,
) -> SupplierProduct:
    """A person's answer for one product (the Dojo's daily unit check):
    written as Norm's verdict, so every later invoice for the product answers
    from it, and Norm's own unsure estimate is settled. Raises ValueError for a
    unit that isn't a measurement. Caller owns the commit."""
    classified = _classify_uom(unit_name)
    if classified is None and (parse_unit(unit_name) or ("",))[0] == "count":
        classified = ("fixed", _unit_norm(unit_name), "count")
    if classified is None:
        raise ValueError(f"'{unit_name}' is not a measurement")
    row = lookup(config_db, supplier_key, key)
    if row is None:
        row = SupplierProduct(
            supplier_key=supplier_key,
            code=key,
            description=str(description or "").strip(),
            provenance="enriched",
        )
        config_db.add(row)
    apply_enrichment(
        config_db,
        row,
        unit_name=unit_name,
        pack_type=classified[0],
        unit_type=classified[2] or None,
        category=None,
        why=why,
        confidence="high",
    )
    ev = copy.deepcopy(row.evidence or {})
    if isinstance(ev.get("guess"), dict):
        ev["guess"]["settled"] = True
        row.evidence = ev
    return row


def unsettled_guess(row: SupplierProduct | None) -> bool:
    """Norm only GUESSED this product's unit and has no verdict yet: whatever
    unit a receive saved for it in Loaded is that guess, so the next invoice
    asks again rather than trusting it."""
    guess = ((row.evidence or {}) if row is not None else {}).get("guess")
    return isinstance(guess, dict) and not guess.get("settled")


#: How long Norm reuses its own earlier estimate before working it out again.
ESTIMATE_REUSE_DAYS = 30


def earlier_estimate(row: SupplierProduct | None) -> dict | None:
    """Norm's own earlier estimate for a product it has no settled answer for
    (``evidence['guess']``), or None.

    ``{"unit", "confidence", "why", "fresh"}`` — ``fresh`` while it is younger
    than ESTIMATE_REUSE_DAYS. The review reuses it so every invoice for the
    product books the same unit, and asks again only once it is stale (owner,
    18 Oct 2026: the unit comes from Norm, never from the unit Loaded stores)."""
    from app.services.unit_resolver import _VERSION

    guess = ((row.evidence or {}) if row is not None else {}).get("guess")
    if not isinstance(guess, dict) or guess.get("settled"):
        return None
    if guess.get("v") != _VERSION:
        return None  # made under older rules: worked out again
    conf = str(guess.get("confidence") or "none").strip().lower()
    fresh = False
    try:
        at = datetime.fromisoformat(str(guess.get("at")))
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        fresh = (datetime.now(timezone.utc) - at).days < ESTIMATE_REUSE_DAYS
    except (TypeError, ValueError):
        fresh = False
    return {
        "unit": str(guess.get("unit") or "").strip() or None,
        "confidence": conf if conf in ("high", "medium", "low") else "none",
        "why": str(guess.get("why") or "").strip(),
        "fresh": fresh,
    }


def guessed_before(row: SupplierProduct | None) -> bool:
    """Norm once guessed this product's unit (settled or not) — so a unit
    Loaded holds for it may be that guess, and a verdict outranks it."""
    return isinstance(
        ((row.evidence or {}) if row is not None else {}).get("guess"), dict
    )


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
        # What venues happened to do is not evidence of what a product is.
        .filter(SupplierProduct.provenance.in_(("enriched", "printed")))
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
