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
    check_binding_actions,
    check_binding_capabilities,
    check_component_api_row,
    check_connector_tools,
    check_display_components,
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
                "action": "get_labour",
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

    def test_engine_only_entry_is_flagged_not_silently_dropped(self):
        # It exists on the spec, so the exists-somewhere check passes — but
        # agents can never see it, so the filter entry vanishes. This is how
        # the sales playbooks lost their data tools when the raws were
        # demoted (prod thread b9bda2c1, 23 Aug 2026).
        issues = check_playbook_tool_filter(
            "sales_comparison",
            ["get_sales_data"],
            {"get_sales_data", "get_sales"},
            engine_only_actions={"get_sales_data"},
        )
        assert len(issues) == 1
        assert "engine-only" in issues[0].problem
        assert "silently dropped" in issues[0].problem

    def test_visible_action_passes_with_engine_only_set_present(self):
        issues = check_playbook_tool_filter(
            "sales_comparison",
            ["get_sales"],
            {"get_sales_data", "get_sales"},
            engine_only_actions={"get_sales_data"},
        )
        assert issues == []


class TestBindingActions:
    """A binding capability that resolves to nothing vanishes SILENTLY.

    ``_collect_tools`` intersects spec tools with the capability set, so a
    stale action name just contributes nothing. The executive chef lost its
    recipe write this way on 29 Aug 2026 — the CB App consolidated its tools
    and ``kitchen_loadedhub_update_recipe`` stopped existing, but the binding
    kept naming it in every environment until a human noticed Save was broken.
    """

    SPEC = {"kitchen_record_recipe", "stock_loadedhub_tender"}

    def test_capability_matching_a_spec_tool_passes(self):
        issues = check_binding_actions(
            "executive_chef",
            "cook_brothers_app",
            [{"action": "kitchen_record_recipe", "enabled": True}],
            self.SPEC,
        )
        assert issues == []

    def test_stale_capability_is_flagged(self):
        # The exact incident: the cap outlived the tool it named.
        issues = check_binding_actions(
            "executive_chef",
            "cook_brothers_app",
            [{"action": "kitchen_loadedhub_update_recipe", "enabled": True}],
            self.SPEC,
        )
        assert len(issues) == 1
        assert "kitchen_loadedhub_update_recipe" in issues[0].problem
        assert "silently" in issues[0].problem
        assert issues[0].where == "binding.executive_chef.cook_brothers_app"

    def test_disabled_capability_is_ignored(self):
        issues = check_binding_actions(
            "executive_chef",
            "cook_brothers_app",
            [{"action": "gone_tool", "enabled": False}],
            self.SPEC,
        )
        assert issues == []

    def test_missing_enabled_key_means_enabled(self):
        # _collect_tools reads cap.get("enabled", True) — mirror it.
        issues = check_binding_actions(
            "executive_chef", "cook_brothers_app", [{"action": "gone_tool"}], self.SPEC
        )
        assert len(issues) == 1

    def test_binding_to_a_connector_with_no_spec_is_flagged(self):
        # Found live on day one: four agents bound to 'microsoft_outlook',
        # a connector no spec defines.
        issues = check_binding_actions("procurement", "microsoft_outlook", [], None)
        assert len(issues) == 1
        assert "no connection spec" in issues[0].problem

    def test_engine_only_capability_is_flagged(self):
        issues = check_binding_actions(
            "reports",
            "loadedhub",
            [{"action": "get_sales_data", "enabled": True}],
            {"get_sales_data", "get_sales"},
            engine_only_actions={"get_sales_data"},
        )
        assert len(issues) == 1
        assert "engine-only" in issues[0].problem

    def test_malformed_entries_are_left_to_the_shape_check(self):
        issues = check_binding_actions(
            "reports", "loadedhub", ["bare-string", {"no": "action"}], self.SPEC
        )
        assert issues == []


class TestDisplayComponents:
    """display_component is free text; a name the web registry lacks renders
    the user a blank display block, silently."""

    KNOWN = {"generic_table", "recipe_editor"}

    def test_known_component_passes(self):
        issues = check_display_components(
            "loadedhub",
            [{"action": "edit_recipe", "display_component": "recipe_editor"}],
            self.KNOWN,
        )
        assert issues == []

    def test_unknown_component_is_flagged(self):
        issues = check_display_components(
            "loadedhub",
            [{"action": "edit_recipe", "display_component": "recipe_edit0r"}],
            self.KNOWN,
        )
        assert len(issues) == 1
        assert "recipe_edit0r" in issues[0].problem
        assert issues[0].where == "loadedhub.edit_recipe"

    def test_tools_without_the_field_pass(self):
        issues = check_display_components(
            "loadedhub", [{"action": "get_sales"}, "malformed"], self.KNOWN
        )
        assert issues == []


class TestComponentApiRows:
    """component_api rows are the HTTP door a page calls at load time. A row
    is self-contained (its own path_template — action_name is the row's name,
    never a spec tool), so only two things can dangle: the connector, and the
    component itself."""

    CONNECTORS = {"loadedhub", "bidfood"}
    KNOWN = {"recipe_editor"}

    def test_valid_row_passes(self):
        issues = check_component_api_row(
            "recipe_editor", "loadedhub", "get_recipes", self.KNOWN, self.CONNECTORS
        )
        assert issues == []

    def test_a_row_action_is_not_a_spec_tool(self):
        # roster_editor.load, recipe_editor.list_units etc. exist ONLY as
        # component_api rows — that must never be flagged.
        issues = check_component_api_row(
            "recipe_editor", "loadedhub", "list_units", self.KNOWN, self.CONNECTORS
        )
        assert issues == []

    def test_unknown_connector_is_flagged(self):
        issues = check_component_api_row(
            "recipe_editor", "loadedhub_v2", "get_recipes", self.KNOWN, self.CONNECTORS
        )
        assert len(issues) == 1
        assert "no connection spec" in issues[0].problem

    def test_undeclared_component_is_flagged(self):
        # The two untracked-row components the marketplace inventory found.
        issues = check_component_api_row(
            "ghost_component", "loadedhub", "get_recipes", self.KNOWN, self.CONNECTORS
        )
        assert len(issues) == 1
        assert "no app composition declares it" in issues[0].problem


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


class TestStaleAggregates:
    """A transform that drops rows must not keep a summary of the old set.

    From a real incident: `get_roster` asked LoadedHub for every venue's shifts,
    filtered them down to one venue, and passed the all-venue `totalHours`
    through. The payload then said 66 shifts / 332.25 hours when those 66 shifts
    were worth 146.5. Nothing errored — one agent quoted the header, another
    summed the rows, and they disagreed by 2.3x.
    """

    def _tool(self, fields, filters, enabled=True):
        return {
            "action": "get_roster",
            # A path_template so the unrelated template-mode rule stays quiet.
            "path_template": "//loadedhub.com/api/time/rosters",
            "response_transform": {
                "enabled": enabled,
                "fields": fields,
                "filters": filters,
            },
        }

    def test_the_real_bug_is_caught(self):
        issues = check_connector_tools(
            "loadedhub",
            "template",
            [
                self._tool(
                    {"id": "id", "totalHours": "totalHours"},
                    [
                        {
                            "field": "rosteredShifts[].isFromOtherVenue",
                            "operator": "equals",
                            "value": "false",
                        }
                    ],
                )
            ],
        )
        assert len(issues) == 1
        assert "totalHours" in issues[0].problem
        assert "rosteredShifts" in issues[0].problem

    def test_dropping_the_summary_is_the_accepted_fix(self):
        """Mapping it to "" removes it from the payload — nothing to be stale."""
        assert (
            check_connector_tools(
                "loadedhub",
                "template",
                [
                    self._tool(
                        {"id": "id", "totalHours": ""},
                        [{"field": "rosteredShifts[].deleted", "operator": "is_empty"}],
                    )
                ],
            )
            == []
        )

    def test_no_row_filter_means_no_problem(self):
        """Passing a summary through is fine when every row survives."""
        assert (
            check_connector_tools(
                "loadedhub",
                "template",
                [self._tool({"totalHours": "totalHours"}, [])],
            )
            == []
        )

    def test_top_level_filters_do_not_trigger_it(self):
        """Filtering whole rosters doesn't desync a roster's own total."""
        assert (
            check_connector_tools(
                "loadedhub",
                "template",
                [
                    self._tool(
                        {"totalHours": "totalHours"},
                        [{"field": "datestampDeleted", "operator": "is_empty"}],
                    )
                ],
            )
            == []
        )

    def test_disabled_transform_is_ignored(self):
        assert (
            check_connector_tools(
                "loadedhub",
                "template",
                [
                    self._tool(
                        {"totalHours": "totalHours"},
                        [{"field": "rosteredShifts[].x", "operator": "equals"}],
                        enabled=False,
                    )
                ],
            )
            == []
        )

    def test_non_summary_fields_pass_through_freely(self):
        """Only summary-shaped names are suspect; ids and names are per-object."""
        assert (
            check_connector_tools(
                "loadedhub",
                "template",
                [
                    self._tool(
                        {"id": "id", "name": "name", "startDateTime": "startDateTime"},
                        [{"field": "rosteredShifts[].x", "operator": "equals"}],
                    )
                ],
            )
            == []
        )
