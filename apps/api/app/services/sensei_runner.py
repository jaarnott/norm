"""Sensei execution: a durable queue kept in the sample's ``analysis`` JSON.

``enqueue()`` is the only way a sensei run starts — every intake (the Dojo
buttons, cannot-receive, a proposal-thread reply) writes ``status="queued"``
and returns immediately, so no HTTP request ever waits on (or dies with) an
analysis. Execution belongs to the worker loop:

- **Without ``SENSEI_JOB``** (local, CI): ``worker_tick`` claims a queued run
  and executes it inline in the worker thread. If the process dies mid-run
  (dev-server reload, crash), the heartbeat goes stale and the next tick —
  in whatever process replaces it — requeues and re-runs it. This is what
  the old daemon-thread fallback could never do: the run *survives* the
  process (16 Aug 2026: every dev reload silently killed an in-flight run,
  and the inline endpoint 504'd through the github.dev proxy).
- **With ``SENSEI_JOB``** (deployed): the worker only *dispatches* the Cloud
  Run job (and re-dispatches when a job died or a trigger was lost). The old
  silent fallback-to-thread is gone — a failed trigger leaves the run
  visibly queued and retried next tick, instead of quietly running a
  memory-heavy analysis inside the web container (the Aug-12/13 OOM path).

State machine, all in ``SupplierSpecSample.analysis`` (shared config DB):

    queued ──claim──► running(claimed_by, heartbeat_at, phase)
       ▲                    │
       │                    ├─► ready | not_green | failed(error)
       └── requeue(attempts+1) ◄─ heartbeat stale (executor died)
                                   attempts > MAX_ATTEMPTS → failed

Runs are environment-scoped (``queued_env``): a run queued from local dev is
executed by the local worker, a production run by the production job — the
config DB is shared, the token spend and llm_call records should land where
the human pressed the button.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import socket
import threading

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# A running analysis heartbeats every phase change (a phase is at most one
# model call, ~60-90s worst case). Twice that with margin = the executor died.
HEARTBEAT_STALE_SECONDS = 180
# A queued run nobody claimed in this long means the worker/job for its
# environment is not running at all — surface that, don't spin forever.
QUEUED_ABANDONED_SECONDS = 15 * 60
# One automatic restart after a death; the third strike fails the run.
MAX_ATTEMPTS = 2
# How long a successful job dispatch may take to claim the run (cold start +
# startup + secret load) before the worker re-dispatches.
DISPATCH_GRACE_SECONDS = 180
WORKER_INTERVAL_SECONDS = 5.0

_METADATA_TOKEN = (
    "http://metadata.google.internal/computeMetadata/v1/"
    "instance/service-accounts/default/token"
)


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(dt: _dt.datetime | None = None) -> str:
    return (dt or _now()).isoformat()


def _age_seconds(iso: object) -> float | None:
    try:
        then = _dt.datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    return (_now() - then).total_seconds()


def worker_identity() -> str:
    return f"{settings.ENVIRONMENT}:{socket.gethostname()}:{os.getpid()}"


# --------------------------------------------------------------------------
# Enqueue — the only entry point for starting a run
# --------------------------------------------------------------------------


def enqueue(sample_id: str, feedback: str | None = None) -> str:
    """Queue a sensei run for ``sample_id``; returns the resulting status.

    Never raises: the caller is a user request (cannot-receive, a Run-sensei
    press) that must not die because the queue write failed — a sample that
    cannot be queued logs the reason and the button can be pressed again.
    """
    db = None
    try:
        from app.db.config_models import SupplierSpecSample
        from app.db.engine import _ConfigSessionLocal

        db = _ConfigSessionLocal()
        sample = (
            db.query(SupplierSpecSample)
            .filter(SupplierSpecSample.id == sample_id)
            .with_for_update()
            .first()
        )
        if not sample:
            logger.warning("enqueue: sample %s not found", sample_id)
            return "missing"
        prev = dict(sample.analysis or {})
        status = prev.get("status")
        thread = list(prev.get("thread") or [])
        if str(feedback or "").strip():
            # The admin's correction rides in the queue entry; the executor
            # passes it to analyse_sample, which appends it to the thread.
            queued_feedback = str(feedback).strip()
        else:
            queued_feedback = str(prev.get("feedback") or "") or None

        if status == "running":
            age = _age_seconds(prev.get("heartbeat_at") or prev.get("at"))
            if age is not None and age < HEARTBEAT_STALE_SECONDS:
                # A live executor is already on it — re-pressing must not
                # kill or duplicate the run.
                return "running"
        if status == "queued":
            # Already queued — refresh the feedback if a new one arrived.
            if queued_feedback != prev.get("feedback"):
                sample.analysis = {**prev, "feedback": queued_feedback}
                db.commit()
            return "queued"

        sample.analysis = {
            "status": "queued",
            "thread": thread,
            "feedback": queued_feedback,
            "queued_at": _iso(),
            "queued_env": settings.ENVIRONMENT,
            # A fresh human press resets the death counter — attempts count
            # executor deaths within one request, not lifetime retries.
            "attempts": 0,
            "at": _iso(),
        }
        db.commit()
        logger.info("sensei run queued for sample %s", sample_id)
        return "queued"
    except Exception:  # noqa: BLE001 — the press must survive a queue failure
        logger.exception("could not queue the sensei for %s", sample_id)
        return "error"
    finally:
        if db is not None:
            db.close()


# Old name, same contract (fire-and-forget start). Kept because call sites
# and tests patch it by this name.
def start_analysis(sample_id: str, feedback: str | None = None) -> str:
    return enqueue(sample_id, feedback)


# --------------------------------------------------------------------------
# Claiming — shared by the inline worker and the Cloud Run job
# --------------------------------------------------------------------------


def claim(sample_id: str, claimed_by: str) -> dict | None:
    """Atomically claim a queued (or dead-running) run; return the entry.

    Returns None when there is nothing to claim — already finished, or a
    live executor holds it. Idempotent for the claimer: re-claiming your own
    running entry succeeds (the job retries its execution on infra errors).
    """
    from app.db.config_models import SupplierSpecSample
    from app.db.engine import _ConfigSessionLocal

    db = _ConfigSessionLocal()
    try:
        sample = (
            db.query(SupplierSpecSample)
            .filter(SupplierSpecSample.id == sample_id)
            .with_for_update()
            .first()
        )
        if not sample:
            return None
        a = dict(sample.analysis or {})
        status = a.get("status")
        if status == "running":
            age = _age_seconds(a.get("heartbeat_at") or a.get("at"))
            live = age is not None and age < HEARTBEAT_STALE_SECONDS
            if live and a.get("claimed_by") != claimed_by:
                return None
        elif status != "queued":
            return None
        entry = {
            **a,
            "status": "running",
            "claimed_by": claimed_by,
            "heartbeat_at": _iso(),
            "phase": "starting",
            "at": _iso(),
        }
        sample.analysis = entry
        db.commit()
        return entry
    finally:
        db.close()


# --------------------------------------------------------------------------
# The worker loop
# --------------------------------------------------------------------------


def _run_job(sample_id: str) -> None:
    """Trigger one Cloud Run job execution for this sample."""
    project = settings.GCP_PROJECT_ID
    if not project:
        raise RuntimeError("GCP_PROJECT_ID is not set")
    r = httpx.get(_METADATA_TOKEN, headers={"Metadata-Flavor": "Google"}, timeout=5.0)
    r.raise_for_status()
    token = r.json()["access_token"]
    url = (
        f"https://run.googleapis.com/v2/projects/{project}/locations/"
        f"{settings.GCP_REGION}/jobs/{settings.SENSEI_JOB}:run"
    )
    resp = httpx.post(
        url,
        headers={"Authorization": f"Bearer {token}"},
        json={"overrides": {"containerOverrides": [{"args": [sample_id]}]}},
        timeout=30.0,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"jobs:run → {resp.status_code}: {resp.text[:200]}")
    logger.info("sensei job dispatched for sample %s", sample_id)


def _fail(db, sample, entry: dict, error: str) -> None:
    sample.analysis = {
        **entry,
        "status": "failed",
        "error": error,
        "at": _iso(),
    }
    db.commit()


def worker_tick() -> dict:
    """One pass over the queue: requeue the dead, execute/dispatch the queued.

    Returns a small summary (for tests and logs). Inline execution happens
    inside the tick — the worker thread is the executor, and queued runs are
    processed one at a time (an admin tool; fairness beats parallelism).
    """
    from app.db.config_models import SupplierSpecSample
    from app.db.engine import _ConfigSessionLocal

    summary = {"requeued": [], "failed": [], "dispatched": [], "ran": []}
    db = _ConfigSessionLocal()
    try:
        rows = (
            db.query(SupplierSpecSample)
            .filter(SupplierSpecSample.analysis.isnot(None))
            .all()
        )
        for s in rows:
            a = dict(s.analysis or {})
            status = a.get("status")
            if status not in ("queued", "running"):
                continue
            env = a.get("queued_env")
            if env != settings.ENVIRONMENT:
                # Another environment's run (or a pre-queue legacy entry with
                # no env at all) — not ours to touch.
                continue

            if status == "running":
                age = _age_seconds(a.get("heartbeat_at") or a.get("at"))
                if age is None or age < HEARTBEAT_STALE_SECONDS:
                    continue
                # The executor died. Requeue — or give up after MAX_ATTEMPTS.
                attempts = int(a.get("attempts") or 0) + 1
                if attempts > MAX_ATTEMPTS:
                    _fail(
                        db,
                        s,
                        a,
                        f"the analysis died {attempts} times without finishing "
                        "— see the server logs, then press Re-run sensei",
                    )
                    summary["failed"].append(s.id)
                    continue
                a = {
                    **a,
                    "status": "queued",
                    "attempts": attempts,
                    "queued_at": _iso(),
                    "at": _iso(),
                }
                # Force a fresh dispatch in job mode.
                a.pop("dispatched_at", None)
                s.analysis = a
                db.commit()
                summary["requeued"].append(s.id)
                logger.warning(
                    "sensei run for %s lost its executor (%s) — requeued (attempt %d)",
                    s.id,
                    a.get("claimed_by"),
                    attempts,
                )

            # status is now "queued" (either originally, or just requeued)
            if settings.SENSEI_JOB:
                dispatched_age = _age_seconds(a.get("dispatched_at"))
                if (
                    dispatched_age is not None
                    and dispatched_age < DISPATCH_GRACE_SECONDS
                ):
                    continue  # a job is (still) starting up for this run
                try:
                    _run_job(s.id)
                    s.analysis = {**a, "dispatched_at": _iso()}
                    db.commit()
                    summary["dispatched"].append(s.id)
                except Exception as exc:  # noqa: BLE001 — retried next tick, visibly queued
                    logger.warning(
                        "could not dispatch the sensei job for %s (%s) — "
                        "still queued, retrying",
                        s.id,
                        exc,
                    )
            else:
                _execute_inline(s.id, a.get("feedback"))
                summary["ran"].append(s.id)
        return summary
    finally:
        db.close()


def _execute_inline(sample_id: str, feedback: str | None) -> None:
    """Claim and run one analysis in this thread (local/CI executor)."""
    from app.db.engine import SessionLocal
    from app.services import spec_dojo

    entry = claim(sample_id, worker_identity())
    if entry is None:
        return
    db = SessionLocal()
    from app.db.engine import _ConfigSessionLocal

    config_db = _ConfigSessionLocal()
    try:
        spec_dojo.analyse_sample(
            db, config_db, sample_id, feedback=entry.get("feedback") or feedback
        )
        # call_llm only add()+flush()es its llm_calls rows; the worker owns
        # this session, so commit or the records roll back on close.
        db.commit()
    except Exception:  # noqa: BLE001 — analyse_sample stores its own failure
        logger.exception("inline sensei run failed for %s", sample_id)
    finally:
        db.close()
        config_db.close()


# --------------------------------------------------------------------------
# The worker thread
# --------------------------------------------------------------------------

_worker_thread: threading.Thread | None = None
_stop = threading.Event()


def start_worker() -> None:
    """Start the queue worker (idempotent). Called from app startup."""
    global _worker_thread
    if _worker_thread is not None and _worker_thread.is_alive():
        return
    _stop.clear()

    def _loop() -> None:
        logger.info("sensei worker started (%s)", worker_identity())
        while not _stop.wait(WORKER_INTERVAL_SECONDS):
            try:
                worker_tick()
            except Exception:  # noqa: BLE001 — one bad tick must not kill the loop
                logger.exception("sensei worker tick failed")

    _worker_thread = threading.Thread(target=_loop, daemon=True, name="sensei-worker")
    _worker_thread.start()


def stop_worker() -> None:
    _stop.set()
