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

    # Explicit timeout on these one-shot calls (classification, summaries) so a
    # stalled request can't hang.
    client = anthropic.Anthropic(api_key=api_key, timeout=120.0)
    llm_call_id = None
    t0 = time.time()

    try:
        # Retry transient failures, the same way call_llm_with_tools does. This
        # path had only the SDK's own retry, which does not cover Anthropic
        # answering 200 and then failing — and this is the path that reads
        # invoice copies, up to 10 concurrent document calls per venue, the most
        # overload-prone thing Norm does. On 24-25 Aug 2026 that cost 27 invoices
        # a day, each reported to the user as an "unreadable copy" when the
        # document was perfectly readable.
        for attempt in range(_LLM_MAX_ATTEMPTS):
            try:
                response = client.messages.create(
                    model=resolved_model,
                    max_tokens=max_tokens,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_content}],
                )
                break
            except Exception as call_exc:
                # A 4xx is never retried: retrying cannot fix a bad request, and
                # three attempts would triple the cost of every genuine failure.
                if attempt < _LLM_MAX_ATTEMPTS - 1 and _is_transient_llm_error(
                    call_exc
                ):
                    logger.warning(
                        "llm_transient_retry",
                        extra={
                            "thread_id": thread_id,
                            "call_type": call_type,
                            "attempt": attempt + 1,
                            "error": str(call_exc)[:160],
                        },
                    )
                    time.sleep(_llm_retry_backoff(attempt))
                    continue
                raise
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
            _persist_llm_call_isolated(
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


def _persist_llm_call_isolated(**kwargs) -> None:
    """Record a FAILED llm call on its own session, so it outlives the turn.

    `_persist_llm_call` only add()s and flush()es — correct on the success path,
    where the turn commits moments later. On the failure path the exception
    propagates to the stream worker, the turn's session closes without a commit,
    and the row is rolled back with it. The effect is that the most interesting
    calls are the ones that leave no trace: the 1 Oct 2026 Service Foods turn
    (thread c6aad2d5) burned a 5m23s call and its tokens on an `overloaded_error`
    and `llm_calls` held nothing at all, so the incident could only be
    reconstructed from Cloud Run logs.

    Same reasoning as `routers.messages._persist_failed_turn`: a fresh session,
    because the turn's own may already be poisoned by the error being handled.
    Telemetry must never be the thing that breaks a turn, so every failure here
    is swallowed and logged.
    """
    from app.db.engine import SessionLocal

    session = SessionLocal()
    try:
        _persist_llm_call(session, **kwargs)
        session.commit()
    except Exception:  # noqa: BLE001 — never mask the original error
        logger.warning("could not persist failed llm_call", exc_info=True)
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
    finally:
        session.close()


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


# How many times to (re)issue a streaming LLM call before giving up. Small: a
# retry only helps a genuinely transient blip, and a persistent overload should
# surface quickly rather than stall a 90s workflow behind three backoffs.
_LLM_MAX_ATTEMPTS = 3

# ── Anthropic context editing: old tool results are cleared server-side ──
#
# A long turn's requests were growing without bound: tool results pile up
# inside one turn and every step re-sends all of them (87 kB by step 4 of the
# 1 Oct 2026 tender turn; 145k tokens at the top). Norm's own clearing
# (`tool_loop._compact_messages`) ran only after the API had already refused a
# prompt, and then cleared everything. This asks Anthropic to do it on the way
# in: once a request passes the trigger, the oldest tool results are replaced
# with a placeholder in the copy the model reads and is billed for. Norm's own
# message list is untouched; the full results stay in ToolCall.result_payload
# and norm__search_tool_result retrieves any of them by id.
#
# Values decided 3 Oct 2026 against 30 days of production, documented in
# ~/.claude/plans/context-editing.md. Their defaults are 100k / keep 3 / none;
# keep 3 assumes one tool call per step and would blank most of a Norm round's
# own fresh answers.
_CONTEXT_EDITING_BETA = "context-management-2025-06-27"
#: Below this the request is left alone; a small job never clears at all.
_CLEAR_TRIGGER_INPUT_TOKENS = 50_000
#: A clear changes the cached prefix and costs one full-price step, so it must
#: free at least this much to be worth doing — their docs' own advice.
_CLEAR_AT_LEAST_INPUT_TOKENS = 10_000


def _context_management() -> dict:
    """The clearing config for one call. `keep` is the fan-out cap, by
    construction — see tool_loop.TOOL_FANOUT_CAP for why they must agree."""
    from app.agents.tool_loop import TOOL_FANOUT_CAP

    return {
        "edits": [
            {
                "type": "clear_tool_uses_20250919",
                "trigger": {
                    "type": "input_tokens",
                    "value": _CLEAR_TRIGGER_INPUT_TOKENS,
                },
                "keep": {"type": "tool_uses", "value": TOOL_FANOUT_CAP},
                "clear_at_least": {
                    "type": "input_tokens",
                    "value": _CLEAR_AT_LEAST_INPUT_TOKENS,
                },
            }
        ]
    }


def _cached_messages(messages: list[dict]) -> list[dict]:
    """A copy of ``messages`` with one cache breakpoint on the last block.

    The cache discount applies to the part of a request identical, from the
    top, to the previous one. Tools and system already carry breakpoints; the
    conversation did not, so within a turn every step re-sent the whole
    growing history at full price (30% of the 1 Oct tender turn's 2.4M prompt
    tokens came from cache — the fixed ~25k, never the part that grew). Marking
    the newest message makes "everything up to the last step" eligible: step
    N+1 is step N plus a bit. Third of the four breakpoints allowed.

    Never mutates the caller's list — it is reused across iterations and is
    the basis of the cache key. A last block that is not a plain dict (an SDK
    object) is left alone rather than guessed at.
    """
    if not messages:
        return messages
    last = messages[-1]
    content = last.get("content") if isinstance(last, dict) else None
    if isinstance(content, str):
        blocks: list = [{"type": "text", "text": content}]
    elif isinstance(content, list) and content and isinstance(content[-1], dict):
        blocks = [*content[:-1], dict(content[-1])]
    else:
        return messages
    blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
    return [*messages[:-1], {**last, "content": blocks}]


#: Above this prompt size a transient failure is NOT retried. Retrying
#: re-sends the whole prompt, so near the top of a 200k window three attempts
#: cost three times over on a call that was already marginal, and failing fast
#: hands the turn back to the loop to land what it has.
#:
#: MEASURED IN ESTIMATE-SPACE, which is nowhere near tokens. `measure_prompt`
#: is a chars/4 heuristic that undercounts, and worse on bigger prompts:
#: against the TRUE prompt (input + cache_read + cache_creation) its ratio ran
#: 0.84 on a 35k prompt down to 0.39 on a 145k one across 29 production calls
#: on thread c6aad2d5. So 70,000 estimated is somewhere between ~83k and ~180k
#: real, and sits just above the largest estimate ever observed (64,087, on a
#: 145,476-token prompt): "bigger than anything we have seen".
#:
#: The usable band is narrow — above ~53,000 or the 1 Oct failure stops being
#: retried, below ~78,000 (200k window x the worst ratio) or the estimate can
#: never reach it. 80,000 was tried first and was still unreachable.
#:
#: It was 120_000 on first write, which could NEVER fire: at the ~0.4 ratio up
#: there the estimate only reaches 120k on a ~300k prompt, and the API rejects
#: anything past 200k as too long — a non-transient 400 that is not retried
#: anyway. A guard that cannot trigger is worse than none, because it reads as
#: protection.
#:
#: The 1 Oct 2026 failure estimated ~53k (110,072 true), so it retries, which
#: is the point of the fix. Do not compare this against an actual-token figure
#: without dividing by the ratio first.
_RETRY_PROMPT_TOKEN_CEILING = 70_000


def _is_transient_llm_error(exc: Exception) -> bool:
    """True for LLM errors worth retrying — a passing infrastructure blip, not
    a bad request.

    The motivating case: Anthropic answers a *streaming* request with HTTP 200
    and then sends ``overloaded_error`` as the first stream event. The SDK's
    own retries don't cover that (the request already succeeded), so without
    this a single overload surfaced as a hard "workflow could not be completed"
    — the roster_viewer failure in the demo, which was the API being busy, not
    a bug in Norm.

    Retryable: connection/timeout, rate limits, 5xx, 529 overloaded. NOT
    retryable: 4xx (bad request, auth, not found) — retrying can't fix those.
    """
    import anthropic

    if isinstance(
        exc,
        (
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
            anthropic.RateLimitError,
            anthropic.InternalServerError,
        ),
    ):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        if getattr(exc, "status_code", None) in (500, 502, 503, 504, 529):
            return True
        body = getattr(exc, "body", None)
        etype = (
            (body or {}).get("error", {}).get("type")
            if isinstance(body, dict)
            else None
        )
        if etype in ("overloaded_error", "api_error", "rate_limit_error"):
            return True
    # Last resort: the overloaded signal is unmistakable in the message even
    # when the status code isn't one of the above.
    return "overloaded" in str(exc).lower()


def _llm_retry_backoff(attempt: int) -> float:
    """Seconds to wait before retrying attempt ``attempt`` (0-based).

    Exponential with jitter, so a fleet of concurrent workflows hitting the
    same overload don't retry in lockstep.
    """
    import random

    return min(8.0, 0.6 * (2**attempt)) + random.uniform(0, 0.4)


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
    # tokens. 16384 then did the same once adaptive thinking shared the budget:
    # a group-wide "top 50 items" turn spent it all reasoning over 30 results
    # and was cut off mid-table (prod thread fa1cfd1c, 23 Sep 2026). Cap, not
    # cost — streaming only pays for tokens actually emitted. 64000 is
    # Anthropic's recommendation for streamed requests (Opus 4.8 allows 128K);
    # a reply that still hits it carries tool_loop.TRUNCATION_NOTE.
    max_tokens: int = 64000,
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

    # Explicit per-call timeout, and no SDK auto-retry: retries are handled
    # manually below (and only before any token has streamed, so a mid-stream
    # blip is re-raised rather than silently doubled). Bounding one stalled model
    # iteration here keeps a single wedged call from riding out the whole request.
    client = anthropic.Anthropic(api_key=api_key, timeout=600.0, max_retries=0)
    llm_call_id = None
    t0 = time.time()

    # Measured before the send so it is available on the failure path too — an
    # overflow is exactly when "what filled the window?" needs answering, and
    # the exception itself never says.
    from app.agents.context_budget import measure_prompt

    breakdown = measure_prompt(system_prompt, tools, messages)

    try:
        from app.agents.tool_loop import _emit_event

        # Answer text streams delta-by-delta (the typewriter wants it that way),
        # but thinking is buffered and emitted once per block: a thinking step
        # is a sentence the user reads, and emitting each delta would fill the
        # strip with fragments like "I" / " need to".
        thinking_buf: list[str] = []

        # Mutable so the nested flusher can bump the counter the retry below
        # reports in `stream_restart` — the client drops exactly this many.
        emitted = {"thinking": 0}

        def _flush_thinking() -> None:
            text = "".join(thinking_buf).strip()
            thinking_buf.clear()
            if text:
                _emit_event({"type": "thinking", "text": text})
                emitted["thinking"] += 1

        # Retry a transient stream failure, including one that lands AFTER
        # tokens have streamed.
        #
        # This used to be vetoed by `not emitted_any`, because re-streaming
        # would print the same thinking twice. Sound rule, wrong remedy: on
        # 1 Oct 2026 an `overloaded_error` arrived 5m23s into a stream and the
        # veto discarded a 17-minute turn (thread c6aad2d5) to protect a screen
        # Cloud Run had disconnected six minutes earlier. Instead of declining
        # to retry, tell the client to throw away what the dead attempt wrote:
        # `stream_restart` carries how many thinking steps to drop, and the
        # browser clears the partial bubble. If nobody is attached the event
        # goes nowhere and the retry is free either way.
        #
        # Safe to re-issue because tools do not run until after
        # `stream.get_final_message()` — a failed attempt has no side effects.
        response = None
        applied_edits: list = []
        for attempt in range(_LLM_MAX_ATTEMPTS):
            emitted_any = False
            emitted["thinking"] = 0
            thinking_buf.clear()
            try:
                with client.beta.messages.stream(
                    model=resolved_model,
                    max_tokens=max_tokens,
                    system=_cached_system(system_prompt),
                    messages=_cached_messages(messages),
                    tools=_cached_tools(tools),
                    betas=[_CONTEXT_EDITING_BETA],
                    context_management=_context_management(),
                    # Adaptive thinking makes reasoning a distinct content-block
                    # type, so the UI no longer has to guess which prose is
                    # "thinking" and which is the answer. `display` must be set
                    # explicitly: the default is "omitted", which streams
                    # thinking blocks with empty text and reads as a long silent
                    # pause before any output.
                    thinking=_THINKING,
                ) as stream:
                    # Raw events, not stream.text_stream — text_stream yields
                    # only text deltas, so thinking would be dropped on the
                    # floor.
                    for event in stream:
                        etype = getattr(event, "type", None)
                        if etype == "content_block_delta":
                            delta = event.delta
                            kind = getattr(delta, "type", None)
                            if kind == "text_delta":
                                _emit_event({"type": "token", "text": delta.text})
                                emitted_any = True
                            elif kind == "thinking_delta":
                                thinking_buf.append(delta.thinking)
                                emitted_any = True
                        elif etype == "content_block_stop":
                            _flush_thinking()
                    # A truncated turn can end without a closing block event.
                    _flush_thinking()
                    response = stream.get_final_message()
                cm = getattr(response, "context_management", None)
                applied_edits = list(getattr(cm, "applied_edits", None) or [])
                break
            except Exception as stream_exc:
                # A retry re-sends the whole prompt. At the top of the window
                # that is the dominant cost of the turn, and a prompt that
                # large is itself a reason the call struggled — so a doomed
                # call is not billed three times. `breakdown` is measured
                # before the send precisely so it is available here.
                too_big_to_retry = breakdown.total >= _RETRY_PROMPT_TOKEN_CEILING
                if (
                    attempt < _LLM_MAX_ATTEMPTS - 1
                    and not too_big_to_retry
                    and _is_transient_llm_error(stream_exc)
                ):
                    logger.warning(
                        "llm_transient_retry",
                        extra={
                            "thread_id": thread_id,
                            "attempt": attempt + 1,
                            "emitted": emitted_any,
                            "drop_thinking": emitted["thinking"],
                            "error": str(stream_exc)[:160],
                        },
                    )
                    # Tell the client to discard the dead attempt's output
                    # before anything is re-streamed over the top of it.
                    if emitted_any:
                        _emit_event(
                            {
                                "type": "stream_restart",
                                "drop_thinking": emitted["thinking"],
                            }
                        )
                    time.sleep(_llm_retry_backoff(attempt))
                    continue
                if too_big_to_retry and _is_transient_llm_error(stream_exc):
                    logger.warning(
                        "llm_transient_not_retried_prompt_too_large",
                        extra={
                            "thread_id": thread_id,
                            "estimated_tokens": breakdown.total,
                        },
                    )
                raise
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
                # What Anthropic cleared on the way in. Zero on most calls;
                # the tuning evidence for the trigger / keep / clear_at_least.
                "ctx_cleared_tool_uses": sum(
                    int(getattr(e, "cleared_tool_uses", 0) or 0) for e in applied_edits
                ),
                "ctx_cleared_tokens": sum(
                    int(getattr(e, "cleared_input_tokens", 0) or 0)
                    for e in applied_edits
                ),
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
            _persist_llm_call_isolated(
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
                # Deliberately no input_tokens: a non-None value makes
                # _persist_llm_call call record_usage, which would bill an
                # ESTIMATE as measured usage against the org's quota. The real
                # figure never arrives on this path. The estimate is already in
                # the prompt_size_on_error log above, which is the right place
                # for a guess.
            )
        raise


def _parse_response(raw: str) -> dict:
    """Parse JSON from LLM response, handling markdown fences and prose.

    Models occasionally think aloud before the JSON ('"Sailor Jerry Spiced
    Rum" — same brand as index 203 … {"matches": …}') — observed live on the
    stock-item matcher, 08 Aug 2026, where the strict parse silently degraded
    a correct match to "NEW item". Strict parse first (unchanged semantics);
    on failure, decode the first JSON CONTAINER found in the text — a leading
    quoted phrase must not satisfy the parse as a bare string.
    """
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                obj, _ = decoder.raw_decode(text, i)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, (dict, list)):
                return obj
    raise json.JSONDecodeError("no JSON object found in response", text, 0)
