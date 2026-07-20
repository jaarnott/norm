"""A change of subject hands the conversation over — it does not split it.

`classify_followup` returns "new_thread" when a message belongs to a *different
domain agent* ("asking about inventory in an HR thread"). Historically that meant
abandoning the thread: build a new one, migrate the conversation into it, delete
the original. That migrate-and-delete was borrowed from `_migrate_prior_task`,
written for throwaway meta/unknown placeholder stubs — the comment at its call
site still says so — and it is what destroyed a live thread in the incident
covered by test_supervisor_venue_clarification.py.

`Thread.domain` is a column. Rebinding it keeps one conversation, gives the new
agent the real history rather than a 4-message summary blob, and carries the
venue across.

These tests pin the rebind AND every case where rebinding would be wrong, because
the failure modes are silent and expensive:

  * an agent with no bound tools ignores the thread_id it is handed and commits a
    thread of its own, so rebinding into one relabels this thread and then answers
    somewhere else;
  * `.mcp_playbook` / `.automated_conversation` / legacy order threads carry their
    identity in `intent`, and four separate call sites key off the
    `.automated_conversation` suffix to find the task a thread belongs to;
  * a thread with a tool approval pending holds the suspended loop in its own
    columns — moving on would take the pending write with it and leave it neither
    approvable nor rejectable.
"""

import pytest

from app.db.models import Message, Thread
from app.services import supervisor
from tests.conftest import _make_thread, _make_venue


class _StubAgent:
    """Stands in for a domain agent, recording what it was handed."""

    def __init__(self, tools=None, seen=None):
        self._tools = [{"name": "x"}] if tools is None else tools
        self.seen = seen if seen is not None else {}

    def get_tool_definitions(self, db, **kw):
        self.seen["venue_name"] = kw.get("active_venue_name")
        return ("system prompt", self._tools)

    def build_context(self, db, user_id):
        return {}

    def handle_message(self, message, db, user_id=None, thread_id=None, **kw):
        """The 'continue' path — the agent that already owns the thread."""
        self.seen["handled_in_thread"] = thread_id
        return {"id": thread_id, "message": message}


@pytest.fixture()
def no_quota_check(monkeypatch):
    monkeypatch.setattr(
        "app.services.billing_service.check_quota_for_user", lambda db, user_id: None
    )


@pytest.fixture()
def stub_loop(monkeypatch):
    """run_tool_loop that reports which thread and message it actually ran on."""
    monkeypatch.setattr(
        "app.agents.tool_loop.run_tool_loop",
        lambda message, thread, db, *a, **kw: {
            "id": thread.id,
            "message": message,
            "domain": thread.domain,
        },
    )


def _switch(db_session, user, monkeypatch, agent, *, thread, message):
    """Drive handle_message with a classifier that always says 'switch agent'."""
    monkeypatch.setattr(
        "app.agents.router.classify_followup",
        lambda *a, **kw: {"action": "new_thread", "domain": "reports"},
    )
    monkeypatch.setattr(
        supervisor, "get_agent", lambda domain: agent if domain == "reports" else agent
    )
    return supervisor.handle_message(
        message,
        db_session,
        config_db=db_session,
        user_id=user.id,
        thread_id=thread.id,
    )


class TestRebindsInPlace:
    def test_the_conversation_keeps_its_thread(
        self, db_session, admin_user, monkeypatch, no_quota_check, stub_loop
    ):
        venue = _make_venue(db_session, name="La Zwppa")
        thread = _make_thread(
            db_session,
            admin_user,
            domain="time_attendance",
            intent="time_attendance.tool_use",
            status="completed",
            raw_prompt="hours last week",
            venue_id=venue.id,
        )
        seen = {}
        result = _switch(
            db_session,
            admin_user,
            monkeypatch,
            _StubAgent(seen=seen),
            thread=thread,
            message="how were sales last week",
        )

        assert result["id"] == thread.id
        assert thread.domain == "reports"
        assert thread.intent == "reports.tool_use"
        # The venue the user already gave us comes along.
        assert seen["venue_name"] == "La Zwppa"

    def test_the_new_agent_gets_the_question_not_a_summary_blob(
        self, db_session, admin_user, monkeypatch, no_quota_check, stub_loop
    ):
        """The old path handed over 4 messages truncated to 100 chars each."""
        thread = _make_thread(
            db_session,
            admin_user,
            domain="time_attendance",
            intent="time_attendance.tool_use",
            status="completed",
        )
        db_session.add(
            Message(thread_id=thread.id, role="user", content="hours last week")
        )
        db_session.flush()

        result = _switch(
            db_session,
            admin_user,
            monkeypatch,
            _StubAgent(),
            thread=thread,
            message="how were sales last week",
        )

        assert result["message"] == "how were sales last week"
        assert "[Prior conversation]" not in result["message"]
        contents = [m.content for m in thread.messages]
        assert "how were sales last week" in contents
        assert not any(c.startswith("[Prior conversation]") for c in contents)


class TestRefusesToRebind:
    """Each of these must leave the thread alone and fall back to the old path."""

    def test_an_agent_with_no_tools(self, db_session, admin_user, monkeypatch):
        """A tool-less agent answers in a thread of its own, ignoring ours."""
        thread = _make_thread(
            db_session,
            admin_user,
            domain="time_attendance",
            intent="time_attendance.tool_use",
        )
        monkeypatch.setattr(supervisor, "get_agent", lambda d: _StubAgent(tools=[]))

        assert (
            supervisor._rebind_thread_agent(
                "reports", thread, "msg", db_session, db_session, admin_user.id
            )
            is None
        )
        assert thread.domain == "time_attendance"

    def test_an_automated_task_conversation(self, db_session, admin_user, monkeypatch):
        """Four call sites find a task by this intent suffix; rewriting it orphans them."""
        thread = _make_thread(
            db_session,
            admin_user,
            domain="procurement",
            intent="procurement.automated_conversation",
        )
        monkeypatch.setattr(supervisor, "get_agent", lambda d: _StubAgent())

        assert (
            supervisor._rebind_thread_agent(
                "reports", thread, "msg", db_session, db_session, admin_user.id
            )
            is None
        )
        assert thread.intent == "procurement.automated_conversation"

    def test_an_mcp_playbook_run(self, db_session, admin_user, monkeypatch):
        thread = _make_thread(
            db_session,
            admin_user,
            domain="reports",
            intent="reports.mcp_playbook",
        )
        monkeypatch.setattr(supervisor, "get_agent", lambda d: _StubAgent())

        assert (
            supervisor._rebind_thread_agent(
                "procurement", thread, "msg", db_session, db_session, admin_user.id
            )
            is None
        )
        assert thread.domain == "reports"

    def test_an_unregistered_domain(self, db_session, admin_user, monkeypatch):
        thread = _make_thread(
            db_session, admin_user, domain="reports", intent="reports.tool_use"
        )
        monkeypatch.setattr(supervisor, "get_agent", lambda d: None)

        assert (
            supervisor._rebind_thread_agent(
                "nonsense", thread, "msg", db_session, db_session, admin_user.id
            )
            is None
        )

    def test_the_same_domain(self, db_session, admin_user, monkeypatch):
        thread = _make_thread(
            db_session, admin_user, domain="reports", intent="reports.tool_use"
        )
        monkeypatch.setattr(supervisor, "get_agent", lambda d: _StubAgent())

        assert (
            supervisor._rebind_thread_agent(
                "reports", thread, "msg", db_session, db_session, admin_user.id
            )
            is None
        )


class TestPendingApprovalPinsTheThread:
    def test_a_thread_awaiting_approval_never_switches_agent(
        self, db_session, admin_user, monkeypatch, no_quota_check, stub_loop
    ):
        """Switching would migrate-and-delete the thread holding the pending write.

        The approval card carries the old thread id, so after a delete the write
        is neither approvable nor rejectable.
        """
        thread = _make_thread(
            db_session,
            admin_user,
            domain="time_attendance",
            intent="time_attendance.tool_use",
            status="awaiting_tool_approval",
        )
        thread.agent_loop_state = {
            "messages": [],
            "iteration": 1,
            "domain": "time_attendance",
        }
        db_session.flush()

        result = _switch(
            db_session,
            admin_user,
            monkeypatch,
            _StubAgent(),
            thread=thread,
            message="how were sales last week",
        )

        assert result["id"] == thread.id
        assert thread.domain == "time_attendance"
        assert (
            db_session.query(Thread).filter(Thread.id == thread.id).first() is not None
        )


class TestResumeUsesTheAgentThatSuspended:
    def test_saved_domain_wins_over_the_threads_current_domain(
        self, db_session, admin_user
    ):
        """A rebind mid-approval must not change which tools the resume runs with."""
        from app.routers.threads import _suspended_domain

        thread = _make_thread(
            db_session, admin_user, domain="reports", intent="reports.tool_use"
        )
        thread.agent_loop_state = {
            "messages": [],
            "iteration": 2,
            "domain": "procurement",
        }

        assert _suspended_domain(thread) == "procurement"

    def test_falls_back_to_the_thread_for_state_saved_before_this_change(
        self, db_session, admin_user
    ):
        from app.routers.threads import _suspended_domain

        thread = _make_thread(
            db_session, admin_user, domain="reports", intent="reports.tool_use"
        )
        thread.agent_loop_state = {"messages": [], "iteration": 2}

        assert _suspended_domain(thread) == "reports"
        thread.agent_loop_state = None
        assert _suspended_domain(thread) == "reports"
