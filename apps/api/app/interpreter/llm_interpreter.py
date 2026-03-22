"""LLM helper for domain agents.

Exposes ``call_llm`` — a thin wrapper around the Anthropic SDK that
domain agents call with their own system prompts.  All persistence
and lifecycle remain in the deterministic backend services.
"""

import datetime
import json
import logging
import os
import time

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _build_parsed_response(response) -> dict:
    """Extract a human-readable summary from Anthropic response content blocks."""
    parsed: dict = {"stop_reason": response.stop_reason}
    text_parts = []
    tool_calls_summary = []
    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use":
            tool_calls_summary.append({
                "tool": block.name,
                "input": block.input,
            })
    if text_parts:
        parsed["text"] = "\n".join(text_parts)
    if tool_calls_summary:
        parsed["tool_calls"] = tool_calls_summary
    return parsed


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

    today = datetime.date.today().isoformat()
    dated_user_prompt = f"[{today}] {user_prompt}"

    client = anthropic.Anthropic(api_key=api_key)
    llm_call_id = None
    t0 = time.time()

    try:
        response = client.messages.create(
            model=resolved_model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": dated_user_prompt}],
        )
        raw = response.content[0].text
        duration_ms = int((time.time() - t0) * 1000)
        parsed = _parse_response(raw)

        _input_tokens = response.usage.input_tokens if response.usage else None
        _output_tokens = response.usage.output_tokens if response.usage else None

        # Persist LLM call record
        if db is not None:
            llm_call_id = _persist_llm_call(
                db, task_id=task_id, call_type=call_type,
                model=resolved_model, system_prompt=system_prompt,
                user_prompt=user_prompt, raw_response=raw,
                parsed_response=parsed, status="success",
                duration_ms=duration_ms,
                input_tokens=_input_tokens, output_tokens=_output_tokens,
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
    error_message=None, duration_ms=None, tools_provided=None,
    input_tokens=None, output_tokens=None, user_id=None,
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
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        tools_provided=tools_provided,
    )
    db.add(record)
    db.flush()

    # Aggregate daily usage for billing
    if input_tokens or output_tokens:
        try:
            from app.services.usage_service import record_usage
            # Resolve user_id from task if not provided
            if not user_id and task_id:
                from app.db.models import Task
                task = db.query(Task).filter(Task.id == task_id).first()
                if task:
                    user_id = task.user_id
            record_usage(db, user_id, input_tokens, output_tokens)
        except Exception:
            pass  # Don't fail the LLM call if usage tracking fails

    return record.id


def call_llm_with_tools(
    system_prompt: str,
    messages: list[dict],
    tools: list[dict],
    model: str | None = None,
    db: Session | None = None,
    task_id: str | None = None,
    call_type: str = "tool_use",
    max_tokens: int = 4096,
):
    """Make an Anthropic API call with native tool use.

    Returns the raw Anthropic Message object (not parsed JSON) since
    tool-use responses have a different structure.
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
        from app.agents.tool_loop import _emit_event
        with client.messages.stream(
            model=resolved_model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages,
            tools=tools,
        ) as stream:
            for chunk in stream.text_stream:
                _emit_event({"type": "token", "text": chunk})
            response = stream.get_final_message()
        duration_ms = int((time.time() - t0) * 1000)

        # Serialize for audit logging
        raw_text = json.dumps([block.model_dump() for block in response.content])
        user_prompt_summary = json.dumps(messages[-1]["content"][:500] if isinstance(messages[-1]["content"], str) else "[tool_results]")

        # Extract token usage from response
        _input_tokens = response.usage.input_tokens if response.usage else None
        _output_tokens = response.usage.output_tokens if response.usage else None

        if db is not None:
            llm_call_id = _persist_llm_call(
                db, task_id=task_id, call_type=call_type,
                model=resolved_model, system_prompt=system_prompt,
                user_prompt=user_prompt_summary, raw_response=raw_text,
                parsed_response=_build_parsed_response(response),
                status="success", duration_ms=duration_ms,
                tools_provided=tools if tools else None,
                input_tokens=_input_tokens, output_tokens=_output_tokens,
            )

        return response, llm_call_id

    except Exception as exc:
        duration_ms = int((time.time() - t0) * 1000)
        if db is not None:
            _persist_llm_call(
                db, task_id=task_id, call_type=call_type,
                model=resolved_model, system_prompt=system_prompt,
                user_prompt="[tool_use call]", raw_response=None,
                parsed_response=None, status="error",
                error_message=str(exc), duration_ms=duration_ms,
            )
        raise


def _parse_response(raw: str) -> dict:
    """Parse JSON from LLM response, handling markdown fences."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    return json.loads(text)
