"""Playbooks are guides the agent opens, never a gate on its tools.

The router used to pick a playbook before the agent saw the question, and the
pick replaced the agent's prompt and narrowed its tools to the playbook's list.
"Fruit and veg used, Apr-Jun" was routed to the COGS playbook, whose three
tools could not answer it, while get_received_items_for_period — which could —
was withheld (prod thread 8de7df19, 23 Sep 2026).

Now, modelled on Claude's Skills: the agent sees every playbook's name and when
to use it, opens one with norm__read_playbook when a request matches, and keeps
every tool either way. A Claude-initiated MCP workflow run is the one place a
boundary remains, and it is a single rule over each tool's own definition.
"""

from app.agents.prompt_builder import playbook_guidance
from app.db.config_models import Playbook


def _playbook(db, slug, enabled=True, description="when to use it"):
    db.add(
        Playbook(
            slug=slug,
            agent_slug="reports",
            display_name=slug.replace("_", " ").title(),
            description=description,
            instructions=f"steps for {slug}",
            enabled=enabled,
        )
    )
    db.flush()


class TestThePlaybookMenu:
    def test_lists_every_enabled_playbook_with_when_to_use_it(self, db_session):
        _playbook(db_session, "cogs_analysis", description="Menu-item cost and margin")
        _playbook(db_session, "stock_order")

        menu, tool = playbook_guidance(db_session)

        assert "`cogs_analysis`: Menu-item cost and margin" in menu
        assert "`stock_order`" in menu
        assert "not a limit" in menu  # guidance, never a gate
        assert tool["name"] == "norm__read_playbook"
        assert tool["description"].startswith("[GET]")  # a read: no approval card
        assert set(tool["input_schema"]["properties"]["slug"]["enum"]) == {
            "cogs_analysis",
            "stock_order",
        }

    def test_a_disabled_playbook_is_not_offered(self, db_session):
        _playbook(db_session, "live")
        _playbook(db_session, "parked", enabled=False)

        menu, tool = playbook_guidance(db_session)

        assert "parked" not in menu
        assert tool["input_schema"]["properties"]["slug"]["enum"] == ["live"]

    def test_no_playbooks_means_no_menu_and_no_tool(self, db_session):
        assert playbook_guidance(db_session) == ("", None)


class TestReadingAPlaybook:
    def _handler(self, db_session, monkeypatch):
        import app.db.engine as engine_mod
        from app.agents.internal_tools import get_handler

        class _Session:
            def query(self, *a, **k):
                return db_session.query(*a, **k)

            def close(self):
                pass

        monkeypatch.setattr(engine_mod, "_ConfigSessionLocal", lambda: _Session())
        return get_handler("norm", "read_playbook")

    def test_returns_the_full_instructions(self, db_session, monkeypatch):
        _playbook(db_session, "cogs_analysis")
        result = self._handler(db_session, monkeypatch)(
            {"slug": "cogs_analysis"}, db_session, None
        )
        assert result["success"] is True
        assert result["data"]["instructions"] == "steps for cogs_analysis"

    def test_a_disabled_or_unknown_playbook_is_an_error(self, db_session, monkeypatch):
        _playbook(db_session, "parked", enabled=False)
        handler = self._handler(db_session, monkeypatch)
        assert handler({"slug": "parked"}, db_session, None)["success"] is False
        assert handler({"slug": "nope"}, db_session, None)["success"] is False


class TestClaudeWorkflowRunBoundary:
    """What a Claude-initiated workflow run may hold (mcp.workflows)."""

    DEFS = {
        ("loadedhub", "get_sales"): {"action": "get_sales", "method": "GET"},
        ("norm_email", "send_report_email"): {
            "action": "send_report_email",
            "method": "GET",  # declared GET, yet it sends — no approval card
        },
        ("norm", "remember"): {"action": "remember", "method": "POST"},
        ("loadedhub", "update_shift"): {"action": "update_shift", "method": "PUT"},
        ("norm", "create_purchase_order"): {
            "action": "create_purchase_order",
            "method": "GET",
            "working_document": {"doc_type": "purchase_order"},
        },
        ("loadedhub", "receive_loadedhub_invoice"): {
            "action": "receive_loadedhub_invoice",
            "method": "GET",
            "working_document": {"doc_type": "invoice"},
            "consolidator_config": {"allowed_write_actions": ["review_invoices"]},
        },
        ("loadedhub", "review_and_receive_invoices"): {
            "action": "review_and_receive_invoices",
            "method": "GET",
            "consolidator_config": {"allowed_write_actions": ["receive_invoice"]},
        },
    }

    def _kept(self, monkeypatch):
        import app.mcp.projection as projection
        from app.mcp.workflows import safe_for_claude

        monkeypatch.setattr(projection, "raw_tool_defs", lambda cdb: self.DEFS)
        names = [f"{c}__{a}" for c, a in self.DEFS] + ["norm__read_playbook"]
        return {t["name"] for t in safe_for_claude([{"name": n} for n in names], None)}

    def test_reads_drafts_and_approval_gated_writes_are_kept(self, monkeypatch):
        kept = self._kept(monkeypatch)
        assert "loadedhub__get_sales" in kept
        assert "norm__create_purchase_order" in kept  # a draft reviewed in Norm
        assert "loadedhub__update_shift" in kept  # suspends for approval in Norm
        assert "norm__read_playbook" in kept

    def test_a_workflow_mode_tool_keeps_the_autonomy_the_user_set(self, monkeypatch):
        assert "loadedhub__review_and_receive_invoices" in self._kept(monkeypatch)

    def test_actions_that_fire_immediately_stay_in_norm(self, monkeypatch):
        kept = self._kept(monkeypatch)
        assert "norm_email__send_report_email" not in kept
        assert "norm__remember" not in kept  # auto-approved write
        assert "loadedhub__receive_loadedhub_invoice" not in kept
