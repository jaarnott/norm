"""A built-in's file must reach the model.

Built-ins return a file beside their data — ``{"data": {...}, "_document":
{type: document, source: {...}}}`` — and the tool loop lifts ``_document`` out
of the stored result and attaches it to the tool result. But the loop stored
only ``data``, so the file was dropped every time: the BambooHR CV reader and
re-opened chat attachments handed the model a file name and size, never the
file (found 29 Sep 2026).
"""

import uuid

from app.db.config_models import ConnectionSpec
from app.db.models import ToolCall
from tests.conftest import _make_thread, _make_user

BLOCK = {
    "type": "document",
    "source": {"type": "base64", "media_type": "application/pdf", "data": "JVBE"},
}


def _tool_call(db_session):
    db_session.add(
        ConnectionSpec(
            id=str(uuid.uuid4()),
            connector_name="doc_norm",
            display_name="Doc",
            execution_mode="internal",
            auth_type="none",
            auth_config={},
            tools=[{"action": "open_file"}],
            enabled=True,
        )
    )
    user = _make_user(db_session)
    thread = _make_thread(db_session, user, domain="norm")
    tc = ToolCall(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        iteration=1,
        tool_name="doc_norm__open_file",
        connector_name="doc_norm",
        action="open_file",
        method="GET",
        status="pending",
        input_params={},
    )
    db_session.add(tc)
    db_session.flush()
    return tc


def test_a_built_ins_document_is_stored_for_the_loop_to_attach(db_session, monkeypatch):
    import app.agents.internal_tools as it
    from app.agents.tool_loop import _execute_tool_call

    def handler(params, db, thread_id):
        return {"success": True, "data": {"filename": "cv.pdf"}, "_document": BLOCK}

    monkeypatch.setattr(
        it,
        "get_handler",
        lambda c, a: handler if (c, a) == ("doc_norm", "open_file") else None,
    )
    tc = _tool_call(db_session)
    result = _execute_tool_call(tc, db_session, config_db=db_session)
    assert tc.result_payload["_document"] == BLOCK
    assert tc.result_payload["filename"] == "cv.pdf"
    # the same dict the loop pops from, so the file never lands in the text
    assert result["data"] is tc.result_payload


def test_a_result_without_a_document_is_unchanged(db_session, monkeypatch):
    import app.agents.internal_tools as it
    from app.agents.tool_loop import _execute_tool_call

    monkeypatch.setattr(
        it,
        "get_handler",
        lambda c, a: lambda p, d, t: {"success": True, "data": {"x": 1}},
    )
    tc = _tool_call(db_session)
    _execute_tool_call(tc, db_session, config_db=db_session)
    assert tc.result_payload == {"x": 1}
