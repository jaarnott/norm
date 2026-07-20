"""Agent-to-agent delegation.

The tests that matter here are the containment ones. A delegated sub-agent runs
with no user watching and no approval step, so the only thing standing between
it and a real purchase order is the read-only tool filter. Most of this file
exists to make a regression in that filter fail loudly.
"""

import pytest

from app.db.config_models import ConnectorSpec
from app.db.models import Message, Thread
from app.services import delegation as D


# Actions that are method=GET — so they look harmless to Norm's approval check —
# but change real state. If any of these ever appears in a sub-agent's tool set,
# a consulted agent can spend money or email a customer with nobody watching.
# Named individually so the failure message says which one leaked.
GET_BUT_MUTATES = [
    ("norm_email", "send_report_email"),
    ("norm", "create_purchase_order"),
    ("norm", "create_automated_task"),
    ("norm", "set_workflow_mode"),
    ("norm", "update_task_config"),
    ("norm", "set_override"),
    ("norm", "update_thread_summary"),
    ("loadedhub", "review_and_receive_invoices"),
    ("loadedhub", "reconcile_received_invoices"),
]


def _thread(db, **kw):
    t = Thread(status="in_progress", **kw)
    db.add(t)
    db.flush()
    return t


@pytest.fixture()
def seeded_specs(db_session):
    """Connector specs mirroring the real ones for the actions that matter.

    The live config DB isn't reachable from CI, so the dangerous definitions are
    reproduced here exactly as they exist in production — method GET,
    read_only False — and the safe ones alongside them. If someone "fixes" the
    filter by trusting the method again, these fail.
    """
    tools = [
        {"action": a, "method": "GET", "read_only": False} for _, a in GET_BUT_MUTATES
    ] + [
        {"action": "get_roster", "method": "GET", "read_only": True},
        {"action": "get_sales_for_period", "method": "GET", "read_only": True},
        {"action": "update_shift", "method": "PUT", "read_only": False},
        # Flagged read-only but not a GET: contradictory config must fail closed.
        {"action": "sneaky_write", "method": "POST", "read_only": True},
    ]
    spec = ConnectorSpec(
        connector_name="test_delegation",
        display_name="Delegation test",
        auth_type="none",
        execution_mode="internal",
        tools=tools,
    )
    db_session.add(spec)
    db_session.flush()
    yield spec


# ---------------------------------------------------------------------------
# The read-only boundary
# ---------------------------------------------------------------------------


class TestReadOnlyBoundary:
    @pytest.mark.parametrize("connector,action", GET_BUT_MUTATES)
    def test_get_but_mutating_actions_never_reach_a_child(
        self, db_session, seeded_specs, connector, action
    ):
        """These are the whole reason read_only is a flag and not `method == GET`."""
        allowed = D.read_only_actions(db_session)
        assert action not in allowed, (
            f"{connector}.{action} is method=GET and mutates — it would be "
            "handed to a consulted agent running with nobody watching"
        )

    def test_safe_reads_do_reach_a_child(self, db_session, seeded_specs):
        allowed = D.read_only_actions(db_session)
        assert "get_roster" in allowed
        assert "get_sales_for_period" in allowed

    def test_missing_flag_fails_closed(self):
        """An action nobody has classified is not consultable."""
        assert D.is_read_only_tool({"action": "whatever", "method": "GET"}) is False

    def test_flag_alone_is_not_enough(self):
        """read_only must agree with the method; a flagged POST is still a write."""
        assert (
            D.is_read_only_tool({"action": "x", "method": "POST", "read_only": True})
            is False
        )
        # ...and that contradiction must not survive into a real tool set.
        assert (
            D.is_read_only_tool(
                {"action": "sneaky_write", "method": "POST", "read_only": True}
            )
            is False
        )

    def test_plain_read_passes(self):
        assert (
            D.is_read_only_tool(
                {"action": "get_roster", "method": "GET", "read_only": True}
            )
            is True
        )

    def test_filter_drops_writes_and_the_delegate_tool_itself(
        self, db_session, seeded_specs
    ):
        tools = [
            {"name": "test_delegation__get_roster"},
            {"name": "test_delegation__create_purchase_order"},
            {"name": "norm__delegate_to_agent"},
        ]
        kept = {t["name"] for t in D.filter_to_read_only(tools, db_session)}
        assert "test_delegation__get_roster" in kept
        assert "test_delegation__create_purchase_order" not in kept
        # Onward delegation is granted by depth, never inherited silently.
        assert "norm__delegate_to_agent" not in kept

    def test_unknown_tool_is_dropped(self, db_session, seeded_specs):
        """A tool with no spec entry has no classification, so it fails closed."""
        kept = D.filter_to_read_only([{"name": "mystery__do_thing"}], db_session)
        assert kept == []


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


class TestGuards:
    def test_depth_cap_refuses(self, db_session):
        parent = _thread(
            db_session, domain="procurement", delegation_depth=D.MAX_DELEGATION_DEPTH
        )
        with pytest.raises(D.DelegationError, match="depth limit"):
            D.check_guards(parent, "reports", db_session)

    def test_within_depth_allowed(self, db_session):
        parent = _thread(db_session, domain="procurement", delegation_depth=0)
        D.check_guards(parent, "reports", db_session)  # does not raise

    def test_direct_self_delegation_refused(self, db_session):
        parent = _thread(db_session, domain="procurement")
        with pytest.raises(D.DelegationError, match="loop"):
            D.check_guards(parent, "procurement", db_session)

    def test_cycle_through_the_chain_refused(self, db_session):
        """A -> B -> A. B asking A back is a loop, not a question."""
        a = _thread(db_session, domain="procurement", delegation_depth=0)
        b = _thread(
            db_session,
            domain="time_attendance",
            delegation_depth=1,
            parent_thread_id=a.id,
        )
        with pytest.raises(D.DelegationError, match="loop"):
            D.check_guards(b, "procurement", db_session)

    def test_sibling_target_is_fine(self, db_session):
        a = _thread(db_session, domain="procurement", delegation_depth=0)
        b = _thread(
            db_session,
            domain="time_attendance",
            delegation_depth=1,
            parent_thread_id=a.id,
        )
        D.check_guards(b, "reports", db_session)  # not in the chain

    def test_budget_exhausted(self, db_session):
        root = _thread(db_session, domain="procurement")
        for i in range(D.MAX_DELEGATIONS_PER_ROOT):
            _thread(
                db_session,
                domain=f"child{i}",
                delegation_depth=1,
                parent_thread_id=root.id,
            )
        with pytest.raises(D.DelegationError, match="delegations"):
            D.check_guards(root, "reports", db_session)

    def test_budget_counts_the_whole_tree_not_just_direct_children(self, db_session):
        """Nested delegations spend the same budget — otherwise depth evades it."""
        root = _thread(db_session, domain="procurement")
        cur = root
        for i in range(4):
            cur = _thread(
                db_session,
                domain=f"d{i}",
                delegation_depth=i + 1,
                parent_thread_id=cur.id,
            )
        assert D._delegation_count(root, db_session) == 4

    def test_root_of_walks_to_the_top(self, db_session):
        a = _thread(db_session, domain="procurement")
        b = _thread(db_session, domain="reports", parent_thread_id=a.id)
        c = _thread(db_session, domain="hr", parent_thread_id=b.id)
        assert D.root_of(c, db_session).id == a.id

    def test_unknown_target(self, db_session):
        with pytest.raises(D.DelegationError, match="Unknown agent"):
            D.resolve_target("nonexistent", db_session)

    def test_empty_target(self, db_session):
        with pytest.raises(D.DelegationError, match="required"):
            D.resolve_target("", db_session)

    def test_agent_only_target_resolves(self, db_session):
        slug, playbook = D.resolve_target("reports", db_session)
        assert slug == "reports" and playbook is None

    def test_unknown_playbook_refused(self, db_session):
        with pytest.raises(D.DelegationError, match="Unknown playbook"):
            D.resolve_target("reports/not_a_playbook", db_session)


# ---------------------------------------------------------------------------
# Running a child
# ---------------------------------------------------------------------------


class TestChildRun:
    """delegate() with the tool loop stubbed, so we assert what we hand it."""

    @pytest.fixture()
    def captured(self, db_session, monkeypatch, seeded_specs):
        calls = {}

        def fake_run_tool_loop(message, task, db, system_prompt, tools, **kw):
            calls["message"] = message
            calls["task"] = task
            calls["system_prompt"] = system_prompt
            calls["tools"] = tools
            calls["kwargs"] = kw
            # A chatty child: lots of internal detail, one useful sentence.
            return {
                "message": "Rostered 283.3 hours next week.",
                "display_blocks": [
                    {"component": "roster_editor", "data": {"x": "y" * 5000}}
                ],
                "conversation": [{"role": "assistant", "content": "z" * 50000}],
            }

        monkeypatch.setattr("app.agents.tool_loop.run_tool_loop", fake_run_tool_loop)

        def fake_build(agent, depth, db, cdb, uid, playbook):
            return "You are the test agent.", [{"name": "test_delegation__get_roster"}]

        monkeypatch.setattr(D, "build_child_tools", fake_build)
        return calls

    def test_child_sees_only_the_question(self, db_session, captured):
        """The isolation that makes delegation worth doing."""
        parent = _thread(
            db_session, domain="procurement", raw_prompt="secret parent talk"
        )
        db_session.add(
            Message(thread_id=parent.id, role="user", content="parent history here")
        )
        db_session.flush()

        D.delegate(parent, "reports", "How many hours?", None, db_session, db_session)

        override = captured["kwargs"]["messages_override"]
        assert override == [{"role": "user", "content": "How many hours?"}]
        blob = str(override)
        assert "parent history here" not in blob
        assert "secret parent talk" not in blob

    def test_context_is_passed_when_given(self, db_session, captured):
        parent = _thread(db_session, domain="procurement")
        D.delegate(
            parent,
            "reports",
            "How many hours?",
            "Venue is La Zwppa",
            db_session,
            db_session,
        )
        content = captured["kwargs"]["messages_override"][0]["content"]
        assert "La Zwppa" in content and "How many hours?" in content

    def test_only_the_summary_comes_back(self, db_session, captured):
        """Returning the child's transcript would blow the parent's context."""
        parent = _thread(db_session, domain="procurement")
        out = D.delegate(parent, "reports", "q", None, db_session, db_session)

        assert out["summary"] == "Rostered 283.3 hours next week."
        assert len(str(out)) < 1000, "the child's transcript leaked into the result"
        assert "display_blocks" not in out
        assert "conversation" not in out

    def test_child_thread_records_lineage(self, db_session, captured):
        parent = _thread(db_session, domain="procurement", delegation_depth=0)
        out = D.delegate(parent, "reports", "q", None, db_session, db_session)

        child = (
            db_session.query(Thread).filter(Thread.id == out["child_thread_id"]).one()
        )
        assert child.parent_thread_id == parent.id
        assert child.delegation_depth == 1
        assert child.domain == "reports"
        assert child.has_tag("delegated")

    def test_child_runs_with_a_lower_iteration_cap(self, db_session, captured):
        """One user turn must not be able to stack two full-length loops."""
        parent = _thread(db_session, domain="procurement")
        D.delegate(parent, "reports", "q", None, db_session, db_session)
        assert captured["kwargs"]["max_iterations"] == D.CHILD_MAX_ITERATIONS
        assert D.CHILD_MAX_ITERATIONS < 10

    def test_empty_child_answer_still_returns_something(
        self, db_session, monkeypatch, captured
    ):
        monkeypatch.setattr(
            "app.agents.tool_loop.run_tool_loop",
            lambda *a, **k: {"message": "   "},
        )
        parent = _thread(db_session, domain="procurement")
        out = D.delegate(parent, "reports", "q", None, db_session, db_session)
        assert "no answer" in out["summary"]

    def test_child_events_do_not_reach_the_parents_stream(
        self, db_session, monkeypatch, seeded_specs
    ):
        """Summary-only: the user sees one chip, not the child's internals."""
        from app.agents import tool_loop

        seen = []
        tool_loop.set_event_callback(lambda e: seen.append(e))

        def noisy(message, task, db, system_prompt, tools, **kw):
            tool_loop._emit_event({"type": "thinking", "text": "child internals"})
            return {"message": "done"}

        monkeypatch.setattr("app.agents.tool_loop.run_tool_loop", noisy)
        monkeypatch.setattr(
            D,
            "build_child_tools",
            lambda *a: ("p", [{"name": "test_delegation__get_roster"}]),
        )
        try:
            parent = _thread(db_session, domain="procurement")
            D.delegate(parent, "reports", "q", None, db_session, db_session)
            assert seen == [], f"child leaked events to the parent: {seen}"

            # ...and the parent's own stream still works afterwards.
            tool_loop._emit_event({"type": "thinking", "text": "parent"})
            assert len(seen) == 1
        finally:
            tool_loop.set_event_callback(None)

    def test_a_target_with_no_read_tools_is_refused(
        self, db_session, monkeypatch, seeded_specs
    ):
        monkeypatch.setattr(D, "build_child_tools", lambda *a: ("p", []))
        parent = _thread(db_session, domain="procurement")
        with pytest.raises(D.DelegationError, match="no read-only tools"):
            D.delegate(parent, "reports", "q", None, db_session, db_session)
