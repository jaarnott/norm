"""The tool loop: what it runs, what it refuses, and what it keeps.

Folded from test_tool_loop_unknown_tool, _truncation, _jobs, _fanout_cap and
_documents (Oct 2026). How a turn ENDS — empty replies, the turn budget, the
wrap-up — is in test_tool_loop_endings.py. The scripted model and helpers are
in tests/tool_loop_harness.py.
"""

import json
import uuid

from app.agents import tool_loop
from app.db.config_models import ConnectionSpec
from app.db.models import Message, Thread, ToolCall
from tests.conftest import _make_thread, _make_user
from tests.tool_loop_harness import (
    ScriptedLlm,
    answers,
    end_turn,
    loop_thread,
    tool,
    tool_round,
    tool_use,
)


class TestUnknownToolIsRefused:
    """A tool call the agent was not given this turn is refused, not executed.

    After a mid-thread agent rebind the model can mimic tool names it sees in
    the conversation history — with an invented schema, since the real one was
    never in its prompt. Phase A used to default an unknown name's method to
    POST, so on 20 Aug 2026 (prod thread bb7010c3) a mimicked recipe WRITE
    executed with garbage params and a mimicked `get_recipe_details` — a READ —
    became a phantom approval card that left the thread stuck in
    `awaiting_tool_approval`.

    Unknown names now come straight back as an error tool_result: no ToolCall
    row, no execution, no approval card, and the loop continues so the model
    can recover.
    """

    def test_no_tool_call_row_and_no_approval(
        self, db_session, admin_user, monkeypatch
    ):
        """...and the model receives an error result and recovers."""
        llm = ScriptedLlm(
            [
                tool_round(
                    tool_use(
                        "tu_unknown",
                        "cook_brothers_app__kitchen_loadedhub_update_recipe",
                        {"recipe_id": "r1", "made_up_field": "x"},
                    )
                ),
                end_turn("Understood."),
            ],
            llm_call_id="llm-1",
        ).install(monkeypatch)
        thread = loop_thread(
            db_session, admin_user, domain="executive_chef", status="processing"
        )
        result = tool_loop.run_tool_loop(
            "swap the leek line",
            thread,
            db_session,
            "system prompt",
            [tool("loadedhub__get_stock_items", description="List stock items.")],
        )

        rows = db_session.query(ToolCall).filter(ToolCall.thread_id == thread.id).all()
        assert rows == []
        assert thread.status != "awaiting_tool_approval"

        # The loop went back to the model with an is_error tool_result...
        assert len(llm.calls) == 2
        followup = llm.calls[1][-1]
        assert followup["role"] == "user"
        (block,) = followup["content"]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "tu_unknown"
        assert block["is_error"] is True
        assert "not available" in json.loads(block["content"])["error"]
        # ...and the turn finished normally on the model's recovery text.
        assert "Understood." in (
            result.get("message") or result.get("answer") or str(result)
        )


class TestACutOffReplySaysSo:
    """An answer cut off at the output ceiling says so.

    A group-wide "top 50 fruit & veg items" turn spent its whole output budget
    reasoning over 30 results and stopped mid-table (prod thread fa1cfd1c,
    23 Sep 2026). The loop treated stop_reason="max_tokens" like end_turn and
    saved half a table as the finished answer, twice, with no sign anything
    had gone wrong. The saved reply must now carry a visible note.
    """

    def _saved(self, db_session, admin_user, monkeypatch, stop_reason):
        ScriptedLlm(
            [end_turn("| # | Item | Spend |\n| 1 |", stop_reason=stop_reason)]
        ).install(monkeypatch)
        thread = loop_thread(db_session, admin_user, domain="reports")
        tool_loop.run_tool_loop("top 50 items", thread, db_session, "system prompt", [])
        (saved,) = answers(db_session, thread.id)
        return saved

    def test_a_reply_cut_off_at_the_ceiling_is_flagged(
        self, db_session, admin_user, monkeypatch
    ):
        saved = self._saved(db_session, admin_user, monkeypatch, "max_tokens")
        assert saved.startswith("| # | Item | Spend |")  # what it did write is kept
        assert saved.endswith(tool_loop.TRUNCATION_NOTE)

    def test_a_finished_reply_carries_no_note(
        self, db_session, admin_user, monkeypatch
    ):
        saved = self._saved(db_session, admin_user, monkeypatch, "end_turn")
        assert tool_loop.TRUNCATION_NOTE not in saved


class TestALongToolCallLeavesATrace:
    """A long tool call leaves a trace, and a job it starts gets its card.

    14 Oct 2026 (prod thread 2f53a67e): a receive ran 25 minutes inside the
    chat request and Cloud Run cut it off. The ToolCall row, Norm's "I'm
    widening the window…" and the turn's steps were only ever flushed, so the
    thread was left with the person's message and nothing else. They are now
    committed before the tool runs. And a tool that hands the work to a
    background job returns ``_job`` — the loop puts the job's live card in the
    conversation.
    """

    def _run(self, db_session, admin_user, monkeypatch, execute):
        ScriptedLlm(
            [
                tool_round(
                    tool_use("toolu_1", "loadedhub__review_and_receive_invoices"),
                    text="Applegum has 200+ drafts — widening the window.",
                ),
                end_turn("Started — it runs in the background."),
            ]
        ).install(monkeypatch)
        monkeypatch.setattr(tool_loop, "_execute_tool_call", execute)
        thread = loop_thread(db_session, admin_user, status="in_progress")
        tool_loop.run_tool_loop(
            "receive all outstanding invoices",
            thread,
            db_session,
            "system prompt",
            [
                tool(
                    "loadedhub__review_and_receive_invoices",
                    description="Review and receive invoices.",
                )
            ],
            config_db=db_session,
        )
        return thread

    def test_the_call_and_the_reasoning_are_saved_before_the_tool_runs(
        self, db_session, admin_user, monkeypatch
    ):
        seen = {}

        def execute(tc, db, config_db=None):
            # Mid-tool: what a cut-off would leave behind is already committed.
            db.expire_all()
            thread = db.query(Thread).filter(Thread.id == tc.thread_id).one()
            seen["steps"] = [
                s.get("text") if isinstance(s, dict) else s
                for s in thread.thinking_steps or []
            ]
            seen["tool_call"] = (
                db.query(ToolCall).filter(ToolCall.id == "toolu_1").first()
            )
            return {"rows": []}

        self._run(db_session, admin_user, monkeypatch, execute)
        assert seen["tool_call"] is not None
        assert any(
            "[reasoning] Applegum has 200+ drafts — widening the window." in str(s)
            for s in seen["steps"]
        )

    def test_a_tool_that_starts_a_job_puts_its_card_in_the_conversation(
        self, db_session, admin_user, monkeypatch
    ):
        def execute(tc, db, config_db=None):
            tc.result_payload = {"_job": {"id": "job-1"}, "message": "started"}
            return {"rows": []}

        thread = self._run(db_session, admin_user, monkeypatch, execute)
        msg = (
            db_session.query(Message)
            .filter(Message.thread_id == thread.id, Message.role == "assistant")
            .one()
        )
        assert {
            "component": "background_job",
            "data": {"job_id": "job-1"},
            "props": {},
        } in (msg.display_blocks or [])


READ = tool("loadedhub__get_stock", description="Read stock.")
WRITE = tool("loadedhub__manage_stock_item", "PUT", "Change a stock item.")


def _reads(n, start=0):
    return [
        tool_use(f"toolu_r{i}", "loadedhub__get_stock") for i in range(start, start + n)
    ]


class TestTheCap:
    """One model response runs at most TOOL_FANOUT_CAP tool calls.

    The cap and Anthropic's `keep` are one number (tool_loop.TOOL_FANOUT_CAP):
    clearing runs on the request that carries a round's fresh results and
    spares only the newest `keep`, so a round wider than keep would have its
    own answers blanked before the model read them. Surplus calls are ANSWERED
    — "not run this round, ask again" — never executed and never silently
    dropped. A write beyond the cap is the case that matters: a dropped write
    is a write that did not happen, and a silently-run one is worse.
    """

    def _run(self, db_session, admin_user, monkeypatch, blocks):
        llm = ScriptedLlm([tool_round(*blocks), end_turn("done")]).install(monkeypatch)
        executed: list[str] = []

        def exec_tc(tc, db, config_db=None):
            executed.append(tc.id)
            return {"rows": []}

        monkeypatch.setattr(tool_loop, "_execute_tool_call", exec_tc)
        monkeypatch.setattr(
            tool_loop,
            "_execute_tool_call_in_thread",
            lambda tc_id, cb: (tc_id, exec_tc(type("T", (), {"id": tc_id})(), None)),
        )
        thread = loop_thread(db_session, admin_user)
        tool_loop.run_tool_loop(
            "go",
            thread,
            db_session,
            "system prompt",
            [READ, WRITE],
            config_db=db_session,
        )
        return thread, executed, llm.tool_results()

    def test_the_first_eight_run_and_the_rest_are_answered_not_dropped(
        self, db_session, admin_user, monkeypatch
    ):
        """Fails if a wide round runs everything (keep would then be too
        small) or if the surplus vanishes without a tool_result (the API
        rejects a tool_use with no result, and the model never learns to
        re-ask)."""
        thread, executed, results = self._run(
            db_session, admin_user, monkeypatch, _reads(11)
        )
        assert len(executed) == tool_loop.TOOL_FANOUT_CAP
        assert set(executed) == {
            f"toolu_r{i}" for i in range(tool_loop.TOOL_FANOUT_CAP)
        }
        # every one of the 11 got a tool_result back
        ids = {r["tool_use_id"] for r in results[0]}
        assert ids == {f"toolu_r{i}" for i in range(11)}
        deferred = [r for r in results[0] if json.loads(r["content"]).get("deferred")]
        assert len(deferred) == 3
        assert "ask again" in json.loads(deferred[0]["content"])["message"].lower()

    def test_a_write_beyond_the_cap_is_never_executed_or_queued(
        self, db_session, admin_user, monkeypatch
    ):
        """Fails if a write past the cap runs, or lands in the approval
        queue as a phantom card. It must be deferred like a read."""
        blocks = _reads(8) + [
            tool_use("toolu_w", "loadedhub__manage_stock_item", {"op": "update"})
        ]
        thread, executed, results = self._run(
            db_session, admin_user, monkeypatch, blocks
        )
        assert "toolu_w" not in executed
        assert (
            db_session.query(ToolCall).filter(ToolCall.id == "toolu_w").first() is None
        )
        assert not thread.pending_tool_call_ids
        w = [r for r in results[0] if r["tool_use_id"] == "toolu_w"][0]
        assert json.loads(w["content"])["deferred"] is True

    def test_a_round_within_the_cap_is_untouched(
        self, db_session, admin_user, monkeypatch
    ):
        """Fails if the cap starts biting ordinary rounds: 9 in 10 production
        rounds fire 6 or fewer calls."""
        thread, executed, results = self._run(
            db_session, admin_user, monkeypatch, _reads(6)
        )
        assert len(executed) == 6
        assert not [r for r in results[0] if json.loads(r["content"]).get("deferred")]


# ---------------------------------------------------------------------------
# A built-in's file must reach the model.

BLOCK = {
    "type": "document",
    "source": {"type": "base64", "media_type": "application/pdf", "data": "JVBE"},
}


def _doc_tool_call(db_session):
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


class TestABuiltInsFileReachesTheModel:
    """Built-ins return a file beside their data — ``{"data": {...},
    "_document": {type: document, source: {...}}}`` — and the tool loop lifts
    ``_document`` out of the stored result and attaches it to the tool result.
    But the loop stored only ``data``, so the file was dropped every time: the
    BambooHR CV reader and re-opened chat attachments handed the model a file
    name and size, never the file (found 29 Sep 2026). How a file is made
    model-ready is in test_chat_attachments.py::TestModelReadyBlock.
    """

    def test_a_built_ins_document_is_stored_for_the_loop_to_attach(
        self, db_session, monkeypatch
    ):
        import app.agents.internal_tools as it
        from app.agents.tool_loop import _execute_tool_call

        def handler(params, db, thread_id):
            return {"success": True, "data": {"filename": "cv.pdf"}, "_document": BLOCK}

        monkeypatch.setattr(
            it,
            "get_handler",
            lambda c, a: handler if (c, a) == ("doc_norm", "open_file") else None,
        )
        tc = _doc_tool_call(db_session)
        result = _execute_tool_call(tc, db_session, config_db=db_session)
        assert tc.result_payload["_document"] == BLOCK
        assert tc.result_payload["filename"] == "cv.pdf"
        # the same dict the loop pops from, so the file never lands in the text
        assert result["data"] is tc.result_payload

    def test_a_result_without_a_document_is_unchanged(self, db_session, monkeypatch):
        import app.agents.internal_tools as it
        from app.agents.tool_loop import _execute_tool_call

        monkeypatch.setattr(
            it,
            "get_handler",
            lambda c, a: lambda p, d, t: {"success": True, "data": {"x": 1}},
        )
        tc = _doc_tool_call(db_session)
        _execute_tool_call(tc, db_session, config_db=db_session)
        assert tc.result_payload == {"x": 1}


class TestLiftDocument:
    def test_both_copies_lose_the_file_after_a_parallel_batch(self, db_session):
        """After a parallel batch, tc was re-read from the DB, so the result the
        loop serialises for the model is a DIFFERENT dict — the file has to come
        off it too, or its base64 lands in the model's text."""
        from app.agents.tool_loop import _lift_document

        tc = _doc_tool_call(db_session)
        tc.result_payload = {"name": "Glenn", "_document": dict(BLOCK)}
        result = {"success": True, "data": {"name": "Glenn", "_document": dict(BLOCK)}}
        doc = _lift_document(tc, result, db_session)
        assert doc["type"] == "document"
        assert doc["source"]["media_type"] == "application/pdf"
        assert "_document" not in tc.result_payload
        assert "_document" not in result["data"]

    def test_the_single_call_path_shares_one_dict(self, db_session):
        from app.agents.tool_loop import _lift_document

        tc = _doc_tool_call(db_session)
        tc.result_payload = {"name": "Glenn", "_document": dict(BLOCK)}
        result = {"success": True, "data": tc.result_payload}
        assert _lift_document(tc, result, db_session)["type"] == "document"
        assert "_document" not in result["data"]

    def test_no_file_no_block(self, db_session):
        from app.agents.tool_loop import _lift_document

        tc = _doc_tool_call(db_session)
        tc.result_payload = {"x": 1}
        assert _lift_document(tc, {"data": {"x": 1}}, db_session) is None
