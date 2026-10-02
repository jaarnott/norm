"""Conversation persistence: the user's message must survive the turn.

Born from a production incident (05 Aug 2026): two questions vanished from the
conversation view. A turn used to run inside one uncommitted transaction — the
thread and user message were only flushed, and the first commit happened when
the turn finished. Any mid-turn error rolled all of it back. The trigger was a
TokenUsage select-then-insert race: the day's first usage row stayed
uncommitted (and row-locked) for a 4½-minute turn, a concurrent turn's insert
blocked at flush, then died with UniqueViolation the moment the first turn
committed — and its rollback took the user's message with it.

Three layers now guarantee persistence, each tested here:
1. record_usage writes through its own short transaction (no shared-session
   poison, no minutes-long row locks, race resolved by retry-as-update).
2. handle_message_with_tools COMMITS the thread + user message before any LLM
   work.
3. The /messages endpoints persist the user message + a failure note on ANY
   turn error, via a fresh session, before emitting the error event.
4. A FAILED llm call records itself the same way. `_persist_llm_call` only
   add()s and flush()es, which is right on the success path (the turn commits
   moments later) and wrong on the failure path: the exception propagates, the
   session closes uncommitted, and the row goes with it. So the most
   interesting calls left no trace at all — the 1 Oct 2026 Service Foods turn
   (prod thread c6aad2d5) burned a 5m23s call on an `overloaded_error` and
   `llm_calls` held nothing, which is why it could only be reconstructed from
   Cloud Run logs.
"""

import datetime
import json

import pytest
from sqlalchemy.orm import Session

import app.routers.messages as messages_mod
from app.db.models import Message, Thread, TokenUsage
from app.services.usage_service import record_usage


def _sse_events(body: str) -> list[dict]:
    events = []
    for line in body.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: ") :]))
    return events


@pytest.fixture()
def net_sessions(monkeypatch, db_session):
    """Point the router's fresh-session factory at the test transaction.

    The failure net (and the stream worker) open sessions via SessionLocal;
    binding them to the test connection keeps their writes inside the test
    transaction and lets them see fixture rows.
    """
    monkeypatch.setattr(
        messages_mod, "SessionLocal", lambda: Session(bind=db_session.get_bind())
    )


class TestRecordUsage:
    def _org_for(self, db_session, user):
        from app.db.models import Organization, OrganizationMembership

        org = Organization(name="Test Org", slug=f"test-org-{user.id[:8]}")
        db_session.add(org)
        db_session.flush()
        db_session.add(
            OrganizationMembership(
                organization_id=org.id, user_id=user.id, role="owner"
            )
        )
        db_session.flush()
        return org

    def test_accumulates_into_one_row(self, db_session, admin_user):
        org = self._org_for(db_session, admin_user)
        record_usage(db_session, admin_user.id, 100, 50)
        record_usage(db_session, admin_user.id, 10, 5)

        rows = (
            db_session.query(TokenUsage)
            .filter(TokenUsage.organization_id == org.id)
            .all()
        )
        assert len(rows) == 1
        assert rows[0].input_tokens == 110
        assert rows[0].output_tokens == 55
        assert rows[0].llm_call_count == 2

    def test_updates_existing_row_with_null_counters(self, db_session, admin_user):
        """Legacy rows may hold NULLs; the increment must coalesce, not NULL out."""
        org = self._org_for(db_session, admin_user)
        db_session.add(
            TokenUsage(
                organization_id=org.id,
                user_id=admin_user.id,
                date=datetime.date.today().isoformat(),
                input_tokens=None,
                output_tokens=None,
                llm_call_count=None,
            )
        )
        db_session.flush()

        record_usage(db_session, admin_user.id, 7, 3)

        row = (
            db_session.query(TokenUsage)
            .filter(TokenUsage.organization_id == org.id)
            .one()
        )
        assert row.input_tokens == 7
        assert row.output_tokens == 3
        assert row.llm_call_count == 1

    def test_no_org_is_a_noop(self, db_session, admin_user):
        record_usage(db_session, admin_user.id, 100, 50)
        assert (
            db_session.query(TokenUsage)
            .filter(TokenUsage.user_id == admin_user.id)
            .count()
            == 0
        )

    def test_failure_never_raises(self, db_session, admin_user, monkeypatch):
        """A broken usage write must not break the turn that reported it."""
        self._org_for(db_session, admin_user)
        import app.services.usage_service as usage_mod

        def boom(*a, **k):
            raise RuntimeError("db down")

        monkeypatch.setattr(usage_mod, "_upsert", boom)
        record_usage(db_session, admin_user.id, 100, 50)  # must not raise


class TestStreamFailureNet:
    """Any turn error leaves the user message + a failure note in a thread."""

    def _post(self, client, admin_headers, payload):
        resp = client.post("/api/messages/stream", json=payload, headers=admin_headers)
        assert resp.status_code == 200
        return _sse_events(resp.text)

    def test_new_conversation_error_creates_thread(
        self, client, db_session, admin_user, admin_headers, monkeypatch, net_sessions
    ):
        def boom(*a, **k):
            raise RuntimeError("model fell over")

        monkeypatch.setattr(messages_mod, "handle_message", boom)

        events = self._post(client, admin_headers, {"message": "how were sales?"})

        created = [e for e in events if e["type"] == "thread_created"]
        assert created, "failure must still announce a thread for the frontend"
        errors = [e for e in events if e["type"] == "error"]
        assert errors

        thread = (
            db_session.query(Thread).filter(Thread.id == created[0]["thread_id"]).one()
        )
        assert thread.user_id == admin_user.id
        msgs = (
            db_session.query(Message)
            .filter(Message.thread_id == thread.id)
            .order_by(Message.created_at)
            .all()
        )
        assert [m.role for m in msgs] == ["user", "assistant"]
        assert msgs[0].content == "how were sales?"
        assert "saved" in msgs[1].content or "went wrong" in msgs[1].content

    def test_followup_error_keeps_message_in_thread(
        self, client, db_session, admin_user, admin_headers, monkeypatch, net_sessions
    ):
        thread = Thread(
            user_id=admin_user.id,
            domain="reports",
            intent="reports.tool_use",
            status="completed",
            raw_prompt="earlier question",
            extracted_fields={},
            missing_fields=[],
        )
        db_session.add(thread)
        db_session.flush()

        def boom(*a, **k):
            raise RuntimeError("model fell over")

        monkeypatch.setattr(messages_mod, "handle_message", boom)

        self._post(
            client,
            admin_headers,
            {"message": "and versus budget?", "thread_id": thread.id},
        )

        msgs = (
            db_session.query(Message)
            .filter(Message.thread_id == thread.id)
            .order_by(Message.created_at)
            .all()
        )
        assert [m.role for m in msgs] == ["user", "assistant"]
        assert msgs[0].content == "and versus budget?"

    def test_already_persisted_message_is_not_duplicated(
        self, client, db_session, admin_user, admin_headers, monkeypatch, net_sessions
    ):
        """The tool loop commits the user message up front; the net must only
        add the failure note, not a second copy of the message."""
        thread = Thread(
            user_id=admin_user.id,
            domain="reports",
            intent="reports.tool_use",
            status="in_progress",
            raw_prompt="q",
            extracted_fields={},
            missing_fields=[],
        )
        db_session.add(thread)
        db_session.flush()

        def persist_then_boom(message, db, **kwargs):
            db.add(Message(thread_id=thread.id, role="user", content=message))
            db.commit()
            raise RuntimeError("died after committing the user message")

        monkeypatch.setattr(messages_mod, "handle_message", persist_then_boom)

        self._post(
            client,
            admin_headers,
            {"message": "and versus budget?", "thread_id": thread.id},
        )

        msgs = (
            db_session.query(Message)
            .filter(Message.thread_id == thread.id, Message.role == "user")
            .all()
        )
        assert len(msgs) == 1

    def test_quota_exceeded_still_persists_the_question(
        self, client, db_session, admin_user, admin_headers, monkeypatch, net_sessions
    ):
        from app.services.billing_service import QuotaExceededError

        def boom(*a, **k):
            raise QuotaExceededError(used=1000, quota=1000)

        monkeypatch.setattr(messages_mod, "handle_message", boom)

        events = self._post(client, admin_headers, {"message": "big question"})

        assert any(e["type"] == "quota_exceeded" for e in events)
        created = [e for e in events if e["type"] == "thread_created"]
        assert created
        msgs = (
            db_session.query(Message)
            .filter(Message.thread_id == created[0]["thread_id"])
            .order_by(Message.created_at)
            .all()
        )
        assert msgs[0].role == "user" and msgs[0].content == "big question"
        assert "tokens" in msgs[1].content


class TestNonStreamFailureNet:
    def test_error_persists_thread_and_returns_500(
        self, client, db_session, admin_user, admin_headers, monkeypatch, net_sessions
    ):
        def boom(*a, **k):
            raise RuntimeError("model fell over")

        monkeypatch.setattr(messages_mod, "handle_message", boom)

        resp = client.post(
            "/api/messages", json={"message": "lost question"}, headers=admin_headers
        )
        assert resp.status_code == 500

        msg = (
            db_session.query(Message)
            .filter(Message.role == "user", Message.content == "lost question")
            .one_or_none()
        )
        assert msg is not None


class TestEarlyCommit:
    """handle_message_with_tools commits the thread + user message before the
    tool loop runs, so a mid-turn crash can no longer roll them back."""

    def _agent(self):
        from app.agents.base import BaseDomainAgent

        class _FakeAgent(BaseDomainAgent):
            @property
            def domain(self):
                return "reports"

            def handle_message(self, *a, **k):
                raise NotImplementedError

            def handle_followup(self, *a, **k):
                raise NotImplementedError

            def build_context(self, db, user_id=None):
                return {}

            def get_tool_definitions(self, db, **kwargs):
                return ("prompt", [])

        return _FakeAgent()

    def test_commits_thread_and_message_before_the_loop_runs(
        self, db_session, admin_user, monkeypatch
    ):
        """The contract is ordering: COMMIT, then run the loop. (Durability of
        a real commit can't be shown under the test harness — the fixture's
        outer transaction swallows it — so assert the commit call and the rows
        as seen at the moment the loop starts.)"""
        import uuid

        import app.agents.tool_loop as tool_loop_mod

        commits = []
        real_commit = db_session.commit

        def counting_commit():
            commits.append(1)
            real_commit()

        monkeypatch.setattr(db_session, "commit", counting_commit)

        question = f"what were sales yesterday? [{uuid.uuid4().hex[:8]}]"
        seen_at_loop: dict = {}

        def boom(message, thread, db, *a, **k):
            seen_at_loop["commits"] = len(commits)
            seen_at_loop["thread_id"] = thread.id
            seen_at_loop["messages"] = (
                db.query(Message).filter(Message.thread_id == thread.id).count()
            )
            raise RuntimeError("LLM exploded mid-turn")

        monkeypatch.setattr(tool_loop_mod, "run_tool_loop", boom)

        agent = self._agent()
        with pytest.raises(RuntimeError):
            agent.handle_message_with_tools(
                question,
                db_session,
                user_id=admin_user.id,
                config_db=db_session,
            )

        assert seen_at_loop["commits"] >= 1, (
            "thread + user message must be committed before the tool loop"
        )
        assert seen_at_loop["messages"] == 1


class TestAFailedLlmCallLeavesATrace:
    """Layer 4: the error row must outlive the turn that died.

    Without this, "how often does this happen and what did it cost" cannot be
    answered from the database — only from logs, which are not queryable
    alongside the thread.
    """

    def test_it_writes_on_its_own_committed_session(
        self, db_session: Session, admin_user, monkeypatch
    ):
        """Fails if the failure path goes back to the turn's own session.

        Durability here comes from two things: a session of its own, and a
        commit. Neither can be observed end-to-end inside this harness — every
        test runs in one outer transaction that is rolled back, so a genuinely
        separate connection could not see the fixture thread and a commit on
        the shared one is undone anyway. So the session factory is bound to the
        test connection (as `net_sessions` does for the router's net) and
        spied: the row must be written through a session that is NOT the
        caller's, and commit() must be called on it.
        """
        from app.db import engine as engine_mod
        from app.db.models import LlmCall
        from app.interpreter.llm_interpreter import _persist_llm_call_isolated

        thread = Thread(
            user_id=admin_user.id,
            raw_prompt="update the stock codes",
            intent="procurement.tool_use",
            domain="procurement",
            status="processing",
        )
        db_session.add(thread)
        db_session.flush()

        commits: list[int] = []
        opened: list[Session] = []

        class _SpySession(Session):
            def commit(self):
                commits.append(1)
                super().commit()

        def factory():
            s = _SpySession(bind=db_session.get_bind())
            opened.append(s)
            return s

        monkeypatch.setattr(engine_mod, "SessionLocal", factory)

        _persist_llm_call_isolated(
            thread_id=thread.id,
            call_type="tool_use",
            model="claude-opus-4-8",
            system_prompt="s",
            user_prompt="[tool_use call]",
            raw_response=None,
            parsed_response=None,
            status="error",
            error_message="overloaded_error",
            duration_ms=323_000,
        )

        assert opened, "it did not open a session of its own"
        assert opened[0] is not db_session
        assert commits, "it never committed — the row dies with the turn"

        row = (
            db_session.query(LlmCall)
            .filter(LlmCall.thread_id == thread.id, LlmCall.status == "error")
            .one()
        )
        assert row.error_message == "overloaded_error"
        assert row.duration_ms == 323_000

    def test_it_never_bills_an_estimate(
        self, db_session: Session, admin_user, monkeypatch
    ):
        """Fails if the failure path starts passing input_tokens.

        A non-None value makes _persist_llm_call call record_usage, which would
        charge a chars/4 GUESS against the org's quota — the real figure never
        arrives on this path.
        """
        import inspect

        from app.interpreter import llm_interpreter

        src = inspect.getsource(llm_interpreter.call_llm_with_tools)
        failure_half = src[src.index("except Exception as exc:") :]
        assert "input_tokens=" not in failure_half, (
            "the failure path must not pass input_tokens — it would bill an estimate"
        )

    def test_telemetry_never_breaks_the_turn(self, monkeypatch):
        """Fails if a telemetry problem can mask the real error. The original
        exception is what the user and the logs need."""
        from app.interpreter import llm_interpreter

        def boom(*a, **k):
            raise RuntimeError("database is on fire")

        monkeypatch.setattr(llm_interpreter, "_persist_llm_call", boom)
        # Must not raise.
        llm_interpreter._persist_llm_call_isolated(
            thread_id="nope",
            call_type="tool_use",
            model="m",
            system_prompt="s",
            user_prompt="u",
            raw_response=None,
            parsed_response=None,
            status="error",
        )
