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

    def test_send_email_declared_get_is_still_a_write(self):
        """norm_email.send_report_email: GET, but sends an email."""
        assert not is_read_tool(_tool("send_report_email", "GET"))

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
        ["create_order", "update_task_config", "send_report_email", "set_override"],
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
            "create_stock_order", "", "Create a purchase order for stock items",
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

    def test_resolve_dates_always_exposed(self):
        """Norm owns date logic, so the client must always be able to ask."""
        assert ("norm", "resolve_dates") in ALWAYS_EXPOSE

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

    @pytest.mark.parametrize(
        "kind,target,action",
        [("connector", "loadedhub", "get_sales"), ("playbook", "weekly_review", "")],
    )
    def test_names_satisfy_the_mcp_name_constraint(self, kind, target, action):
        import re

        assert re.fullmatch(
            r"[a-zA-Z0-9_-]{1,64}", default_tool_name(kind, target, action)
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
        d = to_mcp_tool_dict(self._mk())
        assert set(d) == {"name", "description", "inputSchema", "annotations"}
        assert d["inputSchema"]["type"] == "object"

    def test_uses_mcp_spelling_not_anthropic(self):
        d = to_mcp_tool_dict(self._mk())
        assert "inputSchema" in d
        assert "input_schema" not in d

    def test_no_get_prefix_leaks_into_the_description(self):
        """The `[GET]` prefix exists only so tool_loop can re-parse the method
        out of its own description. An MCP client has no such parser."""
        d = to_mcp_tool_dict(self._mk())
        assert not d["description"].startswith("[")
        assert "[GET]" not in d["description"]

    def test_read_only_hint(self):
        assert to_mcp_tool_dict(self._mk())["annotations"]["readOnlyHint"] is True

    def test_never_destructive_in_v1(self):
        assert to_mcp_tool_dict(self._mk())["annotations"]["destructiveHint"] is False
