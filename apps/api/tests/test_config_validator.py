"""Guard tests for database-held configuration.

Norm keeps connector specs, agent prompts and model selections in the database,
not the repo. That is deliberate — a new integration needs no deploy — but it
means CI cannot see them: CI points CONFIG_DATABASE_URL at a throwaway Postgres
with zero rows, and config is edited through the Settings UI long after deploy.

Every production incident so far lived in that blind spot, so each case below is
a real one that shipped with a green build:

  * a retired Claude model id stored in connector_configs (every agent call 404'd)
  * a consolidator left on the legacy `steps` format after its executor was
    deleted (the deleting commit had passing tests)

These tests cover the pure checks. The same functions run against the real
databases via POST /internal/validate-config — that is the half CI can't do.
"""

from app.services.config_validator import (
    check_binding_capabilities,
    check_connector_tools,
    check_model_selection,
    check_playbook_tool_filter,
)

CURRENT_MODELS = ["claude-sonnet-5", "claude-opus-4-8", "claude-haiku-4-5-20251001"]


class TestConsolidatorFormat:
    """The legacy `steps` executor was deleted — anything still on it is broken."""

    def test_legacy_steps_consolidator_is_an_error(self):
        # Exactly the shape of loadedhub.get_stock_on_hand_for_item in prod.
        tools = [
            {
                "action": "get_stock_on_hand_for_item",
                "path_template": "",
                "consolidator_config": {"steps": [{"action": "get_stock_item"}]},
            }
        ]
        issues = check_connector_tools("loadedhub", "template", tools)
        assert len(issues) == 1
        assert issues[0].severity == "error"
        assert issues[0].where == "loadedhub.get_stock_on_hand_for_item"
        assert "function_code" in issues[0].problem
        assert "legacy 'steps'" in issues[0].problem
        assert "Port the consolidator" in issues[0].fix

    def test_function_code_consolidator_is_fine(self):
        tools = [
            {
                "action": "get_staff_attendance",
                "path_template": "",
                "consolidator_config": {
                    "function_code": "def run(params, call_api, log): ..."
                },
            }
        ]
        assert check_connector_tools("loadedhub", "template", tools) == []

    def test_consolidator_without_path_template_is_not_flagged(self):
        """A consolidator composes other tools — it has no URL of its own."""
        tools = [
            {
                "action": "composite",
                "consolidator_config": {"function_code": "def run(): ..."},
            }
        ]
        assert check_connector_tools("x", "template", tools) == []


class TestPathTemplate:
    def test_template_tool_without_path_template_is_an_error(self):
        tools = [{"action": "get_thing", "path_template": ""}]
        issues = check_connector_tools("loadedhub", "template", tools)
        assert len(issues) == 1
        assert "path_template" in issues[0].problem

    def test_template_tool_with_path_template_is_fine(self):
        tools = [{"action": "get_thing", "path_template": "//api.example.com/things"}]
        assert check_connector_tools("loadedhub", "template", tools) == []

    def test_agent_mode_tool_needs_no_path_template(self):
        """In agent mode the LLM generates the request from API docs."""
        tools = [{"action": "get_thing", "path_template": ""}]
        assert check_connector_tools("x", "agent", tools) == []

    def test_tool_without_action_is_an_error(self):
        issues = check_connector_tools("x", "template", [{"path_template": "/a"}])
        assert len(issues) == 1
        assert "action" in issues[0].problem

    def test_malformed_tool_entry_is_an_error(self):
        issues = check_connector_tools("x", "template", ["not-an-object"])
        assert len(issues) == 1
        assert "expected an object" in issues[0].problem

    def test_empty_and_missing_tools_are_fine(self):
        assert check_connector_tools("x", "template", []) == []
        assert check_connector_tools("x", "template", None) == []


class TestModelSelection:
    """A stored model id overrides the code default — a current default is no defence."""

    def test_retired_model_is_an_error(self):
        # The exact id that took production down.
        issues = check_model_selection(
            "anthropic",
            {"interpreter_model": "claude-sonnet-4-20250514"},
            CURRENT_MODELS,
        )
        assert len(issues) == 1
        assert issues[0].severity == "error"
        assert "claude-sonnet-4-20250514" in issues[0].problem
        assert "Settings" in issues[0].fix

    def test_current_model_is_fine(self):
        assert (
            check_model_selection(
                "anthropic", {"interpreter_model": "claude-opus-4-8"}, CURRENT_MODELS
            )
            == []
        )

    def test_retired_router_model_is_also_caught(self):
        issues = check_model_selection(
            "anthropic", {"router_model": "claude-3-5-sonnet-20241022"}, CURRENT_MODELS
        )
        assert len(issues) == 1
        assert issues[0].where == "anthropic.router_model"

    def test_both_models_checked_independently(self):
        issues = check_model_selection(
            "anthropic",
            {"interpreter_model": "claude-opus-4-8", "router_model": "retired-model"},
            CURRENT_MODELS,
        )
        assert len(issues) == 1
        assert issues[0].where == "anthropic.router_model"

    def test_unset_model_is_fine(self):
        """No stored selection just means the code default applies."""
        assert check_model_selection("anthropic", {}, CURRENT_MODELS) == []
        assert check_model_selection("anthropic", None, CURRENT_MODELS) == []

    def test_other_connectors_config_is_ignored(self):
        assert check_model_selection("bamboohr", {"api_key": "x"}, CURRENT_MODELS) == []


class TestConsolidatorSafety:
    """Checks added with the invoice-receiving workflow: function_code must
    compile, and declared write actions must exist on the connector."""

    def test_syntax_error_in_function_code_is_flagged(self):
        tools = [
            {
                "action": "broken",
                "consolidator_config": {"function_code": "def run(:\n"},
            }
        ]
        issues = check_connector_tools("loadedhub", "template", tools)
        assert len(issues) == 1
        assert "syntax error" in issues[0].problem

    def test_unknown_allowed_write_action_is_flagged(self):
        tools = [
            {
                "action": "review",
                "consolidator_config": {
                    "function_code": "def run(params, call_api, log):\n    return {}\n",
                    "allowed_write_actions": ["recieve_invoice"],  # typo
                },
            }
        ]
        issues = check_connector_tools("loadedhub", "template", tools)
        assert len(issues) == 1
        assert "recieve_invoice" in issues[0].problem

    def test_declared_write_action_that_exists_passes(self):
        tools = [
            {
                "action": "receive_invoice",
                "method": "PUT",
                "path_template": "//api.example.com/i/{{ id }}",
            },
            {
                "action": "review",
                "consolidator_config": {
                    "function_code": "def run(params, call_api, log):\n    return {}\n",
                    "allowed_write_actions": ["receive_invoice"],
                },
            },
        ]
        assert check_connector_tools("loadedhub", "template", tools) == []


class TestResponseFormat:
    def test_binary_is_allowed(self):
        tools = [
            {
                "action": "download_invoice_file",
                "method": "GET",
                "path_template": "//api.example.com/f/{{ id }}",
                "response_format": "binary",
            }
        ]
        assert check_connector_tools("loadedhub", "template", tools) == []

    def test_unknown_format_is_flagged(self):
        tools = [
            {
                "action": "download",
                "method": "GET",
                "path_template": "//api.example.com/f/{{ id }}",
                "response_format": "csv",
            }
        ]
        issues = check_connector_tools("loadedhub", "template", tools)
        assert len(issues) == 1
        assert "unknown response_format 'csv'" in issues[0].problem


class TestPlaybookToolFilter:
    KNOWN = {"review_and_receive_invoices", "get_invoice_detail"}

    def test_known_actions_pass(self):
        issues = check_playbook_tool_filter(
            "receive_loadedhub_invoices",
            ["review_and_receive_invoices", "loadedhub__get_invoice_detail"],
            self.KNOWN,
        )
        assert issues == []

    def test_unknown_action_is_flagged(self):
        issues = check_playbook_tool_filter(
            "receive_loadedhub_invoices", ["reconcile_invoices"], self.KNOWN
        )
        assert len(issues) == 1
        assert "reconcile_invoices" in issues[0].problem
        assert issues[0].where == "playbook.receive_loadedhub_invoices"

    def test_empty_filter_is_fine(self):
        assert check_playbook_tool_filter("p", None, self.KNOWN) == []
        assert check_playbook_tool_filter("p", [], self.KNOWN) == []


class TestBindingCapabilities:
    """A bare-string capability entry 500s the Agents tab and breaks tool
    building for every chat with that agent — real incident, 17 Jul 2026."""

    def test_dict_entries_pass(self):
        caps = [{"action": "get_roster", "label": "Get roster", "enabled": True}]
        assert check_binding_capabilities("procurement", "loadedhub", caps) == []

    def test_string_entry_is_an_error(self):
        issues = check_binding_capabilities(
            "procurement", "loadedhub", ["review_and_receive_invoices"]
        )
        assert len(issues) == 1
        assert issues[0].where == "binding.procurement.loadedhub"
        assert "review_and_receive_invoices" in issues[0].problem

    def test_dict_without_action_is_an_error(self):
        issues = check_binding_capabilities("hr", "bamboohr", [{"enabled": True}])
        assert len(issues) == 1

    def test_empty_is_fine(self):
        assert check_binding_capabilities("hr", "bamboohr", None) == []
        assert check_binding_capabilities("hr", "bamboohr", []) == []


class TestAvailableModelsStayCurrent:
    """The allow-list itself must not drift onto retired ids."""

    RETIRED = {
        "claude-sonnet-4-20250514",
        "claude-opus-4-20250514",
        "claude-3-opus-20240229",
        "claude-3-5-sonnet-20241022",
        "claude-3-5-sonnet-20240620",
        "claude-3-7-sonnet-20250219",
        "claude-3-5-haiku-20241022",
    }

    def test_available_models_contains_no_retired_ids(self):
        from app.routers.connectors import AVAILABLE_MODELS

        offered = {m["id"] for m in AVAILABLE_MODELS}
        assert not (offered & self.RETIRED), (
            f"Settings offers retired model(s): {offered & self.RETIRED}. "
            "Selecting one 404s every agent call."
        )

    def test_settings_defaults_are_offered_models(self):
        """The code default must be a model the UI would let you pick."""
        from app.config import settings
        from app.routers.connectors import AVAILABLE_MODELS

        offered = {m["id"] for m in AVAILABLE_MODELS}
        assert settings.LLM_INTERPRETER_MODEL in offered
        assert settings.ROUTER_MODEL in offered
