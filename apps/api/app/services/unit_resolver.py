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
- Code, not the model, turns that size into a venue unit, with the one check
  every step uses (``invoice_units.match_unit_record``): '1 kg' → Kilo,
  '454 g' → 454 Grams, and a pack only to the same pack ('3x5kg' is never
  '15 KG'). Asked to pick from 379 units by id, the model lost 'Litre' for
  'Coconut Milk 1L'.
- Measured on 45 products whose size the invoice didn't print (sizes printed
  elsewhere on the page): 93% exact, against ~85% for the six Cook Brothers
  venues' own Loaded set-ups of the same products. Its confidence is
  calibrated: high was right 65 of 66 times, medium 17 of 22.

Doctrine kept from the call this replaces:
- Never invent a unit: an answer the venue has no unit for comes back as
  ``create_name`` (the existing create-unit path, gated), never a link.
- Packaging-word units are never answers.
- Category rules invalidate a type-violating answer.
- A call that fails, or an answer that isn't a usable size, is asked once
  more. A line still without an answer is left out; the Replica marks it
  "Norm's answer didn't come through" and the next review asks again (owner,
  19 Oct 2026). Only usable answers are cached, so a failure never repeats
  from the cache.
"""

from __future__ import annotations

import json
import logging
import re

from app.services.invoice_units import (
    CATEGORY_UNIT_TYPES,
    count_first,
    is_charge_line,
    is_multipack,
    is_packaging_word,
    match_unit_record,
    parse_unit,
    unit_type_allowed,
)

logger = logging.getLogger(__name__)

_CONFIDENCE = {"high", "medium", "low"}

#: Bump to re-estimate every cached line (the prompt is part of the key too).
_VERSION = (
    "estimate-v2"  # v2: the reading's unit rules + the venue's country (18 Oct 2026)
)

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
    "You work out the unit ONE line of a hospitality supplier's invoice is "
    "charged in, for the venue's stock system, when the invoice prints no size. "
    "The venue's country is given with the line: use that country's suppliers, "
    "listings and prices. Work like an experienced purchasing manager, and be "
    "accurate: this replaces a person's judgement.\n"
    "The unit is a MEASUREMENT: a weight, a volume, or a count of identical "
    "items. It is what ONE unit of the line's quantity is, at the line's price. "
    "These are the rules Norm uses when it reads invoices, and they decide:\n"
    "- A discrete item used whole (a bun, a roll, a loaf, a focaccia slab, a "
    "slice of cake, a pie, an ice-cream cup, a patty, a mop head) is 'each', "
    "even when its weight is known; a carton of them is counted: 72 buns is "
    "'72 each', never '72x95g'.\n"
    "- A whole piece of cured meat or cheese priced each (a salami, a "
    "mortadella, a cheese wheel or block) is its nominal size, e.g. '2.25 kg'.\n"
    "- Herbs and leafy greens sold by the bunch are the weight of ONE bunch, "
    "e.g. '100 g'.\n"
    "- A box, bag or tray of fruit or vegetables is its weight, e.g. '15 kg'.\n"
    "- Drinks sold whole are counted: a single-serve bottle or can (about 350 ml "
    "or less) is 'each', a carton of 24 cans is '24 each'. Bottles of a litre or "
    "more are poured, so they are a volume ('1 L'; six of them '6x1L').\n"
    "- Meat or produce priced per kilo is random weight: answer '1 kg'.\n"
    "- Anything else sold in a fixed pack (a jar, a tub, a bag of flour, a "
    "bottle of oil) is the pack's net contents.\n"
    "Method:\n"
    "1. Identify the product precisely: brand, product, form. Abbreviations "
    "are common (CHSE = cheese, FZN = frozen, '@ SMART CHOICE' = the brand).\n"
    "2. Find the pack formats it is ACTUALLY sold in. Search whenever you are "
    "not certain: first this supplier's own listing (search the supplier name "
    "with the product code or name), then the brand's site or other wholesalers "
    "in the venue's country. Real listings beat memory, and memory beats "
    "arithmetic.\n"
    "3. Read this supplier's price level from the other lines on the invoice "
    "whose size you can tell (a size in the name, or a product you know): near "
    "wholesale, or marked up like a franchise supplier?\n"
    "4. For EACH real format, work out the price per kg, litre or item this "
    "line's price implies, and compare it with what that product costs at this "
    "supplier's level. Pick the format whose implied price is most plausible. "
    "Never invent a size from price alone.\n"
    "5. If the price can't be a whole pack of any real format (cents for a "
    "jar, $2 for a carton), it is a per-unit rate or an adjustment: ignore it "
    "and pick the format this supplier sells.\n"
    'Reply with ONLY JSON: {"size": "<the unit as one total measure, e.g. '
    "'1 kg', '454 g', '4.5 L', '24 each', '6x1L'; 'each' for a single counted "
    'item>", "confidence": "high|medium|low", "why": "<one short sentence>"}'
)


def _replace_cached(db, key: str, ans: dict) -> None:
    """Put a usable answer where a cached one the current checks refuse
    sits. The cache is insert-only on its key, so the refused answer would
    otherwise be read, refused and asked around on every review."""
    from sqlalchemy.orm import Session as _Session

    from app.db.models import DocumentExtraction

    try:
        own = _Session(bind=db.get_bind())
    except Exception:  # noqa: BLE001 — the cache is best-effort
        return
    try:
        own.query(DocumentExtraction).filter(
            DocumentExtraction.cache_key == key
        ).delete()
        own.add(
            DocumentExtraction(
                cache_key=key, connector="norm", action="unit_estimate", data=ans
            )
        )
        own.commit()
    except Exception as exc:  # noqa: BLE001 — the cache is best-effort
        logger.debug("unit estimate cache replace skipped: %s", exc)
        own.rollback()
    finally:
        own.close()


def _size_problem(ans: dict) -> str | None:
    """Why an estimate's size can't be booked, or None when it can."""
    if _usable((ans or {}).get("size")):
        return None
    return "it isn't a size a unit can be made from (like 5kg, 750ml, 12 pack or each)"


def _usable(size: object) -> bool:
    """A size the estimate can stand on: measurable, and not a packaging word
    ('pack' measures as a count of one but says nothing)."""
    return _measure(size) is not None and not is_packaging_word(size)


def _measure(text: object) -> tuple[str, float] | None:
    """(type, amount in g / mL / count) for a size or a unit name — a
    multipack ('6x750mL', '4x6 pack') as its total."""
    t = count_first(str(text or "").replace("×", "x").strip())
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


def unit_name_for(size: object) -> str | None:
    """A unit name for a size the venue doesn't have yet, in the venues' own
    style ('454 Grams', '2.5 KG', '750 mL', '4.5 L', '24 Pack', '5x3 KG')."""
    raw = str(size or "").replace("×", "x").strip()
    if is_multipack(raw):
        # Created as the pack it is, count first ('700ml x 6' → '6x700ml'),
        # each item's size spelt the venues' way ('150x35g' → '150x35 Grams').
        packed = count_first(raw)
        m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*x\s*(.+)$", packed, re.I)
        inner = unit_name_for(m.group(2)) if m else None
        if not inner or inner == "Each":
            return packed
        return f"{float(m.group(1)):g}x{inner}"
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
    line: dict,
    all_lines: list[dict],
    supplier: str | None,
    evidence: str | None,
    country: str | None = None,
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
        (f"Venue country: {country}\n" if country else "") + f"Supplier: {supplier}\n"
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


def _estimate_all(prompts: dict[str, str], ask, db, problem=None) -> dict[str, dict]:
    """{line_id: answer} — cached answers first, the rest in parallel.

    ``problem(line_id, answer)`` names what makes an answer unusable (None
    when it is fine). An unusable answer is asked once more, with the reason,
    and is never cached: a failure must not come back from the cache on the
    next review (owner, 19 Oct 2026)."""
    problem = problem or (lambda _lid, ans: _size_problem(ans))
    from concurrent.futures import ThreadPoolExecutor
    import contextvars

    from app.connectors.function_executor import (
        _extraction_cache_get,
        _extraction_cache_put_durable,
    )

    out: dict[str, dict] = {}
    todo: list[tuple[str, str]] = []
    stale: set[str] = set()  # cached under older checks, now refused
    for lid, prompt in prompts.items():
        cached = (
            _extraction_cache_get(db, _cache_key(prompt)) if db is not None else None
        )
        if isinstance(cached, dict) and problem(lid, cached) is None:
            out[lid] = cached
        else:
            if isinstance(cached, dict):
                stale.add(lid)
            todo.append((lid, prompt))

    def one(item: tuple[str, str]) -> tuple[str, dict]:
        lid, prompt = item
        ans: dict = {}
        question = prompt
        # Twice at most: call_llm already retries a dropped connection, so
        # this covers an answer that couldn't be read or couldn't be used.
        for attempt in (1, 2):
            try:
                got = ask(question)
            except Exception as exc:  # noqa: BLE001 — an answer is never worth an error
                logger.info(
                    "unit estimate failed for %s (try %d): %s", lid, attempt, exc
                )
                got = {}
            ans = got if isinstance(got, dict) else {}
            why_not = problem(lid, ans)
            if why_not is None:
                break
            if ans.get("size"):
                question = (
                    f"{prompt}\n\nYour last answer, '{ans.get('size')}', can't be "
                    f"used: {why_not}. Answer again."
                )
        return lid, ans

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
                if db is not None and problem(lid, ans) is None:
                    if lid in stale:
                        _replace_cached(db, _cache_key(prompts[lid]), ans)
                    else:
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
    country: str | None = None,
) -> dict[str, dict]:
    """{line_id: {unit, create_name, size, confidence, why}} for the lines in
    ``qualifying_ids``.

    ``all_lines`` are dicts carrying at least id/description/code/quantity/
    unit/unit_of_measure/unit_price; ``units`` is the venue's live unit list.
    ``ask_llm`` (tests) takes one line's prompt and returns
    ``{"size", "confidence", "why"}``. A line with no usable answer after one
    retry is left out; the Replica marks it, and the next review asks again.
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
    from app.services.supplier_catalog import product_key

    # One question per product: a monthly rental invoice lists each item once
    # a week, and Alsco's four lines of one mat were four web searches (15 Oct
    # 2026). Lines with the same code AND the same description (ignoring case
    # and punctuation) share the first line's answer — a code alone is not
    # enough: Angus Meats bills four different cuts as "BONES". A charge
    # (delivery fee, freight, a levy) is one charge — Each — and is never
    # asked about.
    prompts: dict[str, str] = {}
    asked_as: dict[str, str] = {}
    charges: list[str] = []
    first_for: dict[tuple, str] = {}
    for lid in ids:
        ln = by_id.get(lid)
        if ln is None:
            continue
        if is_charge_line(ln.get("description")):
            charges.append(lid)
            continue
        code = str(ln.get("code") or "").strip().lower()
        words = product_key(None, ln.get("description"))
        key = (code, words) if (code or words) else ("line", lid)
        asked_as[lid] = first_for.setdefault(key, lid)
        if asked_as[lid] == lid:
            prompts[lid] = line_prompt(
                ln,
                all_lines,
                supplier_name,
                (evidence_by_line or {}).get(lid),
                country,
            )

    def problem(lid: str, ans: dict) -> str | None:
        # The category rule below, applied while there is still a chance to
        # ask again: a beverage is never a weight, packaging is a count.
        why_not = _size_problem(ans)
        if why_not:
            return why_not
        category = (category_by_line or {}).get(lid)
        kind = _measure(ans.get("size"))[0]
        if category and not unit_type_allowed(category, kind):
            allowed = " or ".join(
                sorted(CATEGORY_UNIT_TYPES.get(str(category).strip().lower(), ()))
            )
            return f"a {category} product is measured by {allowed}, not by {kind}"
        return None

    asked = (
        _estimate_all(prompts, ask_llm or _default_ask(db), db, problem)
        if prompts
        else {}
    )
    answers = {lid: asked.get(first, {}) for lid, first in asked_as.items()}
    for lid in charges:
        answers[lid] = {
            "size": "each",
            "confidence": "high",
            "why": "a delivery, freight or service charge is billed as one charge",
        }

    out: dict[str, dict] = {}
    for lid, ans in answers.items():
        size = ans.get("size")
        if not _usable(size):
            continue
        conf = str(ans.get("confidence") or "").strip().lower()
        if conf not in _CONFIDENCE:
            conf = "low"
        # The same check as every other step (invoice_units.match_unit_record):
        # a pack matches only the same pack, never a single size of its total.
        unit = match_unit_record(size, live)
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
