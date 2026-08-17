"""The durable sensei queue.

The point of the queue is that a run SURVIVES its executor: state lives in
the sample's analysis JSON, a heartbeat proves the executor is alive, and the
worker requeues what died — so a dev-server reload, a crashed job, or a lost
trigger becomes a visible restart, never a sample frozen at "analysing…" and
never a silent memory-heavy thread inside the web container (the Aug-12/13
OOM path, retired 16 Aug 2026).
"""

import datetime as _dt

import pytest

from app.config import settings
from app.db.config_models import SupplierInvoiceSpec, SupplierSpecSample
from app.services import sensei_runner


def _iso_ago(seconds: float) -> str:
    return (
        _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=seconds)
    ).isoformat()


@pytest.fixture()
def bound_config(monkeypatch, db_session):
    """Point the queue's own sessions at the test transaction."""
    import app.db.engine as engine_mod

    monkeypatch.setattr(engine_mod, "_ConfigSessionLocal", lambda: db_session)
    monkeypatch.setattr(engine_mod, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    return db_session


def _sample(db, analysis=None) -> SupplierSpecSample:
    spec = SupplierInvoiceSpec(name="Queue Foods", aliases=[], instructions="notes")
    db.add(spec)
    db.flush()
    s = SupplierSpecSample(
        spec_id=spec.id, label="q.pdf", pdf_bytes=b"%PDF-q", analysis=analysis
    )
    db.add(s)
    db.flush()
    return s


class TestEnqueue:
    def test_fresh_press_queues_with_env_and_reset_attempts(
        self, bound_config, db_session
    ):
        s = _sample(db_session, analysis={"status": "not_green", "attempts": 2})
        assert sensei_runner.enqueue(s.id, "fix line 4") == "queued"
        db_session.refresh(s)
        a = s.analysis
        assert a["status"] == "queued"
        assert a["queued_env"] == settings.ENVIRONMENT
        assert a["attempts"] == 0  # a human press resets the death counter
        assert a["feedback"] == "fix line 4"
        assert a["queued_at"]

    def test_live_running_is_not_disturbed(self, bound_config, db_session):
        a = {
            "status": "running",
            "heartbeat_at": _iso_ago(5),
            "claimed_by": "local:x:1",
        }
        s = _sample(db_session, analysis=a)
        assert sensei_runner.enqueue(s.id) == "running"
        db_session.refresh(s)
        assert s.analysis["status"] == "running"  # untouched

    def test_dead_running_is_requeued_by_a_press(self, bound_config, db_session):
        s = _sample(
            db_session,
            analysis={
                "status": "running",
                "heartbeat_at": _iso_ago(sensei_runner.HEARTBEAT_STALE_SECONDS + 60),
            },
        )
        assert sensei_runner.enqueue(s.id) == "queued"

    def test_never_raises(self, bound_config, db_session, monkeypatch):
        """The press must survive a queue failure — cannot-receive has already
        filed the sample; a broken queue write is a log line, not a 500."""

        def boom():
            raise RuntimeError("config DB down")

        import app.db.engine as engine_mod

        monkeypatch.setattr(engine_mod, "_ConfigSessionLocal", boom)
        assert sensei_runner.enqueue("s-x") == "error"


class TestClaim:
    def test_queued_is_claimed(self, bound_config, db_session):
        s = _sample(db_session, analysis={"status": "queued", "feedback": "hi"})
        entry = sensei_runner.claim(s.id, "local:me:1")
        assert entry is not None
        assert entry["status"] == "running"
        assert entry["claimed_by"] == "local:me:1"
        assert entry["feedback"] == "hi"  # rides along for the executor
        db_session.refresh(s)
        assert s.analysis["status"] == "running"

    def test_live_run_of_another_claimant_is_refused(self, bound_config, db_session):
        s = _sample(
            db_session,
            analysis={
                "status": "running",
                "heartbeat_at": _iso_ago(5),
                "claimed_by": "local:other:2",
            },
        )
        assert sensei_runner.claim(s.id, "local:me:1") is None

    def test_own_running_entry_is_reclaimable(self, bound_config, db_session):
        s = _sample(
            db_session,
            analysis={
                "status": "running",
                "heartbeat_at": _iso_ago(5),
                "claimed_by": "local:me:1",
            },
        )
        assert sensei_runner.claim(s.id, "local:me:1") is not None

    def test_dead_run_is_claimable(self, bound_config, db_session):
        s = _sample(
            db_session,
            analysis={
                "status": "running",
                "heartbeat_at": _iso_ago(sensei_runner.HEARTBEAT_STALE_SECONDS + 30),
                "claimed_by": "local:dead:9",
            },
        )
        entry = sensei_runner.claim(s.id, "local:me:1")
        assert entry is not None and entry["claimed_by"] == "local:me:1"

    def test_terminal_states_are_not_claimable(self, bound_config, db_session):
        s = _sample(db_session, analysis={"status": "ready"})
        assert sensei_runner.claim(s.id, "local:me:1") is None


class TestWorkerTick:
    def test_queued_run_is_executed_inline_without_a_job(
        self, bound_config, db_session, monkeypatch
    ):
        monkeypatch.setattr(settings, "SENSEI_JOB", "")
        s = _sample(
            db_session,
            analysis={
                "status": "queued",
                "queued_env": settings.ENVIRONMENT,
                "feedback": "check units",
            },
        )
        ran = []

        def fake_analyse(db, cdb, sid, feedback=None):
            ran.append((sid, feedback))
            sample = cdb.get(SupplierSpecSample, sid)
            sample.analysis = {"status": "ready"}
            cdb.commit()
            return sample.analysis

        monkeypatch.setattr("app.services.spec_dojo.analyse_sample", fake_analyse)
        summary = sensei_runner.worker_tick()
        assert summary["ran"] == [s.id]
        assert ran == [(s.id, "check units")]
        db_session.refresh(s)
        assert s.analysis["status"] == "ready"
        # A second tick has nothing to do.
        assert sensei_runner.worker_tick()["ran"] == []

    def test_other_environments_runs_are_never_touched(
        self, bound_config, db_session, monkeypatch
    ):
        monkeypatch.setattr(settings, "SENSEI_JOB", "")
        s = _sample(
            db_session,
            analysis={"status": "queued", "queued_env": "production"},
        )
        monkeypatch.setattr(
            "app.services.spec_dojo.analyse_sample",
            lambda *a, **k: pytest.fail("must not run another env's work"),
        )
        summary = sensei_runner.worker_tick()
        assert summary["ran"] == []
        db_session.refresh(s)
        assert s.analysis["status"] == "queued"

    def test_dead_run_is_requeued_with_attempts(
        self, bound_config, db_session, monkeypatch
    ):
        monkeypatch.setattr(settings, "SENSEI_JOB", "")
        s = _sample(
            db_session,
            analysis={
                "status": "running",
                "queued_env": settings.ENVIRONMENT,
                "heartbeat_at": _iso_ago(sensei_runner.HEARTBEAT_STALE_SECONDS + 30),
                "attempts": 0,
            },
        )
        seen = []

        def fake_analyse(db, cdb, sid, feedback=None):
            sample = cdb.get(SupplierSpecSample, sid)
            seen.append(int(sample.analysis.get("attempts") or 0))
            sample.analysis = {"status": "not_green"}
            cdb.commit()

        monkeypatch.setattr("app.services.spec_dojo.analyse_sample", fake_analyse)
        summary = sensei_runner.worker_tick()
        assert summary["requeued"] == [s.id]
        assert summary["ran"] == [s.id]  # requeued AND re-executed, same tick
        assert seen == [1]  # the restart carried attempts=1

    def test_a_run_that_keeps_dying_fails_with_an_explicit_error(
        self, bound_config, db_session, monkeypatch
    ):
        monkeypatch.setattr(settings, "SENSEI_JOB", "")
        s = _sample(
            db_session,
            analysis={
                "status": "running",
                "queued_env": settings.ENVIRONMENT,
                "heartbeat_at": _iso_ago(sensei_runner.HEARTBEAT_STALE_SECONDS + 30),
                "attempts": sensei_runner.MAX_ATTEMPTS,
            },
        )
        summary = sensei_runner.worker_tick()
        assert summary["failed"] == [s.id]
        db_session.refresh(s)
        assert s.analysis["status"] == "failed"
        assert "died" in s.analysis["error"]

    def test_fresh_running_is_left_alone(self, bound_config, db_session, monkeypatch):
        monkeypatch.setattr(settings, "SENSEI_JOB", "")
        s = _sample(
            db_session,
            analysis={
                "status": "running",
                "queued_env": settings.ENVIRONMENT,
                "heartbeat_at": _iso_ago(5),
            },
        )
        summary = sensei_runner.worker_tick()
        assert summary == {"requeued": [], "failed": [], "dispatched": [], "ran": []}
        db_session.refresh(s)
        assert s.analysis["status"] == "running"


class TestWorkerTickWithJob:
    """Deployed mode: the worker only dispatches; the job claims and runs."""

    def test_queued_run_is_dispatched_not_run(
        self, bound_config, db_session, monkeypatch
    ):
        monkeypatch.setattr(settings, "SENSEI_JOB", "norm-sensei-production")
        s = _sample(
            db_session,
            analysis={"status": "queued", "queued_env": settings.ENVIRONMENT},
        )
        dispatched = []
        monkeypatch.setattr(
            sensei_runner, "_run_job", lambda sid: dispatched.append(sid)
        )
        monkeypatch.setattr(
            sensei_runner,
            "_execute_inline",
            lambda *a: pytest.fail("job mode must never run inline"),
        )
        summary = sensei_runner.worker_tick()
        assert summary["dispatched"] == [s.id] and dispatched == [s.id]
        db_session.refresh(s)
        assert s.analysis["status"] == "queued"  # the JOB flips it to running
        assert s.analysis["dispatched_at"]

    def test_a_fresh_dispatch_is_not_repeated(
        self, bound_config, db_session, monkeypatch
    ):
        monkeypatch.setattr(settings, "SENSEI_JOB", "norm-sensei-production")
        _sample(
            db_session,
            analysis={
                "status": "queued",
                "queued_env": settings.ENVIRONMENT,
                "dispatched_at": _iso_ago(10),
            },
        )
        monkeypatch.setattr(
            sensei_runner,
            "_run_job",
            lambda sid: pytest.fail("dispatch already in flight"),
        )
        assert sensei_runner.worker_tick()["dispatched"] == []

    def test_a_failed_trigger_leaves_the_run_visibly_queued(
        self, bound_config, db_session, monkeypatch
    ):
        """The old code fell back to a silent thread in the web container —
        the Aug-12/13 OOM path. Now a lost trigger is just a still-queued run,
        retried next tick and visible in the UI the whole time."""
        monkeypatch.setattr(settings, "SENSEI_JOB", "norm-sensei-production")
        s = _sample(
            db_session,
            analysis={"status": "queued", "queued_env": settings.ENVIRONMENT},
        )

        def boom(sid):
            raise RuntimeError("jobs:run → 403")

        monkeypatch.setattr(sensei_runner, "_run_job", boom)
        monkeypatch.setattr(
            sensei_runner,
            "_execute_inline",
            lambda *a: pytest.fail("must not fall back to inline"),
        )
        summary = sensei_runner.worker_tick()
        assert summary["dispatched"] == []
        db_session.refresh(s)
        assert s.analysis["status"] == "queued"
        assert "dispatched_at" not in s.analysis

    def test_a_stalled_dispatch_is_retried(self, bound_config, db_session, monkeypatch):
        monkeypatch.setattr(settings, "SENSEI_JOB", "norm-sensei-production")
        s = _sample(
            db_session,
            analysis={
                "status": "queued",
                "queued_env": settings.ENVIRONMENT,
                "dispatched_at": _iso_ago(sensei_runner.DISPATCH_GRACE_SECONDS + 30),
            },
        )
        dispatched = []
        monkeypatch.setattr(
            sensei_runner, "_run_job", lambda sid: dispatched.append(sid)
        )
        assert sensei_runner.worker_tick()["dispatched"] == [s.id]


class TestAnalysisView:
    """analysis_view never lies — see spec_dojo.analysis_view."""

    def _view(self, analysis):
        from app.services.spec_dojo import analysis_view

        return analysis_view(analysis)

    def test_fresh_queued_passes_through(self):
        v = self._view({"status": "queued", "queued_at": _iso_ago(10)})
        assert v["status"] == "queued"

    def test_abandoned_queued_reports_the_real_problem(self):
        v = self._view(
            {
                "status": "queued",
                "queued_at": _iso_ago(sensei_runner.QUEUED_ABANDONED_SECONDS + 60),
                "queued_env": "production",
            }
        )
        assert v["status"] == "failed"
        assert "production" in v["error"]

    def test_running_with_live_heartbeat_is_running(self):
        v = self._view(
            {"status": "running", "heartbeat_at": _iso_ago(5), "phase": "asking"}
        )
        assert v["status"] == "running" and not v.get("stale")

    def test_running_with_dead_heartbeat_is_marked_stale(self):
        v = self._view(
            {
                "status": "running",
                "heartbeat_at": _iso_ago(sensei_runner.HEARTBEAT_STALE_SECONDS + 30),
            }
        )
        assert v["status"] == "running" and v["stale"] is True

    def test_legacy_running_without_heartbeat_uses_the_old_rule(self):
        from app.services.spec_dojo import STALE_ANALYSIS_MINUTES

        v = self._view(
            {"status": "running", "at": _iso_ago(STALE_ANALYSIS_MINUTES * 60 + 60)}
        )
        assert v["status"] == "failed"
