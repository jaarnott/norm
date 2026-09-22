"""One capable interactive agent, but unattended runs stay scoped.

The multi-agent router/handoff was folded into a single agent that, in an
INTERACTIVE chat, sees the full entitled tool union (a human is present to
approve any write). Two safety invariants survive that change and are pinned
here:

1. An UNATTENDED run (a scheduled task, a "Run Now") whose task carries no
   explicit tool_filter falls back to its agent's OWN scope, never the union —
   otherwise a report task could reach the GET-method writes
   (`create_purchase_order`, `send_report_email`) that auto-fire without an
   approval card. `default_tool_filter` is that fallback.
2. `delegate_to_agent` — whose handler was deleted with the delegation
   subsystem — is never offered again, even while the config-DB spec still
   lists it, because a tool with no handler would fail on call.
"""

from app.agents.prompt_builder import _RETIRED_ACTIONS
from app.db.config_models import AgentConnectionBinding
from app.services.agent_config_service import default_tool_filter


class TestUnattendedRunsStayScoped:
    def test_default_tool_filter_is_the_agents_own_scope_not_the_union(
        self, db_session
    ):
        db_session.add(
            AgentConnectionBinding(
                agent_slug="reports",
                connector_name="norm",
                capabilities=[
                    {"action": "get_sales", "enabled": True},
                    {"action": "get_labour", "enabled": True},
                ],
                enabled=True,
            )
        )
        db_session.flush()

        # Scoped to reports' own bound actions, sorted — NOT every agent's tools.
        assert default_tool_filter("reports", db_session) == ["get_labour", "get_sales"]

    def test_default_tool_filter_is_none_for_an_unbound_agent(self, db_session):
        # No curated bindings → None, which leaves the union in place (matching
        # the old no-narrowing behaviour for an agent the binding layer isn't
        # curating).
        assert default_tool_filter("no_such_agent", db_session) is None

    def test_a_disabled_capability_is_not_in_the_fallback_scope(self, db_session):
        db_session.add(
            AgentConnectionBinding(
                agent_slug="marketing",
                connector_name="norm",
                capabilities=[
                    {"action": "get_marketing", "enabled": True},
                    {"action": "create_purchase_order", "enabled": False},
                ],
                enabled=True,
            )
        )
        db_session.flush()
        tf = default_tool_filter("marketing", db_session)
        assert tf == ["get_marketing"]
        assert "create_purchase_order" not in tf


class TestDeletedToolsAreNeverOffered:
    def test_delegate_to_agent_is_retired(self):
        # The handler is gone; the guard in _collect_tools drops it from every
        # agent's menu even if a config-DB spec still lists it.
        assert "delegate_to_agent" in _RETIRED_ACTIONS
