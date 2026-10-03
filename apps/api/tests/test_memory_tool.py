"""The conversation's notebook: Anthropic's memory tool, backed by Norm.

What is pinned, and the failure each test is for:

- the six commands return the docs' reference strings, so Claude's trained
  expectations hold;
- a path cannot leave /memories (the docs' own warning: /memories/../../x);
- files are scoped to the conversation — one thread cannot read another's;
- caps refuse clearly instead of growing without bound;
- the loop hands the model the TEXT, not a JSON envelope, maps the
  Anthropic-defined name onto the internal handler, and does not count a
  notebook op toward the fan-out cap;
- a write to the notebook announces itself to the browser;
- an approved write that already ran in this conversation is NOT run again;
- the prompt says what the directory is for and that facts go to `remember`;
- `update_thread_summary` is gone.
"""

import json

from app.agents import memory_tool, tool_loop
from app.db.models import ThreadMemoryFile, ToolCall
from tests.conftest import _make_thread
from tests.test_tool_loop_unknown_tool import _Block, _Response


def _thread(db_session, admin_user):
    return _make_thread(
        db_session,
        admin_user,
        domain="procurement",
        intent="procurement.tool_use",
        status="processing",
    )


def mem(db, thread_id, **cmd):
    return memory_tool.handle(cmd, db, thread_id)


class TestTheSixCommands:
    def test_an_empty_directory_views_as_a_listing_not_an_error(
        self, db_session, admin_user
    ):
        t = _thread(db_session, admin_user)
        out = mem(db_session, t.id, command="view", path="/memories")
        assert out["success"]
        assert out["data"]["text"].startswith("Here're the files and directories")

    def test_create_then_view_with_line_numbers(self, db_session, admin_user):
        t = _thread(db_session, admin_user)
        out = mem(
            db_session,
            t.id,
            command="create",
            path="/memories/job.md",
            file_text="# Job\n- one\n- two\n",
        )
        assert out["data"]["text"] == "File created successfully at: /memories/job.md"
        out = mem(db_session, t.id, command="view", path="/memories/job.md")
        text = out["data"]["text"]
        assert text.startswith(
            "Here's the content of /memories/job.md with line numbers:"
        )
        assert "     1\t# Job" in text and "     3\t- two" in text

    def test_view_range_pages_a_file(self, db_session, admin_user):
        t = _thread(db_session, admin_user)
        mem(
            db_session,
            t.id,
            command="create",
            path="/memories/n.md",
            file_text="\n".join(f"line {i}" for i in range(1, 11)),
        )
        text = mem(
            db_session, t.id, command="view", path="/memories/n.md", view_range=[4, 5]
        )["data"]["text"]
        assert (
            "     4\tline 4" in text
            and "     5\tline 5" in text
            and "line 6" not in text
        )

    def test_str_replace_must_match_exactly_once(self, db_session, admin_user):
        t = _thread(db_session, admin_user)
        mem(
            db_session,
            t.id,
            command="create",
            path="/memories/j.md",
            file_text="- [ ] oil\n- [ ] eggs\n",
        )
        out = mem(
            db_session,
            t.id,
            command="str_replace",
            path="/memories/j.md",
            old_str="- [ ] oil",
            new_str="- [x] oil — done",
        )
        assert out["success"] and out["data"]["text"].startswith(
            "The memory file has been edited."
        )
        assert (
            "- [x] oil — done"
            in mem(db_session, t.id, command="view", path="/memories/j.md")["data"][
                "text"
            ]
        )
        dup = mem(
            db_session,
            t.id,
            command="str_replace",
            path="/memories/j.md",
            old_str="- [",
            new_str="x",
        )
        assert not dup["success"] and "Multiple occurrences" in dup["data"]["text"]
        miss = mem(
            db_session,
            t.id,
            command="str_replace",
            path="/memories/j.md",
            old_str="nope",
            new_str="x",
        )
        assert not miss["success"] and "did not appear verbatim" in miss["data"]["text"]

    def test_insert_delete_rename(self, db_session, admin_user):
        t = _thread(db_session, admin_user)
        mem(
            db_session,
            t.id,
            command="create",
            path="/memories/a.md",
            file_text="one\ntwo",
        )
        assert mem(
            db_session,
            t.id,
            command="insert",
            path="/memories/a.md",
            insert_line=1,
            insert_text="between",
        )["success"]
        assert (
            "     2\tbetween"
            in mem(db_session, t.id, command="view", path="/memories/a.md")["data"][
                "text"
            ]
        )
        bad = mem(
            db_session,
            t.id,
            command="insert",
            path="/memories/a.md",
            insert_line=99,
            insert_text="x",
        )
        assert "Invalid `insert_line`" in bad["data"]["text"]
        assert mem(
            db_session,
            t.id,
            command="rename",
            old_path="/memories/a.md",
            new_path="/memories/b.md",
        )["success"]
        assert (
            "does not exist"
            in mem(db_session, t.id, command="view", path="/memories/a.md")["data"][
                "text"
            ]
        )
        assert (
            mem(db_session, t.id, command="delete", path="/memories/b.md")["data"][
                "text"
            ]
            == "Successfully deleted /memories/b.md"
        )
        assert (
            "does not exist"
            in mem(db_session, t.id, command="delete", path="/memories/b.md")["data"][
                "text"
            ]
        )

    def test_the_root_cannot_be_deleted_or_renamed(self, db_session, admin_user):
        t = _thread(db_session, admin_user)
        assert not mem(db_session, t.id, command="delete", path="/memories")["success"]
        assert not mem(
            db_session,
            t.id,
            command="rename",
            old_path="/memories",
            new_path="/memories/x",
        )["success"]


class TestItCannotLeaveTheDirectory:
    def test_traversal_and_outside_paths_are_refused(self, db_session, admin_user):
        """Fails if the docs' warning case gets through: a path like
        /memories/../../secrets.env reaching files outside the directory."""
        t = _thread(db_session, admin_user)
        for bad in (
            "/memories/../../secrets.env",
            "/etc/passwd",
            "memories/x.md",
            "/memories/%2e%2e/x",
            "/memories\\\\..\\\\x",
            "",
        ):
            out = mem(db_session, t.id, command="create", path=bad, file_text="x")
            assert not out["success"], bad
            assert out["data"]["text"].startswith("Error"), bad
        assert db_session.query(ThreadMemoryFile).count() == 0

    def test_one_conversation_cannot_see_another(self, db_session, admin_user):
        a, b = _thread(db_session, admin_user), _thread(db_session, admin_user)
        mem(
            db_session,
            a.id,
            command="create",
            path="/memories/job.md",
            file_text="secret plan",
        )
        assert (
            "secret plan"
            in mem(db_session, a.id, command="view", path="/memories/job.md")["data"][
                "text"
            ]
        )
        assert (
            "does not exist"
            in mem(db_session, b.id, command="view", path="/memories/job.md")["data"][
                "text"
            ]
        )
        assert (
            "/memories/job.md"
            not in mem(db_session, b.id, command="view", path="/memories")["data"][
                "text"
            ]
        )

    def test_a_file_over_the_cap_is_refused_with_the_limit_named(
        self, db_session, admin_user
    ):
        t = _thread(db_session, admin_user)
        out = mem(
            db_session,
            t.id,
            command="create",
            path="/memories/big.md",
            file_text="x" * (memory_tool.MAX_FILE_CHARS + 1),
        )
        assert (
            not out["success"]
            and f"{memory_tool.MAX_FILE_CHARS:,}" in out["data"]["text"]
        )

    def test_a_long_view_is_truncated_and_says_how_to_page(
        self, db_session, admin_user
    ):
        t = _thread(db_session, admin_user)
        body = "\n".join(f"row {i} " + "x" * 60 for i in range(400))
        mem(
            db_session, t.id, command="create", path="/memories/long.md", file_text=body
        )
        text = mem(db_session, t.id, command="view", path="/memories/long.md")["data"][
            "text"
        ]
        # The cap is on the file\'s content; the line-number prefix (8 chars a
        # line) rides on top. What matters: far fewer lines than the file.
        assert len(text) < memory_tool.VIEW_MAX_CHARS * 1.25
        assert "truncated after" in text and "view_range" in text
        assert "row 399" not in text


class TestTheLoopWiresItIn:
    def _run(self, db_session, admin_user, monkeypatch, blocks, tools=None):
        results: list[list] = []
        calls = {"n": 0}
        events: list[dict] = []
        monkeypatch.setattr(tool_loop, "_emit_event", lambda e: events.append(e))

        def llm(*args, **kwargs):
            calls["n"] += 1
            msgs = kwargs.get("messages", args[1] if len(args) > 1 else [])
            last = msgs[-1]["content"] if msgs else None
            if isinstance(last, list) and last and last[0].get("type") == "tool_result":
                results.append(last)
            if calls["n"] == 1:
                return (_Response("tool_use", blocks), None)
            return (_Response("end_turn", [_Block("text", text="done")]), None)

        monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm_with_tools", llm)
        thread = _thread(db_session, admin_user)
        tool_loop.run_tool_loop(
            "go", thread, db_session, "system", tools or [], config_db=db_session
        )
        return thread, results, events

    def test_the_memory_tool_is_offered_first_and_meta_knows_it(self, db_session):
        tools, meta = tool_loop._ensure_memory_tool(
            [{"name": "loadedhub__get_stock"}], {}
        )
        assert tools[0] == memory_tool.MEMORY_TOOL
        assert (
            meta["memory"]["connector"] == "norm"
            and meta["memory"]["action"] == "memory"
        )
        again, _ = tool_loop._ensure_memory_tool(tools, meta)
        assert again.count(memory_tool.MEMORY_TOOL) == 1, "never added twice"

    def test_a_memory_call_runs_and_the_model_gets_plain_text(
        self, db_session, admin_user, monkeypatch
    ):
        """Fails if the result is wrapped in JSON: the view format's line
        numbers and the docs' confirmation strings are what Claude is
        trained on, not {"success": true, "data": ...}."""
        blocks = [
            _Block(
                "tool_use",
                id="toolu_m1",
                name="memory",
                input={
                    "command": "create",
                    "path": "/memories/job.md",
                    "file_text": "# Job\n",
                },
            ),
        ]
        thread, results, events = self._run(db_session, admin_user, monkeypatch, blocks)
        r = results[0][0]
        assert r["content"] == "File created successfully at: /memories/job.md"
        assert r.get("is_error") is False
        tc = db_session.query(ToolCall).filter(ToolCall.id == "toolu_m1").one()
        assert (tc.connector_name, tc.action, tc.status) == (
            "norm",
            "memory",
            "executed",
        )
        assert (
            db_session.query(ThreadMemoryFile).filter_by(thread_id=thread.id).count()
            == 1
        )

    def test_a_write_announces_the_notebook_to_the_browser(
        self, db_session, admin_user, monkeypatch
    ):
        blocks = [
            _Block(
                "tool_use",
                id="toolu_m2",
                name="memory",
                input={
                    "command": "create",
                    "path": "/memories/job.md",
                    "file_text": "notes",
                },
            )
        ]
        _, _, events = self._run(db_session, admin_user, monkeypatch, blocks)
        nb = [e for e in events if e.get("type") == "notebook"]
        assert nb and nb[-1]["files"][0]["path"] == "/memories/job.md"
        assert nb[-1]["files"][0]["content"] == "notes"

    def test_a_notebook_op_does_not_count_toward_the_fan_out_cap(
        self, db_session, admin_user, monkeypatch
    ):
        """Fails if a memory view at the start of a wide round pushes a real
        call over the cap — the model views memory first on every turn."""
        read = {
            "name": "loadedhub__get_stock",
            "description": "[GET] x",
            "input_schema": {"type": "object", "properties": {}},
        }
        monkeypatch.setattr(
            tool_loop, "_execute_tool_call", lambda tc, db, config_db=None: {"rows": []}
        )
        monkeypatch.setattr(
            tool_loop,
            "_execute_tool_call_in_thread",
            lambda tc_id, cb: (tc_id, {"rows": []}),
        )
        blocks = [
            _Block(
                "tool_use",
                id="toolu_v",
                name="memory",
                input={"command": "view", "path": "/memories"},
            )
        ] + [
            _Block("tool_use", id=f"toolu_r{i}", name="loadedhub__get_stock", input={})
            for i in range(tool_loop.TOOL_FANOUT_CAP)
        ]
        _, results, _ = self._run(
            db_session, admin_user, monkeypatch, blocks, tools=[read]
        )
        deferred = [
            r
            for r in results[0]
            if isinstance(r["content"], str)
            and r["content"].startswith("{")
            and json.loads(r["content"]).get("deferred")
        ]
        assert deferred == [], "the memory view ate a slot"

    def test_the_prompt_says_what_the_directory_is_for(self):
        from app.agents.prompt_builder import notebook_guidance

        text = notebook_guidance()
        assert "/memories" in text and "remember" in text
        assert "checklist" not in text.lower(), "no format is prescribed"


class TestAnApprovedWriteNeverRunsTwice:
    def test_the_same_write_is_skipped_on_resume(
        self, db_session, admin_user, monkeypatch
    ):
        """Fails if a resumed job re-executes a write that already ran. On
        resume the model re-judges from its notes; the record of what RAN is
        what decides."""
        thread = _thread(db_session, admin_user)
        ran = ToolCall(
            id="toolu_w1",
            thread_id=thread.id,
            iteration=1,
            tool_name="loadedhub__manage_stock_item",
            connector_name="loadedhub",
            action="manage_stock_item",
            method="PUT",
            input_params={"op": "update", "item_id": "i-1", "changes": {"x": 1}},
            status="executed",
            result_payload={"ok": True},
        )
        again = ToolCall(
            id="toolu_w2",
            thread_id=thread.id,
            iteration=1,
            tool_name="loadedhub__manage_stock_item",
            connector_name="loadedhub",
            action="manage_stock_item",
            method="PUT",
            input_params={"op": "update", "item_id": "i-1", "changes": {"x": 1}},
            status="approved",
        )
        other = ToolCall(
            id="toolu_w3",
            thread_id=thread.id,
            iteration=1,
            tool_name="loadedhub__manage_stock_item",
            connector_name="loadedhub",
            action="manage_stock_item",
            method="PUT",
            input_params={"op": "update", "item_id": "i-2", "changes": {"x": 1}},
            status="approved",
        )
        db_session.add_all([ran, again, other])
        db_session.flush()
        assert tool_loop._already_executed_write(db_session, again).id == "toolu_w1"
        assert tool_loop._already_executed_write(db_session, other) is None

    def test_resume_records_the_skip_instead_of_executing(
        self, db_session, admin_user, monkeypatch
    ):
        thread = _thread(db_session, admin_user)
        ran = ToolCall(
            id="toolu_r1",
            thread_id=thread.id,
            iteration=1,
            tool_name="loadedhub__manage_stock_item",
            connector_name="loadedhub",
            action="manage_stock_item",
            method="PUT",
            input_params={"op": "update", "item_id": "i-1"},
            status="executed",
            result_payload={"ok": True},
        )
        again = ToolCall(
            id="toolu_r2",
            thread_id=thread.id,
            iteration=1,
            tool_name="loadedhub__manage_stock_item",
            connector_name="loadedhub",
            action="manage_stock_item",
            method="PUT",
            input_params={"op": "update", "item_id": "i-1"},
            status="approved",
        )
        db_session.add_all([ran, again])
        db_session.flush()
        thread.pending_tool_call_ids = ["toolu_r2"]
        thread.agent_loop_state = {
            "messages": [
                {"role": "user", "content": "go"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_r2",
                            "name": "loadedhub__manage_stock_item",
                            "input": {"op": "update", "item_id": "i-1"},
                        }
                    ],
                },
            ],
            "iteration": 1,
        }
        db_session.flush()
        executed = []
        monkeypatch.setattr(
            tool_loop,
            "_execute_tool_call",
            lambda tc, db, config_db=None: executed.append(tc.id) or {"ok": True},
        )
        monkeypatch.setattr(
            "app.interpreter.llm_interpreter.call_llm_with_tools",
            lambda *a, **k: (
                _Response("end_turn", [_Block("text", text="done")]),
                None,
            ),
        )
        tool_loop.resume_tool_loop(
            thread, db_session, "system", [], config_db=db_session
        )
        assert executed == [], "the duplicate write ran"
        assert (
            again.status == "executed"
            and again.result_payload["duplicate_of"] == "toolu_r1"
        )


class TestTheDeadToolIsGone:
    def test_update_thread_summary_is_not_registered(self):
        from app.agents.internal_tools import get_handler

        assert get_handler("norm", "update_thread_summary") is None
        assert get_handler("norm", "memory") is not None
