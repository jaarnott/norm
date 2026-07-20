"""Prompt-cache breakpoints on the tool-use call.

Why this exists: nothing in the codebase used `cache_control`, so every request
re-sent the full prefix at full price — measured at ~9k tokens of tool schemas
alone, on top of an operator-authored system prompt of unbounded length. The
tool loop makes up to MAX_ITERATIONS calls per user turn with an identical
system prompt and identical tools, so the same prefix was paid for up to ten
times to answer one question.

The two breakpoints are deliberately separate; see `_cached_tools`.
"""

from app.interpreter.llm_interpreter import (
    MIN_CACHEABLE_TOKENS,
    _cached_system,
    _cached_tools,
)


def _big(n=MIN_CACHEABLE_TOKENS * 8):
    return "x" * n


def _tools(count=2):
    return [
        {"name": f"tool_{i}", "description": _big(), "input_schema": {}}
        for i in range(count)
    ]


class TestToolBreakpoint:
    def test_marks_the_last_tool(self):
        cached = _cached_tools(_tools())
        assert cached[-1]["cache_control"] == {"type": "ephemeral"}

    def test_only_the_last_tool_is_marked(self):
        """One breakpoint caches everything before it; marking each tool would
        burn the limited number of breakpoints for nothing."""
        cached = _cached_tools(_tools(4))
        assert [("cache_control" in t) for t in cached] == [False, False, False, True]

    def test_does_not_mutate_the_callers_list(self):
        """`tools` is built once per turn and passed to every iteration of the
        loop. Mutating it would accumulate cache_control onto the caller's
        objects and leak across calls."""
        original = _tools()
        snapshot = [dict(t) for t in original]
        _cached_tools(original)
        assert original == snapshot
        assert "cache_control" not in original[-1]

    def test_small_tool_sets_are_left_alone(self):
        """Below the minimum a breakpoint is ignored by the API, so adding one
        is noise."""
        tiny = [{"name": "t", "description": "short", "input_schema": {}}]
        assert _cached_tools(tiny) == tiny

    def test_empty_and_none_are_passed_through(self):
        assert _cached_tools(None) is None
        assert _cached_tools([]) == []


class TestSystemBreakpoint:
    def test_large_prompt_becomes_a_cached_block(self):
        blocks = _cached_system(_big())
        assert blocks[0]["type"] == "text"
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}

    def test_small_prompt_is_a_block_without_a_breakpoint(self):
        blocks = _cached_system("be helpful")
        assert blocks[0]["text"] == "be helpful"
        assert "cache_control" not in blocks[0]

    def test_empty_prompt_is_passed_through(self):
        assert _cached_system("") == ""
        assert _cached_system(None) is None


class TestSeparateBreakpoints:
    def test_tools_stay_cacheable_independently_of_the_system_prompt(self):
        """The system prompt carries per-turn page context, so it changes when
        the user navigates. Because the cache key is built tools → system, a
        breakpoint on tools survives that change; a single breakpoint on system
        alone would not."""
        tools_a = _cached_tools(_tools())
        tools_b = _cached_tools(_tools())
        assert tools_a == tools_b  # identical regardless of system prompt

        sys_a = _cached_system(_big() + "page: roster")
        sys_b = _cached_system(_big() + "page: invoices")
        assert sys_a != sys_b
