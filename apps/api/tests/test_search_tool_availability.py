"""The result-search tool is present from the first call, not injected on truncation.

Two problems with injecting it mid-loop on the first `_too_large` result:

1. **Correctness.** Tool results are never persisted as Messages, so a payload
   fetched on an earlier turn is gone from context. context_builder now
   advertises those results by tool_call_id, and the model needs this tool to
   act on the manifest — without it the only way to answer a follow-up about
   data already fetched is to fetch it again.
2. **Caching.** Appending a tool mid-turn changed the tool list between
   iterations, which shifts the prompt-cache prefix and invalidates it for
   every remaining call of that turn.
"""

from app.agents.tool_loop import _ensure_search_tool

SEARCH = "norm__search_tool_result"


def _tools():
    return [
        {"name": "loadedhub__get_sales_data", "description": "d", "input_schema": {}}
    ]


class TestEnsureSearchTool:
    def test_adds_the_search_tool_when_absent(self):
        """...last, after the existing tools in their order, with its tool_meta
        entry registered — without that entry the loop cannot resolve the call
        to a connector/action and the tool would fail when used."""
        tools, meta, available = _ensure_search_tool(_tools(), {})
        assert available is True
        assert [t["name"] for t in tools][-1] == SEARCH
        assert tools[0]["name"] == "loadedhub__get_sales_data"
        assert meta[SEARCH] == {
            "method": "GET",
            "connector": "norm",
            "action": "search_tool_result",
        }

    def test_does_not_duplicate_an_already_present_tool(self):
        existing = [*_tools(), {"name": SEARCH, "description": "d", "input_schema": {}}]
        tools, _meta, available = _ensure_search_tool(existing, {})
        assert available is True
        assert [t["name"] for t in tools].count(SEARCH) == 1

    def test_does_not_mutate_the_callers_list(self):
        """...or the caller's meta. The tool list is built once per turn,
        reused across every iteration, and is now the basis of the prompt-cache
        key. Mutating it in place would leak across calls and shift the cached
        prefix mid-turn."""
        original = _tools()
        snapshot = [dict(t) for t in original]
        meta = {"existing": {"method": "GET"}}
        meta_snapshot = dict(meta)
        _ensure_search_tool(original, meta)
        assert original == snapshot
        assert len(original) == 1
        assert meta == meta_snapshot
        assert SEARCH not in meta


class TestCompactMessages:
    """Shedding tool results to survive a context overflow.

    Overflow used to be a dead end: the loop caught "prompt is too long" and
    told the user to start a new conversation, losing the turn's work. Tool
    results are the dominant term (up to MAX_ITERATIONS of them per turn) and
    the safest to drop, because the payload stays in ToolCall.result_payload
    and remains reachable by tool_use_id.
    """

    def _msgs(self):
        return [
            {"role": "user", "content": "sales?"},
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "tu_1", "name": "get_sales"}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_1",
                        "content": "x" * 50_000,
                    }
                ],
            },
        ]

    def test_tool_results_are_replaced_by_a_stub(self):
        """The stub keeps the id needed to get the data back — without it,
        compaction is data loss rather than a round trip. Plain text turns are
        untouched, and tool_use blocks survive: dropping one would break the
        tool_use/tool_result pairing the API requires."""
        from app.agents.tool_loop import _compact_messages

        out = _compact_messages(self._msgs())
        body = out[2]["content"][0]["content"]
        assert len(body) < 500
        assert "x" * 100 not in body
        assert "tu_1" in body
        assert "norm__search_tool_result" in body
        assert out[0] == {"role": "user", "content": "sales?"}
        assert out[1]["content"][0]["type"] == "tool_use"
        assert out[1]["content"][0]["id"] == "tu_1"

    def test_does_not_mutate_the_input(self):
        from app.agents.tool_loop import _compact_messages

        msgs = self._msgs()
        _compact_messages(msgs)
        assert len(msgs[2]["content"][0]["content"]) == 50_000
