"""Playbook workflow tools — projection and outcome mapping.

A curated playbook becomes one natural-language MCP tool that runs Norm's own
agent + tool loop, so drafts and the approval gate stay in Norm.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.mcp.principal import McpPrincipal
from app.mcp import workflows


def _principal(user_id="u1"):
    return McpPrincipal(
        user_id=user_id,
        organization_id="org1",
        venue_ids=("v1",),
        scopes=frozenset({"mcp:reports:read"}),
    )


def _real_user(db):
    from app.db.models import User

    u = User(
        id=str(uuid.uuid4()),
        email=f"wf-{uuid.uuid4().hex[:8]}@x.com",
        hashed_password="x",
        full_name="WF",
        role="user",
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


class TestPlaybookProjection:
    def test_enabled_playbook_projects_as_a_workflow_tool(self, db_session):
        from app.db.config_models import McpCapability, Playbook
        from app.mcp.projection import project_tools

        db_session.add(
            Playbook(
                id=str(uuid.uuid4()),
                slug="weekly_review",
                agent_slug="reports",
                display_name="Weekly Review",
                description="Generate a weekly sales summary",
                instructions="...",
                enabled=True,
            )
        )
        db_session.add(
            McpCapability(
                id=str(uuid.uuid4()),
                kind="playbook",
                target="weekly_review",
                action="",
                scopes=["mcp:reports:read"],
                enabled=True,
            )
        )
        db_session.flush()

        with patch("app.mcp.projection._collect_tools", return_value=[]):
            tools = project_tools(
                db_session,
                db_session,
                user_id="u1",
                granted_scopes=frozenset({"mcp:reports:read"}),
                venue_names=["La Zeppa"],
            )
        pb = [t for t in tools if t.kind == "playbook"]
        assert len(pb) == 1
        t = pb[0]
        assert t.name == "norm_playbook__weekly_review"
        assert t.access == "draft"
        assert not t.is_read_only
        assert list(t.input_schema["properties"]) == ["request"]  # NL, single venue
        assert t.description == "Generate a weekly sales summary"

    def test_disabled_playbook_does_not_project(self, db_session):
        from app.db.config_models import McpCapability, Playbook
        from app.mcp.projection import project_tools

        db_session.add(
            Playbook(
                id=str(uuid.uuid4()),
                slug="pb2",
                agent_slug="reports",
                display_name="X",
                description="d",
                instructions="i",
                enabled=False,
            )
        )
        db_session.add(
            McpCapability(
                id=str(uuid.uuid4()),
                kind="playbook",
                target="pb2",
                action="",
                scopes=["mcp:reports:read"],
                enabled=True,
            )
        )
        db_session.flush()
        with patch("app.mcp.projection._collect_tools", return_value=[]):
            tools = project_tools(
                db_session,
                db_session,
                user_id="u1",
                granted_scopes=frozenset({"mcp:reports:read"}),
            )
        assert not [t for t in tools if t.kind == "playbook"]

    def test_out_of_scope_playbook_omitted(self, db_session):
        from app.db.config_models import McpCapability, Playbook
        from app.mcp.projection import project_tools

        db_session.add(
            Playbook(
                id=str(uuid.uuid4()),
                slug="pb3",
                agent_slug="hr",
                display_name="X",
                description="d",
                instructions="i",
                enabled=True,
            )
        )
        db_session.add(
            McpCapability(
                id=str(uuid.uuid4()),
                kind="playbook",
                target="pb3",
                action="",
                scopes=["mcp:hr:read"],
                enabled=True,
            )
        )
        db_session.flush()
        with patch("app.mcp.projection._collect_tools", return_value=[]):
            tools = project_tools(
                db_session,
                db_session,
                user_id="u1",
                granted_scopes=frozenset({"mcp:reports:read"}),  # not hr
            )
        assert not [t for t in tools if t.kind == "playbook"]


class TestOutcomeMapping:
    """_map_outcome maps final thread state to an MCP outcome payload.

    Tested directly (pure over a thread + optional doc) rather than through the
    worker thread, whose own SessionLocal can't see the test transaction.
    """

    def _thread(self, db, status="completed"):
        from app.db.models import Thread

        u = _real_user(db)
        t = Thread(
            id=str(uuid.uuid4()),
            user_id=u.id,
            domain="reports",
            intent="reports.mcp_playbook",
            status=status,
            raw_prompt="x",
            title="[MCP] X",
            extracted_fields={},
            missing_fields=[],
        )
        db.add(t)
        db.flush()
        return t

    def test_completed(self, db_session):
        from app.mcp.workflows import _map_outcome

        t = self._thread(db_session, status="completed")
        p = _map_outcome(db_session, t, {"message": "done"})
        assert p["status"] == "completed"
        assert "?thread=" in p["open_in_norm"]

    def test_draft_created(self, db_session):
        from app.db.models import WorkingDocument
        from app.mcp.workflows import _map_outcome

        t = self._thread(db_session, status="in_progress")
        doc = WorkingDocument(
            id=str(uuid.uuid4()),
            thread_id=t.id,
            doc_type="purchase_order",
            connector_name="bidfood",
            data={},
        )
        db_session.add(doc)
        db_session.flush()
        p = _map_outcome(db_session, t, {"message": "drafted"})
        assert p["status"] == "draft_created"
        assert p["working_document_id"] == doc.id
        assert "?doc=" in p["open_in_norm"]
        assert "waiting in Norm" in p["note"]

    def test_pending_approval(self, db_session):
        from app.mcp.workflows import _map_outcome

        t = self._thread(db_session, status="awaiting_tool_approval")
        p = _map_outcome(db_session, t, {"message": "needs approval"})
        assert p["status"] == "pending_approval"
        assert "approval" in p["note"].lower()

    def test_wholly_ambiguous_order_asks_instead_of_claiming_success(self, db_session):
        """ "24 coronas" matched nothing (Corona Extra vs Corona 0%), so the
        draft is empty. It must come back as needs_input carrying the options —
        not draft_created, which made the model say "done" over an empty card."""
        from app.db.models import WorkingDocument
        from app.mcp.workflows import _map_outcome

        t = self._thread(db_session, status="completed")
        doc = WorkingDocument(
            id=str(uuid.uuid4()),
            thread_id=t.id,
            doc_type="order",
            connector_name="norm",
            data={
                "venue": "La Zeppa",
                "order_lines": [],
                "needs_selection": [
                    {
                        "query": "corona",
                        "quantity": 24,
                        "candidates": [
                            {"id": "1", "name": "Corona 0%"},
                            {"id": "2", "name": "CORONA EXTRA BEER 330ML"},
                        ],
                    }
                ],
            },
        )
        db_session.add(doc)
        db_session.flush()
        p = _map_outcome(db_session, t, {"message": "created"})
        assert p["status"] == "needs_input"
        assert "working_document_id" not in p  # no empty editor renders
        assert p["clarify"][0]["options"] == ["Corona 0%", "CORONA EXTRA BEER 330ML"]
        assert "which" in p["summary"].lower()
        assert "not tell the user the order was created" in p["note"].lower()

    def test_partially_resolved_order_still_drafts(self, db_session):
        """Some lines matched — that's a real draft; render it, don't block on
        the one ambiguous item."""
        from app.db.models import WorkingDocument
        from app.mcp.workflows import _map_outcome

        t = self._thread(db_session, status="completed")
        doc = WorkingDocument(
            id=str(uuid.uuid4()),
            thread_id=t.id,
            doc_type="order",
            connector_name="norm",
            data={
                "order_lines": [{"itemId": "beer-1", "quantity": 2}],
                "needs_selection": [{"query": "corona", "candidates": []}],
            },
        )
        db_session.add(doc)
        db_session.flush()
        p = _map_outcome(db_session, t, {"message": "drafted"})
        assert p["status"] == "draft_created"
        assert p["working_document_id"] == doc.id

    def test_unfindable_item_asks_rather_than_empty_draft(self, db_session):
        from app.db.models import WorkingDocument
        from app.mcp.workflows import _map_outcome

        t = self._thread(db_session, status="completed")
        doc = WorkingDocument(
            id=str(uuid.uuid4()),
            thread_id=t.id,
            doc_type="order",
            connector_name="norm",
            data={
                "order_lines": [],
                "resolution_report": {"failed": [{"name": "unicorn tears"}]},
            },
        )
        db_session.add(doc)
        db_session.flush()
        p = _map_outcome(db_session, t, {"message": "created"})
        assert p["status"] == "needs_input"
        assert "unicorn tears" in p["unfindable"]

    def test_unknown_playbook(self, db_session):
        payload = workflows.execute_playbook_tool(
            "nope", "x", None, _principal(), db_session, db_session
        )
        assert "error" in payload
        assert payload["code"] == "NOT_FOUND"


class TestWorkflowTimeout:
    """A slow workflow returns 'running' rather than blocking past the client."""

    def test_slow_loop_returns_running(self, db_session, monkeypatch):
        import time as _time
        from types import SimpleNamespace

        from app.db.config_models import Playbook
        from app.mcp import workflows as wf

        db_session.add(
            Playbook(
                id=str(uuid.uuid4()),
                slug="slowpb",
                agent_slug="reports",
                display_name="Slow",
                description="d",
                instructions="i",
                enabled=True,
            )
        )
        user = _real_user(db_session)
        db_session.flush()

        # Tiny timeout; the worker sleeps past it.
        monkeypatch.setattr(wf, "WORKFLOW_TIMEOUT_S", 0.2)
        fake_agent = SimpleNamespace(
            get_tool_definitions=lambda *a, **k: ("sys", []),
            build_context=lambda *a, **k: {},
        )

        def slow_loop(*a, **k):
            _time.sleep(2)
            return {"message": "eventually"}

        monkeypatch.setattr("app.agents.registry.get_agent", lambda s: fake_agent)
        monkeypatch.setattr("app.agents.tool_loop.run_tool_loop", slow_loop)

        payload = wf.execute_playbook_tool(
            "slowpb", "do it", None, _principal(user.id), db_session, db_session
        )
        assert payload["status"] == "running"
        assert "?thread=" in payload["open_in_norm"]
