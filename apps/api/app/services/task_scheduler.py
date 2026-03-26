"""APScheduler-based task scheduler for automated agent workflows.

Manages scheduling, execution, and lifecycle of AutomatedTask records.
Started at FastAPI startup, loads active tasks from DB, and runs them
on their configured schedules.
"""

import json
import logging
import time
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()

# How many recent conversation messages to inject into scheduled runs
RECENT_MESSAGES_LIMIT = 8


def init_scheduler():
    """Start the scheduler and load active tasks from DB."""
    from app.db.engine import SessionLocal
    from app.db.models import AutomatedTask

    scheduler.start()

    db = SessionLocal()
    try:
        active_tasks = (
            db.query(AutomatedTask).filter(AutomatedTask.status == "active").all()
        )
        for task in active_tasks:
            schedule_task(task)
        logger.info("Scheduler started with %d active tasks", len(active_tasks))
    finally:
        db.close()


def schedule_task(task) -> None:
    """Add or replace a scheduler job for an AutomatedTask."""
    trigger = _build_trigger(task.schedule_type, task.schedule_config or {})
    if trigger is None:
        return  # manual tasks don't get scheduled

    scheduler.add_job(
        _execute_automated_task,
        trigger=trigger,
        args=[task.id],
        id=task.id,
        replace_existing=True,
    )
    logger.info(
        "Scheduled task %s (%s) with %s trigger",
        task.id[:12],
        task.title,
        task.schedule_type,
    )


def unschedule_task(task_id: str) -> None:
    """Remove a scheduler job."""
    try:
        scheduler.remove_job(task_id)
        logger.info("Unscheduled task %s", task_id[:12])
    except Exception:
        pass  # job might not exist


def _build_trigger(schedule_type: str, config: dict):
    """Build an APScheduler trigger from schedule_type + config."""
    hour = config.get("hour", 9)
    minute = config.get("minute", 0)

    if schedule_type == "hourly":
        return IntervalTrigger(hours=1)
    if schedule_type == "daily":
        return CronTrigger(hour=hour, minute=minute)
    if schedule_type == "weekly":
        day = config.get("day_of_week", "monday")
        return CronTrigger(day_of_week=day[:3].lower(), hour=hour, minute=minute)
    if schedule_type == "monthly":
        day = config.get("day_of_month", 1)
        return CronTrigger(day=day, hour=hour, minute=minute)
    return None  # manual


def _execute_automated_task(task_id: str):
    """Worker function: run an automated task. Called by APScheduler."""
    execute_task_now(task_id, mode="live")


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


def _build_augmented_prompt(automated_task, db) -> str:
    """Build the prompt for a scheduled run with injected context."""
    from app.db.models import Message

    parts = [automated_task.prompt]

    # Task configuration
    config = automated_task.task_config or {}
    if config:
        parts.append(f"\n[Task Configuration]\n{json.dumps(config, indent=2)}")

    # Thread summary
    if automated_task.thread_summary:
        parts.append(f"\n[Thread Summary]\n{automated_task.thread_summary}")

    # Recent conversation messages
    if automated_task.conversation_thread_id:
        recent = (
            db.query(Message)
            .filter(Message.thread_id == automated_task.conversation_thread_id)
            .order_by(Message.created_at.desc())
            .limit(RECENT_MESSAGES_LIMIT)
            .all()
        )
        if recent:
            recent.reverse()  # chronological order
            lines = []
            for m in recent:
                prefix = "User" if m.role == "user" else "Assistant"
                # Truncate long messages in context
                content = m.content[:500] if len(m.content) > 500 else m.content
                lines.append(f"{prefix}: {content}")
            parts.append("\n[Recent Context]\n" + "\n".join(lines))

    # One-time overrides
    overrides = automated_task.overrides_next_run
    if overrides:
        if isinstance(overrides, dict):
            parts.append(f"\n[One-time Override]\n{json.dumps(overrides, indent=2)}")
        elif isinstance(overrides, str):
            parts.append(f"\n[One-time Override]\n{overrides}")

    return "\n".join(parts)


def execute_task_now(task_id: str, mode: str = "live", db=None) -> dict:
    """Execute an automated task immediately. Returns the run result dict.

    Can be called by the scheduler (background thread) or by an API endpoint.
    """
    from app.db.engine import SessionLocal
    from app.db.models import AutomatedTask, AutomatedTaskRun, Thread, Message
    from app.agents.registry import get_agent
    from app.agents.tool_loop import run_tool_loop

    owns_session = db is None
    if owns_session:
        db = SessionLocal()

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
                db, user_id=task.created_by
            )
            if not system_prompt:
                system_prompt = f"You are the {task.agent_slug} agent for Norm."

            ctx = agent.build_context(db)

            # Build augmented prompt with context
            augmented_prompt = _build_augmented_prompt(task, db)

            # Create execution Task record for the tool loop
            temp_task = Thread(
                user_id=task.created_by,
                domain=task.agent_slug,
                intent=f"{task.agent_slug}.automated_task",
                status="in_progress",
                raw_prompt=augmented_prompt,
                title=f"[Auto] {task.title}",
                extracted_fields={},
                missing_fields=[],
            )
            db.add(temp_task)
            db.flush()

            # Link run to execution task
            run.thread_id = temp_task.id

            db.add(
                Message(thread_id=temp_task.id, role="user", content=augmented_prompt)
            )
            db.flush()

            # Execute the tool loop
            test_mode = mode == "test"
            result = run_tool_loop(
                augmented_prompt,
                temp_task,
                db,
                system_prompt,
                anthropic_tools,
                context=ctx,
                test_mode=test_mode,
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

            # Post run summary to conversation task
            if task.conversation_thread_id and result_text:
                run_label = f"[Run {run.id[:8]} — {run.status}]"
                summary = result_text[:1000] if result_text else "Task completed."
                db.add(
                    Message(
                        thread_id=task.conversation_thread_id,
                        role="assistant",
                        content=f"{run_label}\n{summary}",
                    )
                )

            db.commit()

            return {
                "success": True,
                "data": {
                    "run_id": run.id,
                    "task_id": run.thread_id,
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
            db.commit()

            logger.exception("Automated task %s failed", task_id[:12])
            return {"success": False, "error": str(exc)}

    finally:
        if owns_session:
            db.close()
