"""Scheduling and execution for automated agent workflows.

Execution is driven by an external trigger (Cloud Scheduler) that calls the
``/internal/run-due-tasks`` endpoint on a fixed cadence. Each AutomatedTask
carries a ``next_run_at`` timestamp; the runner atomically claims due tasks
(advancing ``next_run_at`` under a row lock) and then executes them.

This replaces the previous in-process ``BackgroundScheduler``, which was
unreliable under gunicorn's multiple workers and Cloud Run autoscaling
(jobs lived only in the worker/instance that happened to schedule them, and
were lost on every recycle), and which computed cron times in the container's
UTC local time rather than the business timezone.
"""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger

from app.config import settings

logger = logging.getLogger(__name__)


def _resolve_timezone(config: dict) -> ZoneInfo:
    """Timezone for a task's schedule: per-task override or the global default."""
    tzname = (config or {}).get("timezone") or settings.SCHEDULER_TIMEZONE
    try:
        return ZoneInfo(tzname)
    except Exception:
        logger.warning("Unknown scheduler timezone %r; falling back to UTC", tzname)
        return ZoneInfo("UTC")


def compute_next_run_at(
    schedule_type: str, config: dict | None, after: datetime | None = None
) -> datetime | None:
    """Return the next UTC fire time strictly after ``after`` (default: now).

    Returns None for manual tasks (never auto-fire). Cron-style schedules are
    evaluated in the task's configured timezone so "daily at 9:00" means 9am
    local, not 9am UTC.
    """
    config = config or {}
    now = after or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    if schedule_type == "manual":
        return None
    if schedule_type == "hourly":
        return now.astimezone(timezone.utc) + timedelta(hours=1)

    tz = _resolve_timezone(config)
    hour = int(config.get("hour", 9))
    minute = int(config.get("minute", 0))

    if schedule_type == "daily":
        trigger = CronTrigger(hour=hour, minute=minute, timezone=tz)
    elif schedule_type == "weekly":
        day = str(config.get("day_of_week", "monday"))[:3].lower()
        trigger = CronTrigger(day_of_week=day, hour=hour, minute=minute, timezone=tz)
    elif schedule_type == "monthly":
        day = int(config.get("day_of_month", 1))
        trigger = CronTrigger(day=day, hour=hour, minute=minute, timezone=tz)
    else:
        return None

    nxt = trigger.get_next_fire_time(None, now.astimezone(tz))
    return nxt.astimezone(timezone.utc) if nxt else None


def apply_schedule(task) -> None:
    """Refresh ``task.next_run_at`` from its current status + schedule.

    Active, non-manual tasks get the next fire time; everything else is cleared
    so it won't be picked up by the runner. The caller is responsible for
    committing.
    """
    if task.status == "active" and task.schedule_type != "manual":
        task.next_run_at = compute_next_run_at(task.schedule_type, task.schedule_config)
    else:
        task.next_run_at = None


def backfill_next_run_times() -> None:
    """Initialise ``next_run_at`` for active tasks that are missing it.

    Run at startup so tasks activated before this trigger model existed (or
    while next_run_at was unused) begin firing without needing to be re-saved.
    """
    from app.db.engine import SessionLocal
    from app.db.models import AutomatedTask

    db = SessionLocal()
    try:
        tasks = (
            db.query(AutomatedTask)
            .filter(
                AutomatedTask.status == "active",
                AutomatedTask.schedule_type != "manual",
                AutomatedTask.next_run_at.is_(None),
            )
            .all()
        )
        for task in tasks:
            task.next_run_at = compute_next_run_at(
                task.schedule_type, task.schedule_config
            )
        if tasks:
            db.commit()
        logger.info("Backfilled next_run_at for %d active task(s)", len(tasks))
    finally:
        db.close()


def _claim_due_tasks(db=None) -> list[str]:
    """Atomically claim all currently-due tasks, returning their ids.

    Uses ``SELECT ... FOR UPDATE SKIP LOCKED`` and advances ``next_run_at`` for
    each claimed task before committing, so concurrent runner invocations (across
    workers or instances) never execute the same task twice.
    """
    from app.db.engine import SessionLocal
    from app.db.models import AutomatedTask

    owns_session = db is None
    if owns_session:
        db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        due = (
            db.query(AutomatedTask)
            .filter(
                AutomatedTask.status == "active",
                AutomatedTask.schedule_type != "manual",
                AutomatedTask.next_run_at.isnot(None),
                AutomatedTask.next_run_at <= now,
            )
            .order_by(AutomatedTask.next_run_at)
            .with_for_update(skip_locked=True)
            .all()
        )
        claimed = []
        for task in due:
            claimed.append(task.id)
            task.next_run_at = compute_next_run_at(
                task.schedule_type, task.schedule_config, after=now
            )
        db.commit()
        return claimed
    finally:
        if owns_session:
            db.close()


def _execute_claimed(task_ids: list[str]) -> None:
    """Run each claimed task in sequence. Intended to run off the request thread."""
    for task_id in task_ids:
        try:
            execute_task_now(task_id, mode="live")
        except Exception:
            logger.exception("run_due_tasks: task %s failed", task_id[:12])


def run_due_tasks(background: bool = True, db=None) -> dict:
    """Claim due tasks and execute them.

    Claiming is synchronous (fast, and it's what prevents double-runs). By
    default execution is dispatched to a background thread so the HTTP caller
    (Cloud Scheduler) gets a quick response and isn't blocked for the length of
    an agent tool-loop. Pass ``background=False`` to run inline (used by tests).
    """
    claimed = _claim_due_tasks(db=db)
    if claimed:
        if background:
            threading.Thread(
                target=_execute_claimed, args=(claimed,), daemon=True
            ).start()
        else:
            _execute_claimed(claimed)
    return {"claimed": len(claimed), "task_ids": claimed}


def _ensure_conversation_task(automated_task, db) -> str:
    """Ensure the automated task has a persistent conversation Thread. Returns its id."""
    from app.db.models import Thread

    if automated_task.conversation_thread_id:
        return automated_task.conversation_thread_id

    conv_thread = Thread(
        user_id=automated_task.created_by,
        domain=automated_task.agent_slug,
        intent=f"{automated_task.agent_slug}.automated_conversation",
        status="active",
        raw_prompt=automated_task.prompt,
        title=automated_task.title,
        extracted_fields={},
        missing_fields=[],
    )
    db.add(conv_thread)
    db.flush()
    automated_task.conversation_thread_id = conv_thread.id
    db.flush()
    return conv_thread.id


def _build_task_context(automated_task, agent_context: dict) -> dict:
    """Build context dict for an automated task run, merging agent context with task extras."""
    ctx = dict(agent_context)

    config = automated_task.task_config or {}
    if config:
        ctx["task_configuration"] = config

    if automated_task.thread_summary:
        ctx["thread_summary"] = automated_task.thread_summary

    overrides = automated_task.overrides_next_run
    if overrides:
        ctx["one_time_override"] = overrides

    return ctx


# Ceiling on the run output mirrored into the conversation. The result is meant
# to be shown in full, so this is only a safety valve against a pathological
# output — and it matters because the conversation is also the memory every
# future run loads.
_CONVERSATION_SUMMARY_CHARS = 12000


def _post_run_to_conversation(
    automated_task, run, db, body: str, display_blocks=None
) -> None:
    """Post a run into the task's conversation as an ordinary turn.

    The instruction collapses behind a disclosure ("Run the scheduled task: …")
    because it is the same prompt every time and would otherwise bury the part
    that changes; the result underneath is shown in full. Assistant messages
    render as markdown with raw HTML enabled, so <details> works with no
    frontend involvement.

    Called on every path — success, empty result and failure — so the
    conversation is a complete run log. Best-effort: it must never be the
    reason a run is reported as failed.
    """
    from app.db.models import Message

    if not automated_task.conversation_thread_id:
        return
    tz = _resolve_timezone(automated_task.schedule_config)
    when = (run.started_at or datetime.now(timezone.utc)).astimezone(tz)
    # "ran" not "success": the status only means no exception was raised. The
    # output below it is the evidence of what actually happened.
    outcome = "✓ ran" if run.status == "success" else f"✗ {run.status}"
    headline = [
        f"Run the scheduled task: {automated_task.title}",
        when.strftime("%-d %b %Y, %-I:%M%p").lower(),
        outcome,
    ]
    if run.duration_ms:
        headline.append(f"{round(run.duration_ms / 1000)}s")
    if run.tool_calls_count:
        headline.append(f"{run.tool_calls_count} tool calls")

    instruction = (automated_task.prompt or "").strip()
    content = (
        "<details>\n"
        f"<summary>{' · '.join(headline)}</summary>\n\n"
        f"{instruction}\n\n"
        "</details>\n\n"
        f"{(body or '').strip()[:_CONVERSATION_SUMMARY_CHARS]}"
    )
    try:
        db.add(
            Message(
                thread_id=automated_task.conversation_thread_id,
                role="assistant",
                content=content,
                display_blocks=display_blocks or None,
            )
        )
    except Exception:  # noqa: BLE001 — logging the run must not break the run
        logger.exception("Could not post run %s to task conversation", str(run.id)[:12])


def execute_task_now(task_id: str, mode: str = "live", db=None) -> dict:
    """Execute an automated task immediately. Returns the run result dict.

    Can be called by the scheduler (background thread) or by an API endpoint.
    """
    from app.db.engine import SessionLocal, _ConfigSessionLocal
    from app.db.models import AutomatedTask, AutomatedTaskRun, Thread, Message
    from app.agents.registry import get_agent
    from app.agents.tool_loop import run_tool_loop

    owns_session = db is None
    if owns_session:
        db = SessionLocal()
    config_db = _ConfigSessionLocal()

    try:
        task = db.query(AutomatedTask).filter(AutomatedTask.id == task_id).first()
        if not task:
            return {"success": False, "error": "Automated task not found"}

        # Ensure conversation task exists
        _ensure_conversation_task(task, db)

        # Create run record
        run = AutomatedTaskRun(
            automated_task_id=task.id,
            status="running",
            mode=mode,
        )
        db.add(run)
        db.flush()

        t0 = time.time()

        try:
            # Get agent and tools
            agent = get_agent(task.agent_slug)
            if not agent:
                raise ValueError(f"Agent not found: {task.agent_slug}")

            system_prompt, anthropic_tools = agent.get_tool_definitions(
                db,
                user_id=task.created_by,
                config_db=config_db,
                tool_filter=task.tool_filter,
            )
            if not system_prompt:
                system_prompt = f"You are the {task.agent_slug} agent for Norm."

            ctx = agent.build_context(db)
            at_context = _build_task_context(task, ctx)

            # Load conversation history from persistent thread
            conv_messages = []
            conv_thread = None
            if task.conversation_thread_id:
                conv_thread = (
                    db.query(Thread)
                    .filter(Thread.id == task.conversation_thread_id)
                    .first()
                )
                conv_messages = (
                    db.query(Message)
                    .filter(Message.thread_id == task.conversation_thread_id)
                    .order_by(Message.created_at)
                    .all()
                )

            # Build messages using unified context builder
            from app.agents.context_builder import build_conversation_messages

            messages = build_conversation_messages(
                conv_messages,
                task.prompt,
                context=at_context,
                thread=conv_thread,
                db=db,
            )

            # Create execution Thread for run isolation (tool calls + details)
            temp_task = Thread(
                user_id=task.created_by,
                domain=task.agent_slug,
                intent=f"{task.agent_slug}.automated_task",
                status="in_progress",
                raw_prompt=task.prompt,
                title=f"[Auto] {task.title}",
                extracted_fields={},
                missing_fields=[],
            )
            db.add(temp_task)
            db.flush()

            # Link run to execution task
            run.thread_id = temp_task.id

            db.add(Message(thread_id=temp_task.id, role="user", content=task.prompt))
            db.flush()

            # Execute the tool loop with pre-built messages
            test_mode = mode == "test"
            result = run_tool_loop(
                task.prompt,
                temp_task,
                db,
                system_prompt,
                anthropic_tools,
                context=at_context,
                test_mode=test_mode,
                config_db=config_db,
                messages_override=messages,
            )

            # Extract result
            result_text = result.get("message", "")
            tool_calls_count = len(result.get("tool_calls", []))
            duration_ms = int((time.time() - t0) * 1000)

            run.status = "success"
            run.result_summary = result_text[:2000] if result_text else None
            run.tool_calls_count = tool_calls_count
            run.completed_at = datetime.now(timezone.utc)
            run.duration_ms = duration_ms

            task.last_run_at = datetime.now(timezone.utc)

            # Clear one-time overrides after execution
            if task.overrides_next_run:
                task.overrides_next_run = None

            # Mirror the run into the conversation. Always post something — an
            # empty model response must still leave a trace, or a run that
            # produced nothing is indistinguishable from one that never ran.
            # The run's display blocks come along so the cards and tables it
            # produced render inline, exactly as they did during the run.
            _post_run_to_conversation(
                task,
                run,
                db,
                result_text
                or (
                    f"Task completed but produced no output "
                    f"({tool_calls_count} tool call(s), {duration_ms}ms)."
                ),
                display_blocks=result.get("display_blocks"),
            )

            db.commit()

            return {
                "success": True,
                "data": {
                    "run_id": run.id,
                    "thread_id": run.thread_id,
                    "status": run.status,
                    "mode": run.mode,
                    "result_summary": run.result_summary,
                    "tool_calls_count": run.tool_calls_count,
                    "duration_ms": run.duration_ms,
                },
            }

        except Exception as exc:
            duration_ms = int((time.time() - t0) * 1000)
            run.status = "error"
            run.error_message = str(exc)[:1000]
            run.completed_at = datetime.now(timezone.utc)
            run.duration_ms = duration_ms
            # Record the failure in the conversation too. Without this a broken
            # scheduled task looks exactly like one that never fired — the only
            # trace is a row the owner cannot see.
            _post_run_to_conversation(task, run, db, f"Failed: {run.error_message}")
            db.commit()

            logger.exception("Automated task %s failed", task_id[:12])
            return {"success": False, "error": str(exc)}

    finally:
        config_db.close()
        if owns_session:
            db.close()
