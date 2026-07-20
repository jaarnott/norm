"""Tests for the unified conversation context builder."""

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.agents.context_builder import (
    SUMMARY_THRESHOLD,
    RECENT_AFTER_SUMMARY,
    build_conversation_messages,
    _summarise_older_messages,
    _format_messages_for_summary,
    _ensure_alternation,
    _get_or_create_summary,
)


def _make_msg(role: str, content: str, minutes_ago: int = 0) -> MagicMock:
    """Create a mock Message ORM object."""
    msg = MagicMock()
    msg.role = role
    msg.content = content
    msg.created_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc) - timedelta(
        minutes=minutes_ago
    )
    return msg


def _make_thread(summary=None, summary_count=None):
    """Create a mock Thread ORM object."""
    thread = MagicMock()
    thread.conversation_summary = summary
    thread.summary_through_count = summary_count
    return thread


class TestBuildConversationMessages:
    def test_empty_history(self):
        result = build_conversation_messages([], "hello")
        assert len(result) == 1
        assert result[0] == {"role": "user", "content": "hello"}

    def test_short_history_included_in_full(self):
        msgs = [
            _make_msg("user", "first question", minutes_ago=10),
            _make_msg("assistant", "first answer", minutes_ago=9),
        ]
        result = build_conversation_messages(msgs, "follow up")
        assert len(result) == 3
        assert result[0]["content"] == "first question"
        assert result[1]["content"] == "first answer"
        assert result[2]["content"] == "follow up"

    def test_no_summarisation_below_threshold(self):
        """Under SUMMARY_THRESHOLD messages, all are included — no summary."""
        msgs = []
        for i in range(SUMMARY_THRESHOLD - 1):
            role = "user" if i % 2 == 0 else "assistant"
            msgs.append(_make_msg(role, f"msg {i}", minutes_ago=100 - i))
        result = build_conversation_messages(msgs, "new")
        # Should have all messages + new message, no summary pair
        assert not any("summary" in m["content"].lower() for m in result[:2])

    def test_summarisation_above_threshold_without_thread(self):
        """Above threshold but no thread → deterministic fallback summary."""
        msgs = []
        for i in range(SUMMARY_THRESHOLD + 5):
            role = "user" if i % 2 == 0 else "assistant"
            msgs.append(_make_msg(role, f"msg {i}", minutes_ago=100 - i))
        result = build_conversation_messages(msgs, "new")
        # First message should be the deterministic summary
        assert "Earlier conversation" in result[0]["content"]

    @patch("app.agents.context_builder._summarise_with_llm")
    def test_llm_summarisation_with_thread(self, mock_llm):
        """Above threshold with thread + db → LLM summary."""
        mock_llm.return_value = "The user asked about sales data for Bessie."
        thread = _make_thread()
        db = MagicMock()

        msgs = []
        for i in range(SUMMARY_THRESHOLD + 5):
            role = "user" if i % 2 == 0 else "assistant"
            msgs.append(_make_msg(role, f"msg {i}", minutes_ago=100 - i))

        result = build_conversation_messages(msgs, "new", thread=thread, db=db)

        mock_llm.assert_called_once()
        assert "Conversation summary" in result[0]["content"]
        assert "sales data for Bessie" in result[0]["content"]
        # Thread should have been updated
        assert (
            thread.conversation_summary == "The user asked about sales data for Bessie."
        )

    @patch("app.agents.context_builder._summarise_with_llm")
    def test_existing_summary_reused_when_current(self, mock_llm):
        """If summary is up to date, don't call LLM again."""
        msgs = []
        for i in range(SUMMARY_THRESHOLD + 5):
            role = "user" if i % 2 == 0 else "assistant"
            msgs.append(_make_msg(role, f"msg {i}", minutes_ago=100 - i))

        older_count = len(msgs) - RECENT_AFTER_SUMMARY
        thread = _make_thread(summary="Existing summary.", summary_count=older_count)
        db = MagicMock()

        result = build_conversation_messages(msgs, "new", thread=thread, db=db)

        mock_llm.assert_not_called()
        assert "Existing summary." in result[0]["content"]

    @patch("app.agents.context_builder._summarise_with_llm")
    def test_incremental_update_when_summary_stale(self, mock_llm):
        """If summary exists but is stale, do incremental update."""
        mock_llm.return_value = "Updated summary with new info."

        msgs = []
        for i in range(SUMMARY_THRESHOLD + 10):
            role = "user" if i % 2 == 0 else "assistant"
            msgs.append(_make_msg(role, f"msg {i}", minutes_ago=100 - i))

        # Summary covers first 10 messages, but now there are 20 older messages
        thread = _make_thread(summary="Old summary.", summary_count=10)
        db = MagicMock()

        result = build_conversation_messages(msgs, "new", thread=thread, db=db)

        mock_llm.assert_called_once()
        # Should have been called with existing_summary
        call_kwargs = mock_llm.call_args
        assert call_kwargs[1].get("existing_summary") == "Old summary."

    @patch("app.agents.context_builder._summarise_with_llm")
    def test_fallback_on_llm_failure(self, mock_llm):
        """If LLM summarisation fails, fall back to deterministic summary."""
        mock_llm.side_effect = Exception("API error")
        thread = _make_thread()
        db = MagicMock()

        msgs = []
        for i in range(SUMMARY_THRESHOLD + 5):
            role = "user" if i % 2 == 0 else "assistant"
            msgs.append(_make_msg(role, f"msg {i}", minutes_ago=100 - i))

        result = build_conversation_messages(msgs, "new", thread=thread, db=db)

        # Should fall back to deterministic summary
        assert "Earlier conversation" in result[0]["content"]

    def test_context_injected_into_new_message(self):
        result = build_conversation_messages(
            [],
            "what's the weather",
            context={"location": "Auckland", "open_task": {"id": "skip_me"}},
        )
        assert len(result) == 1
        assert "what's the weather" in result[0]["content"]
        assert "[Context]" in result[0]["content"]
        assert "LOCATION" in result[0]["content"]
        assert "skip_me" not in result[0]["content"]

    def test_context_none_values_skipped(self):
        result = build_conversation_messages(
            [], "test", context={"empty": None, "present": "value"}
        )
        assert "EMPTY" not in result[0]["content"]
        assert "PRESENT" in result[0]["content"]

    def test_messages_sorted_chronologically(self):
        msgs = [
            _make_msg("assistant", "second", minutes_ago=5),
            _make_msg("user", "first", minutes_ago=10),
        ]
        result = build_conversation_messages(msgs, "third")
        assert result[0]["content"] == "first"
        assert result[1]["content"] == "second"
        assert result[2]["content"] == "third"

    def test_recent_messages_count(self):
        """Above threshold, exactly RECENT_AFTER_SUMMARY messages are kept in full."""
        msgs = []
        for i in range(SUMMARY_THRESHOLD + 10):
            role = "user" if i % 2 == 0 else "assistant"
            msgs.append(_make_msg(role, f"msg {i}", minutes_ago=100 - i))

        result = build_conversation_messages(msgs, "new")
        # Summary pair (2) + RECENT_AFTER_SUMMARY messages + new message (1)
        # Alternation merging may reduce count slightly
        assert len(result) >= RECENT_AFTER_SUMMARY


class TestFormatMessagesForSummary:
    def test_plain_text_messages(self):
        msgs = [
            _make_msg("user", "hello"),
            _make_msg("assistant", "hi there"),
        ]
        result = _format_messages_for_summary(msgs)
        assert "User: hello" in result
        assert "Assistant: hi there" in result

    def test_tool_use_blocks(self):
        content = '[{"type": "tool_use", "name": "get_sales", "id": "123", "input": {"venue": "Bessie"}}]'
        msg = _make_msg("assistant", content)
        result = _format_messages_for_summary([msg])
        assert "[Called tool: get_sales]" in result

    def test_tool_result_truncated(self):
        long_result = "x" * 1000
        content = f'[{{"type": "tool_result", "content": "{long_result}"}}]'
        msg = _make_msg("user", content)
        result = _format_messages_for_summary([msg])
        assert "[truncated]" in result

    def test_long_plain_text_truncated(self):
        msg = _make_msg("user", "y" * 1000)
        result = _format_messages_for_summary([msg])
        assert "[truncated]" in result
        assert len(result) < 1200


class TestSummariseOlderMessages:
    def test_basic_summary(self):
        msgs = [
            _make_msg("user", "How do I order tomatoes?", minutes_ago=100),
            _make_msg(
                "assistant", "You can use the procurement agent.", minutes_ago=99
            ),
            _make_msg("user", "What about lettuce?", minutes_ago=98),
            _make_msg("assistant", "Same process.", minutes_ago=97),
        ]
        summary = _summarise_older_messages(msgs)
        assert "4 messages" in summary
        assert "Topics discussed:" in summary
        assert "How do I order tomatoes?" in summary
        assert "What about lettuce?" in summary

    def test_long_messages_truncated(self):
        long_content = "x" * 200
        msgs = [_make_msg("user", long_content, minutes_ago=10)]
        summary = _summarise_older_messages(msgs)
        assert "..." in summary
        assert len(summary) < 300

    def test_max_10_bullets(self):
        msgs = []
        for i in range(20):
            role = "user" if i % 2 == 0 else "assistant"
            msgs.append(_make_msg(role, f"topic {i}", minutes_ago=20 - i))
        summary = _summarise_older_messages(msgs)
        bullet_count = summary.count("- topic")
        assert bullet_count == 10


class TestEnsureAlternation:
    def test_already_alternating(self):
        msgs = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ]
        result = _ensure_alternation(msgs)
        assert result == msgs

    def test_consecutive_same_role_merged(self):
        msgs = [
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
            {"role": "assistant", "content": "c"},
        ]
        result = _ensure_alternation(msgs)
        assert len(result) == 2
        assert result[0]["content"] == "a\n\nb"
        assert result[1]["content"] == "c"

    def test_empty_list(self):
        assert _ensure_alternation([]) == []

    def test_single_message(self):
        msgs = [{"role": "user", "content": "hello"}]
        assert _ensure_alternation(msgs) == msgs


class TestUserMessageNotDuplicated:
    """The caller persists the user's message before invoking the loop.

    base.py adds the user Message row and flushes it, then calls run_tool_loop
    with the same text; _build_messages reads task.messages (which now contains
    that row) and appended new_message a second time. _ensure_alternation then
    merged the two consecutive user turns, so the model received the user's
    words twice, concatenated — costing tokens and misrepresenting the turn.
    """

    def test_persisted_copy_of_the_new_message_is_not_sent_twice(self):
        msgs = [
            _make_msg("user", "what were sales yesterday?", minutes_ago=10),
            _make_msg("assistant", "La Zeppa did $9,434.", minutes_ago=9),
            # base.py has already written this row:
            _make_msg("user", "and the week before?", minutes_ago=0),
        ]
        result = build_conversation_messages(msgs, "and the week before?")
        assert result[-1]["content"].count("and the week before?") == 1

    def test_context_block_still_attaches_to_the_deduplicated_turn(self):
        """The persisted row has no [Context]; the appended one does. Dropping
        the wrong copy would silently lose the context block."""
        msgs = [_make_msg("user", "sales?", minutes_ago=0)]
        result = build_conversation_messages(
            msgs, "sales?", context={"venue": "La Zeppa"}
        )
        assert result[-1]["content"].count("sales?") == 1
        assert "[Context]" in result[-1]["content"]
        assert "La Zeppa" in result[-1]["content"]

    def test_a_genuinely_repeated_message_is_preserved(self):
        """Answering 'yes' twice is real. Only the just-persisted copy is
        dropped — the earlier identical turn must survive."""
        msgs = [
            _make_msg("user", "yes", minutes_ago=10),
            _make_msg("assistant", "Which venue?", minutes_ago=9),
            _make_msg("user", "yes", minutes_ago=0),
        ]
        result = build_conversation_messages(msgs, "yes")
        user_turns = [m for m in result if m["role"] == "user"]
        assert len(user_turns) == 2

    def test_unpersisted_callers_are_unaffected(self):
        """task_scheduler routes the prompt through a temp task, so the row is
        not in `messages`. The new message must still be appended."""
        msgs = [_make_msg("assistant", "Ready.", minutes_ago=1)]
        result = build_conversation_messages(msgs, "run the weekly report")
        assert result[-1]["content"] == "run the weekly report"


def _make_tool_call(tc_id, tool_name, params, payload, connector="loadedhub"):
    tc = MagicMock()
    tc.id = tc_id
    tc.tool_name = tool_name
    tc.input_params = params
    tc.result_payload = payload
    tc.connector_name = connector
    tc.status = "executed"
    return tc


def _db_returning(rows):
    db = MagicMock()
    chain = db.query.return_value.filter.return_value.order_by.return_value.limit
    chain.return_value.all.return_value = rows
    return db


class TestToolResultManifest:
    """Advertising data fetched in earlier turns.

    Tool results are never persisted as Messages — they live in the loop's
    in-memory list for one turn and then vanish. A follow-up question about
    data Norm just fetched therefore forced a re-fetch of the same rows. The
    payloads are durable in ToolCall.result_payload and already addressable,
    because ToolCall.id IS the Anthropic tool_use block id that
    norm__search_tool_result takes. The manifest is a pointer, not the data.
    """

    def test_lists_retrievable_results_with_their_ids(self):
        from app.agents.context_builder import build_tool_result_manifest

        rows = [
            _make_tool_call(
                "tu_01ABC",
                "loadedhub__get_sales_for_period",
                {"period": "yesterday", "venue": "La Zeppa"},
                {"window": {}, "data": [{"x": 1}, {"x": 2}]},
            )
        ]
        out = build_tool_result_manifest(MagicMock(), _db_returning(rows))
        assert "tu_01ABC" in out
        assert "loadedhub__get_sales_for_period" in out
        assert "period=yesterday" in out
        assert "2 rows" in out
        assert "norm__search_tool_result" in out

    def test_unwraps_the_consolidator_envelope_for_the_row_count(self):
        """*_for_period tools return {window, data}. Counting the envelope
        would report '2 rows' for every one of them."""
        from app.agents.context_builder import build_tool_result_manifest

        rows = [
            _make_tool_call(
                "t1", "x", {}, {"window": {"start": "..."}, "data": [1, 2, 3, 4, 5]}
            )
        ]
        assert "5 rows" in build_tool_result_manifest(MagicMock(), _db_returning(rows))

    def test_results_with_no_payload_are_not_advertised(self):
        """Advertising an id that search_tool_result cannot resolve would send
        the model to fetch nothing."""
        from app.agents.context_builder import build_tool_result_manifest

        rows = [_make_tool_call("t1", "x", {}, None)]
        assert build_tool_result_manifest(MagicMock(), _db_returning(rows)) is None

    def test_plumbing_args_are_not_shown(self):
        """venue_id and mode are injected by the executor, not chosen by the
        model — echoing them back is noise it has to read every turn."""
        from app.agents.context_builder import build_tool_result_manifest

        rows = [
            _make_tool_call(
                "t1", "x", {"venue_id": "uuid-here", "mode": "autopilot", "period": "yesterday"}, [1]
            )
        ]
        out = build_tool_result_manifest(MagicMock(), _db_returning(rows))
        assert "uuid-here" not in out
        assert "autopilot" not in out
        assert "period=yesterday" in out

    def test_no_tool_calls_yields_nothing(self):
        from app.agents.context_builder import build_tool_result_manifest

        assert build_tool_result_manifest(MagicMock(), _db_returning([])) is None

    def test_missing_thread_or_db_is_safe(self):
        from app.agents.context_builder import build_tool_result_manifest

        assert build_tool_result_manifest(None, MagicMock()) is None
        assert build_tool_result_manifest(MagicMock(), None) is None

    def test_a_db_failure_never_breaks_the_conversation(self):
        """The manifest is an optimisation. If the query fails the turn must
        still go through, just without the pointers."""
        from app.agents.context_builder import build_tool_result_manifest

        db = MagicMock()
        db.query.side_effect = RuntimeError("connection lost")
        assert build_tool_result_manifest(MagicMock(), db) is None

    def test_manifest_reaches_the_final_user_turn(self):
        rows = [_make_tool_call("tu_9", "loadedhub__get_roster", {}, [1, 2])]
        msgs = [_make_msg("user", "the roster?", minutes_ago=5)]
        result = build_conversation_messages(
            msgs, "and yesterday?", thread=MagicMock(), db=_db_returning(rows)
        )
        assert "tu_9" in result[-1]["content"]
        assert result[-1]["role"] == "user"


class TestTokenTriggeredCompaction:
    """History is split by tokens as well as message count.

    The count rule alone left a hole: a thread of ten messages each carrying a
    pasted report never reached SUMMARY_THRESHOLD, so nothing was compacted,
    the whole thing was sent verbatim every turn, and the conversation
    eventually died on "prompt is too long" with no compaction ever attempted.
    """

    def test_short_cheap_threads_are_untouched(self):
        from app.agents.context_builder import _split_history

        msgs = [_make_msg("user", "hi", minutes_ago=i) for i in range(4)]
        older, recent = _split_history(msgs)
        assert older == []
        assert len(recent) == 4

    def test_many_small_messages_still_trigger_on_count(self):
        from app.agents.context_builder import (
            RECENT_AFTER_SUMMARY,
            SUMMARY_THRESHOLD,
            _split_history,
        )

        msgs = [
            _make_msg("user", "hi", minutes_ago=100 - i)
            for i in range(SUMMARY_THRESHOLD + 5)
        ]
        older, recent = _split_history(msgs)
        assert older
        assert len(recent) == RECENT_AFTER_SUMMARY

    def test_few_enormous_messages_now_trigger_on_tokens(self):
        """The case the count rule missed entirely."""
        from app.agents.context_builder import MAX_HISTORY_TOKENS, _split_history

        huge = "x" * (MAX_HISTORY_TOKENS * 4)  # ~MAX_HISTORY_TOKENS tokens each
        msgs = [_make_msg("user", huge, minutes_ago=5 - i) for i in range(5)]
        older, recent = _split_history(msgs)
        assert older, "5 huge messages must compact even though count <= threshold"
        assert len(recent) < 5

    def test_at_least_one_message_always_survives(self):
        """Summarising the message we are about to answer is self-defeating."""
        from app.agents.context_builder import MAX_HISTORY_TOKENS, _split_history

        colossal = "x" * (MAX_HISTORY_TOKENS * 40)
        msgs = [_make_msg("user", colossal, minutes_ago=i) for i in range(3)]
        _older, recent = _split_history(msgs)
        assert len(recent) >= 1

    def test_recent_messages_stay_in_chronological_order(self):
        from app.agents.context_builder import SUMMARY_THRESHOLD, _split_history

        msgs = [
            _make_msg("user", f"m{i}", minutes_ago=100 - i)
            for i in range(SUMMARY_THRESHOLD + 3)
        ]
        _older, recent = _split_history(msgs)
        assert [m.content for m in recent] == sorted(
            [m.content for m in recent], key=lambda c: int(c[1:])
        )

    def test_empty_history_is_safe(self):
        from app.agents.context_builder import _split_history

        assert _split_history([]) == ([], [])


class TestSummaryPreservesRetrievalIds:
    def test_the_prompt_demands_verbatim_tool_call_ids(self):
        """Payloads are not persisted as Messages, so a tool_call_id is the
        only route back to the data once a turn is compacted. Losing the ids
        would turn compaction into permanent data loss."""
        from app.agents.context_builder import _SUMMARY_SYSTEM_PROMPT

        assert "tool_call_id" in _SUMMARY_SYSTEM_PROMPT
        assert "verbatim" in _SUMMARY_SYSTEM_PROMPT

    def test_summariser_input_carries_the_id_alongside_the_result(self):
        import json as _json

        from app.agents.context_builder import _format_messages_for_summary

        msg = _make_msg(
            "user",
            _json.dumps(
                [{"type": "tool_result", "tool_use_id": "tu_42", "content": "rows"}]
            ),
        )
        assert "tu_42" in _format_messages_for_summary([msg])
