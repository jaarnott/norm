"""A turn never ends on an empty model reply, and every ending settles the thread.

On 3 Oct 2026 (prod thread e016ed5c) the model set up a 59-item tender job,
wrote its notebook, recorded the supplier ids — and then returned an
`end_turn` with no content blocks at all (2 output tokens). The loop took
that as the answer: the saved message was the interim narration ("Let me
record the supplier IDs before anything is cleared from context.") and the
thread was marked completed with nothing done and nothing said. Anthropic
documents the behaviour ("Empty responses with end_turn", most often straight
after tool results) and its fallback: a continuation prompt in a new user
message, never a retry carrying the empty reply.

The same silent ending lived at two sibling exits — the time/iteration
wrap-up and the "any other stop reason" branch — and neither of those settled
the thread's status, so the client kept showing "Working..." (two threads
from Aug 2026 were still in_progress on 3 Oct). Every ending now lands a
non-empty message and a settled status.
"""

from app.agents import tool_loop
from app.agents.prompt_builder import notebook_guidance
from app.db.models import Message
from tests.conftest import _make_thread
from tests.test_tool_loop_unknown_tool import _Block, _Response

TOOL = {
    "name": "loadedhub__get_stock",
    "description": "[GET] Read stock.",
    "input_schema": {"type": "object", "properties": {}},
}


def _tool_use(n, text=None):
    blocks = [_Block("text", text=text)] if text else []
    blocks.append(
        _Block("tool_use", id=f"toolu_{n}", name="loadedhub__get_stock", input={})
    )
    return _Response("tool_use", blocks)


def _end(text=None, stop_reason="end_turn"):
    return _Response(stop_reason, [_Block("text", text=text)] if text else [])


class _Clock:
    def __init__(self, step):
        self.now, self.step = 0.0, step

    def __call__(self):
        return self.now

    def tick(self):
        self.now += self.step


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
):
    """Drive the loop with a scripted LLM.

    `script` answers the tool-bearing calls in order (the last entry repeats;
    a callable entry is given the call number, for fresh tool-use ids);
    `wrap_up` answers the tools-free wrap-up call. Returns (calls seen, saved
    answer, thread)."""
    clock = _Clock(step)
    seen = []

    def llm(system_prompt, messages, tools, db, thread_id, call_type):
        seen.append([dict(m) for m in messages])
        clock.tick()
        if not tools and wrap_up is not None:
            return wrap_up, None
        entry = script[min(len(seen), len(script)) - 1]
        return (entry(len(seen)) if callable(entry) else entry), None

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
        status="in_progress",
    )
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
    saved = (
        db_session.query(Message)
        .filter(Message.thread_id == thread.id, Message.role == "assistant")
        .order_by(Message.created_at)
        .all()
    )
    return seen, saved[-1].content if saved else None, thread


class TestAnEmptyEndTurnIsSentBack:
    def test_the_model_is_asked_to_continue_and_its_answer_is_kept(
        self, db_session, admin_user, monkeypatch
    ):
        """Fails if the empty reply is accepted as the answer (the production
        behaviour) — the saved message would be empty and there would be two
        calls, not three."""
        seen, saved, thread = _run(
            db_session,
            admin_user,
            monkeypatch,
            [_tool_use(1), _end(), _end("Done: 59 items updated at 6 venues.")],
        )
        assert len(seen) == 3, f"expected a nudge call, got {len(seen)} calls"
        assert saved == "Done: 59 items updated at 6 venues."
        last = seen[2][-1]
        assert (
            last["role"] == "user" and last["content"] == tool_loop.EMPTY_END_TURN_NUDGE
        )
        # The empty assistant message is not replayed to the API.
        assert all(m["content"] for m in seen[2] if m["role"] == "assistant"), (
            "an empty assistant message was appended to the conversation"
        )
        assert thread.status == "completed"

    def test_the_nudge_shows_in_the_activity_steps(
        self, db_session, admin_user, monkeypatch
    ):
        """The step must be in thinking_steps, not only streamed — the
        `complete` event replaces the thread with the persisted payload."""
        _, _, thread = _run(
            db_session, admin_user, monkeypatch, [_tool_use(1), _end(), _end("Done.")]
        )
        assert any("Continuing" in s for s in (thread.thinking_steps or []))

    def test_a_silent_model_is_told_about_in_the_saved_message(
        self, db_session, admin_user, monkeypatch
    ):
        """Still silent after two nudges: bounded, and the user is told the
        turn stopped — not left with narration that reads like a thought."""
        seen, saved, thread = _run(
            db_session,
            admin_user,
            monkeypatch,
            [_tool_use(1, "Setting up my notebook first."), _end(), _end(), _end()],
        )
        assert len(seen) == 1 + 1 + tool_loop.MAX_EMPTY_END_TURN_NUDGES
        assert saved.startswith("Setting up my notebook first.")
        assert "stopped here without a closing message" in saved
        assert thread.status == "completed"

    def test_an_empty_first_reply_is_also_sent_back(
        self, db_session, admin_user, monkeypatch
    ):
        """No tool round yet — the guard still applies: an empty first reply
        would also defeat the client's "has this thread settled" check."""
        seen, saved, _ = _run(
            db_session, admin_user, monkeypatch, [_end(), _end("Hi.")]
        )
        assert len(seen) == 2
        assert saved == "Hi."

    def test_with_nothing_said_at_all_the_note_stands_alone(
        self, db_session, admin_user, monkeypatch
    ):
        _, saved, _ = _run(
            db_session, admin_user, monkeypatch, [_end(), _end(), _end()]
        )
        assert saved.startswith("---")
        assert "stopped here without a closing message" in saved

    def test_no_nudge_once_the_turn_budget_is_spent(
        self, db_session, admin_user, monkeypatch
    ):
        """An empty reply after the clock has run out must end the turn HERE,
        settled, with the note — not fall into the wrap-up (which cannot
        'carry on' and, until now, never settled the thread)."""
        seen, saved, thread = _run(
            db_session,
            admin_user,
            monkeypatch,
            [_tool_use(1), _end()],
            step=200,  # call 1 at 200 s (head check passes), call 2 ends at 400 s
            budget=300,
        )
        assert len(seen) == 2, "the budget was spent — no nudge call expected"
        assert "stopped here without a closing message" in saved
        assert thread.status == "completed"


class TestTheSiblingExitsAlsoLandAndSettle:
    def test_an_empty_wrap_up_reply_gets_the_note_and_settles(
        self, db_session, admin_user, monkeypatch
    ):
        """Out of tool calls, the tools-free wrap-up comes back empty: today
        that saved narration only and left the thread in_progress."""
        seen, saved, thread = _run(
            db_session,
            admin_user,
            monkeypatch,
            [lambda n: _tool_use(n, "Working on it.")],
            max_iterations=2,
            wrap_up=_end(),
        )
        assert len(seen) == 3  # two tool rounds + the wrap-up
        assert saved.startswith("Working on it.")
        assert "stopped here without a closing message" in saved
        assert thread.status == "completed"

    def test_a_wrap_up_answer_settles_the_thread(
        self, db_session, admin_user, monkeypatch
    ):
        _, saved, thread = _run(
            db_session,
            admin_user,
            monkeypatch,
            [_tool_use(1)],
            max_iterations=1,
            wrap_up=_end("Here is where I got to."),
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
            [_tool_use(1, "Looking into it."), _end(stop_reason="refusal")],
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
            [_tool_use(1, "Looking into it."), _end(stop_reason="pause_turn")],
        )
        assert "stopped here without a closing message" in saved
        assert thread.status == "completed"


class TestANormalTurnIsUntouched:
    def test_a_real_answer_is_never_nudged(self, db_session, admin_user, monkeypatch):
        seen, saved, thread = _run(
            db_session, admin_user, monkeypatch, [_tool_use(1), _end("All done.")]
        )
        assert len(seen) == 2
        assert saved == "All done."
        assert not any(
            m["content"] == tool_loop.EMPTY_END_TURN_NUDGE
            for call in seen
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
