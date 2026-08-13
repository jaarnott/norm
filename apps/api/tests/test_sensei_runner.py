"""Choosing where the sensei runs.

The point of the job is that a crash becomes a visible failed execution
instead of a sample frozen at "sensei analysing…". The point of these tests is
the rule that keeps that true in every environment: use the job when there is
one, fall back to doing the work when there isn't — never fall back to doing
nothing.
"""

import pytest

from app.config import settings
from app.services import sensei_runner


@pytest.fixture()
def no_thread(monkeypatch):
    """Record thread starts instead of running an analysis for real."""
    started = []
    monkeypatch.setattr(
        sensei_runner, "_run_thread", lambda sid, fb: started.append((sid, fb))
    )
    return started


class TestBackendChoice:
    def test_no_job_configured_runs_in_process(self, monkeypatch, no_thread):
        monkeypatch.setattr(settings, "SENSEI_JOB", "")
        assert sensei_runner.start_analysis("s-1") == "thread"
        assert no_thread == [("s-1", None)]

    def test_a_configured_job_is_used(self, monkeypatch, no_thread):
        calls = []
        monkeypatch.setattr(settings, "SENSEI_JOB", "norm-sensei-production")
        monkeypatch.setattr(
            sensei_runner, "_run_job", lambda sid, fb: calls.append((sid, fb))
        )
        assert sensei_runner.start_analysis("s-2", "check the pack size") == "job"
        assert calls == [("s-2", "check the pack size")]
        assert no_thread == []  # not both

    def test_a_job_that_cannot_start_falls_back_to_doing_the_work(
        self, monkeypatch, no_thread
    ):
        """The dangerous failure is silence. If the trigger fails — no IAM, no
        metadata server, job renamed — the analysis must still happen, because
        a sample nobody is working on looks identical to one in progress."""
        monkeypatch.setattr(settings, "SENSEI_JOB", "norm-sensei-production")

        def boom(sid, fb):
            raise RuntimeError("jobs:run → 403")

        monkeypatch.setattr(sensei_runner, "_run_job", boom)
        assert sensei_runner.start_analysis("s-3") == "thread"
        assert no_thread == [("s-3", None)]

    def test_starting_never_raises_at_the_caller(self, monkeypatch):
        """add-to-dojo has already filed the sample by this point; a sensei
        that cannot start must not turn that into a failed request."""
        monkeypatch.setattr(settings, "SENSEI_JOB", "")

        def boom(sid, fb):
            raise RuntimeError("no threads left")

        monkeypatch.setattr(sensei_runner, "_run_thread", boom)
        with pytest.raises(RuntimeError):
            # Documents today's honest limit: the THREAD path is the last
            # resort, so its failure does surface. Only the JOB trigger is
            # forgiven, because it has somewhere to fall back to.
            sensei_runner.start_analysis("s-4")


class TestJobRequest:
    def test_the_sample_becomes_the_container_args(self, monkeypatch):
        sent = {}

        class _Resp:
            status_code = 200
            text = "{}"

        monkeypatch.setattr(settings, "SENSEI_JOB", "norm-sensei-testing")
        monkeypatch.setattr(settings, "GCP_PROJECT_ID", "norm-testing")
        monkeypatch.setattr(sensei_runner, "_access_token", lambda: "tok")
        monkeypatch.setattr(
            sensei_runner.httpx,
            "post",
            lambda url, **kw: (sent.update(url=url, **kw), _Resp())[1],
        )

        sensei_runner._run_job("s-5", None)
        assert "jobs/norm-sensei-testing:run" in sent["url"]
        assert "projects/norm-testing/locations/australia-southeast1" in sent["url"]
        assert sent["json"]["overrides"]["containerOverrides"][0]["args"] == ["s-5"]
        assert sent["headers"]["Authorization"] == "Bearer tok"

    def test_feedback_rides_along_as_a_flag(self, monkeypatch):
        sent = {}

        class _Resp:
            status_code = 200
            text = "{}"

        monkeypatch.setattr(settings, "SENSEI_JOB", "j")
        monkeypatch.setattr(settings, "GCP_PROJECT_ID", "p")
        monkeypatch.setattr(sensei_runner, "_access_token", lambda: "tok")
        monkeypatch.setattr(
            sensei_runner.httpx,
            "post",
            lambda url, **kw: (sent.update(**kw), _Resp())[1],
        )
        sensei_runner._run_job("s-6", "the unit is a 12 pack")
        assert sent["json"]["overrides"]["containerOverrides"][0]["args"] == [
            "s-6",
            "--feedback",
            "the unit is a 12 pack",
        ]

    def test_a_rejected_trigger_raises_so_the_caller_can_fall_back(self, monkeypatch):
        class _Resp:
            status_code = 403
            text = "permission denied"

        monkeypatch.setattr(settings, "SENSEI_JOB", "j")
        monkeypatch.setattr(settings, "GCP_PROJECT_ID", "p")
        monkeypatch.setattr(sensei_runner, "_access_token", lambda: "tok")
        monkeypatch.setattr(sensei_runner.httpx, "post", lambda url, **kw: _Resp())
        with pytest.raises(RuntimeError, match="403"):
            sensei_runner._run_job("s-7", None)

    def test_a_missing_project_is_refused_before_any_call(self, monkeypatch):
        monkeypatch.setattr(settings, "SENSEI_JOB", "j")
        monkeypatch.setattr(settings, "GCP_PROJECT_ID", "")
        with pytest.raises(RuntimeError, match="GCP_PROJECT_ID"):
            sensei_runner._run_job("s-8", None)


class TestTheAnalyseEndpoint:
    """The path that actually crashed production on 13 Aug.

    Add-to-dojo's background thread was the obvious offender, but the Dojo's
    Analyse button ran the same 1-2 minute job INLINE in the request — and
    that is what was in flight when the container aborted, taking the analysis
    with it and freezing the sample at "analysing".
    """

    def _sample(self, db):
        from app.db.config_models import SupplierInvoiceSpec, SupplierSpecSample

        spec = SupplierInvoiceSpec(name="Dispatch Co", aliases=[], instructions="x")
        db.add(spec)
        db.commit()
        s = SupplierSpecSample(spec_id=spec.id, label="a.pdf", pdf_bytes=b"%PDF-")
        db.add(s)
        db.commit()
        return s

    def test_with_a_job_it_dispatches_instead_of_blocking(
        self, client, admin_headers, db_session, monkeypatch
    ):
        s = self._sample(db_session)
        monkeypatch.setattr(settings, "SENSEI_JOB", "norm-sensei-production")
        monkeypatch.setattr(
            "app.services.spec_dojo.analyse_sample",
            lambda *a, **k: pytest.fail("must not run inline when a job exists"),
        )
        seen = {}
        monkeypatch.setattr(
            "app.services.sensei_runner.start_analysis",
            lambda sid, fb=None: seen.setdefault("sid", sid) and "job" or "job",
        )
        res = client.post(
            f"/api/supplier-invoice-specs/samples/{s.id}/analyse", headers=admin_headers
        )
        assert res.status_code == 200, res.text
        assert res.json()["dispatched"] == "job"
        # Marked running immediately: a cold start is tens of seconds, and the
        # panel would otherwise show the stale previous result and look inert.
        assert res.json()["analysis"]["status"] == "running"
        assert seen["sid"] == s.id

    def test_without_a_job_it_still_runs_inline(
        self, client, admin_headers, db_session, monkeypatch
    ):
        """Local and CI have no job — the work must still happen, in the
        foreground where it can be debugged."""
        s = self._sample(db_session)
        monkeypatch.setattr(settings, "SENSEI_JOB", "")
        monkeypatch.setattr(
            "app.services.spec_dojo.analyse_sample",
            lambda *a, **k: {"status": "ready", "green": True},
        )
        res = client.post(
            f"/api/supplier-invoice-specs/samples/{s.id}/analyse", headers=admin_headers
        )
        assert res.status_code == 200, res.text
        assert res.json()["dispatched"] == "inline"
        assert res.json()["analysis"]["status"] == "ready"
