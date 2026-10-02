"""A turn that runs out of time lands an answer instead of being destroyed.

MAX_ITERATIONS bounds how many times the loop goes round; it bounds nothing in
wall-clock time. Until TURN_BUDGET_SECONDS existed, the only thing that stopped
a long turn was Cloud Run severing the request at 300s — and that stopped the
USER seeing it, not the work: cpu-throttling is false, so the worker ran on. On
1 Oct 2026 a Service Foods tender turn (prod thread c6aad2d5) ran 997s that way
and was then destroyed whole by one transient `overloaded_error`, because
nothing had been landed along the way. 12.4% of chat streams were hitting the
cap in the fortnight around it.

The budget replaces a severed socket with a turn that stops itself and reports.
"""

from app.agents import tool_loop
from app.db.models import Message
from tests.conftest import _make_thread
from tests.test_tool_loop_unknown_tool import _Block, _Response

TOOL = {
    "name": "loadedhub__get_stock",
    "description": "[GET] Read stock.",
    "input_schema": {"type": "object", "properties": {}},
}


class _Clock:
    """A monotonic clock that advances a fixed amount per LLM call."""

    def __init__(self, step):
        self.now = 0.0
        self.step = step

    def __call__(self):
        return self.now

    def tick(self):
        self.now += self.step


def _run(db_session, admin_user, monkeypatch, *, step, budget=None, tool_calls=True):
    """Drive the loop with a scripted LLM that always wants another tool call."""
    clock = _Clock(step)
    calls = {"n": 0}

    def llm(*args, **kwargs):
        calls["n"] += 1
        clock.tick()
        # Once tools are taken away (the wrap-up call), answer with text.
        if not kwargs.get("tools", args[2] if len(args) > 2 else None):
            return (
                _Response("end_turn", [_Block("text", text="Here is where I got to.")]),
                "llm-x",
            )
        return (
            _Response(
                "tool_use",
                [
                    _Block(
                        "tool_use",
                        id=f"toolu_{calls['n']}",
                        name="loadedhub__get_stock",
                        input={},
                    )
                ],
            ),
            None,
        )

    monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm_with_tools", llm)
    monkeypatch.setattr(tool_loop.time, "monotonic", clock)
    monkeypatch.setattr(
        tool_loop, "_execute_tool_call", lambda tc, db, config_db=None: {"rows": []}
    )
    thread = _make_thread(
        db_session,
        admin_user,
        domain="procurement",
        intent="procurement.tool_use",
        status="processing",
    )
    tool_loop.run_tool_loop(
        "update the stock codes",
        thread,
        db_session,
        "system prompt",
        [TOOL] if tool_calls else [],
        config_db=db_session,
        turn_budget_seconds=budget,
    )
    saved = (
        db_session.query(Message)
        .filter(Message.thread_id == thread.id, Message.role == "assistant")
        .order_by(Message.created_at)
        .all()
    )
    return calls["n"], saved[-1].content if saved else None


class TestTheBudgetStopsTheTurn:
    def test_an_over_budget_turn_lands_an_answer(
        self, db_session, admin_user, monkeypatch
    ):
        """Fails if the budget raises, or ends the turn with nothing saved —
        the behaviour it exists to replace."""
        n_calls, saved = _run(db_session, admin_user, monkeypatch, step=400, budget=600)
        assert saved, "an over-budget turn must still save an assistant message"
        assert "where I got to" in saved

    def test_it_stops_well_before_the_iteration_cap(
        self, db_session, admin_user, monkeypatch
    ):
        """Fails if the clock is not consulted: 10 iterations at 400s each
        would otherwise run for over an hour."""
        n_calls, _ = _run(db_session, admin_user, monkeypatch, step=400, budget=600)
        # 2 looping calls (0s, 400s; the 800s check breaks) + 1 wrap-up call.
        assert n_calls < tool_loop.MAX_ITERATIONS, (
            f"ran {n_calls} calls — the budget never fired"
        )

    def test_a_fast_turn_is_untouched(self, db_session, admin_user, monkeypatch):
        """Fails if the budget starts cutting ordinary turns. Production median
        is 98s and p90 279s, so a 1s-per-call turn must reach the iteration
        cap, not the clock."""
        n_calls, saved = _run(db_session, admin_user, monkeypatch, step=1, budget=900)
        assert saved
        assert n_calls >= tool_loop.MAX_ITERATIONS, (
            "a fast turn should end on MAX_ITERATIONS, not on time"
        )

    def test_the_first_iteration_always_runs(self, db_session, admin_user, monkeypatch):
        """Fails if a turn can land zero LLM calls.

        The clock here returns 0 for the loop's own start stamp and then jumps
        past the budget, which is the only way the first-iteration check is
        reachable: a budget already spent before the loop begins. Without the
        `iteration > start_iteration` clause the turn would break immediately
        and return with no model call and nothing saved.
        """
        calls = {"n": 0}
        ticks = {"n": 0}

        def clock():
            ticks["n"] += 1
            # 1st call is `turn_started`; everything after is already overdue.
            return 0.0 if ticks["n"] == 1 else 10_000.0

        def llm(*args, **kwargs):
            # Record whether this call could actually use tools. The wrap-up
            # call is deliberately tools-free, so counting calls alone would be
            # satisfied by the wrap-up and prove nothing.
            tools = kwargs.get("tools", args[2] if len(args) > 2 else None)
            calls["n"] += 1
            if tools:
                calls["with_tools"] = calls.get("with_tools", 0) + 1
            return (_Response("end_turn", [_Block("text", text="One pass.")]), None)

        monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm_with_tools", llm)
        monkeypatch.setattr(tool_loop.time, "monotonic", clock)
        thread = _make_thread(
            db_session,
            admin_user,
            domain="procurement",
            intent="procurement.tool_use",
            status="processing",
        )
        tool_loop.run_tool_loop(
            "go",
            thread,
            db_session,
            "system prompt",
            [TOOL],
            config_db=db_session,
            turn_budget_seconds=1,
        )
        assert calls.get("with_tools", 0) >= 1, (
            "the turn never got a tool-capable pass — it broke out of the loop "
            "before doing any work and only ran the tools-free wrap-up"
        )
        saved = (
            db_session.query(Message)
            .filter(Message.thread_id == thread.id, Message.role == "assistant")
            .all()
        )
        assert saved, "an already-overdue turn must still land something"


class TestWhatTheUserIsTold:
    def test_the_clock_case_does_not_claim_it_ran_out_of_tool_calls(
        self, db_session, admin_user, monkeypatch
    ):
        """Fails if the two exhaustion paths share one wrap-up message.

        Telling a user whose turn hit the clock that it "ran out of tool calls"
        is false, and it buries the thing that matters: continuing is the
        expected next step, not an offer.
        """
        prompts: list[str] = []
        seq = {"n": 0}
        clock = _Clock(400)

        def llm(*args, **kwargs):
            seq["n"] += 1
            clock.tick()
            msgs = kwargs.get("messages", args[1] if len(args) > 1 else [])
            if msgs and isinstance(msgs[-1].get("content"), str):
                prompts.append(msgs[-1]["content"])
            if not kwargs.get("tools", args[2] if len(args) > 2 else None):
                return (
                    _Response("end_turn", [_Block("text", text="Progress so far.")]),
                    None,
                )
            return (
                _Response(
                    "tool_use",
                    [
                        _Block(
                            "tool_use",
                            id=f"toolu_{seq['n']}",
                            name="loadedhub__get_stock",
                            input={},
                        )
                    ],
                ),
                None,
            )

        monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm_with_tools", llm)
        monkeypatch.setattr(tool_loop.time, "monotonic", clock)
        monkeypatch.setattr(
            tool_loop, "_execute_tool_call", lambda tc, db, config_db=None: {"rows": []}
        )
        thread = _make_thread(
            db_session,
            admin_user,
            domain="procurement",
            intent="procurement.tool_use",
            status="processing",
        )
        tool_loop.run_tool_loop(
            "go",
            thread,
            db_session,
            "system prompt",
            [TOOL],
            config_db=db_session,
            turn_budget_seconds=600,
        )
        wrap_up = [p for p in prompts if "time limit" in p or "tool calls" in p]
        assert wrap_up, "no wrap-up prompt was sent at all"
        assert "time limit" in wrap_up[-1]
        assert "available tool calls" not in wrap_up[-1], (
            "the clock case must not claim the tool-call budget ran out"
        )
        assert "STILL OUTSTANDING" in wrap_up[-1]

    def test_it_never_says_the_work_was_lost(self, db_session, admin_user, monkeypatch):
        """Fails if the wrap-up claims nothing was saved.

        An approved write tool may already have run — manage_stock_item
        updating Loaded, in exactly the tender scenario this budget was built
        for. Telling the model (and so the user) that nothing persisted invites
        redoing writes that already landed. The earlier wording said "Nothing
        you have not already written has been saved", which reads as "nothing
        was saved".
        """
        prompts: list[str] = []
        seq = {"n": 0}
        clock = _Clock(400)

        def llm(*args, **kwargs):
            seq["n"] += 1
            clock.tick()
            msgs = kwargs.get("messages", args[1] if len(args) > 1 else [])
            if msgs and isinstance(msgs[-1].get("content"), str):
                prompts.append(msgs[-1]["content"])
            if not kwargs.get("tools", args[2] if len(args) > 2 else None):
                return (
                    _Response("end_turn", [_Block("text", text="Done some.")]),
                    None,
                )
            return (
                _Response(
                    "tool_use",
                    [
                        _Block(
                            "tool_use",
                            id=f"toolu_{seq['n']}",
                            name="loadedhub__get_stock",
                            input={},
                        )
                    ],
                ),
                None,
            )

        monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm_with_tools", llm)
        monkeypatch.setattr(tool_loop.time, "monotonic", clock)
        monkeypatch.setattr(
            tool_loop, "_execute_tool_call", lambda tc, db, config_db=None: {"rows": []}
        )
        thread = _make_thread(
            db_session,
            admin_user,
            domain="procurement",
            intent="procurement.tool_use",
            status="processing",
        )
        tool_loop.run_tool_loop(
            "go",
            thread,
            db_session,
            "system prompt",
            [TOOL],
            config_db=db_session,
            turn_budget_seconds=600,
        )
        wrap_up = [p for p in prompts if "time limit" in p][-1]
        assert "has been saved" not in wrap_up
        assert "STANDS" in wrap_up, (
            "the prompt must say completed writes stand, so nothing is redone"
        )


def test_the_budget_is_a_sane_default():
    """Fails if the default drifts somewhere it stops covering real turns
    (p90 was 279s) or stops being a bound at all."""
    assert 300 <= tool_loop.TURN_BUDGET_SECONDS <= 1800
