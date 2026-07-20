"""Token accounting for the assembled prompt.

Every size limit in this codebase is a message count or a character count,
applied locally — nothing has ever measured the prompt that actually goes to the
API. That is why context overflow is discovered by string-matching "prompt is
too long" on the way back out (tool_loop._execute_loop) rather than prevented on
the way in.

This module is the missing measurement. It deliberately does NOT call
``client.messages.count_tokens``: budgeting runs on every turn and often several
times per turn, so it must be free and synchronous. A chars-per-token estimate
is close enough to *decide with* — and because every call already persists the
real ``usage.input_tokens`` to ``LlmCall``, the estimate can be checked against
truth after the fact rather than trusted blindly. ``estimate_error`` exists for
exactly that reconciliation, and it has already earned its place: the first
calibration run showed the original single-divisor estimate running at 0.545 of
the truth.

The breakdown matters as much as the total: "the prompt is 90k tokens" is not
actionable, but "72k of it is tool results from this turn" is.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

# Two divisors, because one was wrong by a lot.
#
# A single 4-chars/token figure was calibrated against a real four-turn
# conversation and came out at 0.545 of the true count — a 45% under-count,
# which meant a 40k history budget did not bite until roughly 73k real tokens.
# The cause is mix: tool schemas were 51% of the prompt and are pure JSON,
# which tokenises far denser than prose because every brace, quote and comma
# is its own token.
#
# So prose and serialised structures are counted differently. These remain
# estimates — `PromptBreakdown.estimate_error` reports the ratio against the
# real usage on every call, which is how this was found and how it should be
# re-checked rather than trusted.
CHARS_PER_TOKEN = 4
CHARS_PER_TOKEN_JSON = 2.6


def estimate_tokens(value) -> int:
    """Rough token count for a string or any JSON-serialisable structure."""
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value) // CHARS_PER_TOKEN
    try:
        serialised = json.dumps(value, default=str)
    except (TypeError, ValueError):
        return len(str(value)) // CHARS_PER_TOKEN
    return int(len(serialised) / CHARS_PER_TOKEN_JSON)


@dataclass
class PromptBreakdown:
    """Estimated token cost of one assembled prompt, by component."""

    system: int = 0
    tools: int = 0
    summary: int = 0
    history: int = 0
    tool_results: int = 0
    new_message: int = 0
    #: Populated only when reconciled against a real API response.
    actual_input_tokens: int | None = None
    #: Prompt-cache accounting from the same response. `cache_read` is the
    #: number that proves caching is working; `cache_write` is what it cost to
    #: populate. Both None until reconciled.
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    #: Per-tool result sizes, largest first — names the specific offender.
    largest_results: list[tuple[str, int]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (
            self.system
            + self.tools
            + self.summary
            + self.history
            + self.tool_results
            + self.new_message
        )

    @property
    def estimate_error(self) -> float | None:
        """Ratio of estimate to truth, or None if not reconciled.

        1.0 is perfect; below 1.0 means we under-counted. Worth watching before
        anyone tunes a budget against these numbers.
        """
        if not self.actual_input_tokens:
            return None
        return round(self.total / self.actual_input_tokens, 3)

    def as_log_fields(self) -> dict:
        """Flat dict for structlog — one line per turn, greppable."""
        fields = {
            "ctx_total": self.total,
            "ctx_system": self.system,
            "ctx_tools": self.tools,
            "ctx_summary": self.summary,
            "ctx_history": self.history,
            "ctx_tool_results": self.tool_results,
            "ctx_new_message": self.new_message,
        }
        if self.actual_input_tokens is not None:
            fields["ctx_actual"] = self.actual_input_tokens
            fields["ctx_estimate_ratio"] = self.estimate_error
        if self.cache_read_tokens is not None:
            fields["ctx_cache_read"] = self.cache_read_tokens
        if self.cache_write_tokens is not None:
            fields["ctx_cache_write"] = self.cache_write_tokens
        if self.largest_results:
            fields["ctx_largest_results"] = [
                f"{name}:{size}" for name, size in self.largest_results[:3]
            ]
        return fields


def _blocks(content) -> list:
    """Content blocks for a message, or [] if it is plain text."""
    if isinstance(content, list):
        return content
    # Blocks are sometimes persisted as a JSON string (see
    # context_builder._format_messages_for_summary, which does the same sniff).
    if isinstance(content, str) and content.startswith("[{"):
        try:
            parsed = json.loads(content)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def measure_prompt(
    system_prompt: str | None = None,
    tools: list | None = None,
    messages: list | None = None,
) -> PromptBreakdown:
    """Estimate the token cost of an assembled prompt, split by component.

    Splits ``messages`` into summary / tool results / everything else so the
    dominant term is visible. The summary is identified by the marker
    ``context_builder`` writes; tool results by their content-block type.
    """
    breakdown = PromptBreakdown(
        system=estimate_tokens(system_prompt),
        tools=estimate_tokens(tools),
    )

    if not messages:
        return breakdown

    results: list[tuple[str, int]] = []
    # Names arrive on the tool_use block, but sizes on the matching tool_result
    # block, so resolve ids to names in one pass before attributing sizes.
    names_by_id: dict[str, str] = {}
    for msg in messages:
        for block in _blocks(msg.get("content")):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                names_by_id[block.get("id", "")] = block.get("name", "?")

    for index, msg in enumerate(messages):
        content = msg.get("content")
        blocks = _blocks(content)

        if blocks:
            for block in blocks:
                if not isinstance(block, dict):
                    breakdown.history += estimate_tokens(block)
                    continue
                size = estimate_tokens(block.get("content") or block.get("text") or "")
                if block.get("type") == "tool_result":
                    breakdown.tool_results += size
                    name = names_by_id.get(block.get("tool_use_id", ""), "?")
                    results.append((name, size))
                else:
                    breakdown.history += size
            continue

        size = estimate_tokens(content)
        # The summary is injected as the first user turn by
        # context_builder._get_or_create_summary.
        if isinstance(content, str) and content.startswith("[Conversation summary]"):
            breakdown.summary += size
        elif index == len(messages) - 1 and msg.get("role") == "user":
            breakdown.new_message += size
        else:
            breakdown.history += size

    breakdown.largest_results = sorted(results, key=lambda r: r[1], reverse=True)
    return breakdown
