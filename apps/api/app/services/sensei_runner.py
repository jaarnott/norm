"""Start a dojo sensei analysis — in a Cloud Run job where one is configured.

One entry point, two backends, chosen by whether ``settings.SENSEI_JOB`` is
set. The caller does not care which ran: both return immediately and the
sample records its own progress.

**Job** (deployed environments). A separate container with its own memory,
whose failures are visible as a failed execution you can re-run.

**Thread** (local, tests, CI). The original behaviour, kept because nothing
should need GCP to develop against — and because a job that cannot be
triggered must degrade to doing the work, not to doing nothing.

Triggering costs no new dependency: Cloud Run's metadata server issues the
token and ``httpx`` is already here. The runtime service account holds
``roles/editor``, which covers ``run.jobs.run``.
"""

from __future__ import annotations

import logging
import threading

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_METADATA_TOKEN = (
    "http://metadata.google.internal/computeMetadata/v1/"
    "instance/service-accounts/default/token"
)


def _access_token() -> str:
    r = httpx.get(_METADATA_TOKEN, headers={"Metadata-Flavor": "Google"}, timeout=5.0)
    r.raise_for_status()
    return r.json()["access_token"]


def _run_job(sample_id: str, feedback: str | None) -> None:
    """Execute the sensei job with this sample as its argument.

    The override replaces the container's args for this execution only, so one
    job definition serves every sample.
    """
    project = settings.GCP_PROJECT_ID
    if not project:
        raise RuntimeError("GCP_PROJECT_ID is not set")
    args = [sample_id] + (["--feedback", feedback] if feedback else [])
    url = (
        f"https://run.googleapis.com/v2/projects/{project}/locations/"
        f"{settings.GCP_REGION}/jobs/{settings.SENSEI_JOB}:run"
    )
    resp = httpx.post(
        url,
        headers={"Authorization": f"Bearer {_access_token()}"},
        json={"overrides": {"containerOverrides": [{"args": args}]}},
        timeout=30.0,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"jobs:run → {resp.status_code}: {resp.text[:200]}")
    logger.info("sensei job started for sample %s", sample_id)


def _run_thread(sample_id: str, feedback: str | None) -> None:
    """The in-process fallback — a daemon thread with its own sessions."""

    def _work() -> None:
        from app.db.engine import SessionLocal, _ConfigSessionLocal
        from app.services import spec_dojo

        db, cdb = SessionLocal(), _ConfigSessionLocal()
        try:
            spec_dojo.analyse_sample(db, cdb, sample_id, feedback=feedback)
        except Exception:  # noqa: BLE001 — the sample records its own failure
            logger.exception("in-process dojo analysis failed for %s", sample_id)
        finally:
            db.close()
            cdb.close()

    threading.Thread(
        target=_work, daemon=True, name=f"dojo-analysis-{sample_id[:8]}"
    ).start()


def start_analysis(sample_id: str, feedback: str | None = None) -> str:
    """Kick off the analysis and return how it was started ("job"|"thread").

    Never raises: a sensei that cannot start must not take the caller's
    request down with it — add-to-dojo has already filed the sample, and the
    analysis can be re-run from the Dojo page. A failed TRIGGER falls back to
    the thread rather than silently doing nothing, because a sample stuck at
    "analysing" with nobody working on it is the failure mode this whole move
    exists to remove.
    """
    if settings.SENSEI_JOB:
        try:
            _run_job(sample_id, feedback)
            return "job"
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "could not start the sensei job for %s (%s) — running in process",
                sample_id,
                exc,
            )
    _run_thread(sample_id, feedback)
    return "thread"
