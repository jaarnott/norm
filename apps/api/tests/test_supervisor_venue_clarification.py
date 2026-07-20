"""Supervisor: answering a venue question must not cost the user their thread.

Incident (dev thread ff5ae525, 2026-07-20). A user asked a time & attendance
question with no venue, answered the venue picker with "La Zwppa", got an
answer, then asked a second, unrelated question about sales. What came back
answered the *first* question, both turns vanished from the screen, and the
stream raised:

    ForeignKeyViolation: update or delete on table "threads" violates foreign
    key constraint "tool_calls_task_id_fkey" on table "tool_calls"

Three defects chained together:

1. The venue-clarification handler sat *after* the follow-up classifier, so it
   could only ever run once the classifier had already ruled the message a new
   topic — i.e. exactly when the message was definitely not a venue name. By
   then `message` had been rewritten to "[Prior conversation]\n...\n\n[New
   request]\n...", and `resolve_venue` does a bidirectional substring match, so
   the venue quoted in that blob from the earlier turn matched.
2. Having "resolved" a venue, the handler replaced the user's message with
   `thread.raw_prompt` — replaying question one.
3. Nothing ever cleared `Thread.intent`, so a thread that had once asked for a
   venue stayed armed forever, and the tidy-up that followed deleted it while
   its tool_calls still pointed at it.
"""

import uuid

import pytest

from app.db.models import Approval, Message, Thread, ToolCall
from app.services import supervisor
from tests.conftest import _make_thread, _make_venue


def _clarification_thread(db_session, user, *, prompt, domain="time_attendance"):
    """A thread parked on the venue picker, as _create_venue_clarification leaves it."""
    thread = _make_thread(
        db_session,
        user,
        domain=domain,
        intent="venue_clarification",
        status="needs_clarification",
        raw_prompt=prompt,
    )
    thread.missing_fields = ["venue"]
    thread.clarification_question = "Which venue would you like me to look at?"
    db_session.add(Message(thread_id=thread.id, role="user", content=prompt))
    db_session.add(
        Message(
            thread_id=thread.id,
            role="assistant",
            content=thread.clarification_question,
        )
    )
    db_session.flush()
    return thread


def _texts(db_session, thread_id):
    return [
        m.content
        for m in db_session.query(Message)
        .filter(Message.thread_id == thread_id)
        .order_by(Message.created_at)
        .all()
    ]


class TestResumeVenueClarification:
    def test_resolved_venue_is_recorded_on_the_thread(self, db_session, admin_user):
        """The venue the user picked has to outlive the turn that picked it.

        Thread.venue_id is what supervisor.handle_message reads for every
        later message in the thread. Before this fix nothing ever wrote it, so
        Norm asked again — or worse, guessed — on the next question.
        """
        venue = _make_venue(db_session, name="La Zwppa")
        thread = _clarification_thread(
            db_session, admin_user, prompt="How many hours did Rendi work last week?"
        )

        assert (
            supervisor._resume_venue_clarification("La Zwppa", thread, db_session)
            is None
        )
        assert thread.venue_id == venue.id

    def test_thread_stands_down_from_clarification(self, db_session, admin_user):
        """Once answered, the thread must stop being a venue question.

        This is the flag that made the incident recur: intent stayed
        "venue_clarification" forever, so every later message re-entered the
        handler and got read as another venue name.
        """
        _make_venue(db_session, name="La Zwppa")
        thread = _clarification_thread(
            db_session, admin_user, prompt="How many hours did Rendi work last week?"
        )

        supervisor._resume_venue_clarification("La Zwppa", thread, db_session)

        assert thread.intent == "time_attendance.tool_use"
        assert thread.status == "in_progress"
        assert thread.missing_fields == []
        assert thread.clarification_question is None

    def test_all_venues_clears_the_flag_without_a_venue(self, db_session, admin_user):
        _make_venue(db_session, name="La Zwppa")
        thread = _clarification_thread(
            db_session, admin_user, prompt="How were sales last week?"
        )

        assert (
            supervisor._resume_venue_clarification("all venues", thread, db_session)
            is None
        )
        assert thread.venue_id is None
        assert thread.intent != "venue_clarification"

    def test_reply_is_recorded_once(self, db_session, admin_user):
        _make_venue(db_session, name="La Zwppa")
        thread = _clarification_thread(
            db_session, admin_user, prompt="How many hours did Rendi work last week?"
        )

        supervisor._resume_venue_clarification("La Zwppa", thread, db_session)

        assert _texts(db_session, thread.id).count("La Zwppa") == 1

    def test_unknown_venue_asks_again_without_duplicating_the_reply(
        self, db_session, admin_user
    ):
        """The re-ask used to store the user's message twice."""
        _make_venue(db_session, name="La Zwppa")
        thread = _clarification_thread(
            db_session, admin_user, prompt="How many hours did Rendi work last week?"
        )

        result = supervisor._resume_venue_clarification(
            "Nowhere Bar", thread, db_session
        )

        assert result is not None
        assert result["status"] == "needs_clarification"
        assert _texts(db_session, thread.id).count("Nowhere Bar") == 1
        assert thread.intent == "venue_clarification"


class TestHandleMessageResumesInPlace:
    """The end-to-end shape of the incident, minus the LLM."""

    @pytest.fixture()
    def no_quota_check(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.billing_service.check_quota_for_user",
            lambda db, user_id: None,
        )

    def test_venue_reply_answers_the_original_question_in_the_same_thread(
        self, db_session, admin_user, monkeypatch, no_quota_check
    ):
        """No new thread, no migration, no delete — and the right question.

        The follow-up classifier must not see the reply at all: it reads a bare
        venue name as a topic change, which is what set the whole chain off.
        """
        venue = _make_venue(db_session, name="La Zwppa")
        thread = _clarification_thread(
            db_session, admin_user, prompt="How many hours did Rendi work last week?"
        )

        def _boom(*a, **kw):  # pragma: no cover - fails the test if reached
            raise AssertionError("classify_followup must not see a venue reply")

        monkeypatch.setattr("app.agents.router.classify_followup", _boom)
        monkeypatch.setattr("app.agents.router.classify", _boom)

        seen = {}

        class _StubAgent:
            def get_tool_definitions(self, db, **kw):
                seen["venue_name"] = kw.get("active_venue_name")
                return ("system", [])

            def build_context(self, db, user_id):
                return {}

        monkeypatch.setattr(supervisor, "get_agent", lambda domain: _StubAgent())
        monkeypatch.setattr(
            "app.agents.tool_loop.run_tool_loop",
            lambda message, thread, db, *a, **kw: {
                "id": thread.id,
                "message": message,
            },
        )

        result = supervisor.handle_message(
            "La Zwppa",
            db_session,
            config_db=db_session,
            user_id=admin_user.id,
            thread_id=thread.id,
        )

        assert result["message"] == "How many hours did Rendi work last week?"
        assert result["id"] == thread.id
        assert seen["venue_name"] == "La Zwppa"
        assert (
            db_session.query(Thread).filter(Thread.id == thread.id).first() is not None
        )
        assert thread.venue_id == venue.id


class TestRestoreUserText:
    """A topic change must not leave the user's question replaced by a transcript.

    On a topic change the router is handed the request wrapped in a
    "[Prior conversation] ..." summary, and the agent persists whatever it was
    given — so the thread showed the blob where the user's own words belonged.
    """

    def test_the_users_words_replace_the_router_blob(self, db_session, admin_user):
        real = "can we check if we did higher sales when staff went over rostered hours"
        blob = f"[Prior conversation]\nUser: something earlier\n\n[New request]\n{real}"
        thread = _make_thread(
            db_session, admin_user, domain="reports", intent="reports", raw_prompt=blob
        )
        db_session.add(Message(thread_id=thread.id, role="user", content=blob))
        db_session.flush()

        supervisor._restore_user_text(thread.id, blob, real, db_session)

        assert _texts(db_session, thread.id) == [real]
        assert thread.raw_prompt == real

    def test_leaves_the_thread_alone_when_nothing_was_rewritten(
        self, db_session, admin_user
    ):
        thread = _make_thread(
            db_session, admin_user, domain="reports", intent="reports", raw_prompt="hi"
        )
        db_session.add(Message(thread_id=thread.id, role="user", content="hi"))
        db_session.flush()

        supervisor._restore_user_text(thread.id, "hi", None, db_session)

        assert _texts(db_session, thread.id) == ["hi"]


class TestMigratePriorThread:
    def test_is_a_no_op_when_the_thread_is_already_the_target(
        self, db_session, admin_user
    ):
        """Guarding this is what stops the merge deleting the thread it just answered."""
        thread = _make_thread(db_session, admin_user, domain="meta", intent="meta")
        db_session.add(Message(thread_id=thread.id, role="user", content="hi"))
        db_session.flush()

        supervisor._migrate_prior_thread(thread, thread.id, db_session)

        assert (
            db_session.query(Thread).filter(Thread.id == thread.id).first() is not None
        )
        assert _texts(db_session, thread.id) == ["hi"]

    def test_tool_calls_move_with_the_conversation(self, db_session, admin_user):
        """tool_calls were the rows that blocked the delete and raised in production."""
        old = _make_thread(db_session, admin_user, domain="meta", intent="meta")
        new = _make_thread(db_session, admin_user, domain="reports", intent="reports")
        db_session.add(Message(thread_id=old.id, role="user", content="hi"))
        db_session.add(
            ToolCall(
                id=str(uuid.uuid4()),
                thread_id=old.id,
                iteration=1,
                tool_name="loadedhub__get_sales",
                connector_name="loadedhub",
                action="get_sales",
                method="GET",
                status="executed",
            )
        )
        db_session.flush()

        supervisor._migrate_prior_thread(old, new.id, db_session)

        assert (
            db_session.query(ToolCall).filter(ToolCall.thread_id == new.id).count() == 1
        )
        assert (
            db_session.query(ToolCall).filter(ToolCall.thread_id == old.id).count() == 0
        )
        assert db_session.query(Thread).filter(Thread.id == old.id).first() is None

    def test_a_blocking_child_row_keeps_the_thread_instead_of_raising(
        self, db_session, admin_user
    ):
        """Twelve tables reference threads.id and this only re-parents three.

        A tidy-up step must never cost the user the answer they just waited
        for, so the delete runs inside a SAVEPOINT: if some other table still
        points at the thread, we keep the thread and the migration still
        commits.
        """
        old = _make_thread(db_session, admin_user, domain="meta", intent="meta")
        new = _make_thread(db_session, admin_user, domain="reports", intent="reports")
        db_session.add(Message(thread_id=old.id, role="user", content="hi"))
        db_session.add(
            Approval(id=str(uuid.uuid4()), thread_id=old.id, action="approved")
        )
        db_session.flush()

        supervisor._migrate_prior_thread(old, new.id, db_session)

        assert db_session.query(Thread).filter(Thread.id == old.id).first() is not None
        assert _texts(db_session, new.id) == ["hi"]
