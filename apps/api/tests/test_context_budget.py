"""Token accounting for the assembled prompt.

Why this exists: every size limit in the codebase was a message count or a
character count applied locally, and nothing measured the prompt that actually
goes to the API. Context overflow was therefore detected by string-matching
"prompt is too long" on the way back out, and the user was told to start a new
conversation. These tests pin the measurement that makes the overflow
preventable — and, more importantly, attributable.
"""

import json

from app.agents.context_budget import (
    CHARS_PER_TOKEN,
    CHARS_PER_TOKEN_JSON,
    PromptBreakdown,
    estimate_tokens,
    measure_prompt,
)


class TestEstimateTokens:
    def test_counts_a_string_by_chars_per_token(self):
        assert estimate_tokens("x" * 400) == 400 // CHARS_PER_TOKEN

    def test_structures_are_counted_denser_than_prose(self):
        """JSON tokenises far denser than prose — every brace, quote and comma
        is its own token. Counting it at the prose rate under-counted the real
        prompt by 45%, which let a 40k history budget run to ~73k real tokens
        before biting."""
        payload = {"rows": [{"product": "Peroni", "revenue": 392.56}]}
        serialised = json.dumps(payload)
        assert estimate_tokens(payload) == int(len(serialised) / CHARS_PER_TOKEN_JSON)
        assert estimate_tokens(payload) > len(serialised) // CHARS_PER_TOKEN

    def test_plain_prose_still_uses_the_prose_rate(self):
        assert estimate_tokens("x" * 400) == 400 // CHARS_PER_TOKEN

    def test_none_is_free(self):
        assert estimate_tokens(None) == 0

    def test_unserialisable_values_do_not_raise(self):
        """A budget that throws is worse than a budget that is slightly wrong."""

        class Opaque:
            pass

        assert estimate_tokens({"x": Opaque()}) > 0


class TestMeasurePrompt:
    def _tool_turn(self, name, result, tool_id="tu_1"):
        return [
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": tool_id, "name": name}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": result,
                    }
                ],
            },
        ]

    def test_splits_system_tools_and_messages(self):
        b = measure_prompt(
            system_prompt="s" * 400,
            tools=[{"name": "t", "description": "d" * 400}],
            messages=[{"role": "user", "content": "u" * 400}],
        )
        assert b.system == 100
        assert b.tools > 100
        assert b.new_message == 100
        assert b.total == b.system + b.tools + b.new_message

    def test_tool_results_are_counted_separately_from_history(self):
        """The dominant term. 'The prompt is 90k tokens' is not actionable;
        '72k of it is tool results' is."""
        messages = [
            {"role": "user", "content": "yesterday's sales"},
            *self._tool_turn("loadedhub__get_sales_for_period", "x" * 4000),
        ]
        b = measure_prompt(messages=messages)
        assert b.tool_results == 1000
        assert b.history < b.tool_results

    def test_largest_results_names_the_offending_tool(self):
        """Attribution is the point — an unnamed 40k blob is not a lead."""
        messages = [
            *self._tool_turn("small_tool", "x" * 400, tool_id="a"),
            *self._tool_turn("loadedhub__get_roster", "x" * 40_000, tool_id="b"),
        ]
        b = measure_prompt(messages=messages)
        assert b.largest_results[0][0] == "loadedhub__get_roster"
        assert b.largest_results[0][1] == 10_000

    def test_summary_is_not_counted_as_history(self):
        """context_builder injects the summary as a plain user turn, so without
        the marker check it would be indistinguishable from real history."""
        messages = [
            {"role": "user", "content": "[Conversation summary]\n" + "s" * 400},
            {"role": "assistant", "content": "Understood."},
            {"role": "user", "content": "and now?"},
        ]
        b = measure_prompt(messages=messages)
        assert b.summary > 0
        assert b.history > 0
        assert not b.new_message or b.new_message < b.summary

    def test_blocks_persisted_as_a_json_string_are_still_measured(self):
        """Message.content is a string column, so blocks round-trip as JSON.
        Measuring them as one opaque string would hide the tool results."""
        blocks = json.dumps(
            [{"type": "tool_result", "tool_use_id": "a", "content": "x" * 4000}]
        )
        b = measure_prompt(messages=[{"role": "user", "content": blocks}])
        assert b.tool_results == 1000

    def test_empty_prompt_is_zero(self):
        assert measure_prompt().total == 0


class TestReconciliation:
    def test_ratio_is_none_until_reconciled(self):
        assert PromptBreakdown(system=100).estimate_error is None

    def test_ratio_reports_estimate_against_truth(self):
        """The heuristic must be checkable, not an article of faith."""
        b = PromptBreakdown(system=900)
        b.actual_input_tokens = 1000
        assert b.estimate_error == 0.9

    def test_log_fields_are_flat_and_include_truth_when_known(self):
        b = PromptBreakdown(system=10, tools=20, tool_results=30)
        b.actual_input_tokens = 100
        fields = b.as_log_fields()
        assert fields["ctx_total"] == 60
        assert fields["ctx_actual"] == 100
        assert all(not isinstance(v, dict) for v in fields.values())
