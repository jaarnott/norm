"""MCP audit trail — redaction and touched-record reporting.

The redaction test is the security-relevant one: arguments carry venue and
staff data, and only an allowlisted subset may ever be persisted.
"""

from app.mcp import audit


class TestRedaction:
    def test_free_text_is_dropped(self):
        """A `query` free-text arg must never be stored — only allowlisted keys."""
        red = audit._redact_args(
            "norm__resolve_dates",
            {"query": "sensitive text", "timezone": "Pacific/Auckland"},
        )
        assert red == {"timezone": "Pacific/Auckland"}
        assert "query" not in red

    def test_default_allowlist_covers_date_and_venue_selectors(self):
        red = audit._redact_args(
            "loadedhub__get_sales_data",
            {
                "start_datetime": "x",
                "end_datetime": "y",
                "interval": "1",
                "secret": "z",
            },
        )
        assert set(red) == {"start_datetime", "end_datetime", "interval"}

    def test_unknown_keys_dropped_by_default(self):
        assert audit._redact_args("whatever", {"employee_ssn": "123"}) == {}

    def test_allowlist_is_per_tool_plus_defaults(self):
        # timezone is tool-specific for resolve_dates; venue is a default.
        red = audit._redact_args(
            "norm__resolve_dates", {"timezone": "UTC", "venue": "X"}
        )
        assert red == {"timezone": "UTC", "venue": "X"}

    def test_empty_args(self):
        assert audit._redact_args("x", {}) == {}
        assert audit._redact_args("x", None) == {}


class TestTouchedRecords:
    def test_reset_then_record(self):
        audit.reset_touched()
        audit.record_touched("order_draft", "ord_1")
        audit.record_touched("order_draft", "ord_2")
        assert audit._touched_var.get() == [
            ("order_draft", "ord_1"),
            ("order_draft", "ord_2"),
        ]

    def test_reset_clears(self):
        audit.record_touched("x", "1")
        audit.reset_touched()
        assert audit._touched_var.get() == []
