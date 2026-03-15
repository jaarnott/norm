"""LLM helper for domain agents.

Exposes ``call_llm`` — a thin wrapper around the Anthropic SDK that
domain agents call with their own system prompts.  All persistence
and lifecycle remain in the deterministic backend services.
"""

import json
import logging
import os
import time

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Reusable LLM helper — agents import this directly
# ---------------------------------------------------------------------------

def call_llm(
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    db: Session | None = None,
    task_id: str | None = None,
    call_type: str = "interpretation",
    max_tokens: int = 4096,
) -> tuple[dict, str | None]:
    """Make a single Anthropic API call and return (parsed_json, llm_call_id).

    This is the shared entry-point that domain agents use with their
    own prompts.  Raises on missing API key or parse failure.
    """
    from app.services.secrets import get_api_key

    api_key = get_api_key("anthropic", "api_key", db)
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY is required for LLM calls")

    import anthropic

    resolved_model = model or os.environ.get("LLM_INTERPRETER_MODEL", "claude-sonnet-4-20250514")

    client = anthropic.Anthropic(api_key=api_key)
    llm_call_id = None
    t0 = time.time()

    try:
        response = client.messages.create(
            model=resolved_model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = response.content[0].text
        duration_ms = int((time.time() - t0) * 1000)
        parsed = _parse_response(raw)

        # Persist LLM call record
        if db is not None:
            llm_call_id = _persist_llm_call(
                db, task_id=task_id, call_type=call_type,
                model=resolved_model, system_prompt=system_prompt,
                user_prompt=user_prompt, raw_response=raw,
                parsed_response=parsed, status="success",
                duration_ms=duration_ms,
            )

        return parsed, llm_call_id

    except Exception as exc:
        duration_ms = int((time.time() - t0) * 1000)
        if db is not None:
            _persist_llm_call(
                db, task_id=task_id, call_type=call_type,
                model=resolved_model, system_prompt=system_prompt,
                user_prompt=user_prompt, raw_response=None,
                parsed_response=None, status="error",
                error_message=str(exc), duration_ms=duration_ms,
            )
        raise


def _persist_llm_call(
    db: Session, *, task_id, call_type, model, system_prompt,
    user_prompt, raw_response, parsed_response, status,
    error_message=None, duration_ms=None,
) -> str:
    from app.db.models import LlmCall
    record = LlmCall(
        task_id=task_id,
        call_type=call_type,
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        raw_response=raw_response,
        parsed_response=parsed_response,
        status=status,
        error_message=error_message,
        duration_ms=duration_ms,
    )
    db.add(record)
    db.flush()
    return record.id


def _parse_response(raw: str) -> dict:
    """Parse JSON from LLM response, handling markdown fences."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    return json.loads(text)
