"""How a tool-loop turn ends: never silently, never unsettled, never by being cut off.

Folded from test_tool_loop_silent_end and test_tool_loop_turn_budget (Oct
2026), which drove the loop the same way: a scripted model, a clock that
advances per model call, and a wrap-up call with tool use switched off. The
helpers are in tests/tool_loop_harness.py.
"""

from app.agents import tool_loop
from app.agents.prompt_builder import notebook_guidance
from tests.tool_loop_harness import (
    Clock,
    ScriptedLlm,
    answers,
    end_turn,
    loop_thread,
    tool,
    tool_round,
    tool_use,
)

TOOL = tool("loadedhub__get_stock", description="Read stock.")


def _tool_use(n, text=None):
    return tool_round(tool_use(f"toolu_{n}", "loadedhub__get_stock"), text=text)


def _run(
    db_session,
    admin_user,
    monkeypatch,
    script,
    *,
    step=0.0,
    budget=None,
    max_iterations=None,
    wrap_up=None,
    status="in_progress",
):
    """Drive the loop with a scripted model.

    `script` answers the tool-bearing calls in order (the last entry repeats;
    a callable entry is given the call number, for fresh tool-use ids);
    `wrap_up` answers the wrap-up call (tool_choice none). Returns (the
    model stand-in, the saved answer, the thread)."""
    clock = Clock(step)
    llm = ScriptedLlm(script, wrap_up=wrap_up, clock=clock).install(monkeypatch)
    monkeypatch.setattr(tool_loop.time, "monotonic", clock)
    monkeypatch.setattr(
        tool_loop, "_execute_tool_call", lambda tc, db, config_db=None: {"rows": []}
    )
    thread = loop_thread(db_session, admin_user, status=status)
    tool_loop.run_tool_loop(
        "update the stock codes",
        thread,
        db_session,
        "system prompt",
        [TOOL],
        config_db=db_session,
        max_iterations=max_iterations,
        turn_budget_seconds=budget,
    )
    saved = answers(db_session, thread.id)
    return llm, saved[-1] if saved else None, thread


# ── A turn never ends on an empty model reply ────────────────────────────


class TestAnEmptyEndTurnIsSentBack:
    """A turn never ends on an empty model reply, and every ending settles the
    thread.

    On 3 Oct 2026 (prod thread e016ed5c) the model set up a 59-item tender job,
    wrote its notebook, recorded the supplier ids — and then returned an
    `end_turn` with no content blocks at all (2 output tokens). The loop took
    that as the answer: the saved message was the interim narration ("Let me
    record the supplier IDs before anything is cleared from context.") and the
    thread was marked completed with nothing done and nothing said. Anthropic
    documents the behaviour ("Empty responses with end_turn", most often
    straight after tool results) and its fallback: a continuation prompt in a
    new user message, never a retry carrying the empty reply.

    The same silent ending lived at two sibling exits — the time/iteration
    wrap-up and the "any other stop reason" branch — and neither of those
    settled the thread's status, so the client kept showing "Working..." (two
    threads from Aug 2026 were still in_progress on 3 Oct). Every ending now
    lands a non-empty message and a settled status.
    """

    def test_the_model_is_asked_to_continue_and_its_answer_is_kept(
        self, db_session, admin_user, monkeypatch
    ):
        """Fails if the empty reply is accepted as the answer (the production
        behaviour) — the saved message would be empty and there would be two
        calls, not three. The nudge also shows in the activity steps: it must
        be in thinking_steps, not only streamed — the `complete` event
        replaces the thread with the persisted payload."""
        llm, saved, thread = _run(
            db_session,
            admin_user,
            monkeypatch,
            [_tool_use(1), end_turn(), end_turn("Done: 59 items updated at 6 venues.")],
        )
        assert len(llm.calls) == 3, f"expected a nudge call, got {len(llm.calls)}"
        assert saved == "Done: 59 items updated at 6 venues."
        last = llm.calls[2][-1]
        assert (
            last["role"] == "user" and last["content"] == tool_loop.EMPTY_END_TURN_NUDGE
        )
        # The empty assistant message is not replayed to the API.
        assert all(m["content"] for m in llm.calls[2] if m["role"] == "assistant"), (
            "an empty assistant message was appended to the conversation"
        )
        assert thread.status == "completed"
        assert any("Continuing" in s for s in (thread.thinking_steps or []))

    def test_a_silent_model_is_told_about_in_the_saved_message(
        self, db_session, admin_user, monkeypatch
    ):
        """Still silent after two nudges: bounded, and the user is told the
        turn stopped — not left with narration that reads like a thought."""
        llm, saved, thread = _run(
            db_session,
            admin_user,
            monkeypatch,
            [
                _tool_use(1, "Setting up my notebook first."),
                end_turn(),
                end_turn(),
                end_turn(),
            ],
        )
        assert len(llm.calls) == 1 + 1 + tool_loop.MAX_EMPTY_END_TURN_NUDGES
        assert saved.startswith("Setting up my notebook first.")
        assert "stopped here without a closing message" in saved
        assert thread.status == "completed"

    def test_an_empty_first_reply_is_also_sent_back(
        self, db_session, admin_user, monkeypatch
    ):
        """No tool round yet — the guard still applies: an empty first reply
        would also defeat the client's "has this thread settled" check."""
        llm, saved, _ = _run(
            db_session, admin_user, monkeypatch, [end_turn(), end_turn("Hi.")]
        )
        assert len(llm.calls) == 2
        assert saved == "Hi."

    def test_with_nothing_said_at_all_the_note_stands_alone(
        self, db_session, admin_user, monkeypatch
    ):
        _, saved, _ = _run(
            db_session, admin_user, monkeypatch, [end_turn(), end_turn(), end_turn()]
        )
        assert saved.startswith("---")
        assert "stopped here without a closing message" in saved

    def test_no_nudge_once_the_turn_budget_is_spent(
        self, db_session, admin_user, monkeypatch
    ):
        """An empty reply after the clock has run out must end the turn HERE,
        settled, with the note — not fall into the wrap-up (which cannot
        'carry on' and, until now, never settled the thread)."""
        llm, saved, thread = _run(
            db_session,
            admin_user,
            monkeypatch,
            [_tool_use(1), end_turn()],
            step=200,  # call 1 at 200 s (head check passes), call 2 ends at 400 s
            budget=300,
        )
        assert len(llm.calls) == 2, "the budget was spent — no nudge call expected"
        assert "stopped here without a closing message" in saved
        assert thread.status == "completed"


class TestTheSiblingExitsAlsoLandAndSettle:
    def test_an_empty_wrap_up_reply_gets_the_note_and_settles(
        self, db_session, admin_user, monkeypatch
    ):
        """Out of tool calls, the tools-free wrap-up comes back empty: today
        that saved narration only and left the thread in_progress."""
        llm, saved, thread = _run(
            db_session,
            admin_user,
            monkeypatch,
            [lambda n: _tool_use(n, "Working on it.")],
            max_iterations=2,
            wrap_up=end_turn(),
        )
        assert len(llm.calls) == 3  # two tool rounds + the wrap-up
        assert saved.startswith("Working on it.")
        assert "stopped here without a closing message" in saved
        assert thread.status == "completed"

    def test_a_wrap_up_answer_settles_the_thread(
        self, db_session, admin_user, monkeypatch
    ):
        """The wrap-up's own text is what is saved — which also pins the
        (response, llm_call_id) unpack at its call site: a tuple there raised
        AttributeError, a bare except swallowed it, and boilerplate replaced
        the summary (test_thinking_blocks.py::TestFinalSummaryContract)."""
        _, saved, thread = _run(
            db_session,
            admin_user,
            monkeypatch,
            [_tool_use(1)],
            max_iterations=1,
            wrap_up=end_turn("Here is where I got to."),
        )
        assert saved == "Here is where I got to."
        assert thread.status == "completed"

    def test_a_refusal_says_so_and_settles(self, db_session, admin_user, monkeypatch):
        """The 'any other stop reason' branch never set the status, so the
        client polled 'Working...' for half an hour. A classifier decline is
        final for the turn and gets its own note, not the silent-end one."""
        _, saved, thread = _run(
            db_session,
            admin_user,
            monkeypatch,
            [_tool_use(1, "Looking into it."), end_turn(stop_reason="refusal")],
        )
        assert saved.startswith("Looking into it.")
        assert "declined to continue" in saved
        assert "stopped here without a closing message" not in saved
        assert thread.status == "completed"

    def test_an_empty_pause_turn_gets_the_silent_end_note(
        self, db_session, admin_user, monkeypatch
    ):
        _, saved, thread = _run(
            db_session,
            admin_user,
            monkeypatch,
            [_tool_use(1, "Looking into it."), end_turn(stop_reason="pause_turn")],
        )
        assert "stopped here without a closing message" in saved
        assert thread.status == "completed"


class TestANormalTurnIsUntouched:
    def test_a_real_answer_is_never_nudged(self, db_session, admin_user, monkeypatch):
        llm, saved, thread = _run(
            db_session, admin_user, monkeypatch, [_tool_use(1), end_turn("All done.")]
        )
        assert len(llm.calls) == 2
        assert saved == "All done."
        assert not any(
            m["content"] == tool_loop.EMPTY_END_TURN_NUDGE
            for call in llm.calls
            for m in call
        )
        assert thread.status == "completed"


def test_the_notebook_guidance_says_a_note_is_a_step_not_an_ending():
    """The other half of the fix: the model is told, in the prompt, that a
    notebook write is a step, that a turn never ends without a message, and
    that the clearing notice is not a request to stop."""
    text = " ".join(notebook_guidance().split())  # the prompt is line-wrapped
    assert "A note is a step, not an ending" in text
    assert "never end a turn without a message" in text
    assert "not a request to stop" in text


# ── A turn that runs out of time lands an answer ─────────────────────────


def _always_another_tool(db_session, admin_user, monkeypatch, *, step, budget):
    """A model that always wants another tool call; the wrap-up answers."""
    return _run(
        db_session,
        admin_user,
        monkeypatch,
        [_tool_use],
        step=step,
        budget=budget,
        wrap_up=end_turn("Here is where I got to."),
        status="processing",
    )


class TestTheBudgetStopsTheTurn:
    """A turn that runs out of time lands an answer instead of being destroyed.

    MAX_ITERATIONS bounds how many times the loop goes round; it bounds
    nothing in wall-clock time. Until TURN_BUDGET_SECONDS existed, the only
    thing that stopped a long turn was Cloud Run severing the request at 300s
    — and that stopped the USER seeing it, not the work: cpu-throttling is
    false, so the worker ran on. On 1 Oct 2026 a Service Foods tender turn
    (prod thread c6aad2d5) ran 997s that way and was then destroyed whole by
    one transient `overloaded_error`, because nothing had been landed along
    the way. 12.4% of chat streams were hitting the cap in the fortnight
    around it.

    The budget replaces a severed socket with a turn that stops itself and
    reports.
    """

    def test_an_over_budget_turn_lands_an_answer(
        self, db_session, admin_user, monkeypatch
    ):
        """Fails if the budget raises, or ends the turn with nothing saved —
        the behaviour it exists to replace. Also fails if the clock is not
        consulted: 10 iterations at 400s each would otherwise run for over an
        hour, so it must stop well before the iteration cap."""
        llm, saved, _ = _always_another_tool(
            db_session, admin_user, monkeypatch, step=400, budget=600
        )
        assert saved, "an over-budget turn must still save an assistant message"
        assert "where I got to" in saved
        # 2 looping calls (0s, 400s; the 800s check breaks) + 1 wrap-up call.
        assert len(llm.calls) < tool_loop.MAX_ITERATIONS, (
            f"ran {len(llm.calls)} calls — the budget never fired"
        )

    def test_a_fast_turn_is_untouched(self, db_session, admin_user, monkeypatch):
        """Fails if the budget starts cutting ordinary turns. Production median
        is 98s and p90 279s, so a 1s-per-call turn must reach the iteration
        cap, not the clock."""
        llm, saved, _ = _always_another_tool(
            db_session, admin_user, monkeypatch, step=1, budget=900
        )
        assert saved
        assert len(llm.calls) >= tool_loop.MAX_ITERATIONS, (
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
        ticks = {"n": 0}

        def clock():
            ticks["n"] += 1
            # 1st call is `turn_started`; everything after is already overdue.
            return 0.0 if ticks["n"] == 1 else 10_000.0

        # The wrap-up call is deliberately tools-free, so counting calls alone
        # would be satisfied by the wrap-up and prove nothing: count the calls
        # that could actually use tools.
        llm = ScriptedLlm([end_turn("One pass.")]).install(monkeypatch)
        monkeypatch.setattr(tool_loop.time, "monotonic", clock)
        thread = loop_thread(db_session, admin_user)
        tool_loop.run_tool_loop(
            "go",
            thread,
            db_session,
            "system prompt",
            [TOOL],
            config_db=db_session,
            turn_budget_seconds=1,
        )
        assert any(llm.could_use_tools), (
            "the turn never got a tool-capable pass — it broke out of the loop "
            "before doing any work and only ran the tools-free wrap-up"
        )
        assert answers(db_session, thread.id), (
            "an already-overdue turn must still land something"
        )


class TestWhatTheUserIsTold:
    def test_the_clock_case_does_not_claim_it_ran_out_of_tool_calls(
        self, db_session, admin_user, monkeypatch
    ):
        """Fails if the two exhaustion paths share one wrap-up message.

        Telling a user whose turn hit the clock that it "ran out of tool calls"
        is false, and it buries the thing that matters: continuing is the
        expected next step, not an offer.

        Also fails if the wrap-up claims nothing was saved (folded from
        test_it_never_says_the_work_was_lost). An approved write tool may
        already have run — manage_stock_item updating Loaded, in exactly the
        tender scenario this budget was built for. Telling the model (and so
        the user) that nothing persisted invites redoing writes that already
        landed. The earlier wording said "Nothing you have not already written
        has been saved", which reads as "nothing was saved".
        """
        llm, _, _ = _always_another_tool(
            db_session, admin_user, monkeypatch, step=400, budget=600
        )
        wrap_up = [
            p for p in llm.last_prompts() if "time limit" in p or "tool calls" in p
        ]
        assert wrap_up, "no wrap-up prompt was sent at all"
        assert "time limit" in wrap_up[-1]
        assert "available tool calls" not in wrap_up[-1], (
            "the clock case must not claim the tool-call budget ran out"
        )
        assert "STILL OUTSTANDING" in wrap_up[-1]

        assert "has been saved" not in wrap_up[-1]
        assert "STANDS" in wrap_up[-1], (
            "the prompt must say completed writes stand, so nothing is redone"
        )


def test_the_budget_is_a_sane_default():
    """Fails if the default drifts somewhere it stops covering real turns
    (p90 was 279s) or stops being a bound at all."""
    assert 300 <= tool_loop.TURN_BUDGET_SECONDS <= 1800
