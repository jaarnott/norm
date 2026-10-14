"""Pick a unit Loaded ALREADY has for a line whose unit could not be resolved.

The `receive_without_unit` toggle exists for the case where the copy gives no
readable unit and the catalogue offers no default: without it the invoice
simply parks. The toggle's job is to let Norm choose sensibly rather than
guess wildly, and the important word is CHOOSE — this never creates.

That asymmetry is deliberate. A wrong match is one wrong line on one invoice,
visible and fixable. A wrong CREATE is a permanent unit in the catalogue that
every future invoice can then match against, and nobody goes looking for it.
`auto_create_units` is the separate, explicit toggle for creating.

Heuristics first, because they are free and exact — `invoice_units` already
knows that '0.7 L' and '700 mL' are the same pack and that '6x1000mL' is not
'6 Pack'. The model is only asked when the arithmetic cannot decide, and it is
asked to pick from a list, never to invent a name.

When the model can't settle it either, a last pass (``insist``) makes it pick
the closest unit anyway. A line with no unit stops autopilot outright, and a
venue that switched on receive-without-a-unit has said a marked guess beats a
parked invoice (the user, 14 Oct 2026, after 'Pistachio Jam-Can' and
'Strawberry Syrup-Case' parked INV-0446). It is still a pick from the list,
never a create.
"""

from __future__ import annotations

import json
import logging

from app.services.invoice_units import is_packaging_word, parse_unit, units_equivalent

logger = logging.getLogger(__name__)


def _live(units: list[dict]) -> list[dict]:
    return [
        u
        for u in units or []
        if isinstance(u, dict) and u.get("id") and not u.get("datestampDeleted")
    ]


def guess_unit(
    line: dict, units: list[dict], *, ask_llm=None, insist=None
) -> tuple[dict | None, str]:
    """Return ``(unit, why)`` — a unit from ``units``, or ``(None, reason)``.

    ``ask_llm`` is injected so the caller owns the model call (and so this is
    testable without one). It receives ``(line, candidates)`` and returns the
    chosen unit id or None. ``insist`` has the same shape and is the last
    resort: asked only when nothing else chose, and it may not decline.
    """
    live = _live(units)
    if not live:
        return None, "this venue has no units in Loaded to choose from"

    printed = line.get("unit") or line.get("unit_of_measure")
    if is_packaging_word(printed):
        # 'PACK'/'CTN' on the copy is bundling, not a measure — and it would
        # "parse" (bare pack reads as a count of 1) and then name-match a unit
        # literally called PACK, which is exactly the meaningless link this
        # module exists to avoid. Treat it as no evidence: fall through to the
        # model, which picks from real units only.
        printed = None
    parsed = parse_unit(printed)

    # 1. The copy named something measurable — trust the arithmetic.
    if parsed or printed:
        exact = [u for u in live if units_equivalent(printed, u.get("name"))]
        if len(exact) == 1:
            return exact[0], f"'{printed}' is the same pack as '{exact[0]['name']}'"
        if len(exact) > 1:
            # Prefer the one whose NAME matches most closely, else refuse
            # rather than pick arbitrarily between equals.
            same = [
                u
                for u in exact
                if str(u.get("name") or "").strip().lower()
                == str(printed).strip().lower()
            ]
            if len(same) == 1:
                return same[0], f"'{printed}' matches '{same[0]['name']}' exactly"
            forced = _insist(line, exact, insist)
            if forced is not None:
                return forced
            return None, f"'{printed}' matches {len(exact)} units equally"

    # 2. Nothing to parse. Offer the model the real list and let it choose.
    if ask_llm is None and insist is None:
        return None, "no unit on the copy and no model available to choose one"
    candidates = [u for u in live if not is_packaging_word(u.get("name"))]
    if not candidates:
        return None, "every unit in this venue is a packaging word"
    match, why = None, "no existing unit fits this line"
    if ask_llm is not None:
        try:
            chosen_id = ask_llm(line, _offer(candidates))
        except Exception as exc:  # noqa: BLE001 — a guess is never worth an error
            logger.info("unit guess failed for line %s: %s", line.get("id"), exc)
            chosen_id, why = None, "the unit could not be worked out"
        # An id that isn't on the list is the failure mode that matters: it
        # would mean inventing a unit through the back door.
        match = next((u for u in candidates if u["id"] == chosen_id), None)
        if match is not None and _invents_a_size(line, match):
            match, why = None, f"'{match.get('name')}' is a size the line doesn't state"
    if match is not None:
        return (
            match,
            f"closest existing unit for '{line.get('description') or 'this line'}'",
        )
    # 3. Nothing chose. The last pass may not decline.
    forced = _insist(line, candidates, insist)
    return forced if forced is not None else (None, why)


def _invents_a_size(line: dict, unit: dict) -> bool:
    """A unit that carries a size (a ratio other than 1: '12.5 L', '1500 Pack')
    picked for a line with no number in it — the size came from nowhere, and a
    wrong one multiplies stock. Base units (Each, Kilo, Litre) pass: loose
    produce is legitimately counted by the kilo. The prompt says the same;
    this holds it when the model doesn't ('Strawberry Syrup-Case' drew
    '12.5 L' on the last pass, 14 Oct 2026)."""
    ratio = unit.get("ratio")
    if not isinstance(ratio, (int, float)) or abs(float(ratio) - 1.0) < 1e-9:
        return False
    stated = f"{line.get('description') or ''} {line.get('unit') or ''}"
    return not any(ch.isdigit() for ch in stated)


def _offer(units: list[dict]) -> list[dict]:
    return [
        {
            "id": u["id"],
            "name": u.get("name"),
            "ratio": u.get("ratio"),
            "type": u.get("stockUnitType"),
        }
        for u in units
    ]


def _insist(line: dict, candidates: list[dict], insist) -> tuple[dict, str] | None:
    """The last-resort pick from ``candidates`` — None when there is no
    ``insist`` pass, it fails, or it names a unit that isn't on the list. A
    size the line doesn't state becomes 'Each', one of whatever came."""
    if insist is None or not candidates:
        return None
    try:
        chosen_id = insist(line, _offer(candidates))
    except Exception as exc:  # noqa: BLE001 — a guess is never worth an error
        logger.info("forced unit guess failed for line %s: %s", line.get("id"), exc)
        return None
    match = next((u for u in candidates if u["id"] == chosen_id), None)
    if match is not None and _invents_a_size(line, match):
        match = next(
            (
                u
                for u in candidates
                if str(u.get("name") or "").strip().lower() == "each"
            ),
            None,
        )
    if match is None:
        return None
    return (
        match,
        f"'{match.get('name')}' is Norm's best guess from the venue's units; check it",
    )


#: Both passes share this: a pack size or weight the line doesn't state is an
#: invention, and a wrong one multiplies stock — 'Strawberry Syrup-Case' at $96
#: drew '1500 Pack' (14 Oct 2026), i.e. one case booked as 1,500 units.
_SIZE_RULE = (
    "Pick a sized unit (a weight, a volume or a pack count) only when the "
    "line's description or unit states that size. When it states none, the "
    "unit is 'Each' — one of whatever was delivered (a can, a bag, a box, a "
    "case) — if the list has it."
)

_CHOOSE_SYSTEM = (
    "You choose the unit an invoice line was delivered in, from the venue's "
    f"existing units. {_SIZE_RULE} Reply with ONLY a JSON object and no other "
    'text: {"unit": <its number on the list>}, or {"unit": null} when none of '
    "them fits."
)

_INSIST_SYSTEM = (
    "You choose the unit an invoice line was delivered in, from the venue's "
    "existing units. Nothing on the invoice settles it and the line cannot be "
    "received without one, so you MUST pick the closest unit on the list: the "
    f"unit a person would most likely count this delivery in. {_SIZE_RULE} "
    'Reply with ONLY a JSON object and no other text: {"unit": <its number '
    "on the list>}. Never null, and never a number that is not on the list."
)


def llm_chooser(db, model_call=None, *, insist: bool = False):
    """An ``ask_llm`` backed by Claude: the line plus a candidate list, and an
    id back. ``insist`` is the last-resort pass, which may not decline.

    Through ``call_llm`` (JSON-only, retried, recorded): the direct call this
    replaced allowed 64 output tokens, and the router model spent them
    explaining itself, so the JSON never arrived and every hard line parked
    (INV-0446, 14 Oct 2026)."""

    def _ask(line: dict, candidates: list[dict]) -> str | None:
        from app.interpreter.llm_interpreter import call_llm
        from app.services.models import router_model

        fields = ("description", "code", "unit", "quantity_received", "unit_cost")
        # Numbered, not by id: 379 units each under a 36-character uuid buried
        # 'Litre' for 'Coconut Milk 1L' (the model picked '1.36 L', then
        # '2.7 KG'). The number maps back to the id here.
        listing = "\n".join(
            f"{n}. {c.get('name')} ({c.get('type') or 'unknown type'}, "
            f"ratio {c.get('ratio')})"
            for n, c in enumerate(candidates, 1)
        )
        parsed, _ = call_llm(
            system_prompt=_INSIST_SYSTEM if insist else _CHOOSE_SYSTEM,
            user_prompt=(
                f"Line: {json.dumps({k: line.get(k) for k in fields})}\n"
                "Units (number. name (type, ratio to its base unit)):\n"
                f"{listing}"
            ),
            model=router_model(db),
            db=db,
            call_type="unit_guess",
            max_tokens=400,
        )
        n = parsed.get("unit") if isinstance(parsed, dict) else None
        if isinstance(n, bool) or not isinstance(n, (int, float, str)):
            return None
        try:
            n = int(n)
        except ValueError:
            return None
        return candidates[n - 1]["id"] if 1 <= n <= len(candidates) else None

    return model_call or _ask
