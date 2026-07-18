"""MCP rate limiting — Postgres fixed-window counters.

The atomic upsert must be correct at the window boundary and reset across
windows. These use the real DB (the counters are SQL).
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.mcp.ratelimit import check_rate_limit, gc_expired


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
