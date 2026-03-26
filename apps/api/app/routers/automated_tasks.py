"""REST endpoints for automated task management (UI board)."""

import logging

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models import (
    AutomatedTask,
    AutomatedTaskRun,
    Thread,
    Message,
    ToolCall,
    User,
)
from app.auth.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


class CreateBody(BaseModel):
    title: str
    description: str | None = None
    agent_slug: str
    prompt: str
    schedule_type: str = "manual"
    schedule_config: dict = {}


class UpdateBody(BaseModel):
    title: str | None = None
    description: str | None = None
    prompt: str | None = None
    schedule_type: str | None = None
    schedule_config: dict | None = None
    status: str | None = None


class RunBody(BaseModel):
    mode: str = "live"


class MessageBody(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _automated_task_to_dict(t: AutomatedTask, include_runs: bool = False) -> dict:
    d = {
        "id": t.id,
        "title": t.title,
        "description": t.description,
        "agent_slug": t.agent_slug,
        "prompt": t.prompt,
        "schedule_type": t.schedule_type,
        "schedule_config": t.schedule_config,
        "status": t.status,
        "created_by": t.created_by,
        "task_config": t.task_config or {},
        "thread_summary": t.thread_summary,
        "overrides_next_run": t.overrides_next_run,
        "conversation_thread_id": t.conversation_thread_id,
        "last_run_at": t.last_run_at.isoformat() if t.last_run_at else None,
        "next_run_at": t.next_run_at.isoformat() if t.next_run_at else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }
    if include_runs:
        d["runs"] = [_run_to_dict(r) for r in (t.runs or [])[:10]]
    return d


def _run_to_dict(r: AutomatedTaskRun) -> dict:
    d = {
        "id": r.id,
        "automated_task_id": r.automated_task_id,
        "thread_id": r.thread_id,
        "status": r.status,
        "mode": r.mode,
        "result_summary": r.result_summary,
        "tool_calls_count": r.tool_calls_count,
        "error_message": r.error_message,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        "duration_ms": r.duration_ms,
        "has_pending_approvals": False,
    }
    # Check if the linked task has pending approvals
    if r.thread_id and r.thread:
        pending = r.thread.pending_tool_call_ids
        d["has_pending_approvals"] = bool(pending and len(pending) > 0)
    return d


def _message_to_dict(m: Message) -> dict:
    return {
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "display_blocks": m.display_blocks,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------


@router.get("/automated-tasks")
async def list_tasks(
    agent_slug: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(AutomatedTask)
    if agent_slug:
        query = query.filter(AutomatedTask.agent_slug == agent_slug)
    if status:
        query = query.filter(AutomatedTask.status == status)
    tasks = query.order_by(AutomatedTask.created_at.desc()).all()
    return {"tasks": [_automated_task_to_dict(t) for t in tasks]}


@router.get("/automated-tasks/{task_id}")
async def get_task(
    task_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    task = db.query(AutomatedTask).filter(AutomatedTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "Automated task not found")
    return _automated_task_to_dict(task, include_runs=True)


@router.post("/automated-tasks")
async def create_task(
    body: CreateBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    task = AutomatedTask(
        title=body.title,
        description=body.description,
        agent_slug=body.agent_slug,
        prompt=body.prompt,
        schedule_type=body.schedule_type,
        schedule_config=body.schedule_config,
        status="draft",
        created_by=user.id,
    )
    db.add(task)
    db.commit()
    return _automated_task_to_dict(task)


@router.put("/automated-tasks/{task_id}")
async def update_task(
    task_id: str,
    body: UpdateBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    task = db.query(AutomatedTask).filter(AutomatedTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "Automated task not found")

    for field in (
        "title",
        "description",
        "prompt",
        "schedule_type",
        "schedule_config",
        "status",
    ):
        val = getattr(body, field, None)
        if val is not None:
            setattr(task, field, val)

    db.commit()

    from app.services.task_scheduler import schedule_task, unschedule_task

    if task.status == "active":
        schedule_task(task)
    else:
        unschedule_task(task.id)

    return _automated_task_to_dict(task)


@router.post("/automated-tasks/{task_id}/run")
async def run_task(
    task_id: str,
    body: RunBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    task = db.query(AutomatedTask).filter(AutomatedTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "Automated task not found")

    from app.services.task_scheduler import execute_task_now

    try:
        result = execute_task_now(task_id, mode=body.mode, db=db)
        return result
    except Exception as exc:
        logger.exception("Automated task run failed")
        return {"success": False, "error": str(exc)}


@router.post("/automated-tasks/{task_id}/pause")
async def pause_task(
    task_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    task = db.query(AutomatedTask).filter(AutomatedTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "Automated task not found")
    task.status = "paused"
    db.commit()

    from app.services.task_scheduler import unschedule_task

    unschedule_task(task.id)
    return _automated_task_to_dict(task)


@router.post("/automated-tasks/{task_id}/resume")
async def resume_task(
    task_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    task = db.query(AutomatedTask).filter(AutomatedTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "Automated task not found")
    task.status = "active"
    db.commit()

    from app.services.task_scheduler import schedule_task

    schedule_task(task)
    return _automated_task_to_dict(task)


@router.delete("/automated-tasks/{task_id}")
async def delete_task(
    task_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    task = db.query(AutomatedTask).filter(AutomatedTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "Automated task not found")

    from app.services.task_scheduler import unschedule_task

    unschedule_task(task.id)

    db.delete(task)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


@router.get("/automated-tasks/{task_id}/runs")
async def list_runs(
    task_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    runs = (
        db.query(AutomatedTaskRun)
        .filter(AutomatedTaskRun.automated_task_id == task_id)
        .order_by(AutomatedTaskRun.started_at.desc())
        .limit(50)
        .all()
    )
    return {"runs": [_run_to_dict(r) for r in runs]}


@router.get("/automated-tasks/{task_id}/runs/{run_id}")
async def get_run_detail(
    task_id: str,
    run_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Get full detail for a specific run including its Task messages and tool calls."""
    run = (
        db.query(AutomatedTaskRun)
        .filter(
            AutomatedTaskRun.id == run_id,
            AutomatedTaskRun.automated_task_id == task_id,
        )
        .first()
    )
    if not run:
        raise HTTPException(404, "Run not found")

    result = _run_to_dict(run)

    # If the run has a linked execution task, include its messages and tool calls
    if run.thread_id:
        exec_thread = db.query(Thread).filter(Thread.id == run.thread_id).first()
        if exec_thread:
            messages = (
                db.query(Message)
                .filter(Message.thread_id == exec_thread.id)
                .order_by(Message.created_at)
                .all()
            )
            tool_calls = (
                db.query(ToolCall)
                .filter(ToolCall.thread_id == exec_thread.id)
                .order_by(ToolCall.created_at)
                .all()
            )
            result["messages"] = [_message_to_dict(m) for m in messages]
            result["tool_calls"] = [
                {
                    "id": tc.id,
                    "tool_name": tc.tool_name,
                    "connector_name": tc.connector_name,
                    "action": tc.action,
                    "method": tc.method,
                    "status": tc.status,
                    "input_params": tc.input_params,
                    "result_payload": tc.result_payload,
                    "slimmed_content": tc.slimmed_content,
                    "error_message": tc.error_message,
                    "duration_ms": tc.duration_ms,
                    "created_at": tc.created_at.isoformat() if tc.created_at else None,
                }
                for tc in tool_calls
            ]
            result["pending_tool_call_ids"] = exec_thread.pending_tool_call_ids

    return result


# ---------------------------------------------------------------------------
# Conversation — chat with an automated task
# ---------------------------------------------------------------------------


@router.get("/automated-tasks/{task_id}/conversation")
async def get_conversation(
    task_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Load the conversation thread for an automated task."""
    task = db.query(AutomatedTask).filter(AutomatedTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "Automated task not found")

    if not task.conversation_thread_id:
        return {
            "messages": [],
            "task_config": task.task_config or {},
            "thread_summary": task.thread_summary,
        }

    messages = (
        db.query(Message)
        .filter(Message.thread_id == task.conversation_thread_id)
        .order_by(Message.created_at)
        .all()
    )

    return {
        "messages": [_message_to_dict(m) for m in messages],
        "task_config": task.task_config or {},
        "thread_summary": task.thread_summary,
        "overrides_next_run": task.overrides_next_run,
    }


@router.post("/automated-tasks/{task_id}/ensure-conversation")
async def ensure_conversation(
    task_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create the conversation task if it doesn't exist. Lightweight — no LLM call."""
    task = db.query(AutomatedTask).filter(AutomatedTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "Automated task not found")

    from app.services.task_scheduler import _ensure_conversation_task

    conv_thread_id = _ensure_conversation_task(task, db)
    # Ensure user_id is set on the conversation thread
    conv_thread = db.query(Thread).filter(Thread.id == conv_thread_id).first()
    if conv_thread and not conv_thread.user_id:
        conv_thread.user_id = user.id
    db.commit()

    return {"conversation_thread_id": conv_thread_id}


@router.post("/automated-tasks/{task_id}/message")
async def send_message(
    task_id: str,
    body: MessageBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Send a user message to the automated task's conversation."""
    task = db.query(AutomatedTask).filter(AutomatedTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "Automated task not found")

    # Ensure conversation task exists
    from app.services.task_scheduler import _ensure_conversation_task

    conv_task_id = _ensure_conversation_task(task, db)
    db.commit()

    # Route through the supervisor to get a full agent response
    from app.services.supervisor import handle_message

    result = handle_message(
        message=body.message,
        db=db,
        user_id=user.id,
        thread_id=conv_task_id,
    )

    return {
        "success": True,
        "message": result.get("message", ""),
        "display_blocks": result.get("display_blocks"),
        "task_config": task.task_config or {},
        "thread_summary": task.thread_summary,
    }
