"""Who may decide a suspended turn's writes, exactly once, and what follows.

Until Oct 2026 POST /threads/{id}/approve took any signed-in user and any thread
id; a double click could run a write twice; an approved call ran at whatever
venue the model named; a new message left the old card live (44 stock writes
stranded `pending_approval` in production, 2 Oct 2026); a scheduled run's card,
copied into the task conversation, approved the conversation and ran nothing;
and a turn that mixed reads with a write could not be resumed at all, because
the reads' results were dropped at suspension.

Folded in (Oct 2026): test_approval_failure_net (a failed resume still lands),
test_approval_resume_durability (the approval commits before the
continuation) and test_mixed_round_resume (the 2–3 Oct mixed-round incident).
"""

import json
import uuid

import pytest

from sqlalchemy.orm import Session, sessionmaker

from app.agents import tool_loop as TL
from app.agents.prompt_builder import approval_card_guidance
from app.db import engine as engine_mod
from app.db.models import (
    Approval,
    AutomatedTask,
    AutomatedTaskRun,
    Message,
    ToolCall,
)
from app.services import approvals
from tests.approval_helpers import headers, owner_thread, suspend
from tests.conftest import (
    _engine,
    _make_membership,
    _make_organization,
    _make_thread,
    _make_user,
    _make_venue,
    _make_venue_access,
)
from tests.tool_loop_harness import ScriptedLlm, end_turn, tool, tool_round, tool_use


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


class TestOnlyTheOwnerDecides:
    def test_someone_elses_thread_is_not_found(self, client, db_session, owner, no_llm):
        thread = owner_thread(db_session, owner)
        (tc_id,) = suspend(db_session, thread, [("manage_stock_item", {})])
        stranger = _make_user(db_session)

        for verb in ("approve", "reject"):
            r = client.post(
                f"/api/threads/{thread.id}/{verb}", headers=headers(stranger)
            )
            assert r.status_code == 404
        assert db_session.get(ToolCall, tc_id).status == "pending_approval"
        assert no_llm == []

    def test_the_owner_approves_and_the_write_runs(
        self, client, db_session, owner, no_llm
    ):
        thread = owner_thread(db_session, owner)
        (tc_id,) = suspend(db_session, thread, [("manage_stock_item", {})])

        r = client.post(f"/api/threads/{thread.id}/approve", headers=headers(owner))

        assert r.status_code == 200, r.text
        assert no_llm == [tc_id]
        assert db_session.get(ToolCall, tc_id).status == "approved"
        row = db_session.query(Approval).filter(Approval.thread_id == thread.id).one()
        assert row.action == "tool_calls_approved"

    def test_a_platform_admin_may_decide(
        self, client, db_session, owner, admin_user, no_llm
    ):
        thread = owner_thread(db_session, owner)
        suspend(db_session, thread, [("manage_stock_item", {})])
        r = client.post(
            f"/api/threads/{thread.id}/approve", headers=headers(admin_user)
        )
        assert r.status_code == 200, r.text


class TestExactlyOnce:
    def test_a_second_click_is_refused_and_nothing_runs_twice(
        self, client, db_session, owner, no_llm
    ):
        thread = owner_thread(db_session, owner)
        suspend(db_session, thread, [("manage_stock_item", {})])

        first = client.post(f"/api/threads/{thread.id}/approve", headers=headers(owner))
        second = client.post(
            f"/api/threads/{thread.id}/approve", headers=headers(owner)
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
        thread = owner_thread(db_session, owner)
        ok_id, blocked_id = suspend(
            db_session,
            thread,
            [
                ("manage_stock_item", {"venue": mine.name}),
                ("manage_stock_item", {"venue": theirs.name}),
            ],
        )

        r = client.post(f"/api/threads/{thread.id}/approve", headers=headers(owner))

        assert r.status_code == 200, r.text
        assert no_llm == [ok_id]
        blocked = db_session.get(ToolCall, blocked_id)
        assert blocked.status == "rejected"
        assert "The Glass Goose" in blocked.error_message


class TestRejectKeepsTheReason:
    def test_notes_land_on_the_approval_row(self, client, db_session, owner, no_llm):
        thread = owner_thread(db_session, owner)
        suspend(db_session, thread, [("manage_stock_item", {})])
        r = client.post(
            f"/api/threads/{thread.id}/reject",
            headers=headers(owner),
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
        thread = owner_thread(db_session, owner)
        (tc_id,) = suspend(db_session, thread, [("manage_stock_item", {})])

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
        thread = owner_thread(db_session, owner)
        suspend(db_session, thread, [("manage_stock_item", {})])
        approvals.supersede(db_session, thread, owner.id)
        new_ids = suspend(db_session, thread, [("manage_menu", {})])

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

        thread = owner_thread(db_session, owner)
        (tc_id,) = suspend(db_session, thread, [("manage_stock_item", {})])
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
        thread = owner_thread(db_session, owner)
        assert approvals.supersede(db_session, thread, owner.id) is False


class TestResumeAnswersEveryCall:
    def test_reads_from_the_same_response_survive_the_pause(
        self, db_session, owner, monkeypatch
    ):
        thread = owner_thread(db_session, owner)
        read_result = {
            "type": "tool_result",
            "tool_use_id": "toolu_read",
            "content": json.dumps({"rows": 3}),
        }
        approved_id, rejected_id = suspend(
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
        conversation = owner_thread(
            db_session, owner, intent="norm.automated_conversation"
        )
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
        run_thread = owner_thread(db_session, owner, intent="norm.automated_task")
        db_session.add(
            AutomatedTaskRun(
                automated_task_id=task.id, thread_id=run_thread.id, status="success"
            )
        )
        (tc_id,) = suspend(db_session, run_thread, [("manage_stock_item", {})])
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

        r = client.post(f"/api/threads/{run_thread.id}/approve", headers=headers(owner))

        assert r.status_code == 200, r.text
        convo = (
            db_session.query(Message)
            .filter(Message.thread_id == conversation.id)
            .order_by(Message.created_at)
            .all()
        )
        assert convo[0].display_blocks[0]["data"]["status"] == "approved"
        assert convo[-1].content == "Updated 1 item."


# ── A decision whose resume fails still lands ────────────────────────────


def _last_assistant_text(db, thread_id):
    m = (
        db.query(Message)
        .filter(Message.thread_id == thread_id, Message.role == "assistant")
        .order_by(Message.created_at.desc())
        .first()
    )
    return m.content if m else None


class TestAFailedResumeLands:
    """A decision whose resume fails still lands, settled, with a message.

    Until Oct 2026 a failure anywhere in ``resume_tool_loop`` surfaced as a 500
    from /approve or /reject: the card said Approved, the thread stayed
    in_progress, the client polled "Working..." for half an hour, and nobody
    was told whether the changes had run. The known cause (a round mixing a
    write with other calls) is fixed; this is the net for the next one.
    """

    @pytest.fixture()
    def db_session(self):
        """The net rolls the request session back, as production must (its
        uncommitted claim holds a lock on the thread row). With the default
        session that rollback would also undo the test's own transaction, so
        these tests use savepoints and each commits its setup."""
        connection = _engine.connect()
        transaction = connection.begin()
        session = sessionmaker(
            bind=connection, join_transaction_mode="create_savepoint"
        )()
        yield session
        session.close()
        transaction.rollback()
        connection.close()

    @pytest.fixture()
    def net_session(self, monkeypatch, db_session):
        """The net opens its own session; bind it to the test transaction."""
        monkeypatch.setattr(
            engine_mod, "SessionLocal", lambda: Session(bind=db_session.get_bind())
        )

    def test_a_crash_before_the_writes_marks_them_failed(
        self, client, db_session, owner, monkeypatch, net_session
    ):
        """Nothing ran: the pending rows are marked failed so a second click
        cannot run a write whose side effect might already stand, the thread
        settles, and the message says what to check."""
        thread = owner_thread(db_session, owner)
        (tc_id,) = suspend(db_session, thread, [("manage_stock_item", {})])
        db_session.commit()

        def boom(*a, **k):
            raise RuntimeError("resume exploded")

        monkeypatch.setattr(TL, "resume_tool_loop", boom)

        r = client.post(f"/api/threads/{thread.id}/approve", headers=headers(owner))

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
        thread = owner_thread(db_session, owner)
        first, second = suspend(
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

        r = client.post(f"/api/threads/{thread.id}/approve", headers=headers(owner))

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
        thread = owner_thread(db_session, owner)
        (tc_id,) = suspend(db_session, thread, [("manage_stock_item", {})])
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

        r = client.post(f"/api/threads/{thread.id}/approve", headers=headers(owner))

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
        thread = owner_thread(db_session, owner)
        suspend(db_session, thread, [("manage_stock_item", {})])
        db_session.commit()

        def boom(*a, **k):
            raise RuntimeError("resume exploded")

        monkeypatch.setattr(TL, "resume_tool_loop", boom)

        r = client.post(
            f"/api/threads/{thread.id}/reject",
            headers=headers(owner),
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


# ── The resume commits the approval before its continuation ──────────────


class TestTheApprovalCommitsFirst:
    """The approval resume commits the approval BEFORE its (long) continuation.

    An approved write runs its external side effect, then the loop continues.
    If the DB commit only happened at the end of that continuation, a request
    cut would roll the DB back while the side effect already stood — reverting
    the thread to awaiting-approval and letting a re-approve DOUBLE-EXECUTE
    the write. So resume_tool_loop must commit the approval boundary first.
    """

    def test_resume_commits_approval_before_the_continuation(
        self, db_session, admin_user, monkeypatch
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


# ── A round that mixes a write with other calls survives the pause ───────


class TestAMixedRoundSurvivesThePause:
    """A round that mixes an approval-gated write with other calls survives
    the pause.

    Prod, 2–3 Oct 2026 (threads c6aad2d5 and aad94383 twice): the model put a
    notebook update and/or a read in the same round as manage_stock_item
    writes. The loop saved only the pending writes at suspension; after
    Approve the writes ran, then the first model call was rejected —
    "tool_use ids were found without tool_result blocks" — /approve returned
    500, and the card showed Approved with no reply. This drives the REAL loop
    through that round, approves the write, and checks that the model is
    answered for every call it made.
    """

    TOOLS = [
        tool("loadedhub__get_stock", description="Read stock."),
        tool("loadedhub__manage_stock_item", "PUT", "Update a stock item."),
    ]

    def test_a_mixed_round_is_fully_answered_after_approval(
        self, db_session, admin_user, monkeypatch
    ):
        llm = ScriptedLlm(
            [
                tool_round(
                    tool_use(
                        "toolu_note",
                        "memory",
                        {
                            "command": "str_replace",
                            "path": "/memories/job.md",
                            "old_str": "(empty)",
                            "new_str": "item 1 done",
                        },
                    ),
                    tool_use("toolu_read", "loadedhub__get_stock"),
                    tool_use(
                        "toolu_write",
                        "loadedhub__manage_stock_item",
                        {"op": "update", "item_id": str(uuid.uuid4())},
                    ),
                    text="Noting progress, then the next item.",
                ),
                end_turn("Done."),
            ]
        ).install(monkeypatch)
        monkeypatch.setattr(
            TL, "_execute_tool_call", lambda tc, db, config_db=None: {"ok": 1}
        )
        thread = _make_thread(
            db_session,
            admin_user,
            domain="procurement",
            intent="procurement.tool_use",
            status="in_progress",
        )
        # Give the notebook something to replace, so the memory op succeeds.
        from app.agents import memory_tool

        memory_tool.handle(
            {"command": "create", "path": "/memories/job.md", "file_text": "(empty)"},
            db_session,
            thread.id,
        )

        TL.run_tool_loop(
            "update the stock codes",
            thread,
            db_session,
            "system prompt",
            self.TOOLS,
            config_db=db_session,
        )
        pending = thread.pending_tool_call_ids or []
        assert pending == ["toolu_write"], f"expected the write to wait: {pending}"

        # The owner approves, and the loop resumes.
        tc = db_session.get(ToolCall, "toolu_write")
        tc.status = "approved"
        db_session.flush()
        TL.resume_tool_loop(
            thread, db_session, "system prompt", self.TOOLS, config_db=db_session
        )

        assert len(llm.calls) == 2, (
            f"expected the model to be called again, saw {len(llm.calls)}"
        )
        resumed = llm.calls[1]
        asked = {
            b["id"]
            for m in resumed
            if m["role"] == "assistant" and isinstance(m["content"], list)
            for b in m["content"]
            if b.get("type") == "tool_use"
        }
        answered = {
            b["tool_use_id"]
            for m in resumed
            if m["role"] == "user" and isinstance(m["content"], list)
            for b in m["content"]
            if b.get("type") == "tool_result"
        }
        assert asked == {"toolu_note", "toolu_read", "toolu_write"}
        assert asked - answered == set(), (
            f"tool_use ids without a tool_result: {sorted(asked - answered)} "
            "— the API rejects this turn with a 400"
        )
        assert thread.status == "completed"
