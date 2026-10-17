"""Insight prompts in the organisation's own words — a small model, Norm's cost.

A detector writes each prompt from a template: right, but the same shape every
week and in Norm's words rather than the venue's (the team says "FT", the
template says "the First Table discount"). After a venue's run, the insights
whose template text changed are reworded in one call to the small model, with
the organisation's vocabulary memories, and each answer is checked: no
number the template doesn't have (one may be left out, never changed), the
venue still named, one line of a sensible length. An answer that fails the
check keeps the template, so a bad rewording never reaches Home.

The call is Norm's own cost: it is recorded with no organisation, so it never
counts against a plan (docs/plans/home-suggested-prompts.md). The template
stays on the insight (``data["wording"]["template"]``), so an unchanged
template next run keeps its wording without another call.
"""

from __future__ import annotations

import json
import logging
import re
import time

logger = logging.getLogger(__name__)

CALL_TYPE = "insight_wording"
MAX_CHARS = 240
MAX_VOCABULARY = 20

SYSTEM = """You rewrite suggested questions that a hospitality manager can tap to ask Norm, their AI operations assistant. Each one shows on ONE line under the chat box (only about the first 100 characters are visible) and is sent to Norm as written when tapped.

Rewrite every prompt — don't hand it back unchanged:
- As the manager would actually type it to Norm: short, conversational, first person ("we", "our", "show me"). The question first.
- Aim for under 140 characters. Keep the venue's name and the one or two figures that matter most; leave out secondary figures and comparisons.
- Never change a number or add one: every figure you keep is copied exactly as given ($7,553 stays $7,553). Add no facts or advice.
- A figure you keep still says what it measured: "16% of sales went on discounts" must not become "16% of discounts"; "$175 an hour in sales" is sales, not wages. If you can't keep its meaning in fewer words, leave the figure out.
- Where the organisation's vocabulary has its own word for something, use it.
- Keep what it asks Norm to show, in fewer words.
- One line, no quotation marks around it, no emoji.

Example
In: How can I bring Tuesday wages down at La Zeppa? They've run at 104% of sales on Tuesdays over the last 4 weeks, against 30% across the week — show me who's rostered, the hours, and Tuesday trade by hour.
Out: Tuesday wages at La Zeppa are 104% of sales — who's on, for how long, and what's trade doing by the hour?

Return JSON only: {"prompts": [{"id": "...", "prompt": "..."}]}"""

#: Numbers as a prompt writes them: $7,553 · 14% · 2.5 · 4.
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers(text: str) -> list[str]:
    return [n.replace(",", "") for n in _NUMBER.findall(text or "")]


def keeps_the_facts(template: str, worded: str, venue_name: str) -> bool:
    """Safe to show in the template's place: no number that the template
    doesn't have (a figure may be left out, never changed or made up), at
    least one of its figures kept, the venue named if it was, and one line of
    a length Home can show."""
    if not worded or "\n" in worded or len(worded) > MAX_CHARS:
        return False
    if (
        venue_name.lower() in template.lower()
        and venue_name.lower() not in worded.lower()
    ):
        return False
    given, kept = numbers(template), numbers(worded)
    if given and not kept:
        return False
    for n in kept:
        if n in given:
            given.remove(n)
        else:
            return False
    return True


def vocabulary(db, org_id: str, venue_id: str | None) -> list[str]:
    """The organisation's own words: its active vocabulary memories, org-wide
    or for this venue."""
    from sqlalchemy import or_

    from app.db.models import Memory

    rows = (
        db.query(Memory)
        .filter(
            Memory.organization_id == org_id,
            Memory.scope == "org",
            Memory.type == "vocabulary",
            Memory.status == "active",
            or_(Memory.venue_id.is_(None), Memory.venue_id == venue_id),
        )
        .order_by(Memory.updated_at.desc())
        .limit(MAX_VOCABULARY)
        .all()
    )
    return [f"{m.title}: {m.body}"[:200] for m in rows]


def needs_wording(row) -> bool:
    """Not yet tried for its current words: the run's save() keeps the
    wording only while the template is unchanged."""
    return "wording" not in (row.data or {})


def word(db, venue, rows) -> dict:
    """Reword ``rows`` (Insights whose ``prompt`` is still the template) in
    place. Returns what happened, for the run's summary; never raises."""
    from app.config import settings

    rows = [r for r in rows if needs_wording(r)]
    if not rows or not settings.INSIGHT_WORDING_ENABLED:
        return {"asked": 0}
    by_id = {str(i): r for i, r in enumerate(rows, 1)}
    ask = {
        "venue": venue.name,
        "vocabulary": vocabulary(db, venue.organization_id, venue.id),
        "prompts": [{"id": i, "prompt": r.prompt} for i, r in by_id.items()],
    }
    try:
        answer = _call(db, json.dumps(ask, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 — the templates stand
        logger.warning("insight wording failed: %s", exc)
        return {"asked": len(rows), "error": str(exc)[:200]}
    answers = {
        str(item.get("id")): " ".join(str(item.get("prompt") or "").split())
        for item in (answer or {}).get("prompts") or []
        if isinstance(item, dict)
    }
    worded = 0
    for i, row in by_id.items():
        text = answers.get(i, "")
        ok = keeps_the_facts(row.prompt, text, venue.name)
        # A rewording that fails the check is remembered as tried (prompt
        # None), so the same template isn't asked again every run.
        row.data = {
            **(row.data or {}),
            "wording": {"template": row.prompt, "prompt": text if ok else None},
        }
        if ok:
            row.prompt = text
            worded += 1
    return {"asked": len(rows), "worded": worded}


def _call(db, user_prompt: str) -> dict | None:
    """One call to the small model, recorded with NO organisation (Norm's
    cost, never a plan's)."""
    import anthropic

    from app.db.models import LlmCall
    from app.services.circuit_breaker import anthropic_breaker
    from app.services.llm_cost import Usage, billable_tokens, cost_usd
    from app.services.models import router_model
    from app.services.secrets import get_api_key

    if not anthropic_breaker.allow_request():
        raise RuntimeError("Anthropic is unavailable (circuit breaker open)")
    api_key = get_api_key("anthropic", "api_key", db)
    if not api_key:
        raise RuntimeError("no Anthropic API key")
    model = router_model(db)
    t0 = time.time()
    response = anthropic.Anthropic(api_key=api_key, timeout=60.0).messages.create(
        model=model,
        max_tokens=1500,
        system=SYSTEM,
        messages=[{"role": "user", "content": user_prompt}],
    )
    raw = "".join(
        b.text for b in response.content if getattr(b, "type", None) == "text"
    )
    usage = Usage.from_response(response.usage)
    match = re.search(r"\{.*\}", raw, re.S)
    try:
        parsed = json.loads(match.group(0)) if match else None
    except ValueError:
        parsed = None
    db.add(
        LlmCall(
            organization_id=None,
            call_type=CALL_TYPE,
            model=model,
            system_prompt=SYSTEM,
            user_prompt=user_prompt,
            raw_response=raw,
            parsed_response=parsed,
            status="success" if parsed else "error",
            error_message=None if parsed else "reply was not JSON",
            duration_ms=int((time.time() - t0) * 1000),
            input_tokens=usage.input,
            output_tokens=usage.output,
            cache_read_tokens=usage.cache_read,
            cache_write_tokens=usage.cache_write,
            billable_tokens=billable_tokens(model, usage),
            cost_usd=cost_usd(model, usage),
        )
    )
    return parsed
