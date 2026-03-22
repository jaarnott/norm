"""Unified task lifecycle endpoints."""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models import Task, Approval, ToolCall, User
from app.auth.dependencies import get_current_user
from app.services.order_service import (
    get_order, approve_order, reject_order, submit_order,
)
from app.services.hr_service import (
    get_task as get_hr_task,
    approve_task as approve_hr_task,
    reject_task as reject_hr_task,
    submit_task as submit_hr_task,
)
from app.agents.reports.context import _report_task_to_dict

router = APIRouter()


def _find(db: Session, task_id: str) -> tuple[dict | None, str]:
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        return None, ""
    # Tool-loop tasks use a generic dict format
    if task.intent and task.intent.endswith(".tool_use"):
        return _tool_use_task_to_dict(task), task.domain
    if task.domain == "procurement":
        return get_order(db, task_id), "procurement"
    if task.domain == "hr":
        return get_hr_task(db, task_id), "hr"
    if task.domain == "reports":
        return _report_task_to_dict(task), "reports"
    return None, ""


def _tool_use_task_to_dict(task: Task) -> dict:
    """Serialize a tool-loop task to a generic response dict."""
    conversation = [
        {
            "role": m.role,
            "text": m.content,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "display_blocks": m.display_blocks,
        }
        for m in sorted(task.messages, key=lambda x: x.created_at)
    ]
    tool_calls = [
        {
            "id": tc.id,
            "iteration": tc.iteration,
            "tool_name": tc.tool_name,
            "connector_name": tc.connector_name,
            "action": tc.action,
            "method": tc.method,
            "input_params": tc.input_params,
            "status": tc.status,
            "result_payload": tc.result_payload,
            "error_message": tc.error_message,
            "duration_ms": tc.duration_ms,
            "created_at": tc.created_at.isoformat() if tc.created_at else None,
        }
        for tc in sorted(task.tool_calls, key=lambda x: x.created_at)
    ]
    llm_calls = [
        {
            "id": lc.id,
            "call_type": lc.call_type,
            "model": lc.model,
            "system_prompt": lc.system_prompt,
            "user_prompt": lc.user_prompt,
            "raw_response": lc.raw_response,
            "parsed_response": lc.parsed_response,
            "status": lc.status,
            "error_message": lc.error_message,
            "duration_ms": lc.duration_ms,
            "input_tokens": lc.input_tokens,
            "output_tokens": lc.output_tokens,
            "tools_provided": lc.tools_provided,
            "created_at": lc.created_at.isoformat() if lc.created_at else None,
        }
        for lc in sorted(task.llm_calls, key=lambda x: x.created_at)
    ]
    return {
        "id": task.id,
        "domain": task.domain,
        "intent": task.intent,
        "title": task.title,
        "message": task.raw_prompt or "",
        "status": task.status,
        "extracted_fields": task.extracted_fields,
        "missing_fields": task.missing_fields,
        "clarification_question": task.clarification_question,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "conversation": conversation,
        "tool_calls": tool_calls,
        "llm_calls": llm_calls,
        "thinking_steps": task.thinking_steps or [],
    }


@router.get("/tasks")
async def get_all_tasks(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Return lightweight task summaries for the sidebar list.

    Only includes fields needed by TaskCard (id, domain, status, title, etc.).
    Full conversation/tool_calls/llm_calls are loaded on demand via GET /tasks/{id}.
    """
    tasks = (
        db.query(Task)
        .filter(Task.user_id == user.id)
        .order_by(Task.created_at.desc())
        .all()
    )
    return {"tasks": [_task_summary(t) for t in tasks]}


def _task_summary(task: Task) -> dict:
    """Lightweight serialisation — no relationships loaded."""
    extracted = task.extracted_fields or {}
    venue = extracted.get("venue")
    product = extracted.get("product")

    summary: dict = {
        "id": task.id,
        "domain": task.domain,
        "intent": task.intent,
        "title": task.title,
        "message": task.raw_prompt or "",
        "status": task.status,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "missing_fields": task.missing_fields or [],
        "clarification_question": task.clarification_question,
        "thinking_steps": task.thinking_steps or [],
    }

    # Domain-specific card fields
    if task.domain == "procurement":
        summary["venue"] = {"id": venue["id"], "name": venue["name"]} if venue else None
        summary["product"] = {"id": product["id"], "name": product["name"], "unit": product.get("unit", "case"), "category": product.get("category")} if product else None
        summary["supplier"] = product.get("supplier") if product else None
        summary["quantity"] = extracted.get("quantity")
    elif task.domain == "hr":
        summary["employee_name"] = extracted.get("employee_name")
        summary["venue"] = {"id": venue["id"], "name": venue["name"]} if venue else None
        summary["role"] = extracted.get("role")
        summary["start_date"] = extracted.get("start_date")
    elif task.domain == "reports":
        summary["report_type"] = extracted.get("report_type")

    return summary


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
    # Check if this is a tool-approval request
    raw_task = db.query(Task).filter(Task.id == task_id).first()
    if raw_task and raw_task.status == "awaiting_tool_approval":
        return _approve_tool_calls(db, raw_task, user)

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
    # Check if this is a tool-rejection request
    raw_task = db.query(Task).filter(Task.id == task_id).first()
    if raw_task and raw_task.status == "awaiting_tool_approval":
        return _reject_tool_calls(db, raw_task, user)

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


def _approve_tool_calls(db: Session, task: Task, user: User) -> dict:
    """Approve pending write tool calls and resume the agentic loop."""
    from app.agents.tool_loop import resume_tool_loop
    from app.agents.prompt_builder import build_tool_definitions

    # Mark pending tool calls as approved
    for tc_id in (task.pending_tool_call_ids or []):
        tc = db.query(ToolCall).filter(ToolCall.id == tc_id).first()
        if tc and tc.status == "pending_approval":
            tc.status = "approved"
    db.flush()

    # Record the approval
    db.add(Approval(
        task_id=task.id,
        action="tool_calls_approved",
        performed_by=user.email,
        user_id=user.id,
    ))
    db.flush()

    # Resume the loop
    system_prompt, anthropic_tools = build_tool_definitions(task.domain, db)
    return resume_tool_loop(task, db, system_prompt, anthropic_tools)


def _reject_tool_calls(db: Session, task: Task, user: User) -> dict:
    """Reject pending write tool calls and resume the loop (tool results will say 'rejected')."""
    from app.agents.tool_loop import resume_tool_loop
    from app.agents.prompt_builder import build_tool_definitions

    # Mark pending tool calls as rejected
    for tc_id in (task.pending_tool_call_ids or []):
        tc = db.query(ToolCall).filter(ToolCall.id == tc_id).first()
        if tc and tc.status == "pending_approval":
            tc.status = "rejected"
    db.flush()

    # Record the rejection
    db.add(Approval(
        task_id=task.id,
        action="tool_calls_rejected",
        performed_by=user.email,
        user_id=user.id,
    ))
    db.flush()

    # Resume the loop — the tool results will contain rejection messages
    system_prompt, anthropic_tools = build_tool_definitions(task.domain, db)
    return resume_tool_loop(task, db, system_prompt, anthropic_tools)


# ---------------------------------------------------------------------------
# Widget-initiated tool actions
# ---------------------------------------------------------------------------

class WidgetActionRequest(BaseModel):
    connector_name: str
    action: str
    params: dict = {}


@router.post("/tasks/{task_id}/widget-action")
async def widget_action(
    task_id: str,
    body: WidgetActionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Execute a tool call initiated from an interactive widget."""
    from app.agents.tool_loop import _execute_tool_call, _find_tool_def
    from app.db.models import ConnectorSpec
    import uuid
    import time as _time

    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Reject if there are already pending tool calls
    if task.status == "awaiting_tool_approval":
        raise HTTPException(status_code=409, detail="Task already has pending tool calls awaiting approval")

    # Look up the tool definition
    tool_def = _find_tool_def(body.connector_name, body.action, db)
    if not tool_def:
        raise HTTPException(status_code=404, detail=f"Tool not found: {body.action} on {body.connector_name}")

    method = tool_def.get("method", "POST").upper()
    is_read_only = method == "GET"

    # Create a ToolCall record
    tc = ToolCall(
        id=str(uuid.uuid4()),
        task_id=task.id,
        iteration=0,
        tool_name=f"{body.connector_name}__{body.action}",
        connector_name=body.connector_name,
        action=body.action,
        method=method,
        input_params=body.params,
        status="executed" if is_read_only else "pending_approval",
    )
    db.add(tc)
    db.flush()

    if is_read_only:
        # Auto-execute read-only tool
        result = _execute_tool_call(tc, db)
        db.commit()
        return {"status": "executed", "data": result}
    else:
        # Queue for approval
        task.pending_tool_call_ids = [tc.id]
        task.status = "awaiting_tool_approval"
        db.commit()

        return {
            "status": "pending_approval",
            "tool_call_id": tc.id,
            "action": body.action,
            "connector_name": body.connector_name,
            "params": body.params,
        }
