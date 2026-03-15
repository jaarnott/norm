"""Unified task lifecycle endpoints."""

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models import Task, Approval, User
from app.auth.dependencies import get_current_user
from app.services.order_service import (
    get_order, approve_order, reject_order, submit_order, list_orders,
)
from app.services.hr_service import (
    get_task as get_hr_task,
    approve_task as approve_hr_task,
    reject_task as reject_hr_task,
    submit_task as submit_hr_task,
    list_tasks as list_hr_tasks,
)
from app.agents.reports.context import _report_task_to_dict

router = APIRouter()


def _find(db: Session, task_id: str) -> tuple[dict | None, str]:
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        return None, ""
    if task.domain == "procurement":
        return get_order(db, task_id), "procurement"
    if task.domain == "hr":
        return get_hr_task(db, task_id), "hr"
    if task.domain == "reports":
        return _report_task_to_dict(task), "reports"
    return None, ""


def _list_report_tasks(db: Session, user_id: str | None = None) -> list[dict]:
    q = db.query(Task).filter(Task.domain == "reports")
    if user_id:
        q = q.filter(Task.user_id == user_id)
    tasks = q.order_by(Task.created_at.desc()).all()
    return [_report_task_to_dict(t) for t in tasks]


@router.get("/tasks")
async def get_all_tasks(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    uid = user.id
    all_tasks = list_orders(db, uid) + list_hr_tasks(db, uid) + _list_report_tasks(db, uid)
    all_tasks.sort(key=lambda t: t.get("created_at", ""), reverse=True)
    return {"tasks": all_tasks}


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = db.query(Task).filter(Task.id == task_id, Task.user_id == user.id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    db.delete(task)
    db.commit()
    return {"ok": True}


@router.get("/tasks/{task_id}")
async def get_task_detail(task_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task, _ = _find(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/tasks/{task_id}/approve")
async def approve(task_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task, domain = _find(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if domain == "procurement":
        return approve_order(db, task_id, user=user)
    if domain == "hr":
        return approve_hr_task(db, task_id, user=user)
    if domain == "reports":
        return _approve_report(db, task_id, user=user)
    raise HTTPException(status_code=400, detail="Unsupported domain")


@router.post("/tasks/{task_id}/reject")
async def reject(task_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task, domain = _find(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if domain == "procurement":
        return reject_order(db, task_id, user=user)
    if domain == "hr":
        return reject_hr_task(db, task_id, user=user)
    if domain == "reports":
        return _reject_report(db, task_id, user=user)
    raise HTTPException(status_code=400, detail="Unsupported domain")


@router.post("/tasks/{task_id}/submit")
async def submit(task_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task, domain = _find(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if domain == "procurement":
        result = submit_order(db, task_id)
    elif domain == "hr":
        result = submit_hr_task(db, task_id)
    elif domain == "reports":
        # Reports don't submit externally — approve is the terminal action
        raise HTTPException(status_code=400, detail="Reports cannot be submitted to external systems")
    else:
        raise HTTPException(status_code=400, detail="Unsupported domain")
    if not result:
        raise HTTPException(status_code=400, detail="Task not in approved state")
    return result


def _approve_report(db: Session, task_id: str, user: User | None = None) -> dict:
    from datetime import datetime, timezone
    task = db.query(Task).filter(Task.id == task_id, Task.domain == "reports").first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task.status = "approved"
    task.updated_at = datetime.now(timezone.utc)
    db.add(Approval(
        task_id=task_id,
        action="approved",
        performed_by=user.email if user else "system",
        user_id=user.id if user else None,
    ))
    db.commit()
    db.refresh(task)
    return _report_task_to_dict(task)


def _reject_report(db: Session, task_id: str, user: User | None = None) -> dict:
    from datetime import datetime, timezone
    task = db.query(Task).filter(Task.id == task_id, Task.domain == "reports").first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task.status = "rejected"
    task.updated_at = datetime.now(timezone.utc)
    db.add(Approval(
        task_id=task_id,
        action="rejected",
        performed_by=user.email if user else "system",
        user_id=user.id if user else None,
    ))
    db.commit()
    db.refresh(task)
    return _report_task_to_dict(task)
