"""A tool call the agent was not given this turn is refused, not executed.

After a mid-thread agent rebind the model can mimic tool names it sees in the
conversation history — with an invented schema, since the real one was never in
its prompt. Phase A used to default an unknown name's method to POST, so on
20 Aug 2026 (prod thread bb7010c3) a mimicked recipe WRITE executed with garbage
params and a mimicked `get_recipe_details` — a READ — became a phantom approval
card that left the thread stuck in `awaiting_tool_approval`.

Unknown names now come straight back as an error tool_result: no ToolCall row,
no execution, no approval card, and the loop continues so the model can recover.
"""

import json

from app.agents import tool_loop
from app.db.models import ToolCall
from tests.conftest import _make_thread


class _Block:
    def __init__(self, type, **kw):
        self.type = type
        for k, v in kw.items():
            setattr(self, k, v)


class _Response:
    def __init__(self, stop_reason, content):
        self.stop_reason = stop_reason
        self.content = content
        self.usage = None


KNOWN_TOOL = {
    "name": "loadedhub__get_stock_items",
    "description": "[GET] List stock items.",
    "input_schema": {"type": "object", "properties": {}},
}


class _ScriptedLlm:
    """First turn calls one known + one unknown tool; second turn ends."""

    def __init__(self):
        self.calls = []

    def __call__(self, system_prompt, messages, tools, db, thread_id, call_type):
        self.calls.append({"messages": [dict(m) for m in messages]})
        if len(self.calls) == 1:
            return (
                _Response(
                    "tool_use",
                    [
                        _Block(
                            "tool_use",
                            name="cook_brothers_app__kitchen_loadedhub_update_recipe",
                            id="tu_unknown",
                            input={"recipe_id": "r1", "made_up_field": "x"},
                        ),
                    ],
                ),
                "llm-1",
            )
        return (_Response("end_turn", [_Block("text", text="Understood.")]), "llm-2")


def _run(db_session, admin_user, monkeypatch):
    llm = _ScriptedLlm()
    monkeypatch.setattr(
        "app.interpreter.llm_interpreter.call_llm_with_tools", llm
    )
    thread = _make_thread(
        db_session,
        admin_user,
        domain="executive_chef",
        intent="executive_chef.tool_use",
        status="processing",
    )
    result = tool_loop.run_tool_loop(
        "swap the leek line",
        thread,
        db_session,
        "system prompt",
        [dict(KNOWN_TOOL)],
    )
    return llm, thread, result


class TestUnknownToolIsRefused:
    def test_no_tool_call_row_and_no_approval(
        self, db_session, admin_user, monkeypatch
    ):
        llm, thread, _ = _run(db_session, admin_user, monkeypatch)
        rows = (
            db_session.query(ToolCall).filter(ToolCall.thread_id == thread.id).all()
        )
        assert rows == []
        assert thread.status != "awaiting_tool_approval"

    def test_model_receives_an_error_result_and_recovers(
        self, db_session, admin_user, monkeypatch
    ):
        llm, _, result = _run(db_session, admin_user, monkeypatch)
        # The loop went back to the model with an is_error tool_result...
        assert len(llm.calls) == 2
        followup = llm.calls[1]["messages"][-1]
        assert followup["role"] == "user"
        (block,) = followup["content"]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "tu_unknown"
        assert block["is_error"] is True
        assert "not available" in json.loads(block["content"])["error"]
        # ...and the turn finished normally on the model's recovery text.
        assert "Understood." in (result.get("message") or result.get("answer") or str(result))
