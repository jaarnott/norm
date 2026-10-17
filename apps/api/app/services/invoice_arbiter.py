"""LLM arbitrations for defects deterministic checks can only flag.

Same doctrine as ``unit_resolver``: one schema-constrained call each, an
injectable ``ask_llm`` for tests, HARD validators on the answer, and {} on
any failure — the model can never make things worse than the status quo
(the blocker simply stands, exactly as it would without this module).

``diagnose_totals`` — the copy's own arithmetic fails (lines vs subtotal,
subtotal+tax vs total). The model names WHICH figure was misread and the
correction; the validator re-runs the arithmetic with the corrections
applied and rejects the whole verdict unless it reconciles. Corrections
surface as ordinary Accept-able suggestions.

``work_out_totals`` — with every suggestion applied the invoice still
doesn't come to its total. The model looks at the invoice itself, told how
far out it is, and names the lines that are wrong; the caller's check (the
invoice must then add up, Loaded's way) decides, and a failed answer goes
back with why, for another try.

``same_business`` — the printed supplier matches no Loaded record by name,
but some look close. The model compares the details the invoice prints with
each candidate's record: one of them, or a new supplier.

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

_WORK_OUT_PROMPT = (
    "A supplier invoice is about to be received into a venue's stock system, "
    "but the values it will be received with don't come to the invoice's "
    "total. You are given each line as it will be received (receive: true), "
    "any line that will NOT be received because it wasn't found on the "
    "invoice (receive: false), what was read off the invoice for each line "
    "('printed'), the invoice's printed subtotal, GST and total, and the "
    "attached invoice itself when there is one. Work out which lines are "
    "wrong and what they should be, so the invoice comes to its total.\n"
    "Rules:\n"
    "- Look at the invoice: a misread quantity or price, a line charged "
    "GST or GST-free the other way, a misread total, a line the invoice "
    "prints that no listed line covers, or a listed line it doesn't print.\n"
    "- Change only what the invoice shows is wrong. Never spread a "
    "difference over the lines, and never add a line the invoice doesn't "
    "print.\n"
    "- Each line is received against its stock item ('item'). Put a "
    "printed product's figures only on a line whose item is that product; "
    "when no line's item is, add it — even if a line's description "
    "mentions it.\n"
    "- add: a line the invoice prints that is missing from the list — its "
    "description and code as printed, quantity, unit price ex GST and GST.\n"
    "- remove: a line that will be received but isn't on the invoice (a "
    "duplicate, a line from another invoice). keep: a line marked receive "
    "false that IS on the invoice.\n"
    "- A charge printed as a single amount with no quantity is quantity 1 "
    "at that amount.\n"
    "- A line's value is quantity x unit price ex GST, plus GST when "
    "charged. The total is every received line plus its GST.\n"
    "- 'earlier_answers' are answers that didn't add up, with what they came "
    "to. Don't repeat one.\n"
    "- why: ONE sentence naming what the invoice shows. A person reads it.\n\n"
    'Return ONLY JSON: {"lines": [{"line_id": "<id>", "quantity": <number, '
    'optional>, "unit_price": <number, optional>, "gst": "charged|free, '
    'optional"}], "add": [{"description": "<as printed>", "code": "<as '
    'printed, or null>", "quantity": <number>, "unit_price": <number>, '
    '"gst": "charged|free"}], "remove": ["<line_id>"], "keep": '
    '["<line_id>"], "total": <number or null>, "why": "<evidence>"}'
)

_SAME_BUSINESS_PROMPT = (
    "An invoice was issued by a supplier whose name matches none of the "
    "venue's supplier records exactly. Some records look close. Decide "
    "whether one of them IS the business that issued the invoice — the same "
    "company under a trading name, legal name or branch — or none is.\n"
    "Compare the details the invoice prints (address, phone, email, the "
    "venue's account number with the supplier) with each candidate's record. "
    "Businesses that only share a word ('Fresh', 'Sydney', 'Meats') are "
    "different businesses.\n"
    "- why: ONE sentence naming the evidence. A person reads it.\n\n"
    'Return ONLY JSON: {"same": "<candidate id or null>", "why": "<evidence>"}'
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


def _default_ask(db, system_prompt: str, documents: list[dict] | None = None):
    def _ask(payload: dict) -> dict:
        from app.interpreter.llm_interpreter import call_llm

        parsed, _ = call_llm(
            system_prompt=system_prompt,
            user_prompt=json.dumps(payload),
            db=db,
            call_type="extraction",
            max_tokens=1200,
            documents=documents,
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


#: Tries at working out the totals: the first answer, then two more told why
#: the one before didn't add up.
WORK_OUT_ATTEMPTS = 3


def _clean_work_out(raw: object, received: set[str], held: set[str]) -> dict:
    """The model's answer, kept to lines that exist and fields that mean
    something; {} when there's nothing in it. ``received``: the ids of lines
    that will be received (changed or removed); ``held``: those that won't be
    (kept, then changed if need be)."""
    if not isinstance(raw, dict):
        return {}
    ids = received | held
    lines = []
    for ln in raw.get("lines") or []:
        if not isinstance(ln, dict) or str(ln.get("line_id")) not in ids:
            continue
        out: dict = {"line_id": str(ln["line_id"])}
        for field in ("quantity", "unit_price"):
            if _f(ln.get(field)) is not None:
                out[field] = _f(ln[field])
        if ln.get("gst") in ("charged", "free"):
            out["gst"] = ln["gst"]
        if len(out) > 1:
            lines.append(out)
    # A line the invoice prints and the list misses: only with a description
    # and both figures — anything less can't be received.
    add = []
    for ln in raw.get("add") or []:
        if not isinstance(ln, dict) or not str(ln.get("description") or "").strip():
            continue
        qty, price = _f(ln.get("quantity")), _f(ln.get("unit_price"))
        if qty is None or price is None or qty == 0:
            continue
        add.append(
            {
                "description": str(ln["description"]).strip(),
                "code": str(ln.get("code") or "").strip() or None,
                "quantity": qty,
                "unit_price": price,
                "gst": ln.get("gst") if ln.get("gst") in ("charged", "free") else None,
            }
        )
    remove = sorted({str(i) for i in raw.get("remove") or [] if str(i) in received})
    keep = sorted({str(i) for i in raw.get("keep") or [] if str(i) in held})
    total = _f(raw.get("total"))
    if not (lines or add or remove or keep) and total is None:
        return {}
    answer: dict = {"lines": lines, "total": total}
    for key, value in (("add", add), ("remove", remove), ("keep", keep)):
        if value:
            answer[key] = value
    answer["why"] = str(raw.get("why") or "").strip()
    return answer


def work_out_totals(
    lines: list[dict],
    header: dict,
    check,
    *,
    document: dict | None = None,
    ask_llm=None,
    db=None,
    tried: list | None = None,
) -> dict:
    """Up to WORK_OUT_ATTEMPTS calls → an answer the caller's ``check``
    accepts, or {}.

    ``lines``: [{id, description, quantity, unit_price, gst_rate, printed,
    receive}] — ``receive`` False for a line that won't be received (struck
    as not on the invoice), which the answer may keep; every other line it
    may change or remove, and it may add a line the invoice prints that the
    list misses. ``header``: {subtotal, tax, total, came_to}.
    ``check(answer)`` returns None when the invoice adds up with the answer
    applied, else what it then comes to — which goes back to the model with
    the answer, for another try. ``document``: the invoice copy as a content
    block, when there is one. ``tried`` collects every answer that didn't add
    up, so a caller can say what was tried.
    """
    if not lines:
        return {}
    ask = ask_llm or _default_ask(
        db, _WORK_OUT_PROMPT, [document] if document else None
    )
    received = {str(ln.get("id")) for ln in lines if ln.get("receive", True)}
    held = {str(ln.get("id")) for ln in lines if not ln.get("receive", True)}
    earlier: list[dict] = tried if tried is not None else []
    for _ in range(WORK_OUT_ATTEMPTS):
        payload = {"lines": lines, "header": header, "earlier_answers": list(earlier)}
        try:
            raw = ask(payload)
        except Exception as exc:  # noqa: BLE001 — the blocker stands, as before
            logger.info("working out the totals failed: %s", exc)
            return {}
        answer = _clean_work_out(raw, received, held)
        came_to = check(answer) if answer else None
        if answer and came_to is None:
            return answer
        earlier.append(
            {
                "answer": answer or raw,
                "problem": (
                    f"with it the invoice comes to {came_to:.2f}"
                    if answer
                    else "it names no line that exists, no line to add and no total"
                ),
            }
        )
    return {}


def same_business(
    name: str,
    details: dict | None,
    candidates: list[dict],
    *,
    ask_llm=None,
    db=None,
) -> dict:
    """One call → ``{"id": <candidate id or None>, "why"}``, or {} on any
    failure. ``candidates``: Loaded supplier records."""
    if not candidates:
        return {}
    ids = {str(c.get("id")) for c in candidates}
    payload = {
        "invoice_supplier": {"name": name, **(details or {})},
        "candidates": [
            {
                "id": str(c.get("id")),
                "name": c.get("name"),
                "address": ", ".join(
                    str(c.get(k))
                    for k in ("address1", "address2", "address3", "city", "postcode")
                    if c.get(k)
                ),
                "phone": c.get("phone1") or c.get("phone2"),
                "emails": [
                    e.get("email") for e in c.get("emails") or [] if isinstance(e, dict)
                ],
                "contact_name": c.get("contactName"),
                "account_number": c.get("customerNumber"),
            }
            for c in candidates
        ],
    }
    try:
        raw = (ask_llm or _default_ask(db, _SAME_BUSINESS_PROMPT))(payload)
    except Exception as exc:  # noqa: BLE001 — undecided, as before
        logger.info("supplier identity check failed: %s", exc)
        return {}
    if not isinstance(raw, dict) or "same" not in raw:
        return {}
    same = raw.get("same")
    if same is not None and str(same) not in ids:
        return {}
    return {
        "id": str(same) if same is not None else None,
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
