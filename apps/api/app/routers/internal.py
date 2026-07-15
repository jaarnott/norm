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
