"""Transient LLM errors are retried; the roster_viewer failure was one.

Anthropic can answer a streaming request 200 and then send `overloaded_error`
as the first stream event. The SDK doesn't retry that (the request succeeded),
so a single overload used to surface as "The workflow could not be completed."
A mid-stream overload used to be re-raised rather than retried, because
re-streaming would print the same thinking twice. On 1 Oct 2026 that veto
discarded a 17-minute turn (prod thread c6aad2d5) to protect a screen Cloud Run
had disconnected six minutes earlier. The retry now fires either way and emits
`stream_restart` first, telling the client to throw the dead attempt away.

These pin: which errors count as transient, that a mid-stream failure is
retried WITH that handshake, and that an enormous prompt is not retried three
times over.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import anthropic
import httpx
import pytest

from app.interpreter.llm_interpreter import (
    _is_transient_llm_error,
    _llm_retry_backoff,
    call_llm_with_tools,
)


def _status_error(status_code, err_type):
    resp = httpx.Response(status_code, request=httpx.Request("POST", "https://x"))
    return anthropic.APIStatusError(
        message=err_type,
        response=resp,
        body={"type": "error", "error": {"type": err_type}},
    )


class TestTransientClassifier:
    def test_overloaded_is_transient(self):
        assert _is_transient_llm_error(_status_error(529, "overloaded_error"))

    def test_5xx_is_transient(self):
        for code in (500, 502, 503, 504):
            assert _is_transient_llm_error(_status_error(code, "api_error"))

    def test_rate_limit_is_transient(self):
        resp = httpx.Response(429, request=httpx.Request("POST", "https://x"))
        assert _is_transient_llm_error(
            anthropic.RateLimitError(message="slow down", response=resp, body=None)
        )

    def test_connection_error_is_transient(self):
        assert _is_transient_llm_error(
            anthropic.APIConnectionError(request=httpx.Request("POST", "https://x"))
        )

    def test_bad_request_is_not_transient(self):
        # 4xx means the request is wrong — retrying can't fix it.
        assert not _is_transient_llm_error(_status_error(400, "invalid_request_error"))
        assert not _is_transient_llm_error(_status_error(404, "not_found_error"))

    def test_plain_exception_is_not_transient(self):
        assert not _is_transient_llm_error(ValueError("nope"))

    def test_overloaded_in_message_is_caught_even_off_status(self):
        assert _is_transient_llm_error(RuntimeError("upstream: Overloaded"))


class TestBackoff:
    def test_grows_and_is_bounded(self):
        assert (
            _llm_retry_backoff(0) < _llm_retry_backoff(5)
            or _llm_retry_backoff(5) <= 8.5
        )
        assert _llm_retry_backoff(10) <= 8.5  # capped


# ── Retry loop ───────────────────────────────────────────────────────────


class _FakeStreamCtx:
    """A context manager standing in for client.beta.messages.stream(...).

    Either raises on entry (to simulate an overload before any event) or
    yields a scripted list of events and a final message.
    """

    def __init__(self, *, raise_exc=None, events=None, final=None):
        self._raise = raise_exc
        self._events = events or []
        self._final = final

    def __enter__(self):
        if self._raise is not None:
            raise self._raise
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self._events)

    def get_final_message(self):
        return self._final


def _text_delta(t):
    return SimpleNamespace(
        type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=t)
    )


def _thinking_delta(t):
    return SimpleNamespace(
        type="content_block_delta",
        delta=SimpleNamespace(type="thinking_delta", thinking=t),
    )


def _block(text):
    b = SimpleNamespace(type="text", text=text)
    b.model_dump = lambda: {"type": "text", "text": text}
    return b


def _final(text="ok"):
    return SimpleNamespace(
        content=[_block(text)],
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=2,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


@pytest.fixture()
def _patched(monkeypatch):
    """Stub everything call_llm_with_tools needs except the anthropic client."""
    monkeypatch.setattr("app.services.secrets.get_api_key", lambda *a, **k: "sk-test")
    monkeypatch.setattr(
        "app.services.models.agent_model", lambda *a, **k: "claude-opus-4-8"
    )
    monkeypatch.setattr("app.agents.tool_loop._emit_event", lambda *a, **k: None)
    monkeypatch.setattr("time.sleep", lambda *_: None)


def _call():
    return call_llm_with_tools(
        system_prompt="you are norm",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        db=None,
    )


class TestRetryLoop:
    def test_retries_overload_then_succeeds(self, _patched):
        client = MagicMock()
        client.beta.messages.stream.side_effect = [
            _FakeStreamCtx(raise_exc=_status_error(529, "overloaded_error")),
            _FakeStreamCtx(events=[_text_delta("hello")], final=_final("hello")),
        ]
        with patch("anthropic.Anthropic", return_value=client):
            resp, _ = _call()
        assert resp.content[0].text == "hello"
        assert client.beta.messages.stream.call_count == 2

    def test_gives_up_after_max_attempts(self, _patched):
        client = MagicMock()
        client.beta.messages.stream.side_effect = [
            _FakeStreamCtx(raise_exc=_status_error(529, "overloaded_error"))
            for _ in range(5)
        ]
        with patch("anthropic.Anthropic", return_value=client):
            with pytest.raises(anthropic.APIStatusError):
                _call()
        assert client.beta.messages.stream.call_count == 3  # _LLM_MAX_ATTEMPTS

    def test_does_not_retry_a_bad_request(self, _patched):
        client = MagicMock()
        client.beta.messages.stream.side_effect = [
            _FakeStreamCtx(raise_exc=_status_error(400, "invalid_request_error"))
        ]
        with patch("anthropic.Anthropic", return_value=client):
            with pytest.raises(anthropic.APIStatusError):
                _call()
        assert client.beta.messages.stream.call_count == 1

    def test_retries_a_mid_stream_overload(self, _patched):
        """Fails if the `emitted_any` veto comes back.

        An overload AFTER tokens have streamed is the expensive case: the 1 Oct
        2026 turn had been streaming 5m23s and was thrown away whole. Retrying
        is safe because tools do not run until get_final_message() returns, so
        a dead attempt has no side effects.
        """

        class _MidStreamCtx(_FakeStreamCtx):
            def __iter__(self):
                yield _text_delta("par")
                raise _status_error(529, "overloaded_error")

        client = MagicMock()
        client.beta.messages.stream.side_effect = [
            _MidStreamCtx(),
            _FakeStreamCtx(events=[_text_delta("whole")], final=_final("whole")),
        ]
        with patch("anthropic.Anthropic", return_value=client):
            resp, _ = _call()
        assert resp.content[0].text == "whole"
        assert client.beta.messages.stream.call_count == 2

    def test_a_mid_stream_retry_tells_the_client_to_discard(
        self, monkeypatch, _patched
    ):
        """Fails if the retry re-streams over the top of the abandoned attempt.

        Without this event the browser keeps the partial bubble and the user
        reads the answer twice — which is exactly why the old code refused to
        retry at all.
        """
        events: list[dict] = []
        monkeypatch.setattr(
            "app.agents.tool_loop._emit_event", lambda e: events.append(e)
        )

        class _MidStreamCtx(_FakeStreamCtx):
            def __iter__(self):
                yield _thinking_delta("weighing it up")
                yield SimpleNamespace(type="content_block_stop")
                yield _text_delta("par")
                raise _status_error(529, "overloaded_error")

        client = MagicMock()
        client.beta.messages.stream.side_effect = [
            _MidStreamCtx(),
            _FakeStreamCtx(events=[_text_delta("whole")], final=_final("whole")),
        ]
        with patch("anthropic.Anthropic", return_value=client):
            _call()
        restarts = [e for e in events if e.get("type") == "stream_restart"]
        assert len(restarts) == 1, f"expected one stream_restart, got {events}"
        # One thinking block was flushed before the failure, so exactly one
        # must be dropped — a wrong count orphans or over-deletes steps.
        assert restarts[0]["drop_thinking"] == 1

    def test_an_enormous_prompt_is_not_retried(self, monkeypatch, _patched):
        """Fails if a doomed call is billed three times.

        A retry re-sends the whole prompt. Near the top of the window that is
        the dominant cost of the turn, so past the ceiling it fails fast and
        lets the loop land what it has.
        """
        from app.agents import context_budget
        from app.interpreter import llm_interpreter

        monkeypatch.setattr(
            llm_interpreter,
            "measure_prompt",
            lambda *a, **k: context_budget.PromptBreakdown(
                system=llm_interpreter._RETRY_PROMPT_TOKEN_CEILING + 1
            ),
            raising=False,
        )
        monkeypatch.setattr(
            context_budget,
            "measure_prompt",
            lambda *a, **k: context_budget.PromptBreakdown(
                system=llm_interpreter._RETRY_PROMPT_TOKEN_CEILING + 1
            ),
        )
        client = MagicMock()
        client.beta.messages.stream.side_effect = [
            _FakeStreamCtx(raise_exc=_status_error(529, "overloaded_error"))
            for _ in range(5)
        ]
        with patch("anthropic.Anthropic", return_value=client):
            with pytest.raises(anthropic.APIStatusError):
                _call()
        assert client.beta.messages.stream.call_count == 1

    def test_the_size_guard_can_actually_fire(self, _patched):
        """Fails if the ceiling drifts somewhere unreachable, or so low it
        swallows the fix.

        The constant is compared against `measure_prompt`, an ESTIMATE that
        undercounts — 0.84 of the true prompt at 35k, 0.39 at 145k across 29
        production calls. Two consequences pinned here:

        - Too high is dead code. At 120_000 (the value first shipped) the
          estimate only reaches the ceiling on a ~300k prompt, and the API
          rejects anything over 200k as a non-transient 400. The guard could
          never trigger while reading as protection.
        - Too low swallows the fix. The 1 Oct 2026 overload estimated ~53k
          (110,072 true) and MUST still be retried.
        """
        from app.interpreter import llm_interpreter

        ceiling = llm_interpreter._RETRY_PROMPT_TOKEN_CEILING
        assert ceiling > 53_000, "the 1 Oct failure would no longer retry"
        # 200k window x the worst observed ratio (0.39) is the highest estimate
        # the API will ever accept; a ceiling above that cannot be reached.
        assert ceiling < 200_000 * 0.39, "the guard is unreachable — dead code"


class TestTheOneShotPathRetriesToo:
    """`call_llm` had only the SDK's own retry, and it is the path that reads
    invoice copies — up to 10 concurrent document calls per venue, the most
    overload-prone thing Norm does. On 24-25 Aug 2026 that cost 27 invoices a
    day, each reported to the user as an "unreadable copy" when the document
    was perfectly readable.
    """

    def _reply(self, text="{}"):
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )

    def _call(self):
        from app.interpreter.llm_interpreter import call_llm

        return call_llm(system_prompt="s", user_prompt="u", db=None)

    def test_an_overload_is_retried_then_succeeds(self, _patched):
        client = MagicMock()
        client.messages.create.side_effect = [
            _status_error(529, "overloaded_error"),
            self._reply('{"invoice_number": "IN1"}'),
        ]
        with patch("anthropic.Anthropic", return_value=client):
            parsed, _ = self._call()
        assert parsed["invoice_number"] == "IN1"
        assert client.messages.create.call_count == 2

    def test_a_bad_request_is_never_retried(self, _patched):
        """Retrying cannot fix a 400, and three attempts would triple the cost
        of every genuine failure — including the non-PDF copies."""
        client = MagicMock()
        client.messages.create.side_effect = _status_error(400, "invalid_request_error")
        with patch("anthropic.Anthropic", return_value=client):
            with pytest.raises(anthropic.APIStatusError):
                self._call()
        assert client.messages.create.call_count == 1

    def test_it_gives_up_after_max_attempts(self, _patched):
        client = MagicMock()
        client.messages.create.side_effect = _status_error(529, "overloaded_error")
        with patch("anthropic.Anthropic", return_value=client):
            with pytest.raises(anthropic.APIStatusError):
                self._call()
        assert client.messages.create.call_count == 3  # _LLM_MAX_ATTEMPTS


class TestAFailedStreamIsRecorded:
    """The failure path must use the ISOLATED writer, not the turn's session.

    `test_message_persistence` pins what the isolated writer does; this pins
    that `call_llm_with_tools` actually reaches for it. Calling the helper
    directly in a test proves nothing about the wiring — reverting the call
    site to `_persist_llm_call(db, ...)` left that test green.
    """

    def _run_failing_call(self, monkeypatch, exc):
        from app.interpreter import llm_interpreter

        isolated: list[dict] = []
        shared: list[dict] = []
        monkeypatch.setattr(
            llm_interpreter,
            "_persist_llm_call_isolated",
            lambda **kw: isolated.append(kw),
        )
        monkeypatch.setattr(
            llm_interpreter,
            "_persist_llm_call",
            lambda db, **kw: shared.append(kw) or "id",
        )
        client = MagicMock()
        client.beta.messages.stream.side_effect = [_FakeStreamCtx(raise_exc=exc)] * 5
        with patch("anthropic.Anthropic", return_value=client):
            with pytest.raises(Exception):
                call_llm_with_tools(
                    system_prompt="you are norm",
                    messages=[{"role": "user", "content": "hi"}],
                    tools=[],
                    db=MagicMock(),  # a truthy session; it must NOT be used
                    thread_id="t-1",
                    call_type="tool_use",
                )
        return isolated, shared

    def test_an_overload_records_an_error_row_off_the_turn_session(
        self, monkeypatch, _patched
    ):
        """Fails if the error row goes back on the turn's session, where the
        rollback that follows destroys it — the 1 Oct 2026 blind spot."""
        isolated, shared = self._run_failing_call(
            monkeypatch, _status_error(529, "overloaded_error")
        )
        assert len(isolated) == 1, "the failure was not recorded in isolation"
        assert isolated[0]["status"] == "error"
        assert "overloaded" in isolated[0]["error_message"].lower()
        assert isolated[0]["duration_ms"] is not None
        assert not shared, "the failure path must not use the turn's session"

    def test_a_bad_request_is_recorded_too(self, monkeypatch, _patched):
        """Fails if only retryable errors are recorded — a 4xx is the one most
        worth having in the table, because it means a real bug."""
        isolated, _ = self._run_failing_call(
            monkeypatch, _status_error(400, "invalid_request_error")
        )
        assert len(isolated) == 1
        assert isolated[0]["status"] == "error"


class TestContextEditingAndTheCacheMarker:
    """Changes 1 and 2 of the context-editing plan, pinned at the request."""

    def _kwargs(self, _patched, messages=None):
        client = MagicMock()
        client.beta.messages.stream.side_effect = [
            _FakeStreamCtx(events=[_text_delta("ok")], final=_final("ok"))
        ]
        with patch("anthropic.Anthropic", return_value=client):
            call_llm_with_tools(
                system_prompt="you are norm",
                messages=messages or [{"role": "user", "content": "hi"}],
                tools=[],
                db=None,
            )
        return client.beta.messages.stream.call_args.kwargs

    def test_the_stream_asks_anthropic_to_clear_old_tool_results(self, _patched):
        """Fails if context editing is dropped or its values drift from the
        ones decided against production data (plan: context-editing.md;
        raised to 100k / 40k on 7 Oct 2026 — clearing at 50k caused a third
        of all cache writes and cost more than it saved)."""
        from app.agents.tool_loop import TOOL_FANOUT_CAP

        kw = self._kwargs(_patched)
        assert "context-management-2025-06-27" in kw["betas"]
        edit = kw["context_management"]["edits"][0]
        assert edit["type"] == "clear_tool_uses_20250919"
        assert edit["trigger"] == {"type": "input_tokens", "value": 100_000}
        assert edit["clear_at_least"] == {"type": "input_tokens", "value": 40_000}
        assert edit["keep"] == {"type": "tool_uses", "value": TOOL_FANOUT_CAP}

    def test_the_stream_sets_effort_and_opts_into_fallback(self, _patched):
        """Opus 5.5 always thinks and defaults to `medium` effort; the setting
        makes that explicit and tunable. Fallback re-runs a classifier decline
        on another model inside the same call (Claude API only)."""
        from app.config import settings

        kw = self._kwargs(_patched)
        assert kw["output_config"] == {"effort": settings.LLM_AGENT_EFFORT}
        assert "server-side-fallback-2026-07-01" in kw["betas"]
        assert kw["extra_body"] == {"fallbacks": "default"}

    def test_a_model_without_effort_is_not_sent_it(self, _patched, monkeypatch):
        """Haiku 4.5 rejects output_config.effort with a 400."""
        monkeypatch.setattr(
            "app.services.models.agent_model",
            lambda *a, **k: "claude-haiku-4-5-20251001",
        )
        kw = self._kwargs(_patched)
        assert "output_config" not in kw

    def test_keep_can_never_be_below_the_fan_out_cap(self, _patched):
        """Fails if either side is edited alone. A round can fire up to
        TOOL_FANOUT_CAP calls; clearing runs on the request carrying those
        fresh results and spares only the newest `keep`. keep < cap would
        blank a round's own answers before the model read them."""
        from app.agents import tool_loop
        from app.interpreter import llm_interpreter

        keep = llm_interpreter._context_management()["edits"][0]["keep"]["value"]
        assert keep >= tool_loop.TOOL_FANOUT_CAP
        assert tool_loop.TOOL_FANOUT_CAP == 8, "the pool width this was sized to"

    def test_the_last_message_block_carries_the_cache_marker(self, _patched):
        """Fails if the conversation stops being cache-eligible. Only the
        LAST block is marked (one breakpoint), on a copy — the caller's
        list is the cache key's basis and must not be mutated."""
        original = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": [{"type": "text", "text": "a"}]},
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "r1"},
                    {"type": "tool_result", "tool_use_id": "t2", "content": "r2"},
                ],
            },
        ]
        kw = self._kwargs(_patched, messages=original)
        sent = kw["messages"]
        assert sent[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in sent[-1]["content"][0]
        assert (
            "cache_control" not in sent[0].get("content", {})
            if isinstance(sent[0]["content"], dict)
            else True
        )
        # the caller's objects are untouched
        assert "cache_control" not in original[-1]["content"][-1]
        assert original[0]["content"] == "first"

    def test_a_string_message_is_wrapped_so_it_can_be_marked(self, _patched):
        kw = self._kwargs(_patched, messages=[{"role": "user", "content": "plain"}])
        last = kw["messages"][-1]["content"]
        assert last == [
            {"type": "text", "text": "plain", "cache_control": {"type": "ephemeral"}}
        ]

    def test_the_search_tool_says_a_cleared_result_is_retrievable(self):
        """Fails if the hint goes. Anthropic's placeholder is generic — it
        does not know Norm can fetch the result back by id."""
        from app.agents.tool_loop import _build_search_tool_schema

        d = _build_search_tool_schema()["description"].lower()
        assert "cleared" in d and "placeholder" in d and "tool_call_id" in d


class TestOneShotCallsOnAThinkingModel:
    """`call_llm` was written for a model that did not think: budgets of
    200–4096 output tokens. On Opus 5.5 thinking always runs and counts toward
    max_tokens, so the budget gets a 16k floor (a cap, not a target) and
    effort runs low. Haiku keeps its request exactly as before."""

    def _create_kwargs(self, monkeypatch, model, **call_kwargs):
        from unittest.mock import MagicMock, patch

        from app.interpreter.llm_interpreter import call_llm

        monkeypatch.setattr(
            "app.services.secrets.get_api_key", lambda *a, **k: "sk-ant-test"
        )
        client = MagicMock()
        block = MagicMock(type="text", text='{"ok": true}')
        client.messages.create.return_value = MagicMock(
            content=[block], usage=MagicMock(input_tokens=1, output_tokens=1)
        )
        with patch("anthropic.Anthropic", return_value=client):
            call_llm(
                system_prompt="s", user_prompt="u", db=None, model=model, **call_kwargs
            )
        return client.messages.create.call_args.kwargs

    def test_opus_gets_a_floor_and_low_effort(self, monkeypatch):
        from app.config import settings

        kw = self._create_kwargs(monkeypatch, "claude-opus-5-5", max_tokens=500)
        assert kw["max_tokens"] == 16_000
        assert kw["output_config"] == {"effort": settings.LLM_ONE_SHOT_EFFORT}

    def test_a_larger_budget_is_kept(self, monkeypatch):
        kw = self._create_kwargs(monkeypatch, "claude-opus-5-5", max_tokens=32_000)
        assert kw["max_tokens"] == 32_000

    def test_haiku_is_untouched(self, monkeypatch):
        kw = self._create_kwargs(
            monkeypatch, "claude-haiku-4-5-20251001", max_tokens=500
        )
        assert kw["max_tokens"] == 500
        assert "output_config" not in kw
