"""Projection of config rows onto MCP tool schemas.

The write-signal tests are the security-critical ones. The MCP read/draft
boundary is derived, not configured — so if derivation is wrong, the boundary
is decorative.
"""

import pytest

from app.mcp.projection import (
    ALWAYS_EXPOSE,
    MCP_DENYLIST,
    McpTool,
    default_tool_name,
    exposable_reason,
    is_read_tool,
    suggest_scopes,
    to_mcp_tool_dict,
    write_signals,
)
from app.mcp.scopes import ACCESS_READ


def _tool(action, method="GET", **extra):
    return {"action": action, "method": method, **extra}


class TestWriteSignalsMethod:
    @pytest.mark.parametrize("method", ["GET", "HEAD", "get"])
    def test_read_methods(self, method):
        assert write_signals(_tool("get_sales", method)) == []

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_write_methods(self, method):
        assert f"method is {method}" in write_signals(_tool("fetch_thing", method))

    def test_missing_method_defaults_to_write(self):
        """Fail closed: an absent method must not read as GET."""
        assert write_signals({"action": "fetch_thing"}) != []


class TestWriteSignalsBeyondMethod:
    """`method: GET` is not sufficient. These are real rows from the config DB.

    Every execution_mode="internal" tool declares GET, including ones that
    plainly write, so corroborating signals are what make the boundary real.
    """

    def test_consolidator_write_actions_beat_a_get(self):
        """loadedhub.review_and_receive_invoices: GET, but receives invoices."""
        signals = write_signals(
            _tool(
                "review_and_receive_invoices",
                "GET",
                consolidator_config={"allowed_write_actions": ["receive_invoice"]},
            )
        )
        assert any("allowed_write_actions" in s for s in signals)
        assert not is_read_tool(
            _tool(
                "review_and_receive_invoices",
                "GET",
                consolidator_config={"allowed_write_actions": ["receive_invoice"]},
            )
        )

    def test_working_document_beats_a_get(self):
        """norm.create_purchase_order: GET, but creates a draft."""
        signals = write_signals(
            _tool("create_purchase_order", "GET", working_document={"doc_type": "po"})
        )
        assert any("draft" in s for s in signals)

    def test_empty_consolidator_write_actions_is_not_a_signal(self):
        assert (
            write_signals(
                _tool("get_x", "GET", consolidator_config={"allowed_write_actions": []})
            )
            == []
        )


class TestMutatingVerbs:
    @pytest.mark.parametrize(
        "action",
        [
            "create_order",
            "update_task_config",
            # norm_email.send_report_email: a real config row declared GET,
            # but it sends an email.
            "send_report_email",
            "set_override",
        ],
    )
    def test_leading_verb_is_a_write(self, action):
        assert not is_read_tool(_tool(action, "GET"))

    @pytest.mark.parametrize(
        "action",
        [
            "get_best_time_to_post",  # "post" is a noun here
            "get_scheduled_posts",  # "posts" != "post"
            "get_received_invoices",  # "received" != "receive"
            "list_received_invoices",
            "get_sales_data",
            "resolve_dates",
            "check_stock",
            "reconcile_statements",
            "calculate_template_stock_requirements",
            "download_invoice_file",
        ],
    )
    def test_reads_are_not_false_positives(self, action):
        assert is_read_tool(_tool(action, "GET")), (
            f"{action} wrongly flagged as a write"
        )

    def test_strong_verb_matches_anywhere(self):
        """`review_and_receive_invoices` has an innocuous leading verb, so a
        first-token-only rule would miss it."""
        assert not is_read_tool(_tool("review_and_receive_invoices", "GET"))

    def test_weak_verb_only_matches_in_verb_position(self):
        assert is_read_tool(_tool("get_best_time_to_post", "GET"))
        assert not is_read_tool(_tool("post_update", "GET"))


class TestExposableReason:
    def test_read_tool_is_exposable(self):
        assert exposable_reason("connector", _tool("get_sales", "GET")) is None

    def test_reason_cites_the_evidence(self):
        reason = exposable_reason("connector", _tool("create_order", "POST"))
        assert "method is POST" in reason
        assert "create" in reason
        # And points at the alternative rather than just refusing.
        assert "playbook" in reason

    def test_playbooks_are_always_exposable(self):
        """A playbook runs Norm's own tool loop, so drafts and approval apply."""
        assert exposable_reason("playbook", {}) is None


class TestSuggestScopes:
    @pytest.mark.parametrize(
        "connector,action,expected",
        [
            # POS / sales — including the "orders" that are actually sales.
            ("loadedhub", "get_sales_data", "mcp:reports:read"),
            ("loadedhub", "get_pos_orders", "mcp:reports:read"),
            ("loadedhub", "get_pos_item_sales", "mcp:reports:read"),
            ("loadedhub", "get_staff_orders", "mcp:reports:read"),
            ("loadedhub", "get_staff_item_orders", "mcp:reports:read"),
            ("loadedhub", "get_pos_discounts", "mcp:reports:read"),
            # Procurement / stock — must NOT be caught by the sales "order" rule.
            ("loadedhub", "get_stock_items", "mcp:orders:read"),
            ("loadedhub", "get_stock_item", "mcp:orders:read"),
            ("loadedhub", "get_supplier_invoices", "mcp:orders:read"),
            # Roster and HR.
            ("loadedhub", "get_roster", "mcp:roster:read"),
            ("bamboohr", "get_employees", "mcp:hr:read"),
        ],
    )
    def test_natural_scope(self, connector, action, expected):
        assert suggest_scopes(connector, action) == [expected]

    def test_no_match_returns_empty(self):
        # A domain with no scope (marketing/social) gets no confident guess.
        assert suggest_scopes("orbit", "get_social_metrics") == []

    def test_playbook_drafting_uses_draft_scope(self):
        assert suggest_scopes(
            "create_stock_order",
            "",
            "Create a purchase order for stock items",
            drafts=True,
        ) == ["mcp:orders:draft"]

    def test_playbook_read_analysis_stays_read(self):
        # An analysis playbook reads; it does not draft, so the read scope holds.
        assert suggest_scopes(
            "cogs_analysis", "", "Analyse cost of goods and margins", drafts=True
        ) == ["mcp:reports:read"]

    def test_connector_never_suggests_draft(self):
        # drafts defaults False for connectors, so even a create-y name stays read.
        assert suggest_scopes("loadedhub", "get_stock_items") == ["mcp:orders:read"]


class TestDenylistAndAlwaysExpose:
    @pytest.mark.parametrize(
        "pair",
        [
            ("norm", "search_tool_result"),
            ("norm_reports", "render_chart"),
            ("norm", "show_roster"),
            ("norm", "show_orders"),
        ],
    )
    def test_conversation_scoped_tools_denylisted(self, pair):
        assert pair in MCP_DENYLIST

    def test_denylist_and_always_expose_are_disjoint(self):
        assert not (MCP_DENYLIST & ALWAYS_EXPOSE)


class TestToolNaming:
    def test_connector_convention(self):
        assert default_tool_name("connector", "loadedhub", "get_sales") == (
            "loadedhub__get_sales"
        )

    def test_playbook_convention(self):
        assert default_tool_name("playbook", "weekly_review", "") == (
            "norm_playbook__weekly_review"
        )


class TestToMcpToolDict:
    def _mk(self, **kw):
        base = dict(
            name="loadedhub__get_sales",
            kind="connector",
            connector="loadedhub",
            action="get_sales",
            playbook_slug=None,
            method="GET",
            access=ACCESS_READ,
            scopes=frozenset({"mcp:reports:read"}),
            description="Sales totals per trading day",
            input_schema={"type": "object", "properties": {}},
        )
        base.update(kw)
        return McpTool(**base)

    def test_shape(self):
        """MCP spelling (`inputSchema`, not Anthropic's `input_schema` — the
        exact key set pins that), a read-only hint, and never destructive in
        v1."""
        d = to_mcp_tool_dict(self._mk())
        assert set(d) == {"name", "description", "inputSchema", "annotations"}
        assert d["inputSchema"]["type"] == "object"
        assert d["annotations"]["readOnlyHint"] is True
        assert d["annotations"]["destructiveHint"] is False

    def test_no_get_prefix_leaks_into_the_description(self):
        """The `[GET]` prefix exists only so tool_loop can re-parse the method
        out of its own description. An MCP client has no such parser."""
        d = to_mcp_tool_dict(self._mk())
        assert not d["description"].startswith("[")
        assert "[GET]" not in d["description"]


def _own(db, tools):
    """One App owning these ``connector.action`` keys — the App Map is what
    exposes a tool."""
    from app.db.config_models import MarketplaceApp

    db.add(
        MarketplaceApp(
            slug="owner-app",
            name="Owner",
            description="",
            tier="app",
            bundled=True,
            composition={"member": "procurement", "tools": tools},
        )
    )


class TestAlwaysExposeIgnoresExposure:
    """ALWAYS_EXPOSE must not depend on the in-app agents' exposure.

    Step 1 of project_tools gates on `_collect_tools` — the agents' own tool
    list. So an action the agents don't get vanished from MCP however
    ALWAYS_EXPOSE was set — a documented promise made quietly conditional on
    an unrelated config row. It went live the day resolve_dates left the
    agents' menus (Sep 2026): their own tools take `period` in plain English
    now, while an MCP client has no Norm prompt telling it today's date, and
    the MCP instructions tell every client to call exactly this tool.
    (Written when bindings were the exposure list; the App Map is now.)
    """

    def _seed(self, db, *, own_resolve_dates: bool):
        from app.db.models import ConnectionSpec, McpCapability

        db.add(
            ConnectionSpec(
                connector_name="norm",
                display_name="Norm",
                auth_type="none",
                execution_mode="internal",
                enabled=True,
                tools=[
                    {
                        "action": "resolve_dates",
                        "method": "GET",
                        "description": "Resolve a phrase to a window",
                    },
                    {
                        "action": "get_working_document",
                        "method": "GET",
                        "description": "Read a working document",
                    },
                    {
                        "action": "get_attachment",
                        "method": "GET",
                        "description": "Read an attachment",
                    },
                ],
            )
        )
        owned = ["norm.get_working_document"]
        if own_resolve_dates:
            owned.append("norm.resolve_dates")
        _own(db, owned)
        # get_attachment is curated but owned by NO App: the control.
        for action in ("resolve_dates", "get_working_document", "get_attachment"):
            db.add(
                McpCapability(
                    kind="connector",
                    target="norm",
                    action=action,
                    scopes=["mcp:reports:read"],
                    enabled=True,
                )
            )
        db.flush()

    def _actions(self, db, scopes=("mcp:reports:read",)):
        from app.mcp.projection import project_tools

        return {
            t.action
            for t in project_tools(
                db,
                db,
                user_id=None,
                granted_scopes=frozenset(scopes),
                venue_names=["V"],
            )
        }

    def test_survives_having_no_owner_at_all(self, db_session):
        self._seed(db_session, own_resolve_dates=False)
        assert "resolve_dates" in self._actions(db_session)

    def test_a_normal_tool_still_needs_its_owner(self, db_session):
        """The negative control: get_attachment is curated and its scope is
        granted, but no App owns it. This lifts the exposure requirement
        for ALWAYS_EXPOSE alone, not for everything."""
        self._seed(db_session, own_resolve_dates=False)
        actions = self._actions(db_session)
        assert "get_attachment" not in actions
        assert "get_working_document" in actions, "an owned tool is unaffected"

    def test_scope_bypass_still_holds_too(self, db_session):
        """The older promise: exposed regardless of granted scopes. Both
        bypasses have to hold at once, or an unscoped client loses it."""
        self._seed(db_session, own_resolve_dates=False)
        assert self._actions(db_session, scopes=()) == {"resolve_dates"}

    def test_an_app_claim_cannot_put_it_back_on_an_agent_menu(self, db_session):
        """Removing it from the agents' exposure is not enough on its own.

        When bindings were the exposure list, a binding with an EMPTY
        capabilities list exposed every action on its connector —
        executive_chef/norm was exactly that — so unbinding left resolve_dates
        on the agent menu anyway. An App claim is the same risk today.
        _ENGINE_AND_MCP_ONLY is what actually takes it off, and MCP has to
        survive that removal too.
        """
        from app.agents.prompt_builder import _collect_tools

        self._seed(db_session, own_resolve_dates=True)
        collected = {
            t["action"]
            for t in _collect_tools(db_session, user_id=None, config_db=db_session)
        }
        assert "get_working_document" in collected, "the App's other tool works"
        assert "resolve_dates" not in collected, "but it cannot re-expose this one"
        # MCP keeps it, beside the owned tool (the owned case is unchanged)
        assert {"resolve_dates", "get_working_document"} <= self._actions(db_session)
