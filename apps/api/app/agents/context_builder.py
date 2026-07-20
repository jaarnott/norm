"""Unified conversation context builder.

Builds the Anthropic messages list for both normal threads and automated task
runs. When conversations exceed SUMMARY_THRESHOLD messages, uses an LLM
(Haiku) to generate a concise summary of older messages. The summary is stored
on the Thread record and updated incrementally as the conversation grows.
"""

import json
import logging

from app.agents.context_budget import estimate_tokens

logger = logging.getLogger(__name__)

SUMMARY_THRESHOLD = 20  # Trigger LLM summarisation after this many messages
RECENT_AFTER_SUMMARY = 10  # Keep this many recent messages in full after summary
#: Token budget for verbatim history. The count rule alone left a hole: ten
#: messages each carrying a pasted report never reached SUMMARY_THRESHOLD, so
#: they were sent in full every turn and the conversation died on "prompt is
#: too long" with no compaction attempted. Sized to leave room for the system
#: prompt and tool schemas (~10k), in-turn tool results, and the output.
MAX_HISTORY_TOKENS = 40_000  # estimated tokens; see context_budget for calibration

# Structured, not prose. A paragraph reads well and is useless to act on: the
# model has to re-derive what was decided and what is still open. The headings
# below are the things a later turn actually needs.
#
# The tool-call ids matter most. Payloads are not persisted as Messages, so once
# a turn is compacted the only route back to the data is
# norm__search_tool_result with the id — drop the ids and the data becomes
# permanently unreachable, which turns compaction into data loss.
_SUMMARY_SYSTEM_PROMPT = (
    "You are a conversation summariser for a hospitality operations platform. "
    "Summarise under these exact headings, omitting any that do not apply:\n"
    "GOAL: what the user is trying to achieve.\n"
    "SCOPE: venues, periods and filters in play.\n"
    "FACTS: figures and dates established, with their values and the trading "
    "window they were measured over. Never round or restate a number you were "
    "not given.\n"
    "DATA: any tool_call_id mentioned, verbatim, with what it holds. Copy these "
    "exactly — they are how the full results are retrieved later.\n"
    "DECISIONS: what was agreed or chosen, and any draft created.\n"
    "CORRECTIONS: anything the user corrected. Preserve their wording.\n"
    "OPEN: questions or actions still outstanding.\n"
    "Be terse. Under 500 words. Past tense, third person."
)


#: How many prior tool results to advertise. Newest first — older data is
#: usually superseded, and the manifest has to stay cheap enough to send every
#: turn.
TOOL_MANIFEST_LIMIT = 15
#: Args worth showing. Everything else is plumbing the model did not choose
#: (venue_id, mode) or already knows.
_MANIFEST_SKIP_ARGS = {"venue_id", "mode", "confirmed_by_user", "tool_call_id"}


def _describe_shape(payload) -> str:
    """A row count if the payload is tabular, else a coarse type name."""
    if isinstance(payload, list):
        return f"{len(payload)} rows"
    if isinstance(payload, dict):
        # The consolidators wrap as {window, data}; unwrap so the count is the
        # count of actual rows rather than "2 keys".
        inner = payload.get("data", payload)
        if isinstance(inner, list):
            return f"{len(inner)} rows"
        for value in payload.values():
            if isinstance(value, list) and value:
                return f"{len(value)} rows"
        return "object"
    return "value"


def _format_args(params) -> str:
    if not isinstance(params, dict):
        return ""
    parts = []
    for key, value in params.items():
        if key in _MANIFEST_SKIP_ARGS or value in (None, "", [], {}):
            continue
        text = str(value)
        if len(text) > 40:
            text = text[:40] + "…"
        parts.append(f"{key}={text}")
    return ", ".join(parts[:4])


def build_tool_result_manifest(thread, db) -> str | None:
    """Advertise tool results that are still retrievable from earlier turns.

    Tool results are never persisted as Messages — they live in the loop's
    in-memory list for one turn and then vanish. So on the next turn the model
    has no idea it already fetched anything, and a follow-up question about
    data it just pulled forces a re-fetch of the same rows.

    The payloads *are* durable (``ToolCall.result_payload``) and already
    addressable: ``ToolCall.id`` is the Anthropic tool_use block id, which is
    exactly what ``norm__search_tool_result`` takes. So rather than re-inlining
    payloads, this lists what exists and how to reach it — a pointer, not the
    data. One line per result, roughly 25 tokens each.
    """
    if thread is None or db is None:
        return None

    from app.db.models import ToolCall

    try:
        rows = (
            db.query(ToolCall)
            .filter(
                ToolCall.thread_id == thread.id,
                ToolCall.status == "executed",
                # Internal norm tools return control-flow answers (a resolved
                # date window, a search result), not datasets worth re-querying.
                ToolCall.connector_name != "norm",
            )
            .order_by(ToolCall.created_at.desc())
            .limit(TOOL_MANIFEST_LIMIT)
            .all()
        )
    except Exception:
        # Never let the manifest break a conversation — it is an optimisation.
        logger.exception("tool manifest query failed; continuing without it")
        return None

    lines = []
    for tc in rows:
        if tc.result_payload is None:
            continue
        args = _format_args(tc.input_params)
        lines.append(
            f"- {tc.id} | {tc.tool_name}({args}) → {_describe_shape(tc.result_payload)}"
        )

    if not lines:
        return None

    return (
        "[Data already fetched in this conversation — still retrievable]\n"
        "Query any of these with norm__search_tool_result using the id shown, "
        "rather than calling the tool again:\n" + "\n".join(lines)
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

    older, recent = _split_history(sorted_msgs)
    if older:
        summary = _get_or_create_summary(older, thread, db)
        result.append({"role": "user", "content": summary})
        result.append(
            {
                "role": "assistant",
                "content": "Understood, I have context from the earlier conversation.",
            }
        )

    # The caller usually persists the user's message *before* invoking the loop
    # (base.py flushes the Message row, then run_tool_loop reads task.messages),
    # so the new message is already the last row here. Appending it again below
    # sent the user's text twice — _ensure_alternation merged the two user turns
    # into one, silently doubling it. Drop the persisted copy and let the append
    # below add it back with its [Context] block attached.
    if recent and recent[-1].role == "user" and recent[-1].content == new_message:
        recent = recent[:-1]

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

    # What Norm has learned about this user and org. Injected as background
    # context, never as instruction — see memory_service.recall_index.
    if thread is not None and db is not None:
        try:
            from app.services.memory_service import recall_index

            org_id = getattr(thread, "organization_id", None)
            user_id = getattr(thread, "user_id", None)
            if not org_id and user_id:
                from app.db.models import OrganizationMembership

                m = (
                    db.query(OrganizationMembership)
                    .filter(OrganizationMembership.user_id == user_id)
                    .first()
                )
                org_id = m.organization_id if m else None
            if org_id and user_id:
                index = recall_index(db, user_id=user_id, organization_id=org_id)
                if index:
                    content += "\n\n" + index
        except Exception:
            logger.exception("memory index injection failed; continuing without it")

    # Advertise data fetched in earlier turns. Appended last so it sits closest
    # to the question being asked — the point where the model decides whether
    # to call a tool again.
    manifest = build_tool_result_manifest(thread, db)
    if manifest:
        content += "\n\n" + manifest

    result.append({"role": "user", "content": content})

    # Ensure valid alternation — merge consecutive same-role messages
    result = _ensure_alternation(result)

    return result


def _split_history(msgs: list) -> tuple[list, list]:
    """Split history into (summarise, keep verbatim).

    Two triggers, because either alone leaves a hole:

    - **message count** catches many small turns.
    - **token budget** catches few enormous ones. A thread of ten messages each
      containing a pasted report never exceeded SUMMARY_THRESHOLD, so nothing
      was ever compacted and the turn failed outright.

    The newest messages are kept, oldest-first order preserved, and at least
    one message always survives — summarising the message we are about to
    answer would be self-defeating.
    """
    if not msgs:
        return [], []

    total = sum(estimate_tokens(m.content) for m in msgs)
    if len(msgs) <= SUMMARY_THRESHOLD and total <= MAX_HISTORY_TOKENS:
        return [], msgs

    recent: list = []
    used = 0
    for msg in reversed(msgs[-RECENT_AFTER_SUMMARY:]):
        cost = estimate_tokens(msg.content)
        if recent and used + cost > MAX_HISTORY_TOKENS:
            break
        recent.append(msg)
        used += cost
    recent.reverse()
    return msgs[: len(msgs) - len(recent)], recent


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
    from app.services.models import router_model

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
        model=router_model(db),
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
                                # Carry the id: it is the only handle the
                                # summary can offer for retrieving the full
                                # payload after this turn is compacted away.
                                ref = block.get("tool_use_id", "")
                                label = f"Tool result {ref}" if ref else "Tool result"
                                block_parts.append(f"[{label}: {result_content}]")
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
