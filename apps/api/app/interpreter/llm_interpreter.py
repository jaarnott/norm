"""LLM helper for domain agents.

Exposes ``call_llm`` — a thin wrapper around the Anthropic SDK that
domain agents call with their own system prompts.  All persistence
and lifecycle remain in the deterministic backend services.
"""

import datetime
import json
import logging
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
            tool_calls_summary.append(
                {
                    "tool": block.name,
                    "input": block.input,
                }
            )
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
    thread_id: str | None = None,
    call_type: str = "interpretation",
    max_tokens: int = 4096,
    documents: list[dict] | None = None,
) -> tuple[dict, str | None]:
    """Make a single Anthropic API call and return (parsed_json, llm_call_id).

    This is the shared entry-point that domain agents use with their
    own prompts.  Raises on missing API key or parse failure.

    ``documents`` accepts Anthropic content blocks (e.g. base64 PDF
    ``{"type": "document", "source": {...}}``) prepended to the user turn,
    for structured extraction from files.
    """
    from app.services.circuit_breaker import anthropic_breaker
    from app.services.secrets import get_api_key

    if not anthropic_breaker.allow_request():
        raise ValueError(
            "Anthropic API is temporarily unavailable (circuit breaker open). "
            "Please try again in a minute."
        )

    api_key = get_api_key("anthropic", "api_key", db)
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY is required for LLM calls")

    import anthropic

    from app.services.models import agent_model

    resolved_model = agent_model(db, override=model)

    today = datetime.date.today().isoformat()
    dated_user_prompt = f"[{today}] {user_prompt}"

    if documents:
        user_content: str | list = [
            *documents,
            {"type": "text", "text": dated_user_prompt},
        ]
    else:
        user_content = dated_user_prompt

    client = anthropic.Anthropic(api_key=api_key)
    llm_call_id = None
    t0 = time.time()

    try:
        response = client.messages.create(
            model=resolved_model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        # First TEXT block, not content[0] — a response can lead with a
        # non-text block, and indexing blindly would read the wrong one.
        raw = next(
            (b.text for b in response.content if getattr(b, "type", None) == "text"),
            "",
        )
        duration_ms = int((time.time() - t0) * 1000)
        parsed = _parse_response(raw)

        _input_tokens = response.usage.input_tokens if response.usage else None
        _output_tokens = response.usage.output_tokens if response.usage else None

        # Persist LLM call record
        if db is not None:
            llm_call_id = _persist_llm_call(
                db,
                thread_id=thread_id,
                call_type=call_type,
                model=resolved_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                raw_response=raw,
                parsed_response=parsed,
                status="success",
                duration_ms=duration_ms,
                input_tokens=_input_tokens,
                output_tokens=_output_tokens,
            )

        anthropic_breaker.record_success()
        return parsed, llm_call_id

    except Exception as exc:
        anthropic_breaker.record_failure()
        duration_ms = int((time.time() - t0) * 1000)
        if db is not None:
            _persist_llm_call(
                db,
                thread_id=thread_id,
                call_type=call_type,
                model=resolved_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                raw_response=None,
                parsed_response=None,
                status="error",
                error_message=str(exc),
                duration_ms=duration_ms,
            )
        raise


def _persist_llm_call(
    db: Session,
    *,
    thread_id,
    call_type,
    model,
    system_prompt,
    user_prompt,
    raw_response,
    parsed_response,
    status,
    error_message=None,
    duration_ms=None,
    tools_provided=None,
    input_tokens=None,
    output_tokens=None,
    user_id=None,
) -> str:
    from app.db.models import LlmCall

    record = LlmCall(
        thread_id=thread_id,
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
            if not user_id and thread_id:
                from app.db.models import Thread

                task = db.query(Thread).filter(Thread.id == thread_id).first()
                if task:
                    user_id = task.user_id
            record_usage(db, user_id, input_tokens, output_tokens)
        except Exception:
            pass  # Don't fail the LLM call if usage tracking fails

    return record.id


# Anthropic builds the cache key over the prompt prefix in a fixed order:
# tools → system → messages. Below a model-dependent minimum a breakpoint is
# silently ignored — 1024 tokens for Sonnet/Opus, 2048 for Haiku. This is set to
# the Sonnet figure because the agent loop is the consumer that matters; a
# marginal segment simply fails to cache on Haiku, which costs nothing because
# an ignored breakpoint is free.
MIN_CACHEABLE_TOKENS = 1024

#: Adaptive thinking for the agent loop. The model decides how much to think
#: per turn; there is no token budget to tune (`budget_tokens` is rejected on
#: Opus 4.7+). `display: "summarized"` returns readable reasoning — the default
#: is "omitted", which still bills for thinking but streams empty text.
#: Depth can be tuned later with output_config={"effort": ...}; the default is
#: "high". Agent path only — the router runs Haiku, which predates adaptive
#: thinking and would reject this.
_THINKING = {"type": "adaptive", "display": "summarized"}


def _cached_tools(tools: list[dict] | None) -> list[dict] | None:
    """`tools` with a cache breakpoint on the last entry.

    Deliberately separate from the system breakpoint. Tool schemas are ~9k
    tokens and change only when an admin edits config, whereas the system
    prompt carries per-turn page context (prompt_builder.py:522). Marking tools
    on their own means a page change costs a system-block miss but still reads
    the tool schemas from cache.

    Verified against the live API at production scale (~10.8k tokens of tools):
    an identical repeat call read all 10,827 tokens from cache, and changing
    the system prompt still read 9,316 from cache while writing only the 1,516
    that actually changed. The same test with a ~2.4k-token tool set showed no
    reuse at all — the benefit only appears once the tools segment clears the
    model's minimum, which production comfortably does.

    Copies rather than mutating — `tools` belongs to the caller and is reused
    across every iteration of the tool loop.
    """
    from app.agents.context_budget import estimate_tokens

    if not tools or estimate_tokens(tools) < MIN_CACHEABLE_TOKENS:
        return tools
    cached = list(tools)
    cached[-1] = {**cached[-1], "cache_control": {"type": "ephemeral"}}
    return cached


def _cached_system(system_prompt: str | None):
    """System prompt as a content block, with a cache breakpoint when it is
    large enough to be worth one."""
    from app.agents.context_budget import estimate_tokens

    if not system_prompt:
        return system_prompt
    block: dict = {"type": "text", "text": system_prompt}
    if estimate_tokens(system_prompt) >= MIN_CACHEABLE_TOKENS:
        block["cache_control"] = {"type": "ephemeral"}
    return [block]


def call_llm_with_tools(
    system_prompt: str,
    messages: list[dict],
    tools: list[dict],
    model: str | None = None,
    db: Session | None = None,
    thread_id: str | None = None,
    call_type: str = "tool_use",
    # 4096 truncated table-heavy reports mid-table (~1.7 chars/token for
    # markdown tables): an 11-invoice audit report needs well over 4k output
    # tokens. Cap, not cost — streaming only pays for tokens actually emitted.
    max_tokens: int = 16384,
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

    from app.services.models import agent_model

    resolved_model = agent_model(db, override=model)

    client = anthropic.Anthropic(api_key=api_key)
    llm_call_id = None
    t0 = time.time()

    # Measured before the send so it is available on the failure path too — an
    # overflow is exactly when "what filled the window?" needs answering, and
    # the exception itself never says.
    from app.agents.context_budget import measure_prompt

    breakdown = measure_prompt(system_prompt, tools, messages)

    try:
        from app.agents.tool_loop import _emit_event

        with client.messages.stream(
            model=resolved_model,
            max_tokens=max_tokens,
            system=_cached_system(system_prompt),
            messages=messages,
            tools=_cached_tools(tools),
            # Adaptive thinking makes reasoning a distinct content-block type,
            # so the UI no longer has to guess which prose is "thinking" and
            # which is the answer. `display` must be set explicitly: the
            # default is "omitted", which streams thinking blocks with empty
            # text and reads as a long silent pause before any output.
            thinking=_THINKING,
        ) as stream:
            # Raw events, not stream.text_stream — text_stream yields only text
            # deltas, so thinking would be dropped on the floor.
            #
            # Answer text streams delta-by-delta (the typewriter wants it that
            # way), but thinking is buffered and emitted once per block: a
            # thinking step is a sentence the user reads, and emitting each
            # delta would fill the strip with fragments like "I" / " need to".
            thinking_buf: list[str] = []

            def _flush_thinking() -> None:
                text = "".join(thinking_buf).strip()
                thinking_buf.clear()
                if text:
                    _emit_event({"type": "thinking", "text": text})

            for event in stream:
                etype = getattr(event, "type", None)
                if etype == "content_block_delta":
                    delta = event.delta
                    kind = getattr(delta, "type", None)
                    if kind == "text_delta":
                        _emit_event({"type": "token", "text": delta.text})
                    elif kind == "thinking_delta":
                        thinking_buf.append(delta.thinking)
                elif etype == "content_block_stop":
                    _flush_thinking()
            # A truncated turn can end without a closing block event.
            _flush_thinking()
            response = stream.get_final_message()
        duration_ms = int((time.time() - t0) * 1000)

        # Serialize for audit logging
        raw_text = json.dumps([block.model_dump() for block in response.content])
        user_prompt_summary = json.dumps(
            messages[-1]["content"][:500]
            if isinstance(messages[-1]["content"], str)
            else "[tool_results]"
        )

        # Extract token usage from response
        _input_tokens = response.usage.input_tokens if response.usage else None
        _output_tokens = response.usage.output_tokens if response.usage else None

        # Reconcile the estimate against truth. The ratio is what tells us
        # whether the budget this drives can be trusted; without it the
        # chars/4 heuristic would be an article of faith.
        breakdown.actual_input_tokens = _input_tokens
        if response.usage:
            breakdown.cache_read_tokens = getattr(
                response.usage, "cache_read_input_tokens", None
            )
            breakdown.cache_write_tokens = getattr(
                response.usage, "cache_creation_input_tokens", None
            )
        logger.info(
            "prompt_size",
            extra={
                "thread_id": thread_id,
                "call_type": call_type,
                "model": resolved_model,
                **breakdown.as_log_fields(),
            },
        )

        if db is not None:
            llm_call_id = _persist_llm_call(
                db,
                thread_id=thread_id,
                call_type=call_type,
                model=resolved_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt_summary,
                raw_response=raw_text,
                parsed_response=_build_parsed_response(response),
                status="success",
                duration_ms=duration_ms,
                tools_provided=tools if tools else None,
                input_tokens=_input_tokens,
                output_tokens=_output_tokens,
            )

        return response, llm_call_id

    except Exception as exc:
        duration_ms = int((time.time() - t0) * 1000)
        # The breakdown is the whole point on this path: an overflow says only
        # "prompt is too long", never which component filled the window.
        logger.warning(
            "prompt_size_on_error",
            extra={
                "thread_id": thread_id,
                "call_type": call_type,
                "model": resolved_model,
                "error": str(exc)[:200],
                **breakdown.as_log_fields(),
            },
        )
        if db is not None:
            _persist_llm_call(
                db,
                thread_id=thread_id,
                call_type=call_type,
                model=resolved_model,
                system_prompt=system_prompt,
                user_prompt="[tool_use call]",
                raw_response=None,
                parsed_response=None,
                status="error",
                error_message=str(exc),
                duration_ms=duration_ms,
            )
        raise


def _parse_response(raw: str) -> dict:
    """Parse JSON from LLM response, handling markdown fences."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    return json.loads(text)
