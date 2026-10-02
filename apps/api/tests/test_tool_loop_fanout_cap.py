"""One model response runs at most TOOL_FANOUT_CAP tool calls.

The cap and Anthropic's `keep` are one number (tool_loop.TOOL_FANOUT_CAP):
clearing runs on the request that carries a round's fresh results and spares
only the newest `keep`, so a round wider than keep would have its own answers
blanked before the model read them. Surplus calls are ANSWERED — "not run this
round, ask again" — never executed and never silently dropped. A write beyond
the cap is the case that matters: a dropped write is a write that did not
happen, and a silently-run one is worse.
"""

import json

from app.agents import tool_loop
from app.db.models import ToolCall
from tests.conftest import _make_thread
from tests.test_tool_loop_unknown_tool import _Block, _Response

READ = {
    "name": "loadedhub__get_stock",
    "description": "[GET] Read stock.",
    "input_schema": {"type": "object", "properties": {}},
}
WRITE = {
    "name": "loadedhub__manage_stock_item",
    "description": "[PUT] Change a stock item.",
    "input_schema": {"type": "object", "properties": {}},
}


def _run(db_session, admin_user, monkeypatch, blocks):
    seen_results: list[list] = []
    calls = {"n": 0}

    def llm(*args, **kwargs):
        calls["n"] += 1
        msgs = kwargs.get("messages", args[1] if len(args) > 1 else [])
        last = msgs[-1]["content"] if msgs else None
        if isinstance(last, list) and last and last[0].get("type") == "tool_result":
            seen_results.append(last)
        if calls["n"] == 1:
            return (_Response("tool_use", blocks), None)
        return (_Response("end_turn", [_Block("text", text="done")]), None)

    executed: list[str] = []

    def exec_tc(tc, db, config_db=None):
        executed.append(tc.id)
        return {"rows": []}

    monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm_with_tools", llm)
    monkeypatch.setattr(tool_loop, "_execute_tool_call", exec_tc)
    monkeypatch.setattr(
        tool_loop,
        "_execute_tool_call_in_thread",
        lambda tc_id, cb: (tc_id, exec_tc(type("T", (), {"id": tc_id})(), None)),
    )
    thread = _make_thread(
        db_session,
        admin_user,
        domain="procurement",
        intent="procurement.tool_use",
        status="processing",
    )
    tool_loop.run_tool_loop(
        "go", thread, db_session, "system prompt", [READ, WRITE], config_db=db_session
    )
    return thread, executed, seen_results


def _reads(n, start=0):
    return [
        _Block("tool_use", id=f"toolu_r{i}", name="loadedhub__get_stock", input={})
        for i in range(start, start + n)
    ]


class TestTheCap:
    def test_the_first_eight_run_and_the_rest_are_answered_not_dropped(
        self, db_session, admin_user, monkeypatch
    ):
        """Fails if a wide round runs everything (keep would then be too
        small) or if the surplus vanishes without a tool_result (the API
        rejects a tool_use with no result, and the model never learns to
        re-ask)."""
        thread, executed, results = _run(
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
            _Block(
                "tool_use",
                id="toolu_w",
                name="loadedhub__manage_stock_item",
                input={"op": "update"},
            )
        ]
        thread, executed, results = _run(db_session, admin_user, monkeypatch, blocks)
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
        thread, executed, results = _run(db_session, admin_user, monkeypatch, _reads(6))
        assert len(executed) == 6
        assert not [r for r in results[0] if json.loads(r["content"]).get("deferred")]
