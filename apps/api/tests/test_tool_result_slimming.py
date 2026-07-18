"""Per-tool result-size budget for the LLM context (`max_result_chars`).

Audit-style tools (invoice review/reconciliation) return reports the agent
must relay in full; a tool may raise its slim threshold via `max_result_chars`
on the tool definition, clamped to HARD_MAX_TOOL_RESULT_CHARS.
"""

import json

from app.agents.tool_loop import (
    HARD_MAX_TOOL_RESULT_CHARS,
    MAX_TOOL_RESULT_CHARS,
    _slim_tool_result,
    _tool_max_result_chars,
)


class TestToolMaxResultChars:
    def test_no_tool_def_uses_default(self):
        assert _tool_max_result_chars(None) == MAX_TOOL_RESULT_CHARS

    def test_absent_key_uses_default(self):
        assert _tool_max_result_chars({"action": "x"}) == MAX_TOOL_RESULT_CHARS

    def test_override_honoured(self):
        assert _tool_max_result_chars({"max_result_chars": 100_000}) == 100_000

    def test_clamped_to_hard_ceiling(self):
        assert (
            _tool_max_result_chars({"max_result_chars": 10_000_000})
            == HARD_MAX_TOOL_RESULT_CHARS
        )

    def test_never_below_default(self):
        assert _tool_max_result_chars({"max_result_chars": 5}) == MAX_TOOL_RESULT_CHARS

    def test_garbage_value_uses_default(self):
        assert (
            _tool_max_result_chars({"max_result_chars": "lots"})
            == MAX_TOOL_RESULT_CHARS
        )
        assert _tool_max_result_chars({"max_result_chars": None}) == (
            MAX_TOOL_RESULT_CHARS
        )


class TestSlimRespectsBudget:
    def test_result_within_raised_budget_passes_through_verbatim(self):
        # ~60k chars: over the 30k default, under a 100k override
        payload = {"data": [{"i": i, "pad": "x" * 50} for i in range(1000)]}
        assert len(json.dumps(payload)) > MAX_TOOL_RESULT_CHARS
        out = _slim_tool_result(payload, "tc-1", max_chars=100_000)
        assert json.loads(out) == payload

    def test_result_over_raised_budget_still_slims(self):
        payload = {"data": [{"i": i, "pad": "x" * 200} for i in range(1000)]}
        out = _slim_tool_result(payload, "tc-1", max_chars=100_000)
        assert '"_too_large": true' in out
