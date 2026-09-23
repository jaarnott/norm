"""The connector-call DB budget: a fan-out must not hold the pool.

A single large multi-venue query used to open one DB session per parallel call
and hold each across the slow Loaded HTTP round-trip, filling norm-prod-db's
~50 slots and starving every other request until a restart. The fix: a fan-out
worker's fresh session returns its connection to the pool right after the brief
render/token phase, before the HTTP call (which needs no DB), under a
per-instance budget semaphore. These pin that behaviour.
"""

from unittest.mock import MagicMock

from app.connectors import spec_executor
from app.connectors.spec_executor import ConnectorResult, RenderedRequest, execute_spec


def _spec():
    s = MagicMock()
    s.execution_mode = "template"
    s.auth_type = "basic"
    s.auth_config = {}
    return s


def _wire(monkeypatch, order):
    rr = RenderedRequest(method="GET", url="https://x", headers={}, body=None)

    def fake_render(*a, **k):
        order.append("render")
        return rr

    def fake_http(rendered, operation, **k):
        order.append("http")
        return ConnectorResult(
            success=True, reference=None, response_payload={"ok": True}
        )

    monkeypatch.setattr(spec_executor, "render_request", fake_render)
    monkeypatch.setattr(spec_executor, "execute_http", fake_http)
    monkeypatch.setattr(spec_executor, "_should_retry_after_refresh", lambda *a: False)


class TestConnectionReleasedForDisposableSession:
    def test_connection_is_released_before_the_http_call(self, monkeypatch):
        order = []
        _wire(monkeypatch, order)
        db = MagicMock()
        db.commit.side_effect = lambda: order.append("commit")

        execute_spec(
            _spec(),
            {"required_fields": [], "action": "get_x"},
            {},
            {},
            db,
            release_db_after_render=True,
        )

        # The connection returns to the pool (commit) AFTER render but BEFORE the
        # slow HTTP round-trip — so a wide fan-out never holds the pool across it.
        assert order == ["render", "commit", "http"]

    def test_a_shared_session_is_never_committed_mid_call(self, monkeypatch):
        order = []
        _wire(monkeypatch, order)
        db = MagicMock()

        execute_spec(
            _spec(),
            {"required_fields": [], "action": "get_x"},
            {},
            {},
            db,
            release_db_after_render=False,
        )

        # Default (sequential / shared request session): no mid-call commit.
        db.commit.assert_not_called()
        assert order == ["render", "http"]


class TestBudgetSemaphore:
    def test_a_per_instance_budget_exists_and_is_bounded(self):
        from app.db.engine import DB_CALL_LIMIT, db_call_semaphore

        assert DB_CALL_LIMIT < 22  # below the per-process pool ceiling (2 + 20)
        # It is a real bounded semaphore, acquirable and releasable.
        assert db_call_semaphore.acquire(timeout=1)
        db_call_semaphore.release()


class TestIdleFootprint:
    """The pool is per PROCESS and gunicorn runs four per instance.

    pool_size is what each process keeps open forever once a burst has grown
    its pool. At 10 it ratcheted norm-prod-db to 48 idle connections on
    23 Sep 2026 (4 workers x 2 instances x up to 10) with traffic flat.
    """

    def test_each_process_keeps_only_a_small_idle_pool(self):
        from app.db.engine import engine

        workers, instances = 4, 2  # Dockerfile `-w 4`; Cloud Run maxScale
        idle_ceiling = engine.pool.size() * workers * instances
        assert idle_ceiling <= 20, idle_ceiling  # of norm-prod-db's ~50 slots

    def test_a_process_can_still_burst_to_a_full_fan_out(self):
        from app.db.engine import engine

        # A 20-way consolidator fan-out plus a little room, inside one worker.
        assert engine.pool.size() + engine.pool._max_overflow >= 22
