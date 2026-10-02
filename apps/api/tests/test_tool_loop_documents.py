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


# ---------------------------------------------------------------------------
# A consolidator's file, made model-ready (Oct 2026). BambooHR hands back a
# Word CV as wordprocessingml; the model reads only PDFs and common images, so
# attaching it as-is failed the NEXT model call and lost the whole turn.

_WORD = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _b64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode()


def _docx(text: str) -> bytes:
    import io

    import docx

    d = docx.Document()
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _doc(media_type, data):
    return {
        "type": "document",
        "source": {"type": "base64", "media_type": media_type, "data": _b64(data)},
    }


class TestModelReadyBlock:
    def test_a_word_cv_becomes_text(self):
        from app.services.attachments import model_ready_block

        out = model_ready_block(_doc(_WORD, _docx("Head chef, 8 years, Auckland")))
        assert out["type"] == "text"
        assert "Head chef, 8 years, Auckland" in out["text"]

    def test_a_pdf_stays_a_document(self):
        from app.services.attachments import model_ready_block

        out = model_ready_block(_doc("application/pdf", b"%PDF-1.4 x"))
        assert out["type"] == "document"
        assert out["source"]["media_type"] == "application/pdf"

    def test_a_generic_type_is_sniffed_from_the_bytes(self):
        from app.services.attachments import model_ready_block

        pdf = model_ready_block(_doc("application/octet-stream", b"%PDF-1.4 x"))
        assert pdf["type"] == "document"
        assert pdf["source"]["media_type"] == "application/pdf"
        word = model_ready_block(_doc("application/octet-stream", _docx("Barista")))
        assert word["type"] == "text" and "Barista" in word["text"]

    def test_an_unreadable_file_becomes_a_sentence_not_a_broken_request(self):
        from app.services.attachments import model_ready_block

        out = model_ready_block(_doc("application/zip", b"\x00\x01\x02"))
        assert out["type"] == "text" and "couldn't be read" in out["text"]

    def test_a_text_block_passes_through(self):
        from app.services.attachments import model_ready_block

        block = {"type": "text", "text": "[Attachment: notes.txt]\nhi"}
        assert model_ready_block(block) is block


class TestLiftDocument:
    def test_both_copies_lose_the_file_after_a_parallel_batch(self, db_session):
        """After a parallel batch, tc was re-read from the DB, so the result the
        loop serialises for the model is a DIFFERENT dict — the file has to come
        off it too, or its base64 lands in the model's text."""
        from app.agents.tool_loop import _lift_document

        tc = _tool_call(db_session)
        tc.result_payload = {"name": "Glenn", "_document": dict(BLOCK)}
        result = {"success": True, "data": {"name": "Glenn", "_document": dict(BLOCK)}}
        doc = _lift_document(tc, result, db_session)
        assert doc["type"] == "document"
        assert doc["source"]["media_type"] == "application/pdf"
        assert "_document" not in tc.result_payload
        assert "_document" not in result["data"]

    def test_the_single_call_path_shares_one_dict(self, db_session):
        from app.agents.tool_loop import _lift_document

        tc = _tool_call(db_session)
        tc.result_payload = {"name": "Glenn", "_document": dict(BLOCK)}
        result = {"success": True, "data": tc.result_payload}
        assert _lift_document(tc, result, db_session)["type"] == "document"
        assert "_document" not in result["data"]

    def test_no_file_no_block(self, db_session):
        from app.agents.tool_loop import _lift_document

        tc = _tool_call(db_session)
        tc.result_payload = {"x": 1}
        assert _lift_document(tc, {"data": {"x": 1}}, db_session) is None
