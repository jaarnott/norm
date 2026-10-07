"""A round that mixes an approval-gated write with other calls survives the pause.

Prod, 2–3 Oct 2026 (threads c6aad2d5 and aad94383 twice): the model put a
notebook update and/or a read in the same round as manage_stock_item writes.
The loop saved only the pending writes at suspension; after Approve the writes
ran, then the first model call was rejected — "tool_use ids were found without
tool_result blocks" — /approve returned 500, and the card showed Approved with
no reply. This drives the REAL loop through that round, approves the write, and
checks that the model is answered for every call it made.
"""

import uuid

from app.agents import tool_loop as TL
from app.db.models import ToolCall
from tests.conftest import _make_thread
from tests.test_tool_loop_unknown_tool import _Block, _Response

TOOLS = [
    {
        "name": "loadedhub__get_stock",
        "description": "[GET] Read stock.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "loadedhub__manage_stock_item",
        "description": "[PUT] Update a stock item.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def test_a_mixed_round_is_fully_answered_after_approval(
    db_session, admin_user, monkeypatch
):
    seen = []
    mixed = _Response(
        "tool_use",
        [
            _Block("text", text="Noting progress, then the next item."),
            _Block(
                "tool_use",
                id="toolu_note",
                name="memory",
                input={
                    "command": "str_replace",
                    "path": "/memories/job.md",
                    "old_str": "(empty)",
                    "new_str": "item 1 done",
                },
            ),
            _Block("tool_use", id="toolu_read", name="loadedhub__get_stock", input={}),
            _Block(
                "tool_use",
                id="toolu_write",
                name="loadedhub__manage_stock_item",
                input={"op": "update", "item_id": str(uuid.uuid4())},
            ),
        ],
    )

    def llm(system_prompt, messages, tools, db, thread_id, call_type):
        seen.append([dict(m) for m in messages])
        if len(seen) == 1:
            return mixed, None
        return _Response("end_turn", [_Block("text", text="Done.")]), None

    monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm_with_tools", llm)
    monkeypatch.setattr(
        TL, "_execute_tool_call", lambda tc, db, config_db=None: {"ok": 1}
    )
    thread = _make_thread(
        db_session,
        admin_user,
        domain="procurement",
        intent="procurement.tool_use",
        status="in_progress",
    )
    # Give the notebook something to replace, so the memory op succeeds.
    from app.agents import memory_tool

    memory_tool.handle(
        {"command": "create", "path": "/memories/job.md", "file_text": "(empty)"},
        db_session,
        thread.id,
    )

    TL.run_tool_loop(
        "update the stock codes",
        thread,
        db_session,
        "system prompt",
        TOOLS,
        config_db=db_session,
    )
    pending = thread.pending_tool_call_ids or []
    assert pending == ["toolu_write"], f"expected the write to wait: {pending}"

    # The owner approves, and the loop resumes.
    tc = db_session.get(ToolCall, "toolu_write")
    tc.status = "approved"
    db_session.flush()
    TL.resume_tool_loop(thread, db_session, "system prompt", TOOLS, config_db=db_session)

    assert len(seen) == 2, f"expected the model to be called again, saw {len(seen)}"
    resumed = seen[1]
    asked = {
        b["id"]
        for m in resumed
        if m["role"] == "assistant" and isinstance(m["content"], list)
        for b in m["content"]
        if b.get("type") == "tool_use"
    }
    answered = {
        b["tool_use_id"]
        for m in resumed
        if m["role"] == "user" and isinstance(m["content"], list)
        for b in m["content"]
        if b.get("type") == "tool_result"
    }
    assert asked == {"toolu_note", "toolu_read", "toolu_write"}
    assert asked - answered == set(), (
        f"tool_use ids without a tool_result: {sorted(asked - answered)} "
        "— the API rejects this turn with a 400"
    )
    assert thread.status == "completed"
