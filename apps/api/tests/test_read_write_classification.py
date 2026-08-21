"""Read vs write classification for the chat tool loop.

An MCP connector's transport is always POST, so a genuine read
(`functions_get_contacts`, `training_list_job_openings`) used to be mislabelled
a write and demand an approval nobody should grant to READ. These cover the fix
that relabels such actions GET so the loop auto-runs them, while keeping every
real write — and every template-mode POST — behind the approval gate.
"""

from types import SimpleNamespace

from app.agents.prompt_builder import _effective_method, _name_is_read


class TestNameIsRead:
    def test_domain_prefixed_reads_are_reads(self):
        for action in (
            "functions_get_contacts",
            "training_list_job_openings",
            "stock_get_stock_on_hand",
            "marketing_get_content_plans",
            "kitchen_list_temperature_logs",
            "loadedhub_search_stock_items",
            "training_get_capability_framework",
        ):
            assert _name_is_read(action) is True, action

    def test_writes_are_not_reads(self):
        for action in (
            "functions_update_venue_function",
            "stock_create_stocktake",
            "training_move_candidate_stage",
            "functions_send_inbox_reply",
            "marketing_approve_social_post",
            "place_stock_order",
            "training_sign_off_module",
            "kitchen_log_temperature_reading",
        ):
            assert _name_is_read(action) is False, action

    def test_a_write_verb_vetoes_a_read_verb(self):
        # Fail-closed: a name carrying any write verb is a write even if it also
        # carries a read verb.
        assert _name_is_read("get_and_delete_thing") is False
        assert _name_is_read("list_then_create") is False


class TestEffectiveMethod:
    def _spec(self, mode):
        return SimpleNamespace(execution_mode=mode)

    def test_mcp_read_only_flag_true_is_get(self):
        m = _effective_method(
            self._spec("mcp"),
            {"action": "x_do_thing", "method": "POST", "read_only": True},
        )
        assert m == "GET"

    def test_mcp_read_only_flag_false_stays_declared(self):
        m = _effective_method(
            self._spec("mcp"),
            {"action": "x_get_thing", "method": "POST", "read_only": False},
        )
        assert m == "POST"  # explicit flag wins over the read-looking name

    def test_mcp_no_flag_read_name_is_get(self):
        m = _effective_method(
            self._spec("mcp"), {"action": "functions_get_contacts", "method": "POST"}
        )
        assert m == "GET"

    def test_mcp_no_flag_write_name_stays_post(self):
        m = _effective_method(
            self._spec("mcp"),
            {"action": "functions_update_venue_function", "method": "POST"},
        )
        assert m == "POST"

    def test_template_mode_never_overridden_by_name(self):
        # A template-mode POST is a real write; a read-looking name must not
        # turn it into an unapproved read.
        m = _effective_method(
            self._spec("template"), {"action": "get_report", "method": "POST"}
        )
        assert m == "POST"

    def test_template_mode_ignores_read_only_flag(self):
        # The flag only settles mcp-mode actions; on a template connector a
        # mis-set flag must not silently drop the write gate.
        m = _effective_method(
            self._spec("template"),
            {"action": "do_write", "method": "POST", "read_only": True},
        )
        assert m == "POST"

    def test_declared_get_is_preserved(self):
        m = _effective_method(
            self._spec("mcp"), {"action": "get_thing", "method": "GET"}
        )
        assert m == "GET"
