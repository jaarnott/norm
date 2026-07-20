"""Tests for the external-trigger task scheduler.

Covers next_run_at computation, atomic claiming of due tasks, and the
authentication on the /internal/run-due-tasks endpoint.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.config import settings
from app.db.models import AutomatedTask
from app.services import task_scheduler


def _make_task(db, *, status="active", schedule_type="hourly", next_run_at=None):
    task = AutomatedTask(
        id=str(uuid.uuid4()),
        title="Scheduled Task",
        agent_slug="procurement",
        prompt="Do the thing",
        schedule_type=schedule_type,
        schedule_config={},
        status=status,
        next_run_at=next_run_at,
    )
    db.add(task)
    db.flush()
    return task


class TestComputeNextRunAt:
    def test_manual_never_fires(self):
        assert task_scheduler.compute_next_run_at("manual", {}) is None

    def test_hourly_is_one_hour_out(self):
        now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        nxt = task_scheduler.compute_next_run_at("hourly", {}, after=now)
        assert nxt == now + timedelta(hours=1)

    def test_naive_after_is_treated_as_utc(self):
        naive = datetime(2026, 1, 1, 12, 0)
        nxt = task_scheduler.compute_next_run_at("hourly", {}, after=naive)
        assert nxt.tzinfo is not None


class TestApplySchedule:
    def test_active_gets_next_run(self, db_session):
        """Assert *when*, not merely that something was set.

        `is not None` would pass just as happily on a next run computed in
        1970 — which the runner would then fire immediately and forever.
        """
        before = datetime.now(timezone.utc)
        task = _make_task(db_session, status="active", schedule_type="hourly")
        task.next_run_at = None
        task_scheduler.apply_schedule(task)

        assert task.next_run_at is not None
        nxt = task.next_run_at
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=timezone.utc)
        # Hourly, computed from "now": roughly an hour out, and in the future.
        assert nxt > before
        assert timedelta(minutes=59) <= (nxt - before) <= timedelta(minutes=61)

    def test_paused_clears_next_run(self, db_session):
        task = _make_task(
            db_session,
            status="paused",
            schedule_type="hourly",
            next_run_at=datetime.now(timezone.utc),
        )
        task_scheduler.apply_schedule(task)
        assert task.next_run_at is None

    def test_manual_active_has_no_next_run(self, db_session):
        task = _make_task(db_session, status="active", schedule_type="manual")
        task_scheduler.apply_schedule(task)
        assert task.next_run_at is None


class TestExecuteTaskNow:
    def test_config_db_is_threaded_into_the_tool_loop(self, db_session, admin_user):
        """execute_task_now must pass config_db through to run_tool_loop.

        Without it, _execute_tool_call raises "config_db is required" the moment
        an automated task invokes a connector tool — so the task fails every run
        while still looking correctly scheduled.
        """
        task = _make_task(db_session, schedule_type="daily")
        task.created_by = admin_user.id
        db_session.flush()

        agent = MagicMock()
        agent.get_tool_definitions.return_value = ("system prompt", [])
        agent.build_context.return_value = {}

        with (
            patch("app.agents.registry.get_agent", return_value=agent),
            patch("app.agents.tool_loop.run_tool_loop") as mock_loop,
            patch(
                "app.agents.context_builder.build_conversation_messages",
                return_value=[],
            ),
        ):
            mock_loop.return_value = {"message": "done", "tool_calls": []}
            task_scheduler.execute_task_now(task.id, mode="live", db=db_session)

        assert mock_loop.called, "run_tool_loop was never invoked"
        assert mock_loop.call_args.kwargs.get("config_db") is not None, (
            "config_db must be passed to run_tool_loop"
        )


class TestRunDueTasks:
    def test_due_task_is_claimed_and_advanced(self, db_session):
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        task = _make_task(db_session, next_run_at=past)

        with patch.object(task_scheduler, "execute_task_now") as mock_exec:
            result = task_scheduler.run_due_tasks(background=False, db=db_session)

        assert result["claimed"] == 1
        assert task.id in result["task_ids"]
        mock_exec.assert_called_once_with(task.id, mode="live")
        # next_run_at advanced into the future so it won't be re-claimed
        assert task.next_run_at > datetime.now(timezone.utc)

    def test_future_task_is_not_claimed(self, db_session):
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        _make_task(db_session, next_run_at=future)

        with patch.object(task_scheduler, "execute_task_now") as mock_exec:
            result = task_scheduler.run_due_tasks(background=False, db=db_session)

        assert result["claimed"] == 0
        mock_exec.assert_not_called()

    def test_paused_task_is_not_claimed(self, db_session):
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        _make_task(db_session, status="paused", next_run_at=past)

        with patch.object(task_scheduler, "execute_task_now") as mock_exec:
            result = task_scheduler.run_due_tasks(background=False, db=db_session)

        assert result["claimed"] == 0
        mock_exec.assert_not_called()


class TestRunDueEndpoint:
    def test_rejects_missing_secret(self, client, monkeypatch):
        monkeypatch.setattr(settings, "SCHEDULER_SECRET", "s3cret")
        resp = client.post("/internal/run-due-tasks")
        assert resp.status_code == 403

    def test_rejects_wrong_secret(self, client, monkeypatch):
        monkeypatch.setattr(settings, "SCHEDULER_SECRET", "s3cret")
        resp = client.post(
            "/internal/run-due-tasks", headers={"X-Scheduler-Secret": "nope"}
        )
        assert resp.status_code == 403

    def test_rejects_when_no_secret_configured(self, client, monkeypatch):
        # Fail closed: an unset secret rejects everything, even a blank header.
        monkeypatch.setattr(settings, "SCHEDULER_SECRET", "")
        resp = client.post(
            "/internal/run-due-tasks", headers={"X-Scheduler-Secret": ""}
        )
        assert resp.status_code == 403

    def test_accepts_correct_secret(self, client, monkeypatch):
        monkeypatch.setattr(settings, "SCHEDULER_SECRET", "s3cret")
        with patch(
            "app.services.task_scheduler.run_due_tasks",
            return_value={"claimed": 0, "task_ids": []},
        ):
            resp = client.post(
                "/internal/run-due-tasks", headers={"X-Scheduler-Secret": "s3cret"}
            )
        assert resp.status_code == 200
        assert resp.json()["claimed"] == 0


class TestRunOutcomeReachesTheConversation:
    """Every run must leave a trace in the task's conversation.

    A scheduled task is unattended: if a run produces nothing, or blows up, the
    only place the owner can notice is the task conversation. Previously the
    summary was posted only on the success path and only when the model
    returned text, so an empty or failed run was indistinguishable from a task
    that never fired at all.
    """

    def _run(self, db_session, admin_user, loop_result=None, loop_error=None):
        from app.db.models import Message, Thread

        task = _make_task(db_session, schedule_type="daily")
        task.created_by = admin_user.id
        conv = Thread(
            user_id=admin_user.id,
            domain="procurement",
            intent="procurement.automated_task_conversation",
            status="in_progress",
            raw_prompt="conversation",
        )
        db_session.add(conv)
        db_session.flush()
        task.conversation_thread_id = conv.id
        db_session.flush()

        agent = MagicMock()
        agent.get_tool_definitions.return_value = ("system prompt", [])
        agent.build_context.return_value = {}

        with (
            patch("app.agents.registry.get_agent", return_value=agent),
            patch("app.agents.tool_loop.run_tool_loop") as mock_loop,
            patch(
                "app.agents.context_builder.build_conversation_messages",
                return_value=[],
            ),
        ):
            if loop_error is not None:
                mock_loop.side_effect = loop_error
            else:
                mock_loop.return_value = loop_result
            task_scheduler.execute_task_now(task.id, mode="live", db=db_session)

        return [
            m.content
            for m in db_session.query(Message)
            .filter(Message.thread_id == conv.id, Message.role == "assistant")
            .all()
        ]

    def test_success_posts_the_summary(self, db_session, admin_user):
        posted = self._run(
            db_session,
            admin_user,
            loop_result={"message": "Reconciled 3 invoices", "tool_calls": []},
        )
        assert len(posted) == 1
        assert "Reconciled 3 invoices" in posted[0]
        assert "success" in posted[0]

    def test_empty_result_still_posts(self, db_session, admin_user):
        posted = self._run(
            db_session, admin_user, loop_result={"message": "", "tool_calls": []}
        )
        assert len(posted) == 1
        assert "no summary" in posted[0].lower()

    def test_failure_posts_the_error(self, db_session, admin_user):
        posted = self._run(
            db_session, admin_user, loop_error=RuntimeError("connector exploded")
        )
        assert len(posted) == 1
        assert "error" in posted[0].lower()
        assert "connector exploded" in posted[0]
