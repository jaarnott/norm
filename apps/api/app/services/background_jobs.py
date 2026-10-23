"""Background jobs — long work that runs outside the chat request.

A chat request is cut off after 25 minutes (Cloud Run's timeout), and work
done inside one lives or dies with it: on 14 Oct 2026 two attempts to receive
~200 invoices for a new venue ran 25 minutes each, cost ~$15 and saved
nothing. A job instead:

- is ENQUEUED by the chat (which answers straight away with a live card);
- is CLAIMED by a worker thread (one per API process, ``start_worker``) with
  ``FOR UPDATE SKIP LOCKED`` — two workers never run the same job;
- HEARTBEATS and saves its progress as it goes (``JobContext.update``);
- is picked up again by another worker when its heartbeat goes stale — a
  deploy or scale-in restarted the instance — and carries on from its saved
  progress; after ``MAX_ATTEMPTS`` claims in a row with no progress it is
  marked failed;
- can be CANCELLED (checked between units of work);
- ends with the handler's summary posted into the thread it came from, in
  the same commit as its end state.

Handlers register per kind (``@register("receive_invoices")``) and must be
resumable: everything they finished is in ``ctx.progress``.
"""

from __future__ import annotations

import copy
import logging
import os
import socket
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.models import BackgroundJob

logger = logging.getLogger(__name__)

#: A running job whose worker hasn't been heard from for this long is taken
#: to be dead (its instance restarted) and is claimed again. The job is heard
#: from after every invoice, and one invoice can include a 1–2 minute supplier
#: study: at 3 minutes a live worker was taken over (16 Oct 2026).
HEARTBEAT_STALE_SECONDS = 15 * 60
#: Claims in a row that die without progress before the job is marked failed.
MAX_ATTEMPTS = 3
WORKER_INTERVAL_SECONDS = 5
ACTIVE = ("queued", "running")

_HANDLERS: dict[str, Callable[["JobContext"], dict | None]] = {}


def register(kind: str):
    """Register the handler for a job kind. It receives a ``JobContext`` and
    returns the job's result (or None)."""

    def deco(fn):
        _HANDLERS[kind] = fn
        return fn

    return deco


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _session() -> Session:
    from app.db.engine import SessionLocal

    return SessionLocal()


def worker_identity() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"


# --------------------------------------------------------------------------
# Enqueue / read / cancel
# --------------------------------------------------------------------------


def enqueue(
    db: Session,
    kind: str,
    *,
    title: str,
    params: dict,
    progress: dict | None = None,
    user_id: str | None = None,
    organization_id: str | None = None,
    venue_id: str | None = None,
    thread_id: str | None = None,
) -> tuple[BackgroundJob, bool]:
    """(job, created). One active job per (kind, venue): asking again while
    one runs returns it instead of starting the same work twice."""
    existing = (
        db.query(BackgroundJob)
        .filter(
            BackgroundJob.kind == kind,
            BackgroundJob.venue_id == venue_id,
            BackgroundJob.status.in_(ACTIVE),
        )
        .order_by(BackgroundJob.created_at.desc())
        .first()
    )
    if existing is not None:
        return existing, False
    job = BackgroundJob(
        kind=kind,
        title=title,
        params=params,
        progress=progress or {},
        user_id=user_id,
        organization_id=organization_id,
        venue_id=venue_id,
        thread_id=thread_id,
        status="queued",
    )
    db.add(job)
    db.flush()
    return job, True


def request_cancel(db: Session, job_id: str) -> BackgroundJob | None:
    job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
    if job is None:
        return None
    if job.status == "queued":
        job.status = "cancelled"
        job.finished_at = _now()
    elif job.status == "running":
        job.cancel_requested = True
    return job


def serialize(job: BackgroundJob) -> dict:
    progress = dict(job.progress or {})
    progress.pop("done_ids", None)  # bookkeeping, not for the card
    now = _now()
    started = job.started_at
    ended = job.finished_at or now
    return {
        "id": job.id,
        "kind": job.kind,
        "title": job.title,
        "status": job.status,
        "cancel_requested": bool(job.cancel_requested),
        "progress": progress,
        "result": job.result,
        "error": job.error,
        "attempts": job.attempts,
        "thread_id": job.thread_id,
        "venue_id": job.venue_id,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": started.isoformat() if started else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "elapsed_seconds": int((ended - started).total_seconds()) if started else 0,
        # A running job whose worker has gone quiet is about to be picked up
        # again — the card says so rather than looking frozen.
        "stalled": bool(
            job.status == "running"
            and job.heartbeat_at
            and (now - job.heartbeat_at).total_seconds() > HEARTBEAT_STALE_SECONDS
        ),
    }


def thread_manifest(thread, db: Session) -> str:
    """What background jobs this conversation has started, for the model —
    so "how are we going?" is answered from the job's real state instead of
    by starting the work again."""
    tid = getattr(thread, "id", None)
    if not tid:
        return ""
    since = _now() - timedelta(days=2)
    jobs = (
        db.query(BackgroundJob)
        .filter(BackgroundJob.thread_id == tid, BackgroundJob.created_at >= since)
        .order_by(BackgroundJob.created_at.desc())
        .limit(5)
        .all()
    )
    if not jobs:
        return ""
    lines = ["[Background jobs started from this conversation]"]
    for j in jobs:
        p = j.progress or {}
        counts = ", ".join(
            f"{p[k]} {label}"
            for k, label in (
                ("received", "received"),
                ("deleted", "deleted"),
                ("needs_you", "need the person"),
                ("failed", "failed"),
            )
            if p.get(k)
        )
        where = f"{p.get('done', 0)} of {p.get('total', '?')} done"
        state = {
            "queued": "waiting to start",
            "running": "running",
            "done": "finished",
            "failed": f"failed ({(j.error or '')[:120]})",
            "cancelled": "cancelled",
        }.get(j.status, j.status)
        lines.append(
            f"- {j.title}: {state} — {where}" + (f" ({counts})" if counts else "")
        )
    lines.append(
        "A running job carries on by itself; its card in this conversation shows "
        "live progress and Norm posts the result here when it finishes. Report "
        "this state when asked — never start the same work again while it runs."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Running a job
# --------------------------------------------------------------------------


class LostClaim(Exception):
    """Another worker has taken this job over (ours was presumed dead)."""


class JobContext:
    """What a handler gets: its params, its saved progress, and the means to
    report progress (which is also the heartbeat) and notice a cancel."""

    def __init__(self, job_id: str, worker: str, params: dict, progress: dict):
        self.job_id = job_id
        self.worker = worker
        self.params = params
        self.progress = progress

    def update(self, **changes) -> None:
        """Merge ``changes`` into the saved progress and heartbeat. Raises
        LostClaim when another worker now owns the job."""
        db = _session()
        try:
            job = (
                db.query(BackgroundJob).filter(BackgroundJob.id == self.job_id).first()
            )
            if job is None or job.claimed_by != self.worker:
                raise LostClaim(self.job_id)
            progress = copy.deepcopy(job.progress or {})  # never mutate JSON in place
            advanced = int(changes.get("done") or 0) > int(progress.get("done") or 0)
            progress.update(changes)
            job.progress = progress
            job.heartbeat_at = _now()
            if advanced:
                # Attempts count claims WITHOUT progress: a deploy restarting
                # the instance mid-job is not the job failing, however often
                # it happens; dying again and again on the same step is.
                job.attempts = 1
            db.commit()
            self.progress = progress
        finally:
            db.close()

    def heartbeat(self) -> None:
        self.update()

    def cancelled(self) -> bool:
        """Stop before the next unit of work: cancelled, or the job now
        belongs to another worker (two workers on one job did the same
        invoices twice, 16 Oct 2026)."""
        db = _session()
        try:
            job = (
                db.query(BackgroundJob).filter(BackgroundJob.id == self.job_id).first()
            )
            return bool(
                job is None
                or job.cancel_requested
                or job.status == "cancelled"
                or job.claimed_by != self.worker
            )
        finally:
            db.close()


def claim(worker: str | None = None) -> JobContext | None:
    """Take the next job: a queued one, or a running one whose worker has gone
    quiet. A job that has used up its attempts is failed instead."""
    worker = worker or worker_identity()
    db = _session()
    try:
        stale_before = _now() - timedelta(seconds=HEARTBEAT_STALE_SECONDS)
        job = (
            db.query(BackgroundJob)
            .filter(
                or_(
                    BackgroundJob.status == "queued",
                    (BackgroundJob.status == "running")
                    & (BackgroundJob.heartbeat_at < stale_before),
                )
            )
            .order_by(BackgroundJob.created_at)
            .with_for_update(skip_locked=True)
            .first()
        )
        if job is None:
            return None
        if job.attempts >= MAX_ATTEMPTS:
            job.status = "failed"
            job.error = job.error or (
                f"stopped {job.attempts} times without finishing — not retried again"
            )
            job.finished_at = _now()
            _post_finish(db, job)
            db.commit()
            _after_finish(job.id, job.kind)
            return None
        if job.cancel_requested:
            job.status = "cancelled"
            job.finished_at = _now()
            db.commit()
            return None
        job.status = "running"
        job.claimed_by = worker
        job.heartbeat_at = _now()
        job.attempts = (job.attempts or 0) + 1
        job.started_at = job.started_at or _now()
        db.commit()
        return JobContext(
            job.id, worker, dict(job.params or {}), dict(job.progress or {})
        )
    finally:
        db.close()


def run(ctx: JobContext) -> None:
    """Run a claimed job to its end state."""
    db = _session()
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.id == ctx.job_id).first()
        kind = job.kind if job else None
    finally:
        db.close()
    handler = _HANDLERS.get(kind or "")
    status, result, error = "done", None, None
    if handler is None:
        status, error = "failed", f"no handler for job kind {kind!r}"
    else:
        try:
            result = handler(ctx)
            if ctx.cancelled():
                status = "cancelled"
        except LostClaim:
            logger.info("job %s was taken over by another worker", ctx.job_id)
            return
        except Exception as exc:  # noqa: BLE001 — a failed job is recorded, not raised
            logger.exception("job %s failed", ctx.job_id)
            status, error = "failed", str(exc)[:2000]
    db = _session()
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.id == ctx.job_id).first()
        if job is None or job.claimed_by != ctx.worker:
            return
        job.status = status
        job.result = result
        job.error = error
        job.finished_at = _now()
        job.heartbeat_at = _now()
        _post_finish(db, job)
        db.commit()
    finally:
        db.close()
    _after_finish(ctx.job_id, kind)


#: What a job leaves in its conversation when it ends — ``fn(db, job)`` adds
#: rows (Norm's summary) to the SAME transaction as the end state, so a card
#: that sees the job end and reloads the thread always finds the summary.
_ON_FINISH: dict[str, Callable[[Session, BackgroundJob], None]] = {}
#: Side effects once that is committed — ``fn(job_id)``, e.g. the email.
_AFTER_FINISH: dict[str, Callable[[str], None]] = {}


def on_finish(kind: str):
    def deco(fn):
        _ON_FINISH[kind] = fn
        return fn

    return deco


def after_finish(kind: str):
    def deco(fn):
        _AFTER_FINISH[kind] = fn
        return fn

    return deco


def _post_finish(db: Session, job: BackgroundJob) -> None:
    hook = _ON_FINISH.get(job.kind or "")
    if hook is None:
        return
    try:
        with db.begin_nested():  # a failed summary never undoes the end state
            hook(db, job)
    except Exception:  # noqa: BLE001
        logger.exception("job %s finish hook failed", job.id)


def _after_finish(job_id: str, kind: str | None) -> None:
    hook = _AFTER_FINISH.get(kind or "")
    if hook is None:
        return
    try:
        hook(job_id)
    except Exception:  # noqa: BLE001 — reporting never undoes the job
        logger.exception("job %s after-finish hook failed", job_id)


def worker_tick(worker: str | None = None) -> bool:
    """Claim and run at most one job. True when one ran."""
    _load_handlers()
    ctx = claim(worker)
    if ctx is None:
        return False
    run(ctx)
    return True


def _load_handlers() -> None:
    # Handlers register on import.
    from app.services import catalogue_job  # noqa: F401 — Catalogue page merges
    from app.services import invoice_receive_job  # noqa: F401
    from app.services import task_scheduler  # noqa: F401 — scheduled task runs


_worker_thread: threading.Thread | None = None
_stop = threading.Event()


def start_worker() -> None:
    """Start this process's job worker (idempotent). Called from app startup."""
    global _worker_thread
    if _worker_thread is not None and _worker_thread.is_alive():
        return
    _stop.clear()

    def _loop() -> None:
        logger.info("background job worker started (%s)", worker_identity())
        while not _stop.wait(WORKER_INTERVAL_SECONDS):
            try:
                worker_tick()
            except Exception:  # noqa: BLE001 — one bad tick must not kill the loop
                logger.exception("background job worker tick failed")

    _worker_thread = threading.Thread(target=_loop, daemon=True, name="job-worker")
    _worker_thread.start()


def stop_worker() -> None:
    _stop.set()
