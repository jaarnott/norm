"""Two LLM arbitrations for defects deterministic checks can only flag.

Same doctrine as ``unit_resolver``: one schema-constrained call each, an
injectable ``ask_llm`` for tests, HARD validators on the answer, and {} on
any failure — the model can never make things worse than the status quo
(the blocker simply stands, exactly as it would without this module).

``diagnose_totals`` — the copy's own arithmetic fails (lines vs subtotal,
subtotal+tax vs total). The model names WHICH figure was misread and the
correction; the validator re-runs the arithmetic with the corrections
applied and rejects the whole verdict unless it reconciles. Corrections
surface as ordinary Accept-able suggestions.

``arbitrate_pairing`` — two copy lines match the same Loaded line, so
comparison can't proceed. The model pairs them from descriptions, codes,
quantities and prices; the validator enforces a proper matching (candidate
ids only, no double use). A HIGH-confidence pairing merely unlocks the
normal line suggestions — it writes nothing itself, so it needs no gate.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_CONFIDENCE = {"high", "medium", "low"}

_TOTALS_PROMPT = (
    "A supplier invoice was extracted from a PDF, but its printed numbers do "
    "not reconcile (the failing identities are listed). Decide which figure "
    "was most likely MISREAD by extraction and what the correct value is.\n\n"
    "Think like an accounts person: line quantity x unit price must equal the "
    "line total; line totals must sum to the subtotal; subtotal + tax - "
    "discount must equal the total. A single misread digit usually explains "
    "the whole failure — find the correction that makes EVERYTHING agree.\n"
    "Rules:\n"
    "- Propose the FEWEST corrections that reconcile all identities.\n"
    "- Only fields that exist: line quantity / unit_price / line_total, or "
    "header subtotal / tax / discount / total.\n"
    "- confidence 'high' only when one correction cleanly explains the "
    "failure; 'low' when guessing — low is discarded.\n"
    "- why: ONE sentence naming the arithmetic evidence. A person reads it.\n\n"
    'Return ONLY JSON: {"corrections": [{"scope": "line|header", '
    '"line_id": "<id or null>", "field": "<field>", "current": <number>, '
    '"proposed": <number>}], "confidence": "high|medium|low", '
    '"why": "<evidence>"}'
)

_PAIRING_PROMPT = (
    "Match each COPY line (extracted from a supplier invoice PDF) to exactly "
    "one LOADED line (the venue system's draft of the same invoice). The "
    "listed copy lines matched more than one Loaded line ambiguously — use "
    "descriptions, codes, quantities and prices to decide which is which.\n"
    "Rules:\n"
    "- Each Loaded line may be used ONCE, and only ids from CANDIDATES.\n"
    "- confidence 'high' only when the evidence is decisive; anything less "
    "leaves the ambiguity for a person.\n"
    "- why: ONE sentence naming the tiebreaker. A person reads it.\n\n"
    'Return ONLY JSON: {"pairs": {"<copy_line_id>": "<loaded_line_id>"}, '
    '"confidence": "high|medium|low", "why": "<evidence>"}'
)

# The same tolerance bands the replica's reconciliation uses.
_LINE_TOL = 0.011
_SUM_TOL = 0.02
_ROUNDING_BAND = 0.10


def _f(v: object) -> float | None:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _default_ask(db, system_prompt: str):
    def _ask(payload: dict) -> dict:
        from app.interpreter.llm_interpreter import call_llm

        parsed, _ = call_llm(
            system_prompt=system_prompt,
            user_prompt=json.dumps(payload),
            db=db,
            call_type="extraction",
            max_tokens=1200,
        )
        return parsed if isinstance(parsed, dict) else {}

    return _ask


def _reconciles(lines: list[dict], header: dict) -> bool:
    """The replica's totals doctrine, re-run over corrected values."""
    for ln in lines:
        q, c, t = (
            _f(ln.get("quantity")),
            _f(ln.get("unit_price")),
            _f(ln.get("line_total")),
        )
        if q is not None and c is not None and t is not None:
            if abs(q * c - t) > _LINE_TOL:
                return False
    line_sum = sum(
        t for t in (_f(ln.get("line_total")) for ln in lines) if t is not None
    )
    sub, tax = _f(header.get("subtotal")), _f(header.get("tax"))
    disc = _f(header.get("discount")) or 0.0
    total = _f(header.get("total"))
    if sub is not None and abs(line_sum - sub) > _SUM_TOL:
        return False
    if tax is not None and total is not None:
        if abs(line_sum + tax - disc - total) > _ROUNDING_BAND:
            return False
        if sub is not None and abs(sub + tax - disc - total) > _SUM_TOL:
            return False
    return True


def diagnose_totals(
    lines: list[dict],
    header: dict,
    problems: list[str],
    *,
    ask_llm=None,
    db=None,
) -> dict:
    """One call → validated corrections, or {}.

    ``lines``: [{id, description, quantity, unit_price, line_total}];
    ``header``: {subtotal, tax, discount, total}. The validator applies the
    proposed corrections to a copy and re-runs the arithmetic — a verdict
    that doesn't reconcile everything is rejected wholesale.
    """
    if not lines:
        return {}
    payload = {"lines": lines, "header": header, "failing": problems}
    try:
        raw = (ask_llm or _default_ask(db, _TOTALS_PROMPT))(payload)
    except Exception as exc:  # noqa: BLE001 — a diagnosis is never worth an error
        logger.info("totals diagnosis failed: %s", exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    conf = str(raw.get("confidence") or "").strip().lower()
    corrections = raw.get("corrections")
    if conf not in ("high", "medium") or not isinstance(corrections, list):
        return {}
    by_id = {str(ln.get("id")): ln for ln in lines}
    fixed_lines = [dict(ln) for ln in lines]
    fixed_by_id = {str(ln.get("id")): ln for ln in fixed_lines}
    fixed_header = dict(header)
    clean: list[dict] = []
    for c in corrections:
        if not isinstance(c, dict):
            return {}
        field = str(c.get("field") or "")
        proposed = _f(c.get("proposed"))
        if proposed is None:
            return {}
        if c.get("scope") == "line":
            ln = fixed_by_id.get(str(c.get("line_id") or ""))
            if ln is None or field not in ("quantity", "unit_price", "line_total"):
                return {}
            current = _f(by_id[str(c["line_id"])].get(field))
            ln[field] = proposed
        elif c.get("scope") == "header":
            if field not in ("subtotal", "tax", "discount", "total"):
                return {}
            current = _f(header.get(field))
            fixed_header[field] = proposed
        else:
            return {}
        clean.append(
            {
                "scope": c.get("scope"),
                "line_id": str(c.get("line_id")) if c.get("line_id") else None,
                "field": field,
                "current": current,
                "proposed": proposed,
            }
        )
    if not clean or not _reconciles(fixed_lines, fixed_header):
        return {}
    return {
        "corrections": clean,
        "confidence": conf,
        "why": str(raw.get("why") or "").strip(),
    }


def arbitrate_pairing(
    copy_lines: list[dict],
    loaded_candidates: list[dict],
    *,
    context_lines: list[dict] | None = None,
    ask_llm=None,
    db=None,
) -> dict:
    """One call → a validated proper matching at HIGH confidence, or {}.

    ``copy_lines``: the ambiguous replica lines [{id, description, code,
    quantity, unit_price, line_total}]; ``loaded_candidates``: the Loaded
    lines they could pair with (same shape). ``context_lines``: already-
    paired lines, for the model's bearings only.
    """
    if not copy_lines or not loaded_candidates:
        return {}
    candidate_ids = {str(ln.get("id")) for ln in loaded_candidates}
    payload = {
        "copy_lines": copy_lines,
        "candidates": loaded_candidates,
        "already_paired": context_lines or [],
    }
    try:
        raw = (ask_llm or _default_ask(db, _PAIRING_PROMPT))(payload)
    except Exception as exc:  # noqa: BLE001
        logger.info("pairing arbitration failed: %s", exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    conf = str(raw.get("confidence") or "").strip().lower()
    pairs = raw.get("pairs")
    # Only a decisive answer unlocks anything — medium keeps the blockers.
    if conf != "high" or not isinstance(pairs, dict):
        return {}
    copy_ids = {str(ln.get("id")) for ln in copy_lines}
    out: dict[str, str] = {}
    used: set[str] = set()
    for cid, lid in pairs.items():
        cid, lid = str(cid), str(lid)
        # A proper matching or nothing: unknown ids or double-use rejects
        # the whole verdict (a half-right pairing is how the wrong cost
        # lands on the wrong line).
        if cid not in copy_ids or lid not in candidate_ids or lid in used:
            return {}
        used.add(lid)
        out[cid] = lid
    if set(out) != copy_ids:
        return {}
    return {"pairs": out, "confidence": conf, "why": str(raw.get("why") or "").strip()}
