"""An answer cut off at the output ceiling says so.

A group-wide "top 50 fruit & veg items" turn spent its whole output budget
reasoning over 30 results and stopped mid-table (prod thread fa1cfd1c,
23 Sep 2026). The loop treated stop_reason="max_tokens" like end_turn and
saved half a table as the finished answer, twice, with no sign anything had
gone wrong. The saved reply must now carry a visible note.
"""

from app.agents import tool_loop
from app.db.models import Message
from tests.conftest import _make_thread
from tests.test_tool_loop_unknown_tool import _Block, _Response


def _run(db_session, admin_user, monkeypatch, stop_reason):
    def llm(system_prompt, messages, tools, db, thread_id, call_type):
        return (
            _Response(
                stop_reason, [_Block("text", text="| # | Item | Spend |\n| 1 |")]
            ),
            "llm-1",
        )

    monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm_with_tools", llm)
    thread = _make_thread(
        db_session,
        admin_user,
        domain="reports",
        intent="reports.tool_use",
        status="processing",
    )
    tool_loop.run_tool_loop("top 50 items", thread, db_session, "system prompt", [])
    return (
        db_session.query(Message)
        .filter(Message.thread_id == thread.id, Message.role == "assistant")
        .one()
        .content
    )


def test_a_reply_cut_off_at_the_ceiling_is_flagged(db_session, admin_user, monkeypatch):
    saved = _run(db_session, admin_user, monkeypatch, "max_tokens")
    assert saved.startswith("| # | Item | Spend |")  # what it did write is kept
    assert saved.endswith(tool_loop.TRUNCATION_NOTE)


def test_a_finished_reply_carries_no_note(db_session, admin_user, monkeypatch):
    saved = _run(db_session, admin_user, monkeypatch, "end_turn")
    assert tool_loop.TRUNCATION_NOTE not in saved
