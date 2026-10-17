"""Internal endpoints invoked by trusted infrastructure, not end users.

These are authenticated with a shared secret carried in a request header
(``X-Scheduler-Secret``) rather than a user JWT. The Cloud Run service is
publicly invokable, so the secret is what gates access.
"""

import hmac
import logging

from fastapi import APIRouter, Header, HTTPException

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


def _authorize(provided: str) -> None:
    expected = settings.SCHEDULER_SECRET
    # Fail closed: if no secret is configured, reject everything.
    if not expected or not hmac.compare_digest(provided or "", expected):
        raise HTTPException(status_code=403, detail="forbidden")


@router.post("/internal/run-due-tasks")
async def run_due_tasks_endpoint(
    x_scheduler_secret: str = Header(default=""),
):
    """Claim and execute all automated tasks whose next_run_at is due.

    Called on a fixed cadence by Cloud Scheduler. Claiming is atomic, so it is
    safe to invoke concurrently and across multiple API instances/workers.
    """
    _authorize(x_scheduler_secret)

    from app.services.task_scheduler import run_due_tasks

    result = run_due_tasks()
    # Built-in apps' daily jobs ride the same 5-minute tick — no extra Cloud
    # Scheduler job (services/app_schedules.py). On a background thread, as
    # run_due_tasks dispatches its tasks: a night's runs (email, Google calls)
    # must not hold this worker's event loop. Overlapping ticks are safe — each
    # job claims its (org, op, day) slot before running.
    import threading

    def _app_schedules() -> None:
        try:
            from app.services.app_schedules import run_app_schedules

            apps = run_app_schedules()
            if apps["ran"]:
                logger.info("app schedules ran: %s", apps["ran"])
        except Exception:  # noqa: BLE001
            logger.exception("app schedules failed")

    threading.Thread(target=_app_schedules, name="app-schedules", daemon=True).start()

    # Insights ride the same tick: each venue's run claims its (venue, day)
    # slot first, so overlapping ticks and instances never repeat one
    # (services/insights/runner.py). Its own thread: a Monday's weekly reads
    # take a minute or two per venue.
    def _insights() -> None:
        from app.config import settings

        if not settings.INSIGHTS_ENABLED:
            return
        try:
            from app.services.insights.runner import run_due_insights

            out = run_due_insights()
            if out["ran"]:
                logger.info("insight runs: %s", out["ran"])
        except Exception:  # noqa: BLE001
            logger.exception("insight runs failed")

    threading.Thread(target=_insights, name="insights", daemon=True).start()
    if result["claimed"]:
        logger.info(
            "run-due-tasks claimed %d task(s): %s",
            result["claimed"],
            ", ".join(t[:12] for t in result["task_ids"]),
        )
    return result


@router.post("/internal/run-app-schedules")
async def run_app_schedules_endpoint(
    x_scheduler_secret: str = Header(default=""),
):
    """Run any due built-in app schedules now (also runs every 5 minutes as
    part of /internal/run-due-tasks). Safe to call repeatedly — each job runs
    once per org per day."""
    _authorize(x_scheduler_secret)
    from starlette.concurrency import run_in_threadpool

    from app.services.app_schedules import run_app_schedules

    return await run_in_threadpool(run_app_schedules)


@router.post("/internal/prewarm-reviews")
async def prewarm_reviews_endpoint(
    venue_id: str | None = None,
    x_scheduler_secret: str = Header(default=""),
):
    """Warm the invoice-copy extraction cache ahead of whoever opens next.

    Called on a cadence by Cloud Scheduler. Bounded by construction (see
    ``invoice_prewarm``): a handful of extractions per tick, one at a time
    fleet-wide, behind an advisory lock — so this can never become the
    200-invoice sequential loop docs/scaling-and-load.md warns about, and
    never holds more than one open invoice's worth of memory.

    Runs INLINE rather than in a daemon thread: a tick is well inside the
    scheduler's deadline, and inline means a failure is a visible non-200 in
    Cloud Scheduler's logs instead of a thread nobody hears about.

    ``venue_id`` is for local smoke tests. There is deliberately no ``limit``
    parameter — the cap is a constant, not a caller's choice.
    """
    _authorize(x_scheduler_secret)

    from app.services.invoice_prewarm import prewarm_extractions

    result = prewarm_extractions(venue_id=venue_id)
    if result.get("warmed") or result.get("failed"):
        logger.info(
            "prewarm-reviews: warmed %s, failed %s (%ss)",
            result.get("warmed"),
            result.get("failed"),
            result.get("seconds"),
        )
    return result


@router.post("/internal/validate-config")
async def validate_config_endpoint(
    x_scheduler_secret: str = Header(default=""),
):
    """Check the database-held configuration for known breakages.

    Connector specs, agent prompts and model selections live in the database and
    are edited via the Settings UI, so CI cannot see them — and config can drift
    long after a deploy with no code change at all. This runs the same checks CI
    unit-tests, but against the real databases.
    """
    _authorize(x_scheduler_secret)

    from app.services.config_validator import validate_config

    result = validate_config()
    if result["ok"]:
        logger.info("validate-config: no issues")
    else:
        logger.error("validate-config: %d issue(s) found", result["issue_count"])
        for issue in result["issues"]:
            logger.error(
                "validate-config: [%s] %s — %s | fix: %s",
                issue["severity"],
                issue["where"],
                issue["problem"],
                issue["fix"],
            )
    return result


@router.post("/internal/refresh-tokens")
async def refresh_tokens_endpoint(
    x_scheduler_secret: str = Header(default=""),
):
    """Keep OAuth connector tokens alive.

    Called on a schedule by Cloud Scheduler. Rotating refresh tokens (LoadedHub)
    only have their lifetime reset when a refresh actually happens, so an idle
    connector expires and locks us out. Refreshing on a cadence — rather than
    only when a task happens to run — is what makes this reliable.
    """
    _authorize(x_scheduler_secret)

    from app.db.encrypted import write_key_canary
    from app.services.oauth_service import refresh_all_tokens

    # The marker scripts/encrypt_connection_tokens.py checks before it
    # re-encrypts anything: written under THIS service's newest key.
    canary = write_key_canary()
    result = refresh_all_tokens()
    result["key_canary"] = canary
    logger.info(
        "refresh-tokens: refreshed=%d failed=%d skipped=%d",
        len(result["refreshed"]),
        len(result["failed"]),
        len(result["skipped"]),
    )
    for failure in result["failed"]:
        logger.warning(
            "refresh-tokens: %s needs re-authorization: %s",
            failure["connector"],
            failure["error"],
        )
    return result


@router.post("/internal/sync-mailboxes")
async def sync_mailboxes_endpoint(
    x_scheduler_secret: str = Header(default=""),
):
    """Sync every connected Norm Functions venue mailbox (Gmail → mirror).

    Called every minute by Cloud Scheduler. The work runs in a threadpool
    under a ~45s deadline (a mailbox with a backlog carries on next minute);
    per-mailbox leases make overlapping ticks and concurrent "sync now"
    calls safe. Answers 503 while any active mailbox has gone more than ten
    minutes without a clean sync, so the existing 5xx alerting notices a
    stalled mailbox — a dead grant is not "active" (it emails the venue's
    people once instead).
    """
    _authorize(x_scheduler_secret)
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool

    from app.services.functions.mailbox.sync import sync_mailboxes

    result = await run_in_threadpool(sync_mailboxes, deadline_seconds=45)
    worked = [r for r in result["results"] if not r.get("skipped")]
    if worked or result["healed"]:
        logger.info(
            "sync-mailboxes: %d synced, %d failed, %d healed",
            sum(1 for r in worked if r.get("ok")),
            sum(1 for r in worked if r.get("ok") is False),
            len(result["healed"]),
        )
    if result["lagging"]:
        logger.error(
            "sync-mailboxes: %d mailbox(es) lagging: %s",
            len(result["lagging"]),
            ", ".join(a["google_email"] for a in result["lagging"]),
        )
        return JSONResponse(status_code=503, content=result)
    return result


@router.post("/internal/functions-retention")
def functions_retention_endpoint(
    x_scheduler_secret: str = Header(default=""),
):
    """Daily Norm Functions retention: OTP state older than a day, expired or
    revoked portal sessions, and form-submission IP hashes / user agents older
    than six months (services/functions/retention.py). Plain ``def`` so the
    sync DB work runs in the threadpool, not on the event loop."""
    _authorize(x_scheduler_secret)

    from app.db.engine import SessionLocal
    from app.services.functions.retention import purge

    db = SessionLocal()
    try:
        result = purge(db)
        db.commit()
    finally:
        db.close()
    logger.info("functions-retention: %s", result)
    return result


@router.post("/internal/mcp-gc")
async def mcp_gc_endpoint(
    x_scheduler_secret: str = Header(default=""),
):
    """Garbage-collect expired MCP OAuth state.

    Deletes expired authorization codes, long-expired/revoked tokens, and stale
    rate-limit windows. This is the fix for the OAuthState-never-GC'd problem
    the connector-OAuth table has — do not let these tables grow forever.

    Called on a schedule by Cloud Scheduler, guarded by X-Scheduler-Secret.
    """
    _authorize(x_scheduler_secret)

    from datetime import datetime, timedelta, timezone

    from sqlalchemy import text

    from app.db.engine import SessionLocal
    from app.mcp.ratelimit import gc_expired

    now = datetime.now(timezone.utc)
    token_cutoff = now - timedelta(days=30)

    db = SessionLocal()
    try:
        codes = db.execute(
            text("DELETE FROM mcp_authorization_codes WHERE expires_at < :now"),
            {"now": now},
        ).rowcount
        tokens = db.execute(
            text(
                "DELETE FROM mcp_tokens "
                "WHERE (expires_at < :cutoff) OR "
                "(revoked_at IS NOT NULL AND revoked_at < :cutoff)"
            ),
            {"cutoff": token_cutoff},
        ).rowcount
        db.commit()
        windows = gc_expired(db)
    finally:
        db.close()

    result = {"codes": codes, "tokens": tokens, "rate_limit_windows": windows}
    logger.info("mcp-gc: %s", result)
    return result
