"""Who may decide a suspended turn's writes, exactly once, and what follows.

Until Oct 2026 POST /threads/{id}/approve took any signed-in user and any thread
id; a double click could run a write twice; an approved call ran at whatever
venue the model named; a new message left the old card live (44 stock writes
stranded `pending_approval` in production, 2 Oct 2026); a scheduled run's card,
copied into the task conversation, approved the conversation and ran nothing;
and a turn that mixed reads with a write could not be resumed at all, because
the reads' results were dropped at suspension.
"""

import json
import uuid

import pytest

from app.agents import tool_loop as TL
from app.auth.security import create_access_token
from app.db.models import (
    Approval,
    AutomatedTask,
    AutomatedTaskRun,
    Message,
    ToolCall,
)
from app.services import approvals
from tests.conftest import (
    _make_membership,
    _make_organization,
    _make_thread,
    _make_user,
    _make_venue,
    _make_venue_access,
)


def _headers(user):
    return {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}


def _suspend(db, thread, calls, done_results=None):
    """Put ``thread`` in the state the loop leaves it in when it pauses."""
    ids = []
    for action, params in calls:
        tc = ToolCall(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            iteration=1,
            tool_name=f"loadedhub__{action}",
            connector_name="loadedhub",
            action=action,
            method="PUT",
            status="pending_approval",
            input_params=params,
        )
        db.add(tc)
        ids.append(tc.id)
    thread.status = approvals.AWAITING
    thread.pending_tool_call_ids = ids
    thread.agent_loop_state = {
        "messages": [],
        "iteration": 1,
        "done_results": done_results or [],
    }
    db.add(
        Message(
            thread_id=thread.id,
            role="assistant",
            content="I'd like to:\n\n- Manage Stock Item via loadedhub",
            display_blocks=[
                {
                    "component": "tool_approval",
                    "data": {
                        "thread_id": thread.id,
                        "status": "pending",
                        "tool_calls": [{"id": i} for i in ids],
                    },
                    "props": {},
                }
            ],
        )
    )
    db.flush()
    return ids


@pytest.fixture()
def owner(db_session):
    return _make_user(db_session, full_name="Owner")


@pytest.fixture()
def no_llm(monkeypatch):
    """Approved writes 'run' without Loaded; the continuation makes no LLM call."""
    ran = []

    def fake_execute(tc, db, config_db=None):
        ran.append(tc.id)
        return {"ok": True}

    monkeypatch.setattr(TL, "_execute_tool_call", fake_execute)
    monkeypatch.setattr(TL, "_execute_loop", lambda *a, **k: {"status": "completed"})
    return ran


def _thread(db, owner, intent="norm.tool_use"):
    return _make_thread(db, owner, domain="norm", intent=intent, status="in_progress")


class TestOnlyTheOwnerDecides:
    def test_someone_elses_thread_is_not_found(self, client, db_session, owner, no_llm):
        thread = _thread(db_session, owner)
        (tc_id,) = _suspend(db_session, thread, [("manage_stock_item", {})])
        stranger = _make_user(db_session)

        for verb in ("approve", "reject"):
            r = client.post(
                f"/api/threads/{thread.id}/{verb}", headers=_headers(stranger)
            )
            assert r.status_code == 404
        assert db_session.get(ToolCall, tc_id).status == "pending_approval"
        assert no_llm == []

    def test_the_owner_approves_and_the_write_runs(
        self, client, db_session, owner, no_llm
    ):
        thread = _thread(db_session, owner)
        (tc_id,) = _suspend(db_session, thread, [("manage_stock_item", {})])

        r = client.post(f"/api/threads/{thread.id}/approve", headers=_headers(owner))

        assert r.status_code == 200, r.text
        assert no_llm == [tc_id]
        assert db_session.get(ToolCall, tc_id).status == "approved"
        row = db_session.query(Approval).filter(Approval.thread_id == thread.id).one()
        assert row.action == "tool_calls_approved"

    def test_a_platform_admin_may_decide(
        self, client, db_session, owner, admin_user, no_llm
    ):
        thread = _thread(db_session, owner)
        _suspend(db_session, thread, [("manage_stock_item", {})])
        r = client.post(
            f"/api/threads/{thread.id}/approve", headers=_headers(admin_user)
        )
        assert r.status_code == 200, r.text


class TestExactlyOnce:
    def test_a_second_click_is_refused_and_nothing_runs_twice(
        self, client, db_session, owner, no_llm
    ):
        thread = _thread(db_session, owner)
        _suspend(db_session, thread, [("manage_stock_item", {})])

        first = client.post(
            f"/api/threads/{thread.id}/approve", headers=_headers(owner)
        )
        second = client.post(
            f"/api/threads/{thread.id}/approve", headers=_headers(owner)
        )

        assert first.status_code == 200
        assert second.status_code == 409
        assert len(no_llm) == 1


class TestVenueAccess:
    def test_a_call_at_a_venue_the_approver_cannot_act_on_is_declined(
        self, client, db_session, owner, no_llm
    ):
        org = _make_organization(db_session)
        _make_membership(db_session, owner, org)
        mine = _make_venue(db_session, name="La Zeppa", organization_id=org.id)
        theirs = _make_venue(db_session, name="The Glass Goose", organization_id=org.id)
        _make_venue_access(db_session, owner, mine)
        thread = _thread(db_session, owner)
        ok_id, blocked_id = _suspend(
            db_session,
            thread,
            [
                ("manage_stock_item", {"venue": mine.name}),
                ("manage_stock_item", {"venue": theirs.name}),
            ],
        )

        r = client.post(f"/api/threads/{thread.id}/approve", headers=_headers(owner))

        assert r.status_code == 200, r.text
        assert no_llm == [ok_id]
        blocked = db_session.get(ToolCall, blocked_id)
        assert blocked.status == "rejected"
        assert "The Glass Goose" in blocked.error_message


class TestRejectKeepsTheReason:
    def test_notes_land_on_the_approval_row(self, client, db_session, owner, no_llm):
        thread = _thread(db_session, owner)
        _suspend(db_session, thread, [("manage_stock_item", {})])
        r = client.post(
            f"/api/threads/{thread.id}/reject",
            headers=_headers(owner),
            json={"notes": "wrong supplier"},
        )
        assert r.status_code == 200, r.text
        row = db_session.query(Approval).filter(Approval.thread_id == thread.id).one()
        assert (row.action, row.notes) == ("tool_calls_rejected", "wrong supplier")
        assert no_llm == []


class TestANewMessageSupersedes:
    def test_pending_calls_are_declined_and_the_card_stops_being_live(
        self, db_session, owner
    ):
        thread = _thread(db_session, owner)
        (tc_id,) = _suspend(db_session, thread, [("manage_stock_item", {})])

        assert approvals.supersede(db_session, thread, owner.id) is True
        db_session.flush()

        assert db_session.get(ToolCall, tc_id).status == "superseded"
        assert thread.status == "in_progress"
        assert thread.pending_tool_call_ids is None
        assert thread.agent_loop_state is None
        msg = db_session.query(Message).filter(Message.thread_id == thread.id).one()
        assert msg.display_blocks[0]["data"]["status"] == "superseded"
        assert approvals.SUPERSEDED_NOTE in msg.content

    def test_a_later_approval_leaves_the_superseded_card_alone(self, db_session, owner):
        thread = _thread(db_session, owner)
        _suspend(db_session, thread, [("manage_stock_item", {})])
        approvals.supersede(db_session, thread, owner.id)
        new_ids = _suspend(db_session, thread, [("manage_menu", {})])

        approvals.set_card_status(db_session, thread, new_ids, "approved")

        statuses = [
            m.display_blocks[0]["data"]["status"]
            for m in db_session.query(Message)
            .filter(Message.thread_id == thread.id)
            .order_by(Message.created_at)
        ]
        assert statuses == ["superseded", "approved"]

    def test_sending_a_message_supersedes(self, db_session, owner, monkeypatch):
        from app.agents.norm import NormAgent

        thread = _thread(db_session, owner)
        (tc_id,) = _suspend(db_session, thread, [("manage_stock_item", {})])
        agent = NormAgent()
        monkeypatch.setattr(agent, "get_tool_definitions", lambda *a, **k: ("sys", []))
        monkeypatch.setattr(
            TL, "run_tool_loop", lambda *a, **k: {"status": "completed"}
        )

        agent.handle_message_with_tools(
            "actually, never mind",
            db_session,
            owner.id,
            thread.id,
            config_db=db_session,
        )

        assert db_session.get(ToolCall, tc_id).status == "superseded"
        assert thread.status == "in_progress"

    def test_nothing_waiting_is_a_no_op(self, db_session, owner):
        thread = _thread(db_session, owner)
        assert approvals.supersede(db_session, thread, owner.id) is False


class TestResumeAnswersEveryCall:
    def test_reads_from_the_same_response_survive_the_pause(
        self, db_session, owner, monkeypatch
    ):
        thread = _thread(db_session, owner)
        read_result = {
            "type": "tool_result",
            "tool_use_id": "toolu_read",
            "content": json.dumps({"rows": 3}),
        }
        approved_id, rejected_id = _suspend(
            db_session,
            thread,
            [("manage_stock_item", {}), ("manage_stock_item", {})],
            done_results=[read_result],
        )
        thread.pending_tool_call_ids = [approved_id, rejected_id, "toolu_gone"]
        db_session.get(ToolCall, approved_id).status = "approved"
        rejected = db_session.get(ToolCall, rejected_id)
        rejected.status = "rejected"
        rejected.error_message = "Not run: no access to The Glass Goose."
        db_session.flush()
        monkeypatch.setattr(
            TL, "_execute_tool_call", lambda tc, db, config_db=None: {"ok": 1}
        )
        seen = {}

        def capture(messages, *a, **k):
            seen["messages"] = messages
            return {"status": "completed"}

        monkeypatch.setattr(TL, "_execute_loop", capture)

        TL.resume_tool_loop(thread, db_session, "sys", [], config_db=None)

        results = {r["tool_use_id"]: r for r in seen["messages"][-1]["content"]}
        assert set(results) == {"toolu_read", approved_id, rejected_id, "toolu_gone"}
        assert results["toolu_read"] == read_result
        assert "The Glass Goose" in results[rejected_id]["content"]
        assert json.loads(results["toolu_gone"]["content"])["status"] == "not_run"


class TestScheduledRunCards:
    def test_deciding_the_run_updates_the_copy_and_posts_the_outcome(
        self, client, db_session, owner, monkeypatch
    ):
        conversation = _thread(db_session, owner, intent="norm.automated_conversation")
        task = AutomatedTask(
            id=str(uuid.uuid4()),
            title="Invoice Reconciliation",
            agent_slug="norm",
            prompt="reconcile",
            schedule_type="daily",
            schedule_config={},
            status="active",
            created_by=owner.id,
            conversation_thread_id=conversation.id,
        )
        db_session.add(task)
        run_thread = _thread(db_session, owner, intent="norm.automated_task")
        db_session.add(
            AutomatedTaskRun(
                automated_task_id=task.id, thread_id=run_thread.id, status="success"
            )
        )
        (tc_id,) = _suspend(db_session, run_thread, [("manage_stock_item", {})])
        # The scheduler copies the run's card into the conversation.
        card = (
            db_session.query(Message).filter(Message.thread_id == run_thread.id).one()
        )
        db_session.add(
            Message(
                thread_id=conversation.id,
                role="assistant",
                content="Run the scheduled task",
                display_blocks=json.loads(json.dumps(card.display_blocks)),
            )
        )
        db_session.flush()
        monkeypatch.setattr(
            TL, "_execute_tool_call", lambda tc, db, config_db=None: {"ok": 1}
        )

        def finish(messages, task_, db, *a, **k):
            db.add(
                Message(thread_id=task_.id, role="assistant", content="Updated 1 item.")
            )
            db.flush()
            return {"status": "completed"}

        monkeypatch.setattr(TL, "_execute_loop", finish)

        r = client.post(
            f"/api/threads/{run_thread.id}/approve", headers=_headers(owner)
        )

        assert r.status_code == 200, r.text
        convo = (
            db_session.query(Message)
            .filter(Message.thread_id == conversation.id)
            .order_by(Message.created_at)
            .all()
        )
        assert convo[0].display_blocks[0]["data"]["status"] == "approved"
        assert convo[-1].content == "Updated 1 item."
