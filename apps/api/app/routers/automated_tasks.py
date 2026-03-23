"""REST endpoints for automated task management (UI board)."""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models import AutomatedTask, AutomatedTaskRun, User
from app.auth.dependencies import get_current_user

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


def _task_to_dict(t: AutomatedTask, include_runs: bool = False) -> dict:
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
        "last_run_at": t.last_run_at.isoformat() if t.last_run_at else None,
        "next_run_at": t.next_run_at.isoformat() if t.next_run_at else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }
    if include_runs:
        d["runs"] = [_run_to_dict(r) for r in (t.runs or [])[:10]]
    return d


def _run_to_dict(r: AutomatedTaskRun) -> dict:
    return {
        "id": r.id,
        "automated_task_id": r.automated_task_id,
        "status": r.status,
        "mode": r.mode,
        "result_summary": r.result_summary,
        "tool_calls_count": r.tool_calls_count,
        "error_message": r.error_message,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        "duration_ms": r.duration_ms,
    }


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
    return {"tasks": [_task_to_dict(t) for t in tasks]}


@router.get("/automated-tasks/{task_id}")
async def get_task(
    task_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    task = db.query(AutomatedTask).filter(AutomatedTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "Automated task not found")
    return _task_to_dict(task, include_runs=True)


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
    return _task_to_dict(task)


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

    return _task_to_dict(task)


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
        import logging

        logging.getLogger(__name__).exception("Automated task run failed")
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
    return _task_to_dict(task)


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
    return _task_to_dict(task)


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
