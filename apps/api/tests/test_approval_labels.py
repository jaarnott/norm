"""A tool's declared effect decides whether a person is asked.

Before Oct 2026 the agent loop asked only for non-GET tools, so every
GET-declared writer ran unasked — the report email, task changes, workflow-mode
changes, both invoice tools — and a "Test" run of a scheduled task sent real
email. Each tool now declares ``effect`` (read | draft | write) and, for a
write, an ``approval`` policy (scripts/sync_approval_labels.py).
"""

import json
import sys
import uuid
from pathlib import Path

import pytest

from app.agents import tool_loop as TL
from app.db.config_models import ConnectionSpec
from app.db.models import ToolCall
from app.services import approvals
from app.services.config_validator import check_approval_labels
from tests.conftest import _make_thread
from tests.test_tool_loop_unknown_tool import _Block, _Response

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.sync_approval_labels import labels  # noqa: E402

ASK = {"default": "ask", "allow_auto": True, "label": "email reports"}
AUTO = {"default": "auto", "allow_auto": False, "label": "remember things"}
TIERED = {
    "default": "ask",
    "label": "receive invoices",
    "levels": [
        {"id": "approve_all", "writes": False},
        {"id": "autopilot", "writes": True},
    ],
}


class TestGate:
    @pytest.mark.parametrize(
        "row,method,expected",
        [
            ({"effect": "read"}, "GET", "run"),
            ({"effect": "draft"}, "GET", "run"),
            ({"effect": "write", "approval": ASK}, "GET", "ask"),
            ({"effect": "write", "approval": AUTO}, "POST", "auto"),
            ({"effect": "write", "approval": TIERED}, "GET", "run"),
            ({"effect": "write"}, "GET", "ask"),  # no policy: ask
        ],
    )
    def test_a_labelled_tool_follows_its_label_not_its_method(
        self, row, method, expected
    ):
        assert approvals.gate(row, method, "c", "a") == expected

    @pytest.mark.parametrize(
        "method,connector,action,expected",
        [
            ("GET", "c", "a", "run"),
            ("PUT", "c", "a", "ask"),
            ("POST", "norm", "remember", "auto"),
        ],
    )
    def test_an_unlabelled_tool_keeps_the_old_rule(
        self, method, connector, action, expected
    ):
        assert approvals.gate(None, method, connector, action) == expected
        assert approvals.gate({}, method, connector, action) == expected


def _spec(db, row: dict) -> str:
    """A connector holding one tool row; returns the connector name."""
    name = f"t{uuid.uuid4().hex[:10]}"
    db.add(
        ConnectionSpec(
            connector_name=name,
            display_name=name,
            execution_mode="internal",
            auth_type="none",
            tools=[row],
        )
    )
    db.flush()
    return name


def _tool(connector, action, method="GET"):
    return {
        "name": f"{connector}__{action}",
        "description": f"[{method}] test tool",
        "input_schema": {"type": "object", "properties": {}},
    }


def _run(db, user, monkeypatch, connector, action, params=None, test_mode=False):
    """One model turn that calls the tool, then a second that ends."""
    calls = []

    def llm(system_prompt, messages, tools, db, thread_id, call_type):
        calls.append(1)
        if len(calls) == 1:
            return (
                _Response(
                    "tool_use",
                    [
                        _Block(
                            "tool_use",
                            name=f"{connector}__{action}",
                            id=f"toolu_{uuid.uuid4().hex[:8]}",
                            input=params or {"to": "dianna@cbhg.co.nz"},
                        )
                    ],
                ),
                None,  # no llm_calls row to point at
            )
        return (_Response("end_turn", [_Block("text", text="Done.")]), None)

    ran = []

    def fake_execute(tc, db, config_db=None):
        ran.append(dict(tc.input_params or {}))
        return {"ok": True}

    monkeypatch.setattr("app.interpreter.llm_interpreter.call_llm_with_tools", llm)
    monkeypatch.setattr(TL, "_execute_tool_call", fake_execute)
    monkeypatch.setattr(
        TL, "_execute_tool_call_in_thread", lambda tc_id, cb: (tc_id, {"ok": True})
    )
    thread = _make_thread(
        db, user, domain="norm", intent="norm.tool_use", status="in_progress"
    )
    TL.run_tool_loop(
        "go",
        thread,
        db,
        "sys",
        [_tool(connector, action)],
        config_db=db,
        test_mode=test_mode,
    )
    tcs = db.query(ToolCall).filter(ToolCall.thread_id == thread.id).all()
    return thread, tcs, ran


class TestTheLoopFollowsTheLabel:
    def test_a_get_declared_write_now_asks(self, db_session, admin_user, monkeypatch):
        conn = _spec(
            db_session,
            {
                "action": "send_report_email",
                "method": "GET",
                "effect": "write",
                "approval": ASK,
            },
        )
        thread, tcs, ran = _run(
            db_session, admin_user, monkeypatch, conn, "send_report_email"
        )
        assert thread.status == "awaiting_tool_approval"
        assert [tc.status for tc in tcs] == ["pending_approval"]
        assert ran == []

    def test_an_auto_write_runs_without_a_card(
        self, db_session, admin_user, monkeypatch
    ):
        conn = _spec(
            db_session,
            {
                "action": "remember",
                "method": "POST",
                "effect": "write",
                "approval": AUTO,
            },
        )
        thread, tcs, ran = _run(db_session, admin_user, monkeypatch, conn, "remember")
        assert thread.status != "awaiting_tool_approval"
        assert [tc.status for tc in tcs] == ["executed"]
        assert len(ran) == 1

    def test_a_test_run_simulates_a_get_declared_write(
        self, db_session, admin_user, monkeypatch
    ):
        """A "Test" run of a task used to send the report email for real."""
        conn = _spec(
            db_session,
            {
                "action": "send_report_email",
                "method": "GET",
                "effect": "write",
                "approval": ASK,
            },
        )
        thread, tcs, ran = _run(
            db_session,
            admin_user,
            monkeypatch,
            conn,
            "send_report_email",
            test_mode=True,
        )
        assert ran == []
        assert tcs[0].result_payload["simulated"] is True
        assert thread.status != "awaiting_tool_approval"

    def test_a_test_run_runs_a_tiered_tool_at_its_level_that_writes_nothing(
        self, db_session, admin_user, monkeypatch
    ):
        conn = _spec(
            db_session,
            {
                "action": "review_and_receive_invoices",
                "method": "GET",
                "effect": "write",
                "approval": TIERED,
            },
        )
        _, tcs, _ = _run(
            db_session,
            admin_user,
            monkeypatch,
            conn,
            "review_and_receive_invoices",
            params={"venue": "La Zeppa", "mode": "autopilot"},
            test_mode=True,
        )
        assert tcs[0].input_params == {"venue": "La Zeppa", "mode": "approve_all"}


class TestEveryClaimedToolIsLabelled:
    def test_the_installed_labels_pass_the_check(self):
        rows = [
            {"action": key.partition(".")[2], **fields}
            for key, fields in labels().items()
        ]
        by_connector: dict[str, list] = {}
        for key, row in zip(labels(), rows):
            by_connector.setdefault(key.partition(".")[0], []).append(row)
        claims = {key: "some-app" for key in labels()}
        assert check_approval_labels(list(by_connector.items()), claims) == []

    def test_a_claimed_tool_without_an_effect_is_an_error(self):
        issues = check_approval_labels(
            [("loadedhub", [{"action": "get_sales", "method": "GET"}])],
            {"loadedhub.get_sales": "loaded-reports"},
        )
        assert [i.where for i in issues] == ["loadedhub.get_sales"]

    def test_a_write_needs_a_label_and_a_safe_first_level(self):
        issues = check_approval_labels(
            [
                (
                    "loadedhub",
                    [
                        {"action": "w", "effect": "write", "approval": {}},
                        {
                            "action": "t",
                            "effect": "write",
                            "approval": {
                                "label": "x",
                                "levels": [{"id": "autopilot", "writes": True}],
                            },
                        },
                    ],
                )
            ],
            {"loadedhub.w": "a", "loadedhub.t": "a"},
        )
        assert sorted(i.where for i in issues) == ["loadedhub.t", "loadedhub.w"]

    def test_unclaimed_rows_are_not_checked(self):
        assert check_approval_labels([("c", [{"action": "x"}])], {}) == []


class TestEveryReaderUsesTheLabel:
    def test_mcp_treats_a_labelled_write_as_a_write(self):
        from app.mcp.projection import write_signals

        row = {"action": "get_x", "method": "GET", "effect": "write"}
        assert any("declared effect" in s for s in write_signals(row))
        assert (
            write_signals({"action": "get_x", "method": "GET", "effect": "read"}) == []
        )

    def test_claude_runs_drop_only_auto_writes(self, monkeypatch):
        import app.mcp.projection as projection
        from app.mcp.workflows import safe_for_claude

        defs = {
            ("norm_email", "send_report_email"): {
                "action": "send_report_email",
                "method": "GET",
                "effect": "write",
                "approval": ASK,
            },
            ("norm", "remember"): {
                "action": "remember",
                "method": "POST",
                "effect": "write",
                "approval": AUTO,
            },
        }
        monkeypatch.setattr(projection, "raw_tool_defs", lambda cdb: defs)
        tools = [{"name": "norm_email__send_report_email"}, {"name": "norm__remember"}]
        kept = [t["name"] for t in safe_for_claude(tools, None)]
        # The email now asks for approval in Norm, so a Claude run may hold it.
        assert kept == ["norm_email__send_report_email"]

    def test_an_app_calling_a_get_declared_write_needs_write_approval(self, db_session):
        from app.services.app_runtime import _tool_writes

        conn = _spec(
            db_session,
            {"action": "w", "method": "GET", "effect": "write", "approval": ASK},
        )
        assert _tool_writes(db_session, conn, "w", "GET") is True
        draft = _spec(db_session, {"action": "d", "method": "GET", "effect": "draft"})
        assert _tool_writes(db_session, draft, "d", "GET") is False

    def test_the_app_map_badge_follows_the_label(self, db_session):
        """'Can make changes' on the Team page used to be false for every
        GET-declared writer."""
        from app.services.agent_catalog import _CapabilityContext

        conn = _spec(
            db_session,
            {"action": "w", "method": "GET", "effect": "write", "approval": ASK},
        )
        ctx = _CapabilityContext(db_session)
        assert ctx.tool(f"{conn}.w")["writes"] is True

    def test_an_unknown_effect_is_no_label(self):
        from app.connectors import spec_rows

        assert spec_rows.effect({"effect": "nonsense"}) is None
        assert json.dumps(spec_rows.approval({"approval": "x"})) == "{}"
