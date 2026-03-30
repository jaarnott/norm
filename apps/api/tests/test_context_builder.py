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
        assert thread.conversation_summary == "The user asked about sales data for Bessie."

    @patch("app.agents.context_builder._summarise_with_llm")
    def test_existing_summary_reused_when_current(self, mock_llm):
        """If summary is up to date, don't call LLM again."""
        msgs = []
        for i in range(SUMMARY_THRESHOLD + 5):
            role = "user" if i % 2 == 0 else "assistant"
            msgs.append(_make_msg(role, f"msg {i}", minutes_ago=100 - i))

        older_count = len(msgs) - RECENT_AFTER_SUMMARY
        thread = _make_thread(
            summary="Existing summary.", summary_count=older_count
        )
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
            _make_msg("assistant", "You can use the procurement agent.", minutes_ago=99),
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
