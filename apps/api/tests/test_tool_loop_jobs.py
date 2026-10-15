"""A long tool call leaves a trace, and a job it starts gets its card.

14 Oct 2026 (prod thread 2f53a67e): a receive ran 25 minutes inside the chat
request and Cloud Run cut it off. The ToolCall row, Norm's "I'm widening the
window…" and the turn's steps were only ever flushed, so the thread was left
with the person's message and nothing else. They are now committed before the
tool runs. And a tool that hands the work to a background job returns
``_job`` — the loop puts the job's live card in the conversation.
"""

from app.agents import tool_loop
from app.db.models import Message, Thread, ToolCall
from tests.conftest import _make_thread
from tests.test_tool_loop_unknown_tool import _Block, _Response

TOOL = {
    "name": "loadedhub__review_and_receive_invoices",
    "description": "[GET] Review and receive invoices.",
    "input_schema": {"type": "object", "properties": {}},
}


def _run(db_session, admin_user, monkeypatch, execute):
    script = [
        _Response(
            "tool_use",
            [
                _Block("text", text="Applegum has 200+ drafts — widening the window."),
                _Block(
                    "tool_use",
                    id="toolu_1",
                    name="loadedhub__review_and_receive_invoices",
                    input={},
                ),
            ],
        ),
        _Response("end_turn", [_Block("text", text="Started — it runs in the background.")]),
    ]
    calls = []

    def llm(system_prompt, messages, tools, db, thread_id, call_type, tool_choice=None):
        calls.append(1)
        return script[min(len(calls), len(script)) - 1], None

    monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm_with_tools", llm)
    monkeypatch.setattr(tool_loop, "_execute_tool_call", execute)
    thread = _make_thread(
        db_session,
        admin_user,
        domain="procurement",
        intent="procurement.tool_use",
        status="in_progress",
    )
    tool_loop.run_tool_loop(
        "receive all outstanding invoices",
        thread,
        db_session,
        "system prompt",
        [TOOL],
        config_db=db_session,
    )
    return thread


def test_the_call_and_the_reasoning_are_saved_before_the_tool_runs(
    db_session, admin_user, monkeypatch
):
    seen = {}

    def execute(tc, db, config_db=None):
        # Mid-tool: what a cut-off would leave behind is already committed.
        db.expire_all()
        thread = db.query(Thread).filter(Thread.id == tc.thread_id).one()
        seen["steps"] = [s.get("text") if isinstance(s, dict) else s for s in thread.thinking_steps or []]
        seen["tool_call"] = db.query(ToolCall).filter(ToolCall.id == "toolu_1").first()
        return {"rows": []}

    _run(db_session, admin_user, monkeypatch, execute)
    assert seen["tool_call"] is not None
    assert any(
        "[reasoning] Applegum has 200+ drafts — widening the window." in str(s)
        for s in seen["steps"]
    )


def test_a_tool_that_starts_a_job_puts_its_card_in_the_conversation(
    db_session, admin_user, monkeypatch
):
    def execute(tc, db, config_db=None):
        tc.result_payload = {"_job": {"id": "job-1"}, "message": "started"}
        return {"rows": []}

    thread = _run(db_session, admin_user, monkeypatch, execute)
    msg = (
        db_session.query(Message)
        .filter(Message.thread_id == thread.id, Message.role == "assistant")
        .one()
    )
    assert {"component": "background_job", "data": {"job_id": "job-1"}, "props": {}} in (
        msg.display_blocks or []
    )
