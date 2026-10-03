"""A scheduled run that needs an approval says so, tells its owner, and is
replaced by the next run rather than piling up.

Until Oct 2026 a scheduled run that reached a write which asks suspended
silently and was recorded as "success". In production, Dianna's La Zeppa
reconciliation stopped at its report email every day from 17 Jul to 8 Aug —
20 runs, each waiting on a card copied into a conversation nobody opened, and
not one email sent.
"""

import importlib.util
import pathlib
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.agents import tool_loop as TL
from app.auth.security import create_access_token
from app.db.models import (
    AutomatedTask,
    AutomatedTaskRun,
    Message,
    Thread,
    ToolCall,
)
from app.services import approvals, task_scheduler
from tests.conftest import (
    _make_membership,
    _make_organization,
    _make_thread,
    _make_user,
)


def _headers(user):
    return {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}


def _task(db, owner):
    conv = _make_thread(
        db,
        owner,
        domain="norm",
        intent="norm.automated_conversation",
        status="in_progress",
    )
    task = AutomatedTask(
        title="Invoice Reconciliation (All Venues)",
        agent_slug="norm",
        prompt="Reconcile and email me the report.",
        schedule_type="daily",
        schedule_config={"hour": 7, "minute": 0},
        status="active",
        created_by=owner.id,
        conversation_thread_id=conv.id,
    )
    db.add(task)
    db.flush()
    return task


def _asks(prompt, thread, db, *a, **k):
    """A tool loop that stops at the report email, as the real one does."""
    tc = ToolCall(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        iteration=1,
        tool_name="norm_email__send_report_email",
        connector_name="norm_email",
        action="send_report_email",
        method="GET",
        status="pending_approval",
        input_params={"to": "dianna@cbhg.co.nz"},
        preview={"title": "Email a report", "target": "Reconciliation — 4 Oct"},
    )
    db.add(tc)
    card = {
        "component": "tool_approval",
        "data": {
            "thread_id": thread.id,
            "status": "pending",
            "tool_calls": [{"id": tc.id, "summary": "Email a report"}],
        },
        "props": {},
    }
    db.add(
        Message(
            thread_id=thread.id,
            role="assistant",
            content="Reconciled 5 of 42. Sending you the report.",
            display_blocks=[card],
        )
    )
    thread.status = approvals.AWAITING
    thread.pending_tool_call_ids = [tc.id]
    thread.agent_loop_state = {"messages": [], "iteration": 1, "done_results": []}
    db.flush()
    return {"message": "Reconciled 5 of 42.", "display_blocks": [card]}


def _done(prompt, thread, db, *a, **k):
    return {"message": "Nothing to reconcile today."}


def _run(db, task, loop, unattended=True, mode="live"):
    agent = MagicMock()
    agent.get_tool_definitions.return_value = ("system prompt", [])
    agent.build_context.return_value = {}
    sent = []
    with (
        patch("app.agents.registry.get_agent", return_value=agent),
        patch("app.agents.tool_loop.run_tool_loop", side_effect=loop),
        patch(
            "app.agents.context_builder.build_conversation_messages", return_value=[]
        ),
        patch(
            "app.services.email_service.send_system_email",
            side_effect=lambda name, to, ctx, db, **k: (
                sent.append((name, to, ctx)) or "log-1"
            ),
        ),
    ):
        task_scheduler.execute_task_now(
            task.id, mode=mode, db=db, unattended=unattended
        )
    run = (
        db.query(AutomatedTaskRun)
        .filter(AutomatedTaskRun.automated_task_id == task.id)
        .order_by(AutomatedTaskRun.started_at.desc())
        .first()
    )
    return run, sent


@pytest.fixture()
def owner(db_session):
    return _make_user(db_session, full_name="Dianna Cook")


class TestARunThatAsksSaysSo:
    def test_it_is_recorded_as_waiting_not_success(self, db_session, owner):
        task = _task(db_session, owner)
        run, _ = _run(db_session, task, _asks)
        assert run.status == approvals.WAITING
        posted = (
            db_session.query(Message)
            .filter(Message.thread_id == task.conversation_thread_id)
            .one()
        )
        assert "⏸ waiting for your approval" in posted.content

    def test_its_owner_is_emailed_what_is_waiting(self, db_session, owner):
        task = _task(db_session, owner)
        _, sent = _run(db_session, task, _asks)
        ((name, to, ctx),) = sent
        assert name == "task_waiting"
        assert to == [owner.email]
        assert ctx["user_name"] == "Dianna"
        assert ctx["changes"] == ["Email a report: Reconciliation — 4 Oct"]
        # The link opens the task's conversation, where the card is.
        assert ctx["link"].endswith(f"?thread={task.conversation_thread_id}")

    def test_someone_watching_a_run_now_is_not_emailed(self, db_session, owner):
        task = _task(db_session, owner)
        run, sent = _run(db_session, task, _asks, unattended=False)
        assert run.status == approvals.WAITING
        assert sent == []

    def test_a_run_that_needs_nothing_is_a_plain_success(self, db_session, owner):
        task = _task(db_session, owner)
        run, sent = _run(db_session, task, _done)
        assert run.status == "success" and sent == []


class TestTheNextRunReplacesAWaitingOne:
    def test_the_old_changes_are_not_done_and_the_card_says_why(
        self, db_session, owner
    ):
        task = _task(db_session, owner)
        first, _ = _run(db_session, task, _asks)
        old_thread = db_session.get(Thread, first.thread_id)
        (old_call,) = (
            db_session.query(ToolCall).filter(ToolCall.thread_id == old_thread.id).all()
        )

        second, _ = _run(db_session, task, _done)

        assert first.status == "superseded"
        assert second.status == "success"
        assert old_thread.status == "completed"
        assert old_call.status == "superseded"
        card = next(
            b
            for m in db_session.query(Message).filter(
                Message.thread_id == old_thread.id
            )
            for b in (m.display_blocks or [])
            if b["component"] == "tool_approval"
        )
        assert card["data"]["status"] == "superseded"
        assert card["data"]["status_note"] == "a newer run replaced it"

    def test_a_test_run_leaves_a_waiting_live_run_alone(self, db_session, owner):
        task = _task(db_session, owner)
        first, _ = _run(db_session, task, _asks)
        _run(db_session, task, _done, mode="test")
        assert first.status == approvals.WAITING


@pytest.fixture()
def no_llm(monkeypatch):
    ran = []
    monkeypatch.setattr(
        TL,
        "_execute_tool_call",
        lambda tc, db, config_db=None: ran.append(tc.id) or {"ok": True},
    )
    monkeypatch.setattr(TL, "_execute_loop", lambda *a, **k: {"status": "completed"})
    return ran


class TestDecidingSettlesTheRun:
    def test_approved_is_success(self, client, db_session, owner, no_llm):
        task = _task(db_session, owner)
        run, _ = _run(db_session, task, _asks)
        r = client.post(
            f"/api/threads/{run.thread_id}/approve", headers=_headers(owner)
        )
        assert r.status_code == 200, r.text
        db_session.refresh(run)
        assert run.status == "success" and run.completed_at is not None

    def test_declined_is_declined(self, client, db_session, owner, no_llm):
        task = _task(db_session, owner)
        run, _ = _run(db_session, task, _asks)
        r = client.post(f"/api/threads/{run.thread_id}/reject", headers=_headers(owner))
        assert r.status_code == 200, r.text
        db_session.refresh(run)
        assert run.status == "declined"


class TestTheBoardShowsIt:
    def test_the_task_list_counts_waiting_runs(self, client, db_session, owner):
        task = _task(db_session, owner)
        _run(db_session, task, _asks)
        r = client.get("/api/automated-tasks", headers=_headers(owner))
        (row,) = [t for t in r.json()["tasks"] if t["id"] == task.id]
        assert row["waiting_for_approval"] == 1

    def test_the_conversation_carries_it_to_the_thread_list(
        self, client, db_session, admin_user
    ):
        task = _task(db_session, admin_user)
        _run(db_session, task, _asks)
        r = client.get(
            f"/api/threads/{task.conversation_thread_id}", headers=_headers(admin_user)
        )
        assert r.status_code == 200, r.text
        assert r.json()["automated_task"]["waiting_for_approval"] == 1


class TestATaskIsItsOrganisations:
    """Until Oct 2026 any signed-in user could list, run, edit or delete any
    organisation's scheduled tasks by id."""

    def _setup(self, db):
        org, other_org = _make_organization(db), _make_organization(db)
        owner, colleague, stranger = _make_user(db), _make_user(db), _make_user(db)
        _make_membership(db, owner, org)
        _make_membership(db, colleague, org)
        _make_membership(db, stranger, other_org)
        return _task(db, owner), colleague, stranger

    def test_a_stranger_cannot_see_or_touch_it(self, client, db_session):
        task, _, stranger = self._setup(db_session)
        h = _headers(stranger)
        assert task.id not in [
            t["id"]
            for t in client.get("/api/automated-tasks", headers=h).json()["tasks"]
        ]
        for method, path in (
            ("get", f"/api/automated-tasks/{task.id}"),
            ("get", f"/api/automated-tasks/{task.id}/runs"),
            ("post", f"/api/automated-tasks/{task.id}/pause"),
            ("delete", f"/api/automated-tasks/{task.id}"),
        ):
            assert getattr(client, method)(path, headers=h).status_code == 404, path
        assert db_session.get(AutomatedTask, task.id).status == "active"

    def test_the_owners_team_and_platform_admins_can(
        self, client, db_session, admin_headers
    ):
        task, colleague, _ = self._setup(db_session)
        assert (
            client.get(
                f"/api/automated-tasks/{task.id}", headers=_headers(colleague)
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/automated-tasks/{task.id}", headers=admin_headers
            ).status_code
            == 200
        )


class TestClearingTheStuckOnes:
    def _script(self):
        path = (
            pathlib.Path(__file__).resolve().parents[1]
            / "scripts"
            / "settle_stuck_task_runs.py"
        )
        spec = importlib.util.spec_from_file_location("settle_stuck", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_old_waiting_runs_are_found_and_recent_ones_left(self, db_session, owner):
        import datetime as dt

        task = _task(db_session, owner)
        run, _ = _run(db_session, task, _asks)
        thread = db_session.get(Thread, run.thread_id)
        module = self._script()
        assert module.stuck(db_session, older_than_days=2) == []
        thread.created_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
        db_session.flush()
        ((found_thread, found_run, found_task),) = module.stuck(
            db_session, older_than_days=2
        )
        assert (found_thread.id, found_run.id, found_task.id) == (
            thread.id,
            run.id,
            task.id,
        )
