"""MCP capability drift checks in config_validator.

Pure functions over plain rows (mirrors test_config_validator.py), so CI runs
them with no config DB. They also run daily against the real rows via
/internal/validate-config — the only place that catches config edited after a
capability was enabled.
"""

from app.mcp.projection import MCP_DENYLIST
from app.mcp.scopes import MCP_SCOPES
from app.services.config_validator import check_mcp_capability

SCOPES = set(MCP_SCOPES)


def _check(kind, target, action, scopes, tool_def, pb_enabled):
    return check_mcp_capability(
        kind, target, action, scopes, tool_def, pb_enabled, SCOPES, MCP_DENYLIST
    )


class TestConnectorCapability:
    def test_healthy_read_tool_has_no_issues(self):
        assert (
            _check(
                "connector",
                "loadedhub",
                "get_sales_data",
                ["mcp:reports:read"],
                {"action": "get_sales_data", "method": "GET"},
                None,
            )
            == []
        )

    def test_renamed_action_is_caught(self):
        issues = _check(
            "connector", "loadedhub", "get_sales_data", ["mcp:reports:read"], None, None
        )
        assert any("no connector spec defines" in i.problem for i in issues)

    def test_action_that_became_a_write_is_caught(self):
        issues = _check(
            "connector",
            "loadedhub",
            "get_sales_data",
            ["mcp:reports:read"],
            {"action": "get_sales_data", "method": "POST"},
            None,
        )
        assert any("now writes" in i.problem for i in issues)

    def test_denylisted_tool_is_caught(self):
        target, action = next(iter(MCP_DENYLIST))
        issues = _check(
            "connector",
            target,
            action,
            ["mcp:reports:read"],
            {"action": action, "method": "GET"},
            None,
        )
        assert any("conversation-scoped" in i.problem for i in issues)


class TestScopes:
    def test_unknown_scope_is_caught(self):
        issues = _check(
            "connector",
            "loadedhub",
            "get_sales_data",
            ["mcp:bogus"],
            {"action": "get_sales_data", "method": "GET"},
            None,
        )
        assert any("unknown scope" in i.problem for i in issues)

    def test_empty_scopes_is_caught(self):
        issues = _check(
            "connector",
            "loadedhub",
            "get_sales_data",
            [],
            {"action": "get_sales_data", "method": "GET"},
            None,
        )
        assert any("no scopes" in i.problem for i in issues)


class TestPlaybookCapability:
    def test_enabled_playbook_ok(self):
        assert (
            _check("playbook", "weekly_review", "", ["mcp:reports:read"], None, True)
            == []
        )

    def test_disabled_playbook_is_caught(self):
        issues = _check(
            "playbook", "weekly_review", "", ["mcp:reports:read"], None, False
        )
        assert any("disabled" in i.problem for i in issues)

    def test_missing_playbook_is_caught(self):
        issues = _check("playbook", "nope", "", ["mcp:reports:read"], None, None)
        assert any("does not exist" in i.problem for i in issues)


class TestIssueShape:
    def test_where_and_fix_populated(self):
        issue = _check("playbook", "nope", "", ["mcp:reports:read"], None, None)[0]
        assert issue.where.startswith("mcp.")
        assert issue.severity == "error"
        assert issue.fix  # every issue must be actionable
