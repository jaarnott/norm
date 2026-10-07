"""A decision whose resume fails still lands, settled, with a message.

Until Oct 2026 a failure anywhere in ``resume_tool_loop`` surfaced as a 500
from /approve or /reject: the card said Approved, the thread stayed
in_progress, the client polled "Working..." for half an hour, and nobody was
told whether the changes had run. The known cause (a round mixing a write
with other calls) is fixed; this is the net for the next one.
"""

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.agents import tool_loop as TL
from app.agents.prompt_builder import approval_card_guidance
from app.db import engine as engine_mod
from app.db.models import Message, ToolCall
from app.services import approvals
from tests.conftest import _engine, _make_user
from tests.test_approval_decisions import _headers, _suspend, _thread


@pytest.fixture()
def db_session():
    """The net rolls the request session back, as production must (its
    uncommitted claim holds a lock on the thread row). With the default
    session that rollback would also undo the test's own transaction, so
    this module uses savepoints and each test commits its setup."""
    connection = _engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")()
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def owner(db_session):
    return _make_user(db_session, full_name="Owner")


@pytest.fixture()
def net_session(monkeypatch, db_session):
    """The net opens its own session; bind it to the test transaction."""
    monkeypatch.setattr(
        engine_mod, "SessionLocal", lambda: Session(bind=db_session.get_bind())
    )


def _last_assistant_text(db, thread_id):
    m = (
        db.query(Message)
        .filter(Message.thread_id == thread_id, Message.role == "assistant")
        .order_by(Message.created_at.desc())
        .first()
    )
    return m.content if m else None


class TestAFailedResumeLands:
    def test_a_crash_before_the_writes_marks_them_failed(
        self, client, db_session, owner, monkeypatch, net_session
    ):
        """Nothing ran: the pending rows are marked failed so a second click
        cannot run a write whose side effect might already stand, the thread
        settles, and the message says what to check."""
        thread = _thread(db_session, owner)
        (tc_id,) = _suspend(db_session, thread, [("manage_stock_item", {})])
        db_session.commit()

        def boom(*a, **k):
            raise RuntimeError("resume exploded")

        monkeypatch.setattr(TL, "resume_tool_loop", boom)

        r = client.post(f"/api/threads/{thread.id}/approve", headers=_headers(owner))

        assert r.status_code == 200, r.text
        db_session.expire_all()
        assert db_session.get(ToolCall, tc_id).status == "failed"
        thread = db_session.get(type(thread), thread.id)
        assert thread.status == "completed"
        assert thread.pending_tool_call_ids is None
        text = _last_assistant_text(db_session, thread.id)
        assert "0 of 1 changes ran" in text
        assert "will not run again" in text

    def test_a_crash_part_way_keeps_the_write_that_ran(
        self, client, db_session, owner, monkeypatch, net_session
    ):
        """Two approved writes; the first runs in Loaded, the second blows
        up. Nothing was committed, so without the pre-rollback read the
        record of the first would vanish. It stays executed; the second is
        marked failed, and the message counts them."""
        thread = _thread(db_session, owner)
        first, second = _suspend(
            db_session,
            thread,
            [("manage_stock_item", {"n": 1}), ("manage_stock_item", {"n": 2})],
        )
        db_session.commit()

        def execute(tc, db, config_db=None):
            if tc.id == second:
                raise RuntimeError("Loaded timed out")
            tc.status = "executed"
            tc.result_payload = {"ok": True}
            db.flush()
            return {"ok": True}

        monkeypatch.setattr(TL, "_execute_tool_call", execute)

        r = client.post(f"/api/threads/{thread.id}/approve", headers=_headers(owner))

        assert r.status_code == 200, r.text
        db_session.expire_all()
        assert db_session.get(ToolCall, first).status == "executed"
        assert db_session.get(ToolCall, second).status == "failed"
        assert "1 of 2 changes ran" in _last_assistant_text(db_session, thread.id)
        card = next(
            b["data"]
            for m in db_session.query(Message).filter(Message.thread_id == thread.id)
            for b in (m.display_blocks or [])
            if b.get("component") == "tool_approval"
        )
        outcomes = {row["id"]: row.get("outcome") for row in card["tool_calls"]}
        assert outcomes == {first: "done", second: "failed"}

    def test_a_crash_after_the_writes_says_they_were_made(
        self, client, db_session, owner, monkeypatch, net_session
    ):
        """The write ran and the resume committed; the continuation died.
        The changes stand, the thread settles, the message says so."""
        thread = _thread(db_session, owner)
        (tc_id,) = _suspend(db_session, thread, [("manage_stock_item", {})])
        db_session.commit()

        def execute(tc, db, config_db=None):
            # as the real one does: the row records that the write ran
            tc.status = "executed"
            db.flush()
            return {"ok": True}

        monkeypatch.setattr(TL, "_execute_tool_call", execute)

        def boom(*a, **k):
            raise RuntimeError("continuation exploded")

        monkeypatch.setattr(TL, "_execute_loop", boom)

        r = client.post(f"/api/threads/{thread.id}/approve", headers=_headers(owner))

        assert r.status_code == 200, r.text
        db_session.expire_all()
        assert db_session.get(ToolCall, tc_id).status == "executed"
        thread = db_session.get(type(thread), thread.id)
        assert thread.status == "completed"
        text = _last_assistant_text(db_session, thread.id)
        assert text == approvals.RESUME_FAILED_AFTER_WRITES

    def test_a_crash_on_reject_also_lands(
        self, client, db_session, owner, monkeypatch, net_session
    ):
        thread = _thread(db_session, owner)
        _suspend(db_session, thread, [("manage_stock_item", {})])
        db_session.commit()

        def boom(*a, **k):
            raise RuntimeError("resume exploded")

        monkeypatch.setattr(TL, "resume_tool_loop", boom)

        r = client.post(
            f"/api/threads/{thread.id}/reject",
            headers=_headers(owner),
            json={"notes": "no"},
        )

        assert r.status_code == 200, r.text
        db_session.expire_all()
        thread = db_session.get(type(thread), thread.id)
        assert thread.status == "completed"
        assert "decision was recorded" in _last_assistant_text(db_session, thread.id)


def test_the_prompt_asks_for_one_line_above_the_card():
    text = " ".join(approval_card_guidance().split())
    assert "shown above the approval card" in text
    assert "one short sentence" in text
