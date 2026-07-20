"""Answer text vs reasoning, now that thinking is a real content-block type.

Norm used to tell the model to prefix tool calls with "[Tool] " and had the
browser sniff that prefix to decide what was reasoning and what was the reply.
Opus 4.8 emits `thinking` blocks, so the split is structural. These pin the
behaviours that convention used to fake — and the bugs it hid.
"""

from types import SimpleNamespace

from app.agents.tool_loop import _extract_text, _join_answer, _serialize_block


def _resp(*blocks):
    return SimpleNamespace(content=list(blocks))


def _text(t):
    return SimpleNamespace(type="text", text=t)


def _thinking(t, sig="sig-abc"):
    return SimpleNamespace(type="thinking", thinking=t, signature=sig)


class TestExtractText:
    def test_joins_every_text_block(self):
        assert _extract_text(_resp(_text("one"), _text("two"))) == "one\ntwo"

    def test_no_text_returns_empty_not_done(self):
        # It used to return "Done." — which reached users as a literal answer
        # and made every `if reasoning:` guard downstream always true.
        assert _extract_text(_resp()) == ""
        assert _extract_text(_resp(SimpleNamespace(type="tool_use"))) == ""

    def test_thinking_is_not_answer_text(self):
        # Reasoning is surfaced separately as `thinking` events; leaking it
        # into the reply is exactly what the old heuristics tried to prevent.
        resp = _resp(_thinking("let me check the invoices"), _text("4 invoices."))
        assert _extract_text(resp) == "4 invoices."


class TestJoinAnswer:
    def test_keeps_text_from_every_iteration(self):
        # The core "we only show the last response" bug: the model writes the
        # substance before a tool call and a short line after it.
        got = _join_answer(
            ["Here are the totals for July:"], "Let me know if you need more."
        )
        assert "Here are the totals for July:" in got
        assert "Let me know if you need more." in got

    def test_preserves_order(self):
        assert _join_answer(["first", "second"], "third").splitlines()[0] == "first"

    def test_does_not_repeat_text_the_model_restated(self):
        assert _join_answer(["same"], "same") == "same"

    def test_ignores_blank_parts(self):
        assert _join_answer(["", "  "], "only") == "only"

    def test_all_empty_is_empty(self):
        assert _join_answer([], "") == ""


class TestSerializeBlock:
    def test_thinking_round_trips_with_its_signature(self):
        # The API rejects a modified thinking block, so replay must be exact.
        out = _serialize_block(_thinking("weighing options", sig="sig-xyz"))
        assert out == {
            "type": "thinking",
            "thinking": "weighing options",
            "signature": "sig-xyz",
        }

    def test_redacted_thinking_round_trips(self):
        block = SimpleNamespace(type="redacted_thinking", data="encrypted-payload")
        assert _serialize_block(block) == {
            "type": "redacted_thinking",
            "data": "encrypted-payload",
        }

    def test_text_and_tool_use_still_serialize(self):
        assert _serialize_block(_text("hi")) == {"type": "text", "text": "hi"}
        tu = SimpleNamespace(type="tool_use", id="tu1", name="get", input={"a": 1})
        assert _serialize_block(tu) == {
            "type": "tool_use",
            "id": "tu1",
            "name": "get",
            "input": {"a": 1},
        }


class TestFinalSummaryContract:
    """The max-iterations summary must survive.

    `call_llm_with_tools` returns `(response, llm_call_id)`. The summary call
    site took the tuple whole, so `_extract_text` hit `.content` on a tuple,
    raised AttributeError, and a bare `except Exception` swallowed it —
    replacing the summary the user had just watched stream in with boilerplate.
    """

    def test_extract_text_cannot_read_an_unpacked_tuple(self):
        # Why the unpack matters: this is the failure the bare except hid.
        import pytest

        with pytest.raises(AttributeError):
            _extract_text((_resp(_text("the real summary")), "llm-call-id"))

    def test_the_summary_call_site_unpacks(self):
        # Guards the specific line that regressed, without booting the loop.
        import inspect

        from app.agents import tool_loop

        src = inspect.getsource(tool_loop._execute_loop)
        assert "final_response, _ = call_llm_with_tools(" in src, (
            "the max-iterations summary must unpack (response, llm_call_id)"
        )

    def test_boilerplate_is_reserved_for_real_api_failures(self):
        # A bare `except Exception` turned coding errors into that boilerplate.
        import inspect

        from app.agents import tool_loop

        src = inspect.getsource(tool_loop._execute_loop)
        assert "except (anthropic.APIError, ValueError, RuntimeError):" in src
