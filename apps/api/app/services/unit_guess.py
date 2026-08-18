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
    line: dict, units: list[dict], *, ask_llm=None
) -> tuple[dict | None, str]:
    """Return ``(unit, why)`` — a unit from ``units``, or ``(None, reason)``.

    ``ask_llm`` is injected so the caller owns the model call (and so this is
    testable without one). It receives ``(line, candidates)`` and returns the
    chosen unit id or None.
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
            return None, f"'{printed}' matches {len(exact)} units equally"

    # 2. Nothing to parse. Offer the model the real list and let it choose.
    if ask_llm is None:
        return None, "no unit on the copy and no model available to choose one"
    candidates = [
        {"id": u["id"], "name": u.get("name"), "ratio": u.get("ratio")}
        for u in live
        if not is_packaging_word(u.get("name"))
    ]
    if not candidates:
        return None, "every unit in this venue is a packaging word"
    try:
        chosen_id = ask_llm(line, candidates)
    except Exception as exc:  # noqa: BLE001 — a guess is never worth an error
        logger.info("unit guess failed for line %s: %s", line.get("id"), exc)
        return None, "the unit could not be worked out"
    match = next((u for u in live if u["id"] == chosen_id), None)
    if match is None:
        # An id that isn't on the list is the failure mode that matters: it
        # would mean inventing a unit through the back door.
        return None, "no existing unit fits this line"
    return (
        match,
        f"closest existing unit for '{line.get('description') or 'this line'}'",
    )


def llm_chooser(db, model_call=None):
    """An ``ask_llm`` backed by Claude, in the shape `item_match` already uses:
    the line plus a candidate list, and an id back."""

    def _ask(line: dict, candidates: list[dict]) -> str | None:
        import anthropic

        from app.config import settings as app_settings
        from app.services.models import router_model

        prompt = (
            "Pick the unit ONE of these is delivered in, from the list. "
            'Answer with JSON {"unit_id": "<id>"} — or '
            '{"unit_id": null} if none of them fits.\n\n'
            f"Line: {json.dumps({k: line.get(k) for k in ('description', 'code', 'unit', 'quantity_received')})}\n"
            f"Units available: {json.dumps(candidates)}"
        )
        client = anthropic.Anthropic(api_key=app_settings.ANTHROPIC_API_KEY)
        res = client.messages.create(
            model=router_model(db),
            max_tokens=64,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = res.content[0].text.strip()
        if raw.startswith("```"):
            raw = "\n".join(
                ln for ln in raw.split("\n") if not ln.strip().startswith("```")
            ).strip()
        return (json.JSONDecoder().raw_decode(raw)[0] or {}).get("unit_id")

    return model_call or _ask
