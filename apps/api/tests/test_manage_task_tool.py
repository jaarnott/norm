"""manage_task: four task verbs behind one schema.

create_automated_task / update_automated_task / update_task_config /
set_override were four agent-facing tools for one object — a scheduled
task — carrying ~780 tokens of schema across four agents for 13 calls in
the 60 days to 21 Sep 2026, and they were the most confusable set in the
norm surface ("change how this task behaves" described three of them).

These pin the merge: every op still reaches the handler that always did
the work, an unknown op is refused with the menu rather than silently
doing nothing, and `op` itself never leaks into the handler's params.
"""

import uuid

import app.agents.internal_tools as it
from app.db.models import AutomatedTask, Thread


def _thread_with_task(db, task_config=None):
    """A task and the conversation it owns.

    The link lives on the TASK (`conversation_thread_id`), which is what
    `_get_automated_task_for_conversation` resolves.
    """
    thread = Thread(id=str(uuid.uuid4()), title="t", domain="procurement")
    db.add(thread)
    db.flush()
    task = AutomatedTask(
        id=str(uuid.uuid4()),
        title="Reconcile invoices",
        prompt="Reconcile received invoices",
        status="active",
        agent_slug="procurement",
        task_config=task_config or {},
        conversation_thread_id=thread.id,
    )
    db.add(task)
    db.flush()
    return thread, task


class TestDispatch:
    def test_set_config_reaches_the_task(self, db_session):
        thread, task = _thread_with_task(db_session)
        out = it.get_handler("norm", "manage_task")(
            {"op": "set_config", "key": "require_valid_po", "value": False},
            db_session,
            thread.id,
        )
        assert out["success"] is True
        db_session.refresh(task)
        assert task.task_config["require_valid_po"] is False

    def test_set_override_reaches_the_task(self, db_session):
        thread, task = _thread_with_task(db_session)
        out = it.get_handler("norm", "manage_task")(
            {"op": "set_override", "instruction": "skip Bidfood next run"},
            db_session,
            thread.id,
        )
        assert out["success"] is True
        db_session.refresh(task)
        assert task.overrides_next_run == {"instruction": "skip Bidfood next run"}

    def test_update_reaches_the_task_without_an_explicit_id(self, db_session):
        """The task is resolved from the conversation it owns. The prompt path
        once could not do this, so a live task got an updated config and a
        permanently stale prompt (see test_task_scheduler.py::
        TestUpdateTaskImplicitResolution for the refusal on an unrelated
        thread)."""
        thread, task = _thread_with_task(db_session)
        out = it.get_handler("norm", "manage_task")(
            {"op": "update", "prompt": "Reconcile, and email me the summary"},
            db_session,
            thread.id,
        )
        assert out["success"] is True
        db_session.refresh(task)
        assert task.prompt == "Reconcile, and email me the summary"

    def test_op_is_not_forwarded_to_the_handler(self, db_session):
        """`op` is this tool's own routing key. Leaking it into update's
        field loop would try to write a column that isn't there."""
        seen = {}

        def fake(params, db, thread_id):
            seen.update(params)
            return {"success": True, "data": {}}

        original = dict(it._TASK_OPS)
        it._TASK_OPS["update"] = fake
        try:
            it.get_handler("norm", "manage_task")(
                {"op": "update", "title": "New title"}, db_session, None
            )
        finally:
            it._TASK_OPS.clear()
            it._TASK_OPS.update(original)
        assert "op" not in seen
        assert seen == {"title": "New title"}


class TestRefusal:
    def test_unknown_op_names_the_menu(self, db_session):
        out = it.get_handler("norm", "manage_task")({"op": "delete"}, db_session, None)
        assert out["success"] is False
        for op in ("create", "update", "set_config", "set_override"):
            assert op in out["error"]

    def test_missing_op_is_refused_not_guessed(self, db_session):
        """No default: guessing 'create' here is how a duplicate draft
        gets made from a request to change an existing task."""
        out = it.get_handler("norm", "manage_task")(
            {"instruction": "skip Bidfood"}, db_session, None
        )
        assert out["success"] is False

    def test_every_op_maps_to_a_real_handler(self):
        assert set(it._TASK_OPS) == {"create", "update", "set_config", "set_override"}
        assert all(callable(fn) for fn in it._TASK_OPS.values())


class TestANewTaskCanEmail:
    def test_a_task_made_in_chat_gets_the_report_email_tool(
        self, db_session, monkeypatch
    ):
        """Creation used to add "send_email" — another tool (sending from a
        connected Gmail) — so a task made in chat could never email its
        summary (an hourly invoice task's first run, 20 Oct 2026)."""
        from app.db.models import ToolCall, User

        user = User(
            id=str(uuid.uuid4()),
            email=f"t-{uuid.uuid4().hex[:6]}@x.com",
            hashed_password="x",
            full_name="T",
            role="user",
        )
        db_session.add(user)
        thread = Thread(user_id=user.id, domain="norm", intent="x")
        db_session.add(thread)
        db_session.flush()
        db_session.add(
            ToolCall(
                id=str(uuid.uuid4()),
                thread_id=thread.id,
                iteration=1,
                tool_name="loadedhub__review_and_receive_invoices",
                connector_name="loadedhub",
                action="review_and_receive_invoices",
                method="GET",
                status="executed",
                input_params={},
            )
        )
        db_session.flush()
        import app.interpreter.llm_interpreter as li

        monkeypatch.setattr(
            li,
            "call_llm",
            lambda *a, **k: (
                {
                    "title": "Hourly receive",
                    "prompt": "receive",
                    "schedule_type": "hourly",
                    "schedule_config": {"interval_minutes": 60},
                },
                None,
            ),
        )
        out = it._create_automated_task(
            {"intent": "receive invoices every hour and email me"},
            db_session,
            thread.id,
        )
        task = db_session.query(AutomatedTask).filter_by(id=out["data"]["id"]).one()
        assert "send_report_email" in task.tool_filter
        assert "send_email" not in task.tool_filter
        assert "review_and_receive_invoices" in task.tool_filter
