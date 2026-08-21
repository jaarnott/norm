"""The approval resume commits the approval BEFORE its (long) continuation.

An approved write runs its external side effect, then the loop continues. If the
DB commit only happened at the end of that continuation, a request cut would roll
the DB back while the side effect already stood — reverting the thread to
awaiting-approval and letting a re-approve DOUBLE-EXECUTE the write. So
resume_tool_loop must commit the approval boundary first.
"""

import uuid

from app.agents import tool_loop as TL
from app.db.models import ToolCall
from tests.conftest import _make_thread


def test_resume_commits_approval_before_the_continuation(
    db_session, admin_user, monkeypatch
):
    thread = _make_thread(
        db_session,
        admin_user,
        domain="reports",
        intent="reports.tool_use",
        status="awaiting_tool_approval",
    )
    thread.agent_loop_state = {"messages": [], "iteration": 1}
    tc_id = str(uuid.uuid4())
    thread.pending_tool_call_ids = [tc_id]
    db_session.add(
        ToolCall(
            id=tc_id,
            thread_id=thread.id,
            iteration=1,
            tool_name="loadedhub__update_shift",
            connector_name="loadedhub",
            action="update_shift",
            method="POST",
            status="approved",
            input_params={},
        )
    )
    db_session.flush()

    # The approved write "executes" without a real connector call.
    monkeypatch.setattr(
        TL, "_execute_tool_call", lambda tc, db, config_db=None: {"ok": True}
    )

    seen = {"committed": False}
    real_commit = db_session.commit

    def spy_commit():
        seen["committed"] = True
        return real_commit()

    monkeypatch.setattr(db_session, "commit", spy_commit)

    def fake_loop(*a, **k):
        assert seen["committed"], (
            "the approval must be committed before the continuation runs"
        )
        return {"status": "completed"}

    monkeypatch.setattr(TL, "_execute_loop", fake_loop)

    TL.resume_tool_loop(thread, db_session, "sys", [], config_db=None)

    assert seen["committed"]
    assert thread.status == "in_progress"
    assert thread.pending_tool_call_ids is None
