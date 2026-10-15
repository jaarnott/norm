"""Work out the delivered unit for lines where NOTHING says the size — the
estimator behind Norm's unit accuracy, and the authority in the self-healing
model (see docs/unit-resolution.md).

Runs after every deterministic tier has passed (supplier variant, the page's
own derived unit, the Norm supplier-product catalogue): what remains are
lines like 'Pistachio Jam-Can' or 'PEPPERCORNS BLACK WHOLE @ SMART CHOICE' —
no size anywhere on the invoice.

How (14 Oct 2026, replacing one batched choose-from-the-list call):
- Opus with web search estimates the real size of ONE delivered unit, the
  way a purchasing manager would: identify the product, find the formats it
  is actually sold in (this supplier's listing first), read the supplier's
  price level off the invoice, price each real format against it. One call
  per line, in parallel; the answer is cached per invoice line.
- Code, not the model, turns that size into a venue unit — an exact match on
  type and amount ('1 kg' → Kilo, '454 g' → 454 Grams). Asked to pick from
  379 units by id, the model lost 'Litre' for 'Coconut Milk 1L'.
- Measured on 45 products whose size the invoice didn't print (sizes printed
  elsewhere on the page): 93% exact, against ~85% for the six Cook Brothers
  venues' own Loaded set-ups of the same products. Its confidence is
  calibrated: high was right 65 of 66 times, medium 17 of 22.

Doctrine kept from the call this replaces:
- Never invent a unit: an answer the venue has no unit for comes back as
  ``create_name`` (the existing create-unit path, gated), never a link.
- Packaging-word units are never answers.
- Category rules invalidate a type-violating answer.
- Any failure returns no answer for that line — the caller falls back exactly
  as if this module didn't exist.
"""

from __future__ import annotations

import json
import logging
import re

from app.services.invoice_units import (
    is_multipack,
    is_packaging_word,
    parse_unit,
    unit_type_allowed,
)

logger = logging.getLogger(__name__)

_CONFIDENCE = {"high", "medium", "low"}

#: Bump to re-estimate every cached line (the prompt is part of the key too).
_VERSION = "estimate-v1"

#: How many lines are estimated at once.
_WORKERS = 6

#: The server-side web search tool, as measured (14 Oct 2026). Opus 5.5 also
#: takes the newer web_search_20260209; this is the variant the accuracy
#: figures above were measured with.
_WEB_SEARCH = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}

#: Opus 5.5's default effort, sent explicitly: Norm's one-shot default (low)
#: is not what the accuracy was measured at.
_EFFORT = "medium"

_SYSTEM_PROMPT = (
    "You work out the net contents of ONE delivered unit on a New Zealand "
    "hospitality supplier's invoice line, for the venue's stock system. The "
    "size isn't printed. Work like an experienced purchasing manager, and be "
    "accurate: this replaces a person's judgement.\n"
    "1. Identify the product precisely: brand, product, form. Abbreviations "
    "are common (CHSE = cheese, FZN = frozen, '@ SMART CHOICE' = the brand).\n"
    "2. Find the pack formats it is ACTUALLY sold in. Search whenever you are "
    "not certain: first this supplier's own listing (search the supplier name "
    "with the product code or name), then the brand's site or other NZ "
    "wholesalers. Real listings beat memory, and memory beats arithmetic.\n"
    "3. Read this supplier's price level from the other lines on the invoice "
    "whose size you can tell (a size in the name, or a product you know): near "
    "NZ wholesale, or marked up like a franchise supplier?\n"
    "4. For EACH real format, work out the $/kg or $/L this line's price "
    "implies, and compare it with what that product costs per kg or litre at "
    "this supplier's level. Pick the format whose implied price is most "
    "plausible. Never invent a size from price alone.\n"
    "5. If the price can't be a whole pack of any real format (cents for a "
    "jar, $2 for a carton), it is a per-unit rate or an adjustment: ignore it "
    "and pick the format this supplier sells.\n"
    "6. KG as the charge word, or meat/produce priced per kilo, is random "
    "weight: answer '1 kg'. Use 'each' only for things genuinely counted as "
    "items.\n"
    'Reply with ONLY JSON: {"size": "<net contents of ONE delivered unit as '
    "one total measure, e.g. '1 kg', '454 g', '4.5 L', '200 each'; 'each' for "
    'a single counted item>", "confidence": "high|medium|low", "why": '
    '"<one short sentence>"}'
)


def _measure(text: object) -> tuple[str, float] | None:
    """(type, amount in g / mL / count) for a size or a unit name — a
    multipack ('6x750mL', '4x6 pack') as its total."""
    t = str(text or "").strip()
    if not t:
        return None
    if t.lower() in ("each", "ea"):
        return ("count", 1.0)
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*[x×]\s*(.+)$", t, re.I)
    if m:
        inner = parse_unit(m.group(2))
        return (inner[0], float(m.group(1)) * inner[1]) if inner else None
    p = parse_unit(t)
    return (p[0], float(p[1])) if p else None


def match_unit(size: object, units: list[dict]) -> dict | None:
    """The venue unit that IS this size: same type, same amount. Of several,
    the one named as the size was written, then a base unit (Kilo, Litre,
    Each), then the shortest name. Never a looser 'equivalent' — '1 L' is
    not 'Each'."""
    want = _measure(size)
    if want is None:
        return None
    hits = []
    for u in units:
        got = _measure(u.get("name"))
        if (
            got
            and got[0] == want[0]
            and abs(got[1] - want[1]) <= 0.001 * max(want[1], 1)
        ):
            hits.append(u)
    if not hits:
        return None

    def norm(x: object) -> str:
        return re.sub(r"\s+", "", str(x or "").lower())

    return sorted(
        hits,
        key=lambda u: (
            norm(u.get("name")) != norm(size),
            any(ch.isdigit() for ch in str(u.get("name") or "")),
            len(str(u.get("name") or "")),
        ),
    )[0]


def unit_name_for(size: object) -> str | None:
    """A unit name for a size the venue doesn't have yet, in the venues' own
    style ('454 Grams', '2.5 KG', '750 mL', '4.5 L', '24 Pack')."""
    raw = str(size or "").strip()
    if is_multipack(raw):
        return raw
    m = _measure(raw)
    if m is None:
        return None
    kind, amount = m
    if kind == "weight":
        return f"{amount:g} Grams" if amount < 1000 else f"{amount / 1000:g} KG"
    if kind == "volume":
        return f"{amount:g} mL" if amount < 1000 else f"{amount / 1000:g} L"
    return "Each" if amount == 1 else f"{amount:g} Pack"


def line_prompt(
    line: dict, all_lines: list[dict], supplier: str | None, evidence: str | None
) -> str:
    """What the model sees for one line: the supplier, the rest of the invoice
    (its price scale), any catalogue evidence, and the line itself."""
    others = []
    for o in all_lines:
        if not isinstance(o, dict) or o.get("id") == line.get("id"):
            continue
        size = o.get("unit_of_measure")
        others.append(
            f"- {o.get('description')} @ ${o.get('unit_price')}"
            + (f" (printed size {size})" if size else "")
        )
    body = {
        "description": line.get("description"),
        "code": line.get("code"),
        "charge_word": line.get("unit"),
        "quantity": line.get("quantity"),
        "unit_price_ex_tax": line.get("unit_price"),
    }
    return (
        f"Supplier: {supplier}\n"
        "Other lines on this invoice:\n"
        + ("\n".join(others[:40]) or "(none)")
        + (
            f"\n\nNorm's catalogue (evidence, not authority): {evidence}"
            if evidence
            else ""
        )
        + f"\n\nLine: {json.dumps(body)}"
    )


def _default_ask(db):
    """One web-searching Opus call per line. Each runs on its own session
    (they run in parallel), and is recorded like any other call."""

    def _ask(prompt: str) -> dict:
        from app.interpreter.llm_interpreter import call_llm
        from app.services.models import agent_model

        worker_db = None
        if db is not None:
            from app.db.engine import SessionLocal

            worker_db = SessionLocal()
        try:
            parsed, _ = call_llm(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=prompt,
                model=agent_model(worker_db),
                db=worker_db,
                call_type="unit_estimate",
                max_tokens=16000,
                tools=[_WEB_SEARCH],
                effort=_EFFORT,
            )
            if worker_db is not None:
                worker_db.commit()
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            if worker_db is not None:
                worker_db.rollback()
            raise
        finally:
            if worker_db is not None:
                worker_db.close()

    return _ask


def _cache_key(prompt: str) -> str:
    from app.connectors.function_executor import _extraction_cache_key

    return _extraction_cache_key(
        "norm", "unit_estimate", {"prompt": prompt}, {"v": _VERSION}, _SYSTEM_PROMPT
    )


def _estimate_all(prompts: dict[str, str], ask, db) -> dict[str, dict]:
    """{line_id: answer} — cached answers first, the rest in parallel."""
    from concurrent.futures import ThreadPoolExecutor
    import contextvars

    from app.connectors.function_executor import (
        _extraction_cache_get,
        _extraction_cache_put_durable,
    )

    out: dict[str, dict] = {}
    todo: list[tuple[str, str]] = []
    for lid, prompt in prompts.items():
        cached = (
            _extraction_cache_get(db, _cache_key(prompt)) if db is not None else None
        )
        if isinstance(cached, dict):
            out[lid] = cached
        else:
            todo.append((lid, prompt))

    def one(item: tuple[str, str]) -> tuple[str, dict]:
        lid, prompt = item
        try:
            ans = ask(prompt)
        except Exception as exc:  # noqa: BLE001 — an answer is never worth an error
            logger.info("unit estimate failed for %s: %s", lid, exc)
            ans = {}
        return lid, ans if isinstance(ans, dict) else {}

    if todo:
        # Each worker keeps the caller's organisation (usage is counted to it).
        contexts = [contextvars.copy_context() for _ in todo]
        with ThreadPoolExecutor(max_workers=min(_WORKERS, len(todo))) as pool:
            for lid, ans in pool.map(
                lambda ci: ci[0].run(one, ci[1]), zip(contexts, todo)
            ):
                out[lid] = ans
                # Saved there and then: a batch cut off later keeps every
                # estimate it already paid for.
                if db is not None and _measure(ans.get("size")) is not None:
                    _extraction_cache_put_durable(
                        db, _cache_key(prompts[lid]), "norm", "unit_estimate", ans
                    )
    return out


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
    """{line_id: {unit, create_name, size, confidence, why}} for the lines in
    ``qualifying_ids``.

    ``all_lines`` are dicts carrying at least id/description/code/quantity/
    unit/unit_of_measure/unit_price; ``units`` is the venue's live unit list.
    ``ask_llm`` (tests) takes one line's prompt and returns
    ``{"size", "confidence", "why"}``. A line with no usable answer is left
    out — the caller falls back exactly as before.
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
    if not ids:
        return {}
    by_id = {str(ln.get("id")): ln for ln in all_lines or [] if isinstance(ln, dict)}
    prompts = {
        lid: line_prompt(
            by_id[lid], all_lines, supplier_name, (evidence_by_line or {}).get(lid)
        )
        for lid in ids
        if lid in by_id
    }
    answers = _estimate_all(prompts, ask_llm or _default_ask(db), db)

    out: dict[str, dict] = {}
    for lid, ans in answers.items():
        size = ans.get("size")
        if _measure(size) is None or is_packaging_word(size):
            continue
        conf = str(ans.get("confidence") or "").strip().lower()
        if conf not in _CONFIDENCE:
            conf = "low"
        unit = match_unit(size, live)
        create_name = None if unit is not None else unit_name_for(size)
        # Category rules trump the model: a type its category forbids is
        # invalid, whatever the confidence.
        category = (category_by_line or {}).get(lid)
        kind = _measure(size)[0]
        if category and not unit_type_allowed(category, kind):
            logger.info("unit estimate %r rejected for %s line %s", size, category, lid)
            unit, create_name, conf = None, None, "low"
        out[lid] = {
            "unit": unit,
            "create_name": create_name,
            "size": str(size).strip(),
            "confidence": conf,
            "why": str(ans.get("why") or "").strip(),
        }
    return out
