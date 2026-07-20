"""Transient LLM errors are retried; the roster_viewer failure was one.

Anthropic can answer a streaming request 200 and then send `overloaded_error`
as the first stream event. The SDK doesn't retry that (the request succeeded),
so a single overload used to surface as "The workflow could not be completed."
These pin: which errors count as transient, and that the stream is re-issued
only while nothing has been emitted (retrying after tokens would double them).
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
    """A context manager standing in for client.messages.stream(...).

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
        client.messages.stream.side_effect = [
            _FakeStreamCtx(raise_exc=_status_error(529, "overloaded_error")),
            _FakeStreamCtx(events=[_text_delta("hello")], final=_final("hello")),
        ]
        with patch("anthropic.Anthropic", return_value=client):
            resp, _ = _call()
        assert resp.content[0].text == "hello"
        assert client.messages.stream.call_count == 2

    def test_gives_up_after_max_attempts(self, _patched):
        client = MagicMock()
        client.messages.stream.side_effect = [
            _FakeStreamCtx(raise_exc=_status_error(529, "overloaded_error"))
            for _ in range(5)
        ]
        with patch("anthropic.Anthropic", return_value=client):
            with pytest.raises(anthropic.APIStatusError):
                _call()
        assert client.messages.stream.call_count == 3  # _LLM_MAX_ATTEMPTS

    def test_does_not_retry_a_bad_request(self, _patched):
        client = MagicMock()
        client.messages.stream.side_effect = [
            _FakeStreamCtx(raise_exc=_status_error(400, "invalid_request_error"))
        ]
        with patch("anthropic.Anthropic", return_value=client):
            with pytest.raises(anthropic.APIStatusError):
                _call()
        assert client.messages.stream.call_count == 1

    def test_does_not_retry_after_tokens_emitted(self, _patched):
        # An overload mid-stream, AFTER a token was delivered: retrying would
        # double the output, so it must propagate instead.
        class _MidStreamCtx(_FakeStreamCtx):
            def __iter__(self):
                yield _text_delta("par")
                raise _status_error(529, "overloaded_error")

        client = MagicMock()
        client.messages.stream.side_effect = [_MidStreamCtx()]
        with patch("anthropic.Anthropic", return_value=client):
            with pytest.raises(anthropic.APIStatusError):
                _call()
        assert client.messages.stream.call_count == 1
