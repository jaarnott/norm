"""Unified conversation context builder.

Builds the Anthropic messages list for both normal threads and automated task
runs. When conversations exceed SUMMARY_THRESHOLD messages, uses an LLM
(Haiku) to generate a concise summary of older messages. The summary is stored
on the Thread record and updated incrementally as the conversation grows.
"""

import json
import logging

logger = logging.getLogger(__name__)

SUMMARY_THRESHOLD = 20  # Trigger LLM summarisation after this many messages
RECENT_AFTER_SUMMARY = 10  # Keep this many recent messages in full after summary
SUMMARY_MODEL = "claude-haiku-4-5-20251001"

_SUMMARY_SYSTEM_PROMPT = (
    "You are a conversation summariser for a hospitality operations platform. "
    "Produce a concise summary focusing on: key decisions made, data retrieved "
    "(include specific numbers/dates), user instructions, and any outstanding "
    "questions or pending actions. Keep it under 500 words. Write in past tense, "
    "third person."
)


def build_conversation_messages(
    messages: list,
    new_message: str,
    context: dict | None = None,
    thread=None,
    db=None,
) -> list[dict]:
    """Build Anthropic messages list with capped history + LLM summary of older messages.

    Args:
        messages: Message ORM objects (must have .role, .content, .created_at).
        new_message: The new user message to append.
        context: Optional dict of domain context injected into the new message.
        thread: Optional Thread ORM object for persisting conversation summaries.
        db: Optional DB session (needed for LLM summarisation calls).

    Returns:
        List of {role, content} dicts ready for the Anthropic API.
    """
    sorted_msgs = sorted(messages, key=lambda m: m.created_at)

    result: list[dict] = []

    if len(sorted_msgs) > SUMMARY_THRESHOLD:
        older = sorted_msgs[:-RECENT_AFTER_SUMMARY]
        recent = sorted_msgs[-RECENT_AFTER_SUMMARY:]

        summary = _get_or_create_summary(older, thread, db)
        result.append({"role": "user", "content": summary})
        result.append(
            {
                "role": "assistant",
                "content": "Understood, I have context from the earlier conversation.",
            }
        )
    else:
        recent = sorted_msgs

    for msg in recent:
        result.append({"role": msg.role, "content": msg.content})

    # Append the new user message with optional context
    content = new_message
    if context:
        context_parts = []
        for key, value in context.items():
            if key == "open_task":
                continue
            if value:
                label = key.upper().replace("_", " ")
                context_parts.append(f"{label}: {json.dumps(value)}")
        if context_parts:
            content = new_message + "\n\n[Context]\n" + "\n".join(context_parts)

    result.append({"role": "user", "content": content})

    # Ensure valid alternation — merge consecutive same-role messages
    result = _ensure_alternation(result)

    return result


def _get_or_create_summary(older_messages: list, thread, db) -> str:
    """Get existing summary or generate a new one. Falls back to deterministic summary."""
    if thread is None or db is None:
        return _summarise_older_messages(older_messages)

    older_count = len(older_messages)
    existing_summary = thread.conversation_summary
    summarised_count = thread.summary_through_count or 0

    # Summary is up to date — reuse it
    if existing_summary and summarised_count >= older_count:
        return f"[Conversation summary]\n{existing_summary}"

    # Need to summarise — either first time or incremental update
    try:
        if existing_summary and summarised_count > 0:
            # Incremental: only summarise new messages since last summary
            new_messages = older_messages[summarised_count:]
            summary = _summarise_with_llm(
                new_messages, db, existing_summary=existing_summary
            )
        else:
            # First time: summarise all older messages
            summary = _summarise_with_llm(older_messages, db)

        # Persist the summary on the thread
        thread.conversation_summary = summary
        thread.summary_through_count = older_count
        db.flush()

        return f"[Conversation summary]\n{summary}"
    except Exception:
        logger.exception("LLM summarisation failed, using deterministic fallback")
        return _summarise_older_messages(older_messages)


def _summarise_with_llm(messages: list, db, existing_summary: str | None = None) -> str:
    """Call Haiku to summarise conversation messages."""
    from app.interpreter.llm_interpreter import call_llm

    formatted = _format_messages_for_summary(messages)

    if existing_summary:
        user_prompt = (
            f"Existing summary:\n{existing_summary}\n\n"
            f"New messages to incorporate:\n{formatted}\n\n"
            "Update the summary to include the key points from the new messages."
        )
    else:
        user_prompt = (
            f"Conversation messages:\n{formatted}\n\nSummarise this conversation."
        )

    parsed, _ = call_llm(
        system_prompt=_SUMMARY_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        model=SUMMARY_MODEL,
        db=db,
        call_type="summarisation",
        max_tokens=1024,
    )

    # call_llm returns (parsed_dict, llm_call_id) — the parsed dict has "response"
    # or the raw text. For summarisation we want the raw text output.
    if isinstance(parsed, dict):
        return parsed.get("summary", parsed.get("response", json.dumps(parsed)))
    return str(parsed)


def _format_messages_for_summary(messages: list) -> str:
    """Format messages for the summarisation prompt, truncating tool results."""
    lines = []
    for msg in messages:
        role_label = "User" if msg.role == "user" else "Assistant"
        content = msg.content

        # Handle content blocks (tool_use / tool_result stored as JSON strings)
        if isinstance(content, str) and content.startswith("[{"):
            try:
                blocks = json.loads(content)
                if isinstance(blocks, list):
                    block_parts = []
                    for block in blocks:
                        if isinstance(block, dict):
                            btype = block.get("type", "")
                            if btype == "tool_use":
                                block_parts.append(
                                    f"[Called tool: {block.get('name', '?')}]"
                                )
                            elif btype == "tool_result":
                                result_content = str(block.get("content", ""))[:500]
                                if len(str(block.get("content", ""))) > 500:
                                    result_content += " [truncated]"
                                block_parts.append(f"[Tool result: {result_content}]")
                            elif btype == "text":
                                block_parts.append(block.get("text", ""))
                            else:
                                block_parts.append(str(block)[:200])
                        else:
                            block_parts.append(str(block)[:200])
                    content = " ".join(block_parts)
            except (json.JSONDecodeError, TypeError):
                pass  # treat as plain text

        # Truncate very long plain text messages
        if len(content) > 800:
            content = content[:800] + " [truncated]"

        lines.append(f"{role_label}: {content}")

    return "\n".join(lines)


def _summarise_older_messages(messages: list) -> str:
    """Build a deterministic text summary of older messages (no LLM call).

    Used as fallback when LLM summarisation is unavailable or fails.
    """
    count = len(messages)

    # Date range
    first_date = messages[0].created_at
    last_date = messages[-1].created_at
    date_range = (
        f"{first_date.strftime('%Y-%m-%d')} to {last_date.strftime('%Y-%m-%d')}"
    )

    # Extract user message snippets as topic bullets
    user_msgs = [m for m in messages if m.role == "user"]
    bullets = []
    for m in user_msgs[:10]:
        snippet = m.content[:100].replace("\n", " ").strip()
        if len(m.content) > 100:
            snippet += "..."
        bullets.append(f"- {snippet}")

    parts = [
        f"[Earlier conversation: {count} messages from {date_range}]",
    ]
    if bullets:
        parts.append("Topics discussed:")
        parts.extend(bullets)

    return "\n".join(parts)


def _ensure_alternation(messages: list[dict]) -> list[dict]:
    """Merge consecutive same-role messages to satisfy Anthropic's alternation requirement."""
    if not messages:
        return messages

    merged: list[dict] = [messages[0]]
    for msg in messages[1:]:
        if msg["role"] == merged[-1]["role"]:
            merged[-1]["content"] += "\n\n" + msg["content"]
        else:
            merged.append(msg)

    return merged
