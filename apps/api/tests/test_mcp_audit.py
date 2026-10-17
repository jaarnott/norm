"""MCP request guards — the audit trail and rate limiting.

Audit: redaction and touched-record reporting. The redaction test is the
security-relevant one: arguments carry venue and staff data, and only an
allowlisted subset may ever be persisted.

Rate limiting: Postgres fixed-window counters. The atomic upsert must be
correct at the window boundary and reset across windows. These use the real
DB (the counters are SQL).
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.mcp import audit
from app.mcp.ratelimit import check_rate_limit, gc_expired


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
        """Only the default selectors survive; any other key is dropped — for
        a known tool and an unknown one alike."""
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


# ── Rate limiting ────────────────────────────────────────────────────────


def _key():
    return f"test:{uuid.uuid4().hex}:call"


class TestFixedWindow:
    def test_allows_up_to_the_limit_then_429s(self, db_session):
        key = _key()
        for _ in range(3):
            check_rate_limit(db_session, key, 3, 60)  # 3 allowed
        with pytest.raises(HTTPException) as exc:
            check_rate_limit(db_session, key, 3, 60)  # 4th
        assert exc.value.status_code == 429
        assert "Retry-After" in exc.value.headers

    def test_separate_keys_are_independent(self, db_session):
        a, b = _key(), _key()
        check_rate_limit(db_session, a, 1, 60)
        # b has its own budget
        check_rate_limit(db_session, b, 1, 60)
        with pytest.raises(HTTPException):
            check_rate_limit(db_session, a, 1, 60)

    def test_next_window_resets(self, db_session):
        key = _key()
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        check_rate_limit(db_session, key, 1, 60, now=t0)
        with pytest.raises(HTTPException):
            check_rate_limit(db_session, key, 1, 60, now=t0)
        # A minute later, a fresh window.
        check_rate_limit(db_session, key, 1, 60, now=t0 + timedelta(seconds=61))


class TestGc:
    def test_gc_removes_old_windows(self, db_session):
        key = _key()
        old = datetime(2020, 1, 1, tzinfo=timezone.utc)
        check_rate_limit(db_session, key, 5, 60, now=old)
        removed = gc_expired(db_session, older_than_seconds=0)
        assert removed >= 1
