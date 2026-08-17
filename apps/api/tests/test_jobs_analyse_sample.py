"""The out-of-process sensei entrypoint.

Its whole reason to exist is that a failure must be VISIBLE. The in-process
version died with its container twice in two days and left the sample reading
"sensei analysing…" — so the two things worth pinning here are that a failed
analysis exits non-zero, and that the job loads the real API key rather than
the stale Secret Manager copy.
"""

import pytest

from app.jobs import analyse_sample as job


class TestExitCode:
    def _claimable(self, monkeypatch, entry=None):
        # The job claims from the queue before spending anything; give it a
        # claimed entry so the run proceeds.
        monkeypatch.setattr(
            "app.services.sensei_runner.claim",
            lambda sid, claimed_by: {"status": "running", **(entry or {})},
        )

    def _run(self, monkeypatch, stored):
        monkeypatch.setattr(job, "load_system_secrets", lambda: None)
        self._claimable(monkeypatch)
        monkeypatch.setattr(
            "app.services.spec_dojo.analyse_sample", lambda *a, **k: stored
        )
        return job.main(["sample-1"])

    def test_nothing_claimable_is_a_clean_exit(self, monkeypatch):
        """A duplicate dispatch (or a run another execution already finished)
        must exit 0 without spending a token."""
        monkeypatch.setattr(job, "load_system_secrets", lambda: None)
        monkeypatch.setattr(
            "app.services.sensei_runner.claim", lambda sid, claimed_by: None
        )
        monkeypatch.setattr(
            "app.services.spec_dojo.analyse_sample",
            lambda *a, **k: pytest.fail("must not run without a claim"),
        )
        assert job.main(["sample-1"]) == 0

    def test_feedback_comes_from_the_claimed_entry(self, monkeypatch):
        """The queue carries the admin's correction; the worker dispatches the
        job with no args beyond the sample id."""
        seen = {}
        monkeypatch.setattr(job, "load_system_secrets", lambda: None)
        self._claimable(monkeypatch, {"feedback": "the unit is a 12 pack"})

        def capture(db, cdb, sid, feedback=None):
            seen["feedback"] = feedback
            return {"status": "ready"}

        monkeypatch.setattr("app.services.spec_dojo.analyse_sample", capture)
        assert job.main(["sample-1"]) == 0
        assert seen["feedback"] == "the unit is a 12 pack"

    def test_a_green_analysis_exits_zero(self, monkeypatch):
        assert self._run(monkeypatch, {"status": "ready"}) == 0

    def test_a_not_green_analysis_is_still_a_successful_run(self, monkeypatch):
        # "not_green" is a real proposal the admin can act on — the run did
        # its job even though the candidate did not pass.
        assert self._run(monkeypatch, {"status": "not_green"}) == 0

    def test_a_failed_analysis_exits_non_zero(self, monkeypatch):
        """analyse_sample swallows its own exceptions and stores
        status='failed'. Returning 0 there would report a healthy execution
        for an analysis that died — the exact invisible failure this job
        exists to end."""
        assert self._run(monkeypatch, {"status": "failed", "error": "boom"}) == 1

    def test_an_exception_exits_non_zero(self, monkeypatch):
        monkeypatch.setattr(job, "load_system_secrets", lambda: None)
        self._claimable(monkeypatch)

        def boom(*a, **k):
            raise RuntimeError("model unreachable")

        monkeypatch.setattr("app.services.spec_dojo.analyse_sample", boom)
        assert job.main(["sample-1"]) == 1

    def test_feedback_is_passed_through(self, monkeypatch):
        # An explicit --feedback flag (manual job run) overrides the entry's.
        seen = {}
        monkeypatch.setattr(job, "load_system_secrets", lambda: None)
        self._claimable(monkeypatch)

        def capture(db, cdb, sid, feedback=None):
            seen["sid"], seen["feedback"] = sid, feedback
            return {"status": "ready"}

        monkeypatch.setattr("app.services.spec_dojo.analyse_sample", capture)
        job.main(["s-9", "--feedback", "the unit is a 12 pack"])
        assert seen == {"sid": "s-9", "feedback": "the unit is a 12 pack"}

    def test_llm_call_records_survive_the_job(self, monkeypatch):
        """The job must COMMIT the main session before closing it.

        call_llm only add()+flush()es its llm_calls rows; inside the web
        process the request lifecycle commits them, but the job owns its own
        session and close() rolls back anything uncommitted. That was the
        Aug-2026 blind spot: every job-run sensei analysis burned Opus tokens
        with zero rows in llm_calls (nothing recorded after the Aug-13 move
        to the Cloud Run job).
        """
        from app.db.engine import SessionLocal
        from app.db.models import LlmCall

        marker = "job-commit-pin"
        monkeypatch.setattr(job, "load_system_secrets", lambda: None)
        self._claimable(monkeypatch)

        def record_like_call_llm(db, cdb, sid, feedback=None):
            db.add(
                LlmCall(
                    call_type="dojo_analysis",
                    model="test-model",
                    system_prompt=marker,
                    user_prompt="",
                    input_tokens=10,
                    output_tokens=2,
                )
            )
            db.flush()
            return {"status": "ready"}

        monkeypatch.setattr(
            "app.services.spec_dojo.analyse_sample", record_like_call_llm
        )
        assert job.main(["sample-1"]) == 0

        fresh = SessionLocal()
        try:
            row = fresh.query(LlmCall).filter(LlmCall.system_prompt == marker).first()
            assert row is not None, "llm_calls row was rolled back on close"
            fresh.delete(row)
            fresh.commit()
        finally:
            fresh.close()


class TestSecretLoading:
    """These MUST bind the session factory to the test transaction.

    ``load_system_secrets`` resolves ``_ConfigSessionLocal`` itself, and the
    config DB is shared with production — an unbound test reads the real
    secrets table and asserts against a live API key (which is also then
    printed into pytest's failure output). Bind it, always.
    """

    def _bind(self, monkeypatch, db_session):
        class _Shim:
            def __init__(self, s):
                self._s = s

            def __getattr__(self, k):
                return getattr(self._s, k)

            def close(self):
                pass

        monkeypatch.setattr(
            "app.db.engine._ConfigSessionLocal", lambda: _Shim(db_session)
        )

    def test_the_config_db_key_overrides_the_injected_one(
        self, db_session, monkeypatch
    ):
        """The live ANTHROPIC_API_KEY lives in the config DB and is edited in
        the Settings UI; the Secret Manager copy the container starts with can
        be stale. The web process fixes this at startup — a job has no startup,
        so it must do it explicitly."""
        from app.config import settings
        from app.db.models import SystemSecret

        self._bind(monkeypatch, db_session)
        db_session.add(SystemSecret(key="ANTHROPIC_API_KEY", value="sk-from-config-db"))
        db_session.commit()

        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-stale-from-env")
        job.load_system_secrets()
        assert settings.ANTHROPIC_API_KEY == "sk-from-config-db"

    def test_an_unlisted_secret_is_never_applied(self, db_session, monkeypatch):
        from app.config import settings
        from app.db.models import SystemSecret

        self._bind(monkeypatch, db_session)
        db_session.add(SystemSecret(key="NOT_A_REAL_SETTING", value="x"))
        db_session.commit()
        job.load_system_secrets()
        assert not hasattr(settings, "NOT_A_REAL_SETTING")
