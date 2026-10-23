"""Drive the real tool loop with a scripted model.

One home for what the tool-loop, approval and notebook tests share — until
Oct 2026 eleven files borrowed `_Block`/`_Response` from one test module and
eight wrote their own scripted model and clock:

- `_Block` / `_Response`: the SDK's content blocks and responses;
- `ScriptedLlm`: a stand-in for `call_llm_with_tools` that answers from a
  script and records what every call was sent;
- `Clock`: a monotonic clock that advances a fixed step per model call;
- `loop_thread`, `tool`, `answers`: the thread a turn runs in, a tool
  definition, and what the turn saved.

Typical use::

    llm = ScriptedLlm([tool_round(tool_use("toolu_1", "loadedhub__get_stock")),
                       end_turn("Done.")]).install(monkeypatch)
    thread = loop_thread(db_session, admin_user)
    tool_loop.run_tool_loop("go", thread, db_session, "system prompt",
                            [tool("loadedhub__get_stock")], config_db=db_session)
    assert answers(db_session, thread.id)[-1] == "Done."
"""

from app.db.models import Message
from tests.conftest import _make_thread

LLM = "app.interpreter.llm_interpreter.call_llm_with_tools"


class _Block:
    """A content block (text, tool_use, thinking …) as the SDK returns it."""

    def __init__(self, type, **kw):
        self.type = type
        for k, v in kw.items():
            setattr(self, k, v)


class _Response:
    """A model response: a stop reason, its content blocks, and no usage."""

    def __init__(self, stop_reason, content):
        self.stop_reason = stop_reason
        self.content = content
        self.usage = None


def tool_use(id, name, input=None):
    return _Block("tool_use", id=id, name=name, input=input or {})


def tool_round(*blocks, text=None):
    """A response that calls tools (optionally saying ``text`` first)."""
    lead = [_Block("text", text=text)] if text else []
    return _Response("tool_use", lead + list(blocks))


def end_turn(text=None, stop_reason="end_turn"):
    """A final response — with no content blocks at all when ``text`` is None."""
    return _Response(stop_reason, [_Block("text", text=text)] if text else [])


def tool(name, method="GET", description=None):
    """An Anthropic tool definition; the loop reads the method off the
    description's ``[METHOD]`` prefix."""
    return {
        "name": name,
        "description": f"[{method}] {description or 'test tool'}",
        "input_schema": {"type": "object", "properties": {}},
    }


def is_wrap_up(tools, tool_choice) -> bool:
    """The wrap-up call: tool use switched off (tool_choice none). It keeps
    the tools so the cached conversation holds (Oct 2026); an empty tool list
    is the older form."""
    return tool_choice == {"type": "none"} or not tools


class Clock:
    """A monotonic clock that advances ``step`` seconds per model call."""

    def __init__(self, step=0.0):
        self.now = 0.0
        self.step = step

    def __call__(self):
        return self.now

    def tick(self):
        self.now += self.step


class ScriptedLlm:
    """Stands in for ``call_llm_with_tools``.

    ``script`` answers the model calls in order; the last entry repeats, and a
    callable entry is given the call number (for fresh tool_use ids).
    ``wrap_up``, when given, answers the tools-off wrap-up call instead.
    ``clock``, when given, ticks once per call.

    Records, per call: the messages it was sent (``calls``, copied at call
    time) and whether it could use tools (``could_use_tools``).
    """

    def __init__(self, script, *, wrap_up=None, clock=None, llm_call_id=None):
        self.script = list(script)
        self.wrap_up = wrap_up
        self.clock = clock
        self.llm_call_id = llm_call_id
        self.calls: list[list[dict]] = []
        self.could_use_tools: list[bool] = []

    def __call__(
        self,
        system_prompt,
        messages,
        tools=None,
        db=None,
        thread_id=None,
        call_type=None,
        tool_choice=None,
        model=None,
    ):
        self.calls.append([dict(m) for m in messages])
        wrap_up = is_wrap_up(tools, tool_choice)
        self.could_use_tools.append(not wrap_up)
        if self.clock is not None:
            self.clock.tick()
        if wrap_up and self.wrap_up is not None:
            return self.wrap_up, self.llm_call_id
        entry = self.script[min(len(self.calls), len(self.script)) - 1]
        return (entry(len(self.calls)) if callable(entry) else entry), self.llm_call_id

    def install(self, monkeypatch):
        monkeypatch.setattr(LLM, self)
        return self

    def tool_results(self) -> list[list[dict]]:
        """The tool_result blocks handed to each call that had any."""
        out = []
        for msgs in self.calls:
            last = msgs[-1]["content"] if msgs else None
            if (
                isinstance(last, list)
                and last
                and isinstance(last[0], dict)
                and last[0].get("type") == "tool_result"
            ):
                out.append(last)
        return out

    def last_prompts(self) -> list[str]:
        """Each call's final message, where it was plain text."""
        return [
            msgs[-1]["content"]
            for msgs in self.calls
            if msgs and isinstance(msgs[-1].get("content"), str)
        ]


def loop_thread(db, user, domain="procurement", status="processing", intent=None):
    return _make_thread(
        db, user, domain=domain, intent=intent or f"{domain}.tool_use", status=status
    )


def answers(db, thread_id) -> list[str]:
    """The assistant messages the turn saved, oldest first."""
    rows = (
        db.query(Message)
        .filter(Message.thread_id == thread_id, Message.role == "assistant")
        .order_by(Message.created_at)
        .all()
    )
    return [m.content for m in rows]
