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
    if result["claimed"]:
        logger.info(
            "run-due-tasks claimed %d task(s): %s",
            result["claimed"],
            ", ".join(t[:12] for t in result["task_ids"]),
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

    from app.services.oauth_service import refresh_all_tokens

    result = refresh_all_tokens()
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
