"""Result shaping for the MCP surface.

Why this isn't just ``tool_loop._slim_tool_result``:

That function takes a ``search_available`` flag, but **never reads it** — its
"too large" branch unconditionally tells the model to call
``norm__search_tool_result`` with a ``tool_call_id``. In-app that's correct and
the loop compensates (it injects the search tool on first truncation, see
tool_loop.py:407-419). Over MCP it would be a lie twice over: the tool isn't on
this surface, and there is no ToolCall row to reference — MCP is stateless, so
there is no id to hand back.

So we reuse the mechanics (``_unwrap_array``, ``_truncate_nested_arrays``,
``summary_fields``) and write our own message. The honest guidance for a
stateless caller is "narrow your request", not "call this other tool".

The consequence is worth stating plainly: truncation here is **lossy with no
escape hatch**. A tool that truncates often is answering Claude from a
lobotomised payload, and needs ``summary_fields`` on its spec row. That is what
the ``mcp_result_truncations_total`` metric is for.
"""

from __future__ import annotations

import json
from typing import Any

from app.agents.tool_loop import (
    MAX_TOOL_RESULT_CHARS_NO_SEARCH,
    _truncate_nested_arrays,
    _unwrap_array,
)

__all__ = [
    "MCP_MAX_RESULT_CHARS",
    "MCP_MAX_UI_RESULT_CHARS",
    "shape_result",
    "ui_content_summary",
    "ui_payload",
]

MCP_MAX_RESULT_CHARS = MAX_TOOL_RESULT_CHARS_NO_SEARCH

# A tool bound to an MCP App sends its data twice: a shaped copy in ``content``
# for the model to read, and a copy in ``structuredContent`` for the app to
# render. Only the first is context — the app's copy is transport, so it gets a
# far larger budget. Still bounded: an unbounded payload would be posted into
# the host's iframe, and some hosts do surface structuredContent to the model.
MCP_MAX_UI_RESULT_CHARS = 400_000


def ui_payload(payload: Any, max_chars: int = MCP_MAX_UI_RESULT_CHARS) -> Any | None:
    """The full payload for an app to render, or None if even that is too big.

    Returning None makes the caller fall back to the shaped payload, so a
    pathological result degrades to the model-facing summary rather than
    shipping megabytes into an iframe.
    """
    if not isinstance(payload, dict):
        return None
    serialized = _serialize(payload)
    if serialized is None or len(serialized) > max_chars:
        return None
    return payload


def _serialize(payload: Any) -> str | None:
    try:
        return json.dumps(payload, default=str)
    except (TypeError, ValueError):
        return None


def ui_content_summary(payload: Any) -> Any:
    """Compact, model-facing content for a tool whose result renders in a card.

    The card (``structuredContent``) holds the full data; the model only needs
    to know the tool succeeded and a few headline fields to talk about. So the
    payload is returned with long nested arrays sampled down — never the
    "this result contains N items, too many to return, narrow the request"
    envelope ``shape_result`` produces for an oversized payload.

    That envelope is correct for a plain data tool, but a lie for a UI one: the
    data isn't lost, it's on screen. Handing it to the model reads as a failure
    and sends it chasing the data in chunks or falling back to another tool —
    exactly the roster incident where a fully-rendered 115-shift grid drove the
    model to re-fetch, hit the same envelope again, and detour through the
    attendance view while the user watched a perfectly good grid.
    """
    window = None
    body = payload
    if isinstance(payload, dict) and "window" in payload and "data" in payload:
        window, body = payload["window"], payload["data"]

    out: dict = {
        "_rendered_in_ui": True,
        "message": (
            "This result is shown to the user in an interactive card. The full "
            "data is on their screen; below is the same data with long lists "
            "sampled to a few rows. Do NOT narrow the request or re-fetch to "
            '"get all of it" — the user can already see everything. Use the '
            "headline fields to answer, and only call another tool if the user "
            "asks for something this result does not contain."
        ),
        "data": _truncate_nested_arrays(body),
    }
    if window is not None:
        out["window"] = window
    return out


def shape_result(
    payload: Any,
    summary_fields: list[str] | None = None,
    max_chars: int = MCP_MAX_RESULT_CHARS,
) -> tuple[Any, bool]:
    """Return ``(shaped_payload, was_truncated)``.

    Under the limit the payload is returned **unchanged and native**, so
    ``tools_call_result`` can still emit ``structuredContent``. Only oversized
    payloads are reshaped.
    """
    serialized = _serialize(payload)
    if serialized is None or len(serialized) <= max_chars:
        return payload, False

    data = (
        _unwrap_array(payload)
        if isinstance(payload, dict)
        else (payload if isinstance(payload, list) else None)
    )

    # Not an array of objects — nothing structured to slim, so hard-truncate.
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return (
            {
                "_truncated": True,
                "_reason": "Result too large to return in full.",
                "_preview": serialized[:max_chars],
                "message": (
                    "This result was cut off. Narrow the request — a shorter "
                    "date range, a single venue, or a more specific filter."
                ),
            },
            True,
        )

    available_fields = list(data[0].keys())

    if summary_fields:
        slim_items = [
            _truncate_nested_arrays(
                {k: item.get(k) for k in summary_fields if k in item}
            )
            for item in data
        ]
        summary = {
            "_slimmed": True,
            "_total_items": len(data),
            "_showing_fields": summary_fields,
            "_fields_available": available_fields,
            "data": slim_items,
        }
        if len(_serialize(summary) or "") <= max_chars:
            return summary, True

    # Still too large (or no summary_fields configured): show the shape and
    # tell the caller how to ask for less. Deliberately no tool_call_id and no
    # reference to search_tool_result.
    return (
        {
            "_too_large": True,
            "_total_items": len(data),
            "_fields_available": available_fields,
            "_sample_item": _truncate_nested_arrays(data[0]),
            "message": (
                f"This result contains {len(data)} items — too many to return. "
                f"Narrow the request: a shorter date range, a single venue, or "
                f"a more specific filter. The sample item above shows the shape "
                f"of the data."
            ),
        },
        True,
    )
